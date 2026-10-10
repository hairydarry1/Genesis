#!/usr/bin/env python3
"""
PARTIAL ENTRY — CORRECTED M1 ENGINE (independent check)
=======================================================
The ONLY surviving positive is partial entry from the tick-level sim
(hold_time_levers.py, PF ~2.0, real gap fills). That sim walks real ticks
from the entry tick so it can't have the entry-bar lookahead bug.

This is a SECOND, independent engine on M1 bars with the CORRECTED exit
model (position opens at close of entry bar; only bars AFTER entry can
fill it; gap-through fills on adverse spike bars). It tests whether the
partial-entry edge survives outside the tick sim.

Partial semantics (bar-space mirror of the tick sim):
  - Half A (50%) enters at close of bar e (bar after the spike).
  - At close of bar e+1, if A is still open AND profitable, half B (50%)
    joins at the same entry, same SL/TP levels, window e+2..e+hold.
  - Each half exits at first SL/TP hit in its window, else timeout.
  - Combined PnL = 0.5*(exitA-entry) + 0.5*(exitB-entry if added).
"""
import pandas as pd
import numpy as np

BROKER_FLOOR = {"crash1000": 1.14, "crash500": 0.85, "crash300": 2.43,
                "boom1000": 1.14, "boom500": 0.85}
SPIKE_MULT = 3.0

def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def prep(df):
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df

def arrays(df, sym):
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    opens = df["open"].values
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (closes < opens) if is_crash else (closes > opens)
    return closes, highs, lows, rng, med, adv, is_crash

def spikes(df, sym):
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]

def half_exit(closes, highs, lows, rng, med, adv, is_crash, entry, sl_level,
              tp_level, s, maxbars, n):
    """Walk one half from bar s for maxbars bars. Returns (exit_price, reason)."""
    for j in range(s, min(s + maxbars, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        else:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                return (lows[j] if is_crash else highs[j]), "sl"
            return sl_level, "sl"
        if hit_tp:
            return tp_level, "tp"
    return closes[min(s + maxbars - 1, n - 1)], "timeout"

def partial_trade(df, sym, i, hold, sl_override=None):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = i + 1
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0)) if sl_override is None else sl_override
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)

    # Half A: 50%, bars e+1..e+hold
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, e + 1, hold, n)

    # Half B joins at close[e+1] if A still open then and profitable.
    add_b = False
    if rA == "timeout":  # A open at least through bar e+1... unless hold covers it
        pass
    # A is definitely still open at close[e+1] iff it didn't exit on bar e+1.
    # Re-derive: exit on bar e+1 happens only if rA != timeout AND exit bar was e+1.
    # Simpler check: run A for exactly 1 bar; if it doesn't exit, it's open.
    pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, 1, n)
    if rA1 == "timeout":
        fav = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
        add_b = fav

    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, e + 2, hold - 1, n)
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return {"pnl": pnl, "nhalves": 2 if add_b else 1}

def pipeline(df, sym, hold, partial=True, cluster=True, cooldown=0):
    idx = spikes(df, sym)
    trades = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        tr = partial_trade(df, sym, i, hold) if partial else full_trade(df, sym, i, hold)
        if tr is None:
            continue
        trades.append(tr)
        if tr["pnl"] <= 0 and cooldown > 0:
            skip = cooldown
    return trades

def full_trade(df, sym, i, hold):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = i + 1
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    p, r = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                     entry, sl_level, tp_level, e + 1, hold, n)
    return {"pnl": (p - entry) * (1 if is_crash else -1), "nhalves": 1}

def stats(trades):
    if not trades:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    eq = np.cumsum(pnls)
    dd = (np.maximum.accumulate(eq) - eq).max()
    halves = np.mean([t["nhalves"] for t in trades])
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean(), "maxdd": dd, "avg_halves": halves}

def row(label, s):
    if s is None:
        print(f"  {label:<30}  none"); return
    print(f"  {label:<30}{s['n']:>6}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}"
          f"  (halves avg {s['avg_halves']:.2f})")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = prep(load(sym))
        print(f"\n{'='*100}\n{sym.upper()}\n{'='*100}")
        print(f"  {'config':<30}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD")
        for hold in [2, 3, 5, 7]:
            base = stats(pipeline(df, sym, hold, partial=False))
            prt = stats(pipeline(df, sym, hold, partial=True))
            row(f"FULL   hold={hold}", base)
            row(f"PARTIAL hold={hold}", prt)
        # OOS split of partial hold=3
        half = len(df) // 2
        print("  OOS halves (partial, hold=3):")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            row(f"  {hname} half", stats(pipeline(d, sym, 3, partial=True)))
