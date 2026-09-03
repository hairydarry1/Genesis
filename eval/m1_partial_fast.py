#!/usr/bin/env python3
"""M1 BAR PARTIAL ENTRY - FAST VERSION"""
import pandas as pd
import numpy as np
from pathlib import Path

def analyze_m1(symbol, slug):
    print(f"\n{'='*80}")
    print(f"  M1: {symbol}")
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
    print(f"  Bars: {nb} | Target spikes: {len(target_pos)}")
    
    # Cluster analysis
    clusters = []
    current = [target_pos[0]]
    for i in range(1, len(target_pos)):
        if target_pos[i] - target_pos[i-1] <= 5:
            current.append(target_pos[i])
        else:
            clusters.append(current)
            current = [target_pos[i]]
    clusters.append(current)
    
    multi = sum(1 for c in clusters if len(c) > 1)
    print(f"  Clusters: {multi}/{len(clusters)} ({multi/len(clusters):.1%})")
    
    # Extract arrays for speed
    closes = df["close"].values
    
    # ═══════════════════════════════════════════════════════════
    # BASELINE vs PARTIAL for key holds
    # ═══════════════════════════════════════════════════════════
    print(f"\n  BASELINE vs PARTIAL ENTRY")
    print(f"  {'-'*60}")
    
    best_pf = 0
    best_hold = 0
    
    for hold in [1, 2, 3, 5, 7, 10]:
        pf_base = []
        pf_partial = []
        
        for pos in target_pos:
            if pos + hold >= nb or pos + 1 >= nb:
                continue
            
            entry = closes[pos]
            exit_ = closes[pos + hold]
            price1 = closes[pos + 1]
            
            # Full
            if drift_up:
                pf_base.append(entry - exit_)
            else:
                pf_base.append(exit_ - entry)
            
            # Partial
            if drift_up:
                h1 = (entry - price1) * 0.5
                h2 = ((entry if entry > price1 else price1) - exit_) * 0.5
            else:
                h1 = (price1 - entry) * 0.5
                h2 = (exit_ - (entry if price1 <= entry else price1)) * 0.5
            pf_partial.append(h1 + h2)
        
        if pf_base and pf_partial:
            wr_b = np.mean([p > 0 for p in pf_base])
            wr_p = np.mean([p > 0 for p in pf_partial])
            w_b = sum(p for p in pf_base if p > 0)
            l_b = abs(sum(p for p in pf_base if p < 0))
            w_p = sum(p for p in pf_partial if p > 0)
            l_p = abs(sum(p for p in pf_partial if p < 0))
            pfb = w_b / max(l_b, 1e-10)
            pfp = w_p / max(l_p, 1e-10)
            
            tag = ""
            if pfp > best_pf:
                best_pf = pfp
                best_hold = hold
                tag = " <-- BEST"
            
            print(f"  Hold={hold:2d}: Full WR={wr_b:.1%} PF={pfb:.2f} | Partial WR={wr_p:.1%} PF={pfp:.2f} | PF gain={((pfp/pfb)-1)*100:+.0f}%{tag}")
    
    print(f"\n  OPTIMAL: Hold={best_hold} PF={best_pf:.2f}")
    
    # ═══════════════════════════════════════════════════════════
    # OUT-OF-SAMPLE at optimal hold
    # ═══════════════════════════════════════════════════════════
    print(f"\n  OUT-OF-SAMPLE (Hold={best_hold})")
    split = len(target_pos) // 2
    
    for label, subset in [("TRAIN", target_pos[:split]), ("TEST", target_pos[split:])]:
        profits = []
        for pos in subset:
            if pos + best_hold >= nb or pos + 1 >= nb:
                continue
            entry = closes[pos]
            exit_ = closes[pos + best_hold]
            price1 = closes[pos + 1]
            if drift_up:
                h1 = (entry - price1) * 0.5
                h2 = ((entry if entry > price1 else price1) - exit_) * 0.5
            else:
                h1 = (price1 - entry) * 0.5
                h2 = (exit_ - (entry if price1 <= entry else price1)) * 0.5
            profits.append(h1 + h2)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            w = sum(p for p in profits if p > 0)
            l = abs(sum(p for p in profits if p < 0))
            pf = w / max(l, 1e-10)
            # Max consec loss
            mc, cur = 0, 0
            for p in profits:
                if p <= 0: cur += 1; mc = max(mc, cur)
                else: cur = 0
            print(f"    {label}: n={len(profits)} WR={wr:.1%} PF={pf:.2f} PnL={sum(profits):.4f} MaxConLoss={mc}")
    
    # ═══════════════════════════════════════════════════════════
    # SKIP CLUSTER at optimal hold
    # ═══════════════════════════════════════════════════════════
    print(f"\n  SKIP CLUSTER + PARTIAL (Hold={best_hold})")
    for skip in [0, 3, 5, 7, 10]:
        ep = [target_pos[i] for i in range(len(target_pos))
              if i == 0 or (target_pos[i] - target_pos[i-1]) >= skip]
        profits = []
        for pos in ep:
            if pos + best_hold >= nb or pos + 1 >= nb:
                continue
            entry = closes[pos]
            exit_ = closes[pos + best_hold]
            price1 = closes[pos + 1]
            if drift_up:
                h1 = (entry - price1) * 0.5
                h2 = ((entry if entry > price1 else price1) - exit_) * 0.5
            else:
                h1 = (price1 - entry) * 0.5
                h2 = (exit_ - (entry if price1 <= entry else price1)) * 0.5
            profits.append(h1 + h2)
        if profits:
            wr = np.mean([p > 0 for p in profits])
            w = sum(p for p in profits if p > 0)
            l = abs(sum(p for p in profits if p < 0))
            pf = w / max(l, 1e-10)
            print(f"    Skip<{skip:2d}: n={len(profits)} WR={wr:.1%} PF={pf:.2f} PnL={sum(profits):.4f}")

# RUN
print("="*80)
print("  M1 BAR PARTIAL ENTRY TEST (FAST)")
print("="*80)

for sym, slug in [("BOOM1000","boom1000"),("CRASH1000","crash1000"),
                   ("BOOM500","boom500"),("CRASH500","crash500")]:
    try:
        analyze_m1(sym, slug)
    except Exception as e:
        print(f"\n  ERROR {sym}: {e}")
