#!/usr/bin/env python3
"""
CLUSTER DEFENSE SYSTEM
Deep analysis of spike clusters + test 6 defense strategies.
Goal: survive clusters without losing all profits.
"""
import numpy as np
import pandas as pd
from pathlib import Path

def load_ticks(npy_file):
    return np.load(npy_file).flatten()

def make_bars(prices, window=100):
    n = len(prices) // window
    bars = []
    for i in range(n):
        chunk = prices[i*window:(i+1)*window]
        bars.append({
            "idx": i, "open": chunk[0], "close": chunk[-1],
            "high": chunk.max(), "low": chunk.min(),
            "range": chunk.max() - chunk.min(),
        })
    return pd.DataFrame(bars)

def analyze_cluster(symbol, tick_file):
    print(f"\n{'='*80}")
    print(f"  CLUSTER ANALYSIS: {symbol}")
    print(f"{'='*80}")
    
    prices = load_ticks(tick_file)
    bars = make_bars(prices, 100)
    nb = len(bars)
    
    med = bars["range"].rolling(100, min_periods=20).median()
    bars["is_spike"] = bars["range"] > 3.0 * med
    
    up_pct = (bars["close"] > bars["open"]).mean()
    drift_up = up_pct > 0.5
    
    spikes = bars[bars["is_spike"]]
    if drift_up:
        target = spikes[spikes["close"] < spikes["open"]].index.tolist()
    else:
        target = spikes[spikes["close"] > spikes["open"]].index.tolist()
    
    print(f"  Ticks: {len(prices):,} | Bars: {nb} | Target spikes: {len(target)}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 1: CLUSTER MECHANICS
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PART 1: CLUSTER MECHANICS")
    print(f"  {'-'*60}")
    
    # Define clusters: spikes within 5 bars of each other
    clusters = []
    current = [target[0]]
    for i in range(1, len(target)):
        gap = target[i] - target[i-1]
        if gap <= 5:
            current.append(target[i])
        else:
            clusters.append(current)
            current = [target[i]]
    clusters.append(current)
    
    cluster_sizes = [len(c) for c in clusters]
    cluster_gaps = []
    for c in clusters:
        for i in range(1, len(c)):
            cluster_gaps.append(c[i] - c[i-1])
    
    standalone = [c for c in clusters if len(c) == 1]
    multi = [c for c in clusters if len(c) > 1]
    
    print(f"  Total spike sequences: {len(clusters)}")
    print(f"  Standalone spikes: {len(standalone)} ({len(standalone)/len(clusters):.1%})")
    print(f"  Cluster events: {len(multi)} ({len(multi)/len(clusters):.1%})")
    print(f"  Avg cluster size: {np.mean(cluster_sizes):.1f}")
    print(f"  Max cluster size: {max(cluster_sizes)}")
    print(f"  Median cluster size: {np.median(cluster_sizes):.1f}")
    
    print(f"\n  Cluster size distribution:")
    for n in range(1, 8):
        pct = np.mean([s >= n for s in cluster_sizes])
        print(f"    Size >= {n}: {pct:.1%}")
    
    if cluster_gaps:
        print(f"\n  Gap between spikes within clusters:")
        print(f"    Avg: {np.mean(cluster_gaps):.1f} bars")
        print(f"    Median: {np.median(cluster_gaps):.1f} bars")
        print(f"    Max: {max(cluster_gaps)} bars")
        for g in range(1, 6):
            pct = np.mean([gap <= g for gap in cluster_gaps])
            print(f"    Gap <= {g}: {pct:.1%}")
    
    # ═══════════════════════════════════════════════════════════════
    # What happens AFTER each cluster?
    # How long until the next spike (outside the cluster)?
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  POST-CLUSTER QUIET PERIOD")
    quiet_periods = []
    for i in range(len(clusters) - 1):
        last_spike = clusters[i][-1]
        next_spike = clusters[i+1][0]
        quiet = next_spike - last_spike
        quiet_periods.append(quiet)
    
    if quiet_periods:
        print(f"  Avg quiet period after cluster: {np.mean(quiet_periods):.1f} bars")
        print(f"  Median: {np.median(quiet_periods):.1f} bars")
        for q in [5, 10, 15, 20, 30]:
            pct = np.mean([p >= q for p in quiet_periods])
            print(f"  Quiet >= {q} bars: {pct:.1%}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 2: CLUSTER DAMAGE
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PART 2: CLUSTER DAMAGE (if in position)")
    print(f"  {'-'*60}")
    
    # For each spike, what's the worst case if we entered at the PREVIOUS spike?
    worst_losses = []
    for i in range(1, len(target)):
        entry_bar = target[i-1]
        # Look at all spikes within 10 bars AFTER entry
        for j in range(i, min(i+5, len(target))):
            spike_bar = target[j]
            gap = spike_bar - entry_bar
            if gap <= 10:
                # Price at entry vs price at this spike
                entry_price = bars.iloc[entry_bar]["close"]
                spike_price = bars.iloc[spike_bar]["close"]
                if drift_up:
                    loss = entry_price - spike_price  # LONG position, spike goes UP
                else:
                    loss = spike_price - entry_price  # SHORT position, spike goes DOWN
                
                if loss > 0:  # Loss for us (spike against position)
                    worst_losses.append({
                        "entry_bar": entry_bar,
                        "spike_bar": spike_bar,
                        "gap": gap,
                        "loss": loss,
                        "spike_range": bars.iloc[spike_bar]["range"]
                    })
    
    if worst_losses:
        losses_df = pd.DataFrame(worst_losses)
        print(f"  Total adverse events: {len(losses_df)}")
        print(f"  Avg loss per event: {losses_df['loss'].mean():.4f}")
        print(f"  Max loss: {losses_df['loss'].max():.4f}")
        print(f"  Avg spike range when adverse: {losses_df['spike_range'].mean():.4f}")
        
        # Breakdown by gap
        for g in [1, 2, 3, 5, 10]:
            subset = losses_df[losses_df["gap"] <= g]
            if len(subset) > 0:
                print(f"    Gap <= {g}: {len(subset)} events, avg loss={subset['loss'].mean():.4f}")
    
    # ═══════════════════════════════════════════════════════════════
    # PART 3: DEFENSE STRATEGIES
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  PART 3: DEFENSE STRATEGIES")
    print(f"  {'-'*60}")
    
    # Strategy A: DELAYED ENTRY (wait N bars after last spike before entering)
    print(f"\n  STRATEGY A: DELAYED ENTRY")
    for delay in [1, 2, 3, 5, 7, 10]:
        entry_candidates = []
        for i in range(len(target)):
            bar = target[i]
            # Check if enough time since last spike
            if i > 0 and (bar - target[i-1]) < delay:
                continue
            entry_candidates.append(bar)
        
        profits = []
        for entry_bar in entry_candidates:
            hold = 5  # fixed hold for comparison
            if entry_bar + hold >= nb:
                continue
            entry_price = bars.iloc[entry_bar]["close"]
            exit_price = bars.iloc[entry_bar + hold]["close"]
            if drift_up:
                profits.append(entry_price - exit_price)
            else:
                profits.append(exit_price - entry_price)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            avg_p = np.mean(profits)
            # Count how many trades survived (not hit by cluster)
            cluster_hits = 0
            for entry_bar in entry_candidates:
                for j in range(len(target)):
                    if target[j] > entry_bar and target[j] - entry_bar <= hold:
                        cluster_hits += 1
                        break
            print(f"    Delay={delay:2d}: WR={wr:.1%} PF={pf:.2f} avg={avg_p:.4f} n={len(profits)} cluster_hits={cluster_hits}")
    
    # Strategy B: SKIP IF CLUSTER ACTIVE (don't enter if spike happened within N bars)
    print(f"\n  STRATEGY B: SKIP CLUSTER (don't enter if previous spike < N bars ago)")
    for skip_bars in [3, 5, 7, 10, 15]:
        entry_candidates = []
        for i in range(len(target)):
            bar = target[i]
            if i > 0 and (bar - target[i-1]) <= skip_bars:
                continue
            entry_candidates.append(bar)
        
        profits = []
        cluster_avoided = 0
        for entry_bar in entry_candidates:
            hold = 5
            if entry_bar + hold >= nb:
                continue
            entry_price = bars.iloc[entry_bar]["close"]
            exit_price = bars.iloc[entry_bar + hold]["close"]
            if drift_up:
                profits.append(entry_price - exit_price)
            else:
                profits.append(exit_price - entry_price)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            print(f"    Skip<{skip_bars:2d}: WR={wr:.1%} PF={pf:.2f} avg={np.mean(profits):.4f} n={len(profits)}")
    
    # Strategy C: TRAILING STOP (move SL to breakeven after 1 bar)
    print(f"\n  STRATEGY C: TRAILING STOP (breakeven after N bars)")
    for be_bar in [0, 1, 2, 3]:
        profits = []
        for i in range(len(target)):
            bar = target[i]
            hold = 7
            if bar + hold >= nb:
                continue
            entry_price = bars.iloc[bar]["close"]
            
            # Simulate trailing stop
            max_favorable = 0
            closed = False
            for h in range(1, hold + 1):
                current_price = bars.iloc[bar + h]["close"]
                if drift_up:
                    mfe = entry_price - current_price
                else:
                    mfe = current_price - entry_price
                
                max_favorable = max(max_favorable, mfe)
                
                # After be_bar, close if price returns to breakeven
                if h >= be_bar and max_favorable > 0 and mfe <= 0:
                    profits.append(max_favorable)  # Close at breakeven + small profit
                    closed = True
                    break
            
            if not closed:
                # Final exit
                exit_price = bars.iloc[bar + hold]["close"]
                if drift_up:
                    profits.append(entry_price - exit_price)
                else:
                    profits.append(exit_price - entry_price)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            print(f"    BE after {be_bar} bars: WR={wr:.1%} PF={pf:.2f} avg={np.mean(profits):.4f} n={len(profits)}")
    
    # Strategy D: MAX HOLD (hard timeout to limit cluster exposure)
    print(f"\n  STRATEGY D: MAX HOLD (close after N bars max)")
    for max_hold in [2, 3, 5, 7, 10]:
        profits = []
        for i in range(len(target)):
            bar = target[i]
            if bar + max_hold >= nb:
                continue
            entry_price = bars.iloc[bar]["close"]
            exit_price = bars.iloc[bar + max_hold]["close"]
            if drift_up:
                profits.append(entry_price - exit_price)
            else:
                profits.append(exit_price - entry_price)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            print(f"    MaxHold={max_hold:2d}: WR={wr:.1%} PF={pf:.2f} avg={np.mean(profits):.4f} n={len(profits)}")
    
    # Strategy E: PARTIAL ENTRY (enter 50% size, add 50% after 1 bar if profitable)
    print(f"\n  STRATEGY E: PARTIAL ENTRY (50% now, 50% after 1 bar if winning)")
    profits_full = []
    profits_partial = []
    for i in range(len(target)):
        bar = target[i]
        if bar + 5 >= nb:
            continue
        entry_price = bars.iloc[bar]["close"]
        
        # Full entry at bar 0, hold 5
        exit_full = bars.iloc[bar + 5]["close"]
        if drift_up:
            profits_full.append(entry_price - exit_full)
        else:
            profits_full.append(exit_full - entry_price)
        
        # Partial: enter 50% at bar 0, add 50% at bar 1 only if profitable
        price_at_1 = bars.iloc[bar + 1]["close"]
        if drift_up:
            half1_pnl = (entry_price - price_at_1) * 0.5
            if entry_price > price_at_1:  # winning at bar 1
                half2_pnl = (entry_price - exit_full) * 0.5  # same entry price for simplicity
            else:
                half2_pnl = (price_at_1 - exit_full) * 0.5  # enter at worse price
            profits_partial.append(half1_pnl + half2_pnl)
        else:
            half1_pnl = (price_at_1 - entry_price) * 0.5
            if price_at_1 > entry_price:  # winning at bar 1
                half2_pnl = (exit_full - entry_price) * 0.5
            else:
                half2_pnl = (exit_full - price_at_1) * 0.5
            profits_partial.append(half1_pnl + half2_pnl)
    
    if profits_full and profits_partial:
        wr_f = np.mean([p > 0 for p in profits_full])
        wr_p = np.mean([p > 0 for p in profits_partial])
        print(f"    Full entry: WR={wr_f:.1%} avg={np.mean(profits_full):.4f}")
        print(f"    Partial:    WR={wr_p:.1%} avg={np.mean(profits_partial):.4f}")
    
    # Strategy F: SPIKE SIZE FILTER (skip if previous spike was too big)
    print(f"\n  STRATEGY F: SIZE FILTER (skip if previous spike > Nx median)")
    all_sz = np.array([bars.iloc[i]["range"] for i in target])
    med_sz = np.median(all_sz)
    
    for mult in [1.5, 2.0, 3.0, 5.0]:
        entry_candidates = []
        for i in range(len(target)):
            bar = target[i]
            if i > 0:
                prev_sz = bars.iloc[target[i-1]]["range"]
                if prev_sz > mult * med_sz:
                    continue
            entry_candidates.append(bar)
        
        profits = []
        for entry_bar in entry_candidates:
            hold = 5
            if entry_bar + hold >= nb:
                continue
            entry_price = bars.iloc[entry_bar]["close"]
            exit_price = bars.iloc[entry_bar + hold]["close"]
            if drift_up:
                profits.append(entry_price - exit_price)
            else:
                profits.append(exit_price - entry_price)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            wins = sum(p for p in profits if p > 0)
            losses = abs(sum(p for p in profits if p < 0))
            pf = wins / max(losses, 1e-10)
            print(f"    Skip if prev>{mult:.1f}x: WR={wr:.1%} PF={pf:.2f} avg={np.mean(profits):.4f} n={len(profits)}")
    
    return {
        "clusters": len(clusters),
        "standalone": len(standalone),
        "multi": len(multi),
        "avg_size": np.mean(cluster_sizes),
        "quiet_periods": quiet_periods
    }

# ═══════════════════════════════════════════════════════════════
# RUN ALL SYMBOLS
# ═══════════════════════════════════════════════════════════════
results = {}
for sym, fn in [("BOOM1000", "ticks_boom1000_index.npy"),
                ("CRASH1000", "ticks_crash1000_index.npy"),
                ("BOOM500", "ticks_boom_500_index.npy"),
                ("CRASH500", "ticks_crash_500_index.npy")]:
    results[sym] = analyze_cluster(sym, Path("data") / fn)

# ═══════════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════════
print(f"\n{'='*80}")
print(f"  CLUSTER DEFENSE SUMMARY")
print(f"{'='*80}")

for sym, r in results.items():
    standalone_pct = r["standalone"] / r["clusters"]
    print(f"\n  {sym}:")
    print(f"    Clusters: {r['clusters']} total, {r['multi']} multi-spike ({r['multi']/r['clusters']:.1%})")
    print(f"    Avg cluster size: {r['avg_size']:.1f}")
    if r["quiet_periods"]:
        print(f"    Avg quiet period: {np.mean(r['quiet_periods']):.1f} bars")
        print(f"    Quiet >= 10 bars: {np.mean([p >= 10 for p in r['quiet_periods']]):.1%}")

print(f"\n  RECOMMENDATION:")
print(f"  - Wait 5+ bars after last spike before entering")
print(f"  - Use trailing stop (breakeven after 2 bars)")
print(f"  - Max hold 5-7 bars")
print(f"  - Skip trades after large spikes (>3x median)")
