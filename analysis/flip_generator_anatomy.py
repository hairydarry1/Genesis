#!/usr/bin/env python3
"""Generator anatomy: is the flip1000 spike process really memoryless?

The user's thesis: the index is ALGORITHMIC, so the generator itself can be
the edge. Deriv's spec says one large move every N ticks *on average* with
direction "never predetermined" — i.e., claimed geometric arrivals. Three
forensic tests on the healed 12-day archive (Sep 3-14, all gap-masked):

  1. HAZARD — P(spike | gap=g since last spike). Geometric => flat hazard.
     Rising hazard = an internal timer = predictable spike timing.
     Also: variance of inter-spike gaps vs geometric expectation.
     Overdispersion => clustering => "spikes beget spikes" timing edge.

  2. POST-SPIKE DRIFT — median signed drift per tick over the next H ticks
     after up- vs down-spikes. Quantifies the grind leg that pays the book
     and validates the TIMEBND patience scale in *ticks*, not vibes.

  3. CROSS-SYMBOL SPIKE CORRELATION — the seed-sharing hypothesis. If the
     generator draws one spike event shared across the four variants, spike
     times co-occur across symbols. 60s-bin spike counts, pairwise Pearson
     r, and a within-day permutation null (200 shuffles). Any significant
     co-occurrence is a generator fingerprint no human-market concept has.

Spike definition matches the fleet: |delta| > 5.0 x rolling median
(frozen from warmup, same as flip_concepts/flip_entry_filters).

Usage: python analysis/flip_generator_anatomy.py
"""
import itertools
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MULT

SYMBOLS = ["flip150", "flip300", "flip500", "flip1000"]
DRIFT_H = (60, 300, 1800)   # post-spike drift horizons, in ticks
PERM_N = 200


def spikes_for(sym):
    """Return (ms, big_mask, dir_array) with fleet spike definition."""
    ms, px = load(sym)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    d = np.diff(px)
    big = np.abs(d) > MULT * med
    idx = np.flatnonzero(big)          # spike delta happened between idx-1,idx
    return ms, px, med, idx, np.sign(d[idx]), len(px)


def main():
    data = {s: spikes_for(s) for s in SYMBOLS}

    print("=== 0. SPIKE RATES vs SPEC (one per N ticks on average) ===")
    for s in SYMBOLS:
        ms, px, med, idx, sgn, n = data[s]
        days = (ms[-1] - ms[WARMUP]) / 86_400_000
        n_sp = len(idx)
        spec = int(s.replace("flip", ""))
        mean_gap_obs = (len(px) - WARMUP) / max(1, n_sp)
        print(f"  {s:9s} spikes={n_sp:5d}  observed mean gap = {mean_gap_obs:6.0f} ticks"
              f"   (spec N={spec})")

    print("\n=== 1. HAZARD: P(spike | gap=g since previous spike) ===")
    for s in SYMBOLS:
        ms, px, med, idx, sgn, n = data[s]
        gaps = np.diff(idx)
        # geometric expectation with matched mean
        p_hat = 1.0 / gaps.mean()
        var_exp = (1 - p_hat) / p_hat**2
        var_obs = gaps.var()
        bins = [(1, 250), (251, 500), (501, 1000), (1001, 1500),
                (1501, 2500), (2501, 10**9)]
        row = []
        for lo, hi in bins:
            sel = gaps[(gaps >= lo) & (gaps <= hi)]
            n_b = len(sel)
            # hazard within bin: spikes landing in bin / gaps reaching bin start
            reach = (gaps >= lo).sum()
            row.append(f"{lo}-{hi if hi<10**9 else 'inf'}:{n_b/max(1,reach)*100:.1f}%")
        print(f"  {s:9s} n_gaps={len(gaps):5d} mean_gap={gaps.mean():7.1f} "
              f"p={p_hat:.5f}  var obs/exp = {var_obs/var_exp:.2f}x")
        print(f"            hazard by gap: {'  '.join(row)}")

    print("\n=== 2. POST-SPIKE DRIFT (median signed px-change per tick, x1000; base = px[j+1], spike already landed) ===")
    for s in SYMBOLS:
        ms, px, med, idx, sgn, n = data[s]
        out = []
        for H in DRIFT_H:
            up_dr, dn_dr = [], []
            for k, j in enumerate(idx):
                if j + 1 + H >= len(px):
                    continue
                drift = (px[j + 1 + H] - px[j + 1]) / H * 1000
                (up_dr if sgn[k] > 0 else dn_dr).append(drift)
            out.append((H,
                        np.median(up_dr) if up_dr else float("nan"),
                        np.median(dn_dr) if dn_dr else float("nan")))
        cells = "  ".join(f"H={H}: up{u:+.2f} dn{dnn:+.2f}" for H, u, dnn in out)
        print(f"  {s:9s} {cells}")

    print("\n=== 1b. OVERDISPERSION ROBUSTNESS: threshold sweep on flip1000 ===")
    for x in (5.0, 8.0, 12.0, 20.0):
        ms, px = load("flip1000")
        med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
        idx = np.flatnonzero(np.abs(np.diff(px)) > x * med)
        gaps = np.diff(idx)
        p_hat = 1.0 / gaps.mean()
        vr = gaps.var() / ((1 - p_hat) / p_hat**2)
        spec = int("flip1000".replace("flip", ""))
        print(f"  thr={x:4.1f}x  spikes={len(idx):5d}  mean_gap={gaps.mean():6.1f}  "
              f"spec_N={spec}  var_ratio={vr:.2f}x")

    print("\n=== 3. CROSS-SYMBOL SPIKE CO-OCCURRENCE (60s bins, permutation null) ===")
    spike_sets = {}
    for s in SYMBOLS:
        ms, px, med, idx, sgn, n = data[s]
        spike_sets[s] = ms[idx]  # spike timestamps
    day_of = lambda t: int(t // 86_400_000)

    def bin_counts(times, t0, t1):
        b = (times - t0) // 60_000
        cnt = np.bincount(b.astype(np.int64),
                          minlength=int((t1 - t0) // 60_000) + 1)
        return cnt

    print(f"  {'pair':22s} {'n_both':>7s} {'perm_mean':>10s} {'perm_p':>8s} {'r':>7s}")
    for a, b in itertools.combinations(SYMBOLS, 2):
        ta, tb = spike_sets[a], spike_sets[b]
        t0, t1 = min(ta[0], tb[0]), max(ta[-1], tb[-1])
        ca, cb = bin_counts(ta, t0, t1), bin_counts(tb, t0, t1)
        if ca.std() == 0 or cb.std() == 0:
            print(f"  {a+'/'+b:22s} degenerate")
            continue
        r = float(np.corrcoef(ca, cb)[0, 1])
        both_obs = int(np.sum((ca > 0) & (cb > 0)))
        # permutation: shuffle b's spikes within their own UTC day
        days_b = np.array([day_of(t) for t in tb])
        rng = np.random.default_rng(7)
        perm = []
        for _ in range(PERM_N):
            sh = tb.copy()
            for dd in np.unique(days_b):
                m = days_b == dd
                seg = sh[m]
                span = seg.max() - seg.min()
                sh[m] = np.sort(seg.min() + rng.random(len(seg)) * span)
            cbp = bin_counts(sh, t0, t1)
            perm.append(int(np.sum((ca > 0) & (cbp > 0))))
        perm = np.array(perm)
        pval = (np.sum(perm >= both_obs) + 1) / (PERM_N + 1)
        print(f"  {a+'/'+b:22s} {both_obs:7d} {perm.mean():10.1f} {pval:8.3f} {r:+7.3f}")
    print("  (perm_p ~ 0.5 => independent; perm_p < 0.05 => shared scheduling)")

    print("\n=== 4. DIRECTION MEMORY at the generator level (all symbols) ===")
    for s in SYMBOLS:
        ms, px, med, idx, sgn, n = data[s]
        sgn = sgn[:-1]
        nxt = np.sign(np.diff(sgn))
        # P(next same dir | current dir), and P(flip | long run)
        same = (nxt == 0).mean()
        runs_end = []
        run = 1
        for k in range(1, len(sgn)):
            if sgn[k] == sgn[k - 1]:
                run += 1
            else:
                runs_end.append(run)
                run = 1
        runs_end.append(run)
        runs_arr = np.array(runs_end)
        # P(same as previous | run length L so far) per L
        cond = []
        for L in (1, 2, 3, 4):
            ends_here = runs_arr[runs_arr > L].size  # runs that reached L
            cont = runs_arr[runs_arr > L].sum() - ends_here  # crude: runs longer than L
            cond.append(f"L>={L}: {runs_arr[runs_arr > L].size/max(1,len(runs_arr)):.2f}")
        print(f"  {s:9s} P(next same dir)={same:.3f}  runs: mean={runs_arr.mean():.2f} "
              f"max={runs_arr.max()}  share runs>1={np.mean(runs_arr>1):.2f}")


if __name__ == "__main__":
    main()
