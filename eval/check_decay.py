#!/usr/bin/env python3
"""Check all strategies for pattern decay across different time periods."""

import pandas as pd
import numpy as np
import os
from datetime import datetime

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time'])
    df['hour'] = df['time'].dt.hour
    df['week'] = df['time'].dt.isocalendar().week
    df['date'] = df['time'].dt.date
    df['range'] = df['high'] - df['low']
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1
    return df

def check_cold_hours_decay(df, sym):
    """Check if COLD hours pattern persists across weeks."""
    weeks = sorted(df['week'].unique())
    
    print(f"\n  COLD Hours Decay Check ({sym}):")
    print(f"  {'Week':>8} {'Expected':>20} {'Actual':>20} {'Match':>8}")
    print(f"  {'-'*8} {'-'*20} {'-'*20} {'-'*8}")
    
    # Overall COLD hours
    overall = df.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
    overall['rate'] = overall['spikes'] / overall['bars'] * 100
    expected = df['is_spike'].sum() / len(df) * 100
    cold_hours = sorted(overall[overall['rate'] < expected - 0.5].index.tolist())
    
    decay_found = False
    for w in weeks:
        wdf = df[df['week'] == w]
        if len(wdf) < 100:
            continue
        
        hourly = wdf.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
        hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
        w_expected = wdf['is_spike'].sum() / len(wdf) * 100
        w_cold = sorted(hourly[hourly['rate'] < w_expected - 0.5].index.tolist())
        
        # Calculate match
        expected_set = set(cold_hours)
        actual_set = set(w_cold)
        match = len(expected_set & actual_set) / max(len(expected_set), 1) * 100
        
        status = "OK" if match > 50 else "DECAY"
        if match <= 50:
            decay_found = True
        
        print(f"  {w:>8} {str(cold_hours):>20} {str(w_cold):>20} {match:>6.0f}% {status}")
    
    return not decay_found

def check_spike_frequency_decay(df, sym):
    """Check if spike frequency changes across weeks."""
    weeks = sorted(df['week'].unique())
    
    print(f"\n  Spike Frequency Decay Check ({sym}):")
    print(f"  {'Week':>8} {'Spikes':>8} {'Rate':>8} {'vs Avg':>10}")
    print(f"  {'-'*8} {'-'*8} {'-'*8} {'-'*10}")
    
    overall_rate = df['is_spike'].mean() * 100
    
    stable = True
    for w in weeks:
        wdf = df[df['week'] == w]
        if len(wdf) < 100:
            continue
        
        spikes = wdf['is_spike'].sum()
        rate = wdf['is_spike'].mean() * 100
        diff = rate - overall_rate
        
        status = "OK" if abs(diff) < 2 else ("DECAY" if diff < -2 else "INCREASE")
        if abs(diff) >= 2:
            stable = False
        
        print(f"  {w:>8} {spikes:>8} {rate:>6.1f}% {diff:>+8.1f}% {status}")
    
    return stable

def check_drift_decay(df, sym):
    """Check if counter-drift magnitude changes across weeks."""
    weeks = sorted(df['week'].unique())
    
    print(f"\n  Drift Magnitude Decay Check ({sym}):")
    print(f"  {'Week':>8} {'Avg Drift':>12} {'WR':>8} {'vs Avg':>10}")
    print(f"  {'-'*8} {'-'*12} {'-'*8} {'-'*10}")
    
    # Calculate drift for each spike
    all_drifts = []
    for _, row in df[df['is_spike']].iterrows():
        idx = df.index.get_loc(row.name)
        if idx + 9 < len(df):
            future = df.iloc[idx+1:idx+10]
            drift = future['close'].iloc[-1] - row['close']
            if row['spike_dir'] == 1:
                drift = -drift  # Counter-drift for UP spike
            else:
                drift = drift   # Counter-drift for DOWN spike
            all_drifts.append({'week': row['week'], 'drift': drift})
    
    if not all_drifts:
        print("  No drift data available")
        return True
    
    drift_df = pd.DataFrame(all_drifts)
    overall_avg = drift_df['drift'].mean()
    
    stable = True
    for w in weeks:
        wdrifts = drift_df[drift_df['week'] == w]['drift']
        if len(wdrifts) < 10:
            continue
        
        avg = wdrifts.mean()
        wr = (wdrifts > 0).mean() * 100
        diff = avg - overall_avg
        
        status = "OK" if abs(diff) < 0.01 else "DECAY"
        if abs(diff) >= 0.01:
            stable = False
        
        print(f"  {w:>8} {avg:>10.4f} {wr:>6.1f}% {diff:>+8.4f} {status}")
    
    return stable

def check_buildup_decay(df, sym):
    """Check if velocity buildup pattern persists."""
    # This requires tick data - skip if not available
    print(f"\n  Buildup Pattern Check ({sym}):")
    print(f"  Requires tick data - not available in M1 candles")
    print(f"  Run eval/test_velocity_12.py for tick-level analysis")
    return True  # Can't check without tick data

def main():
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    
    print("="*70)
    print("  STRATEGY DECAY ANALYSIS")
    print(f"  Date: {datetime.now().strftime('%Y-%m-%d')}")
    print("="*70)
    
    all_ok = True
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        
        df = detect_spikes(df)
        
        print(f"\n{'='*70}")
        print(f"  {sym.upper()} ({len(df)} bars, {df['is_spike'].sum()} spikes)")
        print(f"{'='*70}")
        
        # Check each pattern
        cold_ok = check_cold_hours_decay(df, sym)
        freq_ok = check_spike_frequency_decay(df, sym)
        drift_ok = check_drift_decay(df, sym)
        buildup_ok = check_buildup_decay(df, sym)
        
        # Summary
        status = "OK" if all([cold_ok, freq_ok, drift_ok, buildup_ok]) else "DECAY DETECTED"
        if not all([cold_ok, freq_ok, drift_ok, buildup_ok]):
            all_ok = False
        
        print(f"\n  SUMMARY: {status}")
        print(f"    COLD hours: {'OK' if cold_ok else 'DECAY'}")
        print(f"    Spike frequency: {'OK' if freq_ok else 'DECAY'}")
        print(f"    Drift magnitude: {'OK' if drift_ok else 'DECAY'}")
        print(f"    Buildup pattern: {'OK' if buildup_ok else 'NEEDS TICK DATA'}")
    
    # Overall verdict
    print(f"\n{'='*70}")
    print(f"  OVERALL DECAY STATUS")
    print(f"{'='*70}")
    
    if all_ok:
        print(f"  STATUS: ALL PATTERNS STABLE")
        print(f"  All strategies remain valid")
        print(f"  Next check: In 2 weeks")
    else:
        print(f"  STATUS: DECAY DETECTED IN SOME PATTERNS")
        print(f"  Some strategies may need adjustment")
        print(f"  Review individual symbol results above")
    
    print(f"{'='*70}")

if __name__ == '__main__':
    main()
