"""
eval/paper_crash300.py — Crash 300 Live Paper Trader

Combines:
  1. Buy The Dip entry (after spike down, price retraces 86%)
  2. Velocity Exhaustion exit (detect spike BEFORE it hits, exit early)
  3. M5 trend filter (only buy in uptrend)
  4. Brain integration (post-mortem analysis, adaptive learning)

Usage:
    python eval/paper_crash300.py --equity 200 --risk 0.02
    python eval/paper_crash300.py --equity 200 --risk 0.02 --no-velocity
"""

import asyncio
import argparse
import json
import signal
import sys
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.velocity_exhaustion import VelocityExhaustionEngine, VelocityConfig
from model.strategy_brain import StrategyBrain
from model.trade_thinker import MarketContext, TradeOutcome

DERIV_APP_ID = 1089
DERIV_WS_URL = f"wss://api.derivws.com/trading/v1/options/ws/public?app_id={DERIV_APP_ID}"

SYMBOL = "Crash 300 Index"


class Crash300Trader:
    """
    Live paper trader for Crash 300 using velocity exhaustion.
    
    Architecture:
      Tick Stream → M1 Bar Aggregation → Velocity Engine → Trade Signals
      
    Entry: Buy the dip after spike down
    Exit: TP, SL, velocity warning, spike_against, timeout
    """
    
    def __init__(self, equity: float = 200.0, risk_pct: float = 0.02,
                 velocity_exit: bool = True):
        self.symbol = SYMBOL
        self.equity = equity
        self.initial_equity = equity
        self.peak_equity = equity
        self.risk_pct = risk_pct
        
        # Velocity engine
        cfg = VelocityConfig(
            symbol=SYMBOL,
            symbol_code="CRASH300",
            velocity_exit_enabled=velocity_exit,
            entry_bars_after_spike=2,
            tp_range_mult=2.0,
            sl_range_mult=1.0,
            risk_pct=risk_pct,
            dollar_per_point=0.20,
            max_hold_bars=9,
        )
        self.engine = VelocityExhaustionEngine(cfg)
        self.engine.equity = equity
        self.engine.initial_equity = equity
        self.engine.peak_equity = equity
        
        # Brain
        self.brain = StrategyBrain("CRASH300")
        self._trade_id = 0
        self._pending_entry = None
        
        # State
        self._m1_bars = []
        self._current_bar = None
        self._tick_count = 0
        self._bar_count = 0
        self._start_time = time.time()
        self._running = True
        self._all_trades = []
        
        # Dashboard
        self._dashboard_url = "http://localhost:8080"
        self._last_push = 0
        
        # Velocity monitoring
        self._vel_history = []
        self._vel_warnings = 0
        
    def process_tick(self, tick: dict):
        """Process live tick — aggregate into M1 bars."""
        price = float(tick.get("quote", 0))
        ts = tick.get("epoch", int(time.time()))
        
        if price <= 0:
            return
        
        self._tick_count += 1
        bar_minute = ts // 60
        
        # Update velocity engine with tick
        vel_data = self.engine.update_velocity(price)
        self._vel_history.append(vel_data)
        if len(self._vel_history) > 100:
            self._vel_history.pop(0)
        
        if vel_data.get("warning"):
            self._vel_warnings += 1
        
        # Aggregate into M1 bars
        if self._current_bar is None or bar_minute != self._current_bar.get("_minute"):
            if self._current_bar is not None:
                self._finalize_bar()
            
            self._current_bar = {
                "_minute": bar_minute,
                "epoch": bar_minute * 60,
                "open": price, "high": price,
                "low": price, "close": price,
            }
        else:
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price
    
    def _finalize_bar(self):
        """Close bar and run strategy."""
        bar = {k: v for k, v in self._current_bar.items() if not k.startswith("_")}
        self._m1_bars.append(bar)
        self._bar_count += 1
        idx = len(self._m1_bars) - 1
        
        # Run velocity engine
        result = self.engine.on_bar(bar, idx, self.equity)
        
        if result:
            if result["action"] == "open":
                self._handle_open(result)
            elif result["action"] == "close":
                self._handle_close(result)
        
        # Update equity
        self.equity = self.engine.equity
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        
        # Status every 10 bars
        if self._bar_count % 10 == 0:
            self._display_status()
        
        # Dashboard push
        self._push_dashboard()
    
    def _handle_open(self, result: dict):
        """Handle trade open."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        side = result.get("side", "?").upper()
        entry = result.get("entry_price", 0)
        tp = result.get("tp", 0)
        sl = result.get("sl", 0)
        vel_trend = result.get("velocity_trend", "normal")
        
        print(f"\n  [{ts}] 🔵 CRASH300 OPEN {side} @ {entry:.2f}")
        print(f"           TP={tp:.2f} SL={sl:.2f} Vel={vel_trend}")
        
        self._pending_entry = {
            "entry_price": entry,
            "direction": 1,
            "strategy": "velocity_dip",
            "time": ts,
        }
        
        # Brain context
        ctx = MarketContext()
        ctx.price = entry
        ctx.conviction = 0.6  # Default for velocity dip
        self._pending_context = ctx
    
    def _handle_close(self, result: dict):
        """Handle trade close."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        pnl = result.get("pnl_dollars", 0)
        reason = result.get("exit_reason", "?")
        exit_p = result.get("exit_price", 0)
        emoji = "✅" if pnl > 0 else "❌"
        
        print(f"  [{ts}] {emoji} CRASH300 CLOSE @ {exit_p:.2f} "
              f"| PnL=${pnl:+.4f} | {reason} "
              f"| Equity=${self.equity:,.4f}")
        
        self._all_trades.append({
            "time": ts, "symbol": "CRASH300",
            "entry": self._pending_entry["entry_price"] if self._pending_entry else 0,
            "exit": exit_p, "pnl": pnl, "reason": reason,
        })
        
        # Brain analysis
        if self._pending_entry:
            self._trade_id += 1
            ctx = MarketContext()
            ctx.price = self._pending_entry["entry_price"]
            ctx.conviction = 0.6
            
            outcome = TradeOutcome(
                trade_id=self._trade_id,
                entry_price=self._pending_entry["entry_price"],
                exit_price=exit_p,
                pnl_pct=pnl / max(self.equity, 1) * 100,
                pnl_dollars=pnl,
                direction=1,
                bars_held=0,
                exit_reason=reason,
                context=ctx,
            )
            enriched = self.brain.analyze(outcome)
            if enriched.diagnosis:
                print(f"  [{ts}] [BRAIN] {enriched.category}: {enriched.diagnosis[:80]}")
        
        self._pending_entry = None
    
    def _push_dashboard(self):
        """Push state to dashboard."""
        import urllib.request
        now = time.time()
        if now - self._last_push < 3.0:
            return
        self._last_push = now
        
        try:
            stats = self.engine.get_stats()
            vel_data = self._vel_history[-1] if self._vel_history else {}
            
            payload = {
                "symbol": "CRASH300",
                "price": self._m1_bars[-1]["close"] if self._m1_bars else 0,
                "equity": self.equity,
                "initial_equity": self.initial_equity,
                "peak_equity": self.peak_equity,
                "drawdown": (1 - self.equity / self.peak_equity) * 100 if self.peak_equity > 0 else 0,
                "velocity": {
                    "current": vel_data.get("velocity", 0),
                    "avg": vel_data.get("avg_velocity", 0),
                    "trend": vel_data.get("trend", "normal"),
                    "warnings": self._vel_warnings,
                    "consecutive_upticks": vel_data.get("consecutive_upticks", 0),
                    "ticks_since_spike": vel_data.get("ticks_since_spike", 0),
                },
                "engine": {
                    "position": "LONG" if self.engine.trade.in_position else "FLAT",
                    "entry_price": self.engine.trade.entry_price if self.engine.trade.in_position else 0,
                    "unrealized_pnl": self.engine.trade.unrealized_pnl if self.engine.trade.in_position else 0,
                    "bars_held": self.engine.trade.bars_held if self.engine.trade.in_position else 0,
                    "tp": self.engine.trade.tp_price if self.engine.trade.in_position else 0,
                    "sl": self.engine.trade.sl_price if self.engine.trade.in_position else 0,
                },
                "performance": {
                    "trades": stats.get("trades", 0),
                    "win_rate": stats.get("win_rate", 0) * 100,
                    "profit_factor": stats.get("profit_factor", 0),
                    "total_pnl": stats.get("total_pnl", 0),
                    "return_pct": stats.get("return_pct", 0),
                    "max_drawdown": stats.get("max_drawdown", 0) * 100,
                    "velocity_exits": stats.get("velocity_exits", 0),
                },
                "brain": self.brain.get_dashboard_data(),
                "uptime_min": (time.time() - self._start_time) / 60,
            }
            req = urllib.request.Request(
                f"{self._dashboard_url}/api/state",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass
    
    def _display_status(self):
        """Print status to console."""
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        elapsed = (time.time() - self._start_time) / 60
        price = self._m1_bars[-1]["close"] if self._m1_bars else 0
        stats = self.engine.get_stats()
        vel = self._vel_history[-1] if self._vel_history else {}
        
        in_pos = self.engine.trade.in_position
        pos_str = "LONG" if in_pos else "FLAT"
        entry_str = f"@{self.engine.trade.entry_price:.2f}" if in_pos else ""
        upnl = self.engine.trade.unrealized_pnl if in_pos else 0
        
        vel_trend = vel.get("trend", "normal")
        vel_emoji = "🔴" if vel_trend == "exhausted" else "🟡" if vel_trend == "accelerated" else "🟢"
        
        print(f"\n  {'='*64}")
        print(f"  CRASH300 VELOCITY DIP @ {ts}  ({elapsed:.0f} min)")
        print(f"  {'='*64}")
        print(f"  Price: {price:.2f}  |  Equity: ${self.equity:,.4f}  "
              f"|  DD: {(1-self.equity/max(self.peak_equity,1))*100:.2f}%")
        print(f"  Pos: {pos_str} {entry_str}  |  UPnL: ${upnl:+.4f}  "
              f"|  Ticks: {self._tick_count}  |  Bars: {self._bar_count}")
        print(f"  {vel_emoji} Vel: {vel_trend}  |  "
              f"Upticks: {vel.get('consecutive_upticks', 0)}  |  "
              f"Since spike: {vel.get('ticks_since_spike', 0)}")
        print(f"  {'─'*64}")
        if stats.get("trades", 0) > 0:
            print(f"  Trades: {stats['trades']}  |  WR: {stats['win_rate']:.1%}  |  "
                  f"PF: {stats['profit_factor']:.2f}  |  "
                  f"PnL: ${stats['total_pnl']:+.4f}")
            print(f"  Vel Exits: {stats['velocity_exits']}  |  "
                  f"Vel Warnings: {self._vel_warnings}  |  "
                  f"Return: {stats['return_pct']:+.1f}%")
        print(f"  {'='*64}")
    
    def save_trades(self):
        """Save trade log."""
        out_dir = Path("checkpoints_tiny")
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / "paper_trades_crash300.json"
        
        with open(out_file, "w") as f:
            json.dump(self._all_trades, f, indent=2, default=str)
        print(f"  Saved {len(self._all_trades)} trades → {out_file}")
        
        # Save brain state
        self.brain._save_state()
        self.brain.print_summary()


async def run_trader(trader: Crash300Trader):
    """Run the Crash 300 trader connected to Deriv WebSocket."""
    import websockets
    
    print(f"\n  Connecting to Deriv WebSocket for {SYMBOL}...")
    
    async with websockets.connect(DERIV_WS_URL) as ws:
        subscribe_msg = {"ticks": SYMBOL, "subscribe": 1}
        await ws.send(json.dumps(subscribe_msg))
        
        response = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(response)
        if resp.get("error"):
            print(f"  Error: {resp['error']}")
            return
        
        print(f"  ✅ Connected! Streaming live ticks for Crash 300...")
        print(f"  Risk: {trader.risk_pct*100:.1f}%/trade  |  "
              f"Velocity exit: {'ON' if trader.engine.cfg.velocity_exit_enabled else 'OFF'}")
        print(f"  TP: 2x range  |  SL: 1x range  |  Max hold: 9 bars")
        print()
        
        async for message in ws:
            if not trader._running:
                break
            try:
                data = json.loads(message)
                if "tick" in data:
                    trader.process_tick(data["tick"])
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"  Error: {e}")
                continue


def main():
    parser = argparse.ArgumentParser(description="Crash 300 Velocity Exhaustion Paper Trader")
    parser.add_argument("--equity", type=float, default=200.0, help="Starting equity")
    parser.add_argument("--risk", type=float, default=0.02, help="Risk per trade (fraction)")
    parser.add_argument("--no-velocity", action="store_true", help="Disable velocity exit")
    args = parser.parse_args()
    
    print("\n" + "=" * 64)
    print("  CRASH 300 VELOCITY EXHAUSTION PAPER TRADER")
    print("=" * 64)
    print(f"  Symbol:       Crash 300 Index")
    print(f"  Equity:       ${args.equity:,.2f}")
    print(f"  Risk/Trade:   {args.risk*100:.1f}%")
    print(f"  Velocity Exit: {'OFF' if args.no_velocity else 'ON'}")
    print(f"  TP/SL:        2x / 1x median range")
    print(f"  Strategy:     Buy The Dip (spike down → retrace up)")
    print("=" * 64)
    
    trader = Crash300Trader(
        equity=args.equity,
        risk_pct=args.risk,
        velocity_exit=not args.no_velocity,
    )
    
    def shutdown(sig, frame):
        print("\n\n  ⚡ Shutdown signal received...")
        trader._running = False
        trader.save_trades()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    asyncio.run(run_trader(trader))


if __name__ == "__main__":
    main()
