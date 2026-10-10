#!/usr/bin/env python3
"""
SOLO-CORE PROSPECTIVE TEST
===========================
Follow-up to the clean T-C verdict. The retrospective solo-only subset is the best population
on every symbol (solo avg 0.6-1.7 vs full avg 0.1-0.3). The prospective cluster-start filter
couldn't reach it because solos and starts both have low trailing spike counts and can't be
distinguished by a trailing-window rule.

This test asks the honest forward question: is there ANY past-information rule that selects a
better-than-random subset of the full e+1 population — getting closer to the solo core than the
failed cluster-start filter did — even if it's not a "cluster-start filter"?

Candidates (all predictable at spike-close time, no future peeking):
  A. Trailing adverse-spike count in last W bars, threshold K (the failed filter, re-tested cleanly)
  B. Trailing adverse-spike COUNT MAGNITUDE (not just count) — was the last spike big?
  C. Time-since-last-spike (discharge-age) — solos may be separated by longer quiet gaps
  D. Regime-age (M15 bars since last BOS) — settled regime may have more solos
  E. Spike ratio (size vs median) — solos may cluster at the low end of the spike-ratio distribution
  F. Combined: sparse + quiet + settled (AND of A-low, C-high, D-high)

For each: filter avg vs unbiased random sub-sample (same size, 5 seeds), OOS halves by time,
cluster-label diagnostic (solo/member/start removal rates), on all 5 symbols.

If any candidate beats random by a meaningful margin (>= the failed filter's best +0.068 with clean
selectivity), it's a forward candidate. If all collapse, the solo-core insight is retrospective-only.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time as _time
import numpy as np
import pandas as pd
from brain.config import SYMBOLS, by_key, candle_path

# Symbol keys (bare, matching brain/config.py and BROKER_FLOOR)
SYMS = [s["key"] for s in SYMBOLS]
DISPLAY = {s["key"]: s["ea"] for s in SYMBOLS}
FAMILY = {s["key"]: ("crash" if s["is_crash"] else "boom") for s in SYMBOLS}
SPIKE_MULT = 3.0  # must match partial_m1_corrected.SPIKE_MULT

def load_frames():
    frames = {}
    for sym in SYMS:
        p = candle_path(sym)
        if os.path.exists(p):
            df = pd.read_parquet(p)
            frames[sym] = df
            print(f"[load] {DISPLAY[sym]} <- {os.path.basename(p)}  ({len(df)} bars)")
        else:
            print(f"[warn] missing {p}")
    return frames

def make_range(df):
    """range = high - low (point range of the M1 bar)."""
    return (df["high"].to_numpy() - df["low"].to_numpy())

def detect_spikes_and_median(df, tick_threshold, warm=60):
    rng = make_range(df)
    n = len(df)
    med = np.full(n, np.nan)
    for i in range(warm, n):
        med[i] = np.median(rng[i-warm:i])
    is_spike = np.zeros(n, dtype=bool)
    for i in range(warm, n):
        if med[i] > 0 and rng[i] > tick_threshold * med[i]:
            is_spike[i] = True
    return is_spike, med, rng

def half_split(entries, frac=0.5):
    """Split a sorted entry list into first-half and second-half by index."""
    split = len(entries) * frac
    h1 = entries[:int(split)]
    h2 = entries[int(split):]
    return h1, h2

def oos_eval(entries, oracle_cache, all_entries, label_map, solo_total, member_total, start_total, sym, df2):
    """For a candidate entry set: compute filter avg on full set, and on H1/H2, plus random controls per half.
    Returns dict with full + H1 + H2 rows."""
    from partial_m1_corrected import prep
    import numpy as np
    _time = __import__('time')
    pnls = [oracle_cache[e] for e in entries if e in oracle_cache]
    filter_avg = float(np.mean(pnls)) if pnls else 0.0
    rows = []
    # Full
    rows.append({"split": "FULL", "filter_n": len(entries), "filter_avg": filter_avg})
    # H1 / H2
    h1e, h2e = half_split(entries)
    for name, he in [("H1", h1e), ("H2", h2e)]:
        hp = [oracle_cache[e] for e in he if e in oracle_cache]
        havg = float(np.mean(hp)) if hp else 0.0
        # random control same size from same half of all_entries
        half_full = all_entries if name == "FULL" else he
        # actually control should be from same time-half of the full population
        # Use all_entries split the same way
        if name == "H1":
            ctrl_pop = all_entries[:len(all_entries)//2]
        else:
            ctrl_pop = all_entries[len(all_entries)//2:]
        ctrl_pnls = []
        for rs in range(5):
            if len(ctrl_pop) < 1:
                break
            ridx = np.random.default_rng(rs*17+7).choice(ctrl_pop, size=min(len(he), len(ctrl_pop)), replace=False)
            rp = [oracle_cache[e] for e in ridx]
            ctrl_pnls.append(float(np.mean(rp)) if rp else 0.0)
        cmean = float(np.mean(ctrl_pnls)) if ctrl_pnls else 0.0
        cmin = float(np.min(ctrl_pnls)) if ctrl_pnls else 0.0
        cmax = float(np.max(ctrl_pnls)) if ctrl_pnls else 0.0
        rows.append({"split": name, "filter_n": len(he), "filter_avg": havg,
                     "random_mean": cmean, "random_min": cmin, "random_max": cmax,
                     "clean_diff": havg - cmean})
    return rows

def adverse_direction(df, sym):
    close = df["close"].to_numpy()
    openp = df["open"].to_numpy()
    if "crash" in sym.lower():
        return close < openp  # down bar = adverse for crash (we fade down spikes, go long)
    else:
        return close > openp  # up bar = adverse for boom

def label_clusters_via_future(spike_idx, window_bars=10):
    """Label: cluster_start, cluster_member, solo. Uses FUTURE (oracle)."""
    n = len(spike_idx)
    label = np.full(n, "solo", dtype=object)
    spike_set = set(spike_idx.tolist())
    for a in range(n):
        start = spike_idx[a]
        # count spikes in [start, start+window_bars)
        cnt = 0
        for j in range(a, n):
            if spike_idx[j] < start + window_bars:
                cnt += 1
            else:
                break
        if cnt >= 3:
            label[a] = "cluster_start"
            for j in range(a+1, n):
                if spike_idx[j] < start + window_bars:
                    label[j] = "cluster_member"
                else:
                    break
    return label

def m15_bucket_info(df):
    """Return (bucket_epoch array, bar->bucket index array).
    M15 bucket = floor(epoch/900)*900. Returns unique bucket epochs and per-bar bucket index.
    Handles both 'epoch' (int seconds) and 'time' (datetime) columns.
    """
    if "epoch" in df.columns:
        epoch = df["epoch"].to_numpy()
        bucket_epoch = (epoch // 900) * 900
    elif "time" in df.columns:
        t = pd.to_datetime(df["time"].to_numpy())
        epoch = t.view("int64") // 10**9
        bucket_epoch = (epoch // 900) * 900
    else:
        raise KeyError("df must have 'epoch' or 'time' column for M15 bucketing")
    unique_be, inv = np.unique(bucket_epoch, return_inverse=True)
    return unique_be, inv

def regime_direction_m15(df):
    """
    M15 regime per bar: +1 if the bar's M15 bucket made a new high vs all prior buckets,
    -1 if new low vs all prior, 0 otherwise. No lookahead (bucket computed from bar epoch).
    """
    openp = df["open"].to_numpy()
    close = df["close"].to_numpy()
    n = len(df)
    unique_be, inv = m15_bucket_info(df)
    n_bars = len(unique_be)
    if n_bars < 2:
        return np.zeros(n, dtype=int)
    bh = np.full(n_bars, -np.inf)
    bl = np.full(n_bars, np.inf)
    for b in range(n_bars):
        mask = inv == b
        if mask.any():
            bh[b] = np.max(np.maximum(close[mask], openp[mask]))
            bl[b] = np.min(np.minimum(close[mask], openp[mask]))
    regime = np.zeros(n_bars, dtype=int)
    max_so_far = -np.inf
    min_so_far = np.inf
    for b in range(n_bars):
        if bh[b] > max_so_far and bh[b] > bl[b]:
            regime[b] = 1
        elif bl[b] < min_so_far and bl[b] < bh[b]:
            regime[b] = -1
        if bh[b] > max_so_far:
            max_so_far = bh[b]
        if bl[b] < min_so_far:
            min_so_far = bl[b]
    reg = np.zeros(n, dtype=int)
    for i in range(n):
        reg[i] = regime[inv[i]]
    return reg

def run_entry_set(entries, df, sym, tick_threshold):
    """Run e+1 entries on the corrected partial engine, return list of {pnl, bars, won}.
    Uses partial_m1_corrected.partial_trade per entry. Prep() is called once per symbol
    (outside) for speed; this wrapper calls partial_trade for each entry.
    """
    from partial_m1_corrected import partial_trade
    results = []
    for e in entries:
        si = e - 1
        tr = partial_trade(df, sym, si, tick_threshold)
        if tr is not None:
            results.append(tr)
    return results

def matched_lag_control(entries, n_bars, max_lag=20, n_per_entry=1, seed=11):
    """Unbiased random sub-sample from full e+1 population? No — this is matched-lag.
    For the clean test we want UNBIASED random sub-sample of same size as filter set."""
    rng = np.random.default_rng(seed)
    return rng.choice(n_bars, size=min(len(entries), n_bars), replace=False).tolist()

def random_subsample_control(filter_n, n_bars, seeds=(11, 22, 33, 44, 55)):
    """Unbiased random sub-sample of same size as filter set, mean + range over seeds."""
    out = []
    for s in seeds:
        idx = np.random.default_rng(s).choice(n_bars, size=min(filter_n, n_bars), replace=False)
        out.append(idx.tolist())
    return out

def main():
    frames = load_frames()
    print(f"[info] loaded {len(frames)} symbols")
    
    rows = []
    for sym in SYMS:
        if sym not in frames:
            continue
        df = frames[sym]
        n = len(df)
        family = FAMILY[sym]
        tick_threshold = 1.00 if family == "crash" else 2.50
        disp = DISPLAY[sym]
        
        # spike detection
        is_spike, med, rng_arr = detect_spikes_and_median(df, tick_threshold)
        adverse = adverse_direction(df, sym)
        spike_idx = np.where(is_spike & adverse)[0].tolist()
        if len(spike_idx) < 50:
            print(f"[skip] {sym}: only {len(spike_idx)} adverse spikes")
            continue
        
        # oracle cluster labels (future)
        labels = label_clusters_via_future(np.array(spike_idx), window_bars=10)
        label_map = {}
        for i, si in enumerate(spike_idx):
            label_map[si] = labels[i]
        
        # M15 regime per bar
        reg = regime_direction_m15(df)
        
        # full e+1 entry set (entry bar = spike+1, must leave room for hold window)
        all_entries = []
        for si in spike_idx:
            ei = si + 1
            if ei + 4 < n:  # need ei, ei+1, ei+2, ei+3 for partial_trade hold=3
                all_entries.append(ei)
        all_entries = sorted(set(all_entries))
        print(f"\n=== {disp} ===  spikes={len(spike_idx)}  e+1_entries={len(all_entries)}")
        
        # regime age per bar: M15 buckets since last drift-direction BOS
        openp = df["open"].to_numpy()
        close = df["close"].to_numpy()
        unique_be, inv = m15_bucket_info(df)
        n_bars_m15 = len(unique_be)
        bh = np.full(n_bars_m15, -np.inf)
        bl = np.full(n_bars_m15, np.inf)
        for b in range(n_bars_m15):
            mask = inv == b
            if mask.any():
                bh[b] = np.max(np.maximum(close[mask], openp[mask]))
                bl[b] = np.min(np.minimum(close[mask], openp[mask]))
        drift_dir = 1 if family == "crash" else -1
        max_so_far = -np.inf
        min_so_far = np.inf
        last_break = -999
        bucket_age = np.full(n_bars_m15, 999.0)
        for b in range(n_bars_m15):
            is_break = False
            if drift_dir > 0:  # crash: new HH
                if bh[b] > max_so_far and bh[b] > bl[b]:
                    is_break = True
            else:  # boom: new LL
                if bl[b] < min_so_far and bl[b] < bh[b]:
                    is_break = True
            if is_break:
                last_break = b
            max_so_far = max(max_so_far, bh[b])
            min_so_far = min(min_so_far, bl[b])
            bucket_age[b] = (b - last_break) if last_break >= 0 else 999
        regime_age = np.array([bucket_age[inv[i]] for i in range(n)])
        
        # per-spike features (at spike close, before entry decision)
        W_list = [10, 15, 20]
        K_list = [2, 3]
        import time as _time
        t_feat = _time.time()
        trail_count = {}  # si -> {W: count}
        trail_max_ratio = {}  # si -> max ratio over widest window
        time_since = {}  # si -> bars since last adverse spike (independent of W_list)
        ratio_map = {}  # si -> this spike's ratio
        for i, si in enumerate(spike_idx):
            cnt = {}
            mxr = 0.0
            last = None
            for j in range(i-1, -1, -1):
                sj = spike_idx[j]
                gap = si - sj
                r = rng_arr[sj] / med[sj] if med[sj] > 0 else 0
                if r > mxr:
                    mxr = r
                if gap < W_list[-1]:
                    last = sj
                    for W in W_list:
                        if gap < W:
                            cnt[W] = cnt.get(W, 0) + 1
                else:
                    break
            trail_count[si] = cnt
            trail_max_ratio[si] = mxr
            time_since[si] = (si - last) if last is not None else 999
            ratio_map[si] = float(rng_arr[si] / med[si]) if med[si] > 0 else 0
        print(f"  [perf] per-spike features {len(spike_idx)} spikes in {round(_time.time()-t_feat,1)}s")
        
        # Extra: finer time-since thresholds (for boom500 lead sweep)
        ts_fine_thresholds = [5, 10, 15, 20, 25, 30, 40, 60, 90]
        # Precompute filter_n + kept fractions for the fine sweep (no re-run of engine, just set math)
        
        # oracle population means (run each e+1 entry once, cached)
        t_oracle = _time.time()
        oracle_cache = {}  # e -> pnl
        solo_pnls, member_pnls, start_pnls = [], [], []
        full_pnl = []
        chunk = 500
        from partial_m1_corrected import prep, partial_trade as _pt
        # Map sym -> bare key for BROKER_FLOOR (keys are bare: 'crash500' not 'crash500_1min')
        bare = by_key(sym)["key"]
        df2 = prep(df.copy())
        for start in range(0, len(all_entries), chunk):
            chunk_entries = all_entries[start:start+chunk]
            for e in chunk_entries:
                si = e - 1
                tr = _pt(df2, bare, si, 3)
                if tr is not None:
                    oracle_cache[e] = tr["pnl"]
                    p = tr["pnl"]
                    full_pnl.append(p)
                    lbl = label_map.get(si, "solo")
                    if lbl == "solo":
                        solo_pnls.append(p)
                    elif lbl == "cluster_member":
                        member_pnls.append(p)
                    elif lbl == "cluster_start":
                        start_pnls.append(p)
            if (start//chunk)%4==0:
                print(f"    oracle chunk {start}/{len(all_entries)} done {round(_time.time()-t_oracle,1)}s")
        print(f"  [perf] oracle pop run {len(all_entries)} entries in {round(_time.time()-t_oracle,1)}s")
        df2 = None
        
        solo_mean = float(np.mean(solo_pnls)) if solo_pnls else 0.0
        member_mean = float(np.mean(member_pnls)) if member_pnls else 0.0
        start_mean = float(np.mean(start_pnls)) if start_pnls else 0.0
        full_mean = float(np.mean(full_pnl)) if full_pnl else 0.0
        print(f"  oracle: solo={solo_mean:.3f}(n={len(solo_pnls)}) member={member_mean:.3f}(n={len(member_pnls)}) start={start_mean:.3f}(n={len(start_pnls)}) full={full_mean:.3f}")
        
        candidate_specs = []
        def addc(name, entries):
            candidate_specs.append((disp, name, entries))
        for W in W_list:
            for K in K_list:
                addc(f"A_count_W{W}_K{K}", [e for e in all_entries if trail_count.get(e-1, {}).get(W, 0) < K])
        # B_maxratio: coarse sweep for all symbols + fine sweep for the two most promising
        for thr in [2.0, 3.0, 5.0, 8.0]:
            addc(f"B_maxratio_th{thr}", [e for e in all_entries if trail_max_ratio.get(e-1, 0) < thr])
        # Fine sweep for boom1000 (extreme start tail) and crash300 (best result)
        for thr in [3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 10.0, 12.0, 15.0]:
            addc(f"B_maxratio_fine_th{thr}", [e for e in all_entries if trail_max_ratio.get(e-1, 0) < thr])
        for thr in [5, 10, 15, 20, 25, 30, 40, 60, 90]:
            addc(f"C_timesince_th{thr}", [e for e in all_entries if time_since.get(e-1, 0) >= thr])
        for thr in [3, 5, 10, 20]:
            addc(f"D_regimeage_th{thr}", [e for e in all_entries if regime_age[e] >= thr])
        for thr in [5.0, 8.0, 10.0, 15.0, 20.0]:
            addc(f"E_spikeratio_th{thr}", [e for e in all_entries if ratio_map.get(e-1, 999) < thr])
        for W in [10, 15]:
            for K in [2]:
                for ts_thr in [5, 10, 20]:
                    for ra_thr in [3, 5]:
                        addc(f"F_combined_W{W}_K{K}_ts{ts_thr}_ra{ra_thr}",
                             [e for e in all_entries
                              if trail_count.get(e-1, {}).get(W, 0) < K
                              and time_since.get(e-1, 0) >= ts_thr
                              and regime_age[e] >= ra_thr])
        candidate_results = []
        t_cand = _time.time()
        split_point = len(all_entries) // 2
        h1_all = all_entries[:split_point]
        h2_all = all_entries[split_point:]
        for (disp2, name, entries) in candidate_specs:
            if len(entries) < 30:
                continue
            pnls = [oracle_cache[e] for e in entries if e in oracle_cache]
            filter_avg = float(np.mean(pnls)) if pnls else 0.0
            random_avgs = []
            for rs in range(5):
                ridx = np.random.default_rng(rs*17+7).choice(all_entries, size=min(len(entries), len(all_entries)), replace=False)
                rpnl = [oracle_cache[e] for e in ridx]
                random_avgs.append(float(np.mean(rpnl)) if rpnl else 0.0)
            random_mean = float(np.mean(random_avgs))
            random_min = float(np.min(random_avgs))
            random_max = float(np.max(random_avgs))
            clean_diff = filter_avg - random_mean
            # OOS H1/H2 splits
            h1e = [e for e in entries if e in h1_all]
            h2e = [e for e in entries if e in h2_all]
            h1_pnls = [oracle_cache[e] for e in h1e if e in oracle_cache]
            h2_pnls = [oracle_cache[e] for e in h2e if e in oracle_cache]
            h1_avg = float(np.mean(h1_pnls)) if h1_pnls else 0.0
            h2_avg = float(np.mean(h2_pnls)) if h2_pnls else 0.0
            # random controls per half
            h1_rand = []
            for rs in range(5):
                ridx = np.random.default_rng(rs*17+7).choice(h1_all, size=min(len(h1e), len(h1_all)), replace=False)
                rpnl = [oracle_cache[e] for e in ridx]
                h1_rand.append(float(np.mean(rpnl)) if rpnl else 0.0)
            h2_rand = []
            for rs in range(5):
                ridx = np.random.default_rng(rs*17+31+7).choice(h2_all, size=min(len(h2e), len(h2_all)), replace=False)
                rpnl = [oracle_cache[e] for e in ridx]
                h2_rand.append(float(np.mean(rpnl)) if rpnl else 0.0)
            h1_rand_mean = float(np.mean(h1_rand)) if h1_rand else 0.0
            h2_rand_mean = float(np.mean(h2_rand)) if h2_rand else 0.0
            h1_diff = h1_avg - h1_rand_mean
            h2_diff = h2_avg - h2_rand_mean
            solo_kept, member_kept, start_kept = 0, 0, 0
            solo_total, member_total, start_total = len(solo_pnls), len(member_pnls), len(start_pnls)
            for e in entries:
                si = e - 1
                lbl = label_map.get(si, "solo")
                if lbl == "solo":
                    solo_kept += 1
                elif lbl == "cluster_member":
                    member_kept += 1
                elif lbl == "cluster_start":
                    start_kept += 1
            solo_removal = 1.0 - solo_kept/solo_total if solo_total else 0
            member_removal = 1.0 - member_kept/member_total if member_total else 0
            start_removal = 1.0 - start_kept/start_total if start_total else 0
            candidate_results.append({
                "sym": sym, "name": name, "filter_n": len(entries),
                "filter_avg": filter_avg, "random_mean": random_mean,
                "random_min": random_min, "random_max": random_max,
                "clean_diff": clean_diff,
                "h1_filter_n": len(h1e), "h1_filter_avg": h1_avg, "h1_random_mean": h1_rand_mean,
                "h1_diff": h1_diff,
                "h2_filter_n": len(h2e), "h2_filter_avg": h2_avg, "h2_random_mean": h2_rand_mean,
                "h2_diff": h2_diff,
                "solo_removal": solo_removal, "member_removal": member_removal,
                "start_removal": start_removal,
                "solo_kept": solo_kept, "member_kept": member_kept, "start_kept": start_kept,
                "solo_total": solo_total, "member_total": member_total, "start_total": start_total,
            })
        print(f"  [perf] candidates done in {round(_time.time()-t_cand,1)}s")
        
        candidate_results.sort(key=lambda x: -x["clean_diff"])
        print()
        print(f"  top 8 candidates by clean_diff (out of {len(candidate_results)} tested):")
        for cr in candidate_results[:8]:
            print(f"    {cr['name']:30s} n={cr['filter_n']:5d} filt={cr['filter_avg']:+.3f} rand={cr['random_mean']:+.3f} diff={cr['clean_diff']:+.3f} | solo_rm={cr['solo_removal']:.2f} mem_rm={cr['member_removal']:.2f} start_rm={cr['start_removal']:.2f}")
        
        rows.extend(candidate_results)
    
    # Summary
    beats = [r for r in rows if r["clean_diff"] >= 0.05]
    print(f"\n\n=== SUMMARY ===")
    print(f"total candidates tested: {len(rows)}")
    print(f"candidates with clean_diff >= 0.05: {len(beats)}")
    print(f"candidates with clean_diff >= 0.10: {len([r for r in rows if r['clean_diff'] >= 0.10])}")
    
    print(f"\nTop 20 by FULL clean_diff (with OOS H1/H2):")
    rows.sort(key=lambda x: -x["clean_diff"])
    for r in rows[:20]:
        h1h2 = ""
        if "h1_diff" in r:
            h1h2 = f" | H1 diff={r['h1_diff']:+.3f}(n={r['h1_filter_n']}) H2 diff={r['h2_diff']:+.3f}(n={r['h2_filter_n']})"
        print(f"  {r['sym']:15s} {r['name']:34s} full_diff={r['clean_diff']:+.3f} (rand[{r['random_min']:+.3f},{r['random_max']:+.3f}]){h1h2} | solo_rm={r['solo_removal']:.2f} mem_rm={r['member_removal']:.2f} start_rm={r['start_removal']:.2f}")
    
    # OOS consistency check: how many top candidates have both H1 and H2 positive?
    oos_ok = [r for r in rows if r.get("h1_diff", 0) > 0 and r.get("h2_diff", 0) > 0 and r["clean_diff"] >= 0.05]
    print(f"\nOOS-consistent candidates (H1>0 AND H2>0, full_diff>=0.05): {len(oos_ok)}")
    for r in sorted(oos_ok, key=lambda x: -x["clean_diff"]):
        print(f"  {r['sym']:15s} {r['name']:34s} full={r['clean_diff']:+.3f} H1={r['h1_diff']:+.3f} H2={r['h2_diff']:+.3f} (n={r['filter_n']})")
    
    # Save
    import json
    with open("eval/solo_core_results.json", "w") as f:
        json.dump(rows, f, indent=2, default=str)
    print("\nSaved eval/solo_core_results.json")

if __name__ == "__main__":
    main()
