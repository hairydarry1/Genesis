#!/usr/bin/env python3
"""Next-generation exit study: the four seams left open by Appendix N.

  1. COMBO     - TIMEBND x 25u drift stop (Appendix N's untested cell).
  2. ADAPTIVE  - state-dependent patience: patience = clip(K0 * (1 -
                 mae_u / cap_u), Kmin, K0). Deep underwater -> little
                 patience; near flat -> full patience. (The continuous
                 version of the user's "if it keeps going, close".)
  3. SESSION   - trades bucketed by entry UTC hour (0-3, 4-7, ... 20-23):
                 where does the edge live? (Foundation for a session filter
                 if one is real - grind_prediction found none at entry,
                 but time-of-day was never tested.)
  4. LADDER    - the mission question: compounding lot sizing vs fixed 0.1.
                 Lots = 0.1 while equity < 15, then 0.2 while < 30, 0.3
                 while < 50, 0.4 above (all at $0.10/unit/0.1lot). Same
                 trades, PnL scaled by (lots/0.1). Day-block bootstrap of
                 the equity path vs fixed sizing; also DD census.

Conventions as everywhere: frozen median, 5x detector, min_age=1, live
spread/episode, $0.10/unit, gap-masked, fleet-validated engine.

Usage: python analysis/flip_next_gen.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT


def run(ms, px, med, side=0, mode="counter", k=None, stop_u=None, kmin=None,
        cap_u=None, lots_fn=None, want_ms=False, want_entry_ms=False):
    """Phase-follower, pluggable exit + sizing. Returns (nets, close_ms)
    and optionally (entry_ms, entry_h) for session analysis."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    entry_ms = 0
    lots = STEP_LOTS
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    pend_j = 0
    nets = []
    cms = []
    ems = []

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            against = pos < 0

            if is_spike and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if mode == "counter" or pnl_now >= 0:
                    close(px[j], j)
                    continue
                if not pending:
                    pending, pend_j = True, j
                    continue
                # already pending: stays pending (no second adverse close)
            elif pending and is_spike and s == np.sign(pos):
                close(px[j], j)              # retrace arrived
                continue
            elif pending:
                if mode == "timebnd" and j - pend_j >= k:
                    close(px[j], j)          # patience out
                    continue
                # adaptive: patience shrinks as MAE approaches cap
                if mode == "adaptive" and kmin is not None and j - pend_j >= max(
                        kmin, int(k * max(0.0, 1.0 - (-worst) / cap_u))):
                    close(px[j], j)
                    continue
            # drift stop on any tick (spike or not), pending or not
            if stop_u is not None and -worst >= stop_u:
                close(px[j], j)
                continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and is_spike:
                lots = lots_fn(cms, nets) if lots_fn else STEP_LOTS
                pos, entry, entry_ms = lots * cur_dir, px[j], ms[j]
                ems.append(ms[j])
                worst, pending = 0.0, False
    if pos != 0:
        close(px[-1], len(px) - 1)
    nets_a = np.array(nets)
    if want_entry_ms:
        return nets_a, np.array(cms), np.array(ems)
    return (nets_a, np.array(cms)) if want_ms else nets_a


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = (peak - eq).max() if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:32s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def day_block(trades, lots_mult=None, start_eq=10.0, target=100.0,
              n_sims=2000, seed=11):
    """Day-block bootstrap with optional compounding lot multiplier.
    lots_mult(day_pnl) -> sizing multiplier for the next day (default 1)."""
    days = {}
    for pnl, tms in trades:
        day = datetime.fromtimestamp(tms / 1000, tz=timezone.utc).date()
        days.setdefault(day, 0.0)
        days[day] += pnl
    daily = np.array(sorted(days.items()))     # keep order; resample anyway
    vals = daily[:, 1]
    rng = np.random.default_rng(seed)
    flipped = ruined = 0
    for _ in range(n_sims):
        eq = start_eq
        m = 1.0
        for day_pnl in rng.choice(vals, size=28, replace=True):
            eq += day_pnl * m
            if eq <= 0:
                ruined += 1
                break
            if eq >= target:
                flipped += 1
                break
            m = lots_mult(eq) if lots_mult else 1.0
    return flipped / n_sims * 100, ruined / n_sims * 100


def ladder_fn(eq):
    if eq < 15:
        return 1.0
    if eq < 30:
        return 2.0
    if eq < 50:
        return 3.0
    return 4.0


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f}\n")

    print("=== 1. COMBO: TIMEBND x 25u drift stop ===")
    for side, sname in ((0, "LS"), (1, "L")):
        for mode, stop, label in (
                ("counter", None, "incumbent"),
                ("counter", 25.0, "incumbent + 25u stop"),
                ("timebnd", None, "TIMEBND 2000"),
                ("timebnd", 25.0, "TIMEBND 2000 + 25u stop")):
            nets = run(ms, px, med, side=side, mode=mode, k=2000, stop_u=stop)
            stats(nets, days, f"{sname} {label}")
        print()

    print("=== 2. ADAPTIVE patience (K0=2000, Kmin=200, cap=30u) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        variants = [
            (dict(mode="timebnd", k=2000), "TIMEBND 2000 (ref)"),
            (dict(mode="adaptive", k=2000, kmin=200, cap_u=30.0),
             "ADAPTIVE 2000->200 @30u"),
            (dict(mode="adaptive", k=2000, kmin=500, cap_u=20.0),
             "ADAPTIVE 2000->500 @20u"),
        ]
        for kw, label in variants:
            nets = run(ms, px, med, side=side, **kw)
            stats(nets, days, f"{sname} {label}")
        print()

    print("=== 3. SESSION: net by ENTRY-hour bucket (long-only incumbent) ===")
    nets, cms, ems = run(ms, px, med, side=1, mode="counter",
                         want_ms=True, want_entry_ms=True)
    hours = np.array([datetime.fromtimestamp(t / 1000, tz=timezone.utc).hour
                      for t in ems])
    dayof = np.array([datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
                      for t in ems])
    for lo in range(0, 24, 4):
        sel_m = (hours >= lo) & (hours < lo + 4)
        sel = nets[sel_m]
        if len(sel):
            top_day, top_amt = None, 0.0
            for dd in set(dayof[sel_m]):
                amt = nets[sel_m & (dayof == dd)].sum()
                if amt > top_amt:
                    top_day, top_amt = dd, amt
            conc = top_amt / sel.sum() * 100 if sel.sum() > 0 else 0
            print(f"  {lo:02d}-{lo+4:02d}Z  n={len(sel):3d}  "
                  f"net=${sel.sum():+6.2f}  mean=${sel.mean():+5.2f}  "
                  f"WR={(sel > 0).mean()*100:4.1f}%  "
                  f"topday={top_day} ({conc:.0f}% of bucket)")
    print()

    print("=== 4. LADDER: compounding vs fixed (survival, $10->100, 4wk) ===")
    for sname, side, mode, kw in (
            ("L incumbent", 1, "counter", {}),
            ("L TIMEBND2000", 1, "timebnd", dict(k=2000)),
            ("L TIMEBND5000", 1, "timebnd", dict(k=5000)),
            ("LS TIMEBND2000", 0, "timebnd", dict(k=2000))):
        nets, cms = run(ms, px, med, side=side, mode=mode,
                        **kw, want_ms=True)
        trades = list(zip(nets.tolist(), cms.tolist()))
        for sizing, mfn, tag in (("fixed", None, "0.1 lot"),
                                 ("ladder", ladder_fn, "0.1->0.4")):
            fl, ru = day_block(trades, lots_mult=mfn)
            print(f"  {sname:16s} {tag:9s} flip={fl:5.1f}%  ruin={ru:4.1f}%")


if __name__ == "__main__":
    main()
