#!/usr/bin/env python3
"""
DEEP STRATEGY RESEARCH
======================
Test multiple strategy approaches:
1. MA filter (100% WR finding from fresh_eyes.py)
2. Drift-following (trade WITH the algorithm)
3. Spike size filtering (only trade big/small spikes)
4. Time-based filtering (specific hours)
5. Volatility regime detection
6. Combined strategies
"""

import numpy as np
import pandas as pd
import os
import statistics

# Load tick data
DATA_DIR = "data"

def load_ticks(symbol):
    """Load tick data from NPY files"""
    # Map symbol names to file names
    file_map = {
        'BOOM_1000': 'ticks_boom1000_index.npy',
        'CRASH_1000': 'ticks_crash1000_index.npy',
        'BOOM_500': 'ticks_boom_500_index.npy',
        'CRASH_500': 'ticks_crash_500_index.npy',
        'BOOM_300': 'ticks_boom300_index.npy',
        'CRASH_300': 'ticks_crash300_index.npy',
    }
    fname = file_map.get(symbol)
    if fname is None:
        return None
    fpath = os.path.join(DATA_DIR, fname)
    if not os.path.exists(fpath):
        return None
    ticks = np.load(fpath)
    # Use first 500K ticks for speed
    data = ticks.flatten().tolist()
    return data[:500000] if len(data) > 500000 else data

def analyze_strategy(prices, strategy_name, entry_func, exit_func, hold_bars=3):
    """Generic strategy backtester"""
    wins = 0
    losses = 0
    total_pnl = 0
    trade_pnls = []
    max_consecutive_losses = 0
    current_streak = 0
    
    # Find spikes using numpy for speed
    price_arr = np.array(prices)
    moves = np.abs(np.diff(price_arr))
    
    # Rolling median of moves (window=20) using pandas for speed
    window = 20
    s = pd.Series(moves)
    rolling_median = s.rolling(window=window, min_periods=1).median().values
    median_ranges = np.full(len(prices), 0.01)
    median_ranges[1:] = rolling_median  # offset by 1 since moves is diff
    
    # Find spikes (move > 3x median)
    spikes = []
    for i in range(1, len(prices)-hold_bars-10):
        if median_ranges[i] > 0 and moves[i-1] > 3.0 * median_ranges[i]:
            if prices[i] > prices[i-1]:  # BOOM spike (up)
                spikes.append(('boom', i, prices[i], median_ranges[i]))
            else:  # CRASH spike (down)
                spikes.append(('crash', i, prices[i], median_ranges[i]))
    
    # Backtest each spike
    for spike_type, spike_idx, spike_price, median_range in spikes:
        # Entry
        entry_price = entry_func(prices, spike_idx, spike_type, median_range)
        if entry_price is None:
            continue
        
        # Exit
        exit_price, exit_bar = exit_func(prices, spike_idx, entry_price, spike_type, median_range, hold_bars)
        if exit_price is None:
            continue
        
        pnl = entry_price - exit_price if spike_type == 'crash' else exit_price - entry_price
        trade_pnls.append(pnl)
        
        if pnl > 0:
            wins += 1
            current_streak = 0
        else:
            losses += 1
            current_streak += 1
            max_consecutive_losses = max(max_consecutive_losses, current_streak)
        
        total_pnl += pnl
    
    total = wins + losses
    if total == 0:
        return None
    
    wr = wins / total * 100
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
    profit_factor = abs(sum(p for p in trade_pnls if p > 0)) / abs(sum(p for p in trade_pnls if p <= 0)) if losses > 0 and sum(p for p in trade_pnls if p <= 0) != 0 else 999
    
    return {
        'name': strategy_name,
        'trades': total,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': total_pnl,
        'profit_factor': profit_factor,
        'max_consec_loss': max_consecutive_losses,
        'expectancy': total_pnl / total if total > 0 else 0
    }

def strategy_basic_fade(prices, spike_idx, spike_type, median_range):
    """Basic fade: enter 1 bar after spike"""
    if spike_idx + 1 >= len(prices):
        return None
    return prices[spike_idx + 1]

def strategy_basic_exit(prices, spike_idx, entry_price, spike_type, median_range, hold_bars):
    """Basic exit: close after hold_bars"""
    exit_idx = spike_idx + 1 + hold_bars
    if exit_idx >= len(prices):
        return None, None
    return prices[exit_idx], exit_idx

def strategy_ma_filter_entry(prices, spike_idx, spike_type, median_range):
    """MA filter: only enter if price > MA50"""
    if spike_idx < 50:
        return None
    ma50 = np.mean(prices[spike_idx-50:spike_idx])
    if spike_type == 'crash' and prices[spike_idx] > ma50:
        return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None
    elif spike_type == 'boom' and prices[spike_idx] < ma50:
        return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None
    return None

def strategy_spike_size_filter(prices, spike_idx, spike_type, median_range):
    """Only trade LARGE spikes (>5x median)"""
    move = abs(prices[spike_idx] - prices[spike_idx-1])
    if median_range > 0 and move > 5.0 * median_range:
        return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None
    return None

def strategy_drift_follow(prices, spike_idx, spike_type, median_range):
    """Trade WITH the drift after spike (not against it)"""
    if spike_idx + 1 >= len(prices):
        return None
    # For CRASH: drift is UP after spike, so go LONG
    # For BOOM: drift is DOWN after spike, so go SHORT
    return prices[spike_idx + 1]

def strategy_trailing_exit(prices, spike_idx, entry_price, spike_type, median_range, hold_bars):
    """Trailing exit: move to breakeven after 1 bar, then trail"""
    best_pnl = 0
    best_exit = None
    for i in range(spike_idx + 2, min(spike_idx + hold_bars + 3, len(prices))):
        if spike_type == 'crash':
            pnl = entry_price - prices[i]
        else:
            pnl = prices[i] - entry_price
        if pnl > best_pnl:
            best_pnl = pnl
            best_exit = (prices[i], i)
        elif best_pnl > 0 and pnl < best_pnl * 0.5:
            # Trailing stop: exit if we've given back 50% of max profit
            return best_exit
    if best_exit:
        return best_exit
    return prices[min(spike_idx + hold_bars + 2, len(prices)-1)], min(spike_idx + hold_bars + 2, len(prices)-1)

def strategy_cluster_avoid(prices, spike_idx, spike_type, median_range):
    """Skip if spike is within 3 bars of another spike"""
    # Check if there's a spike within 3 bars before
    for j in range(max(0, spike_idx-5), spike_idx):
        if j > 0:
            move = abs(prices[j] - prices[j-1])
            if median_range > 0 and move > 3.0 * median_range:
                return None  # Skip - too close to previous spike
    return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None

def strategy_volatility_regime(prices, spike_idx, spike_type, median_range):
    """Only enter when volatility is LOW (small recent ranges)"""
    if spike_idx < 30:
        return None
    recent_ranges = [abs(prices[j] - prices[j-1]) for j in range(spike_idx-30, spike_idx)]
    avg_range = np.mean(recent_ranges)
    # Only enter if recent volatility is below average
    if avg_range < 0.015:  # Low vol threshold
        return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None
    return None

# Run all strategies
print("=" * 80)
print("DEEP STRATEGY RESEARCH")
print("=" * 80)

for symbol in ["BOOM_1000", "CRASH_1000"]:
    prices = load_ticks(symbol)
    if prices is None or len(prices) < 1000:
        print(f"\n{symbol}: No data available")
        continue
    
    print(f"\n{'='*80}")
    print(f"{symbol} ({len(prices)} ticks)")
    print(f"{'='*80}")
    
    # Strategy 1: Basic fade
    result = analyze_strategy(prices, "Basic Fade (delay=1, hold=3)", 
                            strategy_basic_fade, strategy_basic_exit, hold_bars=3)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")
    
    # Strategy 2: MA filter
    result = analyze_strategy(prices, "MA50 Filter + Fade",
                            strategy_ma_filter_entry, strategy_basic_exit, hold_bars=3)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")
    
    # Strategy 3: Spike size filter
    result = analyze_strategy(prices, "Large Spike Only (>5x)",
                            strategy_spike_size_filter, strategy_basic_exit, hold_bars=3)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")
    
    # Strategy 4: Trailing exit
    result = analyze_strategy(prices, "Fade + Trailing Exit",
                            strategy_basic_fade, strategy_trailing_exit, hold_bars=5)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")
    
    # Strategy 5: Cluster avoid
    result = analyze_strategy(prices, "Fade + Cluster Avoid",
                            strategy_cluster_avoid, strategy_basic_exit, hold_bars=3)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")
    
    # Strategy 6: Combined (MA + Cluster + Large spike)
    def combined_entry(prices, spike_idx, spike_type, median_range):
        # Check MA filter
        if spike_idx < 50:
            return None
        ma50 = np.mean(prices[spike_idx-50:spike_idx])
        if spike_type == 'crash' and prices[spike_idx] <= ma50:
            return None
        if spike_type == 'boom' and prices[spike_idx] >= ma50:
            return None
        
        # Check cluster
        for j in range(max(0, spike_idx-5), spike_idx):
            if j > 0:
                move = abs(prices[j] - prices[j-1])
                if median_range > 0 and move > 3.0 * median_range:
                    return None
        
        # Check spike size
        move = abs(prices[spike_idx] - prices[spike_idx-1])
        if median_range > 0 and move < 4.0 * median_range:
            return None
        
        return prices[spike_idx + 1] if spike_idx + 1 < len(prices) else None
    
    result = analyze_strategy(prices, "COMBINED: MA + Cluster + Large Spike + Trailing",
                            combined_entry, strategy_trailing_exit, hold_bars=5)
    if result:
        print(f"\n  {result['name']}:")
        print(f"    Trades: {result['trades']} | WR: {result['wr']:.1f}% | PF: {result['profit_factor']:.2f}")
        print(f"    Avg Win: {result['avg_win']:.4f} | Avg Loss: {result['avg_loss']:.4f}")
        print(f"    Total PnL: {result['total_pnl']:.4f} | Max Consec Loss: {result['max_consec_loss']}")

print("\n" + "=" * 80)
print("RESEARCH COMPLETE")
print("=" * 80)
