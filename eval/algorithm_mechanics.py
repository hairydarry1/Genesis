#!/usr/bin/env python3
"""
ALGORITHM MECHANICS ANALYSIS v2 - OPTIMIZED
Reverse-engineering the Boom/Crash spike generator
"""
import os, statistics, math
import numpy as np
from collections import defaultdict, Counter

def load_ticks(filepath, max_ticks=200000):
    try:
        if filepath.endswith('.npy'):
            data = np.load(filepath, allow_pickle=True)
            if data.dtype == object:
                return [(int(row[0]), float(row[1])) for row in data[:max_ticks]]
            return [(i, float(data[i])) for i in range(min(len(data), max_ticks))]
        elif filepath.endswith('.parquet'):
            import pandas as pd
            df = pd.read_parquet(filepath)
            col = 'bid' if 'bid' in df.columns else 'close' if 'close' in df.columns else df.columns[0]
            return list(zip(range(len(df)), df[col].values[:max_ticks]))
    except Exception as e:
        print(f"  Error: {e}")
    return []

def detect_spikes(ticks, lookback=100, threshold=3.0):
    if len(ticks) < lookback + 10:
        return []
    spikes = []
    prices = [t[1] for t in ticks]
    for i in range(lookback, len(prices)):
        move = abs(prices[i] - prices[i-1])
        window = [abs(prices[j] - prices[j-1]) for j in range(max(1, i-lookback), i)]
        if not window:
            continue
        median = sorted(window)[len(window)//2]
        if median > 0 and move > threshold * median:
            spikes.append({'index': i, 'price': prices[i], 'move': move, 'median': median, 'ratio': move/median})
    return spikes

# ============================================================
# ANALYSIS 1: INTER-SPIKE DYNAMICS
# ============================================================
def analyze_inter_spike(spikes):
    print("\n" + "="*60)
    print("ANALYSIS 1: INTER-SPIKE DYNAMICS")
    print("="*60)
    if len(spikes) < 10:
        return
    
    intervals = [spikes[i]['index'] - spikes[i-1]['index'] for i in range(1, len(spikes))]
    sizes = [s['ratio'] for s in spikes]
    
    print(f"\nInter-spike intervals (ticks):")
    print(f"  Count:  {len(intervals)}")
    print(f"  Mean:   {statistics.mean(intervals):.1f}")
    print(f"  Median: {statistics.median(intervals):.1f}")
    print(f"  Std:    {statistics.stdev(intervals):.1f}")
    cv = statistics.stdev(intervals) / statistics.mean(intervals)
    print(f"  CV:     {cv:.3f} -> {'RANDOM' if cv > 0.5 else 'REGULAR'}")
    
    # Does a long gap predict a larger spike?
    print(f"\nGap size vs next spike size:")
    short_gaps = [(intervals[i], sizes[i+1]) for i in range(len(intervals)-1) if intervals[i] < statistics.median(intervals)]
    long_gaps = [(intervals[i], sizes[i+1]) for i in range(len(intervals)-1) if intervals[i] >= statistics.median(intervals)]
    
    if short_gaps and long_gaps:
        print(f"  Short gap (<{statistics.median(intervals):.0f}): avg next spike = {statistics.mean([g[1] for g in short_gaps]):.2f}x")
        print(f"  Long gap (>={statistics.median(intervals):.0f}): avg next spike = {statistics.mean([g[1] for g in long_gaps]):.2f}x")
    
    # Does a large spike predict the next interval?
    print(f"\nSpike size vs next gap:")
    small_spikes = [(sizes[i], intervals[i]) for i in range(len(sizes)-1) if sizes[i] < statistics.median(sizes)]
    large_spikes = [(sizes[i], intervals[i]) for i in range(len(sizes)-1) if sizes[i] >= statistics.median(sizes)]
    
    if small_spikes and large_spikes:
        print(f"  After small spike: avg next gap = {statistics.mean([g[1] for g in small_spikes]):.1f} ticks")
        print(f"  After large spike: avg next gap = {statistics.mean([g[1] for g in large_spikes]):.1f} ticks")

# ============================================================
# ANALYSIS 2: PRE-SPIKE MICROSTRUCTURE
# ============================================================
def analyze_pre_spike(ticks, spikes):
    print("\n" + "="*60)
    print("ANALYSIS 2: PRE-SPIKE MICROSTRUCTURE")
    print("="*60)
    if len(spikes) < 10:
        return
    
    prices = [t[1] for t in ticks]
    
    # Analyze tick-by-tick patterns in the 50 ticks before spike
    windows = [5, 10, 20, 50]
    
    for w in windows:
        pre_moves = []
        pre_directions = []
        pre_acceleration = []
        
        for spike in spikes:
            idx = spike['index']
            if idx < w + 5:
                continue
            
            # Get moves before spike
            moves = [prices[idx-j] - prices[idx-j-1] for j in range(w, 0, -1)]
            pre_moves.append(sum(abs(m) for m in moves))
            
            # Direction consistency
            ups = sum(1 for m in moves if m > 0)
            pre_directions.append(ups / len(moves))
            
            # Acceleration (are moves getting bigger?)
            if len(moves) >= 4:
                first_half = statistics.mean([abs(m) for m in moves[:len(moves)//2]])
                second_half = statistics.mean([abs(m) for m in moves[len(moves)//2:]])
                if first_half > 0:
                    pre_acceleration.append(second_half / first_half)
        
        if pre_moves:
            print(f"\n{w}-tick pre-spike window:")
            print(f"  Total move: {statistics.mean(pre_moves):.6f}")
            print(f"  Direction consistency: {statistics.mean(pre_directions)*100:.1f}%")
            if pre_acceleration:
                print(f"  Acceleration ratio: {statistics.mean(pre_acceleration):.3f}")
                print(f"  {'ACCELERATING' if statistics.mean(pre_acceleration) > 1.05 else 'DECELERATING' if statistics.mean(pre_acceleration) < 0.95 else 'STABLE'}")

# ============================================================
# ANALYSIS 3: TICK SIZE ANOMALIES
# ============================================================
def analyze_tick_sizes(ticks, spikes):
    print("\n" + "="*60)
    print("ANALYSIS 3: TICK SIZE ANOMALIES")
    print("="*60)
    if len(spikes) < 10:
        return
    
    prices = [t[1] for t in ticks]
    all_moves = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
    
    if not all_moves:
        return
    
    overall_median = sorted(all_moves)[len(all_moves)//2]
    overall_mean = statistics.mean(all_moves)
    
    print(f"\nOverall tick size:")
    print(f"  Median: {overall_median:.8f}")
    print(f"  Mean:   {overall_mean:.8f}")
    
    # Tick sizes in different windows relative to spikes
    windows = {
        '5 ticks before spike': 5,
        '10 ticks before spike': 10,
        '20 ticks before spike': 20,
        '50 ticks before spike': 50,
    }
    
    for label, w in windows.items():
        tick_sizes = []
        for spike in spikes:
            idx = spike['index']
            if idx < w + 5:
                continue
            for j in range(w):
                move = abs(prices[idx-j] - prices[idx-j-1])
                tick_sizes.append(move)
        
        if tick_sizes:
            median = statistics.median(tick_sizes)
            mean = statistics.mean(tick_sizes)
            ratio = median / overall_median if overall_median > 0 else 0
            print(f"\n{label}:")
            print(f"  Median: {median:.8f} ({ratio:.2f}x overall)")
            print(f"  Mean:   {mean:.8f}")
    
    # Are tick sizes increasing as we approach the spike?
    print(f"\nTick size progression (tick-by-tick toward spike):")
    for offset in [20, 15, 10, 5, 4, 3, 2, 1, 0]:
        sizes = []
        for spike in spikes:
            idx = spike['index']
            if idx < offset + 2:
                continue
            move = abs(prices[idx-offset] - prices[idx-offset-1])
            sizes.append(move)
        if sizes:
            median = statistics.median(sizes)
            ratio = median / overall_median if overall_median > 0 else 0
            bar = '#' * int(ratio * 10)
            print(f"  Tick -{offset:2d}: {median:.6f} ({ratio:.2f}x) {bar}")

# ============================================================
# ANALYSIS 4: SPIKE DIRECTION PATTERNS
# ============================================================
def analyze_spike_directions(spikes):
    print("\n" + "="*60)
    print("ANALYSIS 4: SPIKE DIRECTION PATTERNS")
    print("="*60)
    if len(spikes) < 10:
        return
    
    # We need the original prices to determine direction
    # But spikes dict has 'price' - we can use move size relative to previous
    print(f"\nTotal spikes: {len(spikes)}")
    
    # Cluster analysis
    print(f"\nSpike clustering (gaps between spikes):")
    intervals = [spikes[i]['index'] - spikes[i-1]['index'] for i in range(1, len(spikes))]
    
    clusters = []
    current_cluster = [intervals[0]]
    for i in range(1, len(intervals)):
        if intervals[i] <= 5:
            current_cluster.append(intervals[i])
        else:
            if len(current_cluster) > 1:
                clusters.append(current_cluster)
            current_cluster = [intervals[i]]
    if len(current_cluster) > 1:
        clusters.append(current_cluster)
    
    print(f"  Total clusters (2+ spikes within 5 ticks): {len(clusters)}")
    if clusters:
        sizes = [len(c)+1 for c in clusters]
        print(f"  Cluster sizes: min={min(sizes)}, max={max(sizes)}, avg={statistics.mean(sizes):.1f}")
    
    # Size autocorrelation
    sizes = [s['ratio'] for s in spikes]
    mean_size = statistics.mean(sizes)
    std_size = statistics.stdev(sizes) if len(sizes) > 1 else 1
    
    print(f"\nSpike size autocorrelation:")
    for lag in [1, 2, 3, 5]:
        if lag >= len(sizes):
            continue
        autocorr = sum((sizes[i]-mean_size)*(sizes[i-lag]-mean_size) for i in range(lag, len(sizes))) / ((len(sizes)-lag)*std_size**2)
        sig = " ***" if abs(autocorr) > 3/math.sqrt(len(sizes)) else ""
        print(f"  Lag {lag}: {autocorr:+.4f}{sig}")

# ============================================================
# ANALYSIS 5: VOLATILITY REGIME
# ============================================================
def analyze_volatility_regimes(ticks, spikes):
    print("\n" + "="*60)
    print("ANALYSIS 5: VOLATILITY REGIME DYNAMICS")
    print("="*60)
    if len(spikes) < 10:
        return
    
    prices = [t[1] for t in ticks]
    
    # Calculate rolling volatility (50-tick window)
    window = 50
    volatilities = []
    for i in range(window, len(prices)):
        chunk = [abs(prices[j] - prices[j-1]) for j in range(i-window+1, i+1)]
        volatilities.append(statistics.mean(chunk))
    
    if not volatilities:
        return
    
    overall_vol = statistics.mean(volatilities)
    
    # Classify volatility regimes
    low_vol_threshold = sorted(volatilities)[len(volatilities)//3]
    high_vol_threshold = sorted(volatilities)[2*len(volatilities)//3]
    
    # Count spikes in each regime
    low_spikes = 0
    med_spikes = 0
    high_spikes = 0
    
    for spike in spikes:
        idx = spike['index'] - window
        if 0 <= idx < len(volatilities):
            vol = volatilities[idx]
            if vol < low_vol_threshold:
                low_spikes += 1
            elif vol > high_vol_threshold:
                high_spikes += 1
            else:
                med_spikes += 1
    
    total_classified = low_spikes + med_spikes + high_spikes
    
    print(f"\nVolatility regimes:")
    print(f"  Low vol threshold:  {low_vol_threshold:.8f}")
    print(f"  High vol threshold: {high_vol_threshold:.8f}")
    print(f"  Overall mean:       {overall_vol:.8f}")
    
    if total_classified > 0:
        print(f"\nSpike frequency by regime:")
        print(f"  Low vol:  {low_spikes:5d} spikes ({low_spikes/total_classified*100:.1f}%)")
        print(f"  Mid vol:  {med_spikes:5d} spikes ({med_spikes/total_classified*100:.1f}%)")
        print(f"  High vol: {high_spikes:5d} spikes ({high_spikes/total_classified*100:.1f}%)")
    
    # Volatility after spike
    print(f"\nVolatility after spike:")
    for bars_after in [1, 5, 10, 20, 50]:
        post_vols = []
        for spike in spikes:
            idx = spike['index'] + bars_after
            if idx + window < len(prices):
                chunk = [abs(prices[j] - prices[j-1]) for j in range(idx, min(idx+window, len(prices)))]
                post_vols.append(statistics.mean(chunk))
        if post_vols:
            ratio = statistics.mean(post_vols) / overall_vol
            print(f"  +{bars_after:2d} bars: {statistics.mean(post_vols):.8f} ({ratio:.2f}x baseline)")

# ============================================================
# MAIN
# ============================================================
def main():
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    
    print("="*60)
    print("ALGORITHM MECHANICS ANALYSIS")
    print("Reverse-engineering the Boom/Crash spike generator")
    print("="*60)
    
    data_dir = os.path.join(os.path.dirname(__file__), '..', 'data')
    
    tick_files = {}
    for f in os.listdir(data_dir):
        if f.startswith('ticks_') and (f.endswith('.npy') or f.endswith('.parquet')):
            name = f.replace('ticks_', '').replace('.npy', '').replace('.parquet', '').replace('_', ' ')
            tick_files[name] = os.path.join(data_dir, f)
    
    print(f"\nFound {len(tick_files)} data files")
    
    for name, filepath in tick_files.items():
        print(f"\n{'#'*60}")
        print(f"# {name}")
        print(f"{'#'*60}")
        
        ticks = load_ticks(filepath, max_ticks=200000)
        if len(ticks) < 1000:
            print(f"  Not enough data ({len(ticks)} ticks)")
            continue
        
        print(f"  Loaded {len(ticks)} ticks")
        
        spikes = detect_spikes(ticks, lookback=100, threshold=3.0)
        print(f"  Detected {len(spikes)} spikes")
        
        if len(spikes) < 3:
            print("  Not enough spikes for analysis")
            continue
        
        analyze_inter_spike(spikes)
        analyze_pre_spike(ticks, spikes)
        analyze_tick_sizes(ticks, spikes)
        analyze_spike_directions(spikes)
        analyze_volatility_regimes(ticks, spikes)

if __name__ == "__main__":
    main()
