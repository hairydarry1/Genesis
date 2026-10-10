"""
ANALYZE CLUSTERS: Double spikes and cluster patterns
Understand the risk of multiple spikes in quick succession
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

def analyze_clusters(symbol: str):
    """Analyze spike clusters"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"CLUSTER ANALYSIS: {symbol}")
    print(f"{'='*70}")
    print(f"Total spikes: {len(spike_indices)}")
    
    # Calculate intervals between spikes
    intervals = np.diff(spike_indices)
    
    print(f"\nSpike Intervals:")
    print(f"  Mean: {np.mean(intervals):.1f} bars")
    print(f"  Median: {np.median(intervals):.1f} bars")
    print(f"  Min: {np.min(intervals)} bars")
    print(f"  Max: {np.max(intervals)} bars")
    
    # Interval distribution
    print(f"\nInterval Distribution:")
    bins = [0, 1, 2, 3, 4, 5, 10, 20, 50, 100]
    hist, _ = np.histogram(intervals, bins=bins)
    
    for i in range(len(bins)-1):
        count = hist[i]
        pct = count / len(intervals) * 100
        bar = '#' * int(pct / 2)
        print(f"  {bins[i]:3d}-{bins[i+1]:3d} bars: {count:5d} ({pct:5.1f}%) {bar}")
    
    # Double spike analysis (interval = 1 bar)
    double_spike_count = np.sum(intervals == 1)
    print(f"\nDouble Spikes (1 bar apart): {double_spike_count} ({double_spike_count/len(intervals)*100:.1f}%)")
    
    # Analyze what happens after double spike
    print(f"\n--- After Double Spike (interval=1) ---")
    
    double_spike_results = []
    for i, interval in enumerate(intervals):
        if interval == 1:
            spike_idx = spike_indices[i]
            
            # Check direction of both spikes
            if spike_idx + 2 < len(prices):
                spike1_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
                spike2_dir = 1 if prices[spike_idx + 2] > prices[spike_idx + 1] else -1
                
                # Same direction or opposite?
                same_dir = (spike1_dir == spike2_dir)
                
                # What happens next (5 bars after second spike)?
                if spike_idx + 7 < len(prices):
                    price_after = prices[spike_idx + 7]
                    price_before = prices[spike_idx + 1]
                    
                    # If we faded spike1, what's our PnL after spike2?
                    # We entered short after spike1 UP, but spike2 goes UP too
                    fade_pnl = -spike1_dir * (price_after - price_before)
                    
                    double_spike_results.append({
                        'same_dir': same_dir,
                        'spike1_dir': spike1_dir,
                        'spike2_dir': spike2_dir,
                        'fade_pnl': fade_pnl
                    })
    
    if double_spike_results:
        same_dir_count = sum(1 for r in double_spike_results if r['same_dir'])
        opp_dir_count = len(double_spike_results) - same_dir_count
        
        print(f"  Same direction: {same_dir_count} ({same_dir_count/len(double_spike_results)*100:.1f}%)")
        print(f"  Opposite direction: {opp_dir_count} ({opp_dir_count/len(double_spike_results)*100:.1f}%)")
        
        # PnL if we faded spike1
        fade_pnls = [r['fade_pnl'] for r in double_spike_results]
        fade_wins = sum(1 for p in fade_pnls if p > 0)
        
        print(f"\n  If we faded spike1:")
        print(f"    Win rate: {fade_wins/len(fade_pnls)*100:.1f}%")
        print(f"    Avg PnL: {np.mean(fade_pnls):.4f}")
        print(f"    Total PnL: {np.sum(fade_pnls):.4f}")
    
    # Triple spike analysis (interval=1 twice)
    print(f"\n--- Triple Spikes (3 spikes in 3 bars) ---")
    
    triple_count = 0
    for i in range(len(intervals) - 1):
        if intervals[i] == 1 and intervals[i+1] == 1:
            triple_count += 1
    
    print(f"  Triple spike occurrences: {triple_count} ({triple_count/len(intervals)*100:.1f}%)")
    
    # Cluster analysis (2+ spikes in 5 bars)
    print(f"\n--- Cluster Analysis (2+ spikes in 5 bars) ---")
    
    cluster_count = 0
    cluster_sizes = []
    
    for i in range(len(spike_indices)):
        # Count spikes in next 5 bars
        spikes_in_window = sum(1 for j in range(i, len(spike_indices)) 
                              if spike_indices[j] - spike_indices[i] <= 5)
        
        if spikes_in_window >= 2:
            cluster_count += 1
            cluster_sizes.append(spikes_in_window)
    
    print(f"  Spikes in clusters: {cluster_count} ({cluster_count/len(spike_indices)*100:.1f}%)")
    
    if cluster_sizes:
        size_counts = {}
        for s in cluster_sizes:
            if s not in size_counts:
                size_counts[s] = 0
            size_counts[s] += 1
        
        print(f"  Cluster size distribution:")
        for size in sorted(size_counts.keys()):
            count = size_counts[size]
            print(f"    Size {size}: {count} clusters")
    
    # Risk analysis: What if we enter at delay 1 and get hit by spike 2?
    print(f"\n--- RISK ANALYSIS: Enter at delay 1, spike 2 happens ---")
    
    risk_results = []
    for i in range(len(spike_indices) - 1):
        spike1_idx = spike_indices[i]
        spike2_idx = spike_indices[i + 1]
        
        interval = spike2_idx - spike1_idx
        
        # If interval is 2 (spike1 at bar 0, we enter at bar 1, spike2 at bar 2)
        if interval == 2:
            if spike2_idx + 5 < len(prices):
                # Direction of spike1
                spike1_dir = 1 if prices[spike1_idx + 1] > prices[spike1_idx] else -1
                
                # We fade spike1 (enter opposite)
                entry_price = prices[spike1_idx + 1]  # Enter at bar 1
                
                # Spike2 happens at bar 2
                spike2_dir = 1 if prices[spike2_idx + 1] > prices[spike2_idx] else -1
                
                # If spike2 is AGAINST our position, we lose
                if spike2_dir == -spike1_dir:  # Spike2 goes against us
                    # We would have been stopped out or taken damage
                    exit_price = prices[spike2_idx + 1]
                    pnl = -spike1_dir * (exit_price - entry_price)
                    risk_results.append({
                        'interval': interval,
                        'spike1_dir': spike1_dir,
                        'spike2_dir': spike2_dir,
                        'pnl': pnl,
                        'against': True
                    })
                else:
                    # Spike2 goes WITH us (helps our fade)
                    risk_results.append({
                        'interval': interval,
                        'spike1_dir': spike1_dir,
                        'spike2_dir': spike2_dir,
                        'pnl': 0,
                        'against': False
                    })
    
    if risk_results:
        against_count = sum(1 for r in risk_results if r['against'])
        with_count = len(risk_results) - against_count
        
        print(f"  Interval=2 occurrences: {len(risk_results)}")
        print(f"  Spike2 AGAINST us: {against_count} ({against_count/len(risk_results)*100:.1f}%)")
        print(f"  Spike2 WITH us: {with_count} ({with_count/len(risk_results)*100:.1f}%)")
        
        if against_count > 0:
            against_pnls = [r['pnl'] for r in risk_results if r['against']]
            print(f"  Avg damage when against: {np.mean(against_pnls):.4f}")
            print(f"  Max damage: {np.min(against_pnls):.4f}")
    
    # How often does safety exit save us?
    print(f"\n--- SAFETY EXIT ANALYSIS ---")
    
    safety_saves = 0
    safety_misses = 0
    
    for i in range(len(spike_indices) - 1):
        spike1_idx = spike_indices[i]
        spike2_idx = spike_indices[i + 1]
        
        interval = spike2_idx - spike1_idx
        
        # If we entered at delay 1 after spike1
        if interval >= 2:
            entry_bar = spike1_idx + 1
            
            # Safety exit would trigger on spike2
            if spike2_idx <= entry_bar + 5:  # Within our hold period
                safety_saves += 1
            else:
                safety_misses += 1
    
    print(f"  Safety exit would trigger: {safety_saves} times")
    print(f"  Safety exit would NOT trigger: {safety_misses} times")
    print(f"  Safety exit rate: {safety_saves/(safety_saves+safety_misses)*100:.1f}%")

def main():
    """Run on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    for symbol in symbols:
        analyze_clusters(symbol)

if __name__ == '__main__':
    main()
