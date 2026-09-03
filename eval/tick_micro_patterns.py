#!/usr/bin/env python3
"""
TICK MICRO-PATTERN ANALYSIS
What happens at the TICK level that M1 bar indicators miss?
Tests: direction patterns, speed, acceleration, entropy, correlation.
"""
import numpy as np
import pandas as pd
from pathlib import Path
from scipy import stats

def analyze(symbol, npy_file):
    print(f"\n{'='*80}")
    print(f"  TICK MICRO-PATTERNS: {symbol}")
    print(f"{'='*80}")
    
    prices = np.load(npy_file).flatten()
    n = len(prices)
    print(f"  Total ticks: {n:,}")
    
    # Detect spikes at tick level
    # A spike = price moves > 3x the median tick-to-tick change
    changes = np.diff(prices)
    abs_changes = np.abs(changes)
    
    # Rolling median of absolute changes
    window = 1000
    median_change = pd.Series(abs_changes).rolling(window, min_periods=100).median().values
    
    # Spike threshold
    spike_threshold = 3.0
    spike_ticks = np.where(abs_changes > spike_threshold * median_change)[0]
    
    # Determine direction
    spike_direction = changes[spike_ticks]  # positive = up, negative = down
    
    # For BOOM: spikes go UP (positive)
    # For CRASH: spikes go DOWN (negative)
    up_spikes = np.sum(spike_direction > 0)
    down_spikes = np.sum(spike_direction < 0)
    
    if up_spikes > down_spikes:
        drift_down = True  # BOOM: drift down, spikes up
        target_spikes = spike_ticks[spike_direction > 0]
        print(f"  BOOM-type: drift DOWN, spikes UP")
    else:
        drift_down = False  # CRASH: drift up, spikes down
        target_spikes = spike_ticks[spike_direction < 0]
        print(f"  CRASH-type: drift UP, spikes DOWN")
    
    print(f"  Total spikes: {len(spike_ticks)} (up={up_spikes}, down={down_spikes})")
    print(f"  Target spikes: {len(target_spikes)}")
    
    if len(target_spikes) < 100:
        print("  NOT ENOUGH SPIKES")
        return
    
    # Random ticks for comparison
    np.random.seed(42)
    random_ticks = np.random.choice(range(10000, n-10000), min(5000, n-20000), replace=False)
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 1: TICK DIRECTION SEQUENCE BEFORE SPIKE
    # How many consecutive same-direction ticks before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 1: CONSECUTIVE DIRECTION BEFORE SPIKE")
    print(f"  {'-'*60}")
    
    def count_consec_ticks(idx, lookback=50):
        """Count consecutive ticks in drift direction before idx."""
        if drift_down:
            # Drift = down (price decreases), so look for consecutive negative changes
            direction = -1
        else:
            # Drift = up (price increases), so look for consecutive positive changes
            direction = 1
        
        count = 0
        for j in range(idx-1, max(0, idx-lookback), -1):
            if changes[j] * direction > 0:  # Same as drift
                count += 1
            else:
                break
        return count
    
    # Before spikes
    consec_spike = []
    for idx in target_spikes:
        if idx < 50:
            continue
        consec_spike.append(count_consec_ticks(idx))
    
    # Before random ticks
    consec_random = []
    for idx in random_ticks:
        if idx < 50:
            continue
        consec_random.append(count_consec_ticks(idx))
    
    print(f"  Avg consecutive ticks before SPIKE: {np.mean(consec_spike):.1f}")
    print(f"  Avg consecutive ticks at RANDOM:    {np.mean(consec_random):.1f}")
    print(f"  Ratio: {np.mean(consec_spike)/max(np.mean(consec_random),0.01):.2f}x")
    
    for n_tick in [3, 5, 10, 15, 20]:
        pct_spike = np.mean([c >= n_tick for c in consec_spike])
        pct_random = np.mean([c >= n_tick for c in consec_random])
        lift = pct_spike / max(pct_random, 0.001)
        print(f"    >= {n_tick:2d} ticks: SPIKE={pct_spike:.1%} RAND={pct_random:.1%} LIFT={lift:.2f}x")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 2: TICK SIZE CHANGES BEFORE SPIKE
    # Do ticks get bigger/smaller before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 2: TICK SIZE CHANGES BEFORE SPIKE")
    print(f"  {'-'*60}")
    
    lookback = 100
    
    # Average tick size in windows before spike
    for offset in [1, 5, 10, 20, 50, 100]:
        sizes_spike = []
        sizes_random = []
        
        for idx in target_spikes:
            if idx < lookback:
                continue
            start = max(0, idx - offset - 10)
            end = max(0, idx - offset)
            if start < end:
                window_changes = abs_changes[start:end]
                if len(window_changes) > 0:
                    sizes_spike.append(np.mean(window_changes))
        
        for idx in random_ticks:
            if idx < lookback:
                continue
            start = max(0, idx - offset - 10)
            end = max(0, idx - offset)
            if start < end:
                window_changes = abs_changes[start:end]
                if len(window_changes) > 0:
                    sizes_random.append(np.mean(window_changes))
        
        if sizes_spike and sizes_random:
            avg_s = np.mean(sizes_spike)
            avg_r = np.mean(sizes_random)
            lift = avg_s / max(avg_r, 0.0001)
            print(f"  {offset:3d} ticks before: SPIKE={avg_s:.6f} RAND={avg_r:.6f} LIFT={lift:.2f}x")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 3: TICK SPEED (changes per second)
    # Does the rate of price changes change before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 3: TICK SPEED (volatility clustering)")
    print(f"  {'-'*60}")
    
    # Calculate rolling volatility at tick level
    vol_window = 100
    rolling_vol = pd.Series(abs_changes).rolling(vol_window, min_periods=10).std().values
    
    for offset in [10, 50, 100, 500]:
        vol_spike = []
        vol_random = []
        
        for idx in target_spikes:
            if idx >= offset and idx < n:
                vol_spike.append(rolling_vol[idx - offset])
        
        for idx in random_ticks:
            if idx >= offset and idx < n:
                vol_spike_val = rolling_vol[idx - offset]
                if not np.isnan(vol_spike_val):
                    vol_random.append(vol_spike_val)
        
        if vol_spike and vol_random:
            avg_s = np.nanmean(vol_spike)
            avg_r = np.nanmean(vol_random)
            lift = avg_s / max(avg_r, 0.0001)
            print(f"  {offset:4d} ticks before: SPIKE={avg_s:.6f} RAND={avg_r:.6f} LIFT={lift:.2f}x")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 4: TICK ACCELERATION (is price accelerating?)
    # Does the 2nd derivative change before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 4: TICK ACCELERATION (2nd derivative)")
    print(f"  {'-'*60}")
    
    # Acceleration = change of change
    acceleration = np.diff(changes)
    
    for offset in [10, 50, 100]:
        accel_spike = []
        accel_random = []
        
        for idx in target_spikes:
            if idx >= offset + 1 and idx < n:
                accel_spike.append(acceleration[idx - offset - 1:idx - offset].mean())
        
        for idx in random_ticks:
            if idx >= offset + 1 and idx < n:
                val = acceleration[idx - offset - 1:idx - offset].mean()
                if not np.isnan(val):
                    accel_random.append(val)
        
        if accel_spike and accel_random:
            avg_s = np.nanmean(accel_spike)
            avg_r = np.nanmean(accel_random)
            print(f"  {offset:4d} ticks before: SPIKE accel={avg_s:.8f} RAND accel={avg_r:.8f}")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 5: TICK ENTROPY (randomness)
    # Is the market more/less random before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 5: TICK ENTROPY (randomness)")
    print(f"  {'-'*60}")
    
    def tick_entropy(changes_chunk):
        """Calculate entropy of tick direction sequence."""
        signs = (changes_chunk > 0).astype(int)
        p1 = signs.mean()
        p0 = 1 - p1
        if p0 <= 0 or p1 <= 0:
            return 1.0  # Maximum entropy
        return -(p0 * np.log2(p0) + p1 * np.log2(p1))
    
    for offset in [50, 100, 500]:
        ent_spike = []
        ent_random = []
        
        for idx in target_spikes:
            if idx >= offset and idx < n:
                chunk = changes[idx-offset:idx]
                ent_spike.append(tick_entropy(chunk))
        
        for idx in random_ticks:
            if idx >= offset and idx < n:
                chunk = changes[idx-offset:idx]
                ent_random.append(tick_entropy(chunk))
        
        if ent_spike and ent_random:
            avg_s = np.mean(ent_spike)
            avg_r = np.mean(ent_random)
            print(f"  {offset:4d} ticks before: SPIKE entropy={avg_s:.4f} RAND entropy={avg_r:.4f} diff={avg_s-avg_r:+.4f}")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 6: TICK AUTOCORRELATION
    # Are ticks more correlated before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 6: TICK AUTOCORRELATION")
    print(f"  {'-'*60}")
    
    for offset in [100, 500]:
        auto_spike = []
        auto_random = []
        
        for idx in target_spikes:
            if idx >= offset and idx < n:
                chunk = changes[idx-offset:idx]
                if len(chunk) > 1:
                    corr = np.corrcoef(chunk[:-1], chunk[1:])[0, 1]
                    if not np.isnan(corr):
                        auto_spike.append(corr)
        
        for idx in random_ticks:
            if idx >= offset and idx < n:
                chunk = changes[idx-offset:idx]
                if len(chunk) > 1:
                    corr = np.corrcoef(chunk[:-1], chunk[1:])[0, 1]
                    if not np.isnan(corr):
                        auto_random.append(corr)
        
        if auto_spike and auto_random:
            avg_s = np.mean(auto_spike)
            avg_r = np.mean(auto_random)
            print(f"  {offset:4d} ticks before: SPIKE autocorr={avg_s:.4f} RAND autocorr={avg_r:.4f}")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 7: EXACT SPIKE MOMENT (last 10 ticks)
    # What happens in the 10 ticks right before spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 7: LAST 10 TICKS BEFORE SPIKE")
    print(f"  {'-'*60}")
    
    lookback = 10
    
    # For each tick offset, what's the average change?
    for offset in range(1, lookback + 1):
        changes_spike = []
        changes_random = []
        
        for idx in target_spikes:
            if idx >= offset:
                changes_spike.append(changes[idx - offset])
        
        for idx in random_ticks:
            if idx >= offset:
                changes_random.append(changes[idx - offset])
        
        avg_s = np.mean(changes_spike)
        avg_r = np.mean(changes_random)
        std_s = np.std(changes_spike)
        std_r = np.std(changes_random)
        
        # Direction match with drift
        if drift_down:
            match_s = np.mean([c < 0 for c in changes_spike])
            match_r = np.mean([c < 0 for c in changes_random])
        else:
            match_s = np.mean([c > 0 for c in changes_spike])
            match_r = np.mean([c > 0 for c in changes_random])
        
        print(f"  Tick -{offset:2d}: SPIKE avg={avg_s:+.6f} std={std_s:.6f} drift_match={match_s:.1%} | RAND avg={avg_r:+.6f} drift_match={match_r:.1%}")
    
    # ═══════════════════════════════════════════════════════════════
    # PATTERN 8: POST-SPIKE RECOVERY (tick by tick)
    # How fast does price recover after spike?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PATTERN 8: POST-SPIKE RECOVERY (tick by tick)")
    print(f"  {'-'*60}")
    
    spike_sizes = []
    for idx in target_spikes:
        spike_sizes.append(abs(changes[idx]))
    
    avg_spike_size = np.mean(spike_sizes)
    print(f"  Avg spike size: {avg_spike_size:.4f}")
    
    for offset in range(1, 21):
        recoveries = []
        for idx in target_spikes:
            if idx + offset < n:
                # Price change from spike end to offset ticks later
                recovery = prices[idx + offset] - prices[idx]
                recoveries.append(recovery)
        
        if recoveries:
            avg_recovery = np.mean(recoveries)
            pct_recovered = avg_recovery / avg_spike_size * 100 if avg_spike_size > 0 else 0
            
            # Direction of recovery (should be opposite to spike)
            if drift_down:
                # Spike went UP, recovery should go DOWN (negative)
                correct_dir = np.mean([r < 0 for r in recoveries])
            else:
                # Spike went DOWN, recovery should go UP (positive)
                correct_dir = np.mean([r > 0 for r in recoveries])
            
            print(f"  +{offset:2d} ticks: recovery={avg_recovery:+.6f} ({pct_recovered:+.1f}% of spike) correct_dir={correct_dir:.1%}")

# RUN ALL SYMBOLS
print("="*80)
print("  TICK MICRO-PATTERN ANALYSIS")
print("="*80)

for sym, fn in [("BOOM1000","ticks_boom1000_index.npy"),
                ("CRASH1000","ticks_crash1000_index.npy"),
                ("BOOM500","ticks_boom_500_index.npy"),
                ("CRASH500","ticks_crash_500_index.npy")]:
    try:
        analyze(sym, Path("data") / fn)
    except Exception as e:
        print(f"\n  ERROR {sym}: {e}")
        import traceback
        traceback.print_exc()
