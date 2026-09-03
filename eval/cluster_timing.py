#!/usr/bin/env python3
"""
CLUSTER TIMING ANALYSIS
How many bars between spikes? When do clusters start?
What's the optimal delay to avoid clusters?
"""
import pandas as pd
import numpy as np
from pathlib import Path

def analyze_timing(symbol, slug):
    print(f"\n{'='*80}")
    print(f"  CLUSTER TIMING: {symbol}")
    print(f"{'='*80}")
    
    path = Path("data") / f"candles_{slug}_1min.parquet"
    df = pd.read_parquet(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["bar_range"] = df["high"] - df["low"]
    nb = len(df)
    
    df["median_range"] = df["bar_range"].rolling(100, min_periods=20).median()
    df["is_spike"] = df["bar_range"] > 3.0 * df["median_range"]
    
    up_pct = (df["close"] > df["open"]).mean()
    drift_up = up_pct > 0.5
    
    spikes = df[df["is_spike"]]
    if drift_up:
        target_idx = spikes[spikes["close"] < spikes["open"]].index.tolist()
    else:
        target_idx = spikes[spikes["close"] > spikes["open"]].index.tolist()
    
    target_pos = [df.index.get_loc(i) for i in target_idx]
    print(f"  Target spikes: {len(target_pos)}")
    
    # ═══════════════════════════════════════════════════════════════
    # ALL GAPS BETWEEN CONSECUTIVE SPIKES
    # ═══════════════════════════════════════════════════════════════
    gaps = [target_pos[i] - target_pos[i-1] for i in range(1, len(target_pos))]
    
    print(f"\n  ALL GAPS BETWEEN SPIKES")
    print(f"  {'-'*60}")
    print(f"  Total gaps: {len(gaps)}")
    print(f"  Mean: {np.mean(gaps):.1f} bars")
    print(f"  Median: {np.median(gaps):.1f} bars")
    print(f"  Std: {np.std(gaps):.1f} bars")
    
    print(f"\n  Gap distribution:")
    for g in range(1, 31):
        count = sum(1 for gap in gaps if gap == g)
        pct = count / len(gaps)
        bar = "#" * int(pct * 100)
        if count > 0:
            print(f"    {g:2d} bars: {count:5d} ({pct:.1%}) {bar}")
    
    # ═══════════════════════════════════════════════════════════════
    # CLASSIFY: STANDALONE vs FIRST-IN-CLUSTER vs IN-CLUSTER
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  SPIKE CLASSIFICATION")
    print(f"  {'-'*60}")
    
    standalone_gaps = []  # gaps BEFORE standalone spikes (safe to enter)
    first_cluster_gaps = []  # gaps BEFORE first spike in cluster (dangerous)
    in_cluster_gaps = []  # gaps WITHIN cluster (very dangerous)
    
    for i in range(1, len(target_pos)):
        gap = target_pos[i] - target_pos[i-1]
        
        # Is this spike the start of a cluster?
        is_cluster_start = False
        if i + 1 < len(target_pos):
            next_gap = target_pos[i+1] - target_pos[i]
            if next_gap <= 5:
                is_cluster_start = True
        
        # Is this spike inside a cluster?
        prev_gap = target_pos[i] - target_pos[i-1]
        in_cluster = prev_gap <= 5
        
        if in_cluster:
            in_cluster_gaps.append(gap)
        elif is_cluster_start:
            first_cluster_gaps.append(gap)
        else:
            standalone_gaps.append(gap)
    
    print(f"  Standalone spikes: {len(standalone_gaps)}")
    print(f"  First-in-cluster: {len(first_cluster_gaps)}")
    print(f"  In-cluster: {len(in_cluster_gaps)}")
    
    if standalone_gaps:
        print(f"\n  Gaps BEFORE standalone (SAFE):")
        print(f"    Mean: {np.mean(standalone_gaps):.1f} bars")
        print(f"    Median: {np.median(standalone_gaps):.1f} bars")
        print(f"    Distribution:")
        for g in range(1, 31):
            count = sum(1 for gap in standalone_gaps if gap == g)
            pct = count / len(standalone_gaps)
            if count > 0:
                print(f"      {g:2d} bars: {count:5d} ({pct:.1%})")
    
    if first_cluster_gaps:
        print(f"\n  Gaps BEFORE first-in-cluster (DANGER):")
        print(f"    Mean: {np.mean(first_cluster_gaps):.1f} bars")
        print(f"    Median: {np.median(first_cluster_gaps):.1f} bars")
        print(f"    Distribution:")
        for g in range(1, 31):
            count = sum(1 for gap in first_cluster_gaps if gap == g)
            pct = count / len(first_cluster_gaps)
            if count > 0:
                print(f"      {g:2d} bars: {count:5d} ({pct:.1%})")
    
    if in_cluster_gaps:
        print(f"\n  Gaps WITHIN cluster (VERY DANGER):")
        print(f"    Mean: {np.mean(in_cluster_gaps):.1f} bars")
        print(f"    Median: {np.median(in_cluster_gaps):.1f} bars")
        print(f"    Distribution:")
        for g in range(1, 6):
            count = sum(1 for gap in in_cluster_gaps if gap == g)
            pct = count / len(in_cluster_gaps)
            if count > 0:
                print(f"      {g:2d} bars: {count:5d} ({pct:.1%})")
    
    # ═══════════════════════════════════════════════════════════════
    # CLUSTER PROBABILITY BY GAP SIZE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  CLUSTER PROBABILITY BY GAP SIZE")
    print(f"  {'-'*60}")
    
    for gap_threshold in range(1, 21):
        # Count how many times a spike at gap <= threshold is followed by another spike
        followed = 0
        total = 0
        for i in range(len(target_pos) - 1):
            gap = target_pos[i+1] - target_pos[i]
            if gap <= gap_threshold:
                total += 1
                # Check if there's a spike after this one too (within 5 bars)
                if i + 2 < len(target_pos):
                    next_gap = target_pos[i+2] - target_pos[i+1]
                    if next_gap <= 5:
                        followed += 1
        
        if total > 0:
            pct = followed / total
            print(f"    Gap <= {gap_threshold:2d}: {total:5d} spikes, {followed:5d} followed by cluster ({pct:.1%})")
    
    # ═══════════════════════════════════════════════════════════════
    # OPTIMAL ENTRY DELAY (skip N bars after last spike)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  OPTIMAL ENTRY DELAY")
    print(f"  {'-'*60}")
    
    closes = df["close"].values
    
    for skip in [0, 1, 2, 3, 5, 7, 10]:
        entry_pos = []
        cluster_avoided = 0
        total_skipped = 0
        
        for i in range(len(target_pos)):
            pos = target_pos[i]
            
            # Check if we should skip this entry
            if i > 0 and (pos - target_pos[i-1]) < skip:
                total_skipped += 1
                # Would this have been a cluster hit?
                if i + 1 < len(target_pos) and (target_pos[i+1] - pos) <= 5:
                    cluster_avoided += 1
                continue
            
            entry_pos.append(pos)
        
        # Calculate PnL
        profits = []
        for pos in entry_pos:
            hold = 5
            if pos + hold >= nb:
                continue
            entry = closes[pos]
            exit_ = closes[pos + hold]
            if drift_up:
                profits.append(entry - exit_)
            else:
                profits.append(exit_ - entry)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            print(f"    Skip<{skip:2d}: Trades={len(profits)} WR={wr:.1%} PF={pf:.2f} PnL={sum(profits):.4f} Avoided={cluster_avoided}/{total_skipped}")
    
    # ═══════════════════════════════════════════════════════════════
    # CLUSTER SIZE PREDICTION (after 2 spikes, how big will cluster get?)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  CLUSTER SIZE PREDICTION")
    print(f"  {'-'*60}")
    
    # After seeing 2 spikes in a row, what's the probability of a 3rd?
    two_in_row = 0
    two_then_three = 0
    two_then_four = 0
    two_then_five = 0
    
    for i in range(len(target_pos) - 2):
        if (target_pos[i+1] - target_pos[i]) <= 5:
            two_in_row += 1
            if (target_pos[i+2] - target_pos[i+1]) <= 5:
                two_then_three += 1
                if i + 3 < len(target_pos) and (target_pos[i+3] - target_pos[i+2]) <= 5:
                    two_then_four += 1
                    if i + 4 < len(target_pos) and (target_pos[i+4] - target_pos[i+3]) <= 5:
                        two_then_five += 1
    
    if two_in_row > 0:
        print(f"  After 2 spikes in a row:")
        print(f"    Probability of 3rd: {two_then_three/two_in_row:.1%}")
        print(f"    Probability of 4th: {two_then_four/two_in_row:.1%}")
        print(f"    Probability of 5th: {two_then_five/two_in_row:.1%}")
    
    # After seeing 3 spikes in a row
    three_in_row = 0
    three_then_four = 0
    
    for i in range(len(target_pos) - 3):
        if (target_pos[i+1] - target_pos[i]) <= 5 and (target_pos[i+2] - target_pos[i+1]) <= 5:
            three_in_row += 1
            if (target_pos[i+3] - target_pos[i+2]) <= 5:
                three_then_four += 1
    
    if three_in_row > 0:
        print(f"  After 3 spikes in a row:")
        print(f"    Probability of 4th: {three_then_four/three_in_row:.1%}")
    
    # ═══════════════════════════════════════════════════════════════
    # QUIET PERIOD AFTER CLUSTER
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  QUIET PERIOD AFTER CLUSTER")
    print(f"  {'-'*60}")
    
    # Find end of each cluster (last spike before gap > 5)
    cluster_ends = []
    current_cluster = [target_pos[0]]
    for i in range(1, len(target_pos)):
        if target_pos[i] - target_pos[i-1] <= 5:
            current_cluster.append(target_pos[i])
        else:
            if len(current_cluster) > 1:
                cluster_ends.append(current_cluster[-1])
            current_cluster = [target_pos[i]]
    if len(current_cluster) > 1:
        cluster_ends.append(current_cluster[-1])
    
    quiet_periods = []
    for i in range(len(cluster_ends) - 1):
        quiet = target_pos[target_pos.index(cluster_ends[i]) + 1] - cluster_ends[i]
        quiet_periods.append(quiet)
    
    if quiet_periods:
        print(f"  Avg quiet period after cluster: {np.mean(quiet_periods):.1f} bars")
        print(f"  Median: {np.median(quiet_periods):.1f} bars")
        print(f"  Distribution:")
        for q in range(1, 31):
            count = sum(1 for p in quiet_periods if p == q)
            pct = count / len(quiet_periods)
            if count > 0:
                print(f"    {q:2d} bars: {count:5d} ({pct:.1%})")
    
    return {
        "gaps": gaps,
        "standalone_gaps": standalone_gaps,
        "first_cluster_gaps": first_cluster_gaps,
        "quiet_periods": quiet_periods
    }

# RUN ALL SYMBOLS
print("="*80)
print("  CLUSTER TIMING ANALYSIS")
print("="*80)

results = {}
for sym, slug in [("BOOM1000","boom1000"),("CRASH1000","crash1000"),
                   ("BOOM500","boom500"),("CRASH500","crash500")]:
    try:
        results[sym] = analyze_timing(sym, slug)
    except Exception as e:
        print(f"\n  ERROR {sym}: {e}")
        import traceback
        traceback.print_exc()

# CROSS-SYMBOL SUMMARY
print(f"\n{'='*80}")
print(f"  CROSS-SYMBOL SUMMARY")
print(f"{'='*80}")

for sym, r in results.items():
    print(f"\n  {sym}:")
    print(f"    Avg gap: {np.mean(r['gaps']):.1f} bars")
    print(f"    Median gap: {np.median(r['gaps']):.1f} bars")
    if r['standalone_gaps']:
        print(f"    Safe gap (standalone): {np.mean(r['standalone_gaps']):.1f} bars")
    if r['first_cluster_gaps']:
        print(f"    Danger gap (cluster start): {np.mean(r['first_cluster_gaps']):.1f} bars")
    if r['quiet_periods']:
        print(f"    Quiet after cluster: {np.mean(r['quiet_periods']):.1f} bars")
