#!/usr/bin/env python3
"""Slippage ledger review: realized execution cost vs the backtest edge.

Reads data/flip_exec/slippage_ledger.csv and answers the mission question:
  total extra cost per round trip = (live spread - modeled 0.05u spread) * VPU
                                  + mean adverse slippage per side * 2 * VPU
and where that leaves the $10->$100 flip odds, by piecewise interpolation of
the sensitivity ladder measured 2026-09-10 (flip1000 @5 k=1, fixed sizing,
4-week horizon):
    extra cost $0.000 -> flip 73.8% / ruin 22.1%   (backtest-exact fills)
    extra cost $0.117 -> flip 27.3% / ruin 62.4%
    extra cost $0.235 -> flip  3.4% / ruin 94.4%   (edge fully eaten)
    extra cost $0.352 -> flip  0.1% / ruin 99.9%

PROBE rows are reported separately (calm-market validation fills); SIGNAL rows
(ENTRY/EXIT*) are the real measurement. Slippage verdict needs >= 20 signal
sides to be meaningful; below that it prints the running estimate with a
sample-size warning.

Usage: python analysis/flip_slippage_review.py
"""
import csv
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LEDGER = "data/flip_exec/slippage_ledger.csv"
VPU = 0.10                 # $ per price unit at 0.1 lot
MODEL_SPREAD = 0.05        # what the backtest charged (u per round trip)
GROSS_EDGE = 0.235         # $/trade, backtest-exact fills (challenge-era)
LADDER_X = [0.000, 0.117, 0.235, 0.352]
LADDER_FLIP = [73.8, 27.3, 3.4, 0.1]
LADDER_RUIN = [22.1, 62.4, 94.4, 99.9]
MIN_N = 20                 # signal sides before the verdict is trustworthy


def main():
    if not os.path.exists(LEDGER):
        print("no ledger yet:", LEDGER)
        return
    with open(LEDGER) as f:
        rows = [r for r in csv.DictReader(f)
                if r.get("actual_deal")]  # failed orders: no fill, no slippage
    if not rows:
        print("ledger empty")
        return

    probes, signals = [], []
    for r in rows:
        (probes if r["mode"].startswith("PROBE") else signals).append(r)

    def adverse(row):
        try:
            return float(row["adverse_price_units"])
        except (ValueError, KeyError):
            return None

    def spread_of(row):
        try:
            return float(row["spread_at_order"])
        except (ValueError, KeyError):
            return None

    print(f"=== slippage ledger review — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC ===")
    print(f"rows: {len(rows)} total = {len(probes)} probe + {len(signals)} signal")

    # ---- probes (order-path validation, calm fills) ----------------------
    if probes:
        pa = [a for a in (adverse(r) for r in probes) if a is not None]
        if pa:
            print(f"\nPROBE fills (calm market, n={len(pa)}): mean adverse "
                  f"{np.mean(pa):+.5f}u (${np.mean(pa)*VPU:+.4f}/side), "
                  f"worst {max(pa):+.5f}u")

    # ---- signals (the real measurement) ----------------------------------
    sig_adv = [a for a in (adverse(r) for r in signals) if a is not None]
    sig_spreads = [s for s in (spread_of(r) for r in signals) if s is not None]
    n_sides = len(sig_adv)

    # days covered by signal rows
    days = sorted({r["spike_ts_utc"][:10] for r in signals if r["spike_ts_utc"]}) \
        if signals else []
    n_days = len(days)

    print(f"\nSIGNAL fills (n={n_sides} sides over {n_days} UTC day(s)):")
    if sig_adv:
        a = np.array(sig_adv)
        print(f"  adverse slippage: mean {a.mean():+.5f}u (${a.mean()*VPU:+.4f}/side)  "
              f"median {np.median(a):+.5f}u  worst {a.max():+.5f}u (${a.max()*VPU:+.4f})")
        pos = a[a > 0]
        if len(pos):
            print(f"  {len(pos)}/{len(a)} sides slipped adversely, "
                  f"mean of adverse-only {pos.mean():+.5f}u")
        else:
            print("  no side slipped adversely yet")
    else:
        print("  no completed signal fills yet")

    live_spread = float(np.mean(sig_spreads)) if sig_spreads else 0.6261
    print(f"  live spread: {live_spread:.4f}u "
          f"(model charged {MODEL_SPREAD}u -> gap {live_spread - MODEL_SPREAD:.4f}u "
          f"= ${(live_spread - MODEL_SPREAD) * VPU:.4f}/round-trip)")

    # ---- the number that decides the mission ------------------------------
    mean_adv = float(np.mean(sig_adv)) if sig_adv else 0.0
    extra = (live_spread - MODEL_SPREAD) * VPU + 2 * mean_adv * VPU
    net_edge = GROSS_EDGE - extra
    flip = float(np.interp(extra, LADDER_X, LADDER_FLIP))
    ruin = float(np.interp(extra, LADDER_X, LADDER_RUIN))

    print("\n" + "=" * 78)
    print(f"EXTRA EXECUTION COST vs backtest model: ${extra:.4f} per round trip")
    print(f"  spread gap        ${(live_spread - MODEL_SPREAD) * VPU:.4f}")
    print(f"  slippage (2 sides) ${2 * mean_adv * VPU:+.4f}")
    print(f"  -> remaining net edge: ${net_edge:.4f}/trade (gross ${GROSS_EDGE:.3f})")
    print(f"  -> interpolated 4-week odds at $10: flip {flip:.1f}% / ruin {ruin:.1f}%")
    if n_sides < MIN_N:
        print(f"  [CAUTION] n={n_sides} signal sides < {MIN_N}: this is a running "
              "estimate, not a verdict — spike-cascade slippage is under-sampled "
              "until more trades accumulate")
    if extra < 0.06:
        print("  VERDICT ZONE: favorable (spread-gap only, fills clean)")
    elif extra < 0.12:
        print("  VERDICT ZONE: marginal (half the edge gone)")
    else:
        print("  VERDICT ZONE: hostile (edge mostly or fully eaten)")
    print("=" * 78)

    # cadence -> when n will be enough
    if n_days and signals:
        per_day = n_sides / n_days
        if per_day > 0:
            need = max(0, MIN_N - n_sides)
            print(f"cadence: {per_day:.1f} signal sides/day -> ~{need / per_day:.0f} "
                  "more day(s) to verdict sample size")


if __name__ == "__main__":
    main()
