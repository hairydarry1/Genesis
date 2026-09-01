"""
eval/backtest_ict.py — Backtest the ICT Decision Orchestrator.

Runs the full pipeline over historical candles and produces:
  - Trade log with entry/exit/reasoning
  - Performance metrics (Sharpe, drawdown, win rate)
  - Per-phase breakdown
  - Narrative context for each trade

Usage:
    python -m eval.backtest_ict --symbol R_75
    python -m eval.backtest_ict --symbol R_75 --max-bars 5000
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

from model.decision_orchestrator import DecisionOrchestrator


def load_candles(symbol: str) -> pd.DataFrame:
    """Load 1-min candles for a symbol."""
    paths = {
        "R_75": "data/candles_R75_1min.parquet",
        "R_10": "data/candles_volatility_10_1min.parquet",
        "BOOM500": "data/candles_boom_500_1min.parquet",
        "CRASH500": "data/candles_crash_500_1min.parquet",
    }
    path = paths.get(symbol)
    if not path or not Path(path).exists():
        raise FileNotFoundError(f"No candle data for {symbol}: {path}")

    df = pd.read_parquet(path, engine="pyarrow")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


def run_backtest(
    symbol: str,
    max_bars: int | None = None,
    min_confluence: float = 60.0,
    sample_interval: int = 1,
    verbose: bool = True,
) -> dict:
    """Run the backtest.
    
    sample_interval: Only run orchestrator every N bars (for speed on large datasets).
    """
    print(f"\n{'='*70}")
    print(f"  ICT BACKTEST — {symbol}")
    print(f"{'='*70}")

    # Load data
    df = load_candles(symbol)
    if max_bars:
        df = df.head(max_bars)

    n_bars = len(df)
    print(f"  Candles: {n_bars} ({df['timestamp'].iloc[0]} to {df['timestamp'].iloc[-1]})")
    print(f"  Min Confluence: {min_confluence}")
    print(f"  Sample Interval: every {sample_interval} bar(s)")
    print(f"{'='*70}\n")

    # Create orchestrator
    orch = DecisionOrchestrator(
        initial_equity=10000.0,
        min_confluence=min_confluence,
    )

    # Pre-compute multi-TF trends (SMA-based)
    closes = df["close"].values
    def _trend_at(idx: int, period: int) -> str:
        if idx < period:
            return "flat"
        sma = float(np.mean(closes[idx - period:idx + 1]))
        price = float(closes[idx])
        # Compare to mid-point of recent range
        hi = float(np.max(closes[max(0, idx - period):idx + 1]))
        lo = float(np.min(closes[max(0, idx - period):idx + 1]))
        rng = hi - lo
        if rng < 1e-10:
            return "flat"
        pos = (price - lo) / rng
        if pos > 0.65:
            return "up"
        elif pos < 0.35:
            return "down"
        return "flat"

    # Run backtest
    t0 = time.time()
    decisions_count = 0

    for i in range(0, n_bars, sample_interval):
        row = df.iloc[i]
        candle = {
            "epoch": int(row["timestamp"].timestamp()),
            "open": float(row["open"]),
            "high": float(row["high"]),
            "low": float(row["low"]),
            "close": float(row["close"]),
            "volume": float(row.get("volume", 0)),
        }

        # Compute multi-TF trends
        trend_15m = _trend_at(i, 15)
        trend_1h = _trend_at(i, 60)

        result = orch.on_bar(candle, trend_15min=trend_15m, trend_1h=trend_1h)

        if result and hasattr(result, "action") and result.action != "hold":
            decisions_count += 1
            if verbose and decisions_count <= 20:
                side = "LONG" if result.direction == 1 else "SHORT" if result.direction == -1 else "CLOSE"
                print(
                    f"  [{i:5d}] {row['timestamp'].strftime('%H:%M')} "
                    f"| {candle['close']:.2f} "
                    f"| {side:5s} "
                    f"| score={result.confluence_score:.0f} "
                    f"| src={result.signal_source} "
                    f"| {result.reasoning[:60]}"
                )

        if i % 2000 == 0 and i > 0:
            elapsed = time.time() - t0
            pct = i / n_bars * 100
            print(f"  ... {i}/{n_bars} bars ({pct:.0f}%) — {elapsed:.1f}s — equity=${orch.equity:.2f}")

    elapsed = time.time() - t0

    # Force close any open position
    if orch.position != 0:
        last_price = float(df.iloc[-1]["close"])
        pnl = (last_price - orch.entry_price) * orch.position
        orch.equity += pnl
        orch.trade_log.append({
            "action": "close",
            "side": "long" if orch.position == 1 else "short",
            "entry_price": orch.entry_price,
            "exit_price": last_price,
            "pnl": pnl,
            "exit_reason": "backtest_end",
            "bars_held": orch.bars_held,
        })
        orch.position = 0

    # Compute metrics
    closes = [t for t in orch.trade_log if t["action"] == "close"]
    pnls = np.array([t["pnl"] for t in closes]) if closes else np.array([0.0])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]

    # Equity curve
    equity_curve = [10000.0]
    for t in closes:
        equity_curve.append(equity_curve[-1] + t["pnl"])
    equity_arr = np.array(equity_curve)
    running_max = np.maximum.accumulate(equity_arr)
    drawdowns = (running_max - equity_arr) / np.maximum(running_max, 1)
    max_dd = float(np.max(drawdowns))

    # Sharpe
    if len(pnls) > 1 and np.std(pnls) > 0:
        sharpe = float(np.mean(pnls) / np.std(pnls) * np.sqrt(252 * 24 * 60))
    else:
        sharpe = 0.0

    # Win rate by phase
    phase_stats = {}
    for t in closes:
        phase = t.get("narrative_phase", "unknown")
        if phase not in phase_stats:
            phase_stats[phase] = {"trades": 0, "wins": 0, "pnl": 0}
        phase_stats[phase]["trades"] += 1
        phase_stats[phase]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            phase_stats[phase]["wins"] += 1

    # Win rate by source
    source_stats = {}
    for t in closes:
        src = t.get("source", "unknown")
        if src not in source_stats:
            source_stats[src] = {"trades": 0, "wins": 0, "pnl": 0}
        source_stats[src]["trades"] += 1
        source_stats[src]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            source_stats[src]["wins"] += 1

    # Win rate by side
    longs = [t for t in closes if t.get("side") == "long"]
    shorts = [t for t in closes if t.get("side") == "short"]
    long_wins = sum(1 for t in longs if t["pnl"] > 0)
    short_wins = sum(1 for t in shorts if t["pnl"] > 0)

    summary = {
        "symbol": symbol,
        "bars": n_bars,
        "elapsed_s": round(elapsed, 2),
        "bars_per_sec": round(n_bars / elapsed, 0),
        "trades": len(closes),
        "decisions": decisions_count,
        "starting_equity": 10000.0,
        "final_equity": round(orch.equity, 2),
        "total_pnl": round(orch.equity - 10000.0, 2),
        "return_pct": round((orch.equity / 10000.0 - 1) * 100, 2),
        "win_rate": round(len(wins) / len(closes) * 100, 1) if closes else 0,
        "avg_win": round(float(np.mean(wins)), 2) if len(wins) > 0 else 0,
        "avg_loss": round(float(np.mean(losses)), 2) if len(losses) > 0 else 0,
        "profit_factor": round(float(np.sum(wins) / abs(np.sum(losses))), 2) if len(losses) > 0 and np.sum(losses) != 0 else 0,
        "max_drawdown_pct": round(max_dd * 100, 2),
        "sharpe": round(sharpe, 2),
        "long_trades": len(longs),
        "long_win_rate": round(long_wins / len(longs) * 100, 1) if longs else 0,
        "short_trades": len(shorts),
        "short_win_rate": round(short_wins / len(shorts) * 100, 1) if shorts else 0,
        "phase_breakdown": {
            phase: {
                "trades": stats["trades"],
                "win_rate": round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] > 0 else 0,
                "pnl": round(stats["pnl"], 2),
            }
            for phase, stats in phase_stats.items()
        },
        "source_breakdown": {
            src: {
                "trades": stats["trades"],
                "win_rate": round(stats["wins"] / stats["trades"] * 100, 1) if stats["trades"] > 0 else 0,
                "pnl": round(stats["pnl"], 2),
            }
            for src, stats in source_stats.items()
        },
    }

    # Print results
    print(f"\n{'='*70}")
    print(f"  BACKTEST RESULTS — {symbol}")
    print(f"{'='*70}")
    print(f"  Bars:            {n_bars} ({elapsed:.1f}s, {n_bars/elapsed:.0f} bars/s)")
    print(f"  Trades:          {len(closes)} (from {decisions_count} decisions)")
    print(f"  Starting Equity: $10,000.00")
    print(f"  Final Equity:    ${orch.equity:,.2f}")
    print(f"  Total PnL:       ${orch.equity - 10000:+,.2f}")
    print(f"  Return:          {(orch.equity/10000 - 1)*100:+.2f}%")
    print(f"  Win Rate:        {summary['win_rate']:.1f}%")
    print(f"  Avg Win:         ${summary['avg_win']:+.2f}")
    print(f"  Avg Loss:        ${summary['avg_loss']:+.2f}")
    print(f"  Profit Factor:   {summary['profit_factor']:.2f}")
    print(f"  Max Drawdown:    {summary['max_drawdown_pct']:.2f}%")
    print(f"  Sharpe:          {summary['sharpe']:.2f}")
    print(f"  Long Trades:     {summary['long_trades']} (WR: {summary['long_win_rate']:.1f}%)")
    print(f"  Short Trades:    {summary['short_trades']} (WR: {summary['short_win_rate']:.1f}%)")

    if phase_stats:
        print(f"\n  --- Phase Breakdown ---")
        for phase, stats in summary["phase_breakdown"].items():
            print(f"    {phase:15s}: {stats['trades']:3d} trades, WR={stats['win_rate']:5.1f}%, PnL=${stats['pnl']:+.2f}")

    if source_stats:
        print(f"\n  --- Source Breakdown ---")
        for src, stats in summary["source_breakdown"].items():
            print(f"    {src:15s}: {stats['trades']:3d} trades, WR={stats['win_rate']:5.1f}%, PnL=${stats['pnl']:+.2f}")

    print(f"{'='*70}")

    # Save results
    output_dir = Path("checkpoints_tiny")
    output_dir.mkdir(exist_ok=True)

    # Save trade log
    trade_log_path = output_dir / f"backtest_{symbol}_trades.json"
    with open(trade_log_path, "w") as f:
        json.dump(orch.get_trade_log(), f, indent=2, default=str)
    print(f"\n  Trade log: {trade_log_path}")

    # Save summary
    summary_path = output_dir / f"backtest_{symbol}_summary.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Summary:   {summary_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="ICT Backtest")
    parser.add_argument("--symbol", default="R_75", choices=["R_75", "R_10", "BOOM500", "CRASH500"])
    parser.add_argument("--max-bars", type=int, default=None, help="Max bars to backtest", dest="max_bars")
    parser.add_argument("--min-confluence", type=float, default=60.0)
    parser.add_argument("--sample", type=int, default=1, help="Run every N bars (for speed)", dest="sample_interval")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    summary = run_backtest(
        symbol=args.symbol,
        max_bars=args.max_bars,
        min_confluence=args.min_confluence,
        sample_interval=args.sample_interval,
        verbose=not args.quiet,
    )


if __name__ == "__main__":
    main()
