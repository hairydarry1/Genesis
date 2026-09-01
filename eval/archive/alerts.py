"""
eval/alerts.py — Trading alert system.

Monitors paper trading and alerts on:
  - New trades (open/close)
  - Drawdown warnings (>3%, >5%, >10%)
  - Winning/losing streaks (>3 consecutive)
  - Regime changes
  - Session changes
  - Equity milestones

Usage:
    python -m eval.alerts
    python -m eval.alerts --watch checkpoints_vol10/paper_trades.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


class AlertSystem:
    """Monitors trading and generates alerts."""

    def __init__(
        self,
        dd_warn: float = 0.03,    # 3% drawdown warning
        dd_danger: float = 0.05,   # 5% drawdown danger
        dd_critical: float = 0.10, # 10% drawdown critical
        streak_warn: int = 3,      # 3 consecutive losses
        streak_pause: int = 5,     # 5 consecutive losses = pause
    ):
        self.dd_warn = dd_warn
        self.dd_danger = dd_danger
        self.dd_critical = dd_critical
        self.streak_warn = streak_warn
        self.streak_pause = streak_pause

        self.last_trade_count = 0
        self.last_equity = 10000.0
        self.peak_equity = 10000.0
        self.consec_losses = 0
        self.consec_wins = 0
        self.alerts: list[dict] = []

    def check(self, trades: list[dict]) -> list[dict]:
        """Check for new alerts. Returns list of new alerts."""
        new_alerts = []

        if not trades:
            return new_alerts

        # Get latest state
        latest = trades[-1]
        equity = latest.get("equity", 10000)
        self.peak_equity = max(self.peak_equity, equity)

        # Check for new trades
        if len(trades) > self.last_trade_count:
            for t in trades[self.last_trade_count:]:
                if "pnl" in t:
                    # Trade completed
                    pnl = t["pnl"]
                    win = t.get("win", pnl > 0)

                    if win:
                        self.consec_wins += 1
                        self.consec_losses = 0
                    else:
                        self.consec_losses += 1
                        self.consec_wins = 0

                    alert = {
                        "type": "trade",
                        "severity": "info" if win else "warning",
                        "message": f"{'WIN' if win else 'LOSS'} {t['side']} @ {t['entry']:.2f} -> {t['exit']:.2f} PnL: ${pnl:+.2f}",
                        "time": t.get("time", ""),
                        "regime": t.get("regime", "?"),
                        "session": t.get("session", "?"),
                    }
                    new_alerts.append(alert)

                    # Streak alerts
                    if self.consec_losses >= self.streak_pause:
                        new_alerts.append({
                            "type": "streak",
                            "severity": "critical",
                            "message": f"LOSING STREAK: {self.consec_losses} consecutive losses — consider pausing",
                        })
                    elif self.consec_losses >= self.streak_warn:
                        new_alerts.append({
                            "type": "streak",
                            "severity": "warning",
                            "message": f"Loss streak: {self.consec_losses} consecutive losses",
                        })

                    if self.consec_wins >= 3:
                        new_alerts.append({
                            "type": "streak",
                            "severity": "ok",
                            "message": f"Winning streak: {self.consec_wins} consecutive wins",
                        })

            self.last_trade_count = len(trades)

        # Drawdown alerts
        dd = (self.peak_equity - equity) / self.peak_equity if self.peak_equity > 0 else 0

        if dd >= self.dd_critical:
            new_alerts.append({
                "type": "drawdown",
                "severity": "critical",
                "message": f"CRITICAL DRAWDOWN: {dd*100:.1f}% — STOP TRADING",
            })
        elif dd >= self.dd_danger:
            new_alerts.append({
                "type": "drawdown",
                "severity": "danger",
                "message": f"Drawdown: {dd*100:.1f}% — reduce position size",
            })
        elif dd >= self.dd_warn:
            new_alerts.append({
                "type": "drawdown",
                "severity": "warning",
                "message": f"Drawdown: {dd*100:.1f}% — monitor closely",
            })

        # Equity milestones
        if equity >= 10100 and self.last_equity < 10100:
            new_alerts.append({
                "type": "milestone",
                "severity": "ok",
                "message": f"EQUITY MILESTONE: +1% (${equity:.2f})",
            })
        elif equity >= 10500 and self.last_equity < 10500:
            new_alerts.append({
                "type": "milestone",
                "severity": "ok",
                "message": f"EQUITY MILESTONE: +5% (${equity:.2f})",
            })
        elif equity <= 9900 and self.last_equity > 9900:
            new_alerts.append({
                "type": "milestone",
                "severity": "warning",
                "message": f"EQUITY WARNING: -1% (${equity:.2f})",
            })

        self.last_equity = equity
        self.alerts.extend(new_alerts)
        return new_alerts


def format_alert(alert: dict) -> str:
    """Format alert for display."""
    icons = {
        "info": "ℹ️",
        "ok": "✅",
        "warning": "⚠️",
        "danger": "🔴",
        "critical": "🚨",
    }
    icon = icons.get(alert["severity"], "❓")
    return f"  {icon} [{alert['type'].upper()}] {alert['message']}"


def monitor(trade_path: str, interval: int = 30):
    """Monitor paper trading and print alerts."""
    print(f"Monitoring {trade_path} (check every {interval}s)...")
    print("Press Ctrl+C to stop\n")

    alert_sys = AlertSystem()
    last_mtime = 0

    while True:
        try:
            path = Path(trade_path)
            if not path.exists():
                time.sleep(interval)
                continue

            mtime = path.stat().st_mtime
            if mtime > last_mtime:
                last_mtime = mtime
                with open(path) as f:
                    trades = json.load(f)

                new_alerts = alert_sys.check(trades)

                if new_alerts:
                    for alert in new_alerts:
                        print(format_alert(alert))
                    print()

            time.sleep(interval)
        except KeyboardInterrupt:
            break


def main():
    parser = argparse.ArgumentParser(description="Trading alert system")
    parser.add_argument("--watch", default="checkpoints_vol10/paper_trades.json")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()

    monitor(args.watch, args.interval)


if __name__ == "__main__":
    main()
