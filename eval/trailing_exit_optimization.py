#!/usr/bin/env python3
"""
TRAILING EXIT OPTIMIZATION
==========================
Test different trailing exit configurations to find the optimal settings.
"""

import numpy as np
import pandas as pd
import os

DATA_DIR = "data"

def load_ticks(symbol):
    file_map = {
        'BOOM_1000': 'ticks_boom1000_index.npy',
        'CRASH_1000': 'ticks_crash1000_index.npy',
        'BOOM_500': 'ticks_boom_500_index.npy',
        'CRASH_500': 'ticks_crash_500_index.npy',
    }
    fname = file_map.get(symbol)
    if fname is None:
        return None
    fpath = os.path.join(DATA_DIR, fname)
    if not os.path.exists(fpath):
        return None
    ticks = np.load(fpath)
    data = ticks.flatten().tolist()
    return data[:500000] if len(data) > 500000 else data

def find_spikes(prices, threshold=3.0):
    """Find spikes in price data"""
    price_arr = np.array(prices)
    moves = np.abs(np.diff(price_arr))
    s = pd.Series(moves)
    rolling_median = s.rolling(window=20, min_periods=1).median().values
    median_ranges = np.full(len(prices), 0.01)
    median_ranges[1:] = rolling_median
    
    spikes = []
    for i in range(1, len(prices) - 20):
        if median_ranges[i] > 0 and moves[i-1] > threshold * median_ranges[i]:
            if prices[i] > prices[i-1]:
                spikes.append(('boom', i, prices[i], median_ranges[i]))
            else:
                spikes.append(('crash', i, prices[i], median_ranges[i]))
    return spikes

def test_trailing_exit(prices, spikes, trail_pct, max_hold, use_ma=False):
    """
    Test trailing exit strategy.
    
    trail_pct: trail at X% of max profit (e.g. 0.5 = trail at 50% of max)
    max_hold: maximum bars to hold
    use_ma: whether to use MA50 filter
    """
    wins = 0
    losses = 0
    total_pnl = 0
    trade_pnls = []
    max_consec = 0
    streak = 0
    
    for spike_type, spike_idx, spike_price, median_range in spikes:
        # MA filter
        if use_ma:
            if spike_idx < 50:
                continue
            ma50 = np.mean(prices[spike_idx-50:spike_idx])
            if spike_type == 'crash' and prices[spike_idx] <= ma50:
                continue
            if spike_type == 'boom' and prices[spike_idx] >= ma50:
                continue
        
        # Entry
        entry_idx = spike_idx + 1
        if entry_idx >= len(prices):
            continue
        entry_price = prices[entry_idx]
        
        # Trailing exit
        best_pnl = 0
        best_exit_price = None
        exit_price = None
        
        for j in range(entry_idx + 1, min(entry_idx + max_hold + 1, len(prices))):
            if spike_type == 'crash':
                pnl = entry_price - prices[j]
            else:
                pnl = prices[j] - entry_price
            
            if pnl > best_pnl:
                best_pnl = pnl
            
            # Trail check: exit if profit drops below trail_pct of max
            if best_pnl > 0 and pnl < best_pnl * trail_pct:
                exit_price = prices[j]
                break
        
        # If no trailing stop hit, exit at max_hold
        if exit_price is None:
            exit_idx = min(entry_idx + max_hold, len(prices) - 1)
            exit_price = prices[exit_idx]
        
        # Calculate PnL
        if spike_type == 'crash':
            pnl = entry_price - exit_price
        else:
            pnl = exit_price - entry_price
        
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
        return None
    
    wr = wins / total * 100
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
    win_sum = sum(p for p in trade_pnls if p > 0)
    loss_sum = abs(sum(p for p in trade_pnls if p <= 0))
    pf = win_sum / loss_sum if loss_sum > 0 else 999
    
    return {
        'trades': total,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'pf': pf,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': total_pnl,
        'max_consec': max_consec
    }

# Test parameters
trail_pcts = [0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]  # 1.0 = no trail (fixed exit)
max_holds = [3, 5, 7, 10]
ma_options = [False, True]

print("=" * 100)
print("TRAILING EXIT OPTIMIZATION")
print("=" * 100)

for symbol in ["BOOM_1000", "CRASH_1000"]:
    prices = load_ticks(symbol)
    if prices is None:
        print(f"\n{symbol}: No data")
        continue
    
    spikes = find_spikes(prices)
    print(f"\n{'='*100}")
    print(f"{symbol} - {len(spikes)} spikes found")
    print(f"{'='*100}")
    
    best_result = None
    best_params = None
    
    print(f"\n{'Trail%':>8} {'MaxHold':>8} {'MA':>5} {'Trades':>8} {'WR%':>8} {'PF':>8} {'AvgWin':>10} {'AvgLoss':>10} {'PnL':>12} {'MaxCL':>7}")
    print("-" * 100)
    
    for trail_pct in trail_pcts:
        for max_hold in max_holds:
            for use_ma in ma_options:
                result = test_trailing_exit(prices, spikes, trail_pct, max_hold, use_ma)
                if result is None:
                    continue
                
                # Filter: need at least 50 trades
                if result['trades'] < 50:
                    continue
                
                ma_str = "YES" if use_ma else "NO"
                print(f"{trail_pct:>8.1f} {max_hold:>8} {ma_str:>5} {result['trades']:>8} {result['wr']:>7.1f}% {result['pf']:>8.2f} {result['avg_win']:>10.4f} {result['avg_loss']:>10.4f} {result['total_pnl']:>12.2f} {result['max_consec']:>7}")
                
                # Score: PF * sqrt(trades) (balance quality and quantity)
                score = result['pf'] * np.sqrt(result['trades'])
                if best_result is None or score > best_result.get('score', 0):
                    best_result = result.copy()
                    best_result['score'] = score
                    best_params = (trail_pct, max_hold, use_ma)
    
    if best_params:
        print(f"\n{'='*100}")
        print(f"BEST: Trail={best_params[0]}, Hold={best_params[1]}, MA={'YES' if best_params[2] else 'NO'}")
        print(f"  Trades: {best_result['trades']} | WR: {best_result['wr']:.1f}% | PF: {best_result['pf']:.2f}")
        print(f"  Avg Win: {best_result['avg_win']:.4f} | Avg Loss: {best_result['avg_loss']:.4f}")
        print(f"  Total PnL: {best_result['total_pnl']:.2f} | Max Consec Loss: {best_result['max_consec']}")
        print(f"{'='*100}")

print("\n" + "=" * 100)
print("OPTIMIZATION COMPLETE")
print("=" * 100)
