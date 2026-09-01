"""
eval/journal.py — Trade journal analyzer.

Finds patterns in trading performance:
  - Best/worst times of day
  - Best/worst sessions
  - Best/worst regimes
  - Win rate by side (long vs short)
  - Average holding period by outcome
  - Consecutive pattern analysis

Usage:
    python -m eval.journal
    python -m eval.journal --trades checkpoints_vol10/paper_trades.json
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path


def analyze(trade_path: str) -> dict:
    """Analyze trade journal and find patterns."""
    with open(trade_path) as f:
        trades = json.load(f)

    real = [t for t in trades if "pnl" in t]
    if not real:
        return {"error": "No trades to analyze"}

    # Basic stats
    total = len(real)
    wins = [t for t in real if t.get("win")]
    losses = [t for t in real if not t.get("win")]
    total_pnl = sum(t["pnl"] for t in real)

    # By session
    by_session = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0})
    for t in real:
        s = t.get("session", "unknown")
        if t.get("win"):
            by_session[s]["wins"] += 1
        else:
            by_session[s]["losses"] += 1
        by_session[s]["pnl"] += t["pnl"]

    # By regime
    by_regime = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0})
    for t in real:
        r = t.get("regime", "unknown")
        if t.get("win"):
            by_regime[r]["wins"] += 1
        else:
            by_regime[r]["losses"] += 1
        by_regime[r]["pnl"] += t["pnl"]

    # By side
    by_side = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0})
    for t in real:
        s = t.get("side", "unknown")
        if t.get("win"):
            by_side[s]["wins"] += 1
        else:
            by_side[s]["losses"] += 1
        by_side[s]["pnl"] += t["pnl"]

    # By hour
    by_hour = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0})
    for t in real:
        time_str = t.get("time", "")
        if ":" in time_str:
            hour = int(time_str.split(":")[0])
            if t.get("win"):
                by_hour[hour]["wins"] += 1
            else:
                by_hour[hour]["losses"] += 1
            by_hour[hour]["pnl"] += t["pnl"]

    # Print report
    print(f"\n{'='*60}")
    print(f"  TRADE JOURNAL ANALYSIS")
    print(f"{'='*60}")
    print(f"\n  Total trades: {total}")
    print(f"  Wins: {len(wins)} ({len(wins)/total*100:.0f}%)")
    print(f"  Losses: {len(losses)} ({len(losses)/total*100:.0f}%)")
    print(f"  Total PnL: ${total_pnl:+.2f}")
    print(f"  Avg PnL/trade: ${total_pnl/total:+.2f}")

    # By session
    print(f"\n  BY SESSION:")
    for session, stats in sorted(by_session.items()):
        t = stats["wins"] + stats["losses"]
        wr = stats["wins"] / t * 100 if t > 0 else 0
        print(f"    {session:12} trades={t:3} WR={wr:.0f}% PnL=${stats['pnl']:+.2f}")

    # By regime
    print(f"\n  BY REGIME:")
    for regime, stats in sorted(by_regime.items()):
        t = stats["wins"] + stats["losses"]
        wr = stats["wins"] / t * 100 if t > 0 else 0
        print(f"    {regime:12} trades={t:3} WR={wr:.0f}% PnL=${stats['pnl']:+.2f}")

    # By side
    print(f"\n  BY SIDE:")
    for side, stats in sorted(by_side.items()):
        t = stats["wins"] + stats["losses"]
        wr = stats["wins"] / t * 100 if t > 0 else 0
        print(f"    {side:12} trades={t:3} WR={wr:.0f}% PnL=${stats['pnl']:+.2f}")

    # By hour
    print(f"\n  BY HOUR (UTC):")
    for hour in sorted(by_hour.keys()):
        stats = by_hour[hour]
        t = stats["wins"] + stats["losses"]
        wr = stats["wins"] / t * 100 if t > 0 else 0
        bar = "#" * int(wr / 5)
        print(f"    {hour:02}:00  trades={t:3} WR={wr:.0f}% {bar}")

    # Recommendations
    print(f"\n  {'='*60}")
    print(f"  RECOMMENDATIONS:")

    # Best session
    if by_session:
        best_session = max(by_session.items(), key=lambda x: x[1]["pnl"])
        print(f"    - Best session: {best_session[0]} (PnL=${best_session[1]['pnl']:+.2f})")

    # Best regime
    if by_regime:
        best_regime = max(by_regime.items(), key=lambda x: x[1]["pnl"])
        print(f"    - Best regime: {best_regime[0]} (PnL=${best_regime[1]['pnl']:+.2f})")

    # Best side
    if by_side:
        best_side = max(by_side.items(), key=lambda x: x[1]["pnl"])
        print(f"    - Best side: {best_side[0]} (PnL=${best_side[1]['pnl']:+.2f})")

    # Best hour
    if by_hour:
        best_hour = max(by_hour.items(), key=lambda x: x[1]["pnl"])
        print(f"    - Best hour: {best_hour[0]:02}:00 UTC (PnL=${best_hour[1]['pnl']:+.2f})")

    print(f"{'='*60}\n")

    return {
        "total": total,
        "wins": len(wins),
        "losses": len(losses),
        "total_pnl": total_pnl,
        "by_session": dict(by_session),
        "by_regime": dict(by_regime),
        "by_side": dict(by_side),
        "by_hour": dict(by_hour),
    }


def main():
    parser = argparse.ArgumentParser(description="Trade journal analyzer")
    parser.add_argument("--trades", default="checkpoints_vol10/paper_trades.json")
    args = parser.parse_args()

    if not Path(args.trades).exists():
        print(f"No trade file found at {args.trades}")
        return

    analyze(args.trades)


if __name__ == "__main__":
    main()
