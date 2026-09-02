#!/usr/bin/env python3
"""
M1 BAR STRATEGY BACKTEST
=========================
Test strategies on actual M1 candle data (what the EA trades on).
This is the CORRECT timeframe — tick data was misleading.
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = "data"

def load_candles(symbol):
    fpath = os.path.join(DATA_DIR, f"candles_{symbol}_1min.parquet")
    if not os.path.exists(fpath):
        return None
    df = pd.read_parquet(fpath)
    return df

def find_bar_spikes(df, threshold=3.0):
    """Find spikes on M1 bars"""
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    
    ranges = highs - lows
    n = len(ranges)
    
    # Rolling median range
    window = 20
    median_ranges = np.full(n, 0.01)
    for i in range(window, n):
        median_ranges[i] = np.median(ranges[max(0,i-window):i])
    
    spikes = []
    for i in range(1, n-10):
        bar_range = ranges[i]
        if median_ranges[i] > 0 and bar_range > threshold * median_ranges[i]:
            is_boom = closes[i] > opens[i]
            is_crash = closes[i] < opens[i]
            
            if is_boom or is_crash:
                spikes.append({
                    'type': 'boom' if is_boom else 'crash',
                    'idx': i,
                    'range': bar_range,
                    'median': median_ranges[i],
                    'ratio': bar_range / median_ranges[i],
                    'close': closes[i],
                    'open': opens[i]
                })
    
    return spikes

def backtest_m1(df, spikes, strategies):
    """Backtest all strategies on M1 data"""
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    results = {}
    
    for name, entry_fn, exit_fn, hold_bars in strategies:
        wins = 0
        losses = 0
        total_pnl = 0
        trade_pnls = []
        max_consec = 0
        streak = 0
        
        for spike in spikes:
            idx = spike['idx']
            if idx + hold_bars + 1 >= len(closes):
                continue
            
            entry_result = entry_fn(closes, highs, lows, idx, spike)
            if entry_result is None:
                continue
            
            entry_price, direction = entry_result
            exit_price = exit_fn(closes, highs, lows, idx, entry_price, direction, hold_bars, spike)
            if exit_price is None:
                continue
            
            if direction == 'long':
                pnl = exit_price - entry_price
            else:
                pnl = entry_price - exit_price
            
            trade_pnls.append(pnl)
            total_pnl += pnl
            
            if pnl > 0:
                wins += 1
                streak = 0
            else:
                losses += 1
                streak += 1
                max_consec = max(max_consec, streak)
        
        total = wins + losses
        if total == 0:
            results[name] = None
            continue
        
        wr = wins / total * 100
        avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
        avg_loss = np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
        win_sum = sum(p for p in trade_pnls if p > 0)
        loss_sum = abs(sum(p for p in trade_pnls if p <= 0))
        pf = win_sum / loss_sum if loss_sum > 0 else 999
        
        results[name] = {
            'trades': total,
            'wins': wins,
            'losses': losses,
            'wr': wr,
            'pf': pf,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'total_pnl': total_pnl,
            'max_consec': max_consec,
            'expectancy': total_pnl / total
        }
    
    return results

# ============================================================
# STRATEGY DEFINITIONS
# ============================================================

def std_fade_entry(closes, highs, lows, idx, spike):
    """Enter at close of spike bar"""
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return closes[idx], direction

def std_fade_exit(closes, highs, lows, idx, entry_price, direction, hold, spike):
    """Exit after hold bars at close"""
    exit_idx = idx + hold
    if exit_idx >= len(closes):
        return None
    return closes[exit_idx]

def delayed_entry(closes, highs, lows, idx, spike):
    """Enter 1 bar AFTER spike"""
    if idx + 1 >= len(closes):
        return None
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return closes[idx + 1], direction

def delayed_exit(closes, highs, lows, idx, entry_price, direction, hold, spike):
    exit_idx = idx + 1 + hold
    if exit_idx >= len(closes):
        return None
    return closes[exit_idx]

def ma50_filter_entry(closes, highs, lows, idx, spike):
    """Only enter if MA50 confirms"""
    if idx < 50:
        return None
    ma50 = np.mean(closes[idx-50:idx])
    direction = 'long' if spike['type'] == 'crash' else 'short'
    if direction == 'long' and closes[idx] <= ma50:
        return None
    if direction == 'short' and closes[idx] >= ma50:
        return None
    return closes[idx], direction

def trailing_exit(closes, highs, lows, idx, entry_price, direction, hold, spike):
    """Exit when profit drops 50% from peak"""
    best = 0
    for j in range(idx + 1, min(idx + hold + 2, len(closes))):
        if direction == 'long':
            pnl = closes[j] - entry_price
        else:
            pnl = entry_price - closes[j]
        if pnl > best:
            best = pnl
        if best > 0 and pnl < best * 0.5:
            return closes[j]
    exit_idx = min(idx + hold + 1, len(closes) - 1)
    return closes[exit_idx]

def size_filter_entry(closes, highs, lows, idx, spike):
    """Only trade 5-10x spikes"""
    if spike['ratio'] < 5 or spike['ratio'] > 10:
        return None
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return closes[idx], direction

def reversal_confirm_entry(closes, highs, lows, idx, spike):
    """Wait for 1 bar of reversal before entering"""
    if idx + 1 >= len(closes):
        return None
    
    direction = 'long' if spike['type'] == 'crash' else 'short'
    
    # Check if next bar confirms reversal
    if direction == 'long' and closes[idx+1] > closes[idx]:
        return closes[idx+1], direction
    if direction == 'short' and closes[idx+1] < closes[idx]:
        return closes[idx+1], direction
    return None

def combined_best_entry(closes, highs, lows, idx, spike):
    """MA50 + size filter + reversal confirmation"""
    if idx < 50 or idx + 1 >= len(closes):
        return None
    
    # Size filter
    if spike['ratio'] < 5 or spike['ratio'] > 10:
        return None
    
    # MA50 filter
    ma50 = np.mean(closes[idx-50:idx])
    direction = 'long' if spike['type'] == 'crash' else 'short'
    if direction == 'long' and closes[idx] <= ma50:
        return None
    if direction == 'short' and closes[idx] >= ma50:
        return None
    
    # Reversal confirmation
    if direction == 'long' and closes[idx+1] <= closes[idx]:
        return None
    if direction == 'short' and closes[idx+1] >= closes[idx]:
        return None
    
    return closes[idx+1], direction

# Run backtests
print("=" * 100)
print("M1 BAR STRATEGY BACKTEST (CORRECT TIMEFRAME)")
print("=" * 100)

for sym in ['crash1000', 'boom1000']:
    df = load_candles(sym)
    if df is None:
        print(f"\n{sym}: No data")
        continue
    
    spikes = find_bar_spikes(df)
    print(f"\n{'='*100}")
    print(f"{sym.upper()} — {len(df)} bars, {len(spikes)} spikes")
    print(f"{'='*100}")
    
    strategies = [
        ("1. FADE immediate (bar0, hold=3)", std_fade_entry, std_fade_exit, 3),
        ("2. FADE immediate (bar0, hold=5)", std_fade_entry, std_fade_exit, 5),
        ("3. FADE delayed (bar1, hold=3)", delayed_entry, delayed_exit, 3),
        ("4. FADE delayed (bar1, hold=5)", delayed_entry, delayed_exit, 5),
        ("5. FADE delayed (bar1, hold=7)", delayed_entry, delayed_exit, 7),
        ("6. FADE delayed + MA50 (hold=3)", ma50_filter_entry, delayed_exit, 3),
        ("7. FADE delayed + MA50 (hold=5)", ma50_filter_entry, delayed_exit, 5),
        ("8. FADE delayed + TRAIL (hold=5)", delayed_entry, trailing_exit, 5),
        ("9. FADE delayed + SIZE 5-10x (hold=3)", size_filter_entry, delayed_exit, 3),
        ("10. FADE + REVERSAL CONFIRM (hold=3)", reversal_confirm_entry, delayed_exit, 3),
        ("11. COMBINED BEST (hold=3)", combined_best_entry, delayed_exit, 3),
        ("12. COMBINED BEST + TRAIL (hold=5)", combined_best_entry, trailing_exit, 5),
    ]
    
    print(f"\n{'Strategy':<45} {'Trades':>8} {'WR%':>8} {'PF':>8} {'AvgWin':>10} {'AvgLoss':>10} {'PnL':>12} {'MaxCL':>7}")
    print("-" * 110)
    
    results = backtest_m1(df, spikes, strategies)
    
    for name, _, _, _ in strategies:
        r = results.get(name)
        if r is None:
            print(f"  {name:<45} {'NO TRADES':>8}")
            continue
        print(f"  {name:<45} {r['trades']:>8} {r['wr']:>7.1f}% {r['pf']:>8.2f} {r['avg_win']:>10.4f} {r['avg_loss']:>10.4f} {r['total_pnl']:>12.2f} {r['max_consec']:>7}")

print("\n" + "=" * 100)
print("M1 BACKTEST COMPLETE")
print("=" * 100)
