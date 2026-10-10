"""
dashboard/spike_fade_server.py — Spike Fade Dashboard Server

Receives trade events from paper_trading_v2 via WebSocket and
broadcasts them to the browser dashboard.

Usage:
    python dashboard/spike_fade_server.py

The paper trader connects as a WebSocket client and pushes events.
The browser connects and receives real-time updates.
"""

import asyncio
import json
import logging
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import websockets
except ImportError:
    print("  Install websockets: pip install websockets")
    sys.exit(1)

logger = logging.getLogger(__name__)

DASHBOARD_PORT = 8766
TRADER_PORT = 8767

# State
state = {
    "symbols": {},
    "trades": deque(maxlen=200),
    "equity_curves": {},
    "stats": {},
    "enhancements": {},
    "pattern_alerts": deque(maxlen=50),
    "connected_traders": set(),
    "connected_browsers": set(),
}


def make_initial_state():
    """Initialize per-symbol state."""
    for sym in ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]:
        state["symbols"][sym] = {
            "price": 0,
            "position": "FLAT",
            "entry_price": 0,
            "equity": 10000,
            "return_pct": 0,
            "drawdown": 0,
            "trades": 0,
            "wins": 0,
            "pnl": 0,
            "risk_pct": 0.10,
            "vol_regime": "calm",
            "tp_pct": 0.005,
            "sl_pct": 0.001,
            "max_hold": 8,
            "kelly": 0,
            "filters_blocked": 0,
            "last_trade": None,
            "spike_count": 0,
            "session_active": True,
            "auto_tune": {},
        }
        state["equity_curves"][sym] = []
        state["stats"][sym] = {
            "categories": {},
            "drift_analysis": {},
            "spike_analysis": {},
            "exit_analysis": {},
        }


make_initial_state()


async def broadcast(msg: dict):
    """Send message to all connected browsers."""
    dead = set()
    for ws in state["connected_browsers"]:
        try:
            await ws.send(json.dumps(msg, default=str))
        except Exception:
            dead.add(ws)
    state["connected_browsers"] -= dead


async def handle_trader(ws):
    """Handle connection from paper_trading_v2."""
    state["connected_traders"].add(ws)
    print(f"  [TRADER] Connected ({len(state['connected_traders'])} traders)")
    try:
        async for raw in ws:
            try:
                msg = json.loads(raw)
                await process_trader_event(msg)
            except json.JSONDecodeError:
                pass
    except websockets.ConnectionClosed:
        pass
    finally:
        state["connected_traders"].discard(ws)
        print(f"  [TRADER] Disconnected")


async def process_trader_event(msg: dict):
    """Process an event from the paper trader."""
    event_type = msg.get("type", "")
    sym = msg.get("symbol", "")

    if event_type == "tick":
        if sym in state["symbols"]:
            state["symbols"][sym]["price"] = msg.get("price", 0)
            await broadcast({"type": "tick", "symbol": sym, "price": msg["price"]})

    elif event_type == "open":
        d = msg.get("direction", 0)
        pos = "LONG" if d == 1 else "SHORT" if d == -1 else "FLAT"
        state["symbols"][sym]["position"] = pos
        state["symbols"][sym]["entry_price"] = msg.get("entry_price", 0)
        state["symbols"][sym]["last_trade"] = {
            "action": "OPEN",
            "side": msg.get("direction", ""),
            "price": msg.get("entry_price", 0),
            "size": msg.get("position_size", 0),
            "time": datetime.now(timezone.utc).isoformat(),
            "conviction": msg.get("conviction", 0),
            "reasoning": msg.get("reasoning", ""),
            "spike_x": msg.get("spike_size", 0),
        }
        await broadcast({"type": "open", "symbol": sym, **msg})

    elif event_type == "close":
        pnl = msg.get("pnl", 0)
        won = pnl > 0
        s = state["symbols"][sym]
        s["position"] = "FLAT"
        s["trades"] += 1
        if won:
            s["wins"] += 1
        s["pnl"] += pnl
        s["equity"] = msg.get("equity", s["equity"])
        s["return_pct"] = msg.get("return_pct", 0)
        s["drawdown"] = msg.get("drawdown", 0)

        # Record equity curve point
        state["equity_curves"][sym].append({
            "time": datetime.now(timezone.utc).isoformat(),
            "equity": s["equity"],
            "pnl": pnl,
            "trade_num": s["trades"],
        })

        # Categorize
        cat = msg.get("category", "unknown")
        if cat not in state["stats"][sym]["categories"]:
            state["stats"][sym]["categories"][cat] = {"trades": 0, "wins": 0, "pnl": 0}
        state["stats"][sym]["categories"][cat]["trades"] += 1
        if won:
            state["stats"][sym]["categories"][cat]["wins"] += 1
        state["stats"][sym]["categories"][cat]["pnl"] += pnl

        # Exit reason
        exit_r = msg.get("exit_reason", "unknown")
        if exit_r not in state["stats"][sym]["exit_analysis"]:
            state["stats"][sym]["exit_analysis"][exit_r] = {"trades": 0, "wins": 0, "pnl": 0}
        state["stats"][sym]["exit_analysis"][exit_r]["trades"] += 1
        if won:
            state["stats"][sym]["exit_analysis"][exit_r]["wins"] += 1
        state["stats"][sym]["exit_analysis"][exit_r]["pnl"] += pnl

        s["last_trade"] = {
            "action": "CLOSE",
            "side": msg.get("side", ""),
            "entry": msg.get("entry_price", 0),
            "exit": msg.get("exit_price", 0),
            "pnl": pnl,
            "pnl_pct": msg.get("pnl_pct", 0),
            "reason": exit_r,
            "bars_held": msg.get("bars_held", 0),
            "category": cat,
            "diagnosis": msg.get("diagnosis", ""),
            "conviction": msg.get("conviction", 0),
            "time": datetime.now(timezone.utc).isoformat(),
        }

        # Pattern alert for significant events
        if abs(pnl) > 1 or not won:
            state["pattern_alerts"].append({
                "time": datetime.now(timezone.utc).isoformat(),
                "symbol": sym,
                "type": "loss" if not won else "big_win",
                "pnl": pnl,
                "category": cat,
                "diagnosis": msg.get("diagnosis", ""),
            })

        await broadcast({"type": "close", "symbol": sym, **msg})

    elif event_type == "status":
        if sym in state["symbols"]:
            s = state["symbols"][sym]
            s["vol_regime"] = msg.get("vol_regime", s["vol_regime"])
            s["tp_pct"] = msg.get("tp_pct", s["tp_pct"])
            s["sl_pct"] = msg.get("sl_pct", s["sl_pct"])
            s["max_hold"] = msg.get("max_hold", s["max_hold"])
            s["kelly"] = msg.get("kelly", s["kelly"])
            s["risk_pct"] = msg.get("risk_pct", s["risk_pct"])
            s["filters_blocked"] = msg.get("filters_blocked", s["filters_blocked"])
            s["spike_count"] = msg.get("spike_count", s["spike_count"])
            s["session_active"] = msg.get("session_active", s["session_active"])
            s["auto_tune"] = msg.get("auto_tune", s["auto_tune"])
            await broadcast({"type": "status", "symbol": sym, **msg})

    elif event_type == "filter_alert":
        state["pattern_alerts"].append({
            "time": datetime.now(timezone.utc).isoformat(),
            "symbol": sym,
            "type": "filter",
            "reason": msg.get("reason", ""),
            "drift": msg.get("drift", 0),
            "spike_x": msg.get("spike_x", 0),
        })
        await broadcast({"type": "filter_alert", "symbol": sym, **msg})


async def handle_browser(ws):
    """Handle connection from browser dashboard."""
    state["connected_browsers"].add(ws)
    print(f"  [BROWSER] Connected ({len(state['connected_browsers'])} browsers)")

    # Send full state on connect
    try:
        await ws.send(json.dumps({
            "type": "init",
            "symbols": dict(state["symbols"]),
            "trades": list(state["trades"])[-50:],
            "equity_curves": {k: list(v)[-200:] for k, v in state["equity_curves"].items()},
            "stats": dict(state["stats"]),
            "alerts": list(state["pattern_alerts"])[-20:],
            "control": {
                "trading_active": state.get("trading_active", True),
                "risk_override": state.get("risk_override", {}),
            },
        }, default=str))
    except Exception:
        pass

    try:
        async for raw in ws:
            try:
                cmd = json.loads(raw)
                await process_browser_command(cmd)
            except json.JSONDecodeError:
                pass
    except websockets.ConnectionClosed:
        pass
    finally:
        state["connected_browsers"].discard(ws)
        print(f"  [BROWSER] Disconnected")


async def process_browser_command(cmd: dict):
    """Process commands from the browser control panel."""
    action = cmd.get("action", "")

    if action == "pause_trading":
        state["trading_active"] = False
        # Write control file for paper trader
        try:
            ctrl_path = Path(__file__).parent.parent / 'logs' / 'control.json'
            ctrl_path.parent.mkdir(parents=True, exist_ok=True)
            ctrl_path.write_text(json.dumps({"trading_active": False}))
        except Exception:
            pass
        await broadcast({"type": "control_update", "trading_active": False})
        print(f"  [CONTROL] Trading PAUSED")

    elif action == "resume_trading":
        state["trading_active"] = True
        try:
            ctrl_path = Path(__file__).parent.parent / 'logs' / 'control.json'
            ctrl_path.parent.mkdir(parents=True, exist_ok=True)
            ctrl_path.write_text(json.dumps({"trading_active": True}))
        except Exception:
            pass
        await broadcast({"type": "control_update", "trading_active": True})
        print(f"  [CONTROL] Trading RESUMED")

    elif action == "set_risk":
        symbol = cmd.get("symbol", "")
        risk = cmd.get("risk", 0.10)
        state.setdefault("risk_override", {})[symbol] = risk
        for trader_ws in state["connected_traders"]:
            try:
                await trader_ws.send(json.dumps({
                    "type": "control", "action": "set_risk",
                    "symbol": symbol, "risk": risk,
                }))
            except Exception:
                pass
        await broadcast({"type": "control_update", "risk_override": state["risk_override"]})
        print(f"  [CONTROL] {symbol} risk set to {risk*100:.0f}%")

    elif action == "get_status":
        await broadcast({
            "type": "control_update",
            "trading_active": state.get("trading_active", True),
            "risk_override": state.get("risk_override", {}),
            "connected_traders": len(state["connected_traders"]),
        })


async def main():
    print(f"\n{'='*60}")
    print(f"  SPIKE FADE DASHBOARD SERVER")
    print(f"{'='*60}")
    print(f"  Browser dashboard:  http://localhost:{DASHBOARD_PORT}")
    print(f"  Trader WebSocket:   ws://localhost:{TRADER_PORT}")
    print(f"{'='*60}\n")

    async with (
        websockets.serve(handle_browser, "localhost", DASHBOARD_PORT),
        websockets.serve(handle_trader, "localhost", TRADER_PORT),
    ):
        print("  Server running. Press Ctrl+C to stop.\n")
        await asyncio.Future()  # run forever


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n  Server stopped.")
