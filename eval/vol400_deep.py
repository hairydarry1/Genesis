#!/usr/bin/env python3
"""
VOL400 DEEP DIVE
================
1. Discharge clustering: after a big bar, does the next big bar follow in the
   same direction (persistence) or alternate? Both matter for fade/chase.
2. Threshold-swept fade EV (short after up-discharge) with OOS halves.
3. Executable vol-regime predictor: does recent big-move density forecast
   the next bar's range? (The M1-bar version of the decaying tick hazard.)
"""
import sys
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
import partial_m1_corrected as eng

FLOOR = 10.0
DFILE = Path = None
from pathlib import Path
DFILE = Path("../data/vol400_Spot_Up__Volatility_Up_Index_1min.parquet")


def load():
    df = pd.read_parquet(DFILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def direction_clustering(df, thr_mult):
    rng = df["range"].values; med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0)
    big = np.where((rng > thr_mult * med) & ok)[0]
    dirs = np.where(up[big], 1, -1)
    print(f"\n=== 1. DISCHARGE CLUSTERING (>{thr_mult}x median, n={len(big)}) ===")
    same = (dirs[1:] == dirs[:-1]).mean()
    print(f"  P(next big bar SAME direction) = {same:.3f}")
    print(f"  P(alternates)                   = {1 - same:.3f}")
    gaps = np.diff(big)
    print(f"  gap between big bars: median={np.median(gaps):.0f}  p25={np.percentile(gaps,25):.0f}  "
          f"p75={np.percentile(gaps,75):.0f}")
    # does persistence change with gap? same-direction rate by gap bucket
    for a, b in ((1, 2), (2, 5), (5, 15), (15, 60), (60, 10_000)):
        m = (gaps >= a) & (gaps < b)
        if m.sum() >= 10:
            print(f"  gap {a:2d}-{b:2d}: n={m.sum():4d}  P(same)={np.mean(dirs[1:][m] == dirs[:-1][m]):.3f}")


def fade_sweep(df, thr_mult):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    spike_idx = np.where((rng > thr_mult * med) & np.isfinite(med) & (rng > 0.2) & adv)[0]
    mid = n // 2
    print(f"\n=== 2. FADE (SHORT after up-discharge, >{thr_mult}x median) ===")
    for entry_off, hold in ((1, 1), (1, 2), (2, 1), (2, 2)):
        for half, (lo, hi) in (("ALL", (0, n)), ("OOS1", (0, mid)), ("OOS2", (mid, n))):
            pnls = []
            skip = 0
            for k, i in enumerate(spike_idx):
                if not (lo <= i < hi):
                    continue
                if skip > 0:
                    skip -= 1
                    continue
                if k >= 2 and (i - spike_idx[k - 2]) <= 10:
                    continue
                e = i + entry_off
                if e + 2 >= n:
                    continue
                entry = closes[e]
                sl_dist = max(med[i], FLOOR)
                p, r = eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                     entry, entry + sl_dist, entry - 2 * sl_dist,
                                     e + 1, hold, n)
                pnls.append(entry - p)
            pnls = np.array(pnls)
            if len(pnls) < 5:
                print(f"  e+{entry_off} h={hold} {half}: n={len(pnls)}")
                continue
            wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
            pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() else float("inf")
            print(f"  e+{entry_off} h={hold} {half}: n={len(pnls):4d} WR={100*(pnls>0).mean():5.1f}% "
                  f"PF={pf:5.2f} EV={pnls.mean():+.3f} worst={pnls.min():+.1f}")


def regime_predictor(df):
    """Does recent big-move density forecast the NEXT bar's range?
    Executable version of the decaying tick hazard at M1 scale."""
    rng = df["range"].values; med = df["median_range"].values
    ok = np.isfinite(med) & (med > 0)
    big = (rng > 2.0 * med) & ok
    # predictor: count of big bars in trailing 10 bars (each must be known by close)
    counts = pd.Series(big).rolling(10, min_periods=1).sum().shift(1).values
    fut = np.full(len(rng), np.nan)
    fut[:-1] = rng[1:]
    print(f"\n=== 3. VOL-REGIME PREDICTOR (big bars in last 10 -> next bar range) ===")
    for c in (0, 1, 2, 3, 4):
        m = (counts == c) & ok & np.isfinite(fut)
        if m.sum() >= 100:
            print(f"  {c} big bars in last 10: n={m.sum():6d}  "
                  f"next-range median={np.median(fut[m]):6.2f}  mean={np.nanmean(fut[m]):6.2f}")
    # next-bar P(big | recent density)
    futbig = np.full(len(rng), np.nan); futbig[:-1] = big[1:]
    for c in (0, 1, 2, 3, 4):
        m = (counts == c) & ok & np.isfinite(futbig)
        if m.sum() >= 100:
            base = (futbig[m]).mean()
            print(f"  {c} big bars in last 10: P(next bar big)={base:.3f} "
                  f"({base/(big.mean()):.2f}x base)")


if __name__ == "__main__":
    df = load()
    for thr in (2.0, 2.5, 3.0):
        direction_clustering(df, thr)
    for thr in (2.0, 2.5, 3.0):
        fade_sweep(df, thr)
    regime_predictor(df)