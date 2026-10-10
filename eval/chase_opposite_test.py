#!/usr/bin/env python3
"""
CHASE THE OPPOSITE-DIRECTION BARS — ride the retracement
========================================================
Main spikes mean-revert (fade wins, chase loses — CHASE_SPIKE_VERDICT).
The opposite-direction bars (crash: up / boom: down) are SIZE-CAPPED by the
algorithm — measured: ZERO opposite bars exceed 1.5x the all-bar median on
4 of 5 symbols. The retracement is a grind of small bars, never a spike.

So "retracement spikes" don't exist as discrete events. What we CAN test:
do the LARGEST opposite bars (top q% of their own class) behave differently
when chased — i.e. does a strong bounce bar predict continued bounce?

  - crash: LONG the strong up bar (ride the updrift it starts)
  - boom:  SHORT the strong down bar (ride the pullback)

Same corrected accounting: SL = max(median, floor), TP = 2x SL,
gap-through fills on main-direction spike bars, timeout at hold.
Selection: opposite bars in the top (1-q) quantile of opposite-bar ranges.
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, ".")
from partial_m1_corrected import load, prep, arrays, stats, pipeline, BROKER_FLOOR, SPIKE_MULT


def opp_spikes(df, sym, q):
    """Opposite-direction bars with range above the q-quantile of opposite ranges."""
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    opp = (df["close"].values > df["open"].values) if is_crash else \
          (df["close"].values < df["open"].values)
    ok = opp & np.isfinite(med) & (rng > 0.05)
    thr = np.quantile(rng[ok], q) if ok.sum() > 0 else np.inf
    m = ok & (rng >= thr)
    return np.where(m)[0]


def opp_chase_trade(df, sym, i, hold, e_off=1, tp_mult=2.0):
    """Enter WITH the opposite bar: crash=LONG the bounce, boom=SHORT the pullback."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = i + e_off
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    if is_crash:  # LONG the bounce
        sl_level = entry - sl_dist
        tp_level = entry + tp_mult * sl_dist
    else:         # SHORT the pullback
        sl_level = entry + sl_dist
        tp_level = entry - tp_mult * sl_dist

    for j in range(e + 1, min(e + 1 + hold, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        else:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        if hit_sl:
            # gap-through on a MAIN-direction spike bar
            if adv[j] and rng[j] > SPIKE_MULT * med[j]:
                return (lows[j] if is_crash else highs[j]), "sl"
            return sl_level, "sl"
        if hit_tp:
            return tp_level, "tp"
    return closes[min(e + hold, n - 1)], "timeout"


def opp_pipeline(df, sym, hold, q, e_off=1, cluster=True, cooldown=0):
    idx = opp_spikes(df, sym, q)
    trades = []
    skip = 0
    closes = df["close"].values
    for m_, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and m_ >= 2 and (i - idx[m_ - 2]) <= 10:
            continue
        tr = opp_chase_trade(df, sym, i, hold, e_off)
        if tr is None:
            continue
        p, r = tr
        entry = closes[i + e_off]
        pnl = (p - entry) if "crash" in sym else (entry - p)
        trades.append({"pnl": pnl, "nhalves": 1, "reason": r})
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def stat_full(trades):
    s = stats(trades)
    if s is None:
        return None
    s["reasons"] = [t["reason"] for t in trades]
    return s


def row(label, s):
    if s is None:
        print(f"  {label:<40}  none")
        return
    reasons = {}
    for t in s.get("reasons", []):
        reasons[t] = reasons.get(t, 0) + 1
    rstr = " ".join(f"{k}:{v}" for k, v in sorted(reasons.items()))
    print(f"  {label:<40}{s['n']:>5}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}  {rstr}")


if __name__ == "__main__":
    SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]
    results = {}
    print("=" * 114)
    print("OPPOSITE-BAR CHASE (top-quantile retracement bars) — crash: LONG | boom: SHORT")
    print("=" * 114)
    for sym in SYMS:
        df = prep(load(sym))
        print(f"\n--- {sym.upper()} ---")
        print(f"  {'config':<40}{'n':>5}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD   exits")
        best = None
        for q in [0.50, 0.90, 0.95, 0.98]:
            for e_off in [0, 1]:
                for hold in [1, 2, 3, 5]:
                    tr = opp_pipeline(df, sym, hold, q, e_off=e_off, cluster=True)
                    s = stat_full(tr)
                    row(f"OPP q={q} off={e_off} hold={hold}", s)
                    if s and (best is None or s["pf"] > best[1]["pf"]):
                        best = ((q, e_off, hold), s)
        fb = stats(pipeline(df, sym, 2, partial=True))
        row("FADE baseline (partial, hold=2)", fb)
        results[sym] = (best, df)
        if best:
            print(f"  >> best opp-chase: q={best[0][0]} off={best[0][1]} hold={best[0][2]} PF={best[1]['pf']:.2f}")

    print("\n" + "=" * 114)
    print("OOS HALVES for best opp-chase config per symbol (gate ON vs OFF)")
    print("=" * 114)
    for sym in SYMS:
        if results[sym][0] is None:
            continue
        (q, e_off, hold), s_full, df = results[sym][0][0], results[sym][0][1], results[sym][1]
        half = len(df) // 2
        print(f"\n--- {sym.upper()} opp-chase q={q} off={e_off} hold={hold} (full PF {s_full['pf']:.2f}) ---")
        for gname, g in [("gate", True), ("nogate", False)]:
            for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
                d = df.iloc[sl].reset_index(drop=True)
                tr = opp_pipeline(d, sym, hold, q, e_off=e_off, cluster=g)
                row(f"  {gname} {hname} half", stat_full(tr))