#!/usr/bin/env python3
"""Daily-loss stop rule backtest + survival re-run (fixed sizing).

Motivation: loss forensics showed the $10 ruin mechanism is a drifted-down
book meeting a bad day. Boom/Crash lesson: halts can be poison (they skip
recovery wins). So test, don't assume.

Method:
  - flip1000 @5.0 min_age=1, corrected live spread (0.6261u vs 0.05u modeled
    => extra -0.5761u per trade), FIXED 1 step (0.1 lot) always
  - day-block bootstrap: each simulated week = 8 whole UTC days resampled
    with replacement from the 8 historical days. This preserves within-day
    loss clustering (what the stop acts on) unlike trade-level iid.
  - stop rule: once a day's cumulative PnL <= -THRESH, skip the rest of that
    day's trades (halt resets at UTC midnight). Ladder of thresholds.

Usage: python analysis/flip_daily_stop.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_phase_backtest import get_specs, path_sim

SYM, MULT, K = "flip1000", 5.0, 1
LIVE_SP, MODEL_SP = 0.6261, 0.05
THRESHOLDS = [None, 9.0, 8.0, 7.0, 5.0, 3.0]
SIMS, DAYS_PER_WEEK, WEEKS = 4000, 8, 4      # 4-week horizon (comparable to survival numbers)


def corrected_trades():
    ms, px = load(SYM)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    vpu = get_specs()[SYM]["tick_value"] / get_specs()[SYM]["tick_size"] * 0.1
    # per-trade close timestamps (parity-checked variant from forensics)
    d = np.diff(px)
    big_idx = np.where(np.abs(d) > MULT * med)[0]
    bad = bad_tick_mask(ms)                    # gap-masking
    big_idx = big_idx[~bad[np.minimum(big_idx + 1, len(px) - 1)]]
    holes = hole_intervals(ms)
    pos, entry, cur_dir, cnt = 0, 0.0, 0, 0
    trades = []                                  # (pnl_units, close_idx)
    last_t = None
    for i in big_idx:
        t_fill = ms[i + 1] if i + 1 < len(ms) else ms[-1]
        if last_t is not None and crosses_hole(holes, last_t, t_fill):
            pos, cur_dir, cnt = 0, 0, 0
        last_t = t_fill
        s = int(np.sign(d[i]))
        ci = i + 1 if i + 1 < len(px) else len(px) - 1
        fill = px[ci]
        if s == cur_dir:
            cnt += 1
        else:
            cur_dir, cnt = s, 1
        if pos == 0:
            if cnt >= K + 1:
                pos, entry = s, fill
        elif pos != s:
            trades.append((pos * (fill - entry), ci))
            pos = 0
            if cnt >= K + 1:
                pos, entry = s, fill
    if pos:
        trades.append((pos * (px[-1] - entry), len(px) - 1))
    # correct to live spread, convert to USD at fixed 1 step
    arr = np.array([[(p - (LIVE_SP - MODEL_SP)) * vpu, float(c)] for p, c in trades])
    return ms, arr


def day_blocks(ms, arr):
    days = {}
    for (pnl, ci) in arr:
        day = datetime.fromtimestamp(ms[int(ci)] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(pnl)
    return [(d, np.array(v)) for d, v in sorted(days.items())]


def sim_week(days, thresh, rng):
    """One simulated 4-week horizon: resample whole day-blocks with replacement
    (WEEKS x DAYS_PER_WEEK blocks). Daily stop resets at each block boundary.
    Returns (eq, ruined, flipped, taken, skipped)."""
    eq = 10.0
    taken = skipped = 0
    for _ in range(WEEKS * DAYS_PER_WEEK):
        day = days[rng.integers(len(days))][1]
        day_pnl = 0.0
        for t in day:
            if thresh is not None and day_pnl <= -thresh:
                skipped += 1
                continue
            eq += t
            day_pnl += t
            taken += 1
            if eq <= 0:
                return 0.0, True, False, taken, skipped
        if eq >= 100:
            return eq, False, True, taken, skipped
    return eq, False, False, taken, skipped


def main():
    ms, arr = corrected_trades()
    day_list = day_blocks(ms, arr)
    n = len(arr)
    print(f"flip1000@{MULT} k={K}, corrected spread, FIXED 1 step")
    print(f"{n} trades over {len(day_list)} UTC days; historical day PnL:")
    for d, v in day_list:
        print(f"  {d}: {len(v):3d} trades  {v.sum():+7.2f}")
    rng = np.random.default_rng(7)
    print(f"\n4-week horizon ({WEEKS}x{DAYS_PER_WEEK} day-blocks), fixed 1 step:")
    print(f"{'stop':>6s} {'flip%':>7s} {'ruin%':>7s} {'alive%':>7s} "
          f"{'trades/4wk':>10s} {'skipped/4wk':>11s}")
    for thresh in THRESHOLDS:
        ru = fl = al = 0
        tot_taken = tot_skip = 0
        for _ in range(SIMS):
            eq, ruined, flipped, taken, skipped = sim_week(day_list, thresh, rng)
            tot_taken += taken
            tot_skip += skipped
            if ruined:
                ru += 1
            elif flipped:
                fl += 1
            else:
                al += 1
        label = "none" if thresh is None else f"-${thresh:.0f}"
        print(f"{label:>6s} {fl / SIMS * 100:>6.1f}% {ru / SIMS * 100:>6.1f}% "
              f"{al / SIMS * 100:>6.1f}% {tot_taken / SIMS:>10.0f} {tot_skip / SIMS:>11.0f}")

    # historical single path with the best threshold, for clustering honesty
    print("\nhistorical path (clustering intact), fixed 1 step:")
    for thresh in THRESHOLDS:
        eq = 10.0
        day_pnl, cur_day = 0.0, None
        min_eq = eq
        for (pnl, ci) in arr:
            day = datetime.fromtimestamp(ms[int(ci)] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
            if day != cur_day:
                cur_day, day_pnl = day, 0.0
            if thresh is not None and day_pnl <= -thresh:
                continue
            eq += pnl
            day_pnl += pnl
            min_eq = min(min_eq, eq)
            if eq <= 0:
                break
        label = "none" if thresh is None else f"-${thresh:.0f}"
        print(f"  stop {label:>5s}: final ${eq:.2f}  min ${min_eq:.2f}")


if __name__ == "__main__":
    main()
