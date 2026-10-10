#!/usr/bin/env python3
"""Concept development for the Flip phase-follower (flip1000 @5.0, min_age=1).

Baseline (incumbent, the executor's config): enter after 2 consecutive
same-direction large moves, exit on counter-spike, fixed 0.1 lot.

Concepts tested (all same detector |delta| > 5x frozen median, same fill
model = first tick at which the trigger is observable, same live spread
0.6261u charged once per position episode):
  P2/P3   pyramiding      add 0.1 lot per extra same-direction move (cap 2/3)
  A20/A30 amplitude exit  take profit when cumulative same-direction spike
                          amplitude since entry >= X medians, re-enter on the
                          next same-direction spike (phase intact)
  T10/T20 trail exit      trailing stop: exit when price retraces X medians
                          from the best price since entry
  TM10/TM30 time stop    exit if no favorable resolution within N minutes
  L/S      one-sided      long-only / short-only phase following
Evaluation: trips/wk, WR, net $/wk, avg lots, net $/wk per 0.1 lot, maxDD,
worst trip. Concepts that beat the baseline net with lower maxDD get a
day-block survival run ($10 start, fixed lot, 4-week horizon) comparable
with the measured baseline numbers (52.9% flip / 33.4% ruin).

Usage: python analysis/flip_concepts.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_gapmask import bad_tick_mask
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
MT5_NAME = "Crash Boom Flip 1000 Index"
MULT = 5.0
MIN_AGE = 1
LIVE_SPREAD = 0.6261          # measured repeatedly, stable (units of price)
STEP_LOTS = 0.1
HORIZON_DAYS = 28
N_SIMS = 2000


def simulate(ms, px, med, mult=MULT, min_age=MIN_AGE, mode="base",
             cap=1, amp=None, trail=None, mins=None, side=0):
    """Generalized phase-follower. Returns (trades, n_trips) where trades is
    a list of (net_usd, close_ms) with live spread charged per episode."""
    vpu = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]  # $/unit/1.0 lot
    d = np.diff(px)
    big_idx = set(np.where(np.abs(d) > mult * med)[0])

    pos = 0.0                    # signed lots
    entry = 0.0
    entry_ms = 0
    best = 0.0                   # best favorable price since entry (trail)
    cum = 0.0                    # cumulative same-direction spike amplitude (amp)
    lots_traded = 0.0            # total lots opened this episode (spread base)
    pos_tick_sum = 0.0           # exposure normalization
    pos_tick_n = 0
    cur_dir, cnt = 0, 0
    trades = []
    n_trips = 0

    def close(fill, tms):
        nonlocal pos, n_trips
        gross = (fill - entry) * pos * vpu
        trades.append((gross - LIVE_SPREAD * lots_traded * vpu, tms))
        n_trips += 1
        pos = 0.0

    n = len(px)
    bad = bad_tick_mask(ms)
    for j in range(WARMUP, n):
        if bad[j]:
            # data hole: the incoming delta is fake (spans dead time).
            # Drop any open position (its fill would be phantom PnL) and
            # reset phase state — nothing observable crosses the gap.
            pos = 0.0
            lots_traded = 0.0
            cur_dir, cnt = 0, 0
            continue
        is_spike = (j - 1) in big_idx          # move d[j-1] lands at tick j
        if is_spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):  # counter-spike: exit (and no
                close(px[j], ms[j])             # re-entry: fresh cnt=1 < gate)
                continue
        else:
            # time stop: checked on every non-spike tick while positioned
            if pos != 0 and mins is not None and ms[j] - entry_ms > mins * 60_000:
                close(px[j], ms[j])
                continue

        if pos != 0:
            pos_tick_sum += abs(pos)
            pos_tick_n += 1
            fav = np.sign(pos)
            if mode == "amp" and amp is not None and is_spike and s == fav:
                cum += abs(d[j - 1])
                if cum >= amp * med:
                    close(px[j], ms[j])
            elif mode == "trail" and trail is not None:
                if fav > 0:
                    best = max(best, px[j])
                    if best - px[j] >= trail * med:
                        close(px[j], ms[j])
                else:
                    best = min(best, px[j])
                    if px[j] - best >= trail * med:
                        close(px[j], ms[j])

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= min_age + 1 and is_spike:
                pos, entry, entry_ms = STEP_LOTS * cur_dir, px[j], ms[j]
                cum, best, lots_traded = 0.0, px[j], STEP_LOTS
        elif mode == "pyramid" and is_spike and cur_dir == np.sign(pos) \
                and cnt >= min_age + 2 and abs(pos) < cap * STEP_LOTS:
            # weighted-average entry so added lots aren't credited at the
            # original (better) price
            entry = (entry * abs(pos) + px[j] * STEP_LOTS) / (abs(pos) + STEP_LOTS)
            pos += STEP_LOTS * cur_dir
            lots_traded += STEP_LOTS
    if pos != 0:
        close(px[-1], ms[-1])
    avg_lots = pos_tick_sum / pos_tick_n if pos_tick_n else 0.0
    return trades, avg_lots


def metrics(trades, ms):
    arr = np.array([t[0] for t in trades]) if trades else np.array([0.0])
    dur_days = (ms[-1] - ms[WARMUP]) / 86_400_000
    wk = dur_days / 7
    cum = np.cumsum(arr)
    dd = float(np.max(np.maximum.accumulate(cum) - cum)) if len(cum) else 0.0
    return dict(
        trips=len(trades), per_wk=len(trades) / wk, wr=float((arr > 0).mean() * 100),
        net_wk=float(arr.sum() / wk), maxdd=dd, worst=float(arr.min()),
        dur_wk=wk,
    )


def day_block_survival(trades, start_eq=10.0, target=100.0, seed=11):
    """Resample whole UTC days (block bootstrap) into 4-week horizons."""
    days = {}
    for pnl, tms in trades:
        day = datetime.fromtimestamp(tms / 1000, tz=timezone.utc).date()
        days.setdefault(day, 0.0)
        days[day] += pnl
    daily = np.array(list(days.values()))
    rng = np.random.default_rng(seed)
    flipped = ruined = 0
    for _ in range(N_SIMS):
        eq = start_eq
        for day_pnl in rng.choice(daily, size=HORIZON_DAYS, replace=True):
            eq += day_pnl
            if eq <= 0:
                ruined += 1
                break
            if eq >= target:
                flipped += 1
                break
    return flipped / N_SIMS * 100, ruined / N_SIMS * 100


CONCEPTS = [
    ("baseline (executor)", dict()),
    ("P2 pyramid cap2", dict(mode="pyramid", cap=2)),
    ("P3 pyramid cap3", dict(mode="pyramid", cap=3)),
    ("A20 amplitude exit", dict(mode="amp", amp=20)),
    ("A30 amplitude exit", dict(mode="amp", amp=30)),
    ("T10 trail 10x", dict(mode="trail", trail=10)),
    ("T20 trail 20x", dict(mode="trail", trail=20)),
    ("TM10 time stop 10m", dict(mode="time", mins=10)),
    ("TM30 time stop 30m", dict(mode="time", mins=30)),
    ("L long-only", dict(side=1)),
    ("S short-only", dict(side=-1)),
]


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    print(f"=== {SYMBOL} @ {MULT}x min_age={MIN_AGE} | {len(px):,} ticks, "
          f"{(ms[-1] - ms[0]) / 86_400_000:.1f} days | med={med:.5f}u "
          f"spread={LIVE_SPREAD}u ===")
    print(f"{'concept':22s} {'trips':>5s} {'/wk':>5s} {'WR%':>6s} {'net$/wk':>8s} "
          f"{'avgLot':>6s} {'$/wk/lot':>8s} {'maxDD$':>7s} {'worst$':>7s}")
    results = {}
    for name, kw in CONCEPTS:
        trades, avg_lots = simulate(ms, px, med, **kw)
        m = metrics(trades, ms)
        per_lot = m["net_wk"] / avg_lots if avg_lots else float("nan")
        results[name] = (m, trades, avg_lots)
        print(f"{name:22s} {m['trips']:5d} {m['per_wk']:5.1f} {m['wr']:6.1f} "
              f"{m['net_wk']:8.2f} {avg_lots:6.2f} {per_lot:8.2f} {m['maxdd']:7.2f} "
              f"{m['worst']:7.2f}")

    base_m, base_tr, _ = results["baseline (executor)"]
    print("\n--- survival (day-block, $10 start, fixed lot, 4wk) ---")
    for name, (m, trades, _) in results.items():
        if name == "baseline (executor)" or (
                m["net_wk"] > base_m["net_wk"] and m["maxdd"] <= base_m["maxdd"] * 1.10):
            f, r = day_block_survival(trades)
            print(f"  {name:22s} flip {f:5.1f}%  ruin {r:5.1f}%   "
                  f"(baseline ref: 52.9 / 33.4)")
        else:
            print(f"  {name:22s} skipped (no edge/DD improvement)")


if __name__ == "__main__":
    main()
