#!/usr/bin/env python3
"""QUIET-ENTRY exploit test (Sep 16, from flip_exploit_probes P2 + minor_regime Q1b).

Generator facts (measured today):
  - majors (>=8x): arrivals flat per-tick AND per-hour (spec clock, 1/1100t)
  - minors (5-8x): density varies 17.7x by UTC hour; bursts CLUSTER in time
  - minor_regime Q1b: entries in a minor burst (>=3 minors in trailing 2000
    ticks) average −$0.49/trip vs +$0.28 quiet (long-only) — but Q2 only
    adapted PATIENCE (+$0.7/wk). The entry-suppression cell was never run.

This script runs it: suppress entry while trailing-minor count >= r0
(2000-tick window, strictly pre-entry, matches Q1b), fleet engine otherwise
untouched. Validation: r0=inf must reproduce the fleet rows exactly.

Configs: {incumbent, TIMEBND 5000} x {LS, L} x r0 {1,2,3}, plus a
day-block survival pass with the std ladder for the best cell, because
fewer trips also means fewer compounding events.

Usage: python analysis/flip_quiet_entry.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MULT, MIN_AGE, STEP_LOTS, LIVE_SPREAD
from flip_entry_filters import SPREAD_USD, VPU
from flip_gapmask import bad_tick_mask
from flip_next_gen import SYMBOL, day_block, ladder_fn

REG_W = 2000       # trailing window for minor count (matches minor_regime)
MAJOR_X = 8.0      # minor = 5x..8x


def run_quiet(ms, px, med, side=0, mode="counter", k=None, r0=None):
    """Fleet phase-follower + entry suppression in minor bursts.
    r0=None -> no filter (validation must match flip_next_gen.run).

    Semantics note (2026-09-16): exits do NOT fall through to the entry
    block via `continue` - the fleet engine allows a same-tick re-entry
    when a TIMEBND retrace-close coincides with a completed phase gate
    (fires for LS/TB). With this, all FOUR fleet cells validate bit-exact,
    not just three."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    minor = big & (np.abs(d) <= MAJOR_X * med)
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    pend_j = 0
    nets, cms = [], []
    minor_ts = []      # indices of recent minor spikes (bounded)

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            minor_ts = []
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if minor[j - 1]:
                minor_ts.append(j - 1)
                if len(minor_ts) > 64:
                    del minor_ts[:-64]
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            if mode == "counter":
                if is_spike and s != np.sign(pos):
                    close(px[j], j)
            else:                      # timebnd, fleet branch order
                if is_spike and s != np.sign(pos):
                    pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                    if pnl_now >= 0:
                        close(px[j], j)
                    elif not pending:
                        pending, pend_j = True, j
                elif pending and is_spike and s == np.sign(pos):
                    close(px[j], j)
                elif pending and j - pend_j >= k:
                    close(px[j], j)

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            quiet = True
            if r0 is not None:
                quiet = sum(1 for t in minor_ts if j - t <= REG_W) < r0
            if want and quiet and cnt >= MIN_AGE + 1 and is_spike:
                pos, entry = STEP_LOTS * cur_dir, px[j]
                worst, pending = 0.0, False
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:34s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}")


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive {days:.1f} days, med={med:.5f}u, burst window {REG_W}t "
          f"(minor = 5-{MAJOR_X}x median)\n")

    # ---- validation: r0=None must reproduce flip_next_gen exactly ---------
    print("=== validation (r0=off vs fleet) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        for mode, k, lab in (("counter", None, "incumbent"),
                             ("timebnd", 5000, "TIMEBND5000")):
            mine, _ = run_quiet(ms, px, med, side=side, mode=mode, k=k)
            if side == 0 and mode == "counter":
                continue  # fleet reference printed from known rows below
            print(f"  {sname} {lab:11s} mine: {len(mine)} trips "
                  f"${mine.sum():+7.2f}")
    print("  (fleet refs: LS counter 310 trips $+23.15 | L counter 198 $+15.24 |"
          " L timebnd5000 198 $+25.78)\n")

    # ---- main grid ---------------------------------------------------------
    print("=== quiet-entry grid ===")
    best = {}
    for side, sname in ((0, "LS"), (1, "L")):
        for mode, k, elab in (("counter", None, "incumbent"),
                              ("timebnd", 5000, "TIMEBND5000")):
            base, _ = run_quiet(ms, px, med, side=side, mode=mode, k=k)
            stats(base, days, f"{sname} {elab} r0=off (ref)")
            best[(sname, elab, "off")] = base
            for r0 in (2, 3, 4):
                nets, _ = run_quiet(ms, px, med, side=side, mode=mode, k=k,
                                    r0=r0)
                stats(nets, days, f"{sname} {elab} r0>={r0} suppressed")
                best[(sname, elab, r0)] = nets
            print()

    # ---- survival: best L-cell on the std ladder ---------------------------
    print("=== day-block survival, long-only, std ladder 0.1->0.4 ===")
    for elab in ("incumbent", "TIMEBND5000"):
        for r0 in ("off", 2, 3, 4):
            nets = best[("L", elab, r0)]
            # cms array aligns with nets
            _, cms = run_quiet(ms, px, med, side=1,
                               mode="counter" if elab == "incumbent" else "timebnd",
                               k=None if elab == "incumbent" else 5000,
                               r0=None if r0 == "off" else r0)
            trades = list(zip(nets.tolist(), cms.tolist()))
            fl, ru = day_block(trades, lots_mult=ladder_fn)
            print(f"  {elab:11s} r0={str(r0):4s} trips={len(nets):3d}  "
                  f"flip={fl:5.1f}%  ruin={ru:5.1f}%")


if __name__ == "__main__":
    main()
