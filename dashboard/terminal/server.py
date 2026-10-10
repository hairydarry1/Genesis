"""
dashboard/terminal/server.py — WebSocket server for Spike Catcher dashboard

Fixed: Realistic BOOM/CRASH candle generation with spikes
"""

import asyncio
import json
import csv
import time
import math
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import random
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import websockets
except ImportError:
    print("Install websockets: pip install websockets")
    sys.exit(1)


# ── Configuration ────────────────────────────────────────────
SYMBOLS = ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000", "CRASH300", "BOOM300"]

SYMBOL_MAP = {
    "BOOM500": "Boom 500 Index",
    "CRASH500": "Crash 500 Index",
    "BOOM1000": "Boom 1000 Index",
    "CRASH1000": "Crash 1000 Index",
    "CRASH300": "Crash 300 Index",
    "BOOM300": "Boom 300 Index",
}

# BOOM/CRASH parameters
SYMBOL_PARAMS = {
    "BOOM500": {"base": 4697.4, "drift": 0.0003, "spike_size": 0.08, "spike_freq": 0.012, "normal_vol": 0.0003, "digits": 3},
    "CRASH500": {"base": 3042.1, "drift": -0.0003, "spike_size": 0.08, "spike_freq": 0.012, "normal_vol": 0.0003, "digits": 3},
    "BOOM1000": {"base": 15102.3, "drift": 0.0002, "spike_size": 0.06, "spike_freq": 0.008, "normal_vol": 0.0002, "digits": 4},
    "CRASH1000": {"base": 6089.2, "drift": -0.0002, "spike_size": 0.06, "spike_freq": 0.008, "normal_vol": 0.0002, "digits": 4},
    "CRASH300": {"base": 1780.7, "drift": -0.0004, "spike_size": 0.10, "spike_freq": 0.015, "normal_vol": 0.0004, "digits": 3},
    "BOOM300": {"base": 2150.3, "drift": 0.0004, "spike_size": 0.10, "spike_freq": 0.015, "normal_vol": 0.0004, "digits": 3},
}

CONFIG_DIR = Path("SpikeCatcher")
WS_PORT = 8766

clients: set = set()
tracker = None


# ── Generate Realistic BOOM/CRASH Candles ────────────────────
def generate_boom_crash_candles(symbol: str, count: int) -> list:
    """Generate realistic BOOM/CRASH candle data with spikes and buildups."""
    p = SYMBOL_PARAMS.get(symbol, SYMBOL_PARAMS["BOOM500"])
    is_boom = symbol.startswith("BOOM")
    is_crash = symbol.startswith("CRASH")

    candles = []
    price = p["base"]
    now = int(time.time()) - count * 60

    for i in range(count):
        t = now + i * 60
        has_spike = random.random() < p["spike_freq"]

        if has_spike:
            # SPIKE: Large sudden move
            spike_dir = 1 if is_boom else -1
            spike_mag = p["spike_size"] * (0.5 + random.random() * 1.0)

            o = price
            c = o + (spike_mag * spike_dir)
            h = max(o, c) + (0.001 if is_boom else 0)
            l = min(o, c) - (0.001 if is_crash else 0)

            candles.append({
                "time": t,
                "open": round(o, p["digits"]),
                "high": round(h, p["digits"]),
                "low": round(l, p["digits"]),
                "close": round(c, p["digits"]),
                "volume": random.randint(500, 2000),
            })
            price = c
        else:
            # NORMAL: Slow drift with noise
            noise = (random.random() - 0.5) * p["normal_vol"] * 2
            drift = p["drift"] * (0.5 + random.random())

            o = price
            change = drift + noise
            c = o + change
            wick_up = random.random() * abs(change) * 0.3
            wick_down = random.random() * abs(change) * 0.3

            candles.append({
                "time": t,
                "open": round(o, p["digits"]),
                "high": round(max(o, c) + wick_up, p["digits"]),
                "low": round(min(o, c) - wick_down, p["digits"]),
                "close": round(c, p["digits"]),
                "volume": random.randint(50, 250),
            })
            price = c

    return candles


# ── Trade Tracker ────────────────────────────────────────────
class TradeTracker:
    def __init__(self):
        self.symbols = {}
        self.trades = []
        self.candles = {s: [] for s in SYMBOLS}
        self.equity_curves = {s: [] for s in SYMBOLS}
        self.category_reports = {s: {} for s in SYMBOLS}
        self.safety_exits = []
        self.brains = {s: {
            "health": {"score": 0.5, "status": "LEARNING"},
            "learnings": [],
            "config_changes": [],
            "stats": {"trades_analyzed": 0, "patterns_found": 0, "adjustments_made": 0},
            "consec_losses": 0,
        } for s in SYMBOLS}

        for sym in SYMBOLS:
            p = SYMBOL_PARAMS.get(sym, SYMBOL_PARAMS["BOOM500"])
            self.symbols[sym] = {
                "symbol": sym,
                "price": p["base"],
                "equity": 10000,
                "returnPct": 0,
                "drawdown": 0,
                "trades": 0,
                "wins": 0,
                "pnl": 0,
                "position": "FLAT",
                "entryPrice": 0,
                "volRegime": "calm",
                "riskPct": 0.10,
                "kelly": 0,
                "lastTrade": None,
                "spikeCount": 0,
                "safetyExits": 0,
            }

        self._processed = {s: 0 for s in SYMBOLS}
        self._load_existing_trades()

    def _load_existing_trades(self):
        for sym in SYMBOLS:
            mt5_symbol = SYMBOL_MAP.get(sym, sym)
            log_file = CONFIG_DIR / f"trades_{mt5_symbol}.csv"
            if log_file.exists():
                try:
                    with open(log_file, "r") as f:
                        reader = csv.DictReader(f)
                        rows = list(reader)
                    self._processed[sym] = len(rows)
                    for row in rows:
                        trade = {
                            "time": row.get("time", ""),
                            "symbol": sym,
                            "action": row.get("action", ""),
                            "direction": row.get("direction", ""),
                            "entry": float(row.get("entry", 0)),
                            "exit": float(row.get("exit", 0)),
                            "pnl": float(row.get("pnl", 0)),
                            "reason": row.get("reason", ""),
                            "bars_held": int(row.get("bars_held", 0)),
                            "equity": float(row.get("equity", 10000)),
                        }
                        self._process_trade_internal(trade)
                    print(f"  [{sym}] Loaded {len(rows)} existing trades")
                except Exception as e:
                    print(f"  [{sym}] Load error: {e}")

    def _process_trade_internal(self, trade):
        sym = trade["symbol"]
        if trade["action"] == "OPEN":
            side = "LONG" if trade["direction"] == "LONG" else "SHORT"
            self.symbols[sym]["position"] = side
            self.symbols[sym]["entryPrice"] = trade["entry"]
        elif trade["action"] == "CLOSE":
            pnl = trade["pnl"]
            s = self.symbols[sym]
            s["position"] = "FLAT"
            s["trades"] += 1
            if pnl > 0:
                s["wins"] += 1
            s["pnl"] += pnl
            s["equity"] = trade["equity"]
            s["returnPct"] = ((trade["equity"] / 10000) - 1) * 100
            self.equity_curves[sym].append({
                "time": trade["time"],
                "equity": trade["equity"],
                "pnl": pnl,
                "tradeNum": s["trades"],
            })
            report = self.category_reports[sym]
            cat_name = self._classify_trade(trade)
            if cat_name not in report:
                report[cat_name] = {"trades": 0, "wins": 0, "pnl": 0, "winRate": 0}
            cat = report[cat_name]
            cat["trades"] += 1
            if pnl > 0:
                cat["wins"] += 1
            cat["pnl"] += pnl
            cat["winRate"] = (cat["wins"] / cat["trades"]) * 100 if cat["trades"] > 0 else 0

    def read_trades(self, symbol):
        mt5_symbol = SYMBOL_MAP.get(symbol, symbol)
        log_file = CONFIG_DIR / f"trades_{mt5_symbol}.csv"
        if not log_file.exists():
            return []
        trades = []
        try:
            with open(log_file, "r") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            new_rows = rows[self._processed[symbol]:]
            self._processed[symbol] = len(rows)
            for row in new_rows:
                trades.append({
                    "time": row.get("time", ""),
                    "symbol": symbol,
                    "action": row.get("action", ""),
                    "direction": row.get("direction", ""),
                    "entry": float(row.get("entry", 0)),
                    "exit": float(row.get("exit", 0)),
                    "pnl": float(row.get("pnl", 0)),
                    "reason": row.get("reason", ""),
                    "bars_held": int(row.get("bars_held", 0)),
                    "equity": float(row.get("equity", 10000)),
                })
        except Exception as e:
            print(f"  [{symbol}] Read error: {e}")
        return trades

    def process_trade(self, trade):
        sym = trade["symbol"]
        if trade["action"] == "OPEN":
            side = "LONG" if trade["direction"] == "LONG" else "SHORT"
            self.symbols[sym]["position"] = side
            self.symbols[sym]["entryPrice"] = trade["entry"]
            return {
                "type": "open", "symbol": sym,
                "direction": 1 if trade["direction"] == "LONG" else -1,
                "entry_price": trade["entry"], "lots": 0.20,
            }
        elif trade["action"] == "CLOSE":
            pnl = trade["pnl"]
            reason = trade["reason"]
            category = self._classify_trade(trade)
            s = self.symbols[sym]
            s["position"] = "FLAT"
            s["trades"] += 1
            if pnl > 0:
                s["wins"] += 1
            s["pnl"] += pnl
            s["equity"] = trade["equity"]
            s["returnPct"] = ((trade["equity"] / 10000) - 1) * 100
            report = self.category_reports[sym]
            if category not in report:
                report[category] = {"trades": 0, "wins": 0, "pnl": 0, "winRate": 0}
            cat = report[category]
            cat["trades"] += 1
            if pnl > 0:
                cat["wins"] += 1
            cat["pnl"] += pnl
            cat["winRate"] = (cat["wins"] / cat["trades"]) * 100 if cat["trades"] > 0 else 0
            self.equity_curves[sym].append({
                "time": trade["time"], "equity": trade["equity"],
                "pnl": pnl, "tradeNum": s["trades"],
            })
            brain = self.brains[sym]
            brain["stats"]["trades_analyzed"] = s["trades"]
            if pnl < 0:
                brain["consec_losses"] += 1
            else:
                brain["consec_losses"] = 0
            wr = (s["wins"] / s["trades"] * 100) if s["trades"] > 0 else 0
            brain["health"]["score"] = min(wr / 100, 1.0)
            brain["health"]["status"] = "HEALTHY" if wr >= 70 else "CAUTION" if wr >= 55 else "CRITICAL"
            return {
                "type": "close", "symbol": sym, "pnl": pnl,
                "exit_reason": reason, "category": category,
                "diagnosis": "", "equity": trade["equity"],
                "bars_held": trade["bars_held"],
            }
        return None

    def _classify_trade(self, trade):
        reason = trade["reason"]
        pnl = trade["pnl"]
        if reason == "safety_spike":
            return "safety_spike"
        if reason == "tp" and pnl > 0:
            return "good_fade"
        if reason == "sl" and pnl < 0:
            return "late_entry"
        if reason == "timeout":
            return "cluster_continuation"
        return "unknown"

    def get_state(self):
        return {
            "symbols": self.symbols,
            "trades": self.trades[-100:],
            "equityCurves": {k: v[-200:] for k, v in self.equity_curves.items()},
            "candles": {s: self.candles.get(s, [])[-500:] for s in SYMBOLS},
        }


# ── Broadcast ────────────────────────────────────────────────
async def broadcast(msg):
    dead = set()
    for ws in list(clients):
        try:
            await ws.send(json.dumps(msg, default=str))
        except Exception:
            dead.add(ws)
    for d in dead:
        clients.discard(d)


# ── Price Simulation with Spikes ─────────────────────────────
async def simulate_prices():
    """Simulate realistic BOOM/CRASH price movement with spikes."""
    # Track current prices
    current_prices = {}
    for sym in SYMBOLS:
        p = SYMBOL_PARAMS.get(sym, SYMBOL_PARAMS["BOOM500"])
        current_prices[sym] = p["base"]

    # Generate initial candle history
    for sym in SYMBOLS:
        tracker.candles[sym] = generate_boom_crash_candles(sym, 500)
        if tracker.candles[sym]:
            current_prices[sym] = tracker.candles[sym][-1]["close"]

    # Send initial candles to connected clients
    for sym in SYMBOLS:
        await broadcast({
            "type": "candles",
            "symbol": sym,
            "candles": tracker.candles[sym][-200:],
        })

    bar_count = {s: 0 for s in SYMBOLS}
    current_bar = {s: None for s in SYMBOLS}

    while True:
        for sym in SYMBOLS:
            p = SYMBOL_PARAMS.get(sym, SYMBOL_PARAMS["BOOM500"])
            is_boom = sym.startswith("BOOM")
            price = current_prices[sym]

            # Check for spike
            has_spike = random.random() < p["spike_freq"]

            if has_spike:
                # SPIKE!
                spike_dir = 1 if is_boom else -1
                spike_mag = p["spike_size"] * (0.5 + random.random() * 1.0)
                new_price = round(price + (spike_mag * spike_dir), p["digits"])

                # Create spike candle
                now = int(time.time())
                candle = {
                    "time": now,
                    "open": round(price, p["digits"]),
                    "high": round(max(price, new_price) + (0.001 if is_boom else 0), p["digits"]),
                    "low": round(min(price, new_price) - (0.001 if not is_boom else 0), p["digits"]),
                    "close": round(new_price, p["digits"]),
                    "volume": random.randint(800, 2000),
                }

                # Add to candles
                if sym in tracker.candles:
                    tracker.candles[sym].append(candle)
                    if len(tracker.candles[sym]) > 1000:
                        tracker.candles[sym] = tracker.candles[sym][-1000:]

                # Broadcast candle
                await broadcast({"type": "candle", "symbol": sym, "candle": candle})

                # Broadcast spike event
                tracker.symbols[sym]["spikeCount"] += 1
                await broadcast({
                    "type": "spike",
                    "symbol": sym,
                    "size": spike_mag,
                    "direction": "up" if is_boom else "down",
                })

                current_prices[sym] = new_price
            else:
                # Normal bar: slow drift
                noise = (random.random() - 0.5) * p["normal_vol"] * 2
                drift = p["drift"] * (0.5 + random.random())
                change = drift + noise
                new_price = round(price + change, p["digits"])

                now = int(time.time())
                o = price
                c = new_price
                candle = {
                    "time": now,
                    "open": round(o, p["digits"]),
                    "high": round(max(o, c) + random.random() * abs(change) * 0.3, p["digits"]),
                    "low": round(min(o, c) - random.random() * abs(change) * 0.3, p["digits"]),
                    "close": round(c, p["digits"]),
                    "volume": random.randint(50, 250),
                }

                if sym in tracker.candles:
                    tracker.candles[sym].append(candle)
                    if len(tracker.candles[sym]) > 1000:
                        tracker.candles[sym] = tracker.candles[sym][-1000:]

                await broadcast({"type": "candle", "symbol": sym, "candle": candle})
                current_prices[sym] = new_price

            # Always broadcast tick
            tracker.symbols[sym]["price"] = current_prices[sym]
            await broadcast({"type": "tick", "symbol": sym, "price": current_prices[sym]})

        await asyncio.sleep(1)


# ── WebSocket Handler ────────────────────────────────────────
async def ws_handler(websocket, path=None):
    clients.add(websocket)
    print(f"  [WS] Browser connected ({len(clients)} total)")
    try:
        # Send initial state with candles
        init_msg = json.dumps({"type": "init", **tracker.get_state()}, default=str)
        await websocket.send(init_msg)
        print(f"  [WS] Sent init state ({len(tracker.symbols)} symbols, candles included)")

        while True:
            try:
                msg = await asyncio.wait_for(websocket.recv(), timeout=5)
                cmd = json.loads(msg)
                print(f"  [WS] Command: {cmd.get('action')}")
            except asyncio.TimeoutError:
                continue
            except Exception:
                break
    except Exception:
        pass
    finally:
        clients.discard(websocket)
        print(f"  [WS] Browser disconnected")


# ── Main ─────────────────────────────────────────────────────
async def main():
    global tracker
    tracker = TradeTracker()

    print(f"\n{'='*60}")
    print(f"  SPIKE CATCHER DASHBOARD SERVER")
    print(f"{'='*60}")
    print(f"  WebSocket: ws://localhost:{WS_PORT}")
    print(f"  Dashboard: http://localhost:3000")
    print(f"  Symbols: {', '.join(SYMBOLS)}")
    print(f"{'='*60}\n")

    ws_server = await websockets.serve(ws_handler, "localhost", WS_PORT)
    print(f"  [WS] Server running on ws://localhost:{WS_PORT}")

    asyncio.create_task(simulate_prices())
    print(f"  [SIM] Price simulation with spikes started")

    last_poll = 0
    try:
        while True:
            now = time.time()
            if now - last_poll >= 3:
                for sym in SYMBOLS:
                    trades = tracker.read_trades(sym)
                    for trade in trades:
                        event = tracker.process_trade(trade)
                        if event:
                            await broadcast(event)
                last_poll = now
            await asyncio.sleep(0.5)
    except KeyboardInterrupt:
        print("\n  Server stopped")
    finally:
        ws_server.close()


if __name__ == "__main__":
    asyncio.run(main())
