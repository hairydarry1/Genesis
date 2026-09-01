"""
eval/paper_smc.py — Live Paper Trading for SMC Spike Hunter.

Connects to Deriv WebSocket, detects H1 zones from history,
and trades live M1 bars using the 3-factor confluence entry.

Usage:
    python eval/paper_smc.py                        # Both symbols
    python eval/paper_smc.py --symbol BOOM500       # Single symbol
    python eval/paper_smc.py --risk 0.02            # 2% risk per trade
    python eval/paper_smc.py --rsi-low 15 --rsi-high 85  # Relaxed RSI
"""

import asyncio
import argparse
import json
import sys
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks, OrderBlock
)

# Deriv WebSocket config
DERIV_APP_ID = 1089
DERIV_WS_URL = f"wss://api.derivws.com/trading/v1/options/ws/public?app_id={DERIV_APP_ID}"

# Symbol mapping
SYMBOL_MAP = {
    "BOOM500": "Boom 500 Index",
    "CRASH500": "Crash 500 Index",
    "BOOM1000": "Boom 1000 Index",
    "CRASH1000": "Crash 1000 Index",
}


class SMCLiveTrader:
    """Live paper trader for SMC Spike Hunter."""

    def __init__(self, symbol: str, risk: float = 0.01, rsi_low: float = 10,
                 rsi_high: float = 90, sl_pct: float = 0.005, initial_equity: float = 10.0):
        self.symbol = symbol
        self.mt5_symbol = SYMBOL_MAP.get(symbol, symbol)

        # Load H1 zones from historical data
        print(f"  [{symbol}] Loading H1 zones...")
        try:
            h1_data = load_candles(symbol, "60min")
            self.zones = detect_order_blocks(h1_data)
            print(f"  [{symbol}] Found {len(self.zones)} H1 order blocks")
            for z in self.zones:
                print(f"    {z.zone_type:8s} zone: {z.low:.2f} - {z.high:.2f} (strength={z.strength})")
        except FileNotFoundError:
            print(f"  [{symbol}] No H1 data found. Download first: python data/download_history.py --symbol {symbol} --timeframes 1,60")
            self.zones = []

        # Initialize strategy
        cfg = SMCHunterConfig(
            sl_pct=sl_pct,
            risk_per_trade=risk,
            rsi_extreme_low=rsi_low,
            rsi_extreme_high=rsi_high,
        )
        self.strategy = SMCSpikeHunter(symbol, config=cfg, initial_equity=initial_equity)
        self.strategy.set_h1_zones(self.zones)

        # Live state
        self._m1_bars = []
        self._current_bar = None
        self._last_tick_time = 0
        self._tick_count = 0
        self._bar_count = 0
        self._trade_count = 0
        self._running = True
        self._dashboard_url = "http://localhost:8080"
        self._last_dashboard_push = 0
        self._last_zone_refresh = time.time()
        self._zone_refresh_interval = 8 * 3600  # Refresh zones every 8 hours

    def _refresh_zones(self):
        """Refresh H1 zones from historical data (called periodically)."""
        now = time.time()
        if now - self._last_zone_refresh < self._zone_refresh_interval:
            return
        self._last_zone_refresh = now
        try:
            h1_data = load_candles(self.symbol, "60min")
            new_zones = detect_order_blocks(h1_data)
            old_count = len(self.zones)
            self.zones = new_zones
            self.strategy.set_h1_zones(new_zones)
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n  [{ts}] [{self.symbol}] Zones refreshed: {old_count} -> {len(new_zones)}")
            for z in new_zones:
                print(f"    {z.zone_type:8s} {z.low:.2f} - {z.high:.2f} (strength={z.strength})")
        except Exception as e:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n  [{ts}] [{self.symbol}] Zone refresh failed: {e}")

    def _push_dashboard(self):
        """Push current state to dashboard via HTTP POST (non-blocking)."""
        import urllib.request
        now = time.time()
        if now - self._last_dashboard_push < 2.0:  # Throttle to 2s
            return
        self._last_dashboard_push = now
        try:
            strat = self.strategy
            trades = strat.trade_log
            n = len(trades)
            wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
            total_pnl = sum(t.get("pnl", 0) for t in trades)

            # RSI
            rsi_val = float(strat._rsi_arr[-1]) if len(strat._rsi_arr) > 0 else None

            # EMA cross
            ema_cross = strat._ema_cross_direction

            # Check if price is in any H1 zone
            price = self._m1_bars[-1]["close"] if self._m1_bars else 0
            in_zone = any(z.low <= price <= z.high for z in self.zones)

            # Confluence count
            confluence = 0
            if in_zone: confluence += 1
            if rsi_val is not None and (rsi_val <= self.strategy.cfg.rsi_extreme_low or rsi_val >= self.strategy.cfg.rsi_extreme_high): confluence += 1
            if ema_cross != 0: confluence += 1

            payload = {
                "symbol": self.symbol,
                "price": price,
                "position": strat.position,
                "rsi": rsi_val,
                "ema_cross": ema_cross,
                "zone_count": len(self.zones),
                "in_zone": in_zone,
                "confluence": confluence,
                "equity": strat.equity,
                "trades": n,
                "win_rate": (wins / n * 100) if n > 0 else 0,
                "total_pnl": total_pnl,
                "tick_count": self._tick_count,
                "bar_count": self._bar_count,
            }
            req = urllib.request.Request(
                f"{self._dashboard_url}/api/smc",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass  # Dashboard may not be running

    def _push_trade(self, trade: dict):
        """Push a trade event to the dashboard."""
        import urllib.request
        try:
            req = urllib.request.Request(
                f"{self._dashboard_url}/api/smc/trade",
                data=json.dumps(trade).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass

    def process_tick(self, tick: dict):
        """Process a live tick from Deriv WebSocket."""
        price = float(tick.get("quote", 0))
        ts = tick.get("epoch", int(time.time()))

        if price <= 0:
            return

        self._tick_count += 1

        # Build M1 bar from ticks (aggregate into 1-minute candles)
        bar_minute = ts // 60

        if self._current_bar is None or bar_minute != self._current_bar.get("_minute"):
            # New bar — close previous bar and start new one
            if self._current_bar is not None and len(self._m1_bars) > 0:
                self._finalize_bar()

            self._current_bar = {
                "_minute": bar_minute,
                "epoch": bar_minute * 60,
                "open": price,
                "high": price,
                "low": price,
                "close": price,
            }
        else:
            # Update current bar
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price

    def _finalize_bar(self):
        """Close current bar and run strategy."""
        bar = {k: v for k, v in self._current_bar.items() if not k.startswith("_")}
        self._m1_bars.append(bar)
        self._bar_count += 1

        # Pre-compute indicators on all bars (fast numpy)
        self.strategy.precompute(self._m1_bars)

        # Run strategy on the latest bar
        idx = len(self._m1_bars) - 1
        result = self.strategy.on_bar(bar, idx)

        if result:
            self._handle_result(result)

        # Push state to dashboard every bar
        self._push_dashboard()

        # Refresh zones periodically
        self._refresh_zones()

        # Display status every 10 bars
        if self._bar_count % 10 == 0:
            self._display_status()

    def _handle_result(self, result: dict):
        """Handle trade open/close events."""
        action = result.get("action")
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        if action == "open":
            self._trade_count += 1
            side = result.get("side", "?").upper()
            entry = result.get("entry_price", 0)
            reasoning = result.get("reasoning", "")
            print(f"\n  [{ts}] {self.symbol} OPEN {side} @ {entry:.2f}")
            print(f"    {reasoning}")

        elif action == "close":
            pnl = result.get("pnl", 0)
            reason = result.get("exit_reason", "?")
            exit_p = result.get("exit_price", 0)
            emoji = "✅" if pnl > 0 else "❌"
            print(f"  [{ts}] {self.symbol} CLOSE {exit_p:.2f} | {emoji} PnL={pnl:+.2f} | Reason: {reason}")
            self._push_trade({
                "time": ts, "symbol": self.symbol,
                "side": result.get("side", "?"),
                "entry": result.get("entry_price", 0),
                "exit": exit_p, "pnl": pnl, "reason": reason,
            })

    def _display_status(self):
        """Display current status."""
        strat = self.strategy
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        price = self._m1_bars[-1]["close"] if self._m1_bars else 0

        pos_str = "FLAT"
        if strat.position == 1:
            pos_str = f"LONG @ {strat.entry_price:.2f} ({strat.bars_held} bars)"
        elif strat.position == -1:
            pos_str = f"SHORT @ {strat.entry_price:.2f} ({strat.bars_held} bars)"

        trades = strat.trade_log
        n = len(trades)
        wins = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total_pnl = sum(t.get("pnl", 0) for t in trades)

        # RSI
        rsi_str = "---"
        if len(strat._rsi_arr) > 0:
            rsi_str = f"{strat._rsi_arr[-1]:.1f}"

        # EMA cross
        cross_str = "none"
        if strat._ema_cross_direction == 1:
            cross_str = "bullish"
        elif strat._ema_cross_direction == -1:
            cross_str = "bearish"

        print(f"\n  {'='*60}")
        print(f"  {self.symbol} SMC SPIKE HUNTER @ {ts}")
        print(f"  {'='*60}")
        print(f"  Price: {price:.2f}  |  Position: {pos_str}")
        print(f"  Equity: ${strat.equity:,.2f}  |  Trades: {n}  |  WR: {wins/n*100:.0f}%  |  PnL: ${total_pnl:+.2f}")
        print(f"  RSI: {rsi_str}  |  EMA Cross: {cross_str}")
        print(f"  Zones: {len(self.zones)}  |  Ticks: {self._tick_count}  |  Bars: {self._bar_count}")
        print(f"  {'='*60}")


async def run_trader(symbol: str, risk: float, rsi_low: float, rsi_high: float,
                     sl_pct: float, dashboard_ws=None, equity: float = 10.0):
    """Run a single symbol trader connected to Deriv WebSocket."""
    import websockets

    trader = SMCLiveTrader(symbol, risk=risk, rsi_low=rsi_low, rsi_high=rsi_high,
                            sl_pct=sl_pct, initial_equity=equity)

    print(f"\n  [{symbol}] Connecting to Deriv WebSocket...")
    async with websockets.connect(DERIV_WS_URL) as ws:
        # Subscribe to ticks
        subscribe_msg = {
            "ticks": symbol,
            "subscribe": 1,
        }
        await ws.send(json.dumps(subscribe_msg))

        # Wait for first tick confirmation
        response = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(response)
        if resp.get("error"):
            print(f"  [{symbol}] Error: {resp['error']}")
            return

        print(f"  [{symbol}] Connected! Streaming ticks...")
        print(f"  [{symbol}] Config: SL={sl_pct*100:.1f}% Risk={risk*100:.0f}% RSI={rsi_low}/{rsi_high}")

        # Main loop
        async for message in ws:
            try:
                data = json.loads(message)
                if "tick" in data:
                    tick = data["tick"]
                    trader.process_tick(tick)

                    # Push to dashboard if connected
                    if dashboard_ws:
                        try:
                            await dashboard_ws.send(json.dumps({
                                "type": "smc_tick",
                                "symbol": symbol,
                                "price": float(tick.get("quote", 0)),
                                "epoch": tick.get("epoch", 0),
                                "position": trader.strategy.position,
                                "rsi": float(trader.strategy._rsi_arr[-1]) if len(trader.strategy._rsi_arr) > 0 else None,
                                "equity": trader.strategy.equity,
                                "trades": len(trader.strategy.trade_log),
                            }))
                        except Exception:
                            pass

            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"  [{symbol}] Error: {e}")
                continue


async def run_multi(symbols: list, risk: float, rsi_low: float, rsi_high: float,
                    sl_pct: float):
    """Run multiple symbols simultaneously."""
    tasks = []
    for sym in symbols:
        tasks.append(run_trader(sym, risk, rsi_low, rsi_high, sl_pct))

    print(f"\n  Starting {len(symbols)} traders: {', '.join(symbols)}")
    print(f"  Risk: {risk*100:.0f}%/trade  SL: {sl_pct*100:.1f}%  RSI: {rsi_low}/{rsi_high}")
    print(f"  Press Ctrl+C to stop\n")

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n  Shutting down...")


def main():
    parser = argparse.ArgumentParser(description="SMC Spike Hunter Paper Trading")
    parser.add_argument("--symbol", default="BOOM500", help="Symbol (default: BOOM500)")
    parser.add_argument("--risk", type=float, default=0.01, help="Risk per trade (default: 0.01)")
    parser.add_argument("--sl", type=float, default=0.005, help="Stop loss pct (default: 0.005)")
    parser.add_argument("--rsi-low", type=float, default=10, help="RSI extreme low for Boom (default: 10)")
    parser.add_argument("--rsi-high", type=float, default=90, help="RSI extreme high for Crash (default: 90)")
    parser.add_argument("--equity", type=float, default=10.0, help="Starting equity in USD (default: 10)")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  SMC SPIKE HUNTER — LIVE PAPER TRADING")
    print("=" * 60)

    asyncio.run(run_trader(args.symbol, args.risk, args.rsi_low, args.rsi_high, args.sl, equity=args.equity))


if __name__ == "__main__":
    main()
