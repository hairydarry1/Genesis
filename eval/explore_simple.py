"""
SIMPLE EXPLORATION: What actually makes money?
"""

import numpy as np
import pandas as pd
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

def load_data(symbol: str) -> pd.DataFrame:
    symbol_lower = symbol.lower()
    filename = f'data/candles_{symbol_lower}_1min.parquet'
    if Path(filename).exists():
        return pd.read_parquet(filename)
    return None

def detect_spikes(prices: np.ndarray, threshold_multiplier: float = 3.0):
    changes = np.abs(np.diff(prices))
    positive_changes = changes[changes > 0]
    median = np.median(positive_changes)
    threshold = median * threshold_multiplier
    spike_mask = changes > threshold
    return np.where(spike_mask)[0], threshold, median

def calculate_cold_hours(spike_indices: np.ndarray, n_hours: int = 24):
    hours = spike_indices // 60
    hour_counts = np.zeros(n_hours)
    for h in hours:
        hour_counts[h % n_hours] += 1
    cold_threshold = np.percentile(hour_counts, 25)
    cold_hours = np.where(hour_counts <= cold_threshold)[0]
    return cold_hours

def backtest_all_approaches(symbol: str):
    """Test ALL approaches with simple logic"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours = calculate_cold_hours(spike_indices)
    
    print(f"\n{'='*70}")
    print(f"ALL APPROACHES: {symbol}")
    print(f"{'='*70}")
    print(f"Spikes: {len(spike_indices)}, Median: {median:.5f}")
    
    results = []
    
    # APPROACH 1: Fade spike (bet against)
    print(f"\n--- Approach 1: FADE SPIKE ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        # Determine spike direction
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        
        # Enter OPPOSITE to spike (fade)
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        
        # Exit after 5 bars
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        # PnL (opposite direction)
        pnl = -spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Fade', len(trades), wr, total_pnl))
    
    # APPROACH 2: Follow spike (bet with)
    print(f"\n--- Approach 2: FOLLOW SPIKE ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        # Determine spike direction
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        
        # Enter SAME direction as spike
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        
        # Exit after 5 bars
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        # PnL (same direction)
        pnl = spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Follow', len(trades), wr, total_pnl))
    
    # APPROACH 3: COLD hours fade
    print(f"\n--- Approach 3: COLD FADE ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        # Check COLD hour
        hour = spike_idx // 60 % 24
        if hour not in cold_hours:
            continue
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = -spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('COLD Fade', len(trades), wr, total_pnl))
    
    # APPROACH 4: COLD hours follow
    print(f"\n--- Approach 4: COLD FOLLOW ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        hour = spike_idx // 60 % 24
        if hour not in cold_hours:
            continue
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('COLD Follow', len(trades), wr, total_pnl))
    
    # APPROACH 5: Fade large spikes only
    print(f"\n--- Approach 5: FADE LARGE SPIKES ---")
    spike_sizes = []
    for spike_idx in spike_indices:
        if spike_idx + 1 < len(prices):
            size = abs(prices[spike_idx + 1] - prices[spike_idx])
            spike_sizes.append(size)
    
    p67 = np.percentile(spike_sizes, 67)
    
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        spike_size = abs(prices[spike_idx + 1] - prices[spike_idx])
        if spike_size < p67:
            continue  # Skip small spikes
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = -spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Fade Large', len(trades), wr, total_pnl))
    
    # APPROACH 6: Follow large spikes only
    print(f"\n--- Approach 6: FOLLOW LARGE SPIKES ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 2:
            continue
        
        spike_size = abs(prices[spike_idx + 1] - prices[spike_idx])
        if spike_size < p67:
            continue
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 1
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Follow Large', len(trades), wr, total_pnl))
    
    # APPROACH 7: Wait 3 bars then fade
    print(f"\n--- Approach 7: WAIT 3 + FADE ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 5:
            continue
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 3  # Wait 3 bars
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = -spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Wait3+Fade', len(trades), wr, total_pnl))
    
    # APPROACH 8: Wait 3 bars then follow
    print(f"\n--- Approach 8: WAIT 3 + FOLLOW ---")
    trades = []
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        if next_spike - spike_idx < 5:
            continue
        
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        entry_bar = spike_idx + 3
        entry_price = prices[entry_bar]
        exit_bar = min(entry_bar + 5, len(prices) - 1)
        exit_price = prices[exit_bar]
        
        pnl = spike_dir * (exit_price - entry_price)
        trades.append(pnl)
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        wr = wins / len(trades) * 100
        total_pnl = sum(trades)
        print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:.2f}")
        results.append(('Wait3+Follow', len(trades), wr, total_pnl))
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY: {symbol}")
    print(f"{'='*70}")
    
    print(f"\n{'Approach':<20} {'Trades':<10} {'WR%':<10} {'PnL':<12}")
    print("-" * 55)
    
    for name, trades_count, wr, pnl in results:
        print(f"{name:<20} {trades_count:<10} {wr:<10.1f} {pnl:<12.2f}")
    
    # Find best
    if results:
        best = max(results, key=lambda x: x[3])
        print(f"\nBest: {best[0]} (WR={best[2]:.1f}%, PnL={best[3]:.2f})")
    
    return results

def main():
    """Run on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = backtest_all_approaches(symbol)
        all_results[symbol] = results
    
    # Overall summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY: ALL SYMBOLS")
    print(f"{'='*70}")
    
    for symbol in symbols:
        if all_results[symbol]:
            best = max(all_results[symbol], key=lambda x: x[3])
            print(f"{symbol}: Best = {best[0]} (WR={best[2]:.1f}%, PnL={best[3]:.2f})")

if __name__ == '__main__':
    main()
