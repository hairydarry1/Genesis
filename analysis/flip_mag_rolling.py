#!/usr/bin/env python3
"""P19 (Sep 17): entry-jump-size filter under the LIVE rolling convention.

User complaint, twice: the executor enters immediately on the tiniest spikes
and those lose. The MAG tercile tombstone (entry magnitude does not predict
outcome) was computed under the FROZEN warmup median — and before the
two-population discovery (AA). Under rolling (what the executor runs), the
5x fence admits a true-jump subclass from ~0.1u: x = 60 means a 0.48u jump,
i.e. 5.6x the biggest ordinary tick. This script re-asks MAG's question on
the live convention:

  1. Clone run_conv verbatim with entry-x recording (validate bit-exact).
  2. Census: x-distribution of qualifying entries under rolling fence5.
  3. Per-band EV: does small-x actually lose? (LS counter, the live book.)
  4. Filter ladder: skip entries with x < T for T in {40, 60, 80, 120}.
  5. Referee: day-concentration of the best filter delta (ACTREG discipline).

Usage: python analysis/flip_mag_rolling.py
"""
import os
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load                      # noqa: E402
import flip_rolling_engine as re                          # noqa: E402
from flip_rolling_engine import (run_conv, rolling_medians,  # noqa: E402
                                 STEP_LOTS, VPU, SPREAD_USD, WARMUP,
                                 MIN_AGE)

SYMBOL = "flip1000"


def run_x(ms, px, med_t, side=0, min_x=None, mult=5.0):
    """run_conv clone (delay=0, counter exit) that records entry_x per trip."""
    d = np.diff(px)
    bad = re.bad_tick_mask(ms)
    thr_t = mult * med_t if np.ndim(med_t) else mult * med_t * np.ones(len(d))

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    pending = False
    nets, cms, exs = [], [], []

    def close(fill, j):
        nonlocal pos, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, pending = 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, pending = 0.0, 0, 0, False
            continue
        is_spike = bool(d[j - 1] > thr_t[j - 1] or -d[j - 1] > thr_t[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

        if pos != 0:
            if is_spike and s != 0 and s != (1 if pos > 0 else -1):
                close(px[j], j)      # counter exit: any adverse spike closes
                continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and is_spike:
                x = abs(d[j - 1]) / med_t[j - 1]
                if min_x is None or x >= min_x:
                    pos = STEP_LOTS * cur_dir
                    entry = px[j]
                    exs.append(x)
                    pending = False
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms), np.array(exs)


def main():
    ms, px = load(SYMBOL)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    week = days / 7.0

    cache = os.path.join("data", "flip_exec", "_rolling_med_flip1000.npz")
    med = None
    if os.path.exists(cache):
        z = np.load(cache)
        if int(z["n"]) == len(px):
            med = z["med"]
            print(f"(rolling medians from cache, n={len(px):,})")
    if med is None:
        print("computing rolling-median series (bisect window)...")
        med = rolling_medians(ms, px)
        np.savez(cache, n=len(px), med=med)
        print(f"  done & cached (n={len(px):,})")

    # ---- 1. validate the clone bit-exact vs run_conv ----
    ref_n, ref_c = run_conv(ms, px, med, None, side=0, mode="counter")
    my_n, my_c, _ = run_x(ms, px, med, side=0)
    ok = (len(ref_n) == len(my_n)
          and np.allclose(ref_n, my_n, atol=1e-12)
          and np.array_equal(ref_c, my_c))
    print(f"clone validation vs run_conv: "
          f"{'BIT-EXACT' if ok else 'MISMATCH - ABORT'} "
          f"(n={len(my_n)}, net ${my_n.sum():+.2f})")
    if not ok:
        return

    # ---- 2. census + 3. per-band EV (LS counter, the live book) ----
    nets, cms, exs = my_n, my_c, _
    print(f"\n=== entry-x census + per-band EV (rolling fence5, LS counter, "
          f"{days:.1f}d) ===")
    print(f"  trips n={len(exs)}, net ${nets.sum():+.2f} (${nets.sum()/week:+.2f}/wk)")
    bands = [(0, 40, "x<40   (<0.32u jump)"),
             (40, 60, "40-60  (0.32-0.48u)"),
             (60, 100, "60-100 (0.48-0.8u)"),
             (100, 300, "100-300 (0.8-2.4u)"),
             (300, 800, "300-800 (2.4-6.4u)"),
             (800, 10**9, "x>=800 (6.4u+, jump core)")]
    for lo, hi, label in bands:
        m = (exs >= lo) & (exs < hi)
        if m.sum():
            print(f"  {label:30s} n={m.sum():3d}  "
                  f"WR={(nets[m] > 0).mean():4.0%}  "
                  f"net=${nets[m].sum():+7.2f}  "
                  f"avg=${nets[m].mean():+5.2f}  (${nets[m].sum()/week:+6.2f}/wk)")
        else:
            print(f"  {label:30s} n=  0")

    # ---- 4. filter ladder ----
    print("\n=== skip entries with x < T (LS counter, rolling) ===")
    print(f"  {'T':>4} {'trips':>6} {'net/wk':>9} {'delta vs none':>14}")
    runs = {}
    for T in (40, 60, 80, 120):
        n2, c2, x2 = run_x(ms, px, med, side=0, min_x=T)
        runs[T] = (n2, c2)
        print(f"  {T:>4} {len(n2):>6} {n2.sum()/week:>+9.2f} "
              f"{(n2.sum()-nets.sum())/week:>+14.2f}")

    # fence8 reference: x >= 8 by construction, but absolute jump size still
    # spans regimes (x=12 at a quiet moment = a ~0.1u jump) — "tiny" is
    # regime-relative under rolling, which is partly what the eye sees live
    _, _, x8 = run_x(ms, px, med, side=0, mult=8.0)
    n8, c8, _ = run_x(ms, px, med, side=0, mult=8.0)
    print(f"\n  (fence8 rolling LS counter: n={len(n8)}, "
          f"min entry_x={x8.min():.0f} — small ABSOLUTE jumps still enter "
          f"when the rolling median is low; x is regime-relative)")

    # ---- 5. referee: day-concentration of the best positive delta ----
    best_T, best_d = None, 0.5
    for T in (40, 60, 80, 120):
        n2, _ = runs[T]
        dl = (n2.sum() - nets.sum()) / week
        if dl > best_d:
            best_T, best_d = T, dl
    if best_T is None:
        print("\nno filter threshold beats no-filter by >$0.50/wk - nothing "
              "to referee; the small-jump complaint does not pay for itself "
              "on the archive")
        return
    n2, c2 = runs[best_T]
    base_days = defaultdict(float)
    filt_days = defaultdict(float)
    for p, t in zip(nets, cms):
        base_days[re.day_of(t)] += p
    for p, t in zip(n2, c2):
        filt_days[re.day_of(t)] += p
    deltas = {d_: filt_days.get(d_, 0.0) - base_days.get(d_, 0.0)
              for d_ in set(base_days) | set(filt_days)}
    tot_abs = sum(abs(v) for v in deltas.values())
    ds = sorted(deltas.items(), key=lambda kv: -abs(kv[1]))
    top2 = sum(abs(v) for _, v in ds[:2])
    print(f"\n=== referee: day-concentration of filter delta (T={best_T}) ===")
    for d_, v in ds[:4]:
        print(f"  {d_}: {v:+.2f}")
    print(f"  top-2 days own {top2/max(tot_abs, 1e-9):.0%} of |delta|; "
          f"total delta ${sum(deltas.values()):+.2f}")
    print("  (top-2 > 100% = day-owned, ACTREG ghost — distrust)")


if __name__ == "__main__":
    main()
