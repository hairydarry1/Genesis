#!/usr/bin/env python3
"""
ICT SWING-TP TEST
=================
Video claim: TP at the PRIOR SWING HIGH (buys) / LOW (sells) instead of 2x SL,
"don't take it any further."

Setup (identical to ict_pullback_test except TP):
  - Gated pullback entries: M15 BOS regime gate + >=50% retrace entry.
  - SL = max(median, floor) as before (unchanged).
  - TP = most recent CONFIRMED M15 swing high (crash LONG) / swing low
    (boom SHORT) that closed strictly before the entry bar (no lookahead).
    Fallback to 2x SL if none found within 120 M15 bars.
  - Same partial engine, gap-through fills, hold swept 3/5/7.
  - Random control + OOS halves for honesty.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, pullback_bar, stats, row
from partial_m1_corrected import arrays, half_exit, BROKER_FLOOR


def m15_frame(df):
    df = df.copy()
    m15 = df.resample("15min", on="time").agg(
        o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    return m15


def m15_swing_tp(df, sym, entry_bar, fallback_dist):
    """Most recent confirmed M15 swing in drift direction, closed before entry bar.
    Returns (tp_level, distance_from_entry, is_fallback)."""
    m15 = m15_frame(df)
    m15t = m15["time"].values.astype("datetime64[ns]")
    entry_t = df["time"].values[entry_bar].astype("datetime64[ns]")
    idx15 = int(np.searchsorted(m15t, entry_t, side="right") - 1)
    if idx15 < 3:
        return None, 0.0, True
    is_crash = "crash" in sym
    h = m15["h"].values; l = m15["l"].values
    entry = df["close"].values[entry_bar]
    hi_lo = h if is_crash else l
    # most recent confirmed swing: bar k with hi_lo[k] > neighbors (crash high)
    # or hi_lo[k] < neighbors (boom low); needs k+1 closed => k <= idx15-1
    for k in range(min(idx15 - 1, 120), 0, -1):
        if is_crash:
            if hi_lo[k] > hi_lo[k - 1] and hi_lo[k] > hi_lo[k + 1]:
                return hi_lo[k], abs(hi_lo[k] - entry), False
        else:
            if hi_lo[k] < hi_lo[k - 1] and hi_lo[k] < hi_lo[k + 1]:
                return hi_lo[k], abs(entry - hi_lo[k]), False
    return None, 0.0, True


def trade_at_tp(df, sym, entry_bar, hold, tp_mode, partial=True):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    if tp_mode == "2sl":
        tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    else:
        tp_level, dist, fb = m15_swing_tp(df, sym, entry_bar, 2 * sl_dist)
        if tp_level is None:
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


def run_cfg(df, sym, hold, mode, tp_mode, regime, partial=True, frac=0.5, maxwait=6):
    if regime is not None and "boom" in sym:
        regime = ~regime
    idx = discharges(df, sym)
    rng_state = np.random.RandomState(42)
    trades = []
    for k, i in enumerate(idx):
        if regime is not None and not regime[i]:
            continue
        if mode == "e1":
            b = i + 1
        elif mode == "pull":
            b = pullback_bar(df, sym, i, maxwait)
            if b < 0:
                continue
        elif mode == "rand":
            b = int(rng_state.randint(0, len(df) - 10))
            if regime is not None and not regime[b]:
                continue
        pnl = trade_at_tp(df, sym, b, hold, tp_mode, partial)
        if pnl is None:
            continue
        trades.append(pnl)
    return trades


def swing_stats(df, sym, tp_mode):
    """Report TP distance stats (swing vs 2xSL) on gated pullback entries."""
    reg = m15_regime_of(df, sym)
    if "boom" in sym:
        reg = ~reg
    idx = discharges(df, sym)
    closes = df["close"].values
    dists = []
    fallbacks = 0
    n_tr = 0
    for i in idx:
        if not reg[i]:
            continue
        b = pullback_bar(df, sym, i, 6)
        if b < 0:
            continue
        entry = closes[b]
        sl_dist = max(df["median_range"].values[b], BROKER_FLOOR.get(sym, 1.0))
        if tp_mode == "2sl":
            dists.append(2 * sl_dist)
        else:
            _, d, fb = m15_swing_tp(df, sym, b, 2 * sl_dist)
            if fb:
                fallbacks += 1
                d = 2 * sl_dist
            dists.append(d)
        n_tr += 1
    return n_tr, np.mean(dists), fallbacks


def m15_regime_of(df, sym):
    from ict_pullback_test import m15_regime
    return m15_regime(df, sym)


if __name__ == "__main__":
    for sym in ["crash300", "boom500", "crash500", "boom1000", "crash1000"]:
        df = load(sym)
        reg = m15_regime_of(df, sym)
        print(f"\n{'='*96}\n{sym.upper()}  (gate + pullback entries, partial)\n{'='*96}")
        ntr, avgd, fb = swing_stats(df, sym, "swing")
        print(f"  pullback+gated trades={ntr}  avg TP dist: swing={avgd:.2f}  (fallbacks={fb})")
        print(f"  {'config':<30}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>10}{'avg':>8}")
        for hold in [3, 5, 7]:
            for tpm, lab in [("2sl", "TP=2xSL "), ("swing", "TP=swing")]:
                s = stats(run_cfg(df, sym, hold, "pull", tpm, reg))
                row(f"hold={hold} {lab}", s)
        # random control on swing TP, hold=5
        s = stats(run_cfg(df, sym, 5, "rand", "swing", reg))
        row("RANDOM + gate TP=swing h=5 (ctrl)", s)
        # OOS halves of swing TP hold=5
        half = len(df) // 2
        print("  OOS (TP=swing, hold=5):")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            r2 = m15_regime_of(d, sym)
            row(f"  {hname}", stats(run_cfg(d, sym, 5, "pull", "swing", r2)))