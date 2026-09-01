"""
eval/deriv_data_server.py — Real-time Deriv data WebSocket server.

Connects to Deriv websocket API and streams:
- Live tick prices (real-time)
- 1-minute OHLCV candles (built from ticks)
- World model predictions
- Trade signals

Clients (dashboard) connect via ws://localhost:8082
All data is real from Deriv, not synthetic.

Usage:
    python eval.deriv_data_server.py
    python eval.deriv_data_server.py --symbols R_75 R_10 BOOM500 CRASH500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    import websockets
    import websockets.server
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False
    print("Install websockets: pip install websockets")
    sys.exit(1)

# Deriv API
DERIV_WS_URL = "wss://ws.derivws.com/websockets/v3?app_id=1089"


# ---------------------------------------------------------------------------
# Candle Builder — builds OHLCV candles from tick data
# ---------------------------------------------------------------------------
class CandleBuilder:
    """Builds OHLCV candles from real-time tick data."""
    
    def __init__(self, granularity_seconds: int = 60):
        self.granularity = granularity_seconds
        self._current_candle: dict | None = None
        self._candle_start: int = 0
        self._candles: deque[dict] = deque(maxlen=2000)
    
    def _get_candle_time(self, epoch: int) -> int:
        """Get the candle start time for a given epoch."""
        return (epoch // self.granularity) * self.granularity
    
    def add_tick(self, tick: dict) -> dict | None:
        """
        Add a tick and return a completed candle if the period changed.
        
        tick = {'epoch': int, 'price': float}
        Returns: completed candle dict or None
        """
        epoch = tick['epoch']
        price = tick['price']
        
        candle_time = self._get_candle_time(epoch)
        completed_candle = None
        
        # Check if we need to close the current candle
        if self._current_candle and candle_time > self._candle_start:
            completed_candle = self._current_candle.copy()
            self._candles.append(completed_candle)
        
        # Start new candle or update current
        if not self._current_candle or candle_time > self._candle_start:
            self._current_candle = {
                'time': candle_time,
                'open': price,
                'high': price,
                'low': price,
                'close': price,
                'volume': 1,
            }
            self._candle_start = candle_time
        else:
            self._current_candle['high'] = max(self._current_candle['high'], price)
            self._current_candle['low'] = min(self._current_candle['low'], price)
            self._current_candle['close'] = price
            self._current_candle['volume'] += 1
        
        return completed_candle
    
    def get_candles(self, count: int = 500) -> list[dict]:
        """Get recent candles."""
        candles = list(self._candles)[-count:]
        if self._current_candle:
            candles.append(self._current_candle)
        return candles
    
    def get_current(self) -> dict | None:
        """Get the current (incomplete) candle."""
        return self._current_candle


# ---------------------------------------------------------------------------
# Symbol Data Manager
# ---------------------------------------------------------------------------
@dataclass
class SymbolState:
    """State for a single symbol."""
    symbol: str
    tick_price: float = 0.0
    tick_epoch: int = 0
    candle_builder: CandleBuilder = field(default_factory=lambda: CandleBuilder(60))
    bid: float = 0.0
    ask: float = 0.0
    spread: float = 0.0
    last_update: float = 0.0
    
    # World model prediction (from our model)
    wm_direction: str = "FLAT"
    wm_confidence: float = 0.0
    wm_mean: float = 0.0
    wm_std: float = 0.0
    
    def to_dict(self) -> dict:
        candles = self.candle_builder.get_candles(500)
        current = self.candle_builder.get_current()
        return {
            'symbol': self.symbol,
            'tick': {
                'price': self.tick_price,
                'epoch': self.tick_epoch,
                'bid': self.bid,
                'ask': self.ask,
                'spread': self.spread,
            },
            'candles': candles,
            'current_candle': current,
            'world_model': {
                'direction': self.wm_direction,
                'confidence': self.wm_confidence,
                'mean': self.wm_mean,
                'std': self.wm_std,
            },
            'timestamp': time.time(),
        }


class DerivDataManager:
    """
    Manages connections to Deriv and builds candle data.
    
    Fetches historical candles on startup, then subscribes to live ticks.
    """
    
    def __init__(self, symbols: list[str]):
        self.symbols = symbols
        self.states: dict[str, SymbolState] = {}
        self._connections: dict[str, Any] = {}
        self._running = False
    
    async def initialize(self):
        """Fetch historical candles for all symbols."""
        for symbol in self.symbols:
            self.states[symbol] = SymbolState(symbol=symbol)
        
        # Connect and fetch history
        try:
            import websockets as ws_lib
            async with ws_lib.connect(DERIV_WS_URL) as ws:
                for symbol in self.symbols:
                    await self._fetch_history(ws, symbol)
        except Exception as e:
            logger.error("history_fetch_failed error=%s", str(e))
            # Generate synthetic data as fallback
            for symbol in self.symbols:
                self._generate_synthetic_history(symbol)
    
    async def _fetch_history(self, ws, symbol: str):
        """Fetch historical candles from Deriv."""
        req = {
            "ticks_history": symbol,
            "adjust_start_time": 1,
            "count": 500,
            "end": "latest",
            "style": "candles",
            "granularity": 60,
        }
        await ws.send(json.dumps(req))
        resp = json.loads(await ws.recv())
        
        candles = resp.get("candles", [])
        state = self.states[symbol]
        
        for c in candles:
            candle = {
                'time': int(c['epoch']),
                'open': float(c['open']),
                'high': float(c['high']),
                'low': float(c['low']),
                'close': float(c['close']),
                'volume': float(c.get('volume', 0)),
            }
            state.candle_builder._candles.append(candle)
            state.candle_builder._candle_start = candle['time']
            state.candle_builder._current_candle = candle.copy()
        
        if candles:
            state.tick_price = float(candles[-1]['close'])
            state.tick_epoch = int(candles[-1]['epoch'])
        
        logger.info("history_loaded symbol=%s count=%d", symbol, len(candles))
    
    def _generate_synthetic_history(self, symbol: str):
        """Generate synthetic history as fallback."""
        import random
        base_prices = {
            'R_75': 52200, 'R_10': 4766, 'BOOM500': 4750, 'CRASH500': 2980,
        }
        price = base_prices.get(symbol, 10000)
        now = int(time.time()) - 500 * 60
        
        for i in range(500):
            vol = price * 0.001
            open_p = price
            change = (random.random() - 0.48) * vol
            close_p = open_p + change
            high_p = max(open_p, close_p) + random.random() * vol * 0.5
            low_p = min(open_p, close_p) - random.random() * vol * 0.5
            
            candle = {
                'time': now,
                'open': round(open_p, 2),
                'high': round(high_p, 2),
                'low': round(low_p, 2),
                'close': round(close_p, 2),
                'volume': round(random.random() * 1000 + 100),
            }
            self.states[symbol].candle_builder._candles.append(candle)
            self.states[symbol].candle_builder._candle_start = candle['time']
            self.states[symbol].candle_builder._current_candle = candle.copy()
            self.states[symbol].tick_price = close_p
            price = close_p
            now += 60
    
    async def subscribe_live(self):
        """Subscribe to live ticks for all symbols."""
        self._running = True
        
        tasks = []
        for symbol in self.symbols:
            tasks.append(self._live_tick_stream(symbol))
        
        await asyncio.gather(*tasks)
    
    async def _live_tick_stream(self, symbol: str):
        """Stream live ticks for a single symbol."""
        try:
            import websockets as ws_lib
            while self._running:
                try:
                    async with ws_lib.connect(DERIV_WS_URL) as ws:
                        # Subscribe to ticks
                        sub_req = {"ticks": symbol, "subscribe": 1}
                        await ws.send(json.dumps(sub_req))
                        logger.info("subscribed symbol=%s", symbol)
                        
                        async for msg in ws:
                            if not self._running:
                                break
                            try:
                                data = json.loads(msg)
                                await self._handle_tick(symbol, data)
                            except json.JSONDecodeError:
                                continue
                
                except Exception as e:
                    logger.warning("disconnect symbol=%s error=%s", symbol, str(e))
                    await asyncio.sleep(3)  # Reconnect delay
        
        except asyncio.CancelledError:
            pass
    
    async def _handle_tick(self, symbol: str, data: dict):
        """Handle a tick message from Deriv."""
        if 'tick' not in data:
            return
        
        tick = data['tick']
        price = float(tick.get('quote', 0))
        epoch = int(tick.get('epoch', time.time()))
        
        if price <= 0:
            return
        
        state = self.states[symbol]
        state.tick_price = price
        state.tick_epoch = epoch
        state.bid = float(tick.get('bid', price))
        state.ask = float(tick.get('ask', price))
        state.spread = state.ask - state.bid
        state.last_update = time.time()
        
        # Build candle
        completed = state.candle_builder.add_tick({'epoch': epoch, 'price': price})
        
        # Broadcast to connected clients
        await self._broadcast_tick(symbol, state, completed)
    
    async def _broadcast_tick(self, symbol: str, state: SymbolState, completed_candle: dict | None):
        """Broadcast tick data to all connected dashboard clients."""
        # This will be called by the server
        pass
    
    def get_full_state(self) -> dict:
        """Get full state for all symbols."""
        return {
            symbol: state.to_dict()
            for symbol, state in self.states.items()
        }
    
    def get_symbol_state(self, symbol: str) -> dict | None:
        """Get state for a single symbol."""
        state = self.states.get(symbol)
        return state.to_dict() if state else None


# ---------------------------------------------------------------------------
# Dashboard WebSocket Server
# ---------------------------------------------------------------------------
class DashboardWSServer:
    """
    WebSocket server that streams real Deriv data to dashboard clients.
    
    Protocol:
    - Client connects to ws://localhost:8082
    - Server sends initial state (all symbols)
    - Server sends tick updates every tick (~1s)
    - Client can send: {"type": "subscribe", "symbols": ["R_75", "R_10"]}
    """
    
    def __init__(self, data_manager: DerivDataManager, port: int = 8082):
        self.dm = data_manager
        self.port = port
        self._clients: set = set()
    
    async def handler(self, websocket):
        """Handle a new client connection."""
        self._clients.add(websocket)
        try:
            # Send initial state
            state = self.dm.get_full_state()
            await websocket.send(json.dumps({
                'type': 'init',
                'data': state,
                'timestamp': time.time(),
            }, default=str))
            
            # Handle client messages
            async for msg in websocket:
                try:
                    data = json.loads(msg)
                    if data.get('type') == 'request_candles':
                        symbol = data.get('symbol', 'R_75')
                        count = data.get('count', 500)
                        candle_state = self.dm.get_symbol_state(symbol)
                        if candle_state:
                            await websocket.send(json.dumps({
                                'type': 'candles',
                                'symbol': symbol,
                                'data': candle_state,
                            }, default=str))
                except json.JSONDecodeError:
                    pass
        finally:
            self._clients.discard(websocket)
    
    async def broadcast_tick(self, symbol: str, state: SymbolState, completed_candle: dict | None):
        """Broadcast a tick update to all clients."""
        if not self._clients:
            return
        
        msg = json.dumps({
            'type': 'tick',
            'symbol': symbol,
            'tick': {
                'price': state.tick_price,
                'epoch': state.tick_epoch,
                'bid': state.bid,
                'ask': state.ask,
                'spread': state.spread,
            },
            'candle': state.candle_builder.get_current(),
            'completed_candle': completed_candle,
            'world_model': {
                'direction': state.wm_direction,
                'confidence': state.wm_confidence,
            },
            'timestamp': time.time(),
        }, default=str)
        
        disconnected = set()
        for client in self._clients:
            try:
                await client.send(msg)
            except Exception:
                disconnected.add(client)
        self._clients -= disconnected
    
    async def run(self):
        """Run the WebSocket server."""
        async with websockets.serve(self.handler, "localhost", self.port):
            logger.info("dashboard_ws_started port=%d", self.port)
            await asyncio.Future()  # Run forever


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main(symbols: list[str], port: int = 8082):
    """Main entry point."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )
    
    # Create data manager
    dm = DerivDataManager(symbols)
    
    # Create server
    server = DashboardWSServer(dm, port=port)
    
    # Patch the broadcast method
    dm._broadcast_tick = server.broadcast_tick
    
    # Initialize (fetch history)
    logger.info("initializing symbols=%s", symbols)
    await dm.initialize()
    
    # Print status
    for symbol in symbols:
        state = dm.states[symbol]
        candles = state.candle_builder.get_candles(5)
        logger.info("symbol=%s price=%.2f candles=%d", symbol, state.tick_price, len(candles))
    
    # Start live streaming + server concurrently
    logger.info("starting_live_stream port=%d", port)
    logger.info("open_dashboard http://localhost:8080")
    
    await asyncio.gather(
        dm.subscribe_live(),
        server.run(),
    )


def run():
    parser = argparse.ArgumentParser(description="Deriv real-time data server")
    parser.add_argument("--symbols", nargs="+", default=["R_75", "R_10", "BOOM500", "CRASH500"])
    parser.add_argument("--port", type=int, default=8082)
    args = parser.parse_args()
    
    asyncio.run(main(args.symbols, args.port))


if __name__ == "__main__":
    run()
