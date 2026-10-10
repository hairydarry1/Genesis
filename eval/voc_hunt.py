#!/usr/bin/env python3
"""
eval/voc_hunt.py — Concept hunt on Vol over Crash 400 (the OSCILLATOR).

Established: 75% of 2-bar windows grind +2.3 pts (median), crashes erase
the mean; wide-stop partial long after >3x crashes = +0.6-0.8 pts/trade
PF ~1.4-1.5, survives all thirds/legs; 12h-up gate is a mild tilt.

New concepts to test, each on corrected engine (gap-through fills):
  A) BURST AVOIDANCE (time-since-last-crash gates) — the grind pays, the
     bursts are the cost. Skip entries within X bars after a big crash.
  B) CRASH-SIZE BANDS — reversion strength by crash magnitude.
  C) BAR-BASED LOSS COOLDOWN — cd=10/20/40 bars (the schedule finding).
  D) ENTRY DELAY after crash (e+1 vs e+2 vs e+3).
  E) GRIND-LONG with burst gates (harvest grind, dodge bursts) — with
     HONEST gap-through (the earlier +82k was an artifact).
  F) UP-BAR FADE (short after up-bars >2x) — the mirror side; 2 up-bars
     exist >3x but many at 2x.
  G) VOL-OF-VOL REGIME — split by rolling median quartile.
  H) REVERSAL TIMER — after a crash, when does the grind resume?
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
FLOOR = 3.60


def load():
    df = pd.read_parquet(DATA / "candles_Vol_over_Crash_400_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def slope_arr(df, win):
    c = df["close"].values
    n = len(df)
    out = np.full(n, np.nan)
    out[win:] = c[win:] - c[:-win]
    return out


def crash_idx(df, th=3.0):
    c = df["close"].values; o = df["open"].values
    h = df["high"].values; l = df["low"].values
    rng_ = h - l; med = df["median_range"].values
    return np.where((rng_ > th * med) & (c < o) & np.isfinite(med)
                    & (rng_ > 0.2))[0]


def engine(df, sl_mult=5.0, hold=2, cd=3, entry_lag=0, gate=None,
           gate_win=720, size_lo=3.0, size_hi=99.0, skip_after_crash=0,
           mask=None):
    """Long-fade crash bars, wide-stop partial. Returns pnl array."""
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values
    idx = crash_idx(df, 3.0)
    slope = slope_arr(df, gate_win) if gate else None
    pnls = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        # size band on the crash itself
        if not (size_lo <= rng_[i] / med[i] < size_hi):
            continue
        if mask is not None and not mask[i]:
            continue
        if slope is not None:
            if not np.isfinite(slope[i]):
                continue
            if gate == "up" and slope[i] <= 0:
                continue
            if gate == "down" and slope[i] >= 0:
                continue
        # burst avoidance: skip if a crash happened within X bars ago
        if skip_after_crash > 0 and k > 0 and (i - idx[k - 1]) <= skip_after_crash:
            continue
        e = i + entry_lag + 1
        if e + 2 >= n:
            continue
        entry = c[e]
        sl_d = max(med[i], FLOOR) * sl_mult
        sl_l = entry - sl_d
        tp_l = entry + 2 * sl_d

        def half(s, mb):
            for j in range(s, min(s + mb, n)):
                if l[j] <= sl_l:
                    if (c[j] < o[j]) and rng_[j] > 3 * med[j]:
                        return l[j]
                    return sl_l
                if h[j] >= tp_l:
                    return tp_l
            return c[min(s + mb - 1, n - 1)]

        pA = half(e + 1, hold)
        add_b = False
        pA1 = half(e + 1, 1)
        if pA1 != sl_l and pA1 != tp_l:
            add_b = c[e + 1] > entry
        if add_b:
            pB = half(e + 2, hold - 1)
            pnl = 0.5 * (pA + pB - 2 * entry)
        else:
            pnl = 0.5 * (pA - entry)
        pnls.append(pnl)
        if pnl <= 0 and cd > 0:
            skip = cd
    return np.array(pnls)


def summ(pnls, label):
    if len(pnls) == 0:
        print(f"    {label:<46} n=0")
        return
    w = pnls[pnls > 0]; l_ = pnls[pnls <= 0]
    pf = w.sum() / abs(l_.sum()) if len(l_) else 999
    print(f"    {label:<46} n={len(pnls):>5}  avg {pnls.mean():>+7.3f}"
          f"  WR {100*len(w)/len(pnls):>3.0f}%  PF {pf:>5.2f}"
          f"  worst {pnls.min():>+7.1f}")


def main():
    df = load()
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values

    print("=" * 100)
    print("A) BURST AVOIDANCE — skip entries within X bars after a crash")
    for sa in (0, 3, 5, 10, 20, 40):
        summ(engine(df, sl_mult=5.0, cd=3, skip_after_crash=sa),
             f"SL5 skip-after-crash={sa}")

    print("\nB) CRASH-SIZE BANDS (no gate, SL5)")
    for lo, hi in ((3, 5), (5, 8), (8, 12), (12, 99), (3, 99), (5, 99)):
        summ(engine(df, sl_mult=5.0, size_lo=lo, size_hi=hi),
             f"crash {lo:>2}-{hi:>2}x med")

    print("\nC) BAR-BASED LOSS COOLDOWN (SL5, skip-after=0)")
    for cd in (0, 5, 10, 20, 40):
        summ(engine(df, sl_mult=5.0, cd=cd), f"cd={cd} bars after loss")

    print("\nD) ENTRY DELAY (SL5, cd=3)")
    for el in (0, 1, 2, 3):
        summ(engine(df, sl_mult=5.0, cd=3, entry_lag=el),
             f"enter +{el + 1} bar after crash")

    print("\nE) GRIND-LONG with burst gates (HONEST gap-through)")
    # long every warm bar; SL = 3x med, TP=2x; skip cd bars after crash
    for cd in (0, 10, 30):
        pnls = []
        skip = 0
        for i in range(150, n - 3):
            if skip > 0:
                skip -= 1
                continue
            entry = c[i]
            sl_d = max(med[i], FLOOR) * 3.0
            sl_l = entry - sl_d
            tp_l = entry + 2 * sl_d
            pnl = None
            for j in range(i + 1, min(i + 3, n)):
                if l[j] <= sl_l:
                    pnl = (l[j] - entry) if (c[j] < o[j] and rng_[j] >
                                             3 * med[j]) else -sl_d
                    break
                if h[j] >= tp_l:
                    pnl = 2 * sl_d
                    break
            if pnl is None:
                pnl = c[min(i + 2, n - 1)] - entry
            pnls.append(pnl)
            # burst gate: skip cd bars after any >2x crash bar
            if (c[i] < o[i]) and rng_[i] > 2 * med[i]:
                skip = cd
        summ(np.array(pnls), f"grind-long cd={cd}")

    print("\nF) UP-BAR FADE — short after up-bars (mirror side)")
    # up-bars > 2x med (only ~1100 exist); short, SL above, TP below
    for th in (2.0, 2.5, 3.0):
        uidx = np.where((rng_ > th * med) & (c > o) & np.isfinite(med))[0]
        pnls = []
        skip = 0
        for k, i in enumerate(uidx):
            if skip > 0:
                skip -= 1
                continue
            e = i + 1
            if e + 2 >= n:
                continue
            entry = c[e]
            sl_d = max(med[i], FLOOR) * 2.0
            sl_l = entry + sl_d
            tp_l = entry - 2 * sl_d
            pnl = None
            for j in range(e + 1, min(e + 3, n)):
                if h[j] >= sl_l:
                    pnl = (h[j] - entry) if (c[j] > o[j] and rng_[j] >
                                             3 * med[j]) else -sl_d
                    break
                if l[j] <= tp_l:
                    pnl = 2 * sl_d
                    break
            if pnl is None:
                pnl = entry - c[min(e + 2, n - 1)]
            pnls.append(pnl)
            if pnl <= 0:
                skip = 3
        summ(np.array(pnls), f"short after up-bar >{th}x")

    print("\nG) VOL-OF-VOL REGIME — SL5 engine by rolling-med quartile")
    q = pd.qcut(pd.Series(med).rolling(500, min_periods=50).median(),
                4, labels=False)
    qv = q.values
    for qi in range(4):
        m = np.zeros(n, bool)
        m[qv == qi] = True
        summ(engine(df, sl_mult=5.0, cd=3, mask=m),
             f"low-vol quartile {qi}")

    print("\nH) REVERSAL TIMER — grind move after crash ends")
    idx = crash_idx(df, 3.0)
    for fut in (5, 10, 30, 60, 120):
        futx = idx[idx + fut < n]
        if len(futx) < 20:
            continue
        mv = c[futx + fut] - c[futx]
        rng2 = np.random.default_rng(5)
        base = rng2.integers(150, n - fut - 1, len(futx))
        bv = c[base + fut] - c[base]
        print(f"    next {fut:>3} bars after crash: {mv.mean():>+7.2f} pts "
              f"vs baseline {bv.mean():>+7.2f}  (n={len(futx)})")


if __name__ == "__main__":
    main()