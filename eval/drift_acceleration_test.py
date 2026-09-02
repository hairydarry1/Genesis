#!/usr/bin/env python3
"""
DRIFT ACCELERATION AS SPIKE PREDICTOR
======================================
H9 found that drift ACCELERATES before spikes:
- CRASH1000: drift goes from -0.6 to -2.1 (3.4x faster)
- BOOM1000: drift goes from +1.6 to +5.4 (3.3x faster)

This means: when drift accelerates 3x, a spike is LIKELY.
We can EXIT before the spike happens!

This is the CONTINGENCY the user wants:
- Monitor drift acceleration
- When acceleration exceeds threshold, EXIT
- This avoids the spike damage entirely
"""

import numpy as np
import pandas as pd
import os

DATA_DIR = "data"

def load_candles(symbol):
    fpath = os.path.join(DATA_DIR, f"candles_{symbol}_1min.parquet")
    if not os.path.exists(fpath):
        return None
    return pd.read_parquet(fpath)

def find_spikes(df, threshold=3.0):
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    ranges = highs - lows
    n = len(ranges)
    median_ranges = np.full(n, 0.01)
    for i in range(20, n):
        median_ranges[i] = np.median(ranges[max(0,i-20):i])
    
    spikes = []
    for i in range(1, n-10):
        if median_ranges[i] > 0 and ranges[i] > threshold * median_ranges[i]:
            t = 'boom' if closes[i] > opens[i] else 'crash'
            spikes.append({
                'type': t, 'idx': i, 'range': ranges[i],
                'median': median_ranges[i], 'ratio': ranges[i]/median_ranges[i]
            })
    return spikes

def test_drift_acceleration_exit(df, spikes):
    """
    Test: EXIT when drift acceleration exceeds threshold.
    If we can detect the acceleration BEFORE the spike,
    we can close the position and avoid the damage.
    """
    closes = df['close'].values
    n = len(closes)
    velocity = np.diff(closes)
    
    # Calculate drift acceleration (change in velocity)
    # Positive acceleration = drift getting faster in UP direction
    # Negative acceleration = drift getting faster in DOWN direction
    
    results = {}
    
    for accel_threshold in [1.5, 2.0, 2.5, 3.0, 4.0, 5.0]:
        # Simulate: enter after spike, exit when acceleration exceeds threshold
        wins = losses = 0
        total_pnl = 0
        early_exits = 0
        max_consec = 0
        streak = 0
        
        spike_indices = set(s['idx'] for s in spikes)
        
        for spike in spikes:
            idx = spike['idx']
            if idx + 5 >= n:
                continue
            
            entry = closes[idx+1]
            direction = 1 if spike['type'] == 'crash' else -1  # LONG for crash, SHORT for boom
            
            # Track position
            exit_price = None
            exit_reason = 'timeout'
            
            for bar in range(2, 8):  # Check bars 2-7
                if idx + bar >= n:
                    break
                
                # Calculate acceleration
                if idx + bar >= 10:
                    drift_10 = np.mean(velocity[idx+bar-10:idx+bar])
                    drift_3 = np.mean(velocity[idx+bar-3:idx+bar])
                    
                    # Acceleration = how much faster is recent drift vs 10-bar drift
                    if abs(drift_10) > 0.001:
                        acceleration = abs(drift_3) / abs(drift_10)
                    else:
                        acceleration = 0
                    
                    # Check if acceleration exceeds threshold
                    # For CRASH: acceleration DOWN is dangerous (spike coming)
                    # For BOOM: acceleration UP is dangerous (spike coming)
                    if direction == 1:  # LONG position (after crash)
                        # Danger: drift accelerating DOWN
                        if drift_3 < 0 and acceleration > accel_threshold:
                            exit_price = closes[idx+bar]
                            exit_reason = 'accel_exit'
                            early_exits += 1
                            break
                    else:  # SHORT position (after boom)
                        # Danger: drift accelerating UP
                        if drift_3 > 0 and acceleration > accel_threshold:
                            exit_price = closes[idx+bar]
                            exit_reason = 'accel_exit'
                            early_exits += 1
                            break
            
            # Timeout exit
            if exit_price is None:
                exit_price = closes[idx+5]
            
            # Calculate PnL
            if direction == 1:
                pnl = exit_price - entry
            else:
                pnl = entry - exit_price
            
            total_pnl += pnl
            if pnl > 0:
                wins += 1
                streak = 0
            else:
                losses += 1
                streak += 1
                max_consec = max(max_consec, streak)
        
        total = wins + losses
        if total > 0:
            results[accel_threshold] = {
                'trades': total,
                'wr': wins/total*100,
                'pnl': total_pnl,
                'early_exits': early_exits,
                'early_exit_pct': early_exits/total*100,
                'max_consec': max_consec
            }
    
    return results

def test_m1_extreme_filter(df, spikes):
    """
    Test: AVOID trading when M1 z-score is extreme (>2).
    51-54% of spikes happen at M1 extremes.
    If we skip these, we avoid half the spikes.
    """
    closes = df['close'].values
    n = len(closes)
    
    results = {}
    
    for z_threshold in [1.5, 2.0, 2.5, 3.0]:
        # Calculate M1 z-score
        window = 20
        z_scores = np.full(n, 0.0)
        for i in range(window, n):
            mean = np.mean(closes[i-window:i])
            std = np.std(closes[i-window:i])
            if std > 0:
                z_scores[i] = (closes[i] - mean) / std
        
        # Test: only enter when |z| < threshold
        wins = losses = 0
        total_pnl = 0
        skipped = 0
        
        for spike in spikes:
            idx = spike['idx']
            if idx + 4 >= n:
                continue
            
            # Check z-score at spike
            if abs(z_scores[idx]) > z_threshold:
                skipped += 1
                continue
            
            entry = closes[idx+1]
            exit_p = closes[idx+4]
            
            if spike['type'] == 'crash':
                pnl = exit_p - entry
            else:
                pnl = entry - exit_p
            
            total_pnl += pnl
            if pnl > 0: wins += 1
            else: losses += 1
        
        total = wins + losses
        if total > 0:
            results[z_threshold] = {
                'trades': total,
                'skipped': skipped,
                'wr': wins/total*100,
                'pnl': total_pnl
            }
    
    return results

def test_price_level_filter(df, spikes):
    """
    Test: AVOID trading when price is at extreme levels.
    Spikes happen more at price extremes.
    """
    closes = df['close'].values
    n = len(closes)
    
    # Normalize prices to 0-1
    price_min = np.min(closes)
    price_max = np.max(closes)
    normalized = (closes - price_min) / (price_max - price_min) if price_max > price_min else np.zeros(n)
    
    results = {}
    
    for extreme_pct in [10, 15, 20, 25]:
        # Test: only enter when price is NOT at extreme levels
        wins = losses = 0
        total_pnl = 0
        skipped = 0
        
        for spike in spikes:
            idx = spike['idx']
            if idx + 4 >= n:
                continue
            
            # Check if price is at extreme
            if normalized[idx] < extreme_pct/100 or normalized[idx] > (1 - extreme_pct/100):
                skipped += 1
                continue
            
            entry = closes[idx+1]
            exit_p = closes[idx+4]
            
            if spike['type'] == 'crash':
                pnl = exit_p - entry
            else:
                pnl = entry - exit_p
            
            total_pnl += pnl
            if pnl > 0: wins += 1
            else: losses += 1
        
        total = wins + losses
        if total > 0:
            results[extreme_pct] = {
                'trades': total,
                'skipped': skipped,
                'wr': wins/total*100,
                'pnl': total_pnl
            }
    
    return results

# Run tests
print("=" * 80)
print("GROUNDBREAKING EXPLOIT TESTS")
print("=" * 80)

for sym in ['crash1000', 'boom1000']:
    df = load_candles(sym)
    if df is None:
        continue
    
    spikes = find_spikes(df)
    print(f"\n{'='*80}")
    print(f"{sym.upper()} - {len(spikes)} spikes")
    print(f"{'='*80}")
    
    # Test 1: Drift acceleration exit
    print(f"\n--- EXPLOIT A: DRIFT ACCELERATION EXIT ---")
    print(f"Exit when drift accelerates X times before spike")
    accel_results = test_drift_acceleration_exit(df, spikes)
    for thresh, r in sorted(accel_results.items()):
        print(f"  Accel>{thresh:.1f}x: {r['trades']:>5}T WR={r['wr']:.1f}% PnL={r['pnl']:>8.2f} "
              f"EarlyExits={r['early_exits']}({r['early_exit_pct']:.0f}%) MaxCL={r['max_consec']}")
    
    # Test 2: M1 extreme filter
    print(f"\n--- EXPLOIT B: M1 EXTREME FILTER ---")
    print(f"Skip trades when M1 z-score is extreme")
    extreme_results = test_m1_extreme_filter(df, spikes)
    for thresh, r in sorted(extreme_results.items()):
        print(f"  |z|<{thresh:.1f}: {r['trades']:>5}T WR={r['wr']:.1f}% PnL={r['pnl']:>8.2f} "
              f"Skipped={r['skipped']}({r['skipped']/(r['trades']+r['skipped'])*100:.0f}%)")
    
    # Test 3: Price level filter
    print(f"\n--- EXPLOIT C: PRICE LEVEL FILTER ---")
    print(f"Skip trades when price is at extreme levels")
    level_results = test_price_level_filter(df, spikes)
    for pct, r in sorted(level_results.items()):
        print(f"  Skip top/bottom {pct}%: {r['trades']:>5}T WR={r['wr']:.1f}% PnL={r['pnl']:>8.2f} "
              f"Skipped={r['skipped']}({r['skipped']/(r['trades']+r['skipped'])*100:.0f}%)")

print("\n" + "=" * 80)
print("EXPLOIT TESTING COMPLETE")
print("=" * 80)
