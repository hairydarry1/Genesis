#!/usr/bin/env python3
"""Fence generality across the Flip family (Sep 16, P16).

Appendix AA found flip1000's tick distribution has TWO populations: an
ordinary band ending near 8x median and a true-jump class from ~20x (rate
= the spec clock). Moving the phase-vote fence there doubled the edge.

This script asks whether that structure is FAMILY-WIDE. For each symbol
(with its frozen baseline detector multiplier and live spread):

  1. QUANTUM CENSUS: spike counts at the baseline fence and at higher
     fences (1.6x / 2.4x / 4.0x baseline) - does a sparse true-jump class
     exist here too (gap in the counts + rate vs that symbol's spec
     clock N: 500 crash/boom per day on flip500, 300 on flip300,
     150 on flip150)?
  2. FENCE LADDER: long-only incumbent engine at baseline vs raised
     fences, net $/wk at that symbol's VPU and live spread.
  3. PERSISTENCE: P(next major same direction) at each fence (the P4
     edge; does the raised fence sharpen it?).

Verdict rule: generality = raised fence improves $/wk on symbols whose
census shows the two-population gap; dead end if only flip1000 shows it.

Usage: python analysis/flip_fence_generality.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
import flip_exit_families as fef
from flip_conditionality import load, WARMUP
from flip_exit_families import run as fleet_run
from flip_gapmask import bad_tick_mask
from flip_phase_backtest import get_specs

SYMS = [("flip150", 2.5, 0.17), ("flip300", 3.0, 2.88),
        ("flip500", 3.5, 1.02), ("flip1000", 5.0, 0.6261)]
SPEC_PER_DAY = {"flip150": 150 * 2, "flip300": 300 * 2,
                "flip500": 500 * 2, "flip1000": 1000 * 2}


def vpu_01(sym):
    sp = get_specs()[sym]
    return sp["tick_value"] / sp["tick_size"] * 0.1


def census(ms, px, med, base_mult, sym):
    d = np.diff(px)
    ok = ~bad_tick_mask(ms)[1:len(px)]
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"  census (days={days:.1f}, spec clock ~{SPEC_PER_DAY[sym]} "
          f"booms+crashes/day):")
    for f in (1.0, 1.6, 2.4, 4.0):
        x = base_mult * f
        n = int(((np.abs(d) > x * med) & ok).sum())
        print(f"    {x:4.1f}x med ({f:.1f}x base): n={n:6d}  "
              f"{n / days:8.1f}/day")


def ladder(ms, px, med, base_mult, spread, sym):
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    vpu = vpu_01(sym)
    ok = ~bad_tick_mask(ms)[1:len(px)]
    import flip_exit_families as fef_mod
    old_spread = fef_mod.LIVE_SPREAD
    fef_mod.LIVE_SPREAD = spread
    try:
        for fam, param, lab in (("counter", None, "incumbent"),
                                ("timebnd", 5000, "TB5000")):
            row = [f"  {lab:10s}"]
            for f in (1.0, 1.6, 2.4):
                fef_mod.MULT = base_mult * f
                nets, _ = fleet_run(ms, px, med, side=1, family=fam,
                                    param=param)
                row.append(f"{base_mult * f:4.1f}x: "
                           f"${nets.sum() / days * 7:+8.2f}/wk "
                           f"({len(nets):3d}t, "
                           f"WR {(nets > 0).mean() * 100:4.1f}%)")
            print("  |  ".join(row))
        # persistence at baseline vs raised fence (P4 convention)
        print("  persistence P(next major same dir):")
        for f in (1.0, 1.6):
            fef_mod.MULT = base_mult * f
            thr = fef_mod.MULT * med
            big = (np.abs(np.diff(px)) > thr) & ok
            idx = np.where(big)[0]
            idx = idx[idx >= WARMUP]
            s = np.sign(np.diff(px)[idx])
            same = (s[1:] == s[:-1]).mean() * 100
            print(f"    {fef_mod.MULT:4.1f}x: {same:5.1f}%  (n={len(s) - 1})")
    finally:
        fef_mod.MULT = 5.0
        fef_mod.LIVE_SPREAD = old_spread


def main():
    for sym, mult, spread in SYMS:
        ms, px = load(sym)
        med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
        print("=" * 78)
        print(f"{sym} @ base {mult}x  spread {spread}u  "
              f"VPU(0.1lot) {vpu_01(sym):.4f}  med {med:.5f}u")
        print("=" * 78)
        census(ms, px, med, mult, sym)
        ladder(ms, px, med, mult, spread, sym)
        print()
    fef.MULT = 5.0   # global hygiene


if __name__ == "__main__":
    main()
