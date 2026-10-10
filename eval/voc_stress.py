#!/usr/bin/env python3
"""
eval/voc_stress.py — Is the Vol over wide-SL edge real, or just the
+83% uptrend of the 60-day sample?

Tests on the surviving config (voc400 SL=5x, partial, cluster+cd3, and
voc550 SL=2x/3x):
  1) TIME-THIRDS: run each third of the sample separately. A real edge
     should be positive in all three, not just the middle.
  2) INVERTED-SERIES: mirror the candles (price' = -price). If the engine
     is LONG because of structure, mirroring flips it negative. If it is
     LONG only because the sample went up 83%, the mirrored LONG still
     makes money (drift artifact) - that's the tell.
  3) DOWN-DRIFT SYNTHETIC: subtract the linear trend from the series so
     the product goes nowhere / down, re-run. If the edge is structural
     (fade crashes), it survives trend removal. If it was the drift, it
     dies.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"


def load(key):
    slug = {"voc400": "Vol_over_Crash_400", "voc550": "Vol_over_Crash_550",
            "vob400": "Vol_over_Boom_400", "vob550": "Vol_over_Boom_550"}[key]
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def run_engine(df, is_crash, floor, sl_mult, hold=2, cooldown=3, th=3.0,
               cluster=True):
    """Returns pnl array in points (half-lot weighting)."""
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values
    adv = (c < o) if is_crash else (c > o)
    idx = np.where((rng_ > th * med) & adv & np.isfinite(med) & (rng_ > 0.2))[0]
    pnls = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        e = i + 1
        if e + 2 >= n:
            continue
        entry = c[e]
        sl_d = max(med[i], floor) * sl_mult
        sl_l = (entry - sl_d) if is_crash else (entry + sl_d)
        tp_l = (entry + 2 * sl_d) if is_crash else (entry - 2 * sl_d)

        def half(s, mb):
            for j in range(s, min(s + mb, n)):
                if is_crash:
                    hs, ht = l[j] <= sl_l, h[j] >= tp_l
                else:
                    hs, ht = h[j] >= sl_l, l[j] <= tp_l
                if hs:
                    if adv[j] and rng_[j] > 3 * med[j]:
                        return (l[j] if is_crash else h[j])
                    return sl_l
                if ht:
                    return tp_l
            return c[min(s + mb - 1, n - 1)]

        pA = half(e + 1, hold)
        add_b = False
        pA1 = half(e + 1, 1)
        if pA1 != sl_l and pA1 != tp_l:
            add_b = (c[e + 1] > entry) if is_crash else (c[e + 1] < entry)
        if add_b:
            pB = half(e + 2, hold - 1)
            pnl = 0.5 * (pA + pB - 2 * entry) * (1 if is_crash else -1)
        else:
            pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
        pnls.append(pnl)
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    return np.array(pnls)


def summ(pnls, label):
    if len(pnls) == 0:
        print(f"    {label:<38} n=0")
        return
    w = pnls[pnls > 0]; l_ = pnls[pnls <= 0]
    pf = w.sum() / abs(l_.sum()) if len(l_) else 999
    print(f"    {label:<38} n={len(pnls):>5}  avg {pnls.mean():>+7.3f} pts"
          f"  WR {100*len(w)/len(pnls):>3.0f}%  PF {pf:>5.2f}"
          f"  sum {pnls.sum():>+8.1f}")


def invert(df):
    """Mirror price series: p' = -p. Keeps range/median identical."""
    d = df.copy()
    for col in ("open", "high", "low", "close"):
        d[col] = -d[col].values
    # swap high/low after negation so high >= low
    h = d[["high", "low"]].max(axis=1)
    l = d[["high", "low"]].min(axis=1)
    d["high"] = h.values
    d["low"] = l.values
    return d


def detrend(df):
    """Remove the linear drift: close' = close - (a*t+b) with a fit to close,
    others shifted by same amount, floor at positive min."""
    d = df.copy()
    n = len(d)
    t = np.arange(n)
    a, b = np.polyfit(t, d["close"].values, 1)
    trend = a * t + b
    base = d[["open", "high", "low", "close"]].values
    adj = base - trend[:, None]
    if adj.min() < 0:
        adj -= adj.min()  # keep positive prices
    d[["open", "high", "low", "close"]] = adj
    return d, a


def main():
    configs = [("voc400", True, 3.60, 5.0), ("voc550", True, 3.20, 2.0),
               ("vob400", False, 3.60, 5.0)]
    for key, is_crash, floor, sl_mult in configs:
        df = load(key)
        n = len(df)
        # trend slope of the sample
        t = np.arange(n)
        a, _ = np.polyfit(t, df["close"].values, 1)
        drift_per_day = a * 1440
        print(f"\n{'='*96}\n{key.upper()}  is_crash={is_crash}  "
              f"SL={sl_mult}x  sample drift {drift_per_day:+.1f} pts/day")
        full = run_engine(df, is_crash, floor, sl_mult)
        summ(full, "FULL sample")
        # 1) time-thirds
        third = n // 3
        for i, (nm, sl) in enumerate([("T1 (first)", slice(0, third)),
                                      ("T2 (middle)", slice(third, 2 * third)),
                                      ("T3 (last)", slice(2 * third, n))]):
            d = df.iloc[sl].reset_index(drop=True)
            summ(run_engine(d, is_crash, floor, sl_mult), nm)
        # 2) inverted series
        dinv = invert(df)
        summ(run_engine(dinv, not is_crash, floor, sl_mult),
             "INVERTED (structure-flip control)")
        # also run the SAME direction on inverted (pure drift control)
        summ(run_engine(dinv, is_crash, floor, sl_mult),
             "INVERTED same-dir (drift control)")
        # 3) detrended
        ddet, slope = detrend(df)
        summ(run_engine(ddet, is_crash, floor, sl_mult),
             "DETRENDED (no-drift control)")


if __name__ == "__main__":
    main()