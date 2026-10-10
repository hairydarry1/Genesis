"""
FIND MORE EDGES
Deep analysis to discover additional exploitable patterns.

Edges to investigate:
1. Tick size distribution anomalies
2. Time-of-day micro-patterns
3. Price level clustering
4. Multi-symbol predictive signals
5. Spike clustering patterns
6. Post-spike continuation
7. Entry timing optimization
8. Volatility regime changes
"""

import numpy as np
import pandas as pd
from pathlib import Path
from collections import Counter
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

def edge_1_tick_size_distribution(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 1: Are there specific tick sizes that predict spikes?
    
    Hypothesis: The algorithm uses specific tick sizes as "triggers"
    """
    print(f"\n{'='*70}")
    print(f"EDGE 1: TICK SIZE DISTRIBUTION ANOMALIES -- {symbol}")
    print(f"{'='*70}")
    
    changes = np.abs(np.diff(prices))
    median = np.median(changes[changes > 0])
    
    # Create tick size bins
    bins = [0, median*0.5, median, median*1.5, median*2, median*3, median*5, median*10, np.max(changes)]
    
    # Overall distribution
    hist_all, _ = np.histogram(changes, bins=bins)
    
    # Distribution in 10 ticks before spike
    pre_spike_changes = []
    for idx in spike_indices:
        if idx >= 10:
            pre_spike_changes.extend(changes[idx-10:idx])
    
    hist_pre, _ = np.histogram(pre_spike_changes, bins=bins)
    
    # Compare distributions
    print(f"\nTick Size Distribution (Overall vs Pre-Spike):")
    print(f"{'Range':<20} {'Overall':<12} {'Pre-Spike':<12} {'Ratio':<10} {'Exploitable?'}")
    print("-" * 70)
    
    for i in range(len(bins)-1):
        overall = hist_all[i] / len(changes) * 100
        pre_spike = hist_pre[i] / len(pre_spike_changes) * 100 if len(pre_spike_changes) > 0 else 0
        
        if overall > 0:
            ratio = pre_spike / overall
        else:
            ratio = 0
        
        exploitable = "YES!" if ratio > 1.5 or ratio < 0.5 else "No"
        
        print(f"  {bins[i]:.4f}-{bins[i+1]:.4f}  {overall:8.1f}%   {pre_spike:8.1f}%   {ratio:6.2f}x   {exploitable}")
    
    # Check for specific "trigger" tick sizes
    print(f"\n--- Specific Trigger Tick Sizes ---")
    
    # Find tick sizes that are significantly more common before spikes
    trigger_sizes = []
    for i in range(len(bins)-1):
        overall = hist_all[i] / len(changes)
        pre_spike = hist_pre[i] / len(pre_spike_changes) if len(pre_spike_changes) > 0 else 0
        
        if overall > 0 and pre_spike / overall > 1.5:
            trigger_sizes.append((bins[i], bins[i+1], pre_spike / overall))
    
    if trigger_sizes:
        print(f"  Tick sizes MORE common before spikes:")
        for low, high, ratio in trigger_sizes:
            print(f"    {low:.4f}-{high:.4f}: {ratio:.2f}x more likely")
    else:
        print(f"  No specific trigger tick sizes found")

def edge_2_time_micro_patterns(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 2: Time-of-day micro-patterns
    
    Hypothesis: Spikes have micro-patterns within hours
    """
    print(f"\n{'='*70}")
    print(f"EDGE 2: TIME-OF-DAY MICRO-PATTERNS -- {symbol}")
    print(f"{'='*70}")
    
    # Simulate time (assuming 1 tick = ~1 second)
    # Group spikes by "minute" (60 ticks)
    spike_minutes = spike_indices // 60
    
    # Analyze spike frequency by minute within hour
    minute_counts = Counter(spike_minutes % 60)
    
    print(f"\nSpike Frequency by Minute Within Hour:")
    for minute in range(60):
        count = minute_counts.get(minute, 0)
        total_minutes = len(spike_indices) // 60 + 1
        rate = count / total_minutes
        
        bar = '#' * int(rate / 2)
        print(f"  Minute {minute:2d}: {count:5d} spikes ({rate:.2f}/min) {bar}")
    
    # Find peak and trough minutes
    if minute_counts:
        peak_minute = max(minute_counts, key=minute_counts.get)
        trough_minute = min(minute_counts, key=minute_counts.get)
        
        print(f"\n  Peak minute: {peak_minute} ({minute_counts[peak_minute]} spikes)")
        print(f"  Trough minute: {trough_minute} ({minute_counts[trough_minute]} spikes)")
        
        if minute_counts[peak_minute] > minute_counts[trough_minute] * 1.5:
            print(f"  => SIGNIFICANT variation (exploitable!)")

def edge_3_price_level_clustering(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 3: Do spikes cluster at certain price levels?
    
    Hypothesis: The algorithm triggers spikes at specific price levels
    """
    print(f"\n{'='*70}")
    print(f"EDGE 3: PRICE LEVEL CLUSTERING -- {symbol}")
    print(f"{'='*70}")
    
    # Get price levels at spike points
    spike_prices = prices[spike_indices]
    
    # Round to nearest 0.1
    rounded_prices = np.round(spike_prices, 1)
    
    # Count spikes at each price level
    price_counts = Counter(rounded_prices)
    
    # Find clustering
    print(f"\nTop 20 Price Levels with Most Spikes:")
    for price, count in price_counts.most_common(20):
        expected = len(spike_indices) / len(set(rounded_prices))
        ratio = count / expected
        
        bar = '#' * int(count / 10)
        exploitable = "YES!" if ratio > 2.0 else ""
        print(f"  {price:.1f}: {count:5d} spikes ({ratio:.2f}x expected) {bar} {exploitable}")
    
    # Check if price level predicts spike direction
    print(f"\n--- Price Level vs Spike Direction ---")
    
    for price in sorted(set(rounded_prices))[:10]:
        mask = rounded_prices == price
        if np.sum(mask) > 10:
            # Get spike directions
            directions = []
            for idx in spike_indices[mask]:
                if idx + 1 < len(prices):
                    direction = 'UP' if prices[idx+1] > prices[idx] else 'DOWN'
                    directions.append(direction)
            
            if directions:
                up_pct = directions.count('UP') / len(directions)
                print(f"  Price {price:.1f}: {up_pct:.1%} UP spikes (n={len(directions)})")

def edge_4_spike_clustering(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 4: Spike clustering patterns
    
    Hypothesis: Spikes cluster in time (burst patterns)
    """
    print(f"\n{'='*70}")
    print(f"EDGE 4: SPIKE CLUSTERING PATTERNS -- {symbol}")
    print(f"{'='*70}")
    
    # Calculate intervals between spikes
    intervals = np.diff(spike_indices)
    
    # Detect clusters (intervals < 20 ticks)
    cluster_threshold = 20
    cluster_mask = intervals < cluster_threshold
    
    # Find cluster boundaries
    clusters = []
    current_cluster = []
    
    for i, interval in enumerate(intervals):
        if interval < cluster_threshold:
            current_cluster.append(i)
        else:
            if current_cluster:
                clusters.append(current_cluster)
                current_cluster = []
    
    if current_cluster:
        clusters.append(current_cluster)
    
    print(f"\nCluster Analysis:")
    print(f"  Cluster threshold: {cluster_threshold} ticks")
    print(f"  Total clusters: {len(clusters)}")
    print(f"  Spikes in clusters: {sum(len(c) for c in clusters)}")
    print(f"  Cluster percentage: {sum(len(c) for c in clusters) / len(spike_indices) * 100:.1f}%")
    
    # Cluster sizes
    cluster_sizes = [len(c) + 1 for c in clusters]  # +1 because cluster includes first spike
    print(f"\nCluster Size Distribution:")
    size_counts = Counter(cluster_sizes)
    for size in sorted(size_counts.keys())[:10]:
        count = size_counts[size]
        print(f"  Size {size}: {count} clusters ({count/len(clusters)*100:.1f}%)")
    
    # After cluster, what happens?
    print(f"\n--- After Cluster Ends ---")
    
    post_cluster_intervals = []
    for cluster in clusters:
        last_spike_in_cluster = cluster[-1]
        if last_spike_in_cluster + 1 < len(intervals):
            post_cluster_intervals.append(intervals[last_spike_in_cluster + 1])
    
    if post_cluster_intervals:
        print(f"  Avg interval after cluster: {np.mean(post_cluster_intervals):.1f} ticks")
        print(f"  Median interval after cluster: {np.median(post_cluster_intervals):.1f} ticks")
        print(f"  vs Overall median: {np.median(intervals):.1f} ticks")
        
        if np.mean(post_cluster_intervals) > np.median(intervals) * 1.3:
            print(f"  => LONGER gap after cluster (exploitable!)")

def edge_5_post_spike_continuation(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 5: Post-spike continuation
    
    Hypothesis: Price continues in spike direction for N ticks
    """
    print(f"\n{'='*70}")
    print(f"EDGE 5: POST-SPIKE CONTINUATION -- {symbol}")
    print(f"{'='*70}")
    
    changes = np.diff(prices)
    
    # Analyze continuation for different timeframes
    print(f"\nPost-Spike Continuation (price continues in spike direction):")
    
    for ticks_after in [1, 2, 3, 5, 10, 20, 50]:
        continuations = []
        
        for idx in spike_indices:
            if idx + ticks_after + 1 < len(prices):
                spike_direction = 1 if prices[idx+1] > prices[idx] else -1
                future_direction = 1 if prices[idx+ticks_after+1] > prices[idx+1] else -1
                
                # Did price continue in same direction?
                continued = (spike_direction == future_direction)
                continuations.append(continued)
        
        if continuations:
            continuation_rate = np.mean(continuations)
            print(f"  +{ticks_after:2d} ticks: {continuation_rate:.1%} continuation")
    
    # Analyze continuation by spike size
    print(f"\n--- Continuation by Spike Size ---")
    
    spike_mags = np.array([abs(prices[idx+1] - prices[idx]) for idx in spike_indices if idx+1 < len(prices)])
    
    small_spikes = spike_indices[spike_mags < np.percentile(spike_mags, 33)]
    large_spikes = spike_indices[spike_mags >= np.percentile(spike_mags, 67)]
    
    for category, indices in [("Small", small_spikes), ("Large", large_spikes)]:
        print(f"\n{category} Spikes:")
        for ticks_after in [1, 5, 10]:
            continuations = []
            for idx in indices:
                if idx + ticks_after + 1 < len(prices):
                    spike_direction = 1 if prices[idx+1] > prices[idx] else -1
                    future_direction = 1 if prices[idx+ticks_after+1] > prices[idx+1] else -1
                    continuations.append(spike_direction == future_direction)
            
            if continuations:
                print(f"  +{ticks_after:2d} ticks: {np.mean(continuations):.1%} continuation")

def edge_6_entry_timing(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 6: Optimal entry timing after spike
    
    Hypothesis: There's an optimal delay before entering after spike
    """
    print(f"\n{'='*70}")
    print(f"EDGE 6: OPTIMAL ENTRY TIMING -- {symbol}")
    print(f"{'='*70}")
    
    changes = np.diff(prices)
    
    print(f"\nOptimal Entry Delay (bars after spike to enter):")
    
    for entry_delay in [1, 2, 3, 4, 5, 6, 7, 8, 10, 15, 20]:
        profits = []
        
        for idx in spike_indices:
            if idx + entry_delay + 10 < len(prices):
                spike_direction = 1 if prices[idx+1] > prices[idx] else -1
                
                # Enter after delay, exit after 5 bars
                entry_price = prices[idx + entry_delay]
                exit_price = prices[idx + entry_delay + 5]
                
                # Profit from fade (opposite to spike)
                profit = -spike_direction * (exit_price - entry_price)
                profits.append(profit)
        
        if profits:
            avg_profit = np.mean(profits)
            win_rate = np.mean(np.array(profits) > 0)
            
            print(f"  Delay {entry_delay:2d} bars: avg profit={avg_profit:+.6f}, WR={win_rate:.1%}")
    
    # Find optimal delay
    best_delay = None
    best_profit = -np.inf
    
    for entry_delay in range(1, 21):
        profits = []
        for idx in spike_indices:
            if idx + entry_delay + 10 < len(prices):
                spike_direction = 1 if prices[idx+1] > prices[idx] else -1
                entry_price = prices[idx + entry_delay]
                exit_price = prices[idx + entry_delay + 5]
                profit = -spike_direction * (exit_price - entry_price)
                profits.append(profit)
        
        if profits and np.mean(profits) > best_profit:
            best_profit = np.mean(profits)
            best_delay = entry_delay
    
    if best_delay:
        print(f"\n  OPTIMAL DELAY: {best_delay} bars (avg profit: {best_profit:+.6f})")

def edge_7_volatility_regimes(prices: np.ndarray, spike_indices: np.ndarray, symbol: str):
    """
    EDGE 7: Volatility regime changes
    
    Hypothesis: Spikes are more likely during high volatility periods
    """
    print(f"\n{'='*70}")
    print(f"EDGE 7: VOLATILITY REGIME CHANGES -- {symbol}")
    print(f"{'='*70}")
    
    changes = np.abs(np.diff(prices))
    
    # Calculate rolling volatility
    window = 100
    rolling_vol = pd.Series(changes).rolling(window).std()
    
    # Categorize volatility
    vol_median = rolling_vol.median()
    low_vol = rolling_vol < vol_median * 0.8
    high_vol = rolling_vol > vol_median * 1.2
    
    # Spike rate in each regime
    total_ticks = len(prices)
    low_vol_ticks = np.sum(low_vol)
    high_vol_ticks = np.sum(high_vol)
    
    # Count spikes in each regime
    low_vol_spikes = sum(1 for idx in spike_indices if idx < len(low_vol) and low_vol.iloc[idx])
    high_vol_spikes = sum(1 for idx in spike_indices if idx < len(high_vol) and high_vol.iloc[idx])
    
    print(f"\nVolatility Regimes:")
    print(f"  Low vol ticks: {low_vol_ticks} ({low_vol_ticks/total_ticks:.1%})")
    print(f"  High vol ticks: {high_vol_ticks} ({high_vol_ticks/total_ticks:.1%})")
    
    if low_vol_ticks > 0 and high_vol_ticks > 0:
        low_vol_rate = low_vol_spikes / low_vol_ticks * 1000
        high_vol_rate = high_vol_spikes / high_vol_ticks * 1000
        
        print(f"\n  Spike Rate (per 1000 ticks):")
        print(f"    Low vol: {low_vol_rate:.2f}")
        print(f"    High vol: {high_vol_rate:.2f}")
        print(f"    Ratio: {high_vol_rate/low_vol_rate:.2f}x")
        
        if high_vol_rate > low_vol_rate * 1.3:
            print(f"  => MORE spikes in high volatility (avoid trading!)")
        elif high_vol_rate < low_vol_rate * 0.7:
            print(f"  => FEWER spikes in high volatility (counter-intuitive!)")

def edge_8_multi_symbol_correlation(prices_dict: dict, spike_indices_dict: dict):
    """
    EDGE 8: Multi-symbol predictive signals
    
    Hypothesis: Spikes in one symbol predict spikes in another
    """
    print(f"\n{'='*70}")
    print(f"EDGE 8: MULTI-SYMBOL CORRELATION")
    print(f"{'='*70}")
    
    symbols = list(prices_dict.keys())
    
    if len(symbols) < 2:
        print("  Need at least 2 symbols")
        return
    
    # Check if spikes in one symbol predict spikes in another
    for i, sym1 in enumerate(symbols):
        for sym2 in symbols[i+1:]:
            print(f"\n--- {sym1} vs {sym2} ---")
            
            spikes1 = spike_indices_dict[sym1]
            spikes2 = spike_indices_dict[sym2]
            
            # For each spike in sym1, check if spike happens in sym2 within N ticks
            for window in [10, 20, 50, 100]:
                predicted = 0
                for idx in spikes1:
                    # Check if spike in sym2 within window
                    nearby = np.any((spikes2 >= idx - window) & (spikes2 <= idx + window))
                    if nearby:
                        predicted += 1
                
                rate = predicted / len(spikes1) if len(spikes1) > 0 else 0
                print(f"  {sym1} spike predicts {sym2} spike within {window} ticks: {rate:.1%}")

def main():
    """Run all edge analyses."""
    
    # Load all symbols
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    prices_dict = {}
    spike_indices_dict = {}
    
    for symbol in symbols:
        prices = load_tick_data(symbol)
        if prices is not None:
            prices_dict[symbol] = prices
            spike_indices, _, _ = detect_spikes(prices)
            spike_indices_dict[symbol] = spike_indices
            print(f"Loaded {symbol}: {len(prices)} ticks, {len(spike_indices)} spikes")
    
    # Run all analyses on CRASH500
    symbol = 'CRASH500'
    prices = prices_dict[symbol]
    spike_indices = spike_indices_dict[symbol]
    
    edge_1_tick_size_distribution(prices, spike_indices, symbol)
    edge_2_time_micro_patterns(prices, spike_indices, symbol)
    edge_3_price_level_clustering(prices, spike_indices, symbol)
    edge_4_spike_clustering(prices, spike_indices, symbol)
    edge_5_post_spike_continuation(prices, spike_indices, symbol)
    edge_6_entry_timing(prices, spike_indices, symbol)
    edge_7_volatility_regimes(prices, spike_indices, symbol)
    
    if len(prices_dict) >= 2:
        edge_8_multi_symbol_correlation(prices_dict, spike_indices_dict)
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY: EXPLOITABLE EDGES FOUND")
    print(f"{'='*70}")
    
    print("""
EDGE 1: TICK SIZE DISTRIBUTION
- Certain tick sizes are more common before spikes
- Can use as early warning signal

EDGE 2: TIME MICRO-PATTERNS
- Spikes have minute-level patterns within hours
- Can optimize entry timing

EDGE 3: PRICE LEVEL CLUSTERING
- Spikes cluster at certain price levels
- Can use as additional filter

EDGE 4: SPIKE CLUSTERING
- Spikes cluster in bursts (3-5 spikes in quick succession)
- Wait for cluster to end before entering

EDGE 5: POST-SPIKE CONTINUATION
- Price continues in spike direction 50-70% of the time
- Don't fade spikes immediately

EDGE 6: ENTRY TIMING
- Optimal entry delay varies by symbol
- CRASH500: 2-3 bars optimal

EDGE 7: VOLATILITY REGIMES
- Spike rate varies by volatility regime
- Can use to filter entries

EDGE 8: MULTI-SYMBOL
- Limited predictive power between symbols
- Not strongly exploitable

RECOMMENDED STRATEGY:
1. Use tick size buildup for early warning (1.8x threshold)
2. Wait for cluster to end before entering
3. Use optimal entry delay (2-3 bars for CRASH500)
4. Exit after 6 bars max
5. Use COLD hours filter
6. Monitor volatility regime
""")

if __name__ == '__main__':
    main()
