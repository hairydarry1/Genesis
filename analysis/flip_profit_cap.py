#!/usr/bin/env python3
"""Profit-cap study: the user's "$3-5 open profit should be banked" rule.

Three parts, all on the healed gap-masked archive, fleet conventions
(frozen median, 5x detector, min_age=1, live spread/episode, $0.10/unit):

  1. CASE RECONSTRUCTION - the last live trade (long @9557.64 Sep 14,
     closed +$0.54 on a counter-spike): track its open profit path, find
     the peak and what the next spike took. The motivating example.
  2. GIVE-BACK CENSUS - for every winner in the archive: peak open profit
     vs realized, distribution of give-back fraction. How often does the
     next spike cut a >=$3 peak by >70%? (The user's claim, quantified.)
  3. TP LADDER - hard take-profit at $X open profit (checked every tick;
     exits at market on the tick it first touches). X in $0.5..$6.5.
     Verdict metric: net/wk vs incumbent, plus maxDD and tail.

Usage: python analysis/flip_profit_cap.py
"""
import sys

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


def run(ms, px, med, side=0, tp_usd=None):
    """Incumbent counter-spike exit, optional hard TP in open-profit dollars.
    Returns (nets, peak_frac_of_winners, close_ms)."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    peak = 0.0
    nets = []
    peaks = []          # peak open profit of each winning trip
    cms = []

    def close(fill, j):
        nonlocal pos, peak
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        if nets[-1] > 0:
            peaks.append(peak)
        cms.append(ms[j])
        pos, peak = 0.0, 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, peak = 0.0, 0, 0, 0.0
            continue
        if pos != 0:
            open_pnl = (px[j] - entry) * pos * VPU
            peak = max(peak, open_pnl)
            if tp_usd is not None and open_pnl >= tp_usd:
                close(px[j], j)
                continue
        is_spike = bool(big[j - 1])
        if is_spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                close(px[j], j)
                continue
        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and is_spike:
                pos, entry = STEP_LOTS * cur_dir, px[j]
                peak = 0.0
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(peaks), np.array(cms)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = (peak - eq).max() if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:24s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    big = np.abs(d) > MULT * med
    big = np.abs(d) > MULT * med
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f}\n")

    # ---- 1. case reconstruction: live short @9550.24410 (Sep 14
    #          13:34->14:20Z, closed +$0.54) ----
    print("=== 1. case: live short @9550.24410 (Sep 14 13:34->14:20Z, +$0.54) ===")
    entry_px = 9550.24410
    sgn = -1                       # short
    from datetime import datetime, timezone
    t0 = datetime(2026, 9, 14, 13, 34, 23, tzinfo=timezone.utc)
    t1 = datetime(2026, 9, 14, 14, 20, 55, tzinfo=timezone.utc)
    i0 = int(np.searchsorted(ms, t0.timestamp() * 1000))
    i1 = int(np.searchsorted(ms, t1.timestamp() * 1000))
    i0, i1 = max(i0, WARMUP), min(i1, len(px) - 1)
    seg_open = sgn * (px[i0:i1 + 1] - entry_px) * PER_UNIT
    realized = (entry_px - px[i1]) * PER_UNIT
    pk = seg_open.max()
    pk_i = int(np.argmax(seg_open))
    t_pk = datetime.fromtimestamp(ms[i0 + pk_i] / 1000, tz=timezone.utc)
    print(f"  hold {i1 - i0} ticks ({(ms[i1]-ms[i0])/60000:.0f} min); "
          f"realized ${realized:+.2f}")
    print(f"  peak open profit during hold: ${pk:+.2f} at {t_pk:%H:%M:%SZ} "
          f"-> giveback ${(pk - realized):.2f} ({(pk - realized)/max(pk,1e-9)*100:.0f}% of peak)")
    # up-spikes (adverse for the short) during the hold
    up = [(k, sgn * (px[k] - entry_px) * PER_UNIT)
          for k in range(i0 + 1, i1 + 1) if big[k - 1] and d[k - 1] > 0]
    if up:
        print(f"  adverse (up-)spikes during hold: {len(up)}; "
              f"open profit right after each: "
              f"{[f'${p:+.2f}' for _, p in up[:8]]}")
    print()

    # ---- 2. give-back census on all archive winners (incumbent engine) ----
    print("=== 2. give-back census (incumbent winners, LS + L) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        nets, peaks, _ = run(ms, px, med, side=side)
        if not len(peaks):
            continue
        pk = np.array(peaks)
        gb = 1.0 - (nets[nets > 0] / np.maximum(pk, 1e-9))
        for thr_pct in (50, 70, 90):
            n_cut = int((gb >= thr_pct / 100).sum())
            n_ge3 = int((pk >= 3.0).sum())
            n_both = int(((pk >= 3.0) & (gb >= thr_pct / 100)).sum())
            print(f"  {sname}: winners={len(pk)}  giveback>= {thr_pct}%: "
                  f"{n_cut} ({n_cut/len(pk)*100:.0f}%)  |  peaks>=$3: {n_ge3}, "
                  f"of which cut>={thr_pct}%: {n_both}")
        p90 = np.quantile(pk, 0.9)
        print(f"  {sname}: peak open profit p50=${np.quantile(pk,0.5):.2f} "
              f"p90=${p90:.2f} max=${pk.max():.2f}; realized p50="
              f"${np.quantile(nets[nets>0],0.5):.2f}")
    print()

    # ---- 3. TP ladder ----
    print("=== 3. dollar TP ladder (open-profit cap) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        nets, _, _ = run(ms, px, med, side=side)
        stats(nets, days, f"{sname} no TP (incumbent)")
        for tp in (1.0, 2.0, 3.0, 4.0, 5.0, 6.5):
            nets, _, _ = run(ms, px, med, side=side, tp_usd=tp)
            stats(nets, days, f"{sname} TP ${tp:.1f}")
        print()


if __name__ == "__main__":
    main()
