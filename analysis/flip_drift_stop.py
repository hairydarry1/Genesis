#!/usr/bin/env python3
"""Catastrophic drift-stop ladder for flip1000 (post-Sep-12 grinddown study).

Distinct from the rejected trail exits (retrace-from-BEST): this is an
entry-anchored MAE cap — if price moves `stop_u` price-units against the
entry, close at market on that tick and stay out until a fresh phase gate.
Goal: amputate only the extreme left tail (the drift-dominated grind losses
like Sep 10 -$5.43 and Sep 12 -$5.37 live) without touching normal trades
(MAE p50 = 7.6u, p90 = 24.7u, p99 = 51.8u).

Conventions identical to flip_concepts.py (frozen median @WARMUP=5000,
|d| > 5x median detector, min_age=1, live spread per episode, $0.10/unit).
Reports per stop level: net $/wk, WR, avg win/loss, worst trip, maxDD.
Also runs long-only (the presumed Oct 8 config) and a winner-damage census
(incumbent trades whose MAE touched each stop yet finished positive — the
true cost of the stop).

Usage: python analysis/flip_drift_stop.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT
STOPS_U = [None, 25.0, 35.0, 50.0]   # None = incumbent (no drift stop)


def run(ms, px, med, stop_u, side=0):
    """Base phase-follower + optional entry-anchored MAE stop.
    Returns nets array ($ per trade, spread included)."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)                    # gap-masking

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    nets = []

    def close(fill):
        nonlocal pos, worst
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        pos = 0.0
        worst = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:                       # hole: drop position + phase memory
            pos = 0.0
            cur_dir, cnt = 0, 0
            worst = 0.0
            continue
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                close(px[j])
                continue
        elif pos != 0 and stop_u is not None and -worst >= stop_u:
            close(px[j])
            continue
        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and big[j - 1]:
                pos, entry = STEP_LOTS * cur_dir, px[j]
                worst = 0.0
    return np.array(nets)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = (peak - eq).max() if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:24s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum():+7.2f} (${nets.sum()/days*7:+6.2f}/wk)  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[0]) / 86400e3
    print(f"archive: {days:.1f} days, med={med:.5f}\n")

    for side, sname in ((0, "LS (incumbent)"), (1, "L long-only")):
        for stop_u in STOPS_U:
            nets = run(ms, px, med, stop_u, side=side)
            stats(nets, days, f"{sname} stop={stop_u or 'none':>5}")
        print()

    # winner-damage census: incumbent trades whose MAE touched each stop yet
    # finished positive = trades the stop would have killed for a loss
    print("winner-damage census (incumbent trades, MAE >= stop):")
    thr = MULT * med
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)                    # gap-masking
    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    recs = []

    def close2(fill):
        nonlocal pos, worst
        recs.append((-worst, (fill - entry) * pos * VPU - SPREAD_USD))
        pos = 0.0
        worst = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:                       # hole: drop position + phase memory
            pos = 0.0
            cur_dir, cnt = 0, 0
            worst = 0.0
            continue
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                close2(px[j])
                continue
        if pos == 0 and cnt >= MIN_AGE + 1 and big[j - 1]:
            pos, entry = STEP_LOTS * cur_dir, px[j]
            worst = 0.0
    mae_arr = np.array([r[0] for r in recs])
    net_arr = np.array([r[1] for r in recs])
    print(f"  incumbent: {len(recs)} trips, net ${net_arr.sum():+.2f}")
    for stop_u in (25.0, 35.0, 50.0):
        hit = mae_arr >= stop_u
        w = (net_arr > 0) & hit
        l = (net_arr <= 0) & hit
        # stopped-loss value: each touched trade becomes ~(stop cost) instead
        capped = -stop_u * PER_UNIT - SPREAD_USD
        saved = net_arr[l].sum() - capped * l.sum()
        lost = net_arr[w].sum() - capped * w.sum()
        print(f"  stop {stop_u:4.0f}u: touches {hit.sum():3d}/{len(recs)} trades "
              f"({hit.mean()*100:4.1f}%)  would-be wins killed: {w.sum():3d} "
              f"(they made ${net_arr[w].sum():+6.2f} -> stop converts to ${capped*w.sum():+6.2f}, "
              f"damage ${lost:+6.2f})  would-be losses capped: {l.sum():3d} "
              f"(they lost ${net_arr[l].sum():+6.2f} -> ${capped*l.sum():+6.2f}, "
              f"saved ${saved:+6.2f})  net effect ${saved+lost:+6.2f}")


if __name__ == "__main__":
    main()
