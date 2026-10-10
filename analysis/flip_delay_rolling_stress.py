#!/usr/bin/env python3
"""Menu-C stress: does the entry-delay edge survive under ROLLING?

Appendix AE re-based the Oct 8 menu on the live (rolling-median)
convention; candidate C = rolling-fence5 counter + DELAY600 came out
at +$29.35/wk LS vs +$28.54 plain (+$0.81/wk from the delay). The
frozen-engine reading (Appendix AD) showed a NON-MONOTONIC K-response
(d180 dip, d600 peak), so the specific K may be window luck. This
script stresses the edge:

  S1. K-SWEEP under rolling: d in {60,120,180,300,600,1200,2400} vs d0
      for LS and L. A real mechanism shows a coherent shape (sign
      stability + broad plateau); window luck shows isolated spikes.
  S2. SIDE SPLIT: where does the delay delta live (L vs S)? P13's
      drift-decay says delay should help shorts, hurt longs.
  S3. DAY-CONCENTRATION REFEREE: per-day table of the d600-vs-d0
      delta (LS). ACTREG ghost rule: if 1-2 days own the delta, dead.
  S4. ARCHIVE-HALF REPLICATION: delta on half1 (Sep 3-10, normal vol)
      vs half2 (incl. the Sep 12/14/15 vol-regime days). Mechanism
      predicts the edge should exist in both; the frozen-engine story
      tied delay's value to flood-avoidance, which is now known to be
      a rolling-convention non-issue.
  S5. SURVIVAL BRACKET: 7 seeds x {d0, d600} x (LS, L), fixed 0.1.

Usage: python analysis/flip_delay_rolling_stress.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_rolling_engine import rolling_medians, run_conv
from flip_next_gen import day_block

SYMBOL = "flip1000"


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    frozen_med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000

    print("computing rolling-median series...")
    med_roll = rolling_medians(ms, px)
    print("done\n")

    def wk(nets):
        return nets.sum() / days * 7

    print("=== S1. K-sweep under rolling (net/wk, LS + L, counter) ===")
    delays = (0, 60, 120, 180, 300, 600, 1200, 2400)
    res = {}
    for side, sname in ((0, "LS"), (1, "L")):
        base = None
        row = []
        for dl in delays:
            nets, cms = run_conv(ms, px, med_roll, frozen_med, side=side,
                                 mode="counter", mult=5.0, delay=dl)
            v = wk(nets)
            res[(sname, dl)] = (v, len(nets), cms, nets)
            if dl == 0:
                base = v
            row.append(f"d{dl}={v:+7.2f}")
        print(f"  {sname}: base(d0)=${base:+7.2f}/wk  |  " + "  ".join(row))
    print()

    print("=== S2. side split of the delay delta (net/wk) ===")
    for sname in ("LS", "L"):
        b = res[(sname, 0)][0]
        for dl in (180, 600):
            print(f"  {sname} d{dl}-d0 = {res[(sname, dl)][0] - b:+6.2f}")
    print()

    print("=== S3. day-concentration of the d600-vs-d0 delta (LS) ===")
    _, _, cms0, nets0 = res[("LS", 0)]
    _, _, cms6, nets6 = res[("LS", 600)]
    d0_map = {}
    for n_, t_ in zip(nets0, cms0):
        dd = datetime.fromtimestamp(t_ / 1000, tz=timezone.utc).date()
        d0_map[dd] = d0_map.get(dd, 0.0) + n_
    d6_map = {}
    for n_, t_ in zip(nets6, cms6):
        dd = datetime.fromtimestamp(t_ / 1000, tz=timezone.utc).date()
        d6_map[dd] = d6_map.get(dd, 0.0) + n_
    alldays = sorted(set(d0_map) | set(d6_map))
    deltas = []
    print(f"{'day':10s} {'d0$':>8s} {'d600$':>8s} {'delta$':>8s}")
    for dd in alldays:
        a = d0_map.get(dd, 0.0)
        b = d6_map.get(dd, 0.0)
        deltas.append((dd, b - a))
        print(f"{dd}  {a:+8.2f} {b:+8.2f} {b - a:+8.2f}")
    tot = sum(x[1] for x in deltas)
    ranked = sorted(deltas, key=lambda x: x[1], reverse=True)
    print(f"total delta = ${tot:+.2f}; top-2 days own "
          f"{(ranked[0][1] + ranked[1][1]) / tot * 100 if tot else 0:.0f}% "
          f"of it")
    print()

    print("=== S4. archive-half replication (LS, net/wk) ===")
    mid = (ms[WARMUP] + ms[-1]) // 2
    for sname in ("LS", "L"):
        for dl in (0, 600):
            nets, cms = res[(sname, dl)][2], None
            nets_, cms_ = run_conv(ms, px, med_roll, frozen_med,
                                   side=0 if sname == "LS" else 1,
                                   mode="counter", mult=5.0, delay=dl)
            h1 = sum(n for n, t in zip(nets_, cms_) if t < mid) / (days / 2) * 7
            h2 = sum(n for n, t in zip(nets_, cms_) if t >= mid) / (days / 2) * 7
            tag = "d0 " if dl == 0 else "d600"
            print(f"  {sname} {tag}: half1 ${h1:+7.2f}/wk   half2 ${h2:+7.2f}/wk"
                  f"   delta-in-half = {h2 - h1 if dl == 600 else 0:+.2f} (vs own d0)")
    print()

    print("=== S5. survival bracket, fixed 0.1 (7 seeds) ===")
    for sname, side in (("LS", 0), ("L", 1)):
        for dl in (0, 600):
            nets, cms = run_conv(ms, px, med_roll, frozen_med, side=side,
                                 mode="counter", mult=5.0, delay=dl)
            trades = list(zip(nets.tolist(), cms.tolist()))
            rows = []
            for seed in range(11, 18):
                fl, ru = day_block(trades, seed=seed)
                rows.append(f"{fl:5.1f}/{ru:4.1f}")
            print(f"  {sname} d{dl:<4d} net=${wk(nets):+7.2f}/wk  "
                  f"flip/ruin: " + "  ".join(rows))
    print()

    med_sweep = [res[(s, dl)][0] for s in ("LS", "L") for dl in delays[1:]]
    print("S1 shape summary: LS deltas =",
          [f"{res[('LS', dl)][0] - res[('LS', 0)][0]:+.2f}" for dl in delays[1:]])
    print("                  L  deltas =",
          [f"{res[('L', dl)][0] - res[('L', 0)][0]:+.2f}" for dl in delays[1:]])


if __name__ == "__main__":
    main()
