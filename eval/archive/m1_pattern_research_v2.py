"""Deep M1 Pattern Research v2 — Focus on actionable edges.

Key insight from v1: ALL spikes occur within 3 bars after any gap.
That's a tautology (the gap IS the distance between spikes).

REAL questions:
1. After a long gap (20+ bars), is the NEXT gap shorter or longer than normal?
2. Within flat runs, what bar patterns appear right before the spike?
3. Can we detect "accumulation" patterns (gradual price build-up)?
4. What is the optimal entry point within a flat run?
"""
import sys
sys.path.insert(0, '.')

import numpy as np
import pandas as pd


def load_m1(path):
    df = pd.read_parquet(path)
    df.columns = [c.lower() for c in df.columns]
    for col in ['datetime', 'time']:
        if col in df.columns:
            df['dt'] = pd.to_datetime(df[col])
            break
    return df


def detect_spikes(bars, direction, threshold=3.0):
    ranges = bars['high'] - bars['low']
    med = ranges.median()
    spikes = []
    for i in range(len(bars)):
        rng = ranges.iloc[i]
        if rng > threshold * med:
            if direction == 'up' and bars['close'].iloc[i] > bars['open'].iloc[i]:
                spikes.append(i)
            elif direction == 'down' and bars['close'].iloc[i] < bars['open'].iloc[i]:
                spikes.append(i)
    return np.array(spikes)


def analyze_post_long_gap(bars, spikes):
    """After a long gap, what is the NEXT gap?"""
    if len(spikes) < 3:
        return {}
    gaps = np.diff(spikes)
    
    print(f"\n  --- NEXT GAP AFTER LONG GAP ---")
    for threshold in [10, 15, 20, 25, 30, 40]:
        long_indices = [i for i in range(len(gaps)-1) if gaps[i] >= threshold]
        if not long_indices:
            print(f"  Gap >= {threshold}: no data")
            continue
        
        next_gaps = [gaps[i+1] for i in long_indices]
        avg_next = np.mean(next_gaps)
        median_next = np.median(next_gaps)
        avg_current = np.mean([gaps[i] for i in long_indices])
        
        # What % of next gaps are shorter?
        shorter = sum(1 for ng in next_gaps if ng < avg_current * 0.5)
        
        print(f"  Gap >= {threshold:2d}: {len(long_indices):4d} occurrences")
        print(f"    Avg current gap:   {avg_current:.1f}")
        print(f"    Avg NEXT gap:      {avg_next:.1f}")
        print(f"    Median NEXT gap:   {median_next:.1f}")
        print(f"    Next gap < 50% of current: {shorter}/{len(long_indices)} ({shorter/len(long_indices)*100:.1f}%)")
    
    return gaps


def analyze_bar_patterns_before_spike(bars, spikes):
    """What do the bars RIGHT BEFORE a spike look like?"""
    ranges = bars['high'].values - bars['low'].values
    closes = bars['close'].values
    opens = bars['open'].values
    med_range = np.median(ranges)
    
    print(f"\n  --- BAR PATTERNS BEFORE SPIKE ---")
    
    for lookback in [1, 2, 3, 5]:
        small_range_count = 0
        bearish_count = 0  # bars closing lower than open (before UP spike)
        bullish_count = 0  # bars closing higher than open (before DOWN spike)
        total = 0
        avg_range_before = []
        
        for s_idx in spikes:
            if s_idx < lookback:
                continue
            total += 1
            
            # Range of bars before spike
            prev_ranges = ranges[s_idx-lookback:s_idx]
            avg_range_before.append(np.mean(prev_ranges))
            
            # Last bar before spike
            last_range = ranges[s_idx-1]
            last_close = closes[s_idx-1]
            last_open = opens[s_idx-1]
            
            if last_range < med_range * 0.8:
                small_range_count += 1
            
            if last_close < last_open:
                bearish_count += 1
            else:
                bullish_count += 1
        
        print(f"\n  Lookback = {lookback} bars:")
        print(f"    Small range bars (<0.8x median): {small_range_count}/{total} ({small_range_count/max(total,1)*100:.1f}%)")
        print(f"    Bearish bars before spike:        {bearish_count}/{total} ({bearish_count/max(total,1)*100:.1f}%)")
        print(f"    Bullish bars before spike:        {bullish_count}/{total} ({bullish_count/max(total,1)*100:.1f}%)")
        print(f"    Avg range of {lookback} bars before spike: {np.mean(avg_range_before):.4f} (median overall: {med_range:.4f})")


def analyze_price_drift(bars, spikes):
    """Track cumulative price movement during flat runs."""
    closes = bars['close'].values
    
    print(f"\n  --- PRICE DRIFT DURING FLAT RUNS ---")
    
    # For each flat run, track cumulative drift
    drift_at_entry = []  # drift from start of flat run to where we'd enter
    
    for i in range(1, len(spikes)):
        start = spikes[i-1]
        end = spikes[i]
        gap_len = end - start
        
        if gap_len < 10:
            continue
        
        # Price drift from spike to spike
        start_price = closes[start]
        
        # Track drift at different points during the flat run
        for entry_offset in [5, 10, 15, 20, 25]:
            if start + entry_offset >= end:
                continue
            entry_price = closes[start + entry_offset]
            drift = (entry_price - start_price) / start_price * 100
            drift_at_entry.append((gap_len, entry_offset, drift))
    
    for entry_offset in [5, 10, 15, 20, 25]:
        subset = [d for d in drift_at_entry if d[1] == entry_offset]
        if not subset:
            continue
        drifts = [d[2] for d in subset]
        print(f"  At bar {entry_offset:2d} into flat run:")
        print(f"    Avg drift: {np.mean(drifts):+.4f}%")
        print(f"    Std drift: {np.std(drifts):.4f}%")
        print(f"    P25 drift: {np.percentile(drifts, 25):+.4f}%")
        print(f"    P75 drift: {np.percentile(drifts, 75):+.4f}%")


def analyze_cluster_patterns(bars, spikes):
    """Analyze spike clusters — groups of spikes close together."""
    gaps = np.diff(spikes)
    
    print(f"\n  --- SPIKE CLUSTER ANALYSIS ---")
    
    # Define cluster as gaps < 5 bars
    clusters = []
    current_cluster = [spikes[0]]
    for i in range(len(gaps)):
        if gaps[i] <= 5:
            current_cluster.append(spikes[i+1])
        else:
            if len(current_cluster) >= 2:
                clusters.append(current_cluster)
            current_cluster = [spikes[i+1]]
    if len(current_cluster) >= 2:
        clusters.append(current_cluster)
    
    print(f"  Clusters (2+ spikes within 5 bars): {len(clusters)}")
    
    # After a cluster, what happens?
    post_cluster_gaps = []
    for c_idx in range(len(clusters) - 1):
        end_of_cluster = clusters[c_idx][-1]
        start_of_next = clusters[c_idx+1][0]
        gap = start_of_next - end_of_cluster
        post_cluster_gaps.append(gap)
    
    if post_cluster_gaps:
        print(f"  Avg gap after cluster: {np.mean(post_cluster_gaps):.1f} bars")
        print(f"  Median gap after cluster: {np.median(post_cluster_gaps):.1f} bars")
    
    # After a SINGLE spike (not in cluster), what's the gap?
    isolated_spikes = []
    for s_idx in spikes:
        in_cluster = False
        for c in clusters:
            if s_idx in c:
                in_cluster = True
                break
        if not in_cluster:
            isolated_spikes.append(s_idx)
    
    isolated_gaps = []
    for s_idx in isolated_spikes:
        # Find next spike
        next_idx = np.searchsorted(spikes, s_idx + 1)
        if next_idx < len(spikes):
            isolated_gaps.append(spikes[next_idx] - s_idx)
    
    if isolated_gaps:
        print(f"  Isolated spikes: {len(isolated_spikes)}")
        print(f"  Avg gap after isolated: {np.mean(isolated_gaps):.1f} bars")
        print(f"  Median gap after isolated: {np.median(isolated_gaps):.1f} bars")
    
    # KEY QUESTION: After a cluster, is the next spike predictable?
    # If cluster ends at bar X, what's the gap to next spike?
    print(f"\n  After cluster ends:")
    for gap_thresh in [5, 10, 15, 20]:
        count = sum(1 for g in post_cluster_gaps if g <= gap_thresh)
        print(f"    Next spike within {gap_thresh:2d} bars: {count}/{len(post_cluster_gaps)} ({count/max(len(post_cluster_gaps),1)*100:.1f}%)")


def analyze_tick_density_in_bars(tick_path, bars_df):
    """Load tick data and analyze tick density per M1 bar."""
    import os
    if not os.path.exists(tick_path):
        print(f"\n  [SKIP] Tick data not found: {tick_path}")
        return
    
    try:
        import pyarrow.parquet as pq
        ticks_df = pq.read_table(tick_path).to_pandas()
    except:
        try:
            ticks_df = pd.read_parquet(tick_path)
        except:
            print(f"\n  [SKIP] Cannot load tick data: {tick_path}")
            return
    
    print(f"\n  --- TICK DENSITY PER M1 BAR ---")
    print(f"  Loaded {len(ticks_df)} ticks")
    
    # Check column names
    tick_cols = ticks_df.columns.tolist()
    print(f"  Tick columns: {tick_cols}")
    
    # Count ticks per minute
    if 'epoch' in tick_cols:
        ticks_df['minute'] = pd.to_datetime(ticks_df['epoch'], unit='s').dt.floor('min')
        ticks_per_min = ticks_df.groupby('minute').size()
        
        print(f"  Ticks per minute:")
        print(f"    Mean:   {ticks_per_min.mean():.1f}")
        print(f"    Median: {ticks_per_min.median():.1f}")
        print(f"    Min:    {ticks_per_min.min()}")
        print(f"    Max:    {ticks_per_min.max()}")
        print(f"    P5:     {ticks_per_min.quantile(0.05):.0f}")
        print(f"    P95:    {ticks_per_min.quantile(0.95):.0f}")
        
        # Does tick density predict spikes?
        print(f"\n  Ticks per minute in spike bars vs normal bars:")
        # This requires matching tick density to bar-level spikes
    else:
        print(f"  No 'epoch' column found. Available: {tick_cols[:10]}")


def analyze_price_distance_from_last_spike(bars, spikes):
    """How far is price from last spike price at each bar?"""
    closes = bars['close'].values
    
    print(f"\n  --- PRICE DISTANCE FROM LAST SPIKE ---")
    
    # For each bar, calculate how far price has moved from last spike close
    distances = []
    for i in range(len(bars)):
        last_spike_idx = -1
        for s in spikes:
            if s < i:
                last_spike_idx = s
            else:
                break
        if last_spike_idx >= 0:
            dist = closes[i] - closes[last_spike_idx]
            distances.append((i, last_spike_idx, dist))
    
    # Group by bars since last spike
    for bars_since in [5, 10, 15, 20, 25, 30, 40, 50]:
        subset = [(i, s, d) for i, s, d in distances if i - s == bars_since]
        if not subset:
            continue
        dists = [d for _, _, d in subset]
        print(f"  {bars_since:2d} bars since spike:")
        print(f"    Avg distance: {np.mean(dists):+.4f}")
        print(f"    Std distance: {np.std(dists):.4f}")
        print(f"    Count: {len(subset)}")


def main():
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'data/ticks_boom_500.parquet', 'up'),
        'CRASH500': ('data/candles_crash500_1min.parquet', 'data/ticks_crash_500.parquet', 'down'),
    }
    
    import os
    for sym, (candle_path, tick_path, direction) in configs.items():
        if not os.path.exists(candle_path):
            continue
        
        print(f"\n{'='*70}")
        print(f"  {sym} — DEEP M1 PATTERN RESEARCH v2")
        print(f"{'='*70}")
        
        bars = load_m1(candle_path)
        print(f"  Loaded {len(bars)} M1 bars")
        spikes = detect_spikes(bars, direction, threshold=3.0)
        print(f"  Detected {len(spikes)} spikes")
        
        analyze_post_long_gap(bars, spikes)
        analyze_bar_patterns_before_spike(bars, spikes)
        analyze_price_drift(bars, spikes)
        analyze_cluster_patterns(bars, spikes)
        analyze_price_distance_from_last_spike(bars, spikes)
        analyze_tick_density_in_bars(tick_path, bars)
    
    print(f"\n{'='*70}")
    print(f"  DEEP RESEARCH COMPLETE")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
