#!/usr/bin/env python3
"""P20 (Sep 17): OUT-OF-SAMPLE test of menu C on post-freeze holdout.

The Oct 8 menu (Appendices AE/AF) was frozen when the last menu-selecting
study was written: flip_delay_rolling_stress.py, mtime
2026-09-16 17:14:30 +0100 = 2026-09-16 16:14:30 UTC (FREEZE_MS below).
Everything the archive collected AFTER that line is data the menu never
saw: Sep 16 16:14Z -> Sep 17 ~09:45Z, ~17.5h (~110k ticks) - and it is a
HOSTILE holdout: the tail of the Sep 14-17 elevated-vol regime plus Sep 17,
the biggest minor-burst day on record (50.8% of the day's spikes are
minors under the rolling convention).

Soft-peek disclosure (honesty): the P19 entry-magnitude diagnostic ran on
Sep 17 and saw these ticks - but it never touched delay K, the menu, or
any selection; P19's verdict was "no filter". Selection remains clean.

  S0. Context: archive span, freeze line, holdout size.
  S1. Bit-exact validation of the stamped C-clone vs run_conv
      (abort everything if it fails - no unvalidated numbers).
  S2. HOLDOUT P&L: C (fence5 counter + d300) vs d0 incumbent vs
      B (fence8 TB5000), rolling convention, LS. Per-side split for C/d0.
  S3. In-sample vs out-of-sample per cell + burst attribution inside the
      holdout: trips whose ENTRY spike was minor-class (x < 8) vs
      true-jump (x >= 8) - tests the AE/AF claim that minors don't
      poison C, on the worst burst day on record.
  S4. STITCHED survival: day-block bootstrap sampling 21 IS days + 7 OOS
      days per sim (seeded), fixed 0.1 and std ladder, 7 seeds.
      Pure-IS rows reproduce AF's bracket for continuity.
  S5. Verdict footer: what 17.5h of holdout can and cannot prove.

Usage: python analysis/flip_oos_c.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
import flip_rolling_engine as eng                                   # noqa: E402
from flip_conditionality import load, WARMUP                        # noqa: E402
from flip_gapmask import bad_tick_mask                              # noqa: E402
from flip_rolling_engine import (STEP_LOTS, VPU, SPREAD_USD,        # noqa: E402
                                 run_conv, rolling_medians, day_of)
from flip_next_gen import day_block, ladder_fn                      # noqa: E402

SYMBOL = "flip1000"
FREEZE_MS = 1789575270000        # 2026-09-16 16:14:30.000 UTC
SIMS = 2000


def run_c(ms, px, med_t, side=0, mult=5.0, delay=0):
    """run_conv clone (counter mode, no r0) that stamps every trip with
    (signed side, entry-spike x, entry ms). Must validate bit-exact."""
    d = np.diff(px)
    bad = eng.bad_tick_mask(ms)
    thr_t = mult * med_t if np.ndim(med_t) else mult * med_t * np.ones(len(d))

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    ep_dir, ep_j = 0, -1
    ep_x = 0.0
    nets, cms, stamps = [], [], []
    cur = [None]

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        stamps.append(cur[0])
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            ep_dir, ep_j = 0, -1
            continue
        is_spike = bool(d[j - 1] > thr_t[j - 1] or -d[j - 1] > thr_t[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if ep_dir != 0 and s != 0 and s != ep_dir:
                ep_dir, ep_j = 0, -1        # cancel pending on opposite jump

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            if is_spike and s != np.sign(pos):
                close(px[j], j)             # counter exit
                continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    cur[0] = (ep_dir, ep_x, ms[j])
                    worst, pending = 0.0, False
                    ep_dir, ep_j = 0, -1
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= eng.MIN_AGE + 1 and is_spike:
                    x = abs(d[j - 1]) / med_t[j - 1]
                    if delay == 0:
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        cur[0] = (cur_dir, x, ms[j])
                        worst, pending = 0.0, False
                    else:
                        ep_dir, ep_j, ep_x = cur_dir, j, x
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms), stamps


def cell_stats(nets, cms, days_span, label):
    m = cms >= FREEZE_MS
    o = nets[m]
    i = nets[~m]
    line = (f"  {label:26s} IS n={len(i):3d} ${i.sum()/max(days_span-0.73,0.1)*7:+7.2f}/wk"
            f"   ||  OOS n={len(o):3d} ${o.sum():+6.2f} (${o.sum()/0.73*7:+8.2f}/wk)")
    if len(o):
        eq = np.cumsum(o)
        dd = (np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()
        wr = (o > 0).mean() * 100
        line += f"  WR={wr:4.0f}%  maxDD=${dd:5.2f}"
    return line, o, m


def stitched(is_tr, oos_tr, sizing, oos_slots=7, seeds=range(11, 18)):
    """Day-block: IS days + oos_slots OOS days per 28-day sim (seeded).
    NOTE: only 2 partial OOS days exist; oos_slots is a REGIME-FREQUENCY
    assumption (how often future days look like the OOS cluster), not a
    claim about the holdout itself. Swept in the sensitivity table."""
    def daymap(tr):
        m = {}
        for p, t in tr:
            m[day_of(t)] = m.get(day_of(t), 0.0) + p
        return np.array([v for _, v in sorted(m.items())])

    is_d, oos_d = daymap(is_tr), daymap(oos_tr)
    rows = []
    for seed in seeds:
        rng = np.random.default_rng(seed)
        fl = ru = 0
        for _ in range(SIMS):
            eq, m = 10.0, 1.0
            slots = (set(rng.permutation(28)[:oos_slots].tolist())
                     if len(oos_d) else set())
            dead = False
            for k in range(28):
                dp = (oos_d[rng.integers(len(oos_d))] if k in slots
                      else is_d[rng.integers(len(is_d))])
                eq += dp * m
                if eq <= 0:
                    ru += 1
                    dead = True
                    break
                if eq >= 100.0:
                    fl += 1
                    dead = True
                    break
                m = ladder_fn(eq) if sizing else 1.0
        rows.append((fl / SIMS * 100, ru / SIMS * 100))
    f = [r[0] for r in rows]
    r = [r[1] for r in rows]
    return f"flip {min(f):.0f}-{max(f):.0f}% / ruin {min(r):.1f}-{max(r):.1f}%"


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    t_freeze = datetime.fromtimestamp(FREEZE_MS / 1000, tz=timezone.utc)
    t_end = datetime.fromtimestamp(ms[-1] / 1000, tz=timezone.utc)
    oos_days = (ms[-1] - FREEZE_MS) / 86_400_000
    n_oos = int((ms[1:len(px)] >= FREEZE_MS).sum())

    print(f"=== S0. context ===")
    print(f"  archive: {ms[WARMUP]/1e3:.0f} -> {ms[-1]/1e3:.0f} UTC "
          f"({days:.2f}d post-warmup, {len(px):,} ticks)")
    print(f"  freeze line (menu AE/AF last selection): {t_freeze:%Y-%m-%d %H:%M:%S}Z")
    print(f"  holdout: {t_freeze:%m-%d %H:%M}Z -> {t_end:%m-%d %H:%M}Z = "
          f"{oos_days:.2f}d, {n_oos:,} ticks ({n_oos / (oos_days * 86_400):,.0f} "
          f"ticks/day vs {86_400:,} spec-clock = "
          f"{n_oos / (oos_days * 86_400):.2f}x activity)")
    print(f"  soft-peek disclosure: P19 (Sep 17) saw these ticks; it never "
          f"touched delay K or the menu (verdict: no filter). Selection clean.\n")

    print("computing rolling-median series on the extended archive...")
    import os
    cache = os.path.join("data", "flip_exec", "_rolling_med_flip1000.npz")
    med = None
    if os.path.exists(cache):
        z = np.load(cache)
        if int(z["n"]) == len(px):
            med = z["med"]
            print(f"  (cache hit, n={len(px):,})")
    if med is None:
        med = rolling_medians(ms, px)
        np.savez(cache, n=len(px), med=med)
        print(f"  computed & cached (n={len(px):,})")
    print()

    # ---- S1. clone validation (bit-exact, both delays) ----
    print("=== S1. C-clone validation vs run_conv (full archive) ===")
    ok_all = True
    for dl in (0, 300):
        rn, rc = run_conv(ms, px, med, None, side=0, mode="counter",
                          mult=5.0, delay=dl)
        cn, cc, _ = run_c(ms, px, med, side=0, mult=5.0, delay=dl)
        ok = (len(rn) == len(cn) and np.allclose(rn, cn, atol=1e-12)
              and np.array_equal(rc, cc))
        ok_all &= ok
        print(f"  d{dl:<3d}: {'BIT-EXACT' if ok else 'MISMATCH'} "
              f"(n={len(cn)}, net ${cn.sum():+.2f})")
    if not ok_all:
        print("ABORT: clone failed validation - no numbers below are trustworthy")
        return
    print()

    # ---- S2. holdout P&L ----
    print("=== S2. HOLDOUT P&L (rolling convention, LS) ===")
    runs = {}
    n0, c0 = run_conv(ms, px, med, None, side=0, mode="counter", mult=5.0, delay=0)
    runs["A d0 incumbent      "] = (n0, c0, None)
    n3, c3, st3 = run_c(ms, px, med, side=0, mult=5.0, delay=300)
    runs["C fence5+d300      "] = (n3, c3, st3)
    nb, cb = run_conv(ms, px, med, None, side=0, mode="timebnd",
                      mult=8.0, k=5000)
    runs["B fence8+TB5000    "] = (nb, cb, None)
    for label, (n_, c_, _s) in runs.items():
        line, o, _ = cell_stats(n_, c_, days, label)
        print(line)
    print()

    print("--- C and d0 per side, holdout only ---")
    for label, nets, cms, stamps in (("C d300", n3, c3, st3), ("d0", n0, c0, None)):
        if stamps is None:
            # side stamps for d0: quick re-run with stamps via run_c (validated)
            _, _, stamps = run_c(ms, px, med, side=0, mult=5.0, delay=0)
        m = cms >= FREEZE_MS
        for sname, sgn in (("long ", 1), ("short", -1)):
            sel = m & np.array([s[0] == sgn for s in stamps])
            o = nets[sel]
            if len(o):
                print(f"  {label} {sname}: n={len(o):3d}  net=${o.sum():+6.2f}  "
                      f"WR={(o > 0).mean():4.0%}  avg=${o.mean():+5.2f}  "
                      f"worst=${o.min():+5.2f}")
            else:
                print(f"  {label} {sname}: n=  0")
    print()

    print("--- OOS trip concentration (does one trade own the loss?) ---")
    for label, nets, cms in (("C d300", n3, c3), ("d0", n0, c0)):
        m = cms >= FREEZE_MS
        o = sorted(zip(nets[m], cms[m]), key=lambda x: x[0])
        s = sum(p for p, _ in o)
        top2 = o[0][0] + (o[1][0] if len(o) > 1 else 0.0)
        print(f"  {label}: worst ${o[0][0]:+.2f}, top-2 ${top2:+.2f} "
              f"({top2/s*100 if s else 0:.0f}% of ${s:+.2f})  "
              f"trip PnLs: {' '.join(f'{p:+.2f}' for p, _ in o)}")
    print()

    # ---- S3. burst attribution inside the holdout ----
    print("=== S3. burst attribution: entry-spike class (x = jump/median) ===")
    print("  x<8 = minor-class entry (burst/absorption), x>=8 = true-jump entry")
    m3 = c3 >= FREEZE_MS
    x3 = np.array([s[1] for s in st3])
    for lab, sel in (("minor-class x<8", (x3 < 8) & m3),
                     ("true-jump  x>=8", (x3 >= 8) & m3)):
        o = n3[sel]
        print(f"  C {lab}: n={len(o):3d}  net=${o.sum():+6.2f}  "
              f"WR={(o > 0).mean() if len(o) else 0:4.0%}  avg=${o.mean() if len(o) else 0:+5.2f}")
    m0 = c0 >= FREEZE_MS
    _, _, st0 = run_c(ms, px, med, side=0, mult=5.0, delay=0)
    x0 = np.array([s[1] for s in st0])
    for lab, sel in (("minor-class x<8", (x0 < 8) & m0),
                     ("true-jump  x>=8", (x0 >= 8) & m0)):
        o = n0[sel]
        print(f"  d0 {lab}: n={len(o):3d}  net=${o.sum():+6.2f}  "
              f"WR={(o > 0).mean() if len(o) else 0:4.0%}  avg=${o.mean() if len(o) else 0:+5.2f}")
    print()

    # ---- S4. stitched survival ----
    print("=== S4. stitched survival: IS days + OOS cluster days per 28d sim ===")
    print("  (7 seeds x 2000 sims; fixed 0.1 and std ladder 0.1->0.4 @ 15/30/50)")
    tr0 = list(zip(n0.tolist(), c0.tolist()))
    tr3 = list(zip(n3.tolist(), c3.tolist()))
    is3 = [(p, t) for p, t in tr3 if t < FREEZE_MS]
    oos3 = [(p, t) for p, t in tr3 if t >= FREEZE_MS]
    is0 = [(p, t) for p, t in tr0 if t < FREEZE_MS]
    oos0 = [(p, t) for p, t in tr0 if t >= FREEZE_MS]
    print("  headline at 25% cluster frequency (7/28 slots):")
    for label, istr, ostr in (("C d300", is3, oos3), ("d0    ", is0, oos0)):
        for sizing, tag in ((False, "fixed 0.1"), (True, "ladder    ")):
            res = stitched(istr, ostr, sizing)
            print(f"  {label} {tag}: {res}")
    print("\n  CLUSTER-FREQUENCY SENSITIVITY (the honest read: survival vs")
    print("  how often future days look like the OOS regime cluster):")
    print(f"  {'f(cluster)':>10s} {'C fixed':>28s} {'C ladder':>28s} {'d0 fixed':>28s}")
    for slots, fl in ((0, "0% (pure IS)"), (2, "7%"), (4, "14%"), (7, "25%"),
                      (14, "50%")):
        cF = stitched(is3, oos3, False, oos_slots=slots, seeds=(12, 15))
        cL = stitched(is3, oos3, True, oos_slots=slots, seeds=(12, 15))
        dF = stitched(is0, oos0, False, oos_slots=slots, seeds=(12, 15))
        print(f"  {fl:>10s} {cF:>28s} {cL:>28s} {dF:>28s}")
    print("\n  (reference: pure-IS C fixed for continuity with AF)")
    print(f"  C d300 pure-IS : {stitched(is3, [], False)}")
    print()

    print("=== S5. what this can and cannot prove ===")
    print("  CAN: config C's OOS direction and burst-robustness on the worst")
    print("       burst day on record; d0-vs-C OOS ordering at trip level.")
    print("  CANNOT: survival odds at meaningful precision (7 OOS days),")
    print("       regime generality (single elevated-regime holdout), or")
    print("       anything about B beyond its OOS cell.")
    print("  Rule: no re-pointing of K or the menu on this sample alone.")


if __name__ == "__main__":
    main()
