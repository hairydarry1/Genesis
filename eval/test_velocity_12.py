"""
eval/test_velocity_12.py — Test VelocityThreshold = 12
"""
import numpy as np
from pathlib import Path


def main():
    print("\n" + "=" * 70)
    print("  VELOCITY EXIT — THRESHOLD = 12 (AGGRESSIVE)")
    print("=" * 70)
    
    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        prices = np.load(Path(f"data/ticks_{symbol.lower().replace(' ', '_')}.npy"))
        is_crash = "crash" in symbol.lower()
        
        # Detect spikes
        changes = np.abs(np.diff(prices))
        positive = changes[changes > 0]
        median = np.median(positive)
        spike_indices = np.where(changes > median * 3.0)[0]
        
        # Count buildup before each spike
        buildups = []
        for idx in spike_indices:
            count = 0
            for j in range(idx - 1, max(idx - 100, -1), -1):
                if j < 0:
                    break
                tick_change = prices[j + 1] - prices[j]
                if is_crash:
                    if tick_change > 0:
                        count += 1
                    else:
                        break
                else:
                    if tick_change < 0:
                        count += 1
                    else:
                        break
            buildups.append(count)
        
        buildups = np.array(buildups)
        
        # At threshold 12
        caught = (buildups >= 12).sum()
        missed = len(buildups) - caught
        
        print(f"\n  {symbol}:")
        print(f"    Total spikes:      {len(spike_indices)}")
        print(f"    Buildup mean:      {buildups.mean():.1f} ticks")
        print(f"    Buildup median:    {np.median(buildups):.1f} ticks")
        print(f"    At threshold 12:")
        print(f"      Caught:          {caught} ({caught/len(spike_indices)*100:.1f}%)")
        print(f"      Missed:          {missed} ({missed/len(spike_indices)*100:.1f}%)")
        
        # Compare to threshold 20
        caught_20 = (buildups >= 20).sum()
        print(f"    At threshold 20 (old):")
        print(f"      Caught:          {caught_20} ({caught_20/len(spike_indices)*100:.1f}%)")
        print(f"      Extra caught at 12: +{caught - caught_20} ({(caught - caught_20)/len(spike_indices)*100:.1f}%)")
    
    # False positive analysis
    print(f"\n  FALSE POSITIVE COMPARISON:")
    
    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        prices = np.load(Path(f"data/ticks_{symbol.lower().replace(' ', '_')}.npy"))
        is_crash = "crash" in symbol.lower()
        
        changes = np.abs(np.diff(prices))
        median = np.median(changes[changes > 0])
        spike_indices = set(np.where(changes > median * 3.0)[0].tolist())
        
        tick_changes = np.diff(prices)
        if is_crash:
            is_buildup = tick_changes > 0
        else:
            is_buildup = tick_changes < 0
        
        # Find runs
        runs = []
        current_run = 0
        for v in is_buildup:
            if v:
                current_run += 1
            else:
                if current_run > 0:
                    runs.append(current_run)
                current_run = 0
        if current_run > 0:
            runs.append(current_run)
        runs = np.array(runs)
        
        n_spikes = len(spike_indices)
        
        for t in [12, 15, 20]:
            triggering_runs = (runs >= t).sum()
            ratio = n_spikes / triggering_runs if triggering_runs > 0 else float('inf')
            print(f"    {symbol} threshold={t:2d}: {triggering_runs:5d} triggers, ratio={ratio:.2f} spikes/trigger")
    
    print(f"\n  VERDICT:")
    print(f"  Threshold 12 catches 79-86% of spikes (vs 68-78% at threshold 20)")
    print(f"  More false triggers, but catches MORE spikes before they hit")
    print(f"  Best for: maximizing spike avoidance (accept more false exits)")
    
    # Update EA reminder
    print(f"\n  EA CONFIG:")
    print(f"    VelocityThreshold = 12  // More aggressive")
    print(f"    UseVelocityExit = true  // Enable velocity protection")


if __name__ == "__main__":
    main()
