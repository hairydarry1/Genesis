#!/usr/bin/env python3
"""
SpikeCatcher v3 Paper Trading Tracker
Logs trades, tracks performance, validates strategy against backtest targets.
"""

import csv
import os
from datetime import datetime, timedelta

# === CONFIGURATION ===
TRACKER_FILE = "SpikeCatcher/paper_trading_log.csv"
DAILY_REPORT = "SpikeCatcher/daily_report.txt"

# Backtest targets (what we expect)
TARGETS = {
    "boom500":  {"wr": 57.3, "pf": 1.24, "trades_per_day": 28, "pnl_per_trade": 0.18},
    "crash500": {"wr": 62.7, "pf": 1.23, "trades_per_day": 29, "pnl_per_trade": 0.19},
    "boom1000": {"wr": 81.1, "pf": 1.07, "trades_per_day": 16, "pnl_per_trade": 0.05},
    "crash1000":{"wr": 80.3, "pf": 1.60, "trades_per_day": 16, "pnl_per_trade": 0.29},
}

# Red flags (stop trading if these hit)
RED_FLAGS = {
    "min_wr": 50.0,           # Below this = strategy broken
    "max_dd_pct": 15.0,       # Above this = stop trading
    "min_pf": 0.8,            # Below this = losing money
    "min_trades_per_day": 5,  # Below this = not enough data
}

def init_tracker():
    """Initialize the paper trading log file."""
    if not os.path.exists("SpikeCatcher"):
        os.makedirs("SpikeCatcher")
    
    if not os.path.exists(TRACKER_FILE):
        with open(TRACKER_FILE, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp", "symbol", "action", "direction", "entry_price",
                "exit_price", "pnl", "reason", "bars_held", "hour",
                "cold_hour", "equity_after"
            ])
        print(f"[INIT] Created tracker: {TRACKER_FILE}")

def log_trade(symbol, action, direction, entry, exit_price, pnl, reason, bars, hour, cold, equity):
    """Log a single trade."""
    with open(TRACKER_FILE, 'a', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            datetime.now().isoformat(), symbol, action, direction,
            f"{entry:.5f}", f"{exit_price:.5f}", f"{pnl:.4f}",
            reason, bars, hour, cold, f"{equity:.2f}"
        ])

def get_trades(symbol=None):
    """Read all trades from log."""
    trades = []
    if not os.path.exists(TRACKER_FILE):
        return trades
    
    with open(TRACKER_FILE, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if symbol and row['symbol'] != symbol:
                continue
            trades.append(row)
    return trades

def calculate_stats(trades):
    """Calculate performance statistics."""
    if not trades:
        return None
    
    pnls = [float(t['pnl']) for t in trades]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    losses = n - wins
    wr = wins / n * 100 if n > 0 else 0
    
    gross_profit = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p <= 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    
    total_pnl = sum(pnls)
    avg_pnl = total_pnl / n if n > 0 else 0
    
    # Exit reasons
    reasons = {}
    for t in trades:
        r = t['reason']
        reasons[r] = reasons.get(r, 0) + 1
    
    # Cold hour stats
    cold_trades = [t for t in trades if t['cold_hour'] == 'True']
    hot_trades = [t for t in trades if t['cold_hour'] == 'False']
    
    return {
        'n': n,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'pf': pf,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'reasons': reasons,
        'cold_trades': len(cold_trades),
        'hot_trades': len(hot_trades),
    }

def check_red_flags(stats, symbol):
    """Check if any red flags are triggered."""
    flags = []
    sym = symbol.lower().replace(' ', '')
    
    if stats['n'] < 10:
        flags.append("Need more trades (have {}, need 10+)".format(stats['n']))
    
    if stats['wr'] < RED_FLAGS['min_wr']:
        flags.append("WR {}% below minimum {}%".format(stats['wr'], RED_FLAGS['min_wr']))
    
    if stats['pf'] < RED_FLAGS['min_pf']:
        flags.append("PF {} below minimum {}".format(stats['pf'], RED_FLAGS['min_pf']))
    
    return flags

def print_report(symbol=None):
    """Print performance report."""
    trades = get_trades(symbol)
    if not trades:
        print("No trades logged yet.")
        return
    
    stats = calculate_stats(trades)
    
    print(f"\n{'='*60}")
    print(f"  PAPER TRADING REPORT{' — ' + symbol.upper() if symbol else ''}")
    print(f"  Period: {trades[0]['timestamp'][:10]} to {trades[-1]['timestamp'][:10]}")
    print(f"{'='*60}")
    
    print(f"\n  PERFORMANCE:")
    print(f"    Trades: {stats['n']}")
    print(f"    Win Rate: {stats['wr']:.1f}%")
    print(f"    Profit Factor: {stats['pf']:.2f}")
    print(f"    Total PnL: ${stats['total_pnl']:.4f}")
    print(f"    Avg PnL/Trade: ${stats['avg_pnl']:.4f}")
    print(f"    Gross Profit: ${stats['gross_profit']:.4f}")
    print(f"    Gross Loss: ${stats['gross_loss']:.4f}")
    
    print(f"\n  EXIT REASONS:")
    for reason, count in sorted(stats['reasons'].items()):
        print(f"    {reason}: {count} trades ({count/stats['n']*100:.1f}%)")
    
    print(f"\n  COLD HOUR ANALYSIS:")
    print(f"    Cold hour trades: {stats['cold_trades']} ({stats['cold_trades']/stats['n']*100:.1f}%)")
    print(f"    Hot hour trades: {stats['hot_trades']} ({stats['hot_trades']/stats['n']*100:.1f}%)")
    
    # Compare to targets
    if symbol:
        sym = symbol.lower().replace(' ', '')
        if sym in TARGETS:
            target = TARGETS[sym]
            print(f"\n  vs BACKTEST TARGET:")
            print(f"    WR: {stats['wr']:.1f}% vs {target['wr']:.1f}% target ({'PASS' if stats['wr'] >= target['wr']*0.9 else 'FAIL'})")
            print(f"    PF: {stats['pf']:.2f} vs {target['pf']:.2f} target ({'PASS' if stats['pf'] >= target['pf']*0.9 else 'FAIL'})")
    
    # Red flags
    flags = check_red_flags(stats, symbol or 'all')
    if flags:
        print(f"\n  RED FLAGS:")
        for flag in flags:
            print(f"    [!] {flag}")
    else:
        print(f"\n  STATUS: NO RED FLAGS")
    
    print(f"{'='*60}\n")

if __name__ == '__main__':
    import sys
    
    init_tracker()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == 'report':
            symbol = sys.argv[2] if len(sys.argv) > 2 else None
            print_report(symbol)
        elif sys.argv[1] == 'test':
            # Add test trades
            log_trade("BOOM500", "OPEN", "SHORT", 4738.258, 0, 0, "entry", 0, 14, True, 200.00)
            log_trade("BOOM500", "CLOSE", "SHORT", 4738.258, 4737.100, 0.158, "timeout", 6, 14, True, 200.16)
            log_trade("CRASH500", "OPEN", "LONG", 2940.123, 0, 0, "entry", 0, 22, True, 200.00)
            log_trade("CRASH500", "CLOSE", "LONG", 2940.123, 2940.891, 0.192, "tick_exit", 5, 22, True, 200.19)
            print("[TEST] Added 2 sample trades")
            print_report()
        else:
            print("Usage: python paper_trading_tracker.py [report|test] [symbol]")
    else:
        print("Usage:")
        print("  python paper_trading_tracker.py report [symbol]  — Show performance")
        print("  python paper_trading_tracker.py test             — Add sample trades")
