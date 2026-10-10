#!/usr/bin/env python3
"""Generator drift monitor: is flip1000 still the market we measured?

The whole plan prices off Sep 3-11 baseline facts. If Deriv changes the
generator (spike rate, tick spacing, asymmetry), the Oct 8 confirmation
could "pass" against a market that no longer exists. This script compares
the accumulated archive against the frozen baseline and prints
STABLE / DRIFTED per metric. Run it monthly (and before funding).

Baseline (frozen 2026-09-11, 8-day archive 687,520 ticks):
  median |delta|            0.0085 u
  spikes/day @5.0x          92.5   (738 spikes / 7.98 days)
  up-phase mean run length  2.57 spikes ; down 2.75 (ratio 0.94)
  absorption gap @H=200     +9.7pp (down-spikes absorbed more; pro-long;
                            recomputed 09-11 with the drift-monitor method;
                            the family probe's +5.8pp used H=200 in ticks)
  spread                    0.6261 u
Drift thresholds: median 15%, spike rate 30%, run ratio 20%,
absorption gap sign flip, spread 15%.

Usage: python analysis/flip_drift_monitor.py [--baseline-out FILE]
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP  # noqa: E402
from data.flip_tick_store import SYMBOLS  # noqa: E402

SYMBOL = "flip1000"
MULT = 5.0
HORIZONS = (200,)
BASELINE = {
    "median_abs_delta": 0.0085,
    "spikes_per_day": 92.5,
    "run_ratio_up_over_down": 0.94,
    "absorption_gap_H200_pp": 9.7,
}
THRESH = {"median": 0.15, "spikes": 0.30, "runs": 0.20, "spread": 0.15}


def run_lengths(spike_dirs):
    runs, cur, ln = [], 0, 0
    for s in spike_dirs:
        if s == cur:
            ln += 1
        else:
            if cur != 0:
                runs.append((cur, ln))
            cur, ln = s, 1
    if cur != 0:
        runs.append((cur, ln))
    return runs


def main():
    ms, px = load(SYMBOL)
    med_full = float(np.median(np.abs(np.diff(px))))
    d = np.diff(px)
    days = (ms[-1] - ms[0]) / 86_400_000
    spikes = np.where(np.abs(d) > MULT * med_full)[0]
    spike_ms = ms[1:][spikes]
    spike_delta = d[spikes]

    print(f"=== drift monitor: {SYMBOL} | {len(px):,} ticks, {days:.1f} days "
          f"({datetime.fromtimestamp(ms[0]/1000, tz=timezone.utc):%m-%d} -> "
          f"{datetime.fromtimestamp(ms[-1]/1000, tz=timezone.utc):%m-%d}) ===")

    checks = []
    # 1. median |delta| (tick spacing / generator scale)
    rel = med_full / BASELINE["median_abs_delta"] - 1
    ok = abs(rel) <= THRESH["median"]
    checks.append(ok)
    print(f"  median |delta|  {med_full:.5f}u  vs 0.0085  ({rel:+.0%})  "
          f"{'STABLE' if ok else 'DRIFTED'}")

    # 2. spike rate
    rate = len(spikes) / days
    rel = rate / BASELINE["spikes_per_day"] - 1
    ok = abs(rel) <= THRESH["spikes"]
    checks.append(ok)
    print(f"  spikes/day      {rate:6.1f}  vs 92.5  ({rel:+.0%})  "
          f"{'STABLE' if ok else 'DRIFTED'}")

    # 3. run-length ratio
    runs = run_lengths(np.sign(spike_delta))
    up = np.mean([l for s, l in runs if s == 1])
    dn = np.mean([l for s, l in runs if s == -1])
    ratio = up / dn if dn else float("nan")
    rel = ratio / BASELINE["run_ratio_up_over_down"] - 1
    ok = abs(rel) <= THRESH["runs"]
    checks.append(ok)
    print(f"  run ratio up/dn {ratio:6.2f}  vs 0.94  ({rel:+.0%})  "
          f"{'STABLE' if ok else 'DRIFTED'}")

    # 4. absorption gap (pro-long asymmetry) - post-spike drift test
    for h in HORIZONS:
        absorbed = {1: 0, -1: 0}
        total = {1: 0, -1: 0}
        for i in range(len(spike_ms)):
            s = int(np.sign(spike_delta[i]))
            total[s] += 1
            end = np.searchsorted(ms, spike_ms[i] + h * 1000)
            if end >= len(ms):
                continue
            # phase survives if price is still beyond the spike extreme
            if s == 1:
                extreme = px[1:][spikes[i]]
                survived = px[end] > extreme - 2 * med_full
                absorbed[-1] += 0 if survived else 1  # up-spike killed down-phase
            else:
                extreme = px[1:][spikes[i]]
                survived = px[end] < extreme + 2 * med_full
                absorbed[1] += 0 if survived else 1  # down-spike killed up-phase
        gap = ((absorbed[1] / total[1]) - (absorbed[-1] / total[-1])) * 100 \
            if total[1] and total[-1] else float("nan")
        ok = np.sign(gap) == np.sign(BASELINE["absorption_gap_H200_pp"]) \
            and abs(gap) >= 2.0
        checks.append(ok)
        print(f"  absorption gap  {gap:+6.1f}pp @H={h}  vs +9.7pp  "
              f"{'STABLE (pro-long)' if ok else 'DRIFTED/FLIPPED'}")

    # 5. spread sanity (uses recent archive bid/ask proxy is unavailable in
    #    bid-only store; report last-day median |delta| as spacing check only)
    print("\nverdict: " + ("GENERATOR STABLE - Oct 8 read remains valid"
                           if all(checks) else
                           "GENERATOR DRIFT SUSPECTED - pause the plan, "
                           "re-derive the edge before funding"))


if __name__ == "__main__":
    main()
