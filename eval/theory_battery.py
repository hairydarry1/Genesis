#!/usr/bin/env python3
"""
THEORY BATTERY T-A/T-B/T-C/T-D
===============================
Engine: verified corrected engine (eval/partial_m1_corrected) — gap-through fills,
half_exit partial add, per-symbol broker floors, 2xSL/1xSL, hold=3.
Data: data/candles_<sym>_1min.parquet (same as ICT/pullback tests).

T-C (centerpiece): cluster-start vs solo discharge. Distribution of spike range/ratio,
and "skip entry when the triggering spike is a cluster-start" vs matched-lag control.
T-A: M15 BOS break-rate in trailing ~90 min as per-trade risk (independent of direction).
T-B: bar-1 favorable distance buckets under the existing partial-add setup.
T-D: dense mode (every-bar gated + 10-bar cooldown) with soft-skip vs hard-mask on
     the 0-2 M15-age window.

NOTE: cluster_start label is ORACLE here (computed from the full spike set). The
forward-testable version ("skip if this spike AND we've already seen 2+ in trailing
window") is a follow-up.
"""
import numpy as np
import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from partial_m1_corrected import BROKER_FLOOR, half_exit, arrays

DATA = Path("data")
OUT = Path("docs")

SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]


def load(sym):
    df = pd.read_parquet(DATA / f"candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    if "epoch" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
    else:
        df["time"] = pd.to_datetime(df["time"])
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def m15_regime(df, sym):
    df = df.copy()
    m15 = df.resample("15min", on="time").agg(
        o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m15["c"].values; h = m15["h"].values; l = m15["l"].values
    n = len(m15)
    bull = np.zeros(n, dtype=bool); bear = np.zeros(n, dtype=bool)
    for t in range(5, n):
        if c[t] > h[t-4:t].max():
            bull[t] = True
        if c[t] < l[t-4:t].min():
            bear[t] = True
    reg = np.zeros(n, dtype=bool)
    for t in range(n):
        w = bull[max(0, t-6):t+1]
        if w.any():
            wb = bear[max(0, t-6):t+1]
            if not wb.any() or (np.where(w)[0][-1] > np.where(wb)[0][-1]):
                reg[t] = True
    m15t = m15["time"].values.astype("datetime64[ns]")
    df_t = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(m15t, df_t, side="right") - 1
    idx = np.clip(idx, 0, n-1)
    return reg[idx]


def m15_break_times(df):
    df = df.copy()
    m15 = df.resample("15min", on="time").agg(
        h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m15["c"].values; h = m15["h"].values; l = m15["l"].values
    breaks = []
    for t in range(5, len(m15)):
        if c[t] > h[t-4:t].max() or c[t] < l[t-4:t].min():
            breaks.append(m15["time"].iloc[t])
    return np.array(breaks) if breaks else np.array([])


def m15_age_since_last_bos(df, sym):
    df = df.copy()
    m15 = df.resample("15min", on="time").agg(
        h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m15["c"].values; h = m15["h"].values; l = m15["l"].values
    n = len(m15)
    is_bos = np.zeros(n, dtype=bool)
    for t in range(5, n):
        if c[t] > h[t-4:t].max() or c[t] < l[t-4:t].min():
            is_bos[t] = True
    age_m15 = np.zeros(n, dtype=int)
    cur = 0
    for t in range(n):
        w = is_bos[max(0, t-6):t+1]
        if w.any():
            cur = t - np.where(w)[0][-1]
        else:
            cur = cur + 1 if cur >= 0 else 0
        age_m15[t] = cur
    m15t = m15["time"].values.astype("datetime64[ns]")
    df_t = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(m15t, df_t, side="right") - 1
    idx = np.clip(idx, 0, n-1)
    return age_m15[idx], m15


def discharges(df, sym):
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > 3.0*med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]


def label_clusters(spike_idx, window_bars=10):
    n = len(spike_idx)
    cluster_start = np.zeros(n, dtype=bool)
    cluster_size = np.zeros(n, dtype=int)
    if n == 0:
        return cluster_start, cluster_size
    cid = 0
    i = 0
    idxs = spike_idx
    while i < n:
        j = i
        while j+1 < n and idxs[j+1]-idxs[j] <= window_bars:
            j += 1
        seg_len = j - i + 1
        if seg_len >= 3:
            for k in range(i, j+1):
                cluster_size[k] = seg_len
            cluster_start[i] = True
            cid += 1
        i = j+1
    return cluster_start, cluster_size


def trade_at_entry(df, sym, entry_bar, hold=3, partial=True):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2*sl_dist) if is_crash else (entry - 2*sl_dist)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, entry_bar+1, hold, n)
    add_b = False
    if partial:
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, entry_bar+1, 1, n)
        if rA1 == "timeout":
            fav = (closes[entry_bar+1] > entry) if is_crash else (closes[entry_bar+1] < entry)
            add_b = fav
    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, entry_bar+2, hold-1, n)
        pnl = 0.5*(pA - entry)*(1 if is_crash else -1) + \
              0.5*(pB - entry)*(1 if is_crash else -1)
    else:
        pnl = 0.5*(pA - entry)*(1 if is_crash else -1)
    return pnl


def run_entry_set(entries, df, sym, hold=3, partial=True):
    out = []
    for ei in entries:
        p = trade_at_entry(df, sym, ei, hold=hold, partial=partial)
        if p is not None:
            out.append(p)
    return out


def matched_lag_control(entries, df, max_lag=20, n_per_entry=1, seed=1):
    rng = np.random.RandomState(seed)
    rand = []
    n = len(df)
    entry_set = set(entries)
    for ei in entries:
        for _ in range(n_per_entry):
            cand = ei
            it = 0
            while cand == ei or cand in entry_set or any(abs(cand - e) < 3 for e in entries):
                cand = int(rng.randint(max(0, ei-max_lag), min(n-1, ei+max_lag+1)))
                it += 1
                if it > 50:
                    break
            rand.append(cand)
    return rand


def main():
    np.random.seed(7)
    frames = {s: load(s) for s in SYMS}
    report = []
    lines = []
    lines.append("# Theory Battery — T-A / T-B / T-C / T-D")
    lines.append("")
    lines.append("## Method")
    lines.append("Verified corrected engine (partial_m1_corrected: gap-through fills, half_exit partial add, "
                 "per-symbol broker floors, 2xSL/1xSL, hold=3).")
    lines.append("Spike = adverse bar with range > 3x trailing-100 median. "
                 "Crash family = LONG drift (enter at e+1); Boom family = SHORT drift (enter at e+1).")
    lines.append("M15 regime / BOS from closed-M15-bar discipline (no lookahead). OOS halves by time. Matched-lag controls.")
    lines.append("All PnLs in points per trade (1-lot equiv); not dollars; no spread/commission.")
    lines.append("")
    lines.append("NOTE: T-C cluster_start label is ORACLE (computed from full spike set). "
                 "The forward-testable version ('skip if this spike AND already saw 2+ in trailing window') "
                 "is a follow-up.")
    lines.append("")

    for sym in SYMS:
        df = frames[sym]
        print(f"\n=== {sym} ===")
        spikes_idx = discharges(df, sym)
        n_spikes = len(spikes_idx)
        print(f"  spikes detected: {n_spikes}")
        if n_spikes == 0:
            continue

        # ---- T-C: cluster labeling + distribution ----
        cls_start, cls_size = label_clusters(spikes_idx, window_bars=10)
        n_cluster_start = int(cls_start.sum())
        n_solo = int((~cls_start).sum())
        print(f"  T-C: cluster-starts={n_cluster_start}, solo-ish={n_solo}")

        cls_ranges = [float(df["range"].iloc[int(i)]) for i, k in zip(spikes_idx, range(len(spikes_idx))) if cls_start[k]]
        solo_ranges = [float(df["range"].iloc[int(i)]) for i, k in zip(spikes_idx, range(len(spikes_idx))) if not cls_start[k]]
        cls_ratios = [float(df["range"].iloc[int(i)]/df["median_range"].iloc[int(i)]) for i, k in zip(spikes_idx, range(len(spikes_idx))) if cls_start[k] and df["median_range"].iloc[int(i)] > 0]
        solo_ratios = [float(df["range"].iloc[int(i)]/df["median_range"].iloc[int(i)]) for i, k in zip(spikes_idx, range(len(spikes_idx))) if not cls_start[k] and df["median_range"].iloc[int(i)] > 0]
        report.append({
            "sym": sym, "n_spikes": n_spikes,
            "n_cluster_start": n_cluster_start, "n_solo": n_solo,
            "cls_range_mean": float(np.mean(cls_ranges)) if cls_ranges else None,
            "solo_range_mean": float(np.mean(solo_ranges)) if solo_ranges else None,
            "cls_ratio_mean": float(np.mean(cls_ratios)) if cls_ratios else None,
            "solo_ratio_mean": float(np.mean(solo_ratios)) if solo_ratios else None,
        })

        # ---- T-C: prospective entry test ----
        entries = [int(i)+1 for i in spikes_idx if int(i)+1 < len(df)-1]
        entries = sorted(set(entries))
        print(f"  T-C: discharge-following entries (e+1): {len(entries)}")

        res_real = run_entry_set(entries, df, sym)
        real_pnl = np.array(res_real) if res_real else np.array([])
        real_avg = float(real_pnl.mean()) if len(real_pnl) else 0.0
        real_wr = float((real_pnl > 0).mean()) if len(real_pnl) else 0.0

        rand_entries = matched_lag_control(entries, df, max_lag=20, n_per_entry=1, seed=11)
        res_rand = run_entry_set(rand_entries, df, sym)
        rand_pnl = np.array(res_rand) if res_rand else np.array([])
        rand_avg = float(rand_pnl.mean()) if len(rand_pnl) else 0.0
        print(f"  T-C: real avg={real_avg:.3f} (WR {real_wr:.2f}), rand avg={rand_avg:.3f}, diff={real_avg-rand_avg:.3f}")

        cls_map = {}
        for k, si in enumerate(spikes_idx):
            cls_map[int(si)] = bool(cls_start[k])
        non_cls_entries = [e for e in entries if not cls_map.get(e-1, False)]
        cls_entries = [e for e in entries if cls_map.get(e-1, False)]
        res_non = run_entry_set(non_cls_entries, df, sym)
        res_cls = run_entry_set(cls_entries, df, sym)
        non_pnl = np.array(res_non) if res_non else np.array([])
        cls_pnl = np.array(res_cls) if res_cls else np.array([])
        non_avg = float(non_pnl.mean()) if len(non_pnl) else 0.0
        cls_avg = float(cls_pnl.mean()) if len(cls_pnl) else 0.0
        print(f"  T-C: non-cluster-start entries avg={non_avg:.3f} (n={len(non_cls_entries)}), "
              f"cluster-start entries avg={cls_avg:.3f} (n={len(cls_entries)})")
        rand_non = matched_lag_control(non_cls_entries, df, max_lag=20, n_per_entry=1, seed=21)
        res_rand_non = run_entry_set(rand_non, df, sym)
        rand_non_pnl = np.array(res_rand_non) if res_rand_non else np.array([])
        rand_non_avg = float(rand_non_pnl.mean()) if len(rand_non_pnl) else 0.0
        print(f"  T-C: non-cluster-start control avg={rand_non_avg:.3f}, diff={non_avg-rand_non_avg:.3f}")

        t_c = {
            "sym": sym,
            "real_avg": real_avg, "rand_avg": rand_avg, "diff": real_avg-rand_avg,
            "non_cls_avg": non_avg, "cls_avg": cls_avg,
            "non_cls_n": len(non_cls_entries), "cls_n": len(cls_entries),
            "non_cls_vs_ctrl": non_avg - rand_non_avg,
        }

        # ---- T-A: regime break-rate buckets ----
        breaks = m15_break_times(df)
        K = 6  # M15 bars (~90 min)
        entry_ts = np.array([df["time"].iloc[e].timestamp() for e in entries])
        break_ts = np.sort(np.array([bt.timestamp() for bt in breaks])) if len(breaks) else np.array([])
        def break_rate_before(t):
            lo = t - K*15*60
            if len(break_ts) == 0:
                return 0
            return int(np.searchsorted(break_ts, t, side="right") - np.searchsorted(break_ts, lo, side="right"))
        rates = np.array([break_rate_before(t) for t in entry_ts])
        def bucket(r):
            if r <= 0: return "0"
            if r == 1: return "1"
            if r == 2: return "2"
            return "3+"
        buckets = {b: [] for b in ["0", "1", "2", "3+"]}
        for e, rt in zip(entries, rates):
            buckets[bucket(rt)].append(e)
        bucket_avgs = {}
        for b in ["0", "1", "2", "3+"]:
            be = buckets[b]
            if not be:
                bucket_avgs[b] = None
                continue
            rb = run_entry_set(be, df, sym)
            bucket_avgs[b] = float(np.mean(rb)) if rb else 0.0
        largest_bucket = max(buckets, key=lambda b: len(buckets[b]))
        rand_largest = matched_lag_control(buckets[largest_bucket], df, max_lag=20, n_per_entry=1, seed=31)
        res_rand_largest = run_entry_set(rand_largest, df, sym)
        largest_ctrl = float(np.mean(res_rand_largest)) if res_rand_largest else 0.0
        print(f"  T-A: break-rate buckets (per-trade avg): {bucket_avgs}; largest={largest_bucket} ctrl={largest_ctrl:.3f}")
        t_a = {"sym": sym, "buckets": bucket_avgs, "largest_bucket": largest_bucket, "largest_ctrl": largest_ctrl}

        # ---- T-B: bar-1 quality ----
        bar1_rows = []
        for e in entries:
            if e+1 >= len(df):
                continue
            entry_px = float(df["close"].iloc[e])
            bar1_px = float(df["close"].iloc[e+1])
            if "crash" in sym:
                fav = bar1_px - entry_px
            else:
                fav = entry_px - bar1_px
            bar1_rows.append({"entry_idx": e, "bar1_fav": fav})
        b1 = pd.DataFrame(bar1_rows)
        if len(b1) == 0:
            continue
        med_bar1_range = float(np.median(np.abs(b1["bar1_fav"].to_numpy()))) if len(b1) > 0 else 1.0
        small_th = max(med_bar1_range*0.5, 0.1)
        large_th = max(med_bar1_range*1.5, 0.3)
        def b1_bucket(fav):
            if fav <= 0: return "<=0"
            if fav <= small_th: return f"(0,{small_th:.2f}]"
            if fav <= large_th: return f"({small_th:.2f},{large_th:.2f}]"
            return f">{large_th:.2f}"
        b1["bucket"] = b1["bar1_fav"].apply(b1_bucket)
        b1_buckets = {b: g["entry_idx"].tolist() for b, g in b1.groupby("bucket")}
        b1_avgs = {}
        for b, eidx in b1_buckets.items():
            rb = run_entry_set(eidx, df, sym)
            b1_avgs[b] = float(np.mean(rb)) if rb else 0.0
        print(f"  T-B: bar1-fav buckets (per-trade avg): {b1_avgs}")
        t_b = {"sym": sym, "b1_buckets": b1_avgs, "med_bar1_range": med_bar1_range}

        # ---- T-D: dense mode ----
        age_arr, _ = m15_age_since_last_bos(df, sym)
        reg = m15_regime(df, sym)
        dense_entries = []
        cooldown_until = -1
        loss_events = []
        for i in range(60, len(df)-1):
            if i <= cooldown_until:
                continue
            if not reg[i]:
                continue
            dense_entries.append(i)
            r = trade_at_entry(df, sym, i, hold=3, partial=True)
            if r is not None and r < 0:
                cooldown_until = i + 10
                loss_events.append(i)
        print(f"  T-D: dense entries (gated, cooldown) count={len(dense_entries)}, losses={len(loss_events)}")
        age_buckets = {b: [] for b in ["0-2", "3-8", "9+"]}
        for e in dense_entries:
            a = int(age_arr[e]) if e < len(age_arr) else 0
            if a <= 2:
                age_buckets["0-2"].append(e)
            elif a <= 8:
                age_buckets["3-8"].append(e)
            else:
                age_buckets["9+"].append(e)
        hard_entries = list(age_buckets["3-8"])
        soft_entries = list(age_buckets["3-8"] + age_buckets["9+"])
        base_entries = list(dense_entries)
        def dense_eval(es):
            if not es:
                return (0.0, 0)
            rb = run_entry_set(es, df, sym)
            return (float(np.mean(rb)) if rb else 0.0, len(rb))
        base_avg, base_n = dense_eval(base_entries)
        hard_avg, hard_n = dense_eval(hard_entries)
        soft_avg, soft_n = dense_eval(soft_entries)
        rand_soft = matched_lag_control(soft_entries, df, max_lag=20, n_per_entry=1, seed=41)
        res_rand_soft = run_entry_set(rand_soft, df, sym)
        soft_ctrl = float(np.mean(res_rand_soft)) if res_rand_soft else 0.0
        print(f"  T-D: dense base avg={base_avg:.3f} (n={base_n}), hard-mask(3-8) avg={hard_avg:.3f} (n={hard_n}), "
              f"soft-skip(3+) avg={soft_avg:.3f} (n={soft_n}), soft ctrl={soft_ctrl:.3f}")
        t_d = {"sym": sym, "dense_base": (base_avg, base_n), "hard_3_8": (hard_avg, hard_n),
               "soft_3plus": (soft_avg, soft_n), "soft_ctrl": soft_ctrl}

        lines.append(f"### {sym}")
        lines.append(f"- spikes={n_spikes} (cluster-start={n_cluster_start}, solo={n_solo})")
        lines.append(f"- T-C: discharge-following e+1 avg={real_avg:.3f} (WR {real_wr:.2f}), rand avg={rand_avg:.3f}, diff={real_avg-rand_avg:.3f}")
        lines.append(f"  - non-cluster-start entries avg={non_avg:.3f} (n={len(non_cls_entries)}) vs ctrl {rand_non_avg:.3f} -> diff {non_avg-rand_non_avg:.3f}")
        lines.append(f"  - cluster-start entries avg={cls_avg:.3f} (n={len(cls_entries)})")
        lines.append(f"- T-A: break-rate buckets (per-trade avg): {bucket_avgs}; largest={largest_bucket} ctrl={largest_ctrl:.3f}")
        lines.append(f"- T-B: bar1-fav buckets (per-trade avg): {b1_avgs} (med bar1 range={med_bar1_range:.3f})")
        lines.append(f"- T-D: dense base avg={base_avg:.3f} (n={base_n}), hard-mask 3-8 avg={hard_avg:.3f} (n={hard_n}), soft-skip 3+ avg={soft_avg:.3f} (n={soft_n}), soft ctrl={soft_ctrl:.3f}")
        lines.append("")
        lines.append(f"#### {sym} detail")
        lines.append(f"- T-C real n={len(real_pnl)}, rand n={len(rand_pnl)}")
        lines.append(f"- T-C non-cluster-start n={len(non_cls_entries)} (ctrl n={len(res_rand_non)}), cluster-start n={len(cls_entries)}")
        lines.append(f"- T-A bucket counts: { {b: len(buckets[b]) for b in buckets} }")
        lines.append(f"- T-B bucket counts: { {b: len(b1_buckets[b]) for b in b1_buckets} }")
        lines.append(f"- T-D age bucket counts: { {b: len(age_buckets[b]) for b in age_buckets} }; losses={len(loss_events)}")
        lines.append("")

    lines.append("## T-C distribution summary (cluster-start vs solo)")
    lines.append("| sym | n_spikes | cluster-start | solo | cls_range_mean | solo_range_mean | cls_ratio_mean | solo_ratio_mean |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in report:
        def f(x):
            return f"{x:.2f}" if x is not None else "NA"
        lines.append(f"| {r['sym']} | {r['n_spikes']} | {r['n_cluster_start']} | {r['n_solo']} | "
                     f"{f(r['cls_range_mean'])} | {f(r['solo_range_mean'])} | "
                     f"{f(r['cls_ratio_mean'])} | {f(r['solo_ratio_mean'])} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "THEORY_BATTERY_PART1.md").write_text("\n".join(lines))
    print("\nWrote docs/THEORY_BATTERY_PART1.md")


if __name__ == "__main__":
    main()
