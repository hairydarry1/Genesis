#!/usr/bin/env python3
"""
DEEP PATTERN FINDER — Round 3
15 new exploit angles on tick data, then ruthlessly challenge each.
Uses npy tick data (rawest form).
"""
import pandas as pd
import numpy as np
from pathlib import Path
from scipy import stats

def load_ticks(npy_file):
    arr = np.load(npy_file)
    return arr.flatten()

def make_bars(prices, window=100):
    n = len(prices) // window
    bars = []
    for i in range(n):
        chunk = prices[i*window:(i+1)*window]
        bars.append({
            "idx": i,
            "open": chunk[0], "close": chunk[-1],
            "high": chunk.max(), "low": chunk.min(),
            "range": chunk.max() - chunk.min(),
        })
    return pd.DataFrame(bars)

def analyze(symbol, tick_file):
    print(f"\n{'='*80}")
    print(f"  {symbol}  ({tick_file.name})")
    print(f"{'='*80}")
    
    prices = load_ticks(tick_file)
    n = len(prices)
    print(f"  Ticks: {n:,}")
    
    bars = make_bars(prices, 100)
    nb = len(bars)
    print(f"  Bars (100-tick): {nb}")
    
    if nb < 300:
        print("  NOT ENOUGH DATA")
        return {}
    
    # --- detect spikes ---
    med = bars["range"].rolling(100, min_periods=20).median()
    bars["median"] = med
    bars["is_spike"] = bars["range"] > 3.0 * bars["median"]
    
    # drift direction
    up_pct = (bars["close"] > bars["open"]).mean()
    drift_up = up_pct > 0.5
    print(f"  Drift: {'UP' if drift_up else 'DOWN'} ({up_pct:.1%})")
    
    # spike direction
    spikes = bars[bars["is_spike"]]
    counter_spikes = spikes[spikes["close"] < spikes["open"]] if drift_up else spikes[spikes["close"] > spikes["open"]]
    target_idx = counter_spikes.index.tolist()
    print(f"  Target spikes: {len(target_idx)}")
    
    if len(target_idx) < 50:
        print("  TOO FEW SPIKES")
        return {}
    
    F = {}
    
    # ═══════════════════════════════════════════════════════════════
    # H1: DRIFT DURATION BEFORE SPIKE
    # ═══════════════════════════════════════════════════════════════
    def count_drift(idx, backwards=True):
        c = 0
        step = -1 if backwards else 1
        for j in range(idx + step, max(0, min(nb, idx + step * 50)), step):
            row = bars.iloc[j]
            if drift_up:
                hit = row["close"] > row["open"]
            else:
                hit = row["close"] < row["open"]
            if hit:
                c += 1
            else:
                break
        return c
    
    d_spike = [count_drift(i) for i in target_idx]
    np.random.seed(42)
    rand_idx = np.random.choice(range(100, nb-1), min(500, nb-101), replace=False)
    d_random = [count_drift(i) for i in rand_idx]
    
    avg_s, avg_r = np.mean(d_spike), np.mean(d_random)
    ratio = avg_s / max(avg_r, 0.01)
    print(f"\n  H1 DRIFT DURATION BEFORE SPIKE")
    print(f"    Spike: {avg_s:.1f} bars | Random: {avg_r:.1f} bars | Ratio: {ratio:.2f}x")
    for n_bar in range(1, 8):
        ps = np.mean([d >= n_bar for d in d_spike])
        pr = np.mean([d >= n_bar for d in d_random])
        lift = ps / max(pr, 0.001)
        print(f"    >= {n_bar}: SPIKE={ps:.1%} RAND={pr:.1%} LIFT={lift:.2f}x")
    F["H1_drift_dur"] = ratio
    
    # ═══════════════════════════════════════════════════════════════
    # H2: SPIKE SIZE vs PRIOR DRIFT AMOUNT
    # ═══════════════════════════════════════════════════════════════
    drift_amts, spike_szs = [], []
    for i in target_idx:
        if i < 20:
            continue
        da = sum(abs(bars.iloc[j]["close"] - bars.iloc[j]["open"]) for j in range(i-10, i))
        drift_amts.append(da)
        spike_szs.append(bars.iloc[i]["range"])
    
    da, ss = np.array(drift_amts), np.array(spike_szs)
    med_da = np.median(da)
    high_ss = ss[da > med_da]
    low_ss = ss[da <= med_da]
    corr, pv = stats.spearmanr(da, ss) if len(da) > 10 else (0, 1)
    
    print(f"\n  H2 DRIFT AMOUNT vs SPIKE SIZE")
    print(f"    High-drift spikes: {np.mean(high_ss):.4f} | Low-drift: {np.mean(low_ss):.4f} | Ratio: {np.mean(high_ss)/max(np.mean(low_ss),1e-6):.2f}x")
    print(f"    Spearman: {corr:.3f} p={pv:.4f}")
    F["H2_drift_vs_size"] = abs(corr)
    
    # ═══════════════════════════════════════════════════════════════
    # H3: PRE-SPIKE VOLATILITY CLUSTERING
    # Does volatility (range/std) increase in the 10 bars before spike?
    # ═══════════════════════════════════════════════════════════════
    pre_vols, rand_vols = [], []
    for i in target_idx:
        if i < 30:
            continue
        recent = bars.iloc[i-10:i]["range"].mean()
        older = bars.iloc[i-30:i-10]["range"].mean()
        pre_vols.append(recent / max(older, 1e-10))
    for i in np.random.choice(range(100, nb-1), min(400, nb-101), replace=False):
        if i < 30:
            continue
        recent = bars.iloc[i-10:i]["range"].mean()
        older = bars.iloc[i-30:i-10]["range"].mean()
        rand_vols.append(recent / max(older, 1e-10))
    
    avg_pv, avg_rv = np.mean(pre_vols), np.mean(rand_vols)
    print(f"\n  H3 PRE-SPIKE VOLATILITY CLUSTERING")
    print(f"    Spike vol ratio: {avg_pv:.2f}x | Random: {avg_rv:.2f}x | Lift: {avg_pv/max(avg_rv,0.01):.2f}x")
    t, pv3 = stats.ttest_ind(pre_vols, rand_vols)
    print(f"    T-test: t={t:.2f} p={pv3:.4f}")
    F["H3_vol_cluster"] = avg_pv / max(avg_rv, 0.01)
    
    # ═══════════════════════════════════════════════════════════════
    # H4: CONSECUTIVE DRIFT BARS COUNT (5-bar pattern)
    # ═══════════════════════════════════════════════════════════════
    def get_5pattern(idx):
        if idx < 5:
            return None
        p = ""
        for j in range(idx-5, idx):
            r = bars.iloc[j]
            if drift_up:
                p += "U" if r["close"] > r["open"] else "D"
            else:
                p += "D" if r["close"] < r["open"] else "U"
        return p
    
    pat_spike, pat_rand = {}, {}
    for i in target_idx:
        p = get_5pattern(i)
        if p:
            pat_spike[p] = pat_spike.get(p, 0) + 1
    for i in rand_idx:
        p = get_5pattern(i)
        if p:
            pat_rand[p] = pat_rand.get(p, 0) + 1
    
    ts = sum(pat_spike.values())
    tr = sum(pat_rand.values())
    
    # All-5-same pattern
    key_spike = pat_spike.get("UUUUU" if drift_up else "DDDDD", 0)
    key_rand = pat_rand.get("UUUUU" if drift_up else "DDDDD", 0)
    lift_5 = (key_spike/ts) / max(key_rand/tr, 0.001)
    
    print(f"\n  H4 5-BAR PATTERN")
    print(f"    All-same before SPIKE: {key_spike/ts:.1%}")
    print(f"    All-same at RANDOM:    {key_rand/tr:.1%}")
    print(f"    Lift: {lift_5:.2f}x")
    # Top patterns
    sorted_sp = sorted(pat_spike.items(), key=lambda x: x[1], reverse=True)[:5]        for pat, cnt in sorted_sp:
            sp_pct = cnt / ts
            rd_pct = pat_rand.get(pat, 0) / tr
            lft = sp_pct/max(rd_pct,0.001)
            print(f"    Pattern {pat}: SPIKE={sp_pct:.1%} RAND={rd_pct:.1%} LIFT={lft:.2f}x")
    F["H4_pattern_lift"] = lift_5
    
    # ═══════════════════════════════════════════════════════════════
    # H5: SPIKE SIZE vs INTERVAL SINCE LAST SPIKE
    # ═══════════════════════════════════════════════════════════════
    intervals, sizes = [], []
    for i in range(1, len(target_idx)):
        gap = target_idx[i] - target_idx[i-1]
        sz = bars.iloc[target_idx[i]]["range"]
        intervals.append(gap)
        sizes.append(sz)
    
    if len(intervals) > 10:
        corr5, pv5 = stats.spearmanr(intervals, sizes)
        med_int = np.median(intervals)
        short_sz = [s for it, s in zip(intervals, sizes) if it <= med_int]
        long_sz = [s for it, s in zip(intervals, sizes) if it > med_int]
        print(f"\n  H5 INTERVAL vs SPIKE SIZE")
        print(f"    Short interval size: {np.mean(short_sz):.4f}")
        print(f"    Long interval size:  {np.mean(long_sz):.4f}")
        print(f"    Ratio: {np.mean(long_sz)/max(np.mean(short_sz),1e-6):.2f}x")
        print(f"    Spearman: {corr5:.3f} p={pv5:.4f}")
        F["H5_int_vs_size"] = abs(corr5)
    
    # ═══════════════════════════════════════════════════════════════
    # H6: SPIKE SIZE SEQUENCE (Markov)
    # ═══════════════════════════════════════════════════════════════
    all_sz = np.array([bars.iloc[i]["range"] for i in target_idx])
    if len(all_sz) > 20:
        med_sz = np.median(all_sz)
        ss, sl, ls, ll = 0, 0, 0, 0
        for i in range(1, len(all_sz)):
            prev_big = all_sz[i-1] > med_sz
            curr_big = all_sz[i] > med_sz
            if not prev_big and not curr_big: ss += 1
            elif not prev_big and curr_big: sl += 1
            elif prev_big and not curr_big: ls += 1
            else: ll += 1
        tot = ss+sl+ls+ll
        print(f"\n  H6 SPIKE SIZE MARKOV CHAIN")
        print(f"    S->S: {ss/tot:.1%} | S->L: {sl/tot:.1%}")
        print(f"    L->S: {ls/tot:.1%} | L->L: {ll/tot:.1%}")
        print(f"    (Random expectation: 25% each)")
        # Entropy deviation from uniform
        probs = [ss/tot, sl/tot, ls/tot, ll/tot]
        entropy = -sum(p * np.log2(max(p, 1e-10)) for p in probs)
        max_entropy = 2.0  # log2(4)
        dev = 1 - entropy / max_entropy
        print(f"    Entropy deviation from uniform: {dev:.3f}")
        F["H6_markov"] = dev
    
    # ═══════════════════════════════════════════════════════════════
    # H7: RECOVERY DEPTH after spike
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H7 RECOVERY DEPTH after spike")
    for delta in [1, 2, 3, 5, 7, 10, 15, 20]:
        recs = []
        for i in target_idx:
            if i + delta >= nb:
                continue
            sp = bars.iloc[i]["close"]
            fp = bars.iloc[i+delta]["close"]
            sr = bars.iloc[i]["range"]
            if drift_up:
                r = (sp - fp) / max(sr, 1e-6)
            else:
                r = (fp - sp) / max(sr, 1e-6)
            recs.append(r)
        if recs:
            wr = np.mean([r > 0 for r in recs])
            avg_r = np.mean(recs)
            print(f"    Bar {delta:2d}: WR={wr:.1%} avg_recovery={avg_r:.2%} n={len(recs)}")
    
    # ═══════════════════════════════════════════════════════════════
    # H8: TICK-LEVEL ENTROPY before spike
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H8 TICK ENTROPY before spike")
    def tick_entropy(arr):
        changes = np.diff(arr)
        signs = (changes > 0).astype(int)
        p1 = signs.mean()
        p0 = 1 - p1
        if p0 <= 0 or p1 <= 0:
            return 1.0
        return -(p0 * np.log2(p0) + p1 * np.log2(p1))
    
    ent_spike, ent_rand = [], []
    for i in target_idx:
        tick_start = i * 100
        if tick_start < 50:
            continue
        chunk = prices[tick_start-50:tick_start]
        ent_spike.append(tick_entropy(chunk))
    for i in np.random.choice(range(100, nb-1), min(400, nb-101), replace=False):
        tick_start = i * 100
        if tick_start < 50:
            continue
        chunk = prices[tick_start-50:tick_start]
        ent_rand.append(tick_entropy(chunk))
    
    avg_es, avg_er = np.mean(ent_spike), np.mean(ent_rand)
    print(f"    Spike entropy: {avg_es:.4f} | Random: {avg_er:.4f} | Diff: {avg_es-avg_er:.4f}")
    F["H8_entropy_diff"] = abs(avg_es - avg_er)
    
    # ═══════════════════════════════════════════════════════════════
    # H9: CLUSTER SIZE and CLUSTER EXIT
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H9 CLUSTER ANALYSIS")
    cluster_szs = []
    cur = 0
    for i in range(1, len(target_idx)):
        gap = target_idx[i] - target_idx[i-1]
        if gap <= 5:
            cur += 1
        else:
            if cur > 0:
                cluster_szs.append(cur + 1)
            cur = 0
    if cur > 0:
        cluster_szs.append(cur + 1)
    
    if cluster_szs:
        print(f"    Clusters: {len(cluster_szs)} | Avg size: {np.mean(cluster_szs):.1f} | Max: {max(cluster_szs)}")
        for n in range(2, 6):
            pct = np.mean([c >= n for c in cluster_szs])
            print(f"    Cluster >= {n}: {pct:.1%}")
        F["H9_cluster_avg"] = np.mean(cluster_szs)
    
    # ═══════════════════════════════════════════════════════════════
    # H10: SPIKE SIZE PERCENTILE → PnL
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H10 SPIKE SIZE -> PnL (fade, 3-bar hold)")
    for q_label, q_lo, q_hi in [("Q1 small", 0, 25), ("Q2 med-low", 25, 50), ("Q3 med-high", 50, 75), ("Q4 large", 75, 100)]:
        lo_val = np.percentile(all_sz, q_lo)
        hi_val = np.percentile(all_sz, q_hi)
        subset = [i for i in target_idx if lo_val <= bars.iloc[i]["range"] < hi_val]
        profits = []
        for i in subset:
            if i + 3 >= nb:
                continue
            entry = bars.iloc[i]["close"]
            exit_ = bars.iloc[i+3]["close"]
            if drift_up:
                profits.append(entry - exit_)
            else:
                profits.append(exit_ - entry)
        if profits:
            wr = np.mean([p > 0 for p in profits])
            avg_p = np.mean(profits)
            print(f"    {q_label}: WR={wr:.1%} avg_PnL={avg_p:.4f} trades={len(profits)}")
    
    # ═══════════════════════════════════════════════════════════════
    # H11: POST-SPIKE DRIFT RESUMPTION SPEED
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H11 DRIFT RESUMPTION (pre vs post spike)")
    pre_spd, post_spd = [], []
    for i in target_idx:
        if i < 20 or i + 10 >= nb:
            continue
        pre = np.mean([abs(bars.iloc[j]["close"] - bars.iloc[j]["open"]) for j in range(i-5, i)])
        post = np.mean([abs(bars.iloc[j]["close"] - bars.iloc[j]["open"]) for j in range(i+1, min(i+6, nb))])
        pre_spd.append(pre)
        post_spd.append(post)
    if pre_spd:
        print(f"    Pre-spike drift: {np.mean(pre_spd):.4f}")
        print(f"    Post-spike drift: {np.mean(post_spd):.4f}")
        print(f"    Ratio: {np.mean(post_spd)/max(np.mean(pre_spd),1e-6):.2f}x")
        F["H11_drift_resumption"] = np.mean(post_spd) / max(np.mean(pre_spd), 1e-6)
    
    # ═══════════════════════════════════════════════════════════════
    # H12: SPIKE vs NON-SPIKE BAR SHAPE (wick ratio)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H12 BAR SHAPE (wick vs body ratio)")
    def wick_ratio(row):
        body = abs(row["close"] - row["open"])
        total = row["high"] - row["low"]
        if total <= 0:
            return 0
        upper_wick = row["high"] - max(row["close"], row["open"])
        lower_wick = min(row["close"], row["open"]) - row["low"]
        return (upper_wick + lower_wick) / total
    
    wr_spike = [wick_ratio(bars.iloc[i]) for i in target_idx if i < nb]
    wr_rand = [wick_ratio(bars.iloc[i]) for i in rand_idx if i < nb]
    print(f"    Spike wick ratio: {np.mean(wr_spike):.2%}")
    print(f"    Random wick ratio: {np.mean(wr_rand):.2%}")
    
    # ═══════════════════════════════════════════════════════════════
    # H13: RUN OF SAME-OPEN-AS-CLOSE (flat bars)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H13 FLAT BAR FREQUENCY")
    flat_threshold = bars["range"].median() * 0.3
    bars["is_flat"] = bars["range"] < flat_threshold
    
    flat_spike = [bars.iloc[i]["is_flat"] for i in target_idx]
    flat_rand = [bars.iloc[i]["is_flat"] for i in rand_idx]
    print(f"    Flat bars AT SPIKE: {np.mean(flat_spike):.1%}")
    print(f"    Flat bars at RANDOM: {np.mean(flat_rand):.1%}")
    
    # Flat bar before spike: count how many flat bars in 5 bars before
    flat_before_spike, flat_before_rand = [], []
    for i in target_idx:
        if i < 5:
            continue
        flat_before_spike.append(sum(bars.iloc[j]["is_flat"] for j in range(i-5, i)))
    for i in rand_idx:
        if i < 5:
            continue
        flat_before_rand.append(sum(bars.iloc[j]["is_flat"] for j in range(i-5, i)))
    if flat_before_spike:
        print(f"    Flat bars in 5 before SPIKE: {np.mean(flat_before_spike):.1f}")
        print(f"    Flat bars in 5 at RANDOM: {np.mean(flat_before_rand):.1f}")
    
    # ═══════════════════════════════════════════════════════════════
    # H14: RANGE EXPANSION RATE
    # Does range grow before spike? (bollinger squeeze → spike)
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H14 RANGE EXPANSION (Bollinger-like)")
    bars["range_ma"] = bars["range"].rolling(20, min_periods=5).mean()
    bars["range_std"] = bars["range"].rolling(20, min_periods=5).std()
    bars["range_zscore"] = (bars["range"] - bars["range_ma"]) / bars["range_std"].clip(lower=1e-10)
    
    z_spike = [bars.iloc[i]["range_zscore"] for i in target_idx if pd.notna(bars.iloc[i]["range_zscore"])]
    z_rand = [bars.iloc[i]["range_zscore"] for i in rand_idx if pd.notna(bars.iloc[i]["range_zscore"])]
    print(f"    Range z-score at SPIKE: {np.mean(z_spike):.2f}")
    print(f"    Range z-score at RANDOM: {np.mean(z_rand):.2f}")
    
    # Pre-spike range z-score (2 bars before)
    z_pre_spike, z_pre_rand = [], []
    for i in target_idx:
        if i >= 2 and pd.notna(bars.iloc[i-2]["range_zscore"]):
            z_pre_spike.append(bars.iloc[i-2]["range_zscore"])
    for i in rand_idx:
        if i >= 2 and pd.notna(bars.iloc[i-2]["range_zscore"]):
            z_pre_rand.append(bars.iloc[i-2]["range_zscore"])
    if z_pre_spike:
        print(f"    Range z-score 2 bars BEFORE spike: {np.mean(z_pre_spike):.2f}")
        print(f"    Range z-score 2 bars at RANDOM: {np.mean(z_pre_rand):.2f}")
    
    # ═══════════════════════════════════════════════════════════════
    # H15: THE MEGA TEST — COMBINE ALL REAL SIGNALS
    # ═══════════════════════════════════════════════════════════════
    print(f"\n  H15 COMBINED STRATEGY BACKTEST")
    for hold in [1, 2, 3, 5, 7, 10]:
        profits = []
        for i in target_idx:
            if i + hold >= nb:
                continue
            entry = bars.iloc[i]["close"]
            exit_ = bars.iloc[i + hold]["close"]
            if drift_up:
                profits.append(entry - exit_)
            else:
                profits.append(exit_ - entry)
        if profits:
            wr = np.mean([p > 0 for p in profits])
            avg_p = np.mean(profits)
            pf = sum(p for p in profits if p > 0) / max(abs(sum(p for p in profits if p < 0)), 1e-10)
            print(f"    Hold={hold:2d}: WR={wr:.1%} PF={pf:.2f} avg={avg_p:.4f} n={len(profits)}")
    
    return F

def main():
    print("="*80)
    print("  DEEP PATTERN FINDER — Round 3")
    print("  15 exploit angles tested on tick data")
    print("="*80)
    
    data_dir = Path("data")
    targets = [
        ("BOOM1000", data_dir / "ticks_boom1000_index.npy"),
        ("CRASH1000", data_dir / "ticks_crash1000_index.npy"),
        ("BOOM500", data_dir / "ticks_boom_500_index.npy"),
        ("CRASH500", data_dir / "ticks_crash_500_index.npy"),
    ]
    
    all_results = {}
    for name, path in targets:
        if path.exists():
            try:
                all_results[name] = analyze(name, path)
            except Exception as e:
                print(f"\n  ERROR {name}: {e}")
                import traceback
                traceback.print_exc()
        else:
            print(f"\n  SKIP {name}: file not found")
    
    # Cross-symbol summary
    print(f"\n{'='*80}")
    print(f"  CROSS-SYMBOL SUMMARY")
    print(f"{'='*80}")
    all_keys = set()
    for r in all_results.values():
        all_keys.update(r.keys())
    
    for key in sorted(all_keys):
        vals = [all_results[s].get(key) for s in all_results if key in all_results[s]]
        if vals:
            print(f"  {key}: {vals}")

if __name__ == "__main__":
    main()
