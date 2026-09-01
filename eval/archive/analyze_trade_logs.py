"""
Analyze trade data to discover new patterns and filter opportunities.

Works by running the SpikeFadeEngine on historical data, capturing
every trade with full context, then analyzing which combinations of
factors predict wins vs losses.
"""
import sys, os
sys.path.insert(0, '.')

import numpy as np
import csv
from collections import defaultdict
from datetime import datetime, timezone

from eval.paper_trading_v2 import SpikeFadeEngine, SPIKE_FADE_CONFIGS


def run_backtest_with_full_logging(symbol: str, max_bars: int = 5000) -> list[dict]:
    """Run spike fade engine on historical data and capture every trade with context."""
    import pandas as pd

    # Load data
    df = pd.read_parquet(f'data/candles_{symbol.lower()}_1min.parquet')

    # Build candles
    candles = []
    for _, row in df.iterrows():
        candles.append({
            'epoch': int(row.get('epoch', 0)),
            'open': float(row['open']),
            'high': float(row['high']),
            'low': float(row['low']),
            'close': float(row['close']),
            'volume': 1,
        })

    if max_bars:
        candles = candles[-max_bars:]

    # Run engine
    eng = SpikeFadeEngine(symbol, risk_pct=0.10)
    eng.use_slippage = True
    eng.use_vol_regime = True
    eng.use_auto_tuner = False  # Don't auto-tune during analysis
    eng.use_session_filter = False

    trades = []
    for i, candle in enumerate(candles):
        result = eng.on_bar(candle)

        if result and result.get('action') == 'open':
            # Capture spike context at entry time
            _entry_spike_x = 0
            if len(eng._returns) > 50:
                abs_rets = np.array(list(eng._returns)[-200:])
                med = float(np.median(np.abs(abs_rets)))
                _entry_spike_x = eng._last_spike_size / med if med > 0 else 0
            _entry_cluster = eng._cluster_active
            _entry_bars_since_spike = eng._bars_since_spike

        if result and result.get('action') == 'close':
            # Enrich with market context at entry
            entry_idx = max(0, i - result['bars_held'])

            # Compute metrics at entry time
            closes = list(eng._closes)
            if entry_idx > 0 and entry_idx < len(closes):
                entry_closes = closes[max(0, entry_idx - 50):entry_idx + 1]
            else:
                entry_closes = closes[:entry_idx + 1]

            # Drift at entry
            if len(entry_closes) >= 20:
                short_drift = (entry_closes[-1] - entry_closes[-5]) / entry_closes[-5]
                long_drift = (entry_closes[-1] - entry_closes[-20]) / entry_closes[-20]
                drift_dir = 'up' if short_drift > 0 else 'down' if short_drift < 0 else 'flat'
                drift_strength = abs(short_drift)
            else:
                short_drift = 0
                long_drift = 0
                drift_dir = 'flat'
                drift_strength = 0

            # Volatility at entry
            if len(entry_closes) >= 20:
                arr = np.array(entry_closes[-21:])
                rets = np.diff(arr) / arr[:-1]
                vol = float(np.std(rets))
                median_vol = float(np.median(np.abs(rets)))
            else:
                vol = 0
                median_vol = 0.0001

            # ATR at entry
            if entry_idx > 0 and entry_idx < len(candles):
                atr_bars = candles[max(0, entry_idx - 14):entry_idx + 1]
                atrs = [b['high'] - b['low'] for b in atr_bars]
                atr = np.mean(atrs) if atrs else 0
                atr_pct = atr / candles[entry_idx]['close'] if candles[entry_idx]['close'] > 0 else 0
            else:
                atr_pct = 0

            # Spike size — captured at entry time
            spike_x = _entry_spike_x if '_entry_spike_x' in dir() else 0

            # Time of day
            entry_epoch = candles[entry_idx]['epoch'] if entry_idx < len(candles) else 0
            hour = datetime.fromtimestamp(entry_epoch, tz=timezone.utc).hour if entry_epoch > 0 else 0

            # Bar of day (0-1439)
            bar_of_day = (entry_epoch % 86400) // 60 if entry_epoch > 0 else 0

            trade = {
                'trade_id': len(trades) + 1,
                'symbol': symbol,
                'entry_price': result.get('entry_price', 0),
                'exit_price': result.get('exit_price', 0),
                'direction': 'short' if eng.fade_direction == -1 else 'long',
                'pnl': result.get('pnl', 0),
                'pnl_pct': result.get('pnl_pct', 0),
                'won': result.get('pnl', 0) > 0,
                'exit_reason': result.get('exit_reason', ''),
                'bars_held': result.get('bars_held', 0),
                'equity_after': result.get('equity', 0),
                'position_size': result.get('position_size', 0),
                # Context
                'drift_dir': drift_dir,
                'drift_strength': round(drift_strength, 6),
                'drift_vs_fade': 'aligned' if (
                    (drift_dir == 'down' and eng.fade_direction == -1) or
                    (drift_dir == 'up' and eng.fade_direction == 1)
                ) else ('against' if drift_dir != 'flat' else 'flat'),
                'long_drift': round(long_drift, 6),
                'vol_at_entry': round(vol, 8),
                'median_vol': round(median_vol, 8),
                'vol_ratio': round(vol / median_vol, 2) if median_vol > 0 else 0,
                'atr_pct': round(atr_pct, 6),
                'spike_x': round(spike_x, 2),
                'hour': hour,
                'bar_of_day': bar_of_day,
                'cluster_size': 1 if not _entry_cluster else 2,
                'bars_since_spike': _entry_bars_since_spike if '_entry_bars_since_spike' in dir() else 0,
                'thinking_category': result.get('category', ''),
                'conviction': result.get('conviction', 0),
                # Derived buckets
                'drift_bucket': 'flat' if drift_strength < 0.0001 else ('weak' if drift_strength < 0.0005 else ('medium' if drift_strength < 0.001 else 'strong')),
                'vol_bucket': 'low' if vol < median_vol * 0.8 else ('normal' if vol < median_vol * 1.2 else 'high'),
                'spike_bucket': 'small' if spike_x < 5 else ('medium' if spike_x < 10 else ('large' if spike_x < 15 else 'algo')),
                'session': 'asian' if 0 <= hour < 8 else ('london' if 8 <= hour < 14 else 'ny'),
            }
            trades.append(trade)

    return trades


def analyze_patterns(trades: list[dict], symbol: str):
    """Deep analysis of trade patterns to discover new filters."""
    if not trades:
        print(f'  No trades for {symbol}')
        return

    total = len(trades)
    wins = [t for t in trades if t['won']]
    losses = [t for t in trades if not t['won']]
    wr = len(wins) / total * 100
    total_pnl = sum(t['pnl'] for t in trades)

    print(f'\n{"="*78}')
    print(f'  {symbol} — TRADE PATTERN ANALYSIS ({total} trades, {wr:.1f}% WR, PnL=${total_pnl:+.2f})')
    print(f'{"="*78}')

    # ── 1. DRIFT ANALYSIS ──
    print(f'\n  ── DRIFT ANALYSIS ──')
    print(f'  {"Drift vs Fade":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}  {"TotalPnL":>10}  {"Action":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}  {"-"*10}  {"-"*10}')

    for drift_vs in ['aligned', 'flat', 'against']:
        subset = [t for t in trades if t['drift_vs_fade'] == drift_vs]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        tot_pnl = sum(t['pnl'] for t in subset)
        action = 'KEEP' if swr > 55 else ('AVOID' if swr < 45 else 'NEUTRAL')
        print(f'  {drift_vs:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}  ${tot_pnl:>+9.2f}  {action:>10}')

    # Drift strength buckets
    print(f'\n  {"Drift Strength":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}')
    for bucket in ['flat', 'weak', 'medium', 'strong']:
        subset = [t for t in trades if t['drift_bucket'] == bucket]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        print(f'  {bucket:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}')

    # ── 2. SPIKE SIZE ANALYSIS ──
    print(f'\n  ── SPIKE SIZE ANALYSIS ──')
    print(f'  {"Spike (x med)":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}  {"TotalPnL":>10}  {"Action":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}  {"-"*10}  {"-"*10}')

    for bucket in ['small', 'medium', 'large', 'algo']:
        subset = [t for t in trades if t['spike_bucket'] == bucket]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        tot_pnl = sum(t['pnl'] for t in subset)
        action = 'KEEP' if swr > 55 else ('AVOID' if swr < 45 else 'NEUTRAL')
        print(f'  {bucket:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}  ${tot_pnl:>+9.2f}  {action:>10}')

    # Precise spike ranges
    print(f'\n  {"Spike Range":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}')
    for lo, hi, label in [(3, 5, '3-5x'), (5, 8, '5-8x'), (8, 10, '8-10x'),
                           (10, 15, '10-15x'), (15, 20, '15-20x'), (20, 100, '20x+')]:
        subset = [t for t in trades if lo <= t['spike_x'] < hi]
        if not subset or len(subset) < 3:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        print(f'  {label:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}')

    # ── 3. VOLATILITY ANALYSIS ──
    print(f'\n  ── VOLATILITY ANALYSIS ──')
    print(f'  {"Vol State":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}  {"Action":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}  {"-"*10}')

    for bucket in ['low', 'normal', 'high']:
        subset = [t for t in trades if t['vol_bucket'] == bucket]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        action = 'KEEP' if swr > 55 else ('AVOID' if swr < 45 else 'NEUTRAL')
        print(f'  {bucket:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}  {action:>10}')

    # ── 4. SESSION / TIME ANALYSIS ──
    print(f'\n  ── SESSION ANALYSIS ──')
    print(f'  {"Session":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}')

    for session in ['asian', 'london', 'ny']:
        subset = [t for t in trades if t['session'] == session]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        print(f'  {session:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}')

    # Hourly breakdown
    print(f'\n  {"Hour(UTC)":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}')
    for h in sorted(set(t['hour'] for t in trades)):
        subset = [t for t in trades if t['hour'] == h]
        if len(subset) < 2:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        marker = ' <-- BEST' if swr > 70 else (' <-- WORST' if swr < 40 else '')
        print(f'  {h:02d}:00{" "*13}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}{marker}')

    # ── 5. EXIT REASON ANALYSIS ──
    print(f'\n  ── EXIT REASON ANALYSIS ──')
    print(f'  {"Reason":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}  {"TotalPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}  {"-"*10}')

    for reason in set(t['exit_reason'] for t in trades):
        subset = [t for t in trades if t['exit_reason'] == reason]
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        tot_pnl = sum(t['pnl'] for t in subset)
        print(f'  {reason:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}  ${tot_pnl:>+9.2f}')

    # ── 6. BARS HELD ANALYSIS ──
    print(f'\n  ── BARS HELD ANALYSIS ──')
    print(f'  {"Bars Held":>18}  {"Trades":>7}  {"WR":>6}  {"AvgPnL":>10}')
    print(f'  {"-"*18}  {"-"*7}  {"-"*6}  {"-"*10}')

    for lo, hi, label in [(1, 3, '1-2'), (3, 5, '3-4'), (5, 8, '5-7'),
                           (8, 12, '8-11'), (12, 100, '12+')]:
        subset = [t for t in trades if lo <= t['bars_held'] < hi]
        if not subset:
            continue
        sw = sum(1 for t in subset if t['won'])
        swr = sw / len(subset) * 100
        avg_pnl = np.mean([t['pnl'] for t in subset])
        print(f'  {label:>18}  {len(subset):>7}  {swr:>5.1f}%  ${avg_pnl:>+9.2f}')

    # ── 7. COMBINED FILTER OPPORTUNITIES ──
    print(f'\n  ── FILTER OPPORTUNITIES (combinations) ──')
    print(f'  Filter combinations that lose money:')
    print()

    filters = [
        ('drift_against + strong_drift', lambda t: t['drift_vs_fade'] == 'against' and t['drift_bucket'] == 'strong'),
        ('drift_against + any_drift', lambda t: t['drift_vs_fade'] == 'against'),
        ('spike > 10x', lambda t: t['spike_x'] > 10),
        ('spike > 15x', lambda t: t['spike_x'] > 15),
        ('spike > 10x + drift_against', lambda t: t['spike_x'] > 10 and t['drift_vs_fade'] == 'against'),
        ('low_vol + drift_against', lambda t: t['vol_bucket'] == 'low' and t['drift_vs_fade'] == 'against'),
        ('high_vol + spike > 10x', lambda t: t['vol_bucket'] == 'high' and t['spike_x'] > 10),
        ('bars_held > 8 + timeout', lambda t: t['bars_held'] > 8 and t['exit_reason'] == 'timeout'),
        ('ny_session + drift_against', lambda t: t['session'] == 'ny' and t['drift_vs_fade'] == 'against'),
    ]

    print(f'  {"Filter":>40}  {"Blocked":>7}  {"Saved$":>8}  {"WR drop":>8}')
    print(f'  {"-"*40}  {"-"*7}  {"-"*8}  {"-"*8}')

    baseline_wr = wr
    for name, fn in filters:
        blocked = [t for t in trades if fn(t)]
        blocked_pnl = sum(t['pnl'] for t in blocked)
        remaining = [t for t in trades if not fn(t)]
        if remaining:
            remaining_wr = sum(1 for t in remaining if t['won']) / len(remaining) * 100
        else:
            remaining_wr = 0
        wr_change = remaining_wr - baseline_wr
        if blocked:
            print(f'  {name:>40}  {len(blocked):>7}  ${blocked_pnl:>+7.2f}  {wr_change:>+7.1f}%')

    # ── 8. WORST TRADES ANALYSIS ──
    print(f'\n  ── WORST 10 TRADES ──')
    worst = sorted(trades, key=lambda t: t['pnl'])[:10]
    for t in worst:
        print(f'  #{t["trade_id"]:>4}  PnL=${t["pnl"]:>+8.2f}  spike={t["spike_x"]:.1f}x  '
              f'drift={t["drift_vs_fade"]}({t["drift_strength"]:.4f})  '
              f'vol={t["vol_bucket"]}  exit={t["exit_reason"]}  bars={t["bars_held"]}')

    # ── 9. BEST TRADES ANALYSIS ──
    print(f'\n  ── BEST 10 TRADES ──')
    best = sorted(trades, key=lambda t: t['pnl'], reverse=True)[:10]
    for t in best:
        print(f'  #{t["trade_id"]:>4}  PnL=${t["pnl"]:>+8.2f}  spike={t["spike_x"]:.1f}x  '
              f'drift={t["drift_vs_fade"]}({t["drift_strength"]:.4f})  '
              f'vol={t["vol_bucket"]}  exit={t["exit_reason"]}  bars={t["bars_held"]}')

    return trades


def save_to_csv(trades: list[dict], symbol: str):
    """Save all enriched trades to CSV."""
    os.makedirs('logs/trades', exist_ok=True)
    filename = f'logs/trades/backtest_{symbol}_enriched.csv'
    if trades:
        with open(filename, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=trades[0].keys())
            writer.writeheader()
            writer.writerows(trades)
        print(f'\n  Saved {len(trades)} trades to {filename}')


def main():
    symbols = ['BOOM500', 'CRASH500']

    all_trades = {}
    for sym in symbols:
        print(f'\n  Running backtest on {sym}...')
        trades = run_backtest_with_full_logging(sym, max_bars=5000)
        print(f'  Got {len(trades)} trades')
        all_trades[sym] = trades
        analyze_patterns(trades, sym)
        save_to_csv(trades, sym)

    # Cross-symbol comparison
    print(f'\n{"="*78}')
    print(f'  CROSS-SYMBOL COMPARISON')
    print(f'{"="*78}')

    for metric in ['drift_vs_fade', 'spike_bucket', 'vol_bucket', 'session']:
        print(f'\n  By {metric}:')
        for sym in symbols:
            trades = all_trades[sym]
            groups = defaultdict(list)
            for t in trades:
                groups[t[metric]].append(t)
            print(f'    {sym}:')
            for g, subset in sorted(groups.items()):
                sw = sum(1 for t in subset if t['won'])
                swr = sw / len(subset) * 100
                tot = sum(t['pnl'] for t in subset)
                print(f'      {g:>15}: {len(subset):>4} trades, WR={swr:.1f}%, PnL=${tot:+.2f}')


if __name__ == '__main__':
    main()
