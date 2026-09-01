"""
eval/backtest_velocity_exit.py — Measure buildup ticks before each spike
"""
import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    print("\n" + "=" * 70)
    print("  VELOCITY EXIT — BUILDUP ANALYSIS")
    print("=" * 70)
    
    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        path = Path(f"data/ticks_{symbol.lower().replace(' ', '_')}.npy")
        prices = np.load(path)
        is_crash = "crash" in symbol.lower()
        
        # Detect spikes
        changes = np.abs(np.diff(prices))
        positive = changes[changes > 0]
        median = np.median(positive)
        threshold = median * 3.0
        spike_indices = np.where(changes > threshold)[0]
        
        print(f"\n{'='*70}")
        print(f"  {symbol} — {len(prices):,} ticks, {len(spike_indices):,} spikes")
        print(f"{'='*70}")
        
        # For each spike, count consecutive buildup ticks BEFORE it
        buildups = []
        for idx in spike_indices:
            count = 0
            # Walk backwards from spike
            for j in range(idx - 1, max(idx - 100, -1), -1):
                if j < 0:
                    break
                tick_change = prices[j + 1] - prices[j]
                if is_crash:
                    if tick_change > 0:  # uptick = buildup
                        count += 1
                    else:
                        break
                else:
                    if tick_change < 0:  # downtick = buildup
                        count += 1
                    else:
                        break
            buildups.append(count)
        
        buildups = np.array(buildups)
        
        print(f"\n  BUILDUP TICKS BEFORE SPIKE:")
        print(f"    Mean:   {buildups.mean():.1f}")
        print(f"    Median: {np.median(buildups):.1f}")
        print(f"    Max:    {buildups.max()}")
        print(f"    Std:    {buildups.std():.1f}")
        
        # Distribution
        print(f"\n  DISTRIBUTION:")
        for t in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30]:
            count = (buildups >= t).sum()
            pct = count / len(buildups) * 100
            print(f"    >= {t:2d} ticks: {count:6d} ({pct:5.1f}%)")
        
        # What % of spikes would we catch at each threshold?
        print(f"\n  SPIKE CATCH RATE (what % of spikes have N+ consecutive buildup ticks):")
        for t in [5, 6, 7, 8, 10, 12, 15, 20, 25, 30]:
            caught = (buildups >= t).sum()
            pct = caught / len(buildups) * 100
            missed = len(buildups) - caught
            print(f"    Threshold {t:2d}: catch {pct:5.1f}% ({caught}/{len(buildups)}), miss {missed}")
        
        # False positive rate: how often do we see N+ buildup ticks WITHOUT a spike coming?
        # Count max consecutive buildup runs in the full data
        tick_changes = np.diff(prices)
        if is_crash:
            is_buildup = tick_changes > 0
        else:
            is_buildup = tick_changes < 0
        
        # Find runs of consecutive True
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
        
        # For each threshold, how many runs would trigger? (false positives if no spike follows)
        print(f"\n  FALSE POSITIVE ANALYSIS:")
        print(f"    Total buildup runs: {len(runs)}")
        print(f"    Total spikes: {len(spike_indices)}")
        print(f"    Spikes per run: {len(spike_indices)/len(runs):.2f}" if len(runs) > 0 else "")
        
        for t in [5, 6, 7, 8, 10, 12, 15, 20, 25, 30]:
            triggering_runs = (runs >= t).sum()
            ratio = len(spike_indices) / triggering_runs if triggering_runs > 0 else float('inf')
            print(f"    Threshold {t:2d}: {triggering_runs:5d} runs trigger, ratio={ratio:.2f} spikes/trigger")
    
    print(f"\n{'='*70}")
    print(f"  CONCLUSION")
    print(f"{'='*70}")
    print("""
  The velocity exit detects consecutive buildup ticks BEFORE a spike.
  
  For CRASH: uptick buildup before crash spike
  For BOOM:  downtick buildup before boom spike
  
  The EA tracks this on every tick. When buildup >= threshold,
  close the position IMMEDIATELY — before the spike hits.
  
  This is the contingency plan: if we're in a trade and the
  buildup pattern appears, we get out before damage occurs.
""")


if __name__ == "__main__":
    main()
