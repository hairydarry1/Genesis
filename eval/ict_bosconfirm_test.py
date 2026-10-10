#!/usr/bin/env python3
"""
ICT M1-BOS-CONFIRMATION TEST (Gemini-surfaced rule)
===================================================
Gemini's Example-4 finding: when price never reaches the 50% level after
a discharge, the author does NOT abandon the trade — he waits for an M1
micro break-of-structure and enters on the confirmation bar instead.

Two entry rules compared (drift direction, gated by M15 BOS regime):
  PULL  (baseline): first bar j in [i+1, i+6] closing >=50% retraced off
         the bounce without breaking the discharge extreme.
  BOS   (new):      after the discharge, find the bounce's local pivot
         (crash: swing high; boom: swing low).  Enter at close of the
         first bar that CLOSES beyond that pivot (M1 BOS confirmation),
         within [i+1, i+10].  Skip if none.
Identical exits (2xSL/1xSL partial, gap-through), random control + OOS.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, pullback_bar, stats, row, m15_regime
from partial_m1_corrected import arrays, half_exit, BROKER_FLOOR


def bos_entry_bar(df, sym, i, maxwait=10):
    """M1 BOS confirmation entry: close beyond the bounce pivot. -1 if none."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    is_crash = "crash" in sym
    # find pivot within the bounce window
    w = 1
    pivot_k = -1
    for k in range(i + 2, min(i + 7, n - 1)):
        if is_crash:
            if highs[k] > highs[k - 1] and highs[k] > highs[k + 1] and \
               highs[k] >= highs[k - w:k + w + 1].max():
                pivot_k = k
                break
        else:
            if lows[k] < lows[k - 1] and lows[k] < lows[k + 1] and \
               lows[k] <= lows[k - w:k + w + 1].min():
                pivot_k = k
                break
    if pivot_k < 0:
        return -1
    level = highs[pivot_k] if is_crash else lows[pivot_k]
    for j in range(pivot_k + 1, min(pivot_k + maxwait + 1, n)):
        if is_crash:
            if closes[j] > level:
                return j
        else:
            if closes[j] < level:
                return j
    return -1


def trade_at(df, sym, entry_bar, hold, partial=True):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, entry_bar + 1, hold, n)
    add_b = False
    if partial:
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, entry_bar + 1, 1, n)
        if rA1 == "timeout":
            fav = (closes[entry_bar + 1] > entry) if is_crash else (closes[entry_bar + 1] < entry)
            add_b = fav
    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, entry_bar + 2, hold - 1, n)
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return pnl


def run_cfg(df, sym, hold, mode, regime, partial=True):
    if regime is not None and "boom" in sym:
        regime = ~regime
    idx = discharges(df, sym)
    rng_state = np.random.RandomState(42)
    trades = []
    for i in idx:
        if regime is not None and not regime[i]:
            continue
        if mode == "pull":
            b = pullback_bar(df, sym, i, 6)
        elif mode == "bos":
            b = bos_entry_bar(df, sym, i)
        elif mode == "rand":
            b = int(rng_state.randint(0, len(df) - 10))
            if regime is not None and not regime[b]:
                continue
        if b < 0:
            continue
        pnl = trade_at(df, sym, b, hold, partial)
        if pnl is None:
            continue
        trades.append(pnl)
    return trades


if __name__ == "__main__":
    for sym in ["crash300", "boom500", "boom1000", "crash500"]:
        df = load(sym)
        reg = m15_regime(df, sym)
        print(f"\n{'='*96}\n{sym.upper()}  (gated, partial, hold=3)\n{'='*96}")
        print(f"  {'config':<34}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>10}{'avg':>8}")
        for mode, lab in [("pull", "PULLBACK 50% (baseline)"),
                          ("bos", "M1-BOS confirm (video Ex.4)"),
                          ("rand", "RANDOM (control)")]:
            row(lab, stats(run_cfg(df, sym, 3, mode, reg)))
        half = len(df) // 2
        print("  OOS (M1-BOS confirm):")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            r2 = m15_regime(d, sym)
            row(f"  {hname}", stats(run_cfg(d, sym, 3, "bos", r2)))