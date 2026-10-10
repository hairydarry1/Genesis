"""
dashboard/server.py — Professional Trading Terminal Server

Combines:
  1. Deriv WebSocket streaming (real-time 1-min candles for all 4 indices)
  2. ICT structure detection (FVG, iFVG, OB, BOS, CHoCH, liquidity sweeps)
  3. World model ensemble predictions
  4. Paper trading state
  5. WebSocket to browser for real-time updates
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from socketserver import ThreadingMixIn

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, str(Path(__file__).parent.parent))

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
INDEX_CONFIGS = {
    "BOOM500": {"short": "BOOM5", "name": "Boom 500", "deriv": "Boom500"},
    "CRASH500": {"short": "CRASH5", "name": "Crash 500", "deriv": "Crash500"},
    "BOOM1000": {"short": "BOOM1K", "name": "Boom 1000", "deriv": "Boom1000"},
    "CRASH1000": {"short": "CRASH1K", "name": "Crash 1000", "deriv": "Crash1000"},
}

WS_PORT = 8765
HTTP_PORT = 8080


# ---------------------------------------------------------------------------
# Threading HTTP Server
# ---------------------------------------------------------------------------
class ThreadingHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


# ---------------------------------------------------------------------------
# Structure Detector (wraps features/structures.py for live candles)
# ---------------------------------------------------------------------------
class LiveStructureDetector:
    """Runs ICT structure detection on live candle data."""

    def __init__(self):
        self._last_bos = 0
        self._last_choch = 0

    def detect(self, candles: list[dict]) -> dict:
        """Detect ICT structures from candle list. Returns overlay data."""
        if len(candles) < 20:
            return {"fvgs": [], "ifvgs": [], "obs": [], "bos": [], "choch": [],
                    "liquidity_sweeps": [], "swing_highs": [], "swing_lows": []}

        df = pd.DataFrame(candles)
        for col in ["open", "high", "low", "close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["timestamp"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.dropna().reset_index(drop=True)

        # Compute ATR
        tr = pd.DataFrame({
            "hl": df["high"] - df["low"],
            "hc": abs(df["high"] - df["close"].shift(1)),
            "lc": abs(df["low"] - df["close"].shift(1)),
        }).max(axis=1)
        atr = tr.rolling(14).mean().fillna(tr.mean())

        # Quality config
        from features.structures import InstrumentQualityCfg
        cfg = InstrumentQualityCfg()

        result = {"fvgs": [], "ifvgs": [], "obs": [], "bos": [], "choch": [],
                   "liquidity_sweeps": [], "swing_highs": [], "swing_lows": []}

        try:
            from features.structures import (
                detect_fvg, detect_ifvg, detect_order_blocks,
                detect_bos, detect_choch, detect_liquidity_sweeps,
                detect_swing_points,
            )

            current_idx = len(df) - 1

            # FVGs (last 50 bars only)
            fvgs = detect_fvg(df, atr, cfg, current_index=current_idx)
            for f in fvgs[-50:]:
                result["fvgs"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "high": f.zone_high,
                    "low": f.zone_low,
                    "direction": f.direction,
                    "size_atr": round(f.size_ATR, 2),
                    "age": f.age_bars,
                })

            # iFVGs
            ifvgs = detect_ifvg(df, atr, cfg, current_index=current_idx)
            for f in ifvgs[-30:]:
                result["ifvgs"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "high": f.zone_high,
                    "low": f.zone_low,
                    "direction": f.direction,
                    "age": f.age_bars,
                })

            # Order Blocks
            obs = detect_order_blocks(df, atr, cfg, current_index=current_idx)
            for f in obs[-30:]:
                result["obs"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "high": f.zone_high,
                    "low": f.zone_low,
                    "direction": f.direction,
                    "age": f.age_bars,
                })

            # Detect swing points first (needed by BOS/CHoCH/sweeps)
            swings = detect_swing_points(df, lookback=5)
            for s in swings[-30:]:
                result["swing_highs" if s.kind == "high" else "swing_lows"].append({
                    "time": int(s.timestamp.timestamp()) if hasattr(s.timestamp, 'timestamp') else 0,
                    "price": s.price,
                })

            # BOS (needs swings)
            bos = detect_bos(df, swings, atr, cfg, current_index=current_idx)
            for f in bos[-20:]:
                result["bos"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "price": f.zone_high if f.direction == "bullish" else f.zone_low,
                    "direction": f.direction,
                    "age": f.age_bars,
                })

            # CHoCH (needs swings)
            choch = detect_choch(df, swings, atr, cfg, current_index=current_idx)
            for f in choch[-20:]:
                result["choch"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "price": f.zone_high if f.direction == "bullish" else f.zone_low,
                    "direction": f.direction,
                    "age": f.age_bars,
                })

            # Liquidity Sweeps (needs swings)
            sweeps = detect_liquidity_sweeps(df, swings, atr, cfg, current_index=current_idx)
            for f in sweeps[-20:]:
                result["liquidity_sweeps"].append({
                    "time": int(f.timestamp.timestamp()) if hasattr(f.timestamp, 'timestamp') else 0,
                    "price": f.zone_high if f.direction == "bullish" else f.zone_low,
                    "direction": f.direction,
                    "age": f.age_bars,
                })



        except Exception as e:
            logger.warning("structure_detection_error: %s", e)

        return result


# ---------------------------------------------------------------------------
# World Model Predictor
# ---------------------------------------------------------------------------
class WorldModelPredictor:
    """Runs world model ensemble predictions on live data."""

    def __init__(self):
        self.ensemble = None
        self.feature_names = []
        self.norm_mean = None
        self.norm_std = None
        self._loaded = False

    def load(self):
        """Load ensemble models."""
        try:
            from model.ensemble import VOL10Ensemble
            self.ensemble = VOL10Ensemble()
            self.ensemble.load()
            self.feature_names = self.ensemble.feature_names

            # Get normalization from checkpoint
            ckpt = torch.load("checkpoints_vol10/world_model_best.pt",
                              map_location="cpu", weights_only=False)
            self.norm_mean = ckpt.get("feat_mean") or ckpt.get("norm_mean")
            self.norm_std = ckpt.get("feat_std") or ckpt.get("norm_std")
            self._loaded = True
            print("  [WM] Ensemble loaded")
        except Exception as e:
            print(f"  [WM] Load failed: {e}")

    def predict(self, features: dict) -> dict:
        """Predict from feature dict. Returns direction, SNR, etc."""
        if not self._loaded:
            return {"direction": "FLAT", "mean": 0, "snr": 0, "regime": "unknown"}

        try:
            feat_arr = np.array([features.get(f, 0.0) for f in self.feature_names],
                                dtype=np.float32)
            if self.norm_mean is not None:
                feat_arr = (feat_arr - self.norm_mean) / (self.norm_std + 1e-8)

            mean, snr = self.ensemble.predict(feat_arr)
            direction = "UP" if mean > 0 else "DOWN" if mean < 0 else "FLAT"

            return {
                "direction": direction,
                "mean": round(float(mean), 8),
                "snr": round(float(snr), 4),
            }
        except Exception as e:
            return {"direction": "ERROR", "mean": 0, "snr": 0, "error": str(e)}


# ---------------------------------------------------------------------------
# Live Data Manager
# ---------------------------------------------------------------------------
class LiveDataManager:
    """Manages live candle data, structure detection, and world model for all indices."""

    def __init__(self):
        self.candles: dict[str, list[dict]] = {s: [] for s in INDEX_CONFIGS}
        self.structure_detector = LiveStructureDetector()
        self.world_model = WorldModelPredictor()
        self.structures: dict[str, dict] = {s: {} for s in INDEX_CONFIGS}
        self.predictions: dict[str, dict] = {s: {} for s in INDEX_CONFIGS}
        self.prices: dict[str, float] = {s: 0 for s in INDEX_CONFIGS}
        self.changes: dict[str, float] = {s: 0 for s in INDEX_CONFIGS}
        self.spreads: dict[str, float] = {s: 0 for s in INDEX_CONFIGS}

        # Paper trading state
        self.positions: dict[str, dict] = {s: {"side": "FLAT", "entry": 0, "pnl": 0} for s in INDEX_CONFIGS}
        self.equity = 10000.0
        self.trade_log: list[dict] = []

        # SMC Spike Hunter state (received from paper_smc.py)
        self.smc_state: dict[str, dict] = {}
        self.smc_trades: list[dict] = []

    def start(self):
        """Start background threads."""
        self.world_model.load()
        self._preload_candles()
        # Start Deriv stream
        self._stream_thread = threading.Thread(target=self._run_stream, daemon=True)
        self._stream_thread.start()

    def _preload_candles(self):
        """Load last 500 candles from parquet files for immediate display."""
        parquet_map = {
            "BOOM500": "data/candles_boom_500_1min.parquet",
            "CRASH500": "data/candles_crash_500_1min.parquet",
            "BOOM1000": "data/candles_boom_1000_1min.parquet",
            "CRASH1000": "data/candles_crash_1000_1min.parquet",
        }
        for symbol, path in parquet_map.items():
            try:
                p = Path(path)
                if p.exists():
                    df = pd.read_parquet(p)
                    # Take last 500 candles
                    df = df.tail(500)
                    candles = []
                    for _, row in df.iterrows():
                        candles.append({
                            "time": int(row["epoch"]) if "epoch" in row.index else int(row.name.timestamp()) if hasattr(row, 'name') else 0,
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row.get("volume", 0)),
                        })
                    self.candles[symbol] = candles
                    if candles:
                        self.prices[symbol] = candles[-1]["close"]
                        self.structures[symbol] = self.structure_detector.detect(candles)
                    print(f"  [PRELOAD] {symbol}: {len(candles)} candles from parquet")
            except Exception as e:
                print(f"  [PRELOAD] {symbol} error: {e}")

    def _run_stream(self):
        """Background thread for Deriv WebSocket."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._stream_all())
        except Exception as e:
            print(f"  [STREAM] Error: {e}")
        finally:
            loop.close()

    async def _stream_all(self):
        """Connect to Deriv and stream all indices."""
        import websockets as ws_lib

        while True:
            try:
                url = "wss://ws.derivws.com/websockets/v3?app_id=1089"
                async with ws_lib.connect(url) as ws:
                    print("  [WS] Connected to Deriv")

                    # Fetch initial candles
                    for symbol, config in INDEX_CONFIGS.items():
                        try:
                            req = {
                                "ticks_history": config["deriv"],
                                "adjust_start_time": 1,
                                "count": 500,
                                "end": "latest",
                                "style": "candles",
                                "granularity": 60,
                            }
                            await ws.send(json.dumps(req))
                            resp = json.loads(await ws.recv())
                            candles = []
                            for c in resp.get("candles", []):
                                candles.append({
                                    "time": int(c["epoch"]),
                                    "open": float(c["open"]),
                                    "high": float(c["high"]),
                                    "low": float(c["low"]),
                                    "close": float(c["close"]),
                                    "volume": float(c.get("volume", 0)),
                                })
                            self.candles[symbol] = candles
                            if candles:
                                self.prices[symbol] = candles[-1]["close"]
                            print(f"  [WS] {symbol}: {len(candles)} candles")
                        except Exception as e:
                            print(f"  [WS] {symbol} history error: {e}")

                    # Run initial structure detection
                    for symbol in INDEX_CONFIGS:
                        if self.candles[symbol]:
                            self.structures[symbol] = self.structure_detector.detect(
                                self.candles[symbol]
                            )

                    # Subscribe to live ticks
                    for symbol, config in INDEX_CONFIGS.items():
                        try:
                            await ws.send(json.dumps({"ticks": config["deriv"]}))
                        except Exception as e:
                            print(f"  [WS] {symbol} subscribe error: {e}")

                    # Listen for ticks
                    while True:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=30)
                            data = json.loads(msg)

                            if "tick" in data:
                                tick = data["tick"]
                                symbol = tick.get("symbol", "")
                                # Find matching index
                                for idx, config in INDEX_CONFIGS.items():
                                    if config["deriv"] == symbol:
                                        price = float(tick.get("quote", 0))
                                        epoch = int(tick.get("epoch", 0))

                                        if price > 0:
                                            self.prices[idx] = price

                                            # Aggregate into 1-min candles
                                            candle_epoch = (epoch // 60) * 60
                                            if self.candles[idx]:
                                                last = self.candles[idx][-1]
                                                if last["time"] == candle_epoch:
                                                    last["high"] = max(last["high"], price)
                                                    last["low"] = min(last["low"], price)
                                                    last["close"] = price
                                                elif candle_epoch > last["time"]:
                                                    self.candles[idx].append({
                                                        "time": candle_epoch,
                                                        "open": price, "high": price,
                                                        "low": price, "close": price,
                                                        "volume": 0,
                                                    })
                                                    # Keep last 1000 candles
                                                    if len(self.candles[idx]) > 1000:
                                                        self.candles[idx] = self.candles[idx][-1000:]
                                                    # Re-detect structures every 5 candles
                                                    if len(self.candles[idx]) % 5 == 0:
                                                        self.structures[idx] = self.structure_detector.detect(
                                                            self.candles[idx]
                                                        )
                                        break

                        except asyncio.TimeoutError:
                            continue
                        except Exception as e:
                            logger.warning("tick_error: %s", e)
                            break

            except Exception as e:
                print(f"  [WS] Connection error: {e}, retrying in 5s...")
                await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# HTTP Handler
# ---------------------------------------------------------------------------
# LiveDataManager.get_state
# ---------------------------------------------------------------------------
def _get_state(self):
    """Get current state for API responses."""
    return {
        "prices": dict(self.prices),
        "candles": {s: len(c) for s, c in self.candles.items()},
        "structures": {s: {k: len(v) if isinstance(v, list) else v for k, v in st.items()} for s, st in self.structures.items()},
        "positions": dict(self.positions),
        "equity": self.equity,
    }

LiveDataManager.get_state = _get_state

def _get_smc_state(self):
    """Get SMC Spike Hunter state for all symbols."""
    return dict(self.smc_state)

LiveDataManager.get_smc_state = _get_smc_state

manager = LiveDataManager()
ws_clients: list = []
ws_loop = None  # Set when WS server starts


class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for dashboard."""

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_file("index.html", "text/html")
        elif self.path == "/api/state":
            self._json_response(manager.get_state())
        elif self.path == "/api/smc":
            self._json_response(manager.get_smc_state())
        elif self.path == "/api/smc/trades":
            self._json_response(manager.smc_trades[-50:])
        elif self.path.startswith("/api/candles/"):
            symbol = self.path.split("/api/candles/")[1]
            candles = manager.candles.get(symbol, [])
            self._json_response(candles[-500:])
        elif self.path.startswith("/api/structures/"):
            symbol = self.path.split("/api/structures/")[1]
            self._json_response(manager.structures.get(symbol, {}))
        elif self.path.startswith("/api/predict/"):
            symbol = self.path.split("/api/predict/")[1]
            pred = manager.predictions.get(symbol, {})
            self._json_response(pred)
        elif self.path.startswith("/api/brain/"):
            # Strategy brain data for a symbol
            symbol = self.path.split("/api/brain/")[1]
            brain_file = Path("checkpoints_tiny") / f"brain_log_{symbol}.json"
            if brain_file.exists():
                try:
                    data = json.loads(brain_file.read_text())
                    self._json_response(data)
                except Exception:
                    self._json_response({"health_score": 0.5, "health_status": "LOADING"})
            else:
                self._json_response({"health_score": 0.5, "health_status": "NO_DATA", "recommendations": ["Start live trading to generate brain data"]})
        elif self.path.startswith("/api/trades/"):
            # Trade log for a symbol
            symbol = self.path.split("/api/trades/")[1]
            trade_file = Path("checkpoints_tiny") / f"deploy_trades_{symbol}.json"
            if trade_file.exists():
                try:
                    trades = json.loads(trade_file.read_text())
                    self._json_response(trades[-50:])
                except Exception:
                    self._json_response([])
            else:
                self._json_response([])
        elif self.path.startswith("/api/journal/"):
            # Brain journal (post-mortem) for a symbol
            symbol = self.path.split("/api/journal/")[1]
            brain_file = Path("checkpoints_tiny") / f"brain_log_{symbol}.json"
            if brain_file.exists():
                try:
                    data = json.loads(brain_file.read_text())
                    self._json_response(data)
                except Exception:
                    self._json_response({"recent_trades": [], "category_report": {}})
            else:
                self._json_response({"recent_trades": [], "category_report": {}})
        else:
            self._json_response({"error": "not found"}, 404)

    def do_POST(self):
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""

        if self.path == "/api/start":
            self._json_response({"status": "started"})
        elif self.path == "/api/stop":
            self._json_response({"status": "stopped"})
        elif self.path == "/api/smc":
            # Receive SMC tick data from paper_smc.py
            try:
                data = json.loads(body)
                symbol = data.get("symbol", "")
                if symbol:
                    manager.smc_state[symbol] = data
                    # Broadcast to browser clients
                    import asyncio
                    for client in ws_clients[:]:
                        try:
                            asyncio.run_coroutine_threadsafe(
                                client.send(json.dumps({"type": "smc_tick", **data})),
                                ws_loop
                            )
                        except Exception:
                            pass
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
        elif self.path == "/api/smc/trade":
            # Receive trade event from paper_smc.py
            try:
                data = json.loads(body)
                manager.smc_trades.append(data)
                # Broadcast to browser clients
                for client in ws_clients[:]:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            client.send(json.dumps({"type": "smc_trade", **data})),
                            ws_loop
                        )
                    except Exception:
                        pass
                self._json_response({"ok": True})
            except Exception as e:
                self._json_response({"error": str(e)}, 400)
        else:
            self._json_response({"error": "not found"}, 404)

    def _serve_file(self, filename: str, content_type: str):
        filepath = Path(__file__).parent / filename
        if filepath.exists():
            content = filepath.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404)

    def _json_response(self, data, status=200):
        content = json.dumps(data, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(content))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs


# ---------------------------------------------------------------------------
# WebSocket Handler (for real-time browser updates)
# ---------------------------------------------------------------------------
async def ws_handler(websocket, path=None):
    """Handle WebSocket connections from browser."""
    ws_clients.append(websocket)
    try:
        # Send initial state
        await websocket.send(json.dumps({
            "type": "init",
            "indices": list(INDEX_CONFIGS.keys()),
            "candles": {s: manager.candles.get(s, [])[-200:] for s in INDEX_CONFIGS},
        }))

        # Keep alive and send updates
        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=1)
            except asyncio.TimeoutError:
                pass

            # Send tick updates
            for symbol in INDEX_CONFIGS:
                state = {
                    "type": "tick",
                    "symbol": symbol,
                    "price": manager.prices.get(symbol, 0),
                    "structures": manager.structures.get(symbol, {}),
                }
                await websocket.send(json.dumps(state, default=str))

            await asyncio.sleep(1)

    except Exception:
        pass
    finally:
        ws_clients.remove(websocket)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Genesis Trading Terminal")
    parser.add_argument("--port", type=int, default=HTTP_PORT)
    parser.add_argument("--ws-port", type=int, default=WS_PORT)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    print("=" * 60)
    print("  GENESIS TRADING TERMINAL")
    print("=" * 60)

    # Start live data
    print("\nStarting live data manager...")
    manager.start()

    # Start WebSocket server in background thread
    import websockets as ws_lib
    global ws_loop

    def _run_ws():
        global ws_loop
        loop = asyncio.new_event_loop()
        ws_loop = loop
        asyncio.set_event_loop(loop)
        try:
            start_server = ws_lib.serve(ws_handler, "localhost", args.ws_port)
            loop.run_until_complete(start_server)
            print(f"  WebSocket server on ws://localhost:{args.ws_port}")
            loop.run_forever()
        except Exception as e:
            print(f"  WebSocket server error: {e}")

    ws_thread = threading.Thread(target=_run_ws, daemon=True)
    ws_thread.start()
    time.sleep(0.5)

    # Start HTTP server
    print(f"  HTTP server on http://localhost:{args.port}")
    print(f"\n  Dashboard ready! Open http://localhost:{args.port}")
    print(f"  SMC data endpoint: POST http://localhost:{args.port}/api/smc")
    http_server = ThreadingHTTPServer(("localhost", args.port), DashboardHandler)

    try:
        http_server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")


if __name__ == "__main__":
    main()
