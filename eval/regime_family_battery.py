#!/usr/bin/env python3
"""
REGIME FAMILY BATTERY — challenge + build on the verified gate+pullback edge
============================================================================
Established: M15 BOS gate + 50%-retrace pullback entry = real on crash300/boom500.
This battery challenges the definition and hunts the neighboring edges:

  A. REGIME SWEEPS (gate+pullback fixed): BOS lookback {2,3,4,6,8} x
     persistence {3,6,12} on crash300/boom500 — is 4/6 a knife-edge?
  B. FRESHNESS: does the gate decay with bars-since-last-BOS? (fresh >> stale
     would be a new timing edge)
  C. CONSECUTIVE BOS: is a 2nd break in the same direction stronger?
  D. HIGHER TF: M60 BOS gate vs M15 gate (4 symbols with 60m data).
  E. FALLBACK ENTRY: discharges with NO pullback, entered at e+7 — do the
     skipped ~70% add throughput at positive EV?
  F. KILL-SWITCH: fade AGAINST the regime must be dead (validates the gate).
All on the corrected engine, partial, hold=3, OOS halves on survivors.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, pullback_bar, stats, row
from ict_bosconfirm_test import trade_at
from partial_m1_corrected import arrays


def regime_mask(df, sym, tf="15min", lookback=4, persist=6):
    """bool per M1 row: True = regime matches drift dir (bull for crash)."""
    df = df.copy()
    m = df.resample(tf, on="time").agg(
        h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m["c"].values; h = m["h"].values; l = m["l"].values
    n = len(m)
    bull = np.zeros(n, dtype=bool); bear = np.zeros(n, dtype=bool)
    for t in range(lookback, n):
        if c[t] > h[t - lookback:t].max():
            bull[t] = True
        if c[t] < l[t - lookback:t].min():
            bear[t] = True
    reg = np.zeros(n, dtype=bool)
    for t in range(n):
        w = bull[max(0, t - persist):t + 1]
        if w.any():
            wb = bear[max(0, t - persist):t + 1]
            if not wb.any() or (np.where(w)[0][-1] > np.where(wb)[0][-1]):
                reg[t] = True
    m["bull"] = reg
    mt = m["time"].values.astype("datetime64[ns]")
    dt = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(mt, dt, side="right") - 1
    idx = np.clip(idx, 0, n - 1)
    return m["bull"].values[idx]


def gated_pull(df, sym, mask, hold=3, fallback=False):
    if "boom" in sym:
        mask = ~mask
    idx = discharges(df, sym)
    trades = []
    for i in idx:
        if not mask[i]:
            continue
        b = pullback_bar(df, sym, i, 6)
        if b < 0:
            if fallback:
                b = i + 7
                if b + 2 >= len(df):
                    continue
            else:
                continue
        p = trade_at(df, sym, b, hold)
        if p is not None:
            trades.append(p)
    return trades


def freshness_analysis(df, sym, tf="15min", lookback=4, persist=6):
    """Split gated pullback trades by bars-since-last-BOS."""
    df = df.copy()
    m = df.resample(tf, on="time").agg(
        h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m["c"].values; h = m["h"].values; l = m["l"].values
    n = len(m)
    bull = np.zeros(n, dtype=bool); bear = np.zeros(n, dtype=bool)
    for t in range(lookback, n):
        if c[t] > h[t - lookback:t].max():
            bull[t] = True
        if c[t] < l[t - lookback:t].min():
            bear[t] = True
    # last-BOS age per M15 bar (in M15 bars)
    age = np.full(n, 999)
    last = None
    for t in range(n):
        if bull[t] or bear[t]:
            last = 0
        if last is not None:
            age[t] = last
            last += 1
    mt = m["time"].values.astype("datetime64[ns]")
    dt = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(mt, dt, side="right") - 1
    idx = np.clip(idx, 0, n - 1)
    m1_age = age[idx]
    is_crash = "crash" in sym
    # direction of last BOS
    dir_last = np.zeros(n, dtype=int)  # +1 bull, -1 bear
    lastd = 0
    for t in range(n):
        if bull[t]:
            lastd = 1
        elif bear[t]:
            lastd = -1
        dir_last[t] = lastd
    m1_dir = dir_last[idx]
    ok_dir = (m1_dir == 1) if is_crash else (m1_dir == -1)
    idx_d = discharges(df, sym)
    buckets = {"fresh(<=2)": [], "mid(3-8)": [], "stale(>8)": []}
    for i in idx_d:
        if not ok_dir[i]:
            continue
        b = pullback_bar(df, sym, i, 6)
        if b < 0:
            continue
        a = m1_age[i]
        p = trade_at(df, sym, b, 3)
        if p is None:
            continue
        if a <= 2:
            buckets["fresh(<=2)"].append(p)
        elif a <= 8:
            buckets["mid(3-8)"].append(p)
        else:
            buckets["stale(>8)"].append(p)
    return buckets


def consecutive_bos(df, sym, tf="15min", lookback=4, persist=6):
    """1st vs 2nd+ consecutive BOS in same direction."""
    df = df.copy()
    m = df.resample(tf, on="time").agg(
        h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m["c"].values; h = m["h"].values; l = m["l"].values
    n = len(m)
    bull = np.zeros(n, dtype=bool); bear = np.zeros(n, dtype=bool)
    for t in range(lookback, n):
        if c[t] > h[t - lookback:t].max():
            bull[t] = True
        if c[t] < l[t - lookback:t].min():
            bear[t] = True
    streak = np.zeros(n, dtype=int)
    s = 0
    for t in range(n):
        if bull[t]:
            s = s + 1 if s > 0 else 1
        elif bear[t]:
            s = s - 1 if s < 0 else -1
        else:
            s = 0
        streak[t] = s
    mt = m["time"].values.astype("datetime64[ns]")
    dt = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(mt, dt, side="right") - 1
    idx = np.clip(idx, 0, n - 1)
    m1_streak = streak[idx]
    is_crash = "crash" in sym
    idx_d = discharges(df, sym)
    first = []; second = []
    for i in idx_d:
        st = m1_streak[i]
        if is_crash and st < 1:
            continue
        if not is_crash and st > -1:
            continue
        b = pullback_bar(df, sym, i, 6)
        if b < 0:
            continue
        p = trade_at(df, sym, b, 3)
        if p is None:
            continue
        if abs(st) == 1:
            first.append(p)
        else:
            second.append(p)
    return first, second


if __name__ == "__main__":
    syms_a = ["crash300", "boom500"]

    print("=" * 96)
    print("A. REGIME SWEEPS — BOS lookback x persistence (gate+pull, hold=3)")
    print("=" * 96)
    for sym in syms_a:
        df = load(sym)
        print(f"\n  {sym}:")
        for lb in [2, 3, 4, 6, 8]:
            for ps in [3, 6, 12]:
                mask = regime_mask(df, sym, "15min", lb, ps)
                s = stats(gated_pull(df, sym, mask))
                if s:
                    row(f"  lookback={lb} persist={ps}", s)

    print("\n" + "=" * 96)
    print("B. FRESHNESS — bars-since-last-BOS (gate+pull)")
    print("=" * 96)
    for sym in syms_a:
        df = load(sym)
        print(f"\n  {sym}:")
        for k, v in freshness_analysis(df, sym).items():
            row(f"  {k}", stats(v))

    print("\n" + "=" * 96)
    print("C. CONSECUTIVE BOS — 1st vs 2nd+ break in same direction")
    print("=" * 96)
    for sym in syms_a:
        df = load(sym)
        f, s = consecutive_bos(df, sym)
        row(f"  {sym} 1st break", stats(f))
        row(f"  {sym} 2nd+ break", stats(s))

    print("\n" + "=" * 96)
    print("D. HIGHER TF — M60 BOS gate vs M15 gate (gate+pull)")
    print("=" * 96)
    for sym in ["crash500", "crash1000", "boom500", "boom1000"]:
        df = load(sym)
        m15 = regime_mask(df, sym, "15min")
        m60 = regime_mask(df, sym, "60min")
        row(f"  {sym} M15 gate", stats(gated_pull(df, sym, m15)))
        row(f"  {sym} M60 gate", stats(gated_pull(df, sym, m60)))

    print("\n" + "=" * 96)
    print("E. FALLBACK — no-pullback discharges entered at e+7 (gate)")
    print("=" * 96)
    for sym in syms_a + ["crash500"]:
        df = load(sym)
        mask = regime_mask(df, sym, "15min")
        s_nofb = stats(gated_pull(df, sym, mask, fallback=False))
        s_fb = stats(gated_pull(df, sym, mask, fallback=True))
        row(f"  {sym} no fallback", s_nofb)
        row(f"  {sym} e+7 fallback", s_fb)

    print("\n" + "=" * 96)
    print("F. KILL-SWITCH — fade AGAINST the regime (must be dead)")
    print("=" * 96)
    for sym in syms_a:
        df = load(sym)
        mask = regime_mask(df, sym, "15min")
        anti = ~mask if "boom" not in sym else mask  # crash: anti = bearish
        anti = ~mask  # opposite of the drift-matching regime
        s = stats(gated_pull(df, sym, anti))
        row(f"  {sym} fade AGAINST regime", s)