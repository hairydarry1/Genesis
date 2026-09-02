"""
SPIKE CREATION MECHANICS - FAST VERSION
Focus on CRASH500 only for speed
"""

import numpy as np
import pandas as pd
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

def load_tick_data(symbol: str) -> np.ndarray:
    symbol_map = {
        'BOOM500': 'boom_500_index',
        'CRASH500': 'crash_500_index',
        'BOOM1000': 'boom1000_index',
        'CRASH1000': 'crash1000_index',
    }
    filename = f'data/ticks_{symbol_map[symbol]}.npy'
    if Path(filename).exists():
        return np.load(filename)
    return None

def detect_spikes(prices: np.ndarray, threshold_multiplier: float = 3.0):
    changes = np.abs(np.diff(prices))
    positive_changes = changes[changes > 0]
    median = np.median(positive_changes)
    threshold = median * threshold_multiplier
    spike_mask = changes > threshold
    return np.where(spike_mask)[0], threshold, median

def main():
    symbol = 'CRASH500'
    print(f"Analyzing {symbol}...")
    
    prices = load_tick_data(symbol)
    if prices is None:
        print("No data")
        return
    
    print(f"Loaded {len(prices)} ticks")
    
    spike_indices, threshold, median = detect_spikes(prices)
    print(f"Detected {len(spike_indices)} spikes")
    
    changes = np.diff(prices)
    abs_changes = np.abs(changes)
    
    # ANALYSIS 1: Pre-spike buildup
    print(f"\n{'='*60}")
    print(f"PRE-SPIKE BUILDUP")
    print(f"{'='*60}")
    
    for window in [5, 10, 20, 50]:
        consistencies = []
        size_trends = []
        max_streaks = []
        
        for spike_idx in spike_indices:
            if spike_idx < window + 5:
                continue
            
            pre_changes = changes[spike_idx-window:spike_idx]
            
            # Direction consistency (for CRASH, UP is drift direction)
            up_pct = np.mean(pre_changes > 0)
            consistencies.append(up_pct)
            
            # Tick size trend
            if len(pre_changes) >= 10:
                first_half = np.mean(np.abs(pre_changes[:len(pre_changes)//2]))
                second_half = np.mean(np.abs(pre_changes[len(pre_changes)//2:]))
                size_trends.append(second_half / first_half if first_half > 0 else 1.0)
            
            # Max streak
            max_streak = 1
            current_streak = 1
            for i in range(1, len(pre_changes)):
                if (pre_changes[i] > 0 and pre_changes[i-1] > 0) or \
                   (pre_changes[i] < 0 and pre_changes[i-1] < 0):
                    current_streak += 1
                    max_streak = max(max_streak, current_streak)
                else:
                    current_streak = 1
            max_streaks.append(max_streak)
        
        print(f"\n{window} ticks before spike:")
        print(f"  Direction consistency: {np.mean(consistencies):.1%}")
        print(f"  Tick size trend: {np.mean(size_trends):.3f}x")
        print(f"  Max streak: {np.mean(max_streaks):.1f}")
    
    # ANALYSIS 2: Spike injection
    print(f"\n{'='*60}")
    print(f"SPIKE INJECTION MECHANICS")
    print(f"{'='*60}")
    
    spike_mags = []
    first_tick_pcts = []
    
    for spike_idx in spike_indices:
        if spike_idx + 5 >= len(prices):
            continue
        
        spike_mag = abs_changes[spike_idx]
        post_ticks = abs_changes[spike_idx:spike_idx+5]
        
        if spike_mag > 0:
            first_tick_pct = post_ticks[0] / spike_mag
            first_tick_pcts.append(first_tick_pct)
        
        spike_mags.append(spike_mag)
    
    spike_mags = np.array(spike_mags)
    first_tick_pcts = np.array(first_tick_pcts)
    
    print(f"\nSpike Size:")
    print(f"  Mean: {spike_mags.mean():.6f}")
    print(f"  Median: {np.median(spike_mags):.6f}")
    print(f"  P25: {np.percentile(spike_mags, 25):.6f}")
    print(f"  P75: {np.percentile(spike_mags, 75):.6f}")
    print(f"  Max: {spike_mags.max():.6f}")
    
    print(f"\nSpike Shape (first tick % of total):")
    print(f"  Mean: {first_tick_pcts.mean():.1%}")
    print(f"  Median: {np.median(first_tick_pcts):.1%}")
    print(f"  Instant (>80%): {np.mean(first_tick_pcts > 0.8):.1%}")
    print(f"  Gradual (<50%): {np.mean(first_tick_pcts < 0.5):.1%}")
    
    # ANALYSIS 3: Tick size patterns
    print(f"\n{'='*60}")
    print(f"TICK SIZE PATTERNS")
    print(f"{'='*60}")
    
    # Check if large ticks cluster before spikes
    large_before_spike = 0
    large_random = 0
    
    for spike_idx in spike_indices:
        if spike_idx >= 5:
            pre_ticks = abs_changes[spike_idx-5:spike_idx]
            if np.any(pre_ticks >= median * 2):
                large_before_spike += 1
    
    random_indices = np.random.choice(range(100, len(abs_changes)-5), size=10000, replace=False)
    for idx in random_indices:
        pre_ticks = abs_changes[idx-5:idx]
        if np.any(pre_ticks >= median * 2):
            large_random += 1
    
    pre_rate = large_before_spike / len(spike_indices)
    rand_rate = large_random / len(random_indices)
    
    print(f"\nLarge tick (2x+ median) in 5 ticks before:")
    print(f"  Before spike: {pre_rate:.1%}")
    print(f"  Random: {rand_rate:.1%}")
    print(f"  Ratio: {pre_rate/rand_rate:.2f}x")
    
    if pre_rate > rand_rate * 1.3:
        print(f"  => LARGE TICKS CLUSTER before spikes!")
    
    # ANALYSIS 4: Retracement
    print(f"\n{'='*60}")
    print(f"RETRACEMENT MECHANICS")
    print(f"{'='*60}")
    
    for bars_after in [1, 2, 5, 10, 20]:
        retraces = []
        for idx in spike_indices:
            if idx + bars_after + 1 < len(prices):
                spike_move = prices[idx + 1] - prices[idx]
                future_move = prices[idx + bars_after + 1] - prices[idx + 1]
                
                if abs(spike_move) > 0:
                    retrace_pct = future_move / spike_move
                    retraces.append(retrace_pct)
        
        if retraces:
            print(f"  +{bars_after:2d} bars: {np.mean(retraces):+.1%} avg retrace")
    
    # Speed to 50% retrace
    retrace_speeds = []
    for idx in spike_indices:
        if idx + 20 < len(prices):
            spike_move = prices[idx + 1] - prices[idx]
            if abs(spike_move) < 0.001:
                continue
            
            for bars in range(1, 21):
                future_move = prices[idx + bars + 1] - prices[idx + 1]
                retrace_pct = future_move / spike_move
                if abs(retrace_pct) >= 0.5:
                    retrace_speeds.append(bars)
                    break
            else:
                retrace_speeds.append(20)
    
    print(f"\nSpeed to 50% retracement:")
    print(f"  Mean: {np.mean(retrace_speeds):.1f} bars")
    print(f"  Median: {np.median(retrace_speeds):.1f} bars")
    print(f"  <5 bars: {np.mean(np.array(retrace_speeds) < 5):.1%}")
    print(f"  Never (20+): {np.mean(np.array(retrace_speeds) >= 20):.1%}")

if __name__ == '__main__':
    main()
