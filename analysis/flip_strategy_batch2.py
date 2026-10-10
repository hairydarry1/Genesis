#!/usr/bin/env python3
"""Strategy development batch 2 for the Flip phase-follower (flip1000 @5.0).

Everything here runs on the frozen 8-day archive with the SAME conventions
as flip_concepts.py: detector |delta| > 5x frozen median, fills at the first
tick where the trigger is observable, live spread 0.6261u per position
episode. The executor config is FROZEN until Oct 8 - this file only decides
what we might adopt AFTER the confirmation, with receipts.

Variants (new vs flip_concepts.py):
  L / S / LS     side fraction ladder, each with $10 day-block survival
  MAG3/MAG5      magnitude gate: enter only when the confirming spike is
                 >= 3x / 5x median (does spike strength predict phase quality?)
  ABS            absorption re-entry: after a counter-spike exit, if the NEXT
                 spike is same-direction as the closed position, re-enter
                 immediately (monetizes the measured absorption asymmetry:
                 down-spikes are absorbed more often than up-spikes)
  SXC            side-asymmetric exit: shorts take profit at +2.0u cumulative
                 amplitude, longs ride uncapped (tail lives in longs)
  phase-structure decomposition: run lengths of consecutive same-direction
                 spikes by direction, and per-phase PnL buckets

Usage: python analysis/flip_strategy_batch2.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
MULT = 5.0
MIN_AGE = 1
LIVE_SPREAD = 0.6261
STEP_LOTS = 0.1
HORIZON_DAYS = 28
N_SIMS = 2000


def simulate(ms, px, med, side=0, entry_mag=None, absorption=False,
             short_tp=None):
    """Phase-follower with batch-2 variants. Returns (trips, runs).
    trips: list of (net_usd, close_ms). runs: list of (dir, length) for
    consecutive same-direction spike runs (whole sample, not per position)."""
    vpu = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
    d = np.diff(px)
    big_idx = set(np.where(np.abs(d) > MULT * med)[0])
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cum = 0.0
    lots_traded = 0.0
    cur_dir, cnt = 0, 0
    reentry_dir = 0          # ABS: direction allowed to re-enter on next spike
    trades = []
    runs = []                # (dir, length) completed runs
    run_len = 0

    def close(fill, tms, direction_just_closed):
        nonlocal pos, reentry_dir
        gross = (fill - entry) * pos * vpu
        trades.append((gross - LIVE_SPREAD * lots_traded * vpu, tms))
        pos = 0.0
        if absorption:
            reentry_dir = direction_just_closed

    n = len(px)
    for j in range(WARMUP, n):
        if bad[j]:
            pos = 0.0            # hole: drop open position (phantom fill),
            lots_traded = 0.0    # reset state, skip fake delta
            cum = 0.0
            cur_dir, cnt, run_len = 0, 0, 0
            reentry_dir = 0
            continue
        is_spike = (j - 1) in big_idx
        if is_spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
                run_len += 1
            else:
                if cur_dir != 0 and run_len:
                    runs.append((cur_dir, run_len))
                cur_dir, cnt, run_len = s, 1, 1
            if pos != 0 and s != np.sign(pos):
                close(px[j], ms[j], int(np.sign(pos)))
                continue
            # ABS re-entry: flat, armed, next spike resumes the closed phase
            if pos == 0 and reentry_dir != 0 and s == reentry_dir:
                pos, entry = STEP_LOTS * s, px[j]
                cum, lots_traded = 0.0, STEP_LOTS
                reentry_dir = 0
                continue
            reentry_dir = 0   # any other spike disarms the re-entry window
        # side-asymmetric TP: shorts cap at short_tp units of favorable cum
        if pos != 0 and short_tp is not None and np.sign(pos) < 0 and is_spike \
                and s == np.sign(pos):
            cum += abs(d[j - 1])
            if cum >= short_tp:
                close(px[j], ms[j], int(np.sign(pos)))
                continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            strong = entry_mag is None or abs(d[j - 1]) >= entry_mag * med
            if want and strong and cnt >= MIN_AGE + 1 and is_spike:
                pos, entry = STEP_LOTS * cur_dir, px[j]
                cum, lots_traded = 0.0, STEP_LOTS
    if pos != 0:
        close(px[-1], ms[-1], int(np.sign(pos)))
    if run_len and cur_dir != 0:
        runs.append((cur_dir, run_len))
    return trades, runs


def metrics(trades, ms):
    arr = np.array([t[0] for t in trades]) if trades else np.array([0.0])
    wk = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7
    cum = np.cumsum(arr)
    dd = float(np.max(np.maximum.accumulate(cum) - cum)) if len(cum) else 0.0
    return dict(trips=len(trades), per_wk=len(trades) / wk,
                wr=float((arr > 0).mean() * 100), net_wk=float(arr.sum() / wk),
                maxdd=dd, worst=float(arr.min()))


def day_block_survival(trades, start_eq=10.0, target=100.0, seed=11):
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


VARIANTS = [
    ("LS (executor config)", dict()),
    ("L long-only", dict(side=1)),
    ("S short-only", dict(side=-1)),
    ("MAG3 strong-entry", dict(entry_mag=3.0)),
    ("MAG5 strong-entry", dict(entry_mag=5.0)),
    ("ABS absorption re-entry", dict(absorption=True)),
    ("SXC short-cap-2.0u", dict(short_tp=2.0)),
    ("L+ABS combo", dict(side=1, absorption=True)),
]


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    days = (ms[-1] - ms[0]) / 86_400_000
    print(f"=== {SYMBOL} @ {MULT}x min_age={MIN_AGE} | {len(px):,} ticks, "
          f"{days:.1f} days | med={med:.5f}u | spread={LIVE_SPREAD}u ===")

    # ---- phase-structure decomposition ------------------------------------
    _, runs = simulate(ms, px, med)
    for sdir, label in ((1, "up-phases (+)"), (-1, "down-phases (-)")):
        lens = np.array([l for s, l in runs if s == sdir])
        if len(lens) == 0:
            continue
        q = np.percentile(lens, [50, 75, 90])
        print(f"  {label:16s} n={len(lens):4d}  mean={lens.mean():5.2f}  "
              f"p50={q[0]:.0f} p75={q[1]:.0f} p90={q[2]:.0f} "
              f"max={lens.max():3d} spikes")
    up = np.array([l for s, l in runs if s == 1])
    dn = np.array([l for s, l in runs if s == -1])
    if len(up) and len(dn):
        print(f"  run-length mean ratio up/down: {up.mean() / dn.mean():.2f}  "
              f"(>1 = up-phases longer = structural pro-long)")

    # ---- variant ladder ---------------------------------------------------
    print(f"\n{'variant':24s} {'trips':>5s} {'/wk':>5s} {'WR%':>6s} "
          f"{'net$/wk':>8s} {'maxDD$':>7s} {'worst$':>7s}")
    results = {}
    for name, kw in VARIANTS:
        trips, _ = simulate(ms, px, med, **kw)
        m = metrics(trips, ms)
        results[name] = (m, trips)
        print(f"{name:24s} {m['trips']:5d} {m['per_wk']:5.1f} {m['wr']:6.1f} "
              f"{m['net_wk']:8.2f} {m['maxdd']:7.2f} {m['worst']:7.2f}")

    print("\n--- survival (day-block, $10 start, fixed 0.1 lot, 4wk) ---")
    for name, (m, trips) in results.items():
        f, r = day_block_survival(trips)
        print(f"  {name:24s} flip {f:5.1f}%  ruin {r:5.1f}%")
    print("\nnote: 8 daily blocks -> survival CIs are wide; treat as ranking "
          "evidence, re-run on 28 days before adopting anything.")


if __name__ == "__main__":
    main()
