#!/usr/bin/env python3
"""Pre-gate survival refresh: day-block odds on the CURRENT archive + live edge.

Why: the last survival run (Sep 10) used an 11.4-day archive. Two more days
(Sep 14-15, a short-friendly week per the Appendix P side-split) have since
healed in, and the live slippage review now measures net edge at
$0.1776/trade (L+S, spread-gap included). Before the Oct 8 gate the menu's
odds must be re-measured on the bigger, fresher sample.

Method (identical machinery to flip_next_gen):
  - fleet engine (analysis/flip_next_gen.run): gap-masked, frozen median,
    live spread 0.6261u charged per episode, entry gate min_age+1
  - long-only (side=1) on all four Oct 8 menu configs
  - day-block bootstrap, $10 -> $100, 28-day horizon, 2000 sims
    fixed 0.1 lot  AND  compounding ladder 0.1->0.4 (ladder_fn)
Reconciliation: engine mean $/trade (both-sides) printed against the live
review's $0.1776 so any divergence between modeled and measured edge is
visible, not buried.

Caveats carried forward: menu chosen in-sample (selection bias), 13 daily
blocks make bootstrap CIs wide, horizon assumes 24/7 uptime.

Usage: python analysis/flip_survival_refresh.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_concepts import load, WARMUP, LIVE_SPREAD           # noqa: E402
from flip_conditionality import load as cond_load             # noqa: E402
from flip_next_gen import run, day_block, ladder_fn           # noqa: E402
from flip_entry_filters import VPU                            # noqa: E402

SYMBOL = "flip1000"
START_EQ, TARGET = 10.0, 100.0
LIVE_NET_PER_TRADE = 0.1776   # slippage review Sep 15 (L+S, spread gap incl.)
MODEL_SPREAD_USD = LIVE_SPREAD * VPU   # $0.0626/episode charged by engine


def trades_of(ms, px, med, side, mode, **kw):
    nets, cms = run(ms, px, med, side=side, mode=mode, want_ms=True, **kw)
    return list(zip(nets.tolist(), cms.tolist()))


def row(label, trades, days):
    n = len(trades)
    arr = np.array([p for p, _ in trades])
    fl, ru = day_block(trades, start_eq=START_EQ, target=TARGET)
    fl_l, ru_l = day_block(trades, lots_mult=ladder_fn,
                           start_eq=START_EQ, target=TARGET)
    print(f"  {label:24s} n={n:3d}  ${arr.mean():+.3f}/trade  "
          f"worst ${arr.min():+.2f}  ${arr.sum() / days:+.2f}/wk")
    print(f"    fixed 0.1 lot : flip {fl:5.1f}%   ruin {ru:4.1f}%")
    print(f"    ladder 0.1->0.4: flip {fl_l:5.1f}%   ruin {ru_l:4.1f}%")
    return fl, ru, fl_l, ru_l


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"=== survival refresh | {SYMBOL} long-only | archive {days:.1f} days "
          f"(gap-masked) | med={med:.5f} | spread {LIVE_SPREAD:.4f}u "
          f"=${MODEL_SPREAD_USD:.4f}/episode ===")
    print(f"horizon: 28 days, ${START_EQ:.0f} -> ${TARGET:.0f}, "
          "2000 sims, day-block bootstrap\n")

    print("--- reconciliation: engine per-trade edge vs live review ---")
    base = trades_of(ms, px, med, 0, "counter")
    b_arr = np.array([p for p, _ in base])
    print(f"  engine both-sides incumbent: ${b_arr.mean():+.4f}/trade "
          f"(n={len(b_arr)})  vs live review ${LIVE_NET_PER_TRADE:+.4f} "
          f"-> drift {b_arr.mean() - LIVE_NET_PER_TRADE:+.4f}\n")

    print("--- Oct 8 menu, long-only, fixed + ladder ---")
    results = {}
    for label, mode, kw in (
            ("incumbent", "counter", {}),
            ("incumbent +25u stop", "counter", dict(stop_u=25.0)),
            ("TIMEBND 2000", "timebnd", dict(k=2000)),
            ("TIMEBND 5000", "timebnd", dict(k=5000))):
        results[label] = row(label, trades_of(ms, px, med, 1, mode, **kw), days)

    print("\n--- read-out ---")
    best_fixed = max(results, key=lambda k: results[k][0])
    best_lad = max(results, key=lambda k: results[k][2])
    print(f"  best fixed-lot flip odds : {best_fixed} "
          f"({results[best_fixed][0]:.1f}%)")
    print(f"  best ladder flip odds    : {best_lad} "
          f"({results[best_lad][2]:.1f}%)")
    print("  caveats: menu selected in-sample; 13 daily blocks -> wide "
          "bootstrap variance; 24/7 uptime assumed; engine spread = live mean "
          "spread (per-episode), no per-side adverse slippage modeled "
          "(measured ~$0.000/side, n=84).")


if __name__ == "__main__":
    main()
