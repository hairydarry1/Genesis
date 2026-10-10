#!/usr/bin/env python3
"""Daily-halt x drift-stop interaction grid (flip1000, pre-28d study).

Question (user, Sep 12): "isn't the $9 stop too much? On a $10 book that's
devastating." Stack today: counter-spike exit (normal losses $0.06-$1.16),
60u broker backstop SL ($6.00 worst, executor-dead scenarios), daily halt
at -$9 realized PnL (new entries only, UTC reset). Worst-day arithmetic
WITHOUT a per-trade cap: SL(-$6) -> re-enter -> SL(-$6) = -$12 day on a
$10 book. The halt fires only after the trade that crosses it has landed.

The old "tight halts are poison" result (-$5: ruin 95%) was measured in a
world where single trades could lose $5+. Hypothesis: with the 25u drift
stop truncating per-trade losses at ~$2.56, tighter daily halts stop being
poison and the day floor becomes structurally safe.

Grid: drift stop {none, 25u} x daily halt {none, 9, 7, 6, 5, 4, 3, 2.5}
for LS and long-only. Day-block bootstrap (28d horizon, 2000 sims, $10
fixed lot, flip >= $100, ruin <= $0) + historical path + worst-day stats.
Gap-masked in-sim (bad_tick_mask), same conventions as flip_concepts.

Usage: python analysis/flip_daily_x_drift.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS
from flip_gapmask import bad_tick_mask
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]  # $/unit per 1.0 lot
SPREAD_USD = LIVE_SPREAD * VPU * STEP_LOTS
HALTS = [None, 9.0, 7.0, 6.0, 5.0, 4.0, 3.0, 2.5]
N_SIMS = 2000
HORIZON = 28


def run(ms, px, med, stop_u, side):
    """Base phase-follower + optional entry-anchored drift stop.
    Returns [(net_usd, close_ms)]."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)
    pos = 0.0
    entry = 0.0
    cur, cnt = 0, 0
    worst = 0.0
    out = []

    def close(fill, tms):
        nonlocal pos, worst
        out.append(((fill - entry) * pos * VPU - SPREAD_USD, tms))
        pos = 0.0
        worst = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos = 0.0
            cur, cnt = 0, 0
            worst = 0.0
            continue
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur:
                cnt += 1
            else:
                cur, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                close(px[j], ms[j])
                continue
        elif pos != 0 and stop_u is not None and -worst >= stop_u:
            close(px[j], ms[j])
            continue
        if pos == 0:
            want = (side == 0) or (cur == side)
            if want and cnt >= MIN_AGE + 1 and big[j - 1]:
                pos, entry = STEP_LOTS * cur, px[j]
                worst = 0.0
    return out


def day_map(trades):
    days = {}
    for pnl, tms in trades:
        day = datetime.fromtimestamp(tms / 1000, tz=timezone.utc).date()
        days.setdefault(day, []).append(pnl)
    return [np.array(v) for _, v in sorted(days.items())]


def walk(daily, halt):
    """28-day day-block bootstrap with an optional daily entry-halt."""
    rng = np.random.default_rng(11)
    flipped = ruined = 0
    finals = np.empty(N_SIMS)
    worst_day = 0.0
    min_eq_all = 10.0
    halts_fired = 0
    for i in range(N_SIMS):
        eq = 10.0
        me = eq
        wd = 0.0
        done = False
        for _ in range(HORIZON):
            tr = daily[rng.integers(len(daily))]
            dp = 0.0
            halted = False
            for p in tr:
                if halted:
                    break
                eq += p
                dp += p
                me = min(me, eq)
                if eq <= 0:
                    ruined += 1
                    done = True
                    break
                if eq >= 100.0:
                    flipped += 1
                    done = True
                    break
                if halt is not None and dp <= -halt:
                    halted = True
                    halts_fired += 1
            wd = min(wd, dp)
            if done:
                break
        worst_day = min(worst_day, wd)
        min_eq_all = min(min_eq_all, me)
        finals[i] = eq
    return dict(flip=flipped / N_SIMS * 100, ruin=ruined / N_SIMS * 100,
                med=float(np.median(finals)), worst_day=worst_day,
                min_eq=min_eq_all, halts_day=halts_fired / N_SIMS / HORIZON)


def hist_path(daily, halt):
    eq = 10.0
    min_eq = eq
    fired = 0
    for tr in daily:
        dp = 0.0
        halted = False
        for p in tr:
            if halted:
                break
            eq += p
            dp += p
            min_eq = min(min_eq, eq)
            if halt is not None and dp <= -halt:
                halted = True
                fired += 1
    return eq, min_eq, fired


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    days_n = (ms[-1] - ms[0]) / 86400e3
    print(f"archive: {days_n:.1f} days, med={med:.5f}  "
          f"(per-trade worst: 60u SL = $6.00; 25u stop = ${25 * 0.10 + 0.06:.2f})\n")

    for side, sname in ((0, "LS (demo executor config)"),
                        (1, "L long-only (deployment candidate)")):
        print(f"=== {sname} ===")
        print(f"{'halt ->':10s}" + "".join(f"{('none' if h is None else f'-${h:g}'):>9s}"
                                           for h in HALTS))
        for stop_u, slabel in ((None, "no drift"), (25.0, "25u drift")):
            trades = run(ms, px, med, stop_u, side)
            daily = day_map(trades)
            nets = np.array([t[0] for t in trades])
            cells = []
            for h in HALTS:
                r = walk(daily, h)
                cells.append(f"{r['flip']:3.0f}/{r['ruin']:3.0f}")
            print(f"{slabel:10s}" + "".join(f"{c:>9s}" for c in cells)
                  + "   (flip%/ruin%)")
            # detail for the two most interesting halts
            for h in (None, 5.0):
                r = walk(daily, h)
                hn = "none" if h is None else f"-${h:g}"
                print(f"   [{slabel:9s} halt {hn:>4s}] worst sim day ${r['worst_day']:+6.2f}  "
                      f"min equity touched ${r['min_eq']:5.2f}  median final ${r['med']:6.2f}  "
                      f"halts {r['halts_day']:.2f}/day")
            eq, me, fired = hist_path(daily, 9.0)
            print(f"   historical path (halt -$9): final ${eq:+6.2f}  min ${me:5.2f}  "
                  f"halts fired {fired}  | raw: {len(trades)} trips, net ${nets.sum():+.2f}")
        print()

    print("reading: flip/ruin at $10 fixed lot, 28d, 2000 sims. Day floor with")
    print("25u drift stop + halt T: worst day ~ max(2 stop-outs, T + one stop-out).")


if __name__ == "__main__":
    main()
