"""Deep M1 Bar Pattern Research — Find exploitable edges beyond tick counting.

Key questions:
1. What happens after long gaps (20+ bars without spikes)?
2. Do squeeze patterns (tiny range bars) predict imminent spikes?
3. Is there a session/time effect on spike frequency?
4. Can we predict spike direction from bar patterns?
5. What is the optimal entry AFTER a long flat period?
"""
import sys
sys.path.insert(0, '.')

import numpy as np
import pandas as pd
from datetime import datetime, timedelta


def load_m1_candles(parquet_path):
    """Load M1 candles from parquet."""
    df = pd.read_parquet(parquet_path)
    # Standardize column names
    for col in ['Open', 'open']:
        if col in df.columns:
            df.columns = [c.lower() for c in df.columns]
            break
    if 'datetime' in df.columns:
        df['datetime'] = pd.to_datetime(df['datetime'])
    elif 'time' in df.columns:
        df['datetime'] = pd.to_datetime(df['time'])
    return df


def detect_spikes(bars, direction, threshold=3.0):
    """Detect spike bars based on range vs median."""
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


def analyze_spike_gaps(bars, spikes):
    """Analyze gap distribution between spikes."""
    if len(spikes) < 2:
        return {}
    gaps = np.diff(spikes)
    return {
        'count': len(spikes),
        'gap_min': int(gaps.min()),
        'gap_median': float(np.median(gaps)),
        'gap_mean': float(gaps.mean()),
        'gap_max': int(gaps.max()),
        'gap_std': float(gaps.std()),
        'gap_p50': float(np.percentile(gaps, 50)),
        'gap_p75': float(np.percentile(gaps, 75)),
        'gap_p90': float(np.percentile(gaps, 90)),
        'gap_p95': float(np.percentile(gaps, 95)),
        'gap_p99': float(np.percentile(gaps, 99)),
    }


def analyze_post_gap_recovery(bars, spikes, gap_threshold=15):
    """After a long gap (gap_threshold+ bars), what happens next?"""
    if len(spikes) < 2:
        return {}
    gaps = np.diff(spikes)
    long_gaps = [(i, gaps[i]) for i in range(len(gaps)) if gaps[i] >= gap_threshold]
    
    results = {}
    for horizon in [3, 5, 10, 15, 20]:
        count = 0
        for idx, gap_len in long_gaps:
            next_spike_bar = spikes[idx + 1] if idx + 1 < len(spikes) else len(bars)
            bars_from_gap_end = next_spike_bar - (spikes[idx] + gap_len)
            if bars_from_gap_end <= horizon:
                count += 1
        results[f'spike_within_{horizon}'] = count
        results[f'pct_within_{horizon}'] = count / max(len(long_gaps), 1) * 100
    
    # What is the actual gap AFTER the long gap?
    post_long_gaps = [gaps[i] for i in range(len(gaps)) if gaps[i] >= gap_threshold]
    next_gaps = [gaps[i+1] for i in range(len(gaps)) if gaps[i] >= gap_threshold and i+1 < len(gaps)]
    
    results['long_gap_count'] = len(long_gaps)
    results['avg_long_gap'] = float(np.mean(post_long_gaps)) if post_long_gaps else 0
    results['avg_next_gap'] = float(np.mean(next_gaps)) if next_gaps else 0
    results['median_next_gap'] = float(np.median(next_gaps)) if next_gaps else 0
    
    return results


def analyze_pre_spike_squeeze(bars, spikes):
    """Analyze if small-range bars precede spikes (volatility squeeze)."""
    ranges = bars['high'].values - bars['low'].values
    med = np.median(ranges)
    
    # Define squeeze as range < 0.5x median
    squeeze_threshold = med * 0.5
    
    results = {}
    for lookback in [1, 2, 3, 5]:
        squeeze_before_spike = 0
        total_spikes = 0
        for s_idx in spikes:
            if s_idx >= lookback:
                total_spikes += 1
                window = ranges[s_idx-lookback:s_idx]
                if np.any(window < squeeze_threshold):
                    squeeze_before_spike += 1
        
        results[f'squeeze_before_spike_{lookback}bar'] = squeeze_before_spike
        results[f'pct_squeeze_{lookback}bar'] = squeeze_before_spike / max(total_spikes, 1) * 100
    
    # Overall squeeze frequency
    squeeze_count = np.sum(ranges < squeeze_threshold)
    results['total_squeeze_bars'] = int(squeeze_count)
    results['squeeze_pct'] = squeeze_count / len(bars) * 100
    results['normal_squeeze_pct'] = 100 * (1 - (1-0.5)**1)  # theoretical ~50% for 0.5x threshold on uniform, but real data differs
    results['actual_squeeze_pct'] = float(squeeze_count / len(bars) * 100)
    
    return results


def analyze_direction_from_bars(bars, spikes, lookback=5):
    """Can we predict spike direction from pre-spike bar patterns?"""
    if len(spikes) < 10:
        return {}
    
    ranges = bars['high'].values - bars['low'].values
    closes = bars['close'].values
    opens = bars['open'].values
    
    # Features before each spike
    correct = 0
    total = 0
    
    for s_idx in spikes:
        if s_idx < lookback + 1:
            continue
        total += 1
        
        # Feature: trend of close prices in last N bars
        recent_closes = closes[s_idx-lookback:s_idx]
        trend = np.polyfit(range(lookback), recent_closes, 1)[0]
        
        # Feature: cumulative range (squeeze detection)
        recent_ranges = ranges[s_idx-lookback:s_idx]
        is_squeeze = np.mean(recent_ranges) < np.median(ranges) * 0.7
        
        # For BOOM (up spikes): price was drifting DOWN before spike
        # For CRASH (down spikes): price was drifting UP before spike
        # So trend < 0 should predict UP spike, trend > 0 should predict DOWN spike
        if trend < 0:  # predicts UP spike
            correct += 1  # We're analyzing one direction, so this is always correct if we know it's a spike
    
    return {
        'lookback': lookback,
        'total_spikes': total,
        'trend_correct': correct,
        'trend_pct': correct / max(total, 1) * 100
    }


def analyze_consecutive_flats(bars, spikes):
    """Analyze consecutive flat (non-spike) bars."""
    is_spike = np.zeros(len(bars), dtype=bool)
    is_spike[spikes] = True
    
    # Find runs of non-spike bars
    flat_runs = []
    current_run = 0
    for i in range(len(bars)):
        if not is_spike[i]:
            current_run += 1
        else:
            if current_run > 0:
                flat_runs.append(current_run)
            current_run = 0
    
    if not flat_runs:
        return {}
    
    flat_runs = np.array(flat_runs)
    
    # After each flat run, what is the NEXT gap?
    results = {
        'avg_flat_run': float(flat_runs.mean()),
        'median_flat_run': float(np.median(flat_runs)),
        'max_flat_run': int(flat_runs.max()),
        'pct_short_runs': float((flat_runs <= 3).sum() / len(flat_runs) * 100),
        'pct_long_runs': float((flat_runs >= 15).sum() / len(flat_runs) * 100),
    }
    
    return results


def analyze_intraday_session(bars, spikes):
    """Analyze spike frequency by hour of day (UTC)."""
    spike_set = set(spikes)
    
    hourly_spike_count = {}
    hourly_bar_count = {}
    
    for i in range(len(bars)):
        if 'datetime' in bars.columns:
            hour = bars['datetime'].iloc[i].hour
        else:
            hour = 0
        
        hourly_bar_count[hour] = hourly_bar_count.get(hour, 0) + 1
        if i in spike_set:
            hourly_spike_count[hour] = hourly_spike_count.get(hour, 0) + 1
    
    results = {}
    for h in range(24):
        bars_n = hourly_bar_count.get(h, 0)
        spikes_n = hourly_spike_count.get(h, 0)
        rate = spikes_n / max(bars_n, 1) * 100
        results[f'hour_{h:02d}'] = {'bars': bars_n, 'spikes': spikes_n, 'rate_pct': round(rate, 2)}
    
    return results


def analyze_range_expansion(bars, spikes, lookback=10):
    """Analyze if range expansion patterns predict spikes."""
    ranges = bars['high'].values - bars['low'].values
    med = np.median(ranges)
    
    # Find bars where range is shrinking (contracting)
    results = {}
    for lb in [3, 5, 10]:
        shrink_before_spike = 0
        total = 0
        for s_idx in spikes:
            if s_idx >= lb + 1:
                total += 1
                prev_ranges = ranges[s_idx-lb:s_idx]
                # Check if range was generally shrinking
                if prev_ranges[-1] < prev_ranges[0] * 0.8:  # range contracted
                    shrink_before_spike += 1
        results[f'contraction_before_spike_{lb}bar'] = shrink_before_spike
        results[f'pct_contraction_{lb}bar'] = shrink_before_spike / max(total, 1) * 100
    
    return results


def main():
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'up'),
        'CRASH500': ('data/candles_crash500_1min.parquet', 'down'),
        'BOOM1000': ('data/candles_boom1000_1min.parquet', 'up'),
        'CRASH1000': ('data/candles_crash1000_1min.parquet', 'down'),
    }
    
    # Check which files exist
    import os
    existing = {k: v for k, v in configs.items() if os.path.exists(v[0])}
    
    print(f"Found {len(existing)}/{len(configs)} data files")
    
    for sym, (path, direction) in existing.items():
        print(f"\n{'='*70}")
        print(f"  {sym} — M1 BAR PATTERN RESEARCH")
        print(f"{'='*70}")
        
        bars = load_m1_candles(path)
        print(f"  Loaded {len(bars)} M1 bars")
        
        # Use threshold=3.0 for spike detection
        spikes = detect_spikes(bars, direction, threshold=3.0)
        print(f"  Detected {len(spikes)} spikes")
        
        # 1. Gap distribution
        gap_info = analyze_spike_gaps(bars, spikes)
        print(f"\n  --- SPIKE GAP DISTRIBUTION ---")
        print(f"  Gaps between spikes (bars):")
        print(f"    Median: {gap_info['gap_median']:.0f} bars")
        print(f"    Mean:   {gap_info['gap_mean']:.0f} bars")
        print(f"    Max:    {gap_info['gap_max']} bars")
        print(f"    P90:    {gap_info['gap_p90']:.0f} bars")
        print(f"    P95:    {gap_info['gap_p95']:.0f} bars")
        print(f"    P99:    {gap_info['gap_p99']:.0f} bars")
        
        # 2. Post-gap recovery
        for threshold in [10, 15, 20, 25, 30]:
            rec = analyze_post_gap_recovery(bars, spikes, gap_threshold=threshold)
            print(f"\n  --- AFTER GAP >= {threshold} BARS ---")
            print(f"    Long gaps found: {rec.get('long_gap_count', 0)}")
            print(f"    Spike within 3 bars:  {rec.get(f'spike_within_3', 0)} ({rec.get(f'pct_within_3', 0):.1f}%)")
            print(f"    Spike within 5 bars:  {rec.get(f'spike_within_5', 0)} ({rec.get(f'pct_within_5', 0):.1f}%)")
            print(f"    Spike within 10 bars: {rec.get(f'spike_within_10', 0)} ({rec.get(f'pct_within_10', 0):.1f}%)")
            print(f"    Avg next gap:         {rec.get('avg_next_gap', 0):.1f} bars")
            print(f"    Median next gap:      {rec.get('median_next_gap', 0):.1f} bars")
        
        # 3. Squeeze analysis
        squeeze = analyze_pre_spike_squeeze(bars, spikes)
        print(f"\n  --- VOLATILITY SQUEEZE ---")
        print(f"  Squeeze bars (<0.5x median range): {squeeze.get('total_squeeze_bars', 0)} ({squeeze.get('actual_squeeze_pct', 0):.1f}%)")
        for lb in [1, 2, 3, 5]:
            print(f"    Squeeze before spike (lookback={lb}): {squeeze.get(f'pct_squeeze_{lb}bar', 0):.1f}%")
        
        # 4. Flat run analysis
        flats = analyze_consecutive_flats(bars, spikes)
        print(f"\n  --- CONSECUTIVE FLAT BARS ---")
        print(f"  Avg flat run:   {flats.get('avg_flat_run', 0):.1f} bars")
        print(f"  Median:         {flats.get('median_flat_run', 0):.1f} bars")
        print(f"  Max:            {flats.get('max_flat_run', 0)} bars")
        print(f"  Short runs (<=3): {flats.get('pct_short_runs', 0):.1f}%")
        print(f"  Long runs (>=15): {flats.get('pct_long_runs', 0):.1f}%")
        
        # 5. Range contraction
        contraction = analyze_range_expansion(bars, spikes)
        print(f"\n  --- RANGE CONTRACTION ---")
        for lb in [3, 5, 10]:
            print(f"    Contraction before spike (lookback={lb}): {contraction.get(f'pct_contraction_{lb}bar', 0):.1f}%")
        
        # 6. Session analysis
        session = analyze_intraday_session(bars, spikes)
        print(f"\n  --- SPIKE RATE BY HOUR (UTC) ---")
        best_hours = sorted(session.items(), key=lambda x: x[1]['rate_pct'], reverse=True)[:5]
        worst_hours = sorted(session.items(), key=lambda x: x[1]['rate_pct'])[:5]
        print(f"  Top 5 hours:")
        for h, info in best_hours:
            print(f"    {h}: {info['spikes']} spikes / {info['bars']} bars = {info['rate_pct']:.2f}%")
        print(f"  Worst 5 hours:")
        for h, info in worst_hours:
            if info['bars'] > 0:
                print(f"    {h}: {info['spikes']} spikes / {info['bars']} bars = {info['rate_pct']:.2f}%")
    
    print(f"\n{'='*70}")
    print(f"  RESEARCH COMPLETE")
    print(f"{'='*70}")


if __name__ == '__main__':
    main()
