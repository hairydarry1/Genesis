#!/usr/bin/env python3
"""
eval/voc_regime.py — Trade the OSCILLATOR, not the drift.

Vol over Crash 400 is a macro mean-reverter: grinds up ~75% of bar-windows
(median +2.3 pts/2 bars), violently crashes the rest, net zero over 59d,
max DD -45%. The boom/crash model (fade every discharge, ride the drift)
is wrong here. Hypotheses:

  H1 MACRO-LEG GATE: fade crashes only when the macro (12h/24h slope) is
     UP — the crash is a local dip in an ongoing grind. When the macro is
     DOWN, the crash is part of the down-leg and long-fading loses.
     => engine EV with gate on macro-leg.
  H2 LEG PERSISTENCE: after a big crash, does the macro continue DOWN
     (leg flip) or resume UP? Measure next-6h/24h from the crash bar.
  H3 CRASH-AVOIDANCE (grind capture): since 75% of 2-bar windows are
     +2.3-pt grinds and the losses come in crash bursts, a schedule-long
     with a time-since-last-crash entry gate and cd-after-loss may beat
     the spike book — the boom/crash base-rate result, applied to an
     oscillator where the "discharge windows" are the thing to skip.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"


def load(slug):
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def macro_slope(df, win):
    c = df["close"].values
    n = len(df)
    out = np.full(n, np.nan)
    out[win:] = c[win:] - c[:-win]
    return out


def engine(df, floor, sl_mult, hold=2, cooldown=3, th=3.0, gate_slope=None,
           gate_win=720, partial=True):
    """Long-fade crash bars; optional macro-leg gate (slope>0 required)."""
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values
    adv = c < o
    idx = np.where((rng_ > th * med) & adv & np.isfinite(med) & (rng_ > 0.2))[0]
    slope = macro_slope(df, gate_win) if (gate_slope and gate_win) else None
    pnls = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if slope is not None:
            if not np.isfinite(slope[i]) or slope[i] <= 0:
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
                    return l[j]
                if h[j] >= tp_l:
                    return tp_l
            return c[min(s + mb - 1, n - 1)]

        pA = half(e + 1, hold)
        add_b = False
        pA1 = half(e + 1, 1)
        if pA1 != sl_l and pA1 != tp_l:
            add_b = c[e + 1] > entry
        if add_b and partial:
            pB = half(e + 2, hold - 1)
            pnl = 0.5 * (pA + pB - 2 * entry)
        else:
            pnl = 0.5 * (pA - entry)
        pnls.append(pnl)
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
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
    df = load("Vol_over_Crash_400")
    floor = 3.60
    print("VOC400 wide-SL engine with MACRO-LEG gate (long-fade crash bars)")
    for sl_mult in (3.0, 5.0):
        for gate in (None, 360, 720, 1440):
            tag = ("no gate" if gate is None else f"12h-slope gate"
                   if gate == 720 else f"{gate//60}h gate")
            summ(engine(df, floor, sl_mult, gate_slope=gate is not None,
                        gate_win=gate), f"SL{sl_mult}x {tag}")

    # H2: leg persistence after big crashes
    print("\n[H2] macro path after big crash bars (>3x med)")
    c = df["close"].values; o = df["open"].values
    h = df["high"].values; l = df["low"].values
    med = df["median_range"].values
    big = (c < o) & ((h - l) > 3 * med) & np.isfinite(med)
    idx = np.where(big)[0]
    n = len(df)
    for future in (60, 360, 1440):  # 1h, 6h, 24h
        fut = idx[idx + future < n]
        if len(fut) < 20:
            continue
        mv = c[fut + future] - c[fut]
        # compare with baseline same-horizon moves from random bars
        rng = np.random.default_rng(3)
        base = rng.integers(150, n - future - 1, size=len(fut))
        bmv = c[base + future] - c[base]
        print(f"    next {future//60:>2}h after crash ({len(fut):>5}): "
              f"{mv.mean():>+8.1f} pts vs baseline {bmv.mean():>+8.1f}")

    # H3: crash-avoidance grind (long every warm bar, skip after crash)
    print("\n[H3] schedule-long with post-crash avoidance vs spike book")
    print("    (long every bar close when median warm; skip 'cd' bars "
          "after any close>open crash burst)")
    c = df["close"].values; o = df["open"].values
    n = len(df)
    for cd in (0, 10, 30, 60):
        pnls = []
        skip = 0
        for i in range(150, n - 3):
            if skip > 0:
                skip -= 1
                continue
            entry = c[i]
            sl_d = max(med[i], floor) * 3.0
            pnl = None
            for j in range(i + 1, min(i + 3, n)):
                if l[j] <= entry - sl_d:
                    pnl = -sl_d
                    break
                if h[j] >= entry + 2 * sl_d:
                    pnl = 2 * sl_d
                    break
            if pnl is None:
                pnl = c[min(i + 2, n - 1)] - entry
            pnls.append(pnl)
            # crash burst = down bar > 2x med this bar -> skip next cd
            if (c[i] < o[i]) and ((h[i] - l[i]) > 2 * med[i]):
                skip = cd
        summ(np.array(pnls), f"grind-long cd={cd} after crash-burst")


if __name__ == "__main__":
    main()