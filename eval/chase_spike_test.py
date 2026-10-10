#!/usr/bin/env python3
"""
SPIKE CHASE TEST — enter WITH the spike direction
=================================================
The verified system FADES spikes (crash: buy the dip, boom: short the pump).
This tests the opposite: CHASING — crash: SHORT the dump, boom: LONG the pump.

User rationale: "stoplosses work normally here" — the SL sits BEHIND the
spike (above entry on a short), so spikes move the trade in its favor and
the SL only gets threatened by the slower retracement/updrift.

Same corrected-engine accounting as partial_m1_corrected.py:
  - entry at close of bar (spike + entry_offset)
  - SL = max(median_range, broker floor)  (mirrored to the opposite side)
  - TP = tp_mult x SL in the spike direction
  - gap-through fills on adverse spike bars (adverse = OPPOSITE direction)
  - timeout at hold bars
No lookahead: only bars after entry can fill it.
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, ".")
from partial_m1_corrected import load, prep, arrays, spikes, stats, BROKER_FLOOR, SPIKE_MULT


def chase_trade(df, sym, i, hold, e_off=1, tp_mult=2.0, sl_override=None):
    """Enter in the spike direction at close of bar i+e_off.

    crash: SHORT (dir=-1), SL above entry, TP below.
    boom:  LONG  (dir=+1), SL below entry, TP above.
    """
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = i + e_off
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0)) if sl_override is None else sl_override
    if is_crash:  # SHORT
        sl_level = entry + sl_dist
        tp_level = entry - tp_mult * sl_dist
    else:         # LONG
        sl_level = entry - sl_dist
        tp_level = entry + tp_mult * sl_dist

    # adverse = opposite-direction bar (for crash-short: an UP bar)
    def adverse(j):
        if is_crash:
            return not adv[j]  # up bar
        return adv[j]          # down bar

    for j in range(e + 1, min(e + 1 + hold, n)):
        if is_crash:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        else:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        if hit_sl:
            if adverse(j) and rng[j] > SPIKE_MULT * med[j]:
                return (highs[j] if is_crash else lows[j]), "sl"
            return sl_level, "sl"
        if hit_tp:
            return tp_level, "tp"
    p = closes[min(e + hold, n - 1)]
    return p, "timeout"


def chase_pipeline(df, sym, hold, e_off=1, tp_mult=2.0, cluster=True, cooldown=0):
    idx = spikes(df, sym)
    trades = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        tr = chase_trade(df, sym, i, hold, e_off, tp_mult)
        if tr is None:
            continue
        p, r = tr
        entry = entry_pnl(df, sym, i, e_off)
        # Direction-correct sign: SHORT profits when price FALLS.
        pnl = (entry - p) if "crash" in sym else (p - entry)
        trades.append({"pnl": pnl, "nhalves": 1, "reason": r})
        if trades[-1]["pnl"] <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def entry_pnl(df, sym, i, e_off):
    closes = df["close"].values
    e = i + e_off
    return closes[e]


def row(label, s):
    if s is None:
        print(f"  {label:<34}  none")
        return
    reasons = {}
    for t in s.get("reasons", []):
        reasons[t] = reasons.get(t, 0) + 1
    rstr = " ".join(f"{k}:{v}" for k, v in sorted(reasons.items()))
    print(f"  {label:<34}{s['n']:>5}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}  {rstr}")


def stat_full(trades):
    s = stats(trades)
    if s is None:
        return None
    s["reasons"] = [t["reason"] for t in trades]
    return s


if __name__ == "__main__":
    SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]
    results = {}
    print("=" * 110)
    print("SPIKE CHASE — enter WITH the spike (crash: SHORT, boom: LONG)")
    print("=" * 110)
    for sym in SYMS:
        df = prep(load(sym))
        print(f"\n--- {sym.upper()} ---")
        print(f"  {'config':<34}{'n':>5}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD   exits")
        best = None
        for e_off in [0, 1, 2, 3]:
            for hold in [1, 2, 3, 5]:
                tr = chase_pipeline(df, sym, hold, e_off=e_off, cluster=True)
                s = stat_full(tr)
                row(f"CHASE off={e_off} hold={hold}", s)
                if s and (best is None or s["pf"] > best[1]["pf"]):
                    best = ((e_off, hold), s)
        # fade baseline for reference (crash long / boom short, entry+1, hold=2)
        from partial_m1_corrected import pipeline, stats as fstats
        fb = fstats(pipeline(df, sym, 2, partial=True))
        row("FADE baseline (partial, hold=2)", fb)
        results[sym] = (best, df)
        print(f"  >> best chase: off={best[0][0]} hold={best[0][1]} PF={best[1]['pf']:.2f}")

    print("\n" + "=" * 110)
    print("OOS HALVES for the best chase config per symbol")
    print("=" * 110)
    for sym in SYMS:
        (e_off, hold), s_full, df = results[sym][0][0], results[sym][0][1], results[sym][1]
        half = len(df) // 2
        print(f"\n--- {sym.upper()} chase off={e_off} hold={hold} (full PF {s_full['pf']:.2f}) ---")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            tr = chase_pipeline(d, sym, hold, e_off=e_off, cluster=True)
            row(f"  {hname} half", stat_full(tr))