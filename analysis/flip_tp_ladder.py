#!/usr/bin/env python3
"""Fixed-TP ladder for the flip1000 phase-follower (docs/FLIP_INDEX_MECHANICS.md).

Question: does adding a classic take-profit (close at +X price units,
then STAY OUT until the phase resets on a counter-spike) beat the
incumbent exit (counter-spike only, uncapped ride)?

Why it matters: the strategy's edge is tail-shaped (avg win $1.52 =
~15u of price, avg loss $0.06 = spread). A TP caps the win side; the
only way it can help is if it (a) avoids late-phase reversals the
counter-spike exit misses, or (b) raises win rate enough to pay for the
surrendered continuation. Measured, not argued.

Method: same detector/fill model/spread as flip_concepts.py (live spread
0.6261u charged once per episode), TP checked on EVERY tick from entry,
re-entry blocked until a counter-spike resets the phase (classic TP
semantics, not the amp-mode chop-re-entry). Ladder: 1..40u + baseline.
Survival (day-block, $10->100, 4wk) for baseline vs the best netting TP.
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_concepts import (MULT, MIN_AGE, LIVE_SPREAD, STEP_LOTS,
                           day_block_survival)
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]


def simulate_tp(ms, px, med, tp=None):
    """Phase-follower with optional fixed TP in price units.

    tp=None  -> incumbent: exit on counter-spike only.
    tp=X     -> exit on counter-spike OR price move >= X favorable from
                entry (whichever first); after a TP close, re-entry is
                blocked until a counter-spike resets the phase.
    Returns list of (net_usd, close_ms)."""
    d = np.diff(px)
    big_idx = set(np.where(np.abs(d) > MULT * med)[0])
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    wait_reset = False          # after TP close: no entry until phase reset
    trades = []

    def close(fill, tms):
        nonlocal pos
        gross = (fill - entry) * pos * VPU
        trades.append((gross - LIVE_SPREAD * STEP_LOTS * VPU, tms))
        pos = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos = 0.0            # hole: drop open position (phantom fill),
            cur_dir, cnt = 0, 0  # reset phase state, skip fake delta
            continue
        is_spike = (j - 1) in big_idx
        if is_spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
                wait_reset = False          # phase reset -> entries allowed again
            if pos != 0 and s != np.sign(pos):
                close(px[j], ms[j])
                continue
        if pos != 0 and tp is not None:
            fav = (px[j] - entry) * np.sign(pos)
            if fav >= tp:
                close(px[j], ms[j])
                wait_reset = True
                continue
        if pos == 0 and not wait_reset and cnt >= MIN_AGE + 1 and is_spike:
            pos = STEP_LOTS * cur_dir
            entry = px[j]
    if pos != 0:
        close(px[-1], ms[-1])
    return trades


def metrics(trades, ms):
    arr = np.array([t[0] for t in trades]) if trades else np.array([0.0])
    wk = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7
    cum = np.cumsum(arr)
    dd = float(np.max(np.maximum.accumulate(cum) - cum)) if len(cum) else 0.0
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    return dict(trips=len(trades), per_wk=len(trades) / wk,
                wr=float((arr > 0).mean() * 100), net_wk=float(arr.sum() / wk),
                maxdd=dd, worst=float(arr.min()),
                avg_win=float(wins.mean()) if len(wins) else 0.0,
                avg_loss=float(losses.mean()) if len(losses) else 0.0)


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[0]) / 86_400_000
    print(f"=== {SYMBOL} TP ladder | {len(px):,} ticks, {days:.1f} days | "
          f"med={med:.5f}u spread={LIVE_SPREAD}u | TP in price units "
          f"($ = units x {VPU:.2f}/0.1lot... shown per 0.1 lot) ===")
    header = (f"{'TP':>10s} {'trips':>5s} {'/wk':>5s} {'WR%':>6s} {'net$/wk':>8s} "
              f"{'avgWin':>7s} {'avgLoss':>8s} {'maxDD$':>7s} {'worst$':>7s}")
    print(header)
    print("-" * len(header))

    ladder = [None, 0.5, 1.0, 2.0, 4.0, 8.0, 12.0, 20.0, 40.0]
    results = {}
    for tp in ladder:
        trades = simulate_tp(ms, px, med, tp=tp)
        m = metrics(trades, ms)
        name = "none (incumb)" if tp is None else f"{tp:.1f}u (${tp*VPU:.2f})"
        results[name] = (tp, m, trades)
        print(f"{name:>10s} {m['trips']:5d} {m['per_wk']:5.1f} {m['wr']:6.1f} "
              f"{m['net_wk']:8.2f} {m['avg_win']:7.2f} {m['avg_loss']:8.2f} "
              f"{m['maxdd']:7.2f} {m['worst']:7.2f}")

    # survival: baseline vs the best netting TP (if any beats baseline)
    base_name = "none (incumb)"
    tp_m = results[base_name][1]
    print("\n--- survival (day-block bootstrap, $10->100, 4wk, 2000 sims) ---")
    f0, r0 = day_block_survival(results[base_name][2])
    print(f"{base_name:>10s}  flip {f0:5.1f}%  ruin {r0:4.1f}%")
    better = [(n, v) for n, v in results.items() if n != base_name
              and v[1]["net_wk"] > tp_m["net_wk"]]
    if better:
        best_name, (tp, m, trades) = max(better, key=lambda kv: kv[1][1]["net_wk"])
        f1, r1 = day_block_survival(trades)
        print(f"{best_name:>10s}  flip {f1:5.1f}%  ruin {r1:4.1f}%   <- best netting TP")
    else:
        print("           no TP beat the incumbent net -> exit design confirmed")

    # where does the TP bind? distribution of incumbent win sizes in units
    base_trades = results[base_name][2]
    wins_u = sorted(t[0] / VPU + LIVE_SPREAD for t in base_trades if t[0] > 0)
    if wins_u:
        qs = np.percentile(wins_u, [25, 50, 75, 90])
        print(f"\nincumbent win sizes (price units): p25={qs[0]:.1f} "
              f"p50={qs[1]:.1f} p75={qs[2]:.1f} p90={qs[3]:.1f} "
              f"max={wins_u[-1]:.1f}  -> TPs below p75 mostly cap real winners")


if __name__ == "__main__":
    main()
