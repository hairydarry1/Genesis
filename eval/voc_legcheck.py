#!/usr/bin/env python3
"""
eval/voc_legcheck.py — Does the 12h-gated crash-fade survive a DOWN-leg?
(CLEAN version — masks actually passed.)

Sample = ~1.5 macro cycles (up +64%, then down -39%). Questions:
  A) chronological thirds — is the edge present in every third?
  B) macro-leg split at entry — UP-leg vs DOWN-leg entries, gated config.
  C) gate-window sweep per leg — is "12h" stable or a knife-edge?
  D) the actual claim test: gated vs ungated, and is any gate gain real
     or subset noise?
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"


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


def engine(df, floor, sl_mult, gate=None, gate_win=720, mask=None):
    """Long-fade crash bars >3x med. gate: None|'up'|'down' requires
    macro slope sign at gate_win. mask: optional bool array over bars."""
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values
    slope = slope_arr(df, gate_win) if gate else None
    idx = np.where((rng_ > 3 * med) & (c < o) & np.isfinite(med)
                   & (rng_ > 0.2))[0]
    pnls = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
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
        if k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        e = i + 1
        if e + 2 >= n:
            continue
        entry = c[e]
        sl_d = max(med[i], floor) * sl_mult
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

        pA = half(e + 1, 2)
        add_b = False
        pA1 = half(e + 1, 1)
        if pA1 != sl_l and pA1 != tp_l:
            add_b = c[e + 1] > entry
        if add_b:
            pB = half(e + 2, 1)
            pnl = 0.5 * (pA + pB - 2 * entry)
        else:
            pnl = 0.5 * (pA - entry)
        pnls.append(pnl)
        if pnl <= 0:
            skip = 3
    return np.array(pnls)


def summ(pnls, label):
    if len(pnls) == 0:
        print(f"    {label:<44} n=0")
        return
    w = pnls[pnls > 0]; l_ = pnls[pnls <= 0]
    pf = w.sum() / abs(l_.sum()) if len(l_) else 999
    print(f"    {label:<44} n={len(pnls):>5}  avg {pnls.mean():>+7.3f}"
          f"  WR {100*len(w)/len(pnls):>3.0f}%  PF {pf:>5.2f}"
          f"  worst {pnls.min():>+7.1f}  sum {pnls.sum():>+8.1f}")


def main():
    df = load()
    n = len(df)
    floor = 3.60
    s12 = slope_arr(df, 720)
    up12 = s12 > 0
    dn12 = s12 < 0
    print(f"bars {n}; macro 12h up {100*np.nanmean(up12):.0f}% "
          f"down {100*np.nanmean(dn12):.0f}%")

    print("\n[A] chronological thirds — gated (12h up) config, SL5x")
    third = n // 3
    for nm, sl in (("T1 (days 0-20)", slice(0, third)),
                   ("T2 (days 20-40)", slice(third, 2 * third)),
                   ("T3 (days 40-59)", slice(2 * third, n))):
        m = np.zeros(n, bool); m[sl] = True
        summ(engine(df, floor, 5.0, gate="up", gate_win=720, mask=m), nm)

    print("\n[A2] chronological thirds — UNGATED, SL5x")
    for nm, sl in (("T1", slice(0, third)), ("T2", slice(third, 2 * third)),
                   ("T3", slice(2 * third, n))):
        m = np.zeros(n, bool); m[sl] = True
        summ(engine(df, floor, 5.0, gate=None, mask=m), nm)

    print("\n[B] macro-leg at entry — gated config per leg (SL5x)")
    summ(engine(df, floor, 5.0, gate="up", gate_win=720), "UP-leg entries")
    summ(engine(df, floor, 5.0, gate="down", gate_win=720),
         "DOWN-leg entries")
    summ(engine(df, floor, 5.0, gate=None), "ungated (both legs)")

    print("\n[C] gate-window sweep — 'up'-gated avg per window (SL5x)")
    for gw in (180, 360, 720, 1440, 2880):
        summ(engine(df, floor, 5.0, gate="up", gate_win=gw),
             f"gate-up gw={gw//60:>3}h")

    print("\n[C2] 'down'-gated for contrast (SL5x)")
    for gw in (360, 720, 1440):
        summ(engine(df, floor, 5.0, gate="down", gate_win=gw),
             f"gate-down gw={gw//60:>3}h")

    print("\n[D] sanity — SL3x and SL8x with 12h-up gate")
    for sm in (3.0, 8.0):
        summ(engine(df, floor, sm, gate="up", gate_win=720), f"SL{sm:.0f}x")
        summ(engine(df, floor, sm, gate=None), f"SL{sm:.0f}x ungated")


if __name__ == "__main__":
    main()