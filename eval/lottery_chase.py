#!/usr/bin/env python3
"""
LOTTERY CHASE — lose small (1R), win big (K*R), catch a few spikes
==================================================================
User's vision: on the spike side, hold a position with a SMALL controlled
loss (SL = 1R at the broker floor) and a WIDE TP (K*R), so a few caught
spikes offset many small stops. E.g. lose $1, gain $8.

The deciding math: EV = P(reach K*R) * K - P(stopped by drift) * 1
(in R units). P(reach K*R) must decay slower than K grows. This sweeps
K up to 15R across entry points in the drift to find whether ANY
(late-entry, wide-TP) cell is positive — and if not, shows why.

crash -> SHORT the spike direction; boom -> LONG. No gate (clusters are
friendly for a lottery). Wait up to H bars for the TP; SL is 1R.
Gap-through fills on adverse spike bars (engine rule) — for the chase,
adverse = opposite-direction (size-capped) bars, so SL fills at level.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E


def lottery_trade(df, sym, i, D, K, H=180):
    """crash: SHORT at close[i+D], SL +1R above, TP K*R below.
    boom:  LONG, SL -1R below, TP K*R above. Wait H bars max."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + D
    if e + 1 >= n:
        return None
    entry = closes[e]
    R = max(med[i], E.BROKER_FLOOR.get(sym, 1.0))
    if is_crash:  # SHORT
        sl_level = entry + R
        tp_level = entry - K * R
    else:         # LONG
        sl_level = entry - R
        tp_level = entry + K * R
    for j in range(e + 1, min(e + 1 + H, n)):
        if is_crash:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        else:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        if hit_sl:
            # adverse (for short: up-bar; for long: down-bar) spike gap
            adv_j = (closes[j] < df["open"].values[j]) if is_crash \
                else (closes[j] > df["open"].values[j])
            if adv_j and rng[j] > 3 * med[j]:
                fill = highs[j] if is_crash else lows[j]
            else:
                fill = sl_level
            return ((entry - fill) if is_crash else (fill - entry)) / R, "sl"
        if hit_tp:
            return K if is_crash else K, "tp"
    p = closes[min(e + H, n - 1)]
    return ((entry - p) if is_crash else (p - entry)) / R, "timeout"


def run(key, D, K):
    df = E.load_prep(key)
    base = E.spike_rows(df, key)
    idx = np.where(base)[0]
    out = []
    for i in idx:
        tr = lottery_trade(df, key, i, D, K)
        if tr is None:
            continue
        out.append(tr)
    return out


def summarize(key):
    print(f"\n=== {key.upper()} : EV in R-units (SL=1R, win=+K R) ===")
    print(f"  {'D':<4}" + "".join(f"{'K='+str(k):>8}" for k in [2, 4, 6, 10, 15]))
    best = None
    for D in [1, 3, 5, 7]:
        cells = []
        for K in [2, 4, 6, 10, 15]:
            trs = run(key, D, K)
            pnls = np.array([t[0] for t in trs])
            ev = pnls.mean()
            wr = 100 * np.mean([t[1] == "tp" for t in trs])
            cells.append((ev, len(pnls), wr))
            if best is None or ev > best[0]:
                best = (ev, key, D, K, len(pnls), wr)
        row = f"  D={D:<3}" + "".join(f"{c[0]:>+8.2f}" for c in cells)
        wrrow = f"  {'':<4}" + "".join(f"({c[2]:.0f}%)" for c in cells)
        print(row)
        print(wrrow)
    return best


if __name__ == "__main__":
    print("EV per trade in R units. Positive = the lottery is alive.")
    for key in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        b = summarize(key)
        if b:
            print(f"  >> best cell: {b[1]} D={b[2]} K={b[3]} EV={b[0]:+.2f}R "
                  f"(n={b[4]}, P(win)={b[5]:.0f}%)")