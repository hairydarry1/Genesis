"""
data/deriv_connection.py — Raw websocket connection to the Deriv Options public API.

Connects to:
    wss://api.derivws.com/trading/v1/options/ws/public

No app_id, no token, no python-deriv-api library.  Pure `websockets`.

Resilience features:
    - Active keepalive pings every 15s (well under Deriv's 30s idle timeout)
    - Reconnect with exponential backoff on drop
    - Subscription replay after reconnect
    - Structured logging via structlog (every drop, reconnect, error)

Message format follows the official deriv-api-schemas (production_v20260804_0):
    - Ping:        {"ping": 1} -> {"ping": "pong", "msg_type": "ping"}
    - Tick stream: {"ticks": "R_75", "subscribe": 1, "req_id": N} -> {"tick": {...}, "msg_type": "tick"}
    - Tick history: {"ticks_history": "R_75", "end": "latest", "count": 1000} -> {"history": {...}, "msg_type": "history"}
    - Candles:     {"ticks_history": "R_75", "end": "latest", "style": "candles", "count": N, "granularity": 60}
    - Forget:      {"forget": "subscription_id"}

Usage:
    conn = DerivConnection()
    await conn.connect()

    # One-shot request
    resp = await conn.send({"ticks_history": "R_75", "end": "latest", "count": 1000})

    # Live stream with callback
    sub_id = await conn.subscribe_ticks("R_75", handler=my_callback)

    # Historical ticks
    ticks = await conn.get_ticks_history("R_75", count=5000)

    await conn.close()
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Optional

import structlog
import websockets
import websockets.asyncio.client

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

WS_PUBLIC_ENDPOINT = "wss://api.derivws.com/trading/v1/options/ws/public"
KEEPALIVE_INTERVAL_S = 15     # Deriv idle timeout is 30s; ping every 15s
RECONNECT_BASE_DELAY_S = 1.0
RECONNECT_MAX_DELAY_S = 60.0
RECONNECT_MAX_ATTEMPTS = 0    # 0 = infinite
DEFAULT_RECV_TIMEOUT_S = 30   # max wait for a response


# ---------------------------------------------------------------------------
# Subscription tracker
# ---------------------------------------------------------------------------

class _SubscriptionTracker:
    """
    Tracks active subscriptions so they can be replayed after reconnect.

    Each subscription is stored as the original send-request dict.  On replay,
    a fresh req_id is assigned.
    """

    def __init__(self) -> None:
        # sub_key -> original request dict (will be replayed on reconnect)
        self._subs: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _key(request: dict[str, Any]) -> str:
        """Deterministic key from a subscribe request (ignores req_id)."""
        k = {k: v for k, v in request.items() if k not in ("req_id", "passthrough")}
        return json.dumps(k, sort_keys=True)

    def register(self, request: dict[str, Any]) -> str:
        key = self._key(request)
        self._subs[key] = {k: v for k, v in request.items() if k != "req_id"}
        return key

    def unregister_key(self, key: str) -> None:
        self._subs.pop(key, None)

    def unregister_sub_id(self, sub_id: str) -> None:
        """Remove by Deriv subscription id (the uuid in the response)."""
        to_remove = [k for k, v in self._subs.items() if v.get("_sub_id") == sub_id]
        for k in to_remove:
            del self._subs[k]

    def store_sub_id(self, key: str, sub_id: str) -> None:
        if key in self._subs:
            self._subs[key]["_sub_id"] = sub_id

    def get_all(self) -> list[dict[str, Any]]:
        """Return all tracked subscriptions (without internal keys)."""
        result = []
        for v in self._subs.values():
            clean = {k: v2 for k, v2 in v.items() if not k.startswith("_")}
            result.append(clean)
        return result

    def clear(self) -> None:
        self._subs.clear()


# ---------------------------------------------------------------------------
# Main connection class
# ---------------------------------------------------------------------------

class DerivConnection:
    """
    Resilient raw-websocket connection to the Deriv Options public API.

    Parameters
    ----------
    endpoint : str
        WebSocket endpoint.  Default is the production public endpoint.
    """

    def __init__(self, endpoint: str = WS_PUBLIC_ENDPOINT) -> None:
        self.endpoint = endpoint

        self._ws: Optional[websockets.asyncio.client.ClientConnection] = None
        self._connected = False
        self._keepalive_task: Optional[asyncio.Task[None]] = None
        self._recv_task: Optional[asyncio.Task[None]] = None

        self._subs = _SubscriptionTracker()

        # Pending one-shot requests: req_id -> Future
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}

        # Streaming callbacks: req_id -> callback
        self._stream_handlers: dict[int, Callable[[dict[str, Any]], Any]] = {}

        # Internal req_id counter (monotonic, per connection lifetime)
        self._next_req_id = 1

        # Subscription id -> req_id mapping (for forget/replay)
        self._sub_id_to_req_id: dict[str, int] = {}

    # -- Lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Open the websocket connection and start background tasks."""
        log = logger.bind(endpoint=self.endpoint)
        log.info("deriv_connection.connecting")

        self._ws = await websockets.asyncio.client.connect(
            self.endpoint,
            ping_interval=None,   # we handle keepalive ourselves
            ping_timeout=None,
            max_size=2**20,       # 1MB max message
            open_timeout=15,
        )
        self._connected = True
        self._start_keepalive()
        self._start_recv_loop()

        log.info("deriv_connection.connected")

    async def close(self) -> None:
        """Shut down background tasks and close the connection."""
        logger.info("deriv_connection.closing")
        self._stop_keepalive()
        self._stop_recv_loop()
        self._connected = False
        self._subs.clear()

        # Cancel all pending futures
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._stream_handlers.clear()

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        logger.info("deriv_connection.closed")

    @property
    def connected(self) -> bool:
        return self._connected

    # -- Low-level send/recv --------------------------------------------------

    async def send(self, request: dict[str, Any], timeout: float = DEFAULT_RECV_TIMEOUT_S) -> dict[str, Any]:
        """
        Send a one-shot request and wait for the matching response.

        Assigns a req_id automatically.  Returns the full response dict.
        """
        if self._ws is None or not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        req_id = self._next_req_id
        self._next_req_id += 1
        request_with_id = {**request, "req_id": req_id}

        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(json.dumps(request_with_id))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"No response for req_id={req_id} within {timeout}s")
        except Exception:
            self._pending.pop(req_id, None)
            raise

    async def subscribe(
        self,
        request: dict[str, Any],
        handler: Callable[[dict[str, Any]], Any] | None = None,
        timeout: float = DEFAULT_RECV_TIMEOUT_S,
    ) -> str:
        """
        Subscribe to a Deriv stream.

        Parameters
        ----------
        request : dict
            The subscribe request (e.g. {"ticks": "R_75"}).  subscribe=1 is
            added automatically.
        handler : callable, optional
            Callback invoked with each streaming message dict.  If None, you
            must poll via `recv()` yourself.
        timeout : float
            Max seconds to wait for the subscription confirmation.

        Returns
        -------
        str
            The Deriv subscription id (uuid) for this stream.
        """
        if self._ws is None or not self._connected:
            raise RuntimeError("Not connected. Call connect() first.")

        req_id = self._next_req_id
        self._next_req_id += 1
        sub_req = {**request, "subscribe": 1, "req_id": req_id}

        key = self._subs.register(sub_req)
        if handler is not None:
            self._stream_handlers[req_id] = handler

        # Register a pending future so _recv_loop can resolve it
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[req_id] = fut

        try:
            await self._ws.send(json.dumps(sub_req))
            msg = await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, TimeoutError):
            self._pending.pop(req_id, None)
            self._subs.unregister_key(key)
            self._stream_handlers.pop(req_id, None)
            raise TimeoutError(f"No subscription confirmation for req_id={req_id} within {timeout}s")
        except Exception:
            self._pending.pop(req_id, None)
            self._subs.unregister_key(key)
            self._stream_handlers.pop(req_id, None)
            raise

        # Process the confirmation response
        if msg.get("error"):
            self._pending.pop(req_id, None)
            self._subs.unregister_key(key)
            self._stream_handlers.pop(req_id, None)
            raise RuntimeError(f"Subscribe failed: {msg['error']}")

        sub_id = msg.get("subscription", {}).get("id", "")
        self._subs.store_sub_id(key, sub_id)
        self._sub_id_to_req_id[sub_id] = req_id

        logger.info("deriv_connection.subscribed", sub_id=sub_id, req_id=req_id)
        return sub_id

    async def forget(self, sub_id: str) -> bool:
        """Unsubscribe from a stream by subscription id."""
        resp = await self.send({"forget": sub_id})
        ok = resp.get("forget") == 1
        if ok:
            self._subs.unregister_sub_id(sub_id)
            req_id = self._sub_id_to_req_id.pop(sub_id, None)
            if req_id is not None:
                self._stream_handlers.pop(req_id, None)
        return ok

    # -- Convenience: tick methods --------------------------------------------

    async def stream_ticks(
        self,
        symbol: str,
        handler: Callable[[dict[str, Any]], Any],
    ) -> str:
        """
        Subscribe to live ticks for *symbol*.

        Each tick dict: {"tick": {"quote": ..., "epoch": ..., "bid": ..., "ask": ...}}.
        Returns the subscription id.
        """
        return await self.subscribe({"ticks": symbol}, handler=handler)

    async def get_ticks_history(
        self,
        symbol: str,
        start: int | None = None,
        end: str = "latest",
        count: int = 1000,
        style: str = "ticks",
        granularity: int | None = None,
    ) -> dict[str, Any]:
        """
        Fetch historical ticks or candles (one-shot, no subscription).

        Parameters
        ----------
        symbol : str
            E.g. "R_75", "1HZ100V".
        start : int | None
            Epoch timestamp for start.  None = API default (~1 day back).
        end : str
            "latest" or an epoch timestamp string.
        count : int
            Number of ticks/candles.
        style : str
            "ticks" or "candles".
        granularity : int | None
            Candle width in seconds (only for style="candles").
            Allowed: 60, 120, 180, 300, 600, 900, 1800, 3600, 7200, 14400, 28800, 86400.

        Returns
        -------
        dict with "history" (prices + times) or "candles" key.
        """
        req: dict[str, Any] = {
            "ticks_history": symbol,
            "end": end,
            "style": style,
            "count": count,
        }
        if start is not None:
            req["start"] = start
        if granularity is not None and style == "candles":
            req["granularity"] = granularity
        return await self.send(req)

    async def get_active_symbols(self) -> list[dict[str, Any]]:
        """Fetch the list of all active symbols."""
        resp = await self.send({"active_symbols": "full"})
        return resp.get("active_symbols", [])

    async def ping(self) -> str:
        """Send a ping and return "pong" or raise."""
        resp = await self.send({"ping": 1})
        return resp.get("ping", "")

    # -- Background: keepalive ping -------------------------------------------

    def _start_keepalive(self) -> None:
        if self._keepalive_task is not None:
            return
        self._keepalive_task = asyncio.create_task(self._keepalive_loop())

    def _stop_keepalive(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            self._keepalive_task = None

    async def _keepalive_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(KEEPALIVE_INTERVAL_S)
                if self._ws is not None and self._connected:
                    req_id = self._next_req_id
                    self._next_req_id += 1
                    await self._ws.send(json.dumps({"ping": 1, "req_id": req_id}))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("deriv_connection.ping_failed", error=str(exc))
                await self._handle_disconnect()

    # -- Background: receive loop ---------------------------------------------

    def _start_recv_loop(self) -> None:
        if self._recv_task is not None:
            return
        self._recv_task = asyncio.create_task(self._recv_loop())

    def _stop_recv_loop(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            self._recv_task = None

    async def _recv_loop(self) -> None:
        """Route incoming messages to pending futures or stream handlers."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("deriv_connection.invalid_json", raw=raw[:200])
                    continue

                req_id = msg.get("req_id")

                # Route to pending one-shot future
                if req_id is not None and req_id in self._pending:
                    fut = self._pending.pop(req_id)
                    if not fut.done():
                        fut.set_result(msg)
                    continue

                # Route to stream handler
                if req_id is not None and req_id in self._stream_handlers:
                    try:
                        self._stream_handlers[req_id](msg)
                    except Exception as exc:
                        logger.error(
                            "deriv_connection.handler_error",
                            req_id=req_id,
                            error=str(exc),
                        )
                    continue

                # Ping responses from keepalive — just ignore
                if msg.get("msg_type") == "ping":
                    continue

                # Subscription confirmations with no handler — log and ignore
                if msg.get("subscription"):
                    continue

                # Everything else (errors, unexpected)
                logger.debug("deriv_connection.unhandled_msg", msg_type=msg.get("msg_type"), msg=msg)

        except asyncio.CancelledError:
            return
        except websockets.exceptions.ConnectionClosed as exc:
            logger.warning("deriv_connection.connection_closed", code=exc.code, reason=exc.reason)
            await self._handle_disconnect()
        except Exception as exc:
            logger.error("deriv_connection.recv_error", error=str(exc))
            await self._handle_disconnect()

    # -- Reconnect with backoff -----------------------------------------------

    async def _handle_disconnect(self) -> None:
        if not self._connected:
            return  # already handling
        self._connected = False
        self._stop_keepalive()
        self._stop_recv_loop()

        # Fail all pending futures
        for req_id, fut in list(self._pending.items()):
            if not fut.done():
                fut.set_exception(ConnectionError("Disconnected"))
        self._pending.clear()

        logger.warning("deriv_connection.disconnected")

        delay = RECONNECT_BASE_DELAY_S
        attempt = 0

        while True:
            attempt += 1
            if RECONNECT_MAX_ATTEMPTS and attempt > RECONNECT_MAX_ATTEMPTS:
                logger.error("deriv_connection.max_reconnect_attempts", attempts=attempt)
                break

            logger.info(
                "deriv_connection.reconnect_attempt",
                attempt=attempt,
                delay_s=round(delay, 2),
            )
            await asyncio.sleep(delay)

            try:
                self._ws = await websockets.asyncio.client.connect(
                    self.endpoint,
                    ping_interval=None,
                    ping_timeout=None,
                    max_size=2**20,
                    open_timeout=15,
                )
                self._connected = True
                self._start_recv_loop()
                await self._replay_subscriptions()
                self._start_keepalive()
                logger.info("deriv_connection.reconnected", attempt=attempt)
                return
            except Exception as exc:
                logger.warning("deriv_connection.reconnect_failed", attempt=attempt, error=str(exc))
                delay = min(delay * 2, RECONNECT_MAX_DELAY_S)

    async def _replay_subscriptions(self) -> None:
        """Re-subscribe to all tracked subscriptions after reconnect."""
        to_replay = self._subs.get_all()
        self._subs.clear()
        self._sub_id_to_req_id.clear()

        for req in to_replay:
            try:
                await self.subscribe(req)
                logger.info("deriv_connection.replayed_subscription", request=req)
            except Exception as exc:
                logger.warning("deriv_connection.replay_failed", request=req, error=str(exc))
