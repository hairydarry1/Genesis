"""
eval/backtest_boom_crash.py — Backtest Spike Fade Strategy for BOOM/CRASH.

Strategy:
  1. Detect spikes (> median_vol * 3)
  2. Identify clusters (spikes with gap <= 5 bars)
  3. Only fade the LAST spike of cluster or isolated spikes
  4. Skip large spikes (> 0.16%)
  5. Enter after cluster ends (6 quiet bars confirmed)
  6. Target: 50-80% retracement of spike
  7. Stop: beyond spike high/low
  8. Max hold: 10 bars

Usage:
    python -m eval.backtest_boom_crash --symbol BOOM500
    python -m eval.backtest_boom_crash --all
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

SYMBOL_CONFIG = {
    "BOOM500": {
        "path": "data/candles_boom_500_1min.parquet",
        "spike_dir": "up",   # spikes go UP → we SELL (fade)
        "label": "BOOM500",
    },
    "CRASH500": {
        "path": "data/candles_crash_500_1min.parquet",
        "spike_dir": "down", # spikes go DOWN → we BUY (fade)
        "label": "CRASH500",
    },
    "BOOM1000": {
        "path": "data/candles_boom1000_1min.parquet",
        "spike_dir": "up",
        "label": "BOOM1000",
    },
    "CRASH1000": {
        "path": "data/candles_crash1000_1min.parquet",
        "spike_dir": "down",
        "label": "CRASH1000",
    },
}


# ---------------------------------------------------------------------------
# Spike Detector
# ---------------------------------------------------------------------------

class SpikeDetector:
    """Detect spikes and classify into clusters."""

    def __init__(
        self,
        spike_mult: float = 3.0,
        cluster_gap: int = 5,
        large_spike_pct: float = 0.0016,  # 0.16%
    ):
        self.spike_mult = spike_mult
        self.cluster_gap = cluster_gap
        self.large_spike_pct = large_spike_pct

    def detect(self, df: pd.DataFrame, spike_dir: str) -> pd.DataFrame:
        """Add spike columns to dataframe."""
        df = df.copy()
        df["ret"] = df["close"].pct_change()
        df["abs_ret"] = df["ret"].abs()

        median_vol = df["abs_ret"].median()
        threshold = median_vol * self.spike_mult

        if spike_dir == "up":
            df["is_spike"] = df["ret"] > threshold
        else:
            df["is_spike"] = df["ret"] < -threshold

        df["spike_size"] = df["ret"].abs()

        # Classify each spike's position in cluster
        spike_indices = df[df["is_spike"]].index.tolist()
        gaps = np.diff(spike_indices) if len(spike_indices) > 1 else []

        # Build cluster info
        spike_positions = {}  # index -> "isolated"|"first"|"middle"|"last"
        cluster_ids = {}      # index -> cluster_id
        cluster_id = 0

        for i, idx in enumerate(spike_indices):
            prev_close = gaps[i - 1] if i > 0 else 999
            next_close = gaps[i] if i < len(gaps) else 999

            in_prev = i > 0 and prev_close <= self.cluster_gap
            in_next = i < len(gaps) and next_close <= self.cluster_gap

            if not in_prev and not in_next:
                spike_positions[idx] = "isolated"
                cluster_ids[idx] = None
            elif not in_prev and in_next:
                spike_positions[idx] = "first"
                cluster_id += 1
                cluster_ids[idx] = cluster_id
            elif in_prev and in_next:
                spike_positions[idx] = "middle"
                cluster_ids[idx] = cluster_id
            else:
                spike_positions[idx] = "last"
                cluster_ids[idx] = cluster_id

        df["spike_position"] = df.index.map(lambda x: spike_positions.get(x, None))
        df["cluster_id"] = df.index.map(lambda x: cluster_ids.get(x, None))

        # Filter: only fadeable spikes (isolated or last in cluster)
        df["fadeable"] = df["is_spike"] & (
            df["spike_position"].isin(["isolated", "last"])
        )

        # Filter: skip large spikes
        df["fadeable"] = df["fadeable"] & (df["spike_size"] < self.large_spike_pct)

        return df


# ---------------------------------------------------------------------------
# Backtester
# ---------------------------------------------------------------------------

class SpikeFadeBacktester:
    """Backtest the spike fade strategy."""

    def __init__(
        self,
        spike_dir: str,
        entry_delay: int = 1,      # Bars to wait after cluster ends
        tp_pct: float = 0.006,     # Take profit (60% of avg spike)
        sl_pct: float = 0.002,     # Stop loss (20%)
        max_hold: int = 10,        # Max bars to hold
        initial_equity: float = 10000.0,
        risk_per_trade: float = 0.02,
    ):
        self.spike_dir = spike_dir
        self.entry_delay = entry_delay
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.max_hold = max_hold
        self.initial_equity = initial_equity
        self.risk_per_trade = risk_per_trade

    def run(self, df: pd.DataFrame, verbose: bool = False) -> dict:
        """Run backtest and return results."""
        equity = self.initial_equity
        peak_equity = equity
        position = 0  # +1 long, -1 short, 0 flat
        entry_price = 0.0
        bars_held = 0
        trade_log = []
        pending_entry = None  # (direction, bars_to_wait)

        for i in range(1, len(df)):
            row = df.iloc[i]
            price = row["close"]
            high = row["high"]
            low = row["low"]

            # If in position, check exits
            if position != 0:
                bars_held += 1

                if position == 1:  # Long (fading down-spike on CRASH)
                    pnl_pct = (price - entry_price) / entry_price
                    # Check TP
                    if high >= entry_price * (1 + self.tp_pct):
                        exit_price = entry_price * (1 + self.tp_pct)
                        pnl = (exit_price - entry_price) * position
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": exit_price,
                            "pnl": pnl, "pnl_pct": self.tp_pct,
                            "bars": bars_held, "reason": "tp",
                        })
                        position = 0
                        bars_held = 0
                        continue
                    # Check SL
                    if low <= entry_price * (1 - self.sl_pct):
                        exit_price = entry_price * (1 - self.sl_pct)
                        pnl = (exit_price - entry_price) * position
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": exit_price,
                            "pnl": pnl, "pnl_pct": -self.sl_pct,
                            "bars": bars_held, "reason": "sl",
                        })
                        position = 0
                        bars_held = 0
                        continue
                    # Check max hold
                    if bars_held >= self.max_hold:
                        pnl = (price - entry_price) * position
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": price,
                            "pnl": pnl, "pnl_pct": pnl / entry_price,
                            "bars": bars_held, "reason": "timeout",
                        })
                        position = 0
                        bars_held = 0
                        continue

                elif position == -1:  # Short (fading up-spike on BOOM)
                    pnl_pct = (entry_price - price) / entry_price
                    if low <= entry_price * (1 - self.tp_pct):
                        exit_price = entry_price * (1 - self.tp_pct)
                        pnl = (entry_price - exit_price)
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": exit_price,
                            "pnl": pnl, "pnl_pct": self.tp_pct,
                            "bars": bars_held, "reason": "tp",
                        })
                        position = 0
                        bars_held = 0
                        continue
                    if high >= entry_price * (1 + self.sl_pct):
                        exit_price = entry_price * (1 + self.sl_pct)
                        pnl = (entry_price - exit_price)
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": exit_price,
                            "pnl": pnl, "pnl_pct": -self.sl_pct,
                            "bars": bars_held, "reason": "sl",
                        })
                        position = 0
                        bars_held = 0
                        continue
                    if bars_held >= self.max_hold:
                        pnl = (entry_price - price)
                        equity += pnl
                        peak_equity = max(peak_equity, equity)
                        trade_log.append({
                            "entry": entry_price, "exit": price,
                            "pnl": pnl, "pnl_pct": pnl / entry_price,
                            "bars": bars_held, "reason": "timeout",
                        })
                        position = 0
                        bars_held = 0
                        continue

            # If pending entry, count down
            if pending_entry is not None:
                pending_entry -= 1
                if pending_entry <= 0:
                    # Execute entry
                    direction = -1 if self.spike_dir == "up" else +1
                    position = direction
                    entry_price = price
                    bars_held = 0
                    pending_entry = None
                    continue

            # Check for fadeable spike
            if position == 0 and row.get("fadeable", False):
                # Start countdown
                pending_entry = self.entry_delay

        # Close any open position
        if position != 0:
            price = df.iloc[-1]["close"]
            if position == 1:
                pnl = (price - entry_price)
            else:
                pnl = (entry_price - price)
            equity += pnl
            trade_log.append({
                "entry": entry_price, "exit": price,
                "pnl": pnl, "pnl_pct": pnl / entry_price,
                "bars": bars_held, "reason": "eod",
            })

        # Compute metrics
        pnls = np.array([t["pnl"] for t in trade_log]) if trade_log else np.array([0.0])
        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]

        # Equity curve
        equity_curve = [self.initial_equity]
        for t in trade_log:
            equity_curve.append(equity_curve[-1] + t["pnl"])
        equity_arr = np.array(equity_curve)
        running_max = np.maximum.accumulate(equity_arr)
        drawdowns = (running_max - equity_arr) / np.maximum(running_max, 1)
        max_dd = float(np.max(drawdowns))

        # Exit reason breakdown
        reasons = {}
        for t in trade_log:
            r = t["reason"]
            if r not in reasons:
                reasons[r] = {"count": 0, "pnl": 0, "wins": 0}
            reasons[r]["count"] += 1
            reasons[r]["pnl"] += t["pnl"]
            if t["pnl"] > 0:
                reasons[r]["wins"] += 1

        n_trades = len(trade_log)
        return {
            "trades": n_trades,
            "starting_equity": self.initial_equity,
            "final_equity": round(equity, 2),
            "total_pnl": round(equity - self.initial_equity, 2),
            "return_pct": round((equity / self.initial_equity - 1) * 100, 2),
            "win_rate": round(len(wins) / n_trades * 100, 1) if n_trades else 0,
            "avg_win": round(float(np.mean(wins)), 2) if len(wins) else 0,
            "avg_loss": round(float(np.mean(losses)), 2) if len(losses) else 0,
            "profit_factor": round(float(np.sum(wins) / abs(np.sum(losses))), 2) if len(losses) and np.sum(losses) != 0 else 0,
            "max_drawdown_pct": round(max_dd * 100, 2),
            "avg_bars_held": round(np.mean([t["bars"] for t in trade_log]), 1) if trade_log else 0,
            "reasons": {k: {"count": v["count"], "pnl": round(v["pnl"], 2), "wr": round(v["wins"]/v["count"]*100, 1)} for k, v in reasons.items()},
        }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_backtest(symbol: str, max_bars: int | None = None) -> dict:
    """Run full backtest for a symbol."""
    cfg = SYMBOL_CONFIG[symbol]
    df = pd.read_parquet(cfg["path"])
    if max_bars:
        df = df.head(max_bars)

    print(f"\n{'='*70}")
    print(f"  SPIKE FADE BACKTEST — {cfg['label']}")
    print(f"{'='*70}")
    print(f"  Bars: {len(df)}")
    print(f"  Direction: {'SELL after up-spike' if cfg['spike_dir'] == 'up' else 'BUY after down-spike'}")

    # Detect spikes
    t0 = time.time()
    detector = SpikeDetector()
    df = detector.detect(df, cfg["spike_dir"])

    n_spikes = df["is_spike"].sum()
    n_fadeable = df["fadeable"].sum()
    n_isolated = (df["spike_position"] == "isolated").sum()
    n_last = (df["spike_position"] == "last").sum()
    n_first = (df["spike_position"] == "first").sum()
    n_middle = (df["spike_position"] == "middle").sum()

    print(f"  Spikes detected: {n_spikes}")
    print(f"  Isolated: {n_isolated} | First: {n_first} | Middle: {n_middle} | Last: {n_last}")
    print(f"  Fadeable (isolated+last, skip large): {n_fadeable}")

    # Run backtest with different parameters
    configs = [
        {"tp_pct": 0.004, "sl_pct": 0.002, "max_hold": 8,  "label": "TP=0.4% SL=0.2% Hold=8"},
        {"tp_pct": 0.006, "sl_pct": 0.002, "max_hold": 10, "label": "TP=0.6% SL=0.2% Hold=10"},
        {"tp_pct": 0.008, "sl_pct": 0.002, "max_hold": 12, "label": "TP=0.8% SL=0.2% Hold=12"},
        {"tp_pct": 0.006, "sl_pct": 0.003, "max_hold": 10, "label": "TP=0.6% SL=0.3% Hold=10"},
        {"tp_pct": 0.005, "sl_pct": 0.0015, "max_hold": 8, "label": "TP=0.5% SL=0.15% Hold=8"},
    ]

    results = []
    for c in configs:
        bt = SpikeFadeBacktester(
            spike_dir=cfg["spike_dir"],
            tp_pct=c["tp_pct"],
            sl_pct=c["sl_pct"],
            max_hold=c["max_hold"],
        )
        r = bt.run(df)
        r["label"] = c["label"]
        results.append(r)

    elapsed = time.time() - t0

    # Print results
    print(f"\n  {'Config':30s} {'Trades':>6s} {'Return':>8s} {'WR':>6s} {'PF':>5s} {'MaxDD':>6s} {'AvgBars':>7s}")
    print(f"  {'-'*30} {'-'*6} {'-'*8} {'-'*6} {'-'*5} {'-'*6} {'-'*7}")
    for r in results:
        print(f"  {r['label']:30s} {r['trades']:6d} {r['return_pct']:+7.2f}% {r['win_rate']:5.1f}% {r['profit_factor']:5.2f} {r['max_drawdown_pct']:5.2f}% {r['avg_bars_held']:7.1f}")

    best = max(results, key=lambda x: x["return_pct"])
    print(f"\n  Best: {best['label']}")
    print(f"    Return: {best['return_pct']:+.2f}% | WR: {best['win_rate']:.1f}% | PF: {best['profit_factor']:.2f} | MaxDD: {best['max_drawdown_pct']:.2f}%")

    # Exit breakdown for best
    if best["reasons"]:
        print(f"\n  Exit Breakdown (best config):")
        for reason, stats in best["reasons"].items():
            print(f"    {reason:10s}: {stats['count']:3d} trades, PnL=${stats['pnl']:+.2f}, WR={stats['wr']:.1f}%")

    print(f"\n  Time: {elapsed:.1f}s")

    # Save results
    output_dir = Path("checkpoints_tiny")
    output_dir.mkdir(exist_ok=True)
    with open(output_dir / f"backtest_{symbol}_spike_fade.json", "w") as f:
        json.dump({"symbol": symbol, "results": results, "spike_stats": {
            "total": int(n_spikes), "isolated": int(n_isolated),
            "first": int(n_first), "middle": int(n_middle), "last": int(n_last),
            "fadeable": int(n_fadeable),
        }}, f, indent=2)

    return best


def main():
    parser = argparse.ArgumentParser(description="BOOM/CRASH Spike Fade Backtest")
    parser.add_argument("--symbol", default="BOOM500", choices=list(SYMBOL_CONFIG.keys()))
    parser.add_argument("--all", action="store_true", help="Backtest all symbols")
    parser.add_argument("--max-bars", type=int, default=None)
    args = parser.parse_args()

    if args.all:
        for symbol in SYMBOL_CONFIG:
            try:
                run_backtest(symbol, args.max_bars)
            except FileNotFoundError:
                print(f"\n  Skipping {symbol}: no data file")
    else:
        run_backtest(args.symbol, args.max_bars)


if __name__ == "__main__":
    main()
