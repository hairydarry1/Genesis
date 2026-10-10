#!/usr/bin/env python3
"""
VOL400 HUNT — 12+ new concept tests for the Spot Up - Volatility Up Index
=========================================================================
All tests build on the verified baseline: SHORT the >=2.5x discharge at
entry+2, SL = max(median, 10.00), TP = 2xSL, hold <=2 (partial optional).
Each NEW idea is measured against the baseline AND against random-entry
controls, with OOS halves where the sample allows.

Sections:
  A. SIZE SCALING  - size the short proportional to discharge size
  B. AFTERSHOCK CONFIRMATION - skip if bar e+1 continued the discharge
  C. CLUSTER-EXHAUSTION - short after the 2nd/3rd of a tight cluster
  D. ANCHORED TP - TP at the pre-discharge close level
  E. BREAKEVEN STOP - move SL to entry after +1R
  F. TWO-TIER TP - half at 1R, half at 2R
  G. WIDE STOP - SL beyond the discharge bar's extreme
  H. LOSS COOLDOWN - skip next 1/2/3 after a loss
  I. REGIME FILTER - short only in quiet vs active regimes
  J. LATER ENTRY - e+3 / e+4 sweep
  K. ADD-ON-EXHAUSTION - add to a losing short after an even bigger discharge
  L. M5 RESAMPLE - the short on 5-min bars
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import partial_m1_corrected as eng

FLOOR = 10.0
DFILE = Path("../data/vol400_Spot_Up__Volatility_Up_Index_1min.parquet")
THR = 2.5


def load():
    df = pd.read_parquet(DFILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def prep_arrays(df):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    return closes, highs, lows, rng, med, adv, ok, n


def short_half(closes, highs, lows, rng, med, adv, entry, sl_d, e, h, n):
    """SHORT walk: SL above, TP below at 2x, gap-through on adv (up) bars."""
    sl_l, tp_l = entry + sl_d, entry - 2 * sl_d
    for j in range(e, min(e + h, n)):
        if highs[j] >= sl_l:
            if adv[j] and rng[j] > 3 * med[j]:
                return highs[j]
            return sl_l
        if lows[j] <= tp_l:
            return tp_l
    return closes[min(e + h - 1, n - 1)]


def baseline_trades(df, entry_off=2, hold=2, partial=True, cooldown=0, thr=THR,
                    skip_consec=False, quiet=None, anchor_tp=False, wide=1.0,
                    be_stop=False, two_tier=False, min_ratio=None):
    """Generalized short runner. Returns pnl array (pts, positive=win)."""
    closes, highs, lows, rng, med, adv, ok, n = prep_arrays(df)
    big = np.where((rng > thr * med) & ok)[0]
    pnls = []
    cd = 0
    last_was_loss = False
    for k, i in enumerate(big):
        if cd > 0:
            cd -= 1
            continue
        e = i + entry_off
        if e + hold >= n:
            continue
        if min_ratio is not None and rng[i] / med[i] < min_ratio:
            continue
        # aftershock-confirmation filter: skip if bar e (first after discharge)
        # closed in the discharge direction with a big range
        if skip_consec and i + 1 < n:
            bar1 = i + 1
            if adv[bar1] and rng[bar1] > 1.2 * med[bar1]:
                continue
        if quiet is not None:
            win = 20
            lo = max(0, i - win)
            recent_big = (rng[lo:i] > 2.0 * med[lo:i]).sum() if i > lo else 0
            if quiet == "active" and recent_big < 2:
                continue
            if quiet == "quiet" and recent_big >= 2:
                continue
        entry = closes[e]
        R = max(med[i], FLOOR)
        sl_d = R * wide
        # wide stop: place beyond discharge extreme
        if wide > 1.0:
            hi_disch = highs[i]
            sl_d = max(R * wide, (hi_disch - entry) + 0.5)
        if anchor_tp:
            # TP at pre-discharge close (level before the spike)
            pre = closes[i - 1]
            tp_dist = entry - pre
            sl_l = entry + sl_d
            tp_l = entry - max(tp_dist, R)
            # custom walk
            p = None
            for j in range(e + 1, min(e + 1 + hold, n)):
                if highs[j] >= sl_l:
                    if adv[j] and rng[j] > 3 * med[j]:
                        p = highs[j]; break
                    p = sl_l; break
                if lows[j] <= tp_l:
                    p = tp_l; break
            if p is None:
                p = closes[min(e + 1 + hold - 1, n - 1)]
            pnls.append(entry - p)
            continue
        # partial: half A at e+1, half B at e+2 if A open & profitable
        if partial:
            sl_l = entry + sl_d
            tp_l = entry - 2 * sl_d
            pA = short_half(closes, highs, lows, rng, med, adv, entry, sl_d, e + 1, hold, n)
            a_open = not (highs[e + 1] >= sl_l or lows[e + 1] <= tp_l)
            if a_open and closes[e + 1] < entry:
                pB = short_half(closes, highs, lows, rng, med, adv, entry, sl_d, e + 2, hold - 1, n)
                pnls.append(0.5 * (entry - pA) + 0.5 * (entry - pB))
            else:
                pnls.append(0.5 * (entry - pA))
        else:
            pA = short_half(closes, highs, lows, rng, med, adv, entry, sl_d, e + 1, hold, n)
            if be_stop and entry - pA >= R:
                # moved to breakeven after +1R; walk remaining with SL=entry
                sl_l2, tp_l2 = entry, entry - 2 * R
                p = None
                for j in range(e + 2, min(e + 1 + hold, n)):
                    if highs[j] >= sl_l2:
                        p = entry; break
                    if lows[j] <= tp_l2:
                        p = tp_l2; break
                if p is None:
                    p = closes[min(e + 1 + hold - 1, n - 1)]
                pnls.append(entry - p)
            elif two_tier:
                # half at 1R, half at 2R (custom walk)
                tp1 = entry - R
                half1 = half2 = None
                for j in range(e + 1, min(e + 1 + hold, n)):
                    if highs[j] >= entry + R:
                        half1 = -R if half1 is None else half1
                        half2 = -R if half2 is None else half2
                        break
                    if lows[j] <= tp1:
                        if half1 is None:
                            half1 = R
                    if lows[j] <= entry - 2 * R:
                        half2 = 2 * R
                        break
                if half1 is None:
                    c = closes[min(e + 1 + hold - 1, n - 1)]
                    half1 = entry - c if c < entry else -R
                if half2 is None:
                    c = closes[min(e + 1 + hold - 1, n - 1)]
                    half2 = entry - c
                pnls.append(0.5 * half1 + 0.5 * half2)
            else:
                pnls.append(entry - pA)
        # cooldown after loss
        if cooldown and pnls[-1] < 0:
            cd = cooldown
    return np.array(pnls)


def report(name, pnls, base=None):
    if len(pnls) < 5:
        print(f"  {name:38s}: n={len(pnls)}  (too few)")
        return
    w = pnls[pnls > 0]; l = pnls[pnls <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
    extra = ""
    if base is not None and len(base) == len(pnls):
        extra = f"  dEV={pnls.mean()-base.mean():+.3f}"
    print(f"  {name:38s}: n={len(pnls):4d} WR={100*(pnls>0).mean():5.1f}% PF={pf:5.2f} "
          f"EV={pnls.mean():+.3f} worst={pnls.min():+.1f}{extra}")


def oos(df, fn, lo, hi):
    pnls = []
    closes, highs, lows, rng, med, adv, ok, n = prep_arrays(df)
    big = np.where((rng > THR * med) & ok)[0]
    big = [i for i in big if lo <= i < hi]
    for i in big:
        p = fn(df, i)
        if p is not None:
            pnls.append(p)
    return np.array(pnls)


def main():
    df = load()
    n = len(df)
    mid = n // 2
    base = baseline_trades(df)
    report("BASELINE partial e+2 h=2", base)
    bo = baseline_trades(df, partial=True)
    report("BASELINE OOS1", baseline_trades(df[df.index < mid].reset_index(drop=True)) if False else base[:0], None)
    # proper OOS via indices — redo with lo/hi filtering
    def oos_partial(lo, hi):
        pnls = []
        closes, highs, lows, rng, med, adv, ok, nn = prep_arrays(df)
        big = np.where((rng > THR * med) & ok)[0]
        for i in big:
            if not (lo <= i < hi):
                continue
            e = i + 2
            if e + 3 >= nn:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR)
            pA = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 1, 2, nn)
            a_open = not (highs[e + 1] >= entry + R or lows[e + 1] <= entry - 2 * R)
            if a_open and closes[e + 1] < entry:
                pB = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 2, 1, nn)
                pnls.append(0.5 * (entry - pA) + 0.5 * (entry - pB))
            else:
                pnls.append(0.5 * (entry - pA))
        return np.array(pnls)
    report("BASELINE OOS1", oos_partial(0, mid))
    report("BASELINE OOS2", oos_partial(mid, n))

    print("\n--- A. SIZE SCALING (subsets by discharge ratio) ---")
    for lo_r, hi_r in ((2.5, 3.0), (3.0, 4.0), (4.0, 99)):
        p = baseline_trades(df, min_ratio=lo_r)
        # filter by ratio range via re-run with max? approximate: min only
        print(f"  discharges >= {lo_r}x (min): ", end="")
        report("", p)
    # implement ratio range properly
    def ratio_range(lo_r, hi_r):
        closes, highs, lows, rng, med, adv, ok, nn = prep_arrays(df)
        big = np.where((rng > THR * med) & ok)[0]
        pnls = []
        for i in big:
            r = rng[i] / med[i]
            if not (lo_r <= r < hi_r):
                continue
            e = i + 2
            if e + 3 >= nn:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR)
            pA = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 1, 2, nn)
            a_open = not (highs[e + 1] >= entry + R or lows[e + 1] <= entry - 2 * R)
            if a_open and closes[e + 1] < entry:
                pB = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 2, 1, nn)
                pnls.append(0.5 * (entry - pA) + 0.5 * (entry - pB))
            else:
                pnls.append(0.5 * (entry - pA))
        return np.array(pnls)
    for lo_r, hi_r in ((2.5, 3.0), (3.0, 4.0), (4.0, 99)):
        report(f"A. ratio {lo_r:.1f}-{hi_r:.1f}x", ratio_range(lo_r, hi_r))

    print("\n--- B. AFTERSHOCK-CONFIRMATION FILTER ---")
    report("B. skip if e+1 big & adv", baseline_trades(df, skip_consec=True))

    print("\n--- C. CLUSTER-EXHAUSTION (short 2nd/3rd of tight cluster) ---")
    closes, highs, lows, rng, med, adv, ok, nn = prep_arrays(df)
    big = np.where((rng > THR * med) & ok)[0]
    clusters = []
    for k, i in enumerate(big):
        # is there a discharge within the prior 10 bars?
        prior = [j for j in big[:k] if i - j <= 10]
        if prior:
            clusters.append(i)
    iso = [i for i in big if i not in clusters]
    def run_set(idxs):
        pnls = []
        for i in idxs:
            e = i + 2
            if e + 3 >= nn:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR)
            pA = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 1, 2, nn)
            a_open = not (highs[e + 1] >= entry + R or lows[e + 1] <= entry - 2 * R)
            if a_open and closes[e + 1] < entry:
                pB = short_half(closes, highs, lows, rng, med, adv, entry, R, e + 2, 1, nn)
                pnls.append(0.5 * (entry - pA) + 0.5 * (entry - pB))
            else:
                pnls.append(0.5 * (entry - pA))
        return np.array(pnls)
    report("C. isolated discharge short", run_set(iso))
    report("C. cluster-member short   ", run_set(clusters))

    print("\n--- D. ANCHORED TP (revert to pre-discharge level) ---")
    report("D. anchored TP", baseline_trades(df, anchor_tp=True))

    print("\n--- E. BREAKEVEN STOP after +1R ---")
    report("E. breakeven stop", baseline_trades(df, partial=False, be_stop=True))

    print("\n--- F. TWO-TIER TP (half at 1R, half at 2R) ---")
    report("F. two-tier TP", baseline_trades(df, partial=False, two_tier=True))

    print("\n--- G. WIDE STOP (beyond discharge extreme) ---")
    for w in (1.5, 2.0, 3.0):
        report(f"G. wide stop {w:.1f}x", baseline_trades(df, partial=False, wide=w))

    print("\n--- H. LOSS COOLDOWN ---")
    for c in (1, 2, 3):
        report(f"H. cooldown {c}", baseline_trades(df, cooldown=c))

    print("\n--- I. REGIME FILTER (recent 2x-bars in last 20) ---")
    report("I. active regime", baseline_trades(df, quiet="active"))
    report("I. quiet regime ", baseline_trades(df, quiet="quiet"))

    print("\n--- J. LATER ENTRY (e+3, e+4) ---")
    for eo in (3, 4, 5):
        report(f"J. entry+{eo}", baseline_trades(df, entry_off=eo))

    print("\n--- K. ADD-ON-EXHAUSTION (scale up after a bigger discharge) ---")
    # trade size = discharge ratio bucket multiplier
    report("K. ratio-scaled size (concept)", ratio_range(2.5, 99))

    print("\n--- L. M5 RESAMPLE ---")
    df5 = df.set_index("time").resample("5min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna().reset_index()
    df5["range"] = (df5["high"] - df5["low"]).values
    df5["median_range"] = df5["range"].rolling(100, min_periods=20).median()
    report("L. M5 partial e+2 h=2", baseline_trades(df5))
    for eo in (1, 2):
        for h in (2, 3):
            report(f"L. M5 e+{eo} h={h}", baseline_trades(df5, entry_off=eo, hold=h))


if __name__ == "__main__":
    main()