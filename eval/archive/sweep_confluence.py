"""
eval/sweep_confluence.py — Sweep min_confluence to find optimal threshold.

Tests different confluence thresholds and compares:
  - Return
  - Win rate
  - Profit factor
  - Max drawdown
  - Sharpe ratio
  - Trade count

Usage:
    python -m eval.sweep_confluence --symbol R_75 --max-bars 5000
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from eval.backtest_ict import load_candles, run_backtest


def sweep(
    symbol: str,
    max_bars: int = 5000,
    thresholds: list[float] | None = None,
    sample_interval: int = 10,
) -> list[dict]:
    """Run backtest across multiple confluence thresholds."""
    if thresholds is None:
        thresholds = [0, 20, 30, 40, 50, 60, 70, 80, 90]

    print(f"\n{'='*80}")
    print(f"  CONFLUENCE SWEEP — {symbol}")
    print(f"{'='*80}")
    print(f"  Thresholds: {thresholds}")
    print(f"  Max bars: {max_bars}")
    print(f"  Sample interval: {sample_interval}")
    print(f"{'='*80}\n")

    results = []

    for threshold in thresholds:
        print(f"\n--- Testing threshold={threshold} ---")
        summary = run_backtest(
            symbol=symbol,
            max_bars=max_bars,
            min_confluence=threshold,
            sample_interval=sample_interval,
            verbose=False,
        )
        summary["threshold"] = threshold
        results.append(summary)

        print(
            f"  Trades: {summary['trades']:3d} | "
            f"Return: {summary['return_pct']:+6.2f}% | "
            f"WR: {summary['win_rate']:5.1f}% | "
            f"PF: {summary['profit_factor']:5.2f} | "
            f"MaxDD: {summary['max_drawdown_pct']:5.2f}% | "
            f"Sharpe: {summary['sharpe']:7.2f}"
        )

    # Find optimal by different metrics
    print(f"\n{'='*80}")
    print(f"  OPTIMAL THRESHOLDS")
    print(f"{'='*80}")

    # Best return
    best_return = max(results, key=lambda r: r["return_pct"])
    print(f"  Best Return:     threshold={best_return['threshold']:5.1f} → {best_return['return_pct']:+.2f}%")

    # Best Sharpe
    best_sharpe = max(results, key=lambda r: r["sharpe"])
    print(f"  Best Sharpe:     threshold={best_sharpe['threshold']:5.1f} → {best_sharpe['sharpe']:.2f}")

    # Best profit factor
    best_pf = max(results, key=lambda r: r["profit_factor"])
    print(f"  Best ProfitFact: threshold={best_pf['threshold']:5.1f} → {best_pf['profit_factor']:.2f}")

    # Lowest drawdown with positive return
    positive = [r for r in results if r["return_pct"] > 0]
    if positive:
        best_dd = min(positive, key=lambda r: r["max_drawdown_pct"])
        print(f"  Lowest DD:       threshold={best_dd['threshold']:5.1f} → {best_dd['max_drawdown_pct']:.2f}% DD")

    # Best risk-adjusted (return / max DD)
    for r in results:
        r["return_dd_ratio"] = r["return_pct"] / max(r["max_drawdown_pct"], 0.01)
    best_ratio = max(results, key=lambda r: r["return_dd_ratio"])
    print(f"  Best Return/DD:  threshold={best_ratio['threshold']:5.1f} → {best_ratio['return_dd_ratio']:.2f}")

    # Print summary table
    print(f"\n{'='*80}")
    print(f"  FULL RESULTS TABLE")
    print(f"{'='*80}")
    print(f"  {'Thresh':>6} {'Trades':>7} {'Return':>8} {'WinRate':>8} {'PF':>6} {'MaxDD':>7} {'Sharpe':>8} {'Ret/DD':>7}")
    print(f"  {'-'*6} {'-'*7} {'-'*8} {'-'*8} {'-'*6} {'-'*7} {'-'*8} {'-'*7}")
    for r in results:
        print(
            f"  {r['threshold']:6.0f} {r['trades']:7d} {r['return_pct']:+7.2f}% "
            f"{r['win_rate']:7.1f}% {r['profit_factor']:6.2f} "
            f"{r['max_drawdown_pct']:6.2f}% {r['sharpe']:8.2f} "
            f"{r['return_dd_ratio']:7.2f}"
        )
    print(f"{'='*80}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Confluence Threshold Sweep")
    parser.add_argument("--symbol", default="R_75", choices=["R_75", "R_10", "BOOM500", "CRASH500"])
    parser.add_argument("--max-bars", type=int, default=5000)
    parser.add_argument("--sample", type=int, default=10, dest="sample_interval")
    args = parser.parse_args()

    results = sweep(
        symbol=args.symbol,
        max_bars=args.max_bars,
        sample_interval=args.sample_interval,
    )

    # Save results
    output_path = Path("checkpoints_tiny") / f"sweep_{args.symbol}_confluence.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to: {output_path}")


if __name__ == "__main__":
    main()
