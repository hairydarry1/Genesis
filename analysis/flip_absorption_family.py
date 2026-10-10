#!/usr/bin/env python3
"""Cross-family absorption asymmetry: is 'up-phases are more durable' general?

For each Flip index (matched 7.1d tick archive):
  1. absorption rates of counter-spikes by direction at H=50/200 ticks
  2. the asymmetry gap (dn absorbed - up absorbed; + means up-phases tougher)
  3. economic consequence: long-only vs short-only per-phase PnL net of that
     symbol's LIVE spread (flip150 0.17, flip300 2.88, flip500 1.02,
     flip1000 0.6261u), WR and maxDD per side.

Symbol configs from the original baseline study: flip150 @2.5, flip300 @3.0,
flip500 @3.5, flip1000 @5.0 (frozen medians, same detector everywhere).

Usage: python analysis/flip_absorption_family.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_phase_backtest import get_specs

SYMS = [("flip150", 2.5, 0.17), ("flip300", 3.0, 2.88),
        ("flip500", 3.5, 1.02), ("flip1000", 5.0, 0.6261)]


def vpu_01(sym):
    sp = get_specs()[sym]
    return sp["tick_value"] / sp["tick_size"] * 0.1


def absorption(ms, px, med, mult, H):
    d = np.diff(px)
    big = np.where(np.abs(d) > mult * med)[0]
    big = big[big >= WARMUP]
    bad = bad_tick_mask(ms)                    # gap-masking
    big = big[~bad[np.minimum(big + 1, len(px) - 1)]]
    holes = hole_intervals(ms)
    dirs = np.sign(d[big]).astype(int)
    res = {1: [0, 0], -1: [0, 0]}          # dir -> [absorbed, total]
    phase_start = 0
    for k in range(1, len(dirs)):
        if dirs[k] != dirs[k - 1]:
            phase_start = k
        if k == phase_start:
            continue                        # same-direction move: no claim
        i = big[k]
        j = min(i + 1 + H, len(px) - 1)
        if crosses_hole(holes, ms[i + 1], ms[j]):
            continue                    # measurement window spans a hole
        drift = px[j] - px[i + 1]
        s = dirs[k]
        res[s][1] += 1
        if s * drift < 0:
            res[s][0] += 1
    up_abs = res[1][0] / res[1][1] * 100 if res[1][1] else float("nan")
    dn_abs = res[-1][0] / res[-1][1] * 100 if res[-1][1] else float("nan")
    return up_abs, dn_abs, res[1][1], res[-1][1]


def main():
    print(f"{'sym':9s} {'mult':>4s} | {'H':>4s} {'upAbs%':>7s} {'dnAbs%':>7s} "
          f"{'gap':>6s} {'n_up':>5s} {'n_dn':>5s} | {'long $/wk':>10s} "
          f"{'WR%':>5s} {'maxDD':>6s} | {'short $/wk':>10s} {'WR%':>5s} {'maxDD':>6s}")
    for sym, mult, spread in SYMS:
        ms, px = load(sym)
        med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
        dur_wk = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7
        vpu = vpu_01(sym)

        # absorption
        for H in (50, 200):
            up_abs, dn_abs, nu, ndn = absorption(ms, px, med, mult, H)
            gap = dn_abs - up_abs
            print(f"{sym:9s} {mult:4.1f} | {H:4d} {up_abs:7.1f} {dn_abs:7.1f} "
                  f"{gap:+6.1f} {nu:5d} {ndn:5d}", end="")

        # economic: one-sided phase-follow per side
        d = np.diff(px)
        big_idx = np.where(np.abs(d) > mult * med)[0]
        big_idx = big_idx[big_idx >= WARMUP]
        bad = bad_tick_mask(ms)                # gap-masking
        big_idx = big_idx[~bad[np.minimum(big_idx + 1, len(px) - 1)]]
        holes = hole_intervals(ms)
        dirs = np.sign(d[big_idx]).astype(int)
        out = {}
        for side in (1, -1):
            pos, entry = 0, 0.0
            trades = []
            last_t = None
            for k, i in enumerate(big_idx):
                t_fill = ms[i + 1] if i + 1 < len(ms) else ms[-1]
                if last_t is not None and crosses_hole(holes, last_t, t_fill):
                    pos = 0            # position would have spanned a hole
                last_t = t_fill
                fill = px[i + 1]
                if pos == 0:
                    if dirs[k] == side:
                        pos, entry = dirs[k], fill
                elif pos != dirs[k]:
                    trades.append((pos * (fill - entry) - spread) * vpu)
                    pos = 0
                    if dirs[k] == side:
                        pos, entry = dirs[k], fill
            if pos:
                trades.append((pos * (px[-1] - entry) - spread) * vpu)
            arr = np.array(trades)
            cum = np.cumsum(arr)
            out[side] = (arr.sum() / dur_wk if len(arr) else 0.0,
                         (arr > 0).mean() * 100 if len(arr) else float("nan"),
                         float(np.max(np.maximum.accumulate(cum) - cum)) if len(cum) else 0.0,
                         len(arr))
        lo, sh = out[1], out[-1]
        print(f" | {lo[0]:+10.2f} {lo[1]:5.1f} {lo[2]:6.2f} | "
              f"{sh[0]:+10.2f} {sh[1]:5.1f} {sh[2]:6.2f}")


if __name__ == "__main__":
    main()
