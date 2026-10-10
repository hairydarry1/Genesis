#!/usr/bin/env python3
"""
VOL400 STRESS TEST — symmetric 2.5x fade
========================================
The 2.5x-discharge fade (short after up-discharge, entry+2) was the only
OOS-consistent positive on the Vol Up index. Because up/down bars are
symmetric here, the clean form is a SYMMETRIC fade: short after a big UP
bar, long after a big DOWN bar. Stress it:
  - direction mirror (long after down should mirror short after up)
  - time-thirds stability
  - entry offset x hold sweep
  - cluster filter on/off
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import partial_m1_corrected as eng

FLOOR = 10.0
DFILE = Path("../data/vol400_Spot_Up__Volatility_Up_Index_1min.parquet")


def load():
    df = pd.read_parquet(DFILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def fade_sym(df, thr_mult, entry_off, hold, cluster, lo=0, hi=None):
    """Fade ANY discharge: short after big UP bar, long after big DOWN bar."""
    n = len(df)
    hi = n if hi is None else hi
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > thr_mult * med) & ok)[0]
    big = big[(big >= lo) & (big < hi)]
    pnls = []
    skipped = 0
    for k, i in enumerate(big):
        if cluster and k >= 2 and (i - big[k - 2]) <= 10:
            skipped += 1
            continue
        e = i + entry_off
        if e + 2 >= n:
            continue
        entry = closes[e]
        sl_dist = max(med[i], FLOOR)
        if up[i]:  # fade = SHORT
            sl_l, tp_l = entry + sl_dist, entry - 2 * sl_dist
            sign = -1
        else:      # fade = LONG
            sl_l, tp_l = entry - sl_dist, entry + 2 * sl_dist
            sign = +1
        p, r = eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_l, tp_l, e + 1, hold, n)
        pnls.append(sign * (p - entry))
    return np.array(pnls)


def report(name, pnls):
    if len(pnls) < 5:
        print(f"  {name}: n={len(pnls)}  (too few)")
        return
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() else float("inf")
    print(f"  {name}: n={len(pnls):4d} WR={100*(pnls>0).mean():5.1f}% PF={pf:5.2f} "
          f"EV={pnls.mean():+.3f} pts ({pnls.mean()/FLOOR:+.3f}R) worst={pnls.min():+.1f}")


def main():
    df = load()
    n = len(df)
    print(f"=== SYMMETRIC FADE, >2.5x median (n bars={n:,}) ===")
    for entry_off in (1, 2, 3):
        for hold in (1, 2, 3, 5):
            pnls = fade_sym(df, 2.5, entry_off, hold, cluster=True)
            report(f"e+{entry_off} h={hold} ALL", pnls)
            if entry_off == 2 and hold == 2:
                thirds = np.array_split(np.sort(pnls) if False else pnls, 3)
                for t, (lo, hi) in enumerate(((0, n//3), (n//3, 2*n//3), (2*n//3, n))):
                    report(f"  e+2 h=2 third{t+1}", fade_sym(df, 2.5, 2, 2, True, lo, hi))
    print()
    print("=== MIRROR CHECK (short-after-up vs long-after-down, e+2 h=2) ===")
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    for lbl, cond in (("after UP (short)", up), ("after DOWN (long)", ~up)):
        idxs = [i for i in big if cond[i]]
        pnls = []
        for i in idxs:
            e = i + 2
            if e + 2 >= n:
                continue
            entry = closes[e]
            sl_dist = max(med[i], FLOOR)
            if cond[i]:
                p, r = eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                     entry, entry + sl_dist, entry - 2 * sl_dist, e + 1, 2, n)
                pnls.append(entry - p)
            else:
                p, r = eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                     entry, entry - sl_dist, entry + 2 * sl_dist, e + 1, 2, n)
                pnls.append(p - entry)
        report(lbl, np.array(pnls))
    print()
    print("=== CLUSTER FILTER EFFECT (e+2 h=2) ===")
    report("cluster OFF", fade_sym(df, 2.5, 2, 2, cluster=False))
    report("cluster ON ", fade_sym(df, 2.5, 2, 2, cluster=True))
    print()
    print("=== COST SENSITIVITY (spread is 0 on this symbol, but test anyway) ===")
    base = fade_sym(df, 2.5, 2, 2, cluster=True)
    for c in (0.0, 0.5, 1.0, 2.0):
        report(f"cost {c} pts/trade", base - c)


if __name__ == "__main__":
    main()