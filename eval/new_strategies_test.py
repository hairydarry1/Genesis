#!/usr/bin/env python3
"""
NEW STRATEGY DEVELOPMENT
========================
Genuinely new approaches, not just fade variations:

1. DRIFT-FOLLOWING: Trade WITH the algorithm after spike
2. MULTI-TIMEFRAME: Use M5/M15 to filter M1 entries  
3. SPIKE-SIZE REGIME: Different strategies for small vs large spikes
4. REVERSAL PATTERN: Wait for price to stabilize, then enter
5. MOMENTUM EXHAUSTION: Enter when momentum dies after spike
6. GRID STRATEGY: Set buy/sell limits around spike price
7. SPIKE CLUSTERING: Exploit predictable cluster patterns
"""

import numpy as np
import pandas as pd
import os

DATA_DIR = "data"

def load_ticks(symbol):
    file_map = {
        'BOOM_1000': 'ticks_boom1000_index.npy',
        'CRASH_1000': 'ticks_crash1000_index.npy',
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
    price_arr = np.array(prices)
    moves = np.abs(np.diff(price_arr))
    s = pd.Series(moves)
    rolling_median = s.rolling(window=20, min_periods=1).median().values
    median_ranges = np.full(len(prices), 0.01)
    median_ranges[1:] = rolling_median
    
    spikes = []
    for i in range(1, len(prices) - 30):
        if median_ranges[i] > 0 and moves[i-1] > threshold * median_ranges[i]:
            direction = 'boom' if prices[i] > prices[i-1] else 'crash'
            spike_size = abs(prices[i] - prices[i-1])
            spikes.append({
                'type': direction,
                'idx': i,
                'price': prices[i],
                'median': median_ranges[i],
                'size': spike_size,
                'ratio': spike_size / median_ranges[i]
            })
    return spikes

def backtest(prices, spikes, entry_func, exit_func, name):
    """Generic backtester"""
    wins = 0
    losses = 0
    total_pnl = 0
    trade_pnls = []
    max_consec = 0
    streak = 0
    
    for spike in spikes:
        result = entry_func(prices, spike)
        if result is None:
            continue
        
        entry_price, direction = result
        exit_price = exit_func(prices, spike, entry_price, direction)
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
        return None
    
    wr = wins / total * 100
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
    win_sum = sum(p for p in trade_pnls if p > 0)
    loss_sum = abs(sum(p for p in trade_pnls if p <= 0))
    pf = win_sum / loss_sum if loss_sum > 0 else 999
    
    return {
        'name': name,
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

# ============================================================
# STRATEGY 1: STANDARD FADE (baseline)
# ============================================================
def std_fade_entry(prices, spike):
    idx = spike['idx']
    if idx + 1 >= len(prices):
        return None
    # CRASH: buy (long) | BOOM: sell (short)
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return prices[idx + 1], direction

def std_fade_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    exit_idx = idx + 4  # Hold 3 bars
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 2: DRIFT-FOLLOWING (trade WITH the algorithm)
# ============================================================
def drift_follow_entry(prices, spike):
    """After spike, wait for drift to resume, then trade WITH it"""
    idx = spike['idx']
    if idx + 3 >= len(prices):
        return None
    
    # Wait 3 bars for drift to resume
    # CRASH drifts UP → go LONG after 3 bars
    # BOOM drifts DOWN → go SHORT after 3 bars
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return prices[idx + 3], direction

def drift_follow_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    # Hold for 5 bars to capture drift
    exit_idx = idx + 8
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 3: REVERSAL CONFIRMATION (wait for price to reverse)
# ============================================================
def reversal_entry(prices, spike):
    """Wait for price to actually start reversing before entering"""
    idx = spike['idx']
    if idx + 2 >= len(prices):
        return None
    
    # Check if price started reversing
    if spike['type'] == 'crash':
        # After crash spike, price should start going UP
        # Wait until we see an uptick
        for j in range(idx + 1, min(idx + 5, len(prices))):
            if prices[j] > prices[j-1]:
                direction = 'long'
                return prices[j], direction
    else:
        # After boom spike, price should start going DOWN
        for j in range(idx + 1, min(idx + 5, len(prices))):
            if prices[j] < prices[j-1]:
                direction = 'short'
                return prices[j], direction
    
    return None

def reversal_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    exit_idx = idx + 5  # Hold 3-4 bars after reversal confirmation
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 4: SPIKE-SIZE ADAPTIVE (different hold for different spikes)
# ============================================================
def size_adaptive_entry(prices, spike):
    idx = spike['idx']
    if idx + 1 >= len(prices):
        return None
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return prices[idx + 1], direction

def size_adaptive_exit(prices, spike, entry_price, direction):
    """Hold longer for larger spikes (they reverse more)"""
    idx = spike['idx']
    ratio = spike['ratio']
    
    # Small spike (3-5x): hold 2 bars
    # Medium spike (5-10x): hold 3 bars  
    # Large spike (10x+): hold 5 bars
    if ratio < 5:
        hold = 2
    elif ratio < 10:
        hold = 3
    else:
        hold = 5
    
    exit_idx = idx + 1 + hold
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 5: MOMENTUM EXHAUSTION (enter when momentum dies)
# ============================================================
def momentum_entry(prices, spike):
    """Enter when consecutive ticks against the spike stop"""
    idx = spike['idx']
    if idx + 3 >= len(prices):
        return None
    
    # Wait for momentum to exhaust (3 consecutive ticks in reversal direction)
    count = 0
    for j in range(idx + 1, min(idx + 8, len(prices))):
        if spike['type'] == 'crash':
            if prices[j] > prices[j-1]:
                count += 1
            else:
                count = 0
        else:
            if prices[j] < prices[j-1]:
                count += 1
            else:
                count = 0
        
        if count >= 3:
            direction = 'long' if spike['type'] == 'crash' else 'short'
            return prices[j], direction
    
    return None

def momentum_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    # Exit when momentum reverses again
    start = spike['idx'] + 4
    for j in range(start, min(start + 5, len(prices))):
        if direction == 'long' and prices[j] < prices[j-1]:
            return prices[j]
        elif direction == 'short' and prices[j] > prices[j-1]:
            return prices[j]
    
    # Timeout exit
    exit_idx = min(start + 5, len(prices) - 1)
    return prices[exit_idx]

# ============================================================
# STRATEGY 6: DOUBLE SPIKE PATTERN (wait for 2nd spike)
# ============================================================
def double_spike_entry(prices, spike):
    """Wait for a second spike, then fade it"""
    idx = spike['idx']
    if idx + 5 >= len(prices):
        return None
    
    # Look for a second spike within 5 bars
    for j in range(idx + 2, min(idx + 8, len(prices))):
        if j > 0:
            move = abs(prices[j] - prices[j-1])
            # Use a lower threshold for the second spike
            if move > 2.0 * spike['median']:
                direction = 'long' if spike['type'] == 'crash' else 'short'
                return prices[j + 1] if j + 1 < len(prices) else None, direction
    
    return None

def double_spike_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    exit_idx = idx + 8  # Hold 5 bars after second spike
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 7: SPIKE SIZE FILTER (only trade optimal size)
# ============================================================
def optimal_size_entry(prices, spike):
    """Only trade spikes between 4-8x (the sweet spot)"""
    idx = spike['idx']
    ratio = spike['ratio']
    
    # Sweet spot: 4-8x median
    if ratio < 4 or ratio > 8:
        return None
    
    if idx + 1 >= len(prices):
        return None
    
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return prices[idx + 1], direction

def optimal_size_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    exit_idx = idx + 4  # Hold 3 bars
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# ============================================================
# STRATEGY 8: COMBINED OPTIMAL (MA + size + reversal confirm)
# ============================================================
def combined_optimal_entry(prices, spike):
    """Combine the best filters"""
    idx = spike['idx']
    
    # Filter 1: Spike size 4-10x
    if spike['ratio'] < 4 or spike['ratio'] > 10:
        return None
    
    # Filter 2: MA50 filter (price above MA for crash, below for boom)
    if idx < 50:
        return None
    ma50 = np.mean(prices[idx-50:idx])
    if spike['type'] == 'crash' and prices[idx] <= ma50:
        return None
    if spike['type'] == 'boom' and prices[idx] >= ma50:
        return None
    
    # Filter 3: Wait for reversal confirmation
    if idx + 2 >= len(prices):
        return None
    
    found_reversal = False
    for j in range(idx + 1, min(idx + 4, len(prices))):
        if spike['type'] == 'crash' and prices[j] > prices[j-1]:
            found_reversal = True
            break
        if spike['type'] == 'boom' and prices[j] < prices[j-1]:
            found_reversal = True
            break
    
    if not found_reversal:
        return None
    
    direction = 'long' if spike['type'] == 'crash' else 'short'
    return prices[idx + 2], direction

def combined_optimal_exit(prices, spike, entry_price, direction):
    idx = spike['idx']
    exit_idx = idx + 5  # Hold 3 bars after entry
    if exit_idx >= len(prices):
        return None
    return prices[exit_idx]

# Run all strategies
print("=" * 100)
print("NEW STRATEGY DEVELOPMENT - BACKTEST RESULTS")
print("=" * 100)

strategies = [
    ("1. STANDARD FADE (baseline)", std_fade_entry, std_fade_exit),
    ("2. DRIFT-FOLLOWING", drift_follow_entry, drift_follow_exit),
    ("3. REVERSAL CONFIRMATION", reversal_entry, reversal_exit),
    ("4. SPIKE-SIZE ADAPTIVE", size_adaptive_entry, size_adaptive_exit),
    ("5. MOMENTUM EXHAUSTION", momentum_entry, momentum_exit),
    ("6. DOUBLE SPIKE PATTERN", double_spike_entry, double_spike_exit),
    ("7. OPTIMAL SIZE (4-8x)", optimal_size_entry, optimal_size_exit),
    ("8. COMBINED OPTIMAL", combined_optimal_entry, combined_optimal_exit),
]

for symbol in ["BOOM_1000", "CRASH_1000"]:
    prices = load_ticks(symbol)
    if prices is None:
        print(f"\n{symbol}: No data")
        continue
    
    spikes = find_spikes(prices)
    print(f"\n{'='*100}")
    print(f"{symbol} - {len(spikes)} spikes")
    print(f"{'='*100}")
    
    print(f"\n{'Strategy':<35} {'Trades':>8} {'WR%':>8} {'PF':>8} {'AvgWin':>10} {'AvgLoss':>10} {'PnL':>12} {'MaxCL':>7}")
    print("-" * 100)
    
    for name, entry_func, exit_func in strategies:
        result = backtest(prices, spikes, entry_func, exit_func, name)
        if result is None:
            print(f"  {name:<35} {'NO TRADES':>8}")
            continue
        
        print(f"  {name:<35} {result['trades']:>8} {result['wr']:>7.1f}% {result['pf']:>8.2f} {result['avg_win']:>10.4f} {result['avg_loss']:>10.4f} {result['total_pnl']:>12.2f} {result['max_consec']:>7}")

print("\n" + "=" * 100)
print("ANALYSIS COMPLETE")
print("=" * 100)
