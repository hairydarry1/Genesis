#!/usr/bin/env python3
"""
CHALLENGE ALL FINDINGS
======================
Be the devil's advocate. Try to DISPROVE every finding.
If it survives, it's real. If not, it's noise.
"""

import numpy as np
import pandas as pd
import os
from scipy import stats

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

print("=" * 80)
print("CHALLENGING ALL FINDINGS")
print("=" * 80)

for sym in ['crash1000', 'boom1000']:
    df = load_candles(sym)
    if df is None:
        continue
    
    spikes = find_spikes(df)
    closes = df['close'].values
    opens = df['open'].values
    n = len(closes)
    
    print(f"\n{'='*80}")
    print(f"{sym.upper()} - CHALLENGING")
    print(f"{'='*80}")
    
    # ============================================================
    # CHALLENGE 1: "99.5% of spikes follow 3 consecutive bars"
    # COUNTER: Of course they do - the algorithm ALWAYS drifts
    # before spiking. This is just describing the drift, not
    # predicting the spike. The question is: AFTER 3 consecutive
    # bars, what's the probability of a spike vs just more drift?
    # ============================================================
    print(f"\n--- CHALLENGE 1: CONSECUTIVE BAR PATTERN ---")
    print(f"CLAIM: 99.5% of spikes follow 3 consecutive UP bars")
    print(f"COUNTER: This just describes the drift. The real question is:")
    print(f"  After 3 UP bars, what happens NEXT?")
    
    # Track consecutive UP bars and what happens next
    directions = np.where(closes > opens, 1, -1)
    
    consecutive_up_counts = {}
    for i in range(3, n):
        # Count consecutive UP bars ending at i-1
        count = 0
        for j in range(i-1, max(i-20, -1), -1):
            if directions[j] == 1:
                count += 1
            else:
                break
        
        if count >= 3:
            if count not in consecutive_up_counts:
                consecutive_up_counts[count] = {'total': 0, 'spike_next': 0}
            consecutive_up_counts[count]['total'] += 1
            
            # Check if spike happens at bar i
            if i in set(s['idx'] for s in spikes):
                consecutive_up_counts[count]['spike_next'] += 1
    
    print(f"\n  After N consecutive UP bars, probability of spike at next bar:")
    for count in sorted(consecutive_up_counts.keys()):
        if count >= 3 and count <= 15:
            data = consecutive_up_counts[count]
            prob = data['spike_next'] / data['total'] * 100 if data['total'] > 0 else 0
            print(f"    {count} UP bars: {prob:.2f}% spike prob ({data['spike_next']}/{data['total']})")
    
    # ============================================================
    # CHALLENGE 2: "94% of spikes follow a drift-direction bar"
    # COUNTER: This is just the drift. After ANY UP bar on CRASH,
    # there's a chance of spike. But how does it compare to
    # random?
    # ============================================================
    print(f"\n--- CHALLENGE 2: PRE-SPIKE PATTERN ---")
    print(f"CLAIM: 94% of spikes follow an UP bar (CRASH)")
    print(f"COUNTER: What % of ALL bars are UP? If 80% of bars are UP,")
    print(f"  then 94% is barely above random chance.")
    
    total_up_bars = np.sum(directions == 1)
    total_bars = len(directions)
    pct_up_bars = total_up_bars / total_bars * 100
    
    spike_up_bars = sum(1 for s in spikes if s['idx'] < n and directions[s['idx']-1] == 1)
    pct_spike_up = spike_up_bars / len(spikes) * 100
    
    lift = pct_spike_up / pct_up_bars if pct_up_bars > 0 else 0
    
    print(f"  Total UP bars: {pct_up_bars:.1f}%")
    print(f"  Spike bars preceded by UP: {pct_spike_up:.1f}%")
    print(f"  Lift over random: {lift:.2f}x")
    print(f"  VERDICT: {'REAL' if lift > 1.2 else 'WEAK' if lift > 1.05 else 'NOISE'}")
    
    # ============================================================
    # CHALLENGE 3: "Drift acceleration predicts spikes"
    # COUNTER: The acceleration might just be the spike itself,
    # not a precursor. Let's check if acceleration happens
    # BEFORE the spike or AT the spike.
    # ============================================================
    print(f"\n--- CHALLENGE 3: DRIFT ACCELERATION ---")
    print(f"CLAIM: Drift accelerates 17-18x before spikes")
    print(f"COUNTER: Is this BEFORE the spike or AT the spike?")
    
    velocity = np.diff(closes)
    
    # Check acceleration at different lookbacks
    for lookback in [1, 2, 3, 5, 10]:
        spike_accels = []
        normal_accels = []
        
        for spike in spikes:
            idx = spike['idx']
            if idx >= lookback + 10:
                # Acceleration at the spike bar
                drift_recent = np.mean(velocity[idx-lookback:idx])
                drift_long = np.mean(velocity[idx-10:idx])
                if abs(drift_long) > 0.001:
                    accel = abs(drift_recent) / abs(drift_long)
                else:
                    accel = 0
                spike_accels.append(accel)
        
        # Normal bars
        spike_indices = set(s['idx'] for s in spikes)
        for i in range(lookback + 10, n):
            if i not in spike_indices:
                drift_recent = np.mean(velocity[i-lookback:i])
                drift_long = np.mean(velocity[i-10:i])
                if abs(drift_long) > 0.001:
                    accel = abs(drift_recent) / abs(drift_long)
                else:
                    accel = 0
                normal_accels.append(accel)
        
        spike_mean = np.mean(spike_accels) if spike_accels else 0
        normal_mean = np.mean(normal_accels) if normal_accels else 0
        ratio = spike_mean / normal_mean if normal_mean > 0 else 0
        
        print(f"  Lookback {lookback}: Spike accel={spike_mean:.2f} Normal={normal_mean:.2f} Ratio={ratio:.2f}x")
    
    # ============================================================
    # CHALLENGE 4: "Spread widens before spikes"
    # COUNTER: Spread widens DURING the spike, not before.
    # Let's check spread BEFORE the spike.
    # ============================================================
    print(f"\n--- CHALLENGE 4: SPREAD WIDENING ---")
    print(f"CLAIM: Spread is 6-14x at spikes")
    print(f"COUNTER: Is spread wider BEFORE the spike or AT the spike?")
    
    if 'spread' in df.columns:
        spreads = df['spread'].values
        
        # Check spread at different offsets from spike
        for offset in [-3, -2, -1, 0, 1]:
            spike_spreads = []
            normal_spreads = []
            
            for spike in spikes:
                idx = spike['idx'] + offset
                if 0 <= idx < n:
                    spike_spreads.append(spreads[idx])
            
            spike_indices = set(s['idx'] for s in spikes)
            sample_count = 0
            for i in range(n):
                if i not in spike_indices and 0 <= i + offset < n:
                    normal_spreads.append(spreads[i + offset])
                    sample_count += 1
                    if sample_count >= len(spike_spreads):
                        break
            
            spike_mean = np.mean(spike_spreads) if spike_spreads else 0
            normal_mean = np.mean(normal_spreads) if normal_spreads else 0
            ratio = spike_mean / normal_mean if normal_mean > 0 else 0
            
            label = f"bar{offset:+d}" if offset != 0 else "AT spike"
            print(f"  {label:>10}: Spike={spike_mean:.1f} Normal={normal_mean:.1f} Ratio={ratio:.2f}x")
    
    # ============================================================
    # CHALLENGE 5: "Time-of-day matters"
    # COUNTER: Is the difference statistically significant?
    # With 2226 spikes, even small differences look big.
    # ============================================================
    print(f"\n--- CHALLENGE 5: TIME-OF-DAY ---")
    print(f"CLAIM: Spikes are 1.5x more likely at certain hours")
    print(f"COUNTER: Is this statistically significant?")
    
    if 'time' in df.columns:
        times = df['time'].values
        hours = []
        for t in times:
            try:
                if hasattr(t, 'hour'):
                    hours.append(t.hour)
                else:
                    dt = pd.to_datetime(t)
                    hours.append(dt.hour)
            except:
                hours.append(12)
        hours = np.array(hours)
        
        spike_indices = set(s['idx'] for s in spikes)
        
        # Chi-square test
        observed = np.zeros(24)
        expected = np.zeros(24)
        
        for h in range(24):
            observed[h] = sum(1 for s in spikes if s['idx'] < n and hours[s['idx']] == h)
            expected[h] = len(spikes) * np.sum(hours == h) / n
        
        # Remove zero expected
        mask = expected > 0
        chi2, p_value = stats.chisquare(observed[mask], expected[mask])
        
        print(f"  Chi-square: {chi2:.2f}")
        print(f"  P-value: {p_value:.6f}")
        print(f"  Significant (p<0.05): {'YES' if p_value < 0.05 else 'NO'}")
    
    # ============================================================
    # CHALLENGE 6: "Price level filter helps"
    # COUNTER: Is this overfitting to the specific price range?
    # Test on different time periods.
    # ============================================================
    print(f"\n--- CHALLENGE 6: PRICE LEVEL FILTER ---")
    print(f"CLAIM: Skip top/bottom 20% improves PnL")
    print(f"COUNTER: Is this consistent across time periods?")
    
    price_min = np.min(closes)
    price_max = np.max(closes)
    normalized = (closes - price_min) / (price_max - price_min) if price_max > price_min else np.zeros(n)
    
    # Split into 4 quarters and test each
    q_size = n // 4
    for q in range(4):
        q_start = q * q_size
        q_end = (q + 1) * q_size if q < 3 else n
        
        q_spikes = [s for s in spikes if q_start <= s['idx'] < q_end]
        
        # Without filter
        wins_no = losses_no = 0
        for s in q_spikes:
            idx = s['idx']
            if idx + 4 >= q_end:
                continue
            entry = closes[idx+1]
            exit_p = closes[idx+4]
            pnl = (exit_p - entry) if s['type'] == 'crash' else (entry - exit_p)
            if pnl > 0: wins_no += 1
            else: losses_no += 1
        
        # With filter (skip extremes)
        wins_f = losses_f = 0
        for s in q_spikes:
            idx = s['idx']
            if idx + 4 >= q_end:
                continue
            if normalized[idx] < 0.20 or normalized[idx] > 0.80:
                continue
            entry = closes[idx+1]
            exit_p = closes[idx+4]
            pnl = (exit_p - entry) if s['type'] == 'crash' else (entry - exit_p)
            if pnl > 0: wins_f += 1
            else: losses_f += 1
        
        total_no = wins_no + losses_no
        total_f = wins_f + losses_f
        wr_no = wins_no / total_no * 100 if total_no > 0 else 0
        wr_f = wins_f / total_f * 100 if total_f > 0 else 0
        
        print(f"  Q{q+1}: No filter {wr_no:.1f}% ({total_no}T) | With filter {wr_f:.1f}% ({total_f}T) | Delta {wr_f-wr_no:+.1f}%")
    
    # ============================================================
    # CHALLENGE 7: "Cluster avoidance helps"
    # COUNTER: Does skipping clusters actually improve WR?
    # Or just reduces trades?
    # ============================================================
    print(f"\n--- CHALLENGE 7: CLUSTER AVOIDANCE ---")
    print(f"CLAIM: Skip clusters improves safety")
    print(f"COUNTER: Does it improve WR or just reduce trades?")
    
    # Split spikes into cluster vs normal
    intervals = [spikes[i]['idx'] - spikes[i-1]['idx'] for i in range(1, len(spikes))]
    
    cluster_spikes = []
    normal_spikes = []
    for i, spike in enumerate(spikes):
        if i == 0:
            normal_spikes.append(spike)
            continue
        interval = spike['idx'] - spikes[i-1]['idx']
        if interval < 10:
            cluster_spikes.append(spike)
        else:
            normal_spikes.append(spike)
    
    # Test both
    for name, spike_list in [('Cluster', cluster_spikes), ('Normal', normal_spikes)]:
        wins = losses = 0
        pnl = 0
        for s in spike_list:
            idx = s['idx']
            if idx + 4 >= n:
                continue
            entry = closes[idx+1]
            exit_p = closes[idx+4]
            p = (exit_p - entry) if s['type'] == 'crash' else (entry - exit_p)
            pnl += p
            if p > 0: wins += 1
            else: losses += 1
        
        total = wins + losses
        if total > 0:
            print(f"  {name}: {total}T WR={wins/total*100:.1f}% PnL={pnl:.2f}")

print("\n" + "=" * 80)
print("CHALLENGE COMPLETE")
print("=" * 80)
