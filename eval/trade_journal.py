#!/usr/bin/env python3
"""
TRADE JOURNAL ANALYZER
======================
Automatically parse live trades and calculate:
- Real win rate
- Real avg win/loss
- Real profit factor
- Comparison to backtest
- Drawdown analysis
- Streak analysis
"""
import csv
import os
import statistics
from collections import defaultdict

def analyze_trades(filepath):
    """Analyze a single trade log"""
    trades = []
    
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                trades.append({
                    'time': row.get('time', ''),
                    'symbol': row.get('symbol', ''),
                    'action': row.get('action', ''),
                    'direction': row.get('direction', ''),
                    'entry': float(row.get('entry', 0)),
                    'exit': float(row.get('exit', 0)),
                    'pnl': float(row.get('pnl', 0)),
                    'reason': row.get('reason', ''),
                    'bars_held': int(row.get('bars_held', 0)),
                    'equity': float(row.get('equity', 0)),
                })
            except:
                continue
    
    if not trades:
        return None
    
    # Filter to closed trades only
    closed = [t for t in trades if t['action'] == 'CLOSE']
    
    if not closed:
        return None
    
    # Basic stats
    pnls = [t['pnl'] for t in closed]
    wins = [t for t in closed if t['pnl'] > 0]
    losses = [t for t in closed if t['pnl'] <= 0]
    
    # Win rate
    wr = len(wins) / len(closed) * 100 if closed else 0
    
    # Avg win/loss
    avg_win = statistics.mean([t['pnl'] for t in wins]) if wins else 0
    avg_loss = statistics.mean([t['pnl'] for t in losses]) if losses else 0
    
    # Profit factor
    total_win = sum(t['pnl'] for t in wins)
    total_loss = abs(sum(t['pnl'] for t in losses))
    pf = total_win / total_loss if total_loss > 0 else 999
    
    # Risk/reward
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else 999
    
    # Drawdown
    equity_curve = [1000]  # Starting equity
    for t in closed:
        equity_curve.append(equity_curve[-1] + t['pnl'])
    
    peak = 1000
    max_dd = 0
    max_dd_pct = 0
    for eq in equity_curve:
        peak = max(peak, eq)
        dd = peak - eq
        dd_pct = dd / peak * 100
        max_dd = max(max_dd, dd)
        max_dd_pct = max(max_dd_pct, dd_pct)
    
    # Streaks
    max_win_streak = 0
    max_loss_streak = 0
    current_streak = 0
    streak_type = None
    
    for t in closed:
        if t['pnl'] > 0:
            if streak_type == 'win':
                current_streak += 1
            else:
                current_streak = 1
                streak_type = 'win'
            max_win_streak = max(max_win_streak, current_streak)
        else:
            if streak_type == 'loss':
                current_streak += 1
            else:
                current_streak = 1
                streak_type = 'loss'
            max_loss_streak = max(max_loss_streak, current_streak)
    
    # By exit reason
    by_reason = defaultdict(list)
    for t in closed:
        by_reason[t['reason']].append(t)
    
    # By direction
    by_dir = defaultdict(list)
    for t in closed:
        by_dir[t['direction']].append(t)
    
    return {
        'total_trades': len(closed),
        'wins': len(wins),
        'losses': len(losses),
        'wr': wr,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': sum(pnls),
        'pf': pf,
        'rr': rr,
        'max_dd': max_dd,
        'max_dd_pct': max_dd_pct,
        'max_win_streak': max_win_streak,
        'max_loss_streak': max_loss_streak,
        'by_reason': dict(by_reason),
        'by_dir': dict(by_dir),
        'equity_curve': equity_curve,
    }

def print_report(name, stats):
    """Print formatted report"""
    print(f"\n{'='*60}")
    print(f"TRADE JOURNAL: {name}")
    print(f"{'='*60}")
    
    print(f"\n--- PERFORMANCE ---")
    print(f"  Total trades: {stats['total_trades']}")
    print(f"  Wins: {stats['wins']} ({stats['wr']:.1f}%)")
    print(f"  Losses: {stats['losses']} ({100-stats['wr']:.1f}%)")
    print(f"  Total PnL: ${stats['total_pnl']:.2f}")
    
    print(f"\n--- RATIOS ---")
    print(f"  Avg win: ${stats['avg_win']:.4f}")
    print(f"  Avg loss: ${stats['avg_loss']:.4f}")
    print(f"  Profit factor: {stats['pf']:.2f}")
    print(f"  Risk/Reward: {stats['rr']:.2f}")
    
    print(f"\n--- RISK ---")
    print(f"  Max drawdown: ${stats['max_dd']:.2f} ({stats['max_dd_pct']:.1f}%)")
    print(f"  Max win streak: {stats['max_win_streak']}")
    print(f"  Max loss streak: {stats['max_loss_streak']}")
    
    print(f"\n--- BY EXIT REASON ---")
    for reason, trades in sorted(stats['by_reason'].items()):
        pnls = [t['pnl'] for t in trades]
        avg = statistics.mean(pnls) if pnls else 0
        print(f"  {reason}: {len(trades)} trades, Avg PnL=${avg:.4f}")
    
    print(f"\n--- BY DIRECTION ---")
    for direction, trades in sorted(stats['by_dir'].items()):
        pnls = [t['pnl'] for t in trades]
        avg = statistics.mean(pnls) if pnls else 0
        wr = sum(1 for p in pnls if p > 0) / len(pnls) * 100 if pnls else 0
        print(f"  {direction}: {len(trades)} trades, WR={wr:.1f}%, Avg=${avg:.4f}")

def compare_to_backtest(stats, backtest_wr=99.7, backtest_pf=791):
    """Compare live results to backtest"""
    print(f"\n--- LIVE vs BACKTEST ---")
    print(f"  {'Metric':<20} {'Live':<15} {'Backtest':<15} {'Difference':<15}")
    print(f"  {'-'*65}")
    print(f"  {'Win Rate':<20} {stats['wr']:<15.1f} {backtest_wr:<15.1f} {stats['wr']-backtest_wr:+.1f}%")
    print(f"  {'Profit Factor':<20} {stats['pf']:<15.2f} {backtest_pf:<15.2f} {stats['pf']-backtest_pf:+.2f}")

def main():
    print("="*60)
    print("TRADE JOURNAL ANALYZER")
    print("="*60)
    
    log_dir = "SpikeCatcher"
    if not os.path.exists(log_dir):
        print(f"No trade logs found in {log_dir}/")
        return
    
    for f in sorted(os.listdir(log_dir)):
        if f.startswith("trades_") and f.endswith(".csv"):
            filepath = os.path.join(log_dir, f)
            name = f.replace("trades_", "").replace(".csv", "")
            
            stats = analyze_trades(filepath)
            if stats:
                print_report(name, stats)
                compare_to_backtest(stats)

if __name__ == "__main__":
    main()
