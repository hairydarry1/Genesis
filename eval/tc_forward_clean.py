#!/usr/bin/env python3
"""
CLEAN FORWARD T-C — unbiased random sub-sample control + cluster-label diagnostic

Replaces the contaminated matched-lag control with an unbiased control:
  for each (W, K), draw N random entries from the FULL e+1 set (N = filter set size),
  uniformly, with multiple seeds. Compare filter avg to the random sub-sample avg across seeds.
  If filter avg > random avg consistently, the filter selects a better-than-random subset of the
  full population — the honest claim.

Also adds the cluster-label diagnostic: for every entry in the full e+1 set, label it as
  - cluster-start: the FIRST spike of a >=3-in-10 cluster (oracle label)
  - cluster-member: a spike in a >=3-in-10 cluster that is NOT the first
  - solo: not in any >=3-in-10 cluster
Then for each (W, K) filter, compute:
  - how many of each label the filter KEEPS
  - how many of each label the filter REMOVES
  - the filter's removal rate per label
  - the filtered set's avg per label (so we can see whether the filter is removing the bad
    cluster-start entries and keeping the good solo entries, or whether it's also removing good
    entries and keeping bad ones)

This lets us compute the filter's complementary value vs the reactive skip:
  - the reactive skip removes cluster-MEMBER entries (the 2nd, 3rd, ... spikes of a cluster)
  - the prospective filter removes cluster-STARTING entries (the 1st spike of a cluster)
  - if the prospective filter removes cluster-start entries effectively, it's complementary to the
    reactive skip and worth adding to the live book as a pre-entry filter

Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors,
2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.
"""
import numpy as np
import pandas as pd
import sys
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent))
from partial_m1_corrected import BROKER_FLOOR, half_exit, arrays

DATA = Path("data")
OUT = Path("docs")
SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]

SPIKE_MULT = 3.0


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


def discharges(df, sym):
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]


def cluster_labels(spike_idx, window_bars=10):
    """Label each spike as cluster-start (first of >=3 in window), cluster-member (in cluster
    but not first), or solo (not in any >=3 cluster). Returns dict spike_idx -> label (0=solo,
    1=member, 2=start)."""
    nsp = len(spike_idx)
    labels = np.zeros(nsp, dtype=int)  # 0=solo, 1=member, 2=start
    if nsp == 0:
        return {int(spike_idx[k]): int(labels[k]) for k in range(nsp)}
    i = 0
    idxs = spike_idx
    while i < nsp:
        j = i
        while j + 1 < nsp and idxs[j + 1] - idxs[j] <= window_bars:
            j += 1
        seg_len = j - i + 1
        if seg_len >= 3:
            labels[i] = 2  # start
            for k in range(i + 1, j + 1):
                labels[k] = 1  # member
        # else all solo (label 0, already)
        i = j + 1
    return {int(spike_idx[k]): int(labels[k]) for k in range(nsp)}


def spike_count_trailing(spike_idx, i, window_bars):
    lo = i - window_bars
    return int(np.searchsorted(spike_idx, i, side="right") -
               np.searchsorted(spike_idx, lo, side="right"))


def forward_entry_set(spike_idx, window_bars, k_min, df_len):
    entries = []
    for i in spike_idx:
        cnt = spike_count_trailing(spike_idx, i, window_bars)
        if cnt < k_min:
            e = i + 1
            if 0 < e < df_len - 2:
                entries.append(e)
    return sorted(set(entries))


def trade_at_entry(df, sym, entry_bar, hold=3, partial=True):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, entry_bar + 1, hold, n)
    add_b = False
    if partial:
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, entry_bar + 1, 1, n)
        if rA1 == "timeout":
            fav = (closes[entry_bar + 1] > entry) if is_crash else (closes[entry_bar + 1] < entry)
            add_b = fav
    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, entry_bar + 2, hold - 1, n)
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return pnl


def run_set(entries, df, sym, hold=3, partial=True):
    out = []
    for ei in entries:
        p = trade_at_entry(df, sym, ei, hold=hold, partial=partial)
        if p is not None:
            out.append(p)
    return out


def random_subsample(full_set, n, seed):
    rng = np.random.RandomState(seed)
    if n >= len(full_set):
        return list(full_set)
    return sorted(rng.choice(full_set, size=n, replace=False).tolist())


def main():
    lines = []
    lines.append("# Clean Forward T-C — unbiased random sub-sample control + cluster-label diagnostic")
    lines.append("")
    lines.append("## Method")
    lines.append("Engine: partial_m1_corrected (gap-through, half_exit partial, per-symbol broker floors, "
                 "2xSL/1xSL, hold=3). PnL in points per trade, 1-lot equiv, no spread/commission.")
    lines.append("Spike = adverse bar with range > 3x trailing-100 median. Crash family = LONG drift at e+1; "
                 "Boom family = SHORT drift at e+1.")
    lines.append("Forward filter: at spike close i, enter at i+1 only if fewer than K adverse spikes in trailing "
                 "W M1 bars [i-W, i). K in {2, 3}; W in {10, 15, 20}.")
    lines.append("Control: UNBIASED random sub-sample of the FULL e+1 set, same size as the filter set, "
                 "multiple seeds (5). Compare filter avg to random sub-sample avg across seeds.")
    lines.append("Cluster label (oracle): each spike labeled cluster-start (first of >=3 in 10 bars), "
                 "cluster-member (in cluster but not first), or solo (not in any >=3 cluster).")
    lines.append("Diagnostic: for each filter, removal rate per label, and filtered-set avg per label kept.")
    lines.append("")

    all_results = []
    for sym in SYMS:
        df = load(sym)
        n = len(df)
        spikes_idx = discharges(df, sym)
        print(f"\n=== {sym} ===  spikes={len(spikes_idx)}")

        lab = cluster_labels(spikes_idx, window_bars=10)
        full_entries = [int(i) + 1 for i in spikes_idx if int(i) + 1 < n - 2]
        full_entries = sorted(set(full_entries))
        # label each full entry by its spike's cluster label
        entry_label = {}
        for e in full_entries:
            s = e - 1
            entry_label[e] = lab.get(s, 0)  # default solo if spike not in lab (shouldn't happen)
        counts = defaultdict(int)
        for e in full_entries:
            counts[entry_label[e]] += 1
        print(f"  full e+1 population: solo={counts[0]}, member={counts[1]}, start={counts[2]}, total={len(full_entries)}")

        full_pnls = run_set(full_entries, df, sym)
        full_avg = float(np.mean(full_pnls)) if full_pnls else 0.0
        full_by_label = {}
        for lbl in [0, 1, 2]:
            es = [e for e in full_entries if entry_label[e] == lbl]
            pnls = run_set(es, df, sym)
            full_by_label[lbl] = float(np.mean(pnls)) if pnls else 0.0
        print(f"  full avg={full_avg:.3f}  by label: solo={full_by_label[0]:.3f} (n={counts[0]}), "
              f"member={full_by_label[1]:.3f} (n={counts[1]}), start={full_by_label[2]:.3f} (n={counts[2]})")

        row = {
            "sym": sym,
            "full_avg": full_avg,
            "full_by_label": full_by_label,
            "counts": dict(counts),
            "configs": [],
        }

        for W in [10, 15, 20]:
            for K in [2, 3]:
                filt = forward_entry_set(spikes_idx, W, K, n)
                filt_pnls = run_set(filt, df, sym)
                filt_avg = float(np.mean(filt_pnls)) if filt_pnls else 0.0
                filt_n = len(filt_pnls)

                # unbiased random sub-sample control, 5 seeds
                rand_avgs = []
                for seed in range(5):
                    rnd = random_subsample(full_entries, filt_n, seed=1000 + seed)
                    rnd_pnls = run_set(rnd, df, sym)
                    rand_avgs.append(float(np.mean(rnd_pnls)) if rnd_pnls else 0.0)
                rand_mean = float(np.mean(rand_avgs))
                rand_lo = float(min(rand_avgs))
                rand_hi = float(max(rand_avgs))

                # cluster-label diagnostic on the filter
                filt_by_label = {}
                filt_removed = {}
                for lbl in [0, 1, 2]:
                    full_lbl_set = set(e for e in full_entries if entry_label[e] == lbl)
                    filt_lbl_set = set(e for e in filt if entry_label.get(e, 0) == lbl)
                    kept = len(filt_lbl_set)
                    removed = len(full_lbl_set) - kept
                    filt_by_label[lbl] = {
                        "kept": kept,
                        "removed": removed,
                        "removal_rate": removed / len(full_lbl_set) if len(full_lbl_set) else float("nan"),
                        "kept_avg": float(np.mean(run_set(list(filt_lbl_set), df, sym))) if filt_lbl_set else float("nan"),
                    }
                    filt_removed[lbl] = removed

                clean_diff = filt_avg - rand_mean
                print(f"  W={W} K={K}: filter n={filt_n} avg={filt_avg:.3f} | random sub-sample mean={rand_mean:.3f} "
                      f"range=[{rand_lo:.3f}, {rand_hi:.3f}] | clean diff={clean_diff:+.3f}")
                print(f"    label removal: solo={filt_by_label[0]['removal_rate']:.3f} "
                      f"({filt_by_label[0]['kept']}/{counts[0]}), "
                      f"member={filt_by_label[1]['removal_rate']:.3f} "
                      f"({filt_by_label[1]['kept']}/{counts[1]}), "
                      f"start={filt_by_label[2]['removal_rate']:.3f} "
                      f"({filt_by_label[2]['kept']}/{counts[2]})")
                print(f"    label kept-avg: solo={filt_by_label[0]['kept_avg']:.3f}, "
                      f"member={filt_by_label[1]['kept_avg']:.3f}, start={filt_by_label[2]['kept_avg']:.3f}")

                row["configs"].append({
                    "W": W, "K": K,
                    "filter_n": filt_n,
                    "filter_avg": filt_avg,
                    "rand_mean": rand_mean,
                    "rand_lo": rand_lo,
                    "rand_hi": rand_hi,
                    "clean_diff": clean_diff,
                    "label_removal": {
                        "solo": filt_by_label[0]["removal_rate"],
                        "member": filt_by_label[1]["removal_rate"],
                        "start": filt_by_label[2]["removal_rate"],
                    },
                    "label_kept": {
                        "solo": filt_by_label[0]["kept"],
                        "member": filt_by_label[1]["kept"],
                        "start": filt_by_label[2]["kept"],
                    },
                    "label_kept_avg": {
                        "solo": filt_by_label[0]["kept_avg"],
                        "member": filt_by_label[1]["kept_avg"],
                        "start": filt_by_label[2]["kept_avg"],
                    },
                })

        all_results.append(row)
        lines.append(f"### {sym}")
        lines.append(f"- full e+1: avg={full_avg:.3f} (solo={full_by_label[0]:.3f} n={counts[0]}, "
                     f"member={full_by_label[1]:.3f} n={counts[1]}, start={full_by_label[2]:.3f} n={counts[2]})")
        lines.append(f"- full-population label means: solo={full_by_label[0]:.3f}, member={full_by_label[1]:.3f}, start={full_by_label[2]:.3f}")
        lines.append("")
        lines.append("| W | K | filter n | filter avg | random mean | random range | clean diff | "
                     "solo removal | member removal | start removal |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for c in row["configs"]:
            lines.append(f"| {c['W']} | {c['K']} | {c['filter_n']} | {c['filter_avg']:.3f} | "
                         f"{c['rand_mean']:.3f} | [{c['rand_lo']:.3f}, {c['rand_hi']:.3f}] | "
                         f"{c['clean_diff']:+.3f} | "
                         f"{c['label_removal']['solo']:.3f} | {c['label_removal']['member']:.3f} | "
                         f"{c['label_removal']['start']:.3f} |")
        lines.append("")
        lines.append("#### Label kept-avg for each filter (W=10 K=2 unless noted)")
        for c in row["configs"]:
            if c["W"] == 10 and c["K"] == 2:
                lines.append(f"- {sym} W=10 K=2: kept-avg solo={c['label_kept_avg']['solo']:.3f}, "
                             f"member={c['label_kept_avg']['member']:.3f}, start={c['label_kept_avg']['start']:.3f}")
        lines.append("")

    lines.append("## Interpretation")
    lines.append("- clean diff = filter avg - random sub-sample mean. Positive = filter selects a better-than-random")
    lines.append("  subset of the full population. The random sub-sample is unbiased (drawn uniformly from the full set,")
    lines.append("  same size as the filter set), so this is the honest comparison.")
    lines.append("- label removal rates: solo removal should be LOW (we want to keep solo entries), start removal should be")
    lines.append("  HIGH (we want to remove cluster-start entries). member removal is the reactive skip's job, so the forward")
    lines.append("  filter's member removal is a bonus if it happens (it means the filter also removes some cluster members),")
    lines.append("  but it's not the primary target.")
    lines.append("- label kept-avg: if the filter keeps solo entries with high avg and start entries with low avg, it's")
    lines.append("  doing the right thing within the kept set. If kept-avg start is high, the filter is keeping bad entries.")
    lines.append("")

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "TC_FORWARD_CLEAN_PART1.md").write_text("\n".join(lines))
    print("\nWrote docs/TC_FORWARD_CLEAN_PART1.md")


if __name__ == "__main__":
    main()
