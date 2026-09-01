"""
eval/backtest_mt5_ticks.py — Backtest spike fade on MT5 tick data.

Compares tick-level vs bar-level spike detection and fading.

Usage:
    python -m eval.backtest_mt5_ticks --symbol Boom500Index
    python -m eval.backtest_mt5_ticks --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from eval.paper_trading_v2 import SpikeFadeEngine, SPIKE_FADE_CONFIGS


# ---------------------------------------------------------------------------
# MT5 Connection
# ---------------------------------------------------------------------------

def connect_mt5():
    """Connect to MT5."""
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("  MetaTrader5 not installed. Run: python -m pip install MetaTrader5")
        return None

    if not mt5.initialize(timeout=10000):
        print(f"  MT5 init failed: {mt5.last_error()}")
        return None

    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server}")
    return mt5


def get_tick_history(mt5, symbol: str, days: int = 7) -> list[dict]:
    """Pull tick history from MT5."""
    print(f"  Pulling {days} days of {symbol}...")
    end = datetime.now()
    start = end - timedelta(days=days)

    ticks = mt5.copy_ticks_range(symbol, start, end, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        print(f"  No ticks found: {mt5.last_error()}")
        return []

    print(f"  Got {len(ticks)} ticks")
    return [{
        "price": float(t["bid"]),
        "epoch": int(t["time"]),
        "time_msc": int(t["time_msc"]),
    } for t in ticks]


# ---------------------------------------------------------------------------
# Tick-Level Backtest
# ---------------------------------------------------------------------------

class TickFadeBacktester:
    """Backtest spike fade on tick-level data."""

    MT5_TO_OURS = {
        "Boom 500 Index": "BOOM500",
        "Crash 500 Index": "CRASH500",
        "Boom 1000 Index": "BOOM1000",
        "Crash 1000 Index": "CRASH1000",
    }

    def __init__(self, mt5_symbol: str, config: dict | None = None):
        self.mt5_symbol = mt5_symbol
        self.our_symbol = self.MT5_TO_OURS.get(mt5_symbol, "BOOM500")
        self.config = config or SPIKE_FADE_CONFIGS.get(self.our_symbol, SPIKE_FADE_CONFIGS["BOOM500"])
        self.engine = SpikeFadeEngine(self.our_symbol, config=self.config)

    def run(self, ticks: list[dict]) -> dict:
        """Run backtest on tick data."""
        t0 = time.time()

        for tick in ticks:
            self.engine.on_tick(tick)

        elapsed = time.time() - t0
        summary = self.engine.get_summary()

        # Analyze spike gaps
        gaps = []
        if len(self.engine._spike_indices) > 1:
            gaps = np.diff(self.engine._spike_indices).tolist()

        n_trades = summary["trades"]
        closes = [t for t in self.engine.trade_log if t.get("action") == "close"]

        # Equity curve
        equity_curve = [10000.0]
        for t in closes:
            equity_curve.append(equity_curve[-1] + t["pnl"])
        equity_arr = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_arr)
        drawdowns = (running_max - equity_arr) / np.maximum(running_max, 1)
        max_dd = float(np.max(drawdowns))

        # Exit breakdown
        from collections import Counter
        reasons = Counter(t.get("exit_reason", "?") for t in closes)

        return {
            "symbol": self.mt5_symbol,
            "mode": "tick",
            "ticks": len(ticks),
            "hours": (ticks[-1]["epoch"] - ticks[0]["epoch"]) / 3600 if len(ticks) > 1 else 0,
            "elapsed_s": round(elapsed, 2),
            "trades": n_trades,
            "return_pct": round((summary["equity"] / 10000 - 1) * 100, 2),
            "win_rate": round(summary["win_rate"] * 100, 1),
            "pnl": round(summary["pnl"], 2),
            "avg_win": round(float(summary["avg_win"]), 2) if summary["avg_win"] else 0,
            "avg_loss": round(float(summary["avg_loss"]), 2) if summary["avg_loss"] else 0,
            "max_dd_pct": round(max_dd * 100, 2),
            "spikes_detected": len(self.engine._spike_indices),
            "avg_tick_gap": round(np.mean(gaps), 1) if gaps else 0,
            "median_tick_gap": round(np.median(gaps), 1) if gaps else 0,
            "exit_reasons": dict(reasons),
        }


# ---------------------------------------------------------------------------
# Bar-Level Backtest (from existing data)
# ---------------------------------------------------------------------------

def run_bar_backtest(symbol: str, days: int = 7) -> dict:
    """Run bar-level backtest for comparison."""
    import pandas as pd

    path_map = {
        "Boom 500 Index": "data/candles_boom_500_1min.parquet",
        "Crash 500 Index": "data/candles_crash_500_1min.parquet",
        "Boom 1000 Index": "data/candles_boom1000_1min.parquet",
        "Crash 1000 Index": "data/candles_crash1000_1min.parquet",
    }

    # Map MT5 symbol to our symbol name
    sym_map = {
        "Boom 500 Index": "BOOM500",
        "Crash 500 Index": "CRASH500",
        "Boom 1000 Index": "BOOM1000",
        "Crash 1000 Index": "CRASH1000",
    }

    our_sym = sym_map.get(symbol, "BOOM500")
    data_path = path_map.get(symbol)

    if not data_path or not Path(data_path).exists():
        print(f"  No bar data for {symbol}")
        return {}

    df = pd.read_parquet(data_path).tail(days * 24 * 60)  # ~days of 1-min bars

    eng = SpikeFadeEngine(our_sym)
    t0 = time.time()

    for _, row in df.iterrows():
        eng.on_bar({
            "epoch": int(row["epoch"]),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": 1.0,
        })

    elapsed = time.time() - t0
    summary = eng.get_summary()
    closes = [t for t in eng.trade_log if t.get("action") == "close"]

    equity_curve = [10000.0]
    for t in closes:
        equity_curve.append(equity_curve[-1] + t["pnl"])
    equity_arr = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity_arr)
    drawdowns = (running_max - equity_arr) / np.maximum(running_max, 1)
    max_dd = float(np.max(drawdowns))

    from collections import Counter
    reasons = Counter(t.get("exit_reason", "?") for t in closes)

    return {
        "symbol": symbol,
        "mode": "bar",
        "bars": len(df),
        "hours": len(df) / 60,
        "elapsed_s": round(elapsed, 2),
        "trades": summary["trades"],
        "return_pct": round((summary["equity"] / 10000 - 1) * 100, 2),
        "win_rate": round(summary["win_rate"] * 100, 1),
        "pnl": round(summary["pnl"], 2),
        "avg_win": round(float(summary["avg_win"]), 2) if summary["avg_win"] else 0,
        "avg_loss": round(float(summary["avg_loss"]), 2) if summary["avg_loss"] else 0,
        "max_dd_pct": round(max_dd * 100, 2),
        "spikes_detected": len(eng._spike_indices),
        "exit_reasons": dict(reasons),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_comparison(mt5, symbol: str, days: int = 7):
    """Run both tick and bar backtests, compare results."""
    print(f"\n{'='*70}")
    print(f"  COMPARISON: Tick vs Bar — {symbol}")
    print(f"{'='*70}")

    # Tick-level
    ticks = get_tick_history(mt5, symbol, days)
    if ticks:
        tick_bt = TickFadeBacktester(symbol)
        tick_result = tick_bt.run(ticks)
    else:
        tick_result = {}

    # Bar-level
    bar_result = run_bar_backtest(symbol, days)

    # Print comparison
    print(f"\n  {'Metric':25s} {'Tick-Level':>15s} {'Bar-Level':>15s}")
    print(f"  {'-'*25} {'-'*15} {'-'*15}")

    metrics = [
        ("Data points", "ticks", "bars"),
        ("Time span (hours)", "hours", "hours"),
        ("Trades", "trades", "trades"),
        ("Return", "return_pct", "return_pct"),
        ("Win Rate", "win_rate", "win_rate"),
        ("PnL", "pnl", "pnl"),
        ("Avg Win", "avg_win", "avg_win"),
        ("Avg Loss", "avg_loss", "avg_loss"),
        ("Max Drawdown", "max_dd_pct", "max_dd_pct"),
        ("Spikes Detected", "spikes_detected", "spikes_detected"),
        ("Avg Tick Gap", "avg_tick_gap", None),
        ("Median Tick Gap", "median_tick_gap", None),
    ]

    for label, tick_key, bar_key in metrics:
        tick_val = tick_result.get(tick_key, "-")
        bar_val = bar_result.get(bar_key, "-") if bar_key else "-"

        if isinstance(tick_val, float):
            tick_val = f"{tick_val:.1f}"
        if isinstance(bar_val, float):
            bar_val = f"{bar_val:.1f}"

        print(f"  {label:25s} {str(tick_val):>15s} {str(bar_val):>15s}")

    # Exit breakdown
    print(f"\n  Exit Reasons (Tick):")
    for reason, count in sorted(tick_result.get("exit_reasons", {}).items(), key=lambda x: -x[1]):
        print(f"    {reason:15s}: {count}")

    print(f"\n  Exit Reasons (Bar):")
    for reason, count in sorted(bar_result.get("exit_reasons", {}).items(), key=lambda x: -x[1]):
        print(f"    {reason:15s}: {count}")

    # Verdict
    tick_ret = tick_result.get("return_pct", 0)
    bar_ret = bar_result.get("return_pct", 0)
    tick_wr = tick_result.get("win_rate", 0)
    bar_wr = bar_result.get("win_rate", 0)

    print(f"\n  VERDICT:")
    if tick_ret > bar_ret:
        print(f"    Tick-level WINS: {tick_ret:+.2f}% vs {bar_ret:+.2f}% (+{tick_ret-bar_ret:.2f}%)")
    elif bar_ret > tick_ret:
        print(f"    Bar-level WINS: {bar_ret:+.2f}% vs {tick_ret:+.2f}% (+{bar_ret-tick_ret:.2f}%)")
    else:
        print(f"    TIED: both {tick_ret:+.2f}%")

    if tick_wr > bar_wr:
        print(f"    Tick WR higher: {tick_wr:.1f}% vs {bar_wr:.1f}%")
    elif bar_wr > tick_wr:
        print(f"    Bar WR higher: {bar_wr:.1f}% vs {tick_wr:.1f}%")

    print(f"{'='*70}")

    # Save
    output_dir = Path("checkpoints_tiny")
    output_dir.mkdir(exist_ok=True)
    with open(output_dir / f"backtest_{symbol.replace(' ', '_')}_tick_vs_bar.json", "w") as f:
        json.dump({"tick": tick_result, "bar": bar_result}, f, indent=2, default=str)

    return tick_result, bar_result


def main():
    parser = argparse.ArgumentParser(description="MT5 Tick vs Bar Backtest")
    parser.add_argument("--symbol", default="Boom 500 Index",
                        help="MT5 symbol name (e.g., 'Boom 500 Index')")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--days", type=int, default=3, help="Days of history")
    args = parser.parse_args()

    mt5 = connect_mt5()
    if not mt5:
        return

    try:
        if args.all:
            symbols = ["Boom 500 Index", "Crash 500 Index", "Boom 1000 Index", "Crash 1000 Index"]
        else:
            symbols = [args.symbol]

        for sym in symbols:
            try:
                run_comparison(mt5, sym, args.days)
            except Exception as e:
                print(f"  Error on {sym}: {e}")
                import traceback
                traceback.print_exc()
    finally:
        mt5.shutdown()
        print("\nMT5 disconnected.")


if __name__ == "__main__":
    main()
