#!/usr/bin/env python3
"""Minor-spike cluster regime: does it predict winners, and can TIMEBND adapt?

Appendix T found flip1000 has TWO spike populations:
  major: >=8x median, ~1/1000 ticks, memoryless (the spec'd spike)
  minor: 5-8x median, clustered (gap-variance 2.22x geometric)

Q1  Does the minor class predict entry outcomes?
    (a) entry spike class: minor (5-8x) vs major (>=8x)
    (b) trailing regime: count of minor spikes in the 2000 ticks before
        the entry spike (strictly pre-entry), R=0 / 1 / 2+
    Bucketed on both the incumbent (counter) and TIMEBND-2000 books.

Q2  Can patience adapt to the regime? Hypothesis: in a minor burst the
    next spike (favorable exit) arrives sooner, so patience can be shorter;
    in the quiet/major regime hold longer. Per-entry K = K_low if R < r0
    else K_high. Prior-motivated cells: (2000,5000) — both already
    pre-registered as gate candidates. Grid labeled exploratory.

Validation: k_low=k_high=K must reproduce fleet TIMEBND-K rows (trips +
total within 0.5%); counter mode must reproduce the incumbent row.

Usage: python analysis/flip_minor_regime.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MULT, MIN_AGE, STEP_LOTS, LIVE_SPREAD
from flip_entry_filters import SPREAD_USD, VPU
from flip_gapmask import bad_tick_mask
from flip_exit_families import run as fleet_run

SYMBOL = "flip1000"
MAJOR_X = 8.0        # >=8x median = major spike (Appendix T sweep)
REG_W = 2000         # trailing regime window, ticks


def run_regime(ms, px, med, side=1, mode="counter", k_low=None, k_high=None,
               r0=None):
    """Phase-follower, incumbent or TIMEBND with optional per-entry adaptive K.

    Returns (nets, eidx, entry_class, R) where entry_class is 0 minor /1
    major and R = trailing minor count in the REG_W ticks before entry.
    """
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = entry = 0.0
    cur_dir, cnt = 0, 0
    pending = False
    pend_j = 0
    k_cur = None
    minor_js = []          # tick indices of minor spikes
    nets, eidx, ecls, ers = [], [], [], []

    def close(fill, j):
        nonlocal pos, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        eidx.append(j)
        pos = 0.0
        pending = False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, pending = 0.0, 0, 0, False
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        x_now = abs(d[j - 1]) / med if is_spike else 0.0

        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if x_now < MAJOR_X:
                minor_js.append(j - 1)

        # ---------------- exits ----------------
        if pos != 0:
            if mode == "counter":
                if is_spike and s != np.sign(pos):
                    close(px[j], j)
                    continue
            else:  # timebnd
                if is_spike and s != np.sign(pos):
                    pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                    if pnl_now >= 0:
                        close(px[j], j)
                        continue
                    if not pending:
                        pending, pend_j = True, j
                elif pending and is_spike and s == np.sign(pos):
                    close(px[j], j)
                    continue
                elif pending and j - pend_j >= k_cur:
                    close(px[j], j)
                    continue

        # ---------------- entries ----------------
        if pos == 0 and is_spike and cnt >= MIN_AGE + 1 and \
                (side == 0 or cur_dir == side):
            R = sum(1 for jj in minor_js
                    if j - 1 - REG_W <= jj < j - 1)
            pos, entry = STEP_LOTS * cur_dir, px[j]
            ecls.append(0 if x_now < MAJOR_X else 1)
            ers.append(R)
            if mode == "timebnd":
                k_cur = k_low if (r0 is None or R < r0) else k_high

    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(eidx), np.array(ecls), np.array(ers)


def stats(nets, days, label):
    wins, losses = nets[nets > 0], nets[nets <= 0]
    eq = np.cumsum(nets)
    dd = (np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max() \
        if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:36s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive {days:.1f}d  med={med:.5f}  major_x>={MAJOR_X}  regW={REG_W}t\n")

    # ---- validation vs fleet ----
    print("=== VALIDATION vs fleet engine ===")
    fnets, _ = fleet_run(ms, px, med, side=1, family="counter")
    nets, *_ = run_regime(ms, px, med, side=1, mode="counter")
    ok = len(fnets) == len(nets) and abs(fnets.sum() - nets.sum()) < 0.005 * abs(fnets.sum())
    print(f"  counter: fleet {len(fnets)} trips ${fnets.sum():+.2f} | "
          f"mine {len(nets)} ${nets.sum():+.2f}  {'OK' if ok else 'MISMATCH'}")
    for K in (2000, 5000):
        fnets, _ = fleet_run(ms, px, med, side=1, family="timebnd", param=K)
        nets, *_ = run_regime(ms, px, med, side=1, mode="timebnd",
                              k_low=K, k_high=K, r0=0)
        ok = len(fnets) == len(nets) and abs(fnets.sum() - nets.sum()) < 0.005 * abs(fnets.sum())
        print(f"  timebnd{K}: fleet {len(fnets)} trips ${fnets.sum():+.2f} | "
              f"mine {len(nets)} ${nets.sum():+.2f}  {'OK' if ok else 'MISMATCH'}")
    print()

    # ---- Q1: regime buckets ----
    print("=== Q1a: ENTRY SPIKE CLASS -> outcome (long-only) ===")
    for mode, label in (("counter", "incumbent"), ("timebnd", "TIMEBND 2000")):
        kw = dict(k_low=2000, k_high=2000, r0=0) if mode == "timebnd" else {}
        nets, _, ecls, _ = run_regime(ms, px, med, side=1, mode=mode, **kw)
        for cls, cl in ((0, "minor 5-8x"), (1, "major >=8x")):
            sel = nets[ecls == cls]
            if len(sel):
                print(f"  {label:12s} {cl:12s} n={len(sel):3d}  "
                      f"WR={(sel>0).mean()*100:4.0f}%  mean=${sel.mean():+.2f}  "
                      f"sum=${sel.sum():+7.2f}")
            else:
                print(f"  {label:12s} {cl:12s} n=  0")
        print()

    print("=== Q1b: TRAILING MINOR COUNT R (2000t pre-entry) -> outcome ===")
    for mode, label in (("counter", "incumbent"), ("timebnd", "TIMEBND 2000")):
        kw = dict(k_low=2000, k_high=2000, r0=0) if mode == "timebnd" else {}
        nets, _, _, ers = run_regime(ms, px, med, side=1, mode=mode, **kw)
        for lo, hi, rl in ((0, 0, "R=0 quiet"), (1, 1, "R=1"), (2, 2, "R=2"),
                           (3, 10**9, "R>=3 burst")):
            sel = nets[(ers >= lo) & (ers <= hi)]
            if len(sel):
                print(f"  {label:12s} {rl:10s} n={len(sel):3d}  "
                      f"WR={(sel>0).mean()*100:4.0f}%  mean=${sel.mean():+.2f}  "
                      f"sum=${sel.sum():+7.2f}")
            else:
                print(f"  {label:12s} {rl:10s} n=  0")
        print()

    # ---- Q2: adaptive patience ----
    print("=== Q2: ADAPTIVE K by regime (K_low if R<r0 else K_high) ===")
    ref_nets, *_ = run_regime(ms, px, med, side=1, mode="counter")
    stats(ref_nets, days, "incumbent (ref)")
    nets, *_ = run_regime(ms, px, med, side=1, mode="timebnd",
                          k_low=2000, k_high=2000, r0=0)
    stats(nets, days, "TIMEBND 2000 flat (ref)")
    nets, *_ = run_regime(ms, px, med, side=1, mode="timebnd",
                          k_low=5000, k_high=5000, r0=0)
    stats(nets, days, "TIMEBND 5000 flat (ref)")
    # prior-motivated pair first
    for k_lo, k_hi, r0 in ((2000, 5000, 1), (2000, 5000, 2),
                           (1000, 5000, 1), (1000, 5000, 2),
                           (2000, 3000, 1), (1000, 3000, 2)):
        nets, *_ = run_regime(ms, px, med, side=1, mode="timebnd",
                              k_low=k_lo, k_high=k_hi, r0=r0)
        stats(nets, days, f"K=({k_lo},{k_hi}) r0={r0}")
    print("  (first pair prior-motivated; rest exploratory grid)")

    # ---- day concentration for the motivated pair ----
    nets, eidx, _, ers = run_regime(ms, px, med, side=1, mode="timebnd",
                                    k_low=2000, k_high=5000, r0=2)
    per_day = defaultdict(float)
    for pnl, j in zip(nets, eidx):
        day = datetime.fromtimestamp(ms[j] / 1000, tz=timezone.utc).strftime("%m-%d")
        per_day[day] += float(pnl)
    flat, *_ = run_regime(ms, px, med, side=1, mode="timebnd",
                          k_low=2000, k_high=2000, r0=0)
    flat_day = defaultdict(float)
    for pnl, j in zip(flat, eidx):
        day = datetime.fromtimestamp(ms[j] / 1000, tz=timezone.utc).strftime("%m-%d")
        flat_day[day] += float(pnl)
    print("\n=== day table: TIMEBND 2000 flat vs adaptive (2000,5000) r0=2 ===")
    for day in sorted(set(per_day) | set(flat_day)):
        b, a = flat_day.get(day, 0.0), per_day.get(day, 0.0)
        print(f"  {day}  flat {b:+7.2f}  adaptive {a:+7.2f}  delta {a-b:+7.2f}")


if __name__ == "__main__":
    main()
