"""
eval/vol10_monitor.py — Live monitoring for VOL10 paper trading.

Watches the paper trading output and provides:
  - Trade log analysis
  - Win rate tracking
  - Drawdown alerts
  - Session summary

Usage:
    python -m eval.vol10_monitor
    python -m eval.vol10_monitor --watch checkpoints_vol10/paper_trades.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def analyze_trades(trade_path: str) -> dict:
    """Analyze completed trades from JSON log."""
    path = Path(trade_path)
    if not path.exists():
        return {"error": "No trade log found"}

    with open(path) as f:
        trades = json.load(f)

    if not trades:
        return {"error": "No trades yet"}

    # Filter to actual trades (have pnl field)
    real_trades = [t for t in trades if "pnl" in t]
    if not real_trades:
        return {"error": "No completed trades yet", "pending": len(trades)}

    pnls = [t["pnl"] for t in real_trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    total_pnl = sum(pnls)
    win_rate = len(wins) / len(pnls) * 100
    avg_win = sum(wins) / len(wins) if wins else 0
    avg_loss = sum(losses) / len(losses) if losses else 0
    profit_factor = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else float("inf")

    # Drawdown from equity curve
    equities = [t.get("equity", 10000) for t in real_trades]
    peak = equities[0]
    max_dd = 0
    for eq in equities:
        peak = max(peak, eq)
        dd = (peak - eq) / peak * 100
        max_dd = max(max_dd, dd)

    # Side analysis
    longs = [t for t in real_trades if t.get("side") == "LONG"]
    shorts = [t for t in real_trades if t.get("side") == "SHORT"]
    long_wins = sum(1 for t in longs if t.get("pnl", 0) > 0)
    short_wins = sum(1 for t in shorts if t.get("pnl", 0) > 0)

    # Holding period
    # (from time strings — approximate since we don't have bar-by-bar)
    pnl_pct = [t.get("pnl_pct", 0) for t in real_trades]
    avg_pnl_pct = sum(pnl_pct) / len(pnl_pct) if pnl_pct else 0

    # Consecutive losses
    max_consec_loss = 0
    consec = 0
    for p in pnls:
        if p <= 0:
            consec += 1
            max_consec_loss = max(max_consec_loss, consec)
        else:
            consec = 0

    return {
        "total_trades": len(pnls),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_pnl": sum(pnls) / len(pnls),
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_pnl_pct": avg_pnl_pct,
        "profit_factor": profit_factor,
        "max_drawdown": max_dd,
        "max_consecutive_losses": max_consec_loss,
        "long_trades": len(longs),
        "short_trades": len(shorts),
        "long_win_rate": long_wins / len(longs) * 100 if longs else 0,
        "short_win_rate": short_wins / len(shorts) * 100 if shorts else 0,
        "final_equity": equities[-1] if equities else 10000,
    }


def print_analysis(stats: dict, symbol: str = "VOL10"):
    """Pretty-print trade analysis."""
    if "error" in stats:
        print(f"  {stats['error']}")
        return

    print(f"\n{'='*55}")
    print(f"  {symbol} PAPER TRADING ANALYSIS")
    print(f"{'='*55}")

    print(f"\n  TRADE SUMMARY:")
    print(f"    Total trades:    {stats['total_trades']}")
    print(f"    Wins / Losses:   {stats['wins']} / {stats['losses']}")
    print(f"    Win rate:        {stats['win_rate']:.1f}%")
    print(f"    Profit factor:   {stats['profit_factor']:.2f}")

    print(f"\n  P&L:")
    print(f"    Total PnL:       ${stats['total_pnl']:+.2f}")
    print(f"    Avg PnL/trade:   ${stats['avg_pnl']:+.2f} ({stats['avg_pnl_pct']:+.3f}%)")
    print(f"    Avg win:         ${stats['avg_win']:+.2f}")
    print(f"    Avg loss:        ${stats['avg_loss']:+.2f}")

    print(f"\n  RISK:")
    print(f"    Max drawdown:    {stats['max_drawdown']:.2f}%")
    print(f"    Max consec loss: {stats['max_consecutive_losses']}")
    print(f"    Final equity:    ${stats['final_equity']:.2f}")
    print(f"    Return:          {(stats['final_equity']/10000 - 1)*100:+.2f}%")

    print(f"\n  DIRECTION BREAKDOWN:")
    print(f"    Longs:           {stats['long_trades']} (WR: {stats['long_win_rate']:.0f}%)")
    print(f"    Shorts:          {stats['short_trades']} (WR: {stats['short_win_rate']:.0f}%)")

    # Verdict
    print(f"\n  {'='*55}")
    if stats["win_rate"] > 52 and stats["profit_factor"] > 1.05:
        print(f"  VERDICT: LOOKING GOOD — edge confirmed")
    elif stats["win_rate"] > 50 and stats["profit_factor"] > 1.0:
        print(f"  VERDICT: MARGINAL — need more trades to confirm")
    elif stats["total_trades"] < 20:
        print(f"  VERDICT: TOO EARLY — need 50+ trades for statistical significance")
    else:
        print(f"  VERDICT: WEAK — may need strategy adjustment")
    print(f"{'='*55}\n")


def watch(trade_path: str, interval: int = 60):
    """Watch and update analysis periodically."""
    print(f"Watching {trade_path} (refresh every {interval}s)...")
    print("Press Ctrl+C to stop\n")

    last_mtime = 0
    while True:
        try:
            mtime = Path(trade_path).stat().st_mtime if Path(trade_path).exists() else 0
            if mtime > last_mtime:
                last_mtime = mtime
                stats = analyze_trades(trade_path)
                print_analysis(stats)
            time.sleep(interval)
        except KeyboardInterrupt:
            break


def main():
    parser = argparse.ArgumentParser(description="VOL10 paper trading monitor")
    parser.add_argument("--watch", default="checkpoints_vol10/paper_trades.json",
                        help="Trade log to watch")
    parser.add_argument("--interval", type=int, default=60, help="Refresh interval (seconds)")
    args = parser.parse_args()

    if args.watch and Path(args.watch).exists():
        # One-shot analysis or watch mode
        stats = analyze_trades(args.watch)
        print_analysis(stats)

        if stats.get("total_trades", 0) < 50:
            watch(args.watch, args.interval)
    else:
        print(f"Waiting for trades at {args.watch}...")
        watch(args.watch, args.interval)


if __name__ == "__main__":
    main()
