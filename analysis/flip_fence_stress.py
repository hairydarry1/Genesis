#!/usr/bin/env python3
"""P15: event-class fence stress + combination (Sep 16).

P14 (flip_quantum_probe) found: moving the PHASE VOTE fence from 5x to 8x+
median (the true jump class, P10's two-population structure) roughly doubles
the long-only edge and transforms ladder survival. Before it earns a menu
slot, the X2 discipline applies:

  A. seed stress: fence8 x {incumbent, TB5000}, paired 3-seed ladder
  B. combination: fence8 x {r0 off, r0=3 quiet-entry} - overlapping
     mechanisms? (minor floods may stop mattering once minors cannot vote)
  C. LS rows for completeness

Note: fence >= 8x cells are identical to each other (same vote set), so one
representative (8x) suffices.

Usage: python analysis/flip_fence_stress.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
import flip_exit_families as fef
from flip_conditionality import load, WARMUP
from flip_entry_filters import SPREAD_USD, VPU  # noqa: F401 (SPREAD for run_quiet)
from flip_next_gen import SYMBOL, day_block, ladder_fn
from flip_quiet_entry import run_quiet


def fence_nets(ms, px, med, side, fam, param, mx):
    fef.MULT = mx
    nets, cms = fef.run(ms, px, med, side=side, family=fam, param=param)
    fef.MULT = 5.0
    return nets, cms


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive {days:.1f} days, med={med:.5f}u\n")

    print("=== A. fence-8x seed stress (L-only, std ladder, paired seeds) ===")
    for fam, param, lab in (("counter", None, "incumbent"),
                            ("timebnd", 5000, "TB5000")):
        nets, cms = fence_nets(ms, px, med, 1, fam, param, 8.0)
        trades = list(zip(nets.tolist(), cms.tolist()))
        cells = []
        for seed in (11, 23, 47):
            fl, ru = day_block(trades, lots_mult=ladder_fn, seed=seed)
            cells.append(f"s{seed}: {fl:5.1f}/{ru:4.1f}")
        print(f"  fence8 {lab:10s} trips={len(nets):3d}  "
              f"net ${nets.sum():+7.2f}  flip/ruin  " + "  ".join(cells))

    print("\n=== B. fence8 x quiet-entry combination (L-only) ===")
    import flip_quiet_entry as qe
    for mx, mlab in ((5.0, "fence5"), (8.0, "fence8")):
        for r0, rlab in ((None, "r0=off"), (3, "r0=3 ")):
            for mode, k, elab in (("counter", None, "incumbent"),
                                  ("timebnd", 5000, "TB5000")):
                nets, cms = run_quiet(ms, px, med, side=1, mode=mode, k=k,
                                      r0=r0)
                if mx == 8.0:
                    # re-run the suppression engine under the fence8 vote:
                    # flip_quiet_entry computes its own big[] from MULT, so
                    # patch the module constant for this call
                    old = qe.MULT
                    qe.MULT = 8.0
                    nets, cms = run_quiet(ms, px, med, side=1, mode=mode,
                                          k=k, r0=r0)
                    qe.MULT = old
                trades = list(zip(nets.tolist(), cms.tolist()))
                fl, ru = day_block(trades, lots_mult=ladder_fn, seed=11)
                print(f"  {mlab} {rlab} {elab:10s} trips={len(nets):3d}  "
                      f"net ${nets.sum() / days * 7:+7.2f}/wk  "
                      f"WR {(nets > 0).mean() * 100:4.1f}%  "
                      f"ladder {fl:5.1f}/{ru:4.1f}")

    print("\n=== C. LS rows at fence8 (reference) ===")
    for fam, param, lab in (("counter", None, "incumbent"),
                            ("timebnd", 5000, "TB5000")):
        nets, _ = fence_nets(ms, px, med, 0, fam, param, 8.0)
        print(f"  LS fence8 {lab:10s} trips={len(nets):3d}  "
              f"net ${nets.sum() / days * 7:+7.2f}/wk  "
              f"WR {(nets > 0).mean() * 100:4.1f}%")
    print()


if __name__ == "__main__":
    main()
