#!/usr/bin/env python3
"""
EDGE HUNT v2 — mechanism-driven, corrected-engine only.

Everything here is computed from bars STRICTLY BEFORE the spike (no
lookahead) and evaluated with the same partial-entry + real-gap-fill
semantics as the corrected engine. Per protocol: any promising bucket
gets checked on both OOS halves before being reported as real.

Tests:
  A. Drift-regime gating  — fade profits come from drift; are entries
     taken when the pre-spike drift is strong meaningfully better?
  C. Spike-size compression — after a BIG discharge is the next adverse
     spike smaller (tail-risk compression behind the cluster gate)?
  D. Entry-bar delay       — 1 vs 2 vs 3 bars after the spike
  E. Spike body ratio      — decisive (|body|~range) vs rejected spike
     bars: does the print shape tell us anything about what follows?
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, by_key  # noqa: E402

W = 96  # summary width


def fmt(s):
    if not s or s["n"] == 0:
        return f"{'none':>44}"
    return (f"n={s['n']:>5} WR={s['wr']:>5.1f}% PF={s['pf']:>6.2f} "
            f"PnL={s['pnl']:>8.1f} avg={s['avg']:>+7.3f} DD={s['maxdd']:>7.1f}")


def trades_with(key, cfg, df=None):
    """engine.run_cfg trades + a per-trade 'entry_row' (already attached)."""
    return E.run_cfg(key, cfg["hold"], cooldown=cfg.get("cooldown", 0),
                     partial=True, gate=cfg.get("gate", False),
                     gate_mult=cfg.get("gate_mult", 8.0),
                     gate_window=cfg.get("gate_window", 3), df=df)


def bucket_stats(trades, values, n_buckets=3, name="bucket"):
    """Split trades into n_buckets by value quantiles, stats per bucket."""
    vals = np.array([v for v in values if v == v], dtype=float)
    if len(vals) < n_buckets * 5:
        return None
    qs = np.quantile(vals, np.linspace(0, 1, n_buckets + 1))
    out = []
    for b in range(n_buckets):
        lo, hi = qs[b], qs[b + 1]
        sel = []
        for t, v in zip(trades, values):
            if v == v and lo <= v < hi:
                sel.append(t)
        # edges
        if b == n_buckets - 1:
            sel = [t for t, v in zip(trades, values) if v == v and lo <= v <= hi]
        out.append((f"{name} {b}", E.outcome_stats(sel)))
    return out


# ----------------------------------------------------------------------
# A. DRIFT-REGIME GATING
# ----------------------------------------------------------------------
def test_a():
    print("\n" + "=" * W)
    print("A. DRIFT GATE — pre-spike drift strength vs fade outcome")
    print("=" * W)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        closes = df["close"].values
        cfg = dict(by_key(key)["verified"])
        trs = trades_with(key, cfg, df)
        if not trs:
            continue
        is_crash = "crash" in key
        drifts = []
        for t in trs:
            i = t["entry_row"]
            ref = max(0, i - 60)
            d = (closes[i - 1] - closes[ref]) if i - 1 >= 0 else np.nan
            # fade direction = drift direction (crash fades need UP-drift)
            drifts.append(d if is_crash else -d)
        base = E.outcome_stats(trs)
        print(f"\n{key} (verified cfg, n trades) overall:\n  {fmt(base)}")
        for label, st in bucket_stats(trs, drifts, 3, "drift"):
            if st and st["n"]:
                print(f"  {label:<12} {fmt(st)}")


# ----------------------------------------------------------------------
# C. SPIKE-SIZE COMPRESSION
# ----------------------------------------------------------------------
def test_c():
    print("\n" + "=" * W)
    print("C. SIZE COMPRESSION — is the next adverse spike smaller after a big one?")
    print("=" * W)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        base = E.spike_rows(df, key)
        idx = np.where(base)[0]
        rng = df["range"].values
        med = df["median_range"].values
        if len(idx) < 50:
            continue
        sizes = rng[idx] / np.maximum(med[idx], 1e-9)
        # consecutive (in spike order) log-size autocorrelation
        logsz = np.log(np.maximum(sizes, 0.01))
        corr1 = float(np.corrcoef(logsz[:-1], logsz[1:])[0, 1]) if len(logsz) > 5 else np.nan
        # mean size of the NEXT adverse spike after a big (>8x) one
        big = sizes[:-1] > 8.0
        nxt_after_big = sizes[1:][big]
        other = sizes[1:][~big]
        print(f"\n{key}: lag-1 log-size corr {corr1:+.2f} | "
              f"next-spike mean size after BIG={np.mean(nxt_after_big) if len(nxt_after_big) else float('nan'):.2f}x "
              f"(n={len(nxt_after_big)}) vs after normal="
              f"{np.mean(other) if len(other) else float('nan'):.2f}x (n={len(other)})")


# ----------------------------------------------------------------------
# D. ENTRY-BAR DELAY (faithful param runner, no cluster)
# ----------------------------------------------------------------------
def partial_trade_e(df, sym, i, hold, e_off):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + e_off
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], by_key(sym)["_floor"])
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, rA = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, hold, n)
    return {"pnl": (pA - entry) * (1 if is_crash else -1), "nhalves": 1}


def test_d():
    print("\n" + "=" * W)
    print("D. ENTRY-BAR DELAY — enter 1/2/3 bars after the spike (full size, cd=0)")
    print("=" * W)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        idx = np.where(E.spike_rows(df, key))[0]
        n = len(df)
        print(f"\n{key}:")
        for e_off in (1, 2, 3):
            trs = []
            for i in idx:
                if i + e_off + 1 >= n:
                    break
                tr = partial_trade_e(df, key, i, 2, e_off)
                if tr is not None:
                    trs.append(tr)
            print(f"  entry bar +{e_off}: {fmt(E.outcome_stats(trs))}")


# ----------------------------------------------------------------------
# E. SPIKE BODY RATIO
# ----------------------------------------------------------------------
def test_e():
    print("\n" + "=" * W)
    print("E. SPIKE BODY RATIO — |close-open|/range of the spike bar vs fade outcome")
    print("=" * W)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        opens = df["open"].values
        closes = df["close"].values
        rng = df["range"].values
        body = np.abs(closes - opens) / np.maximum(rng, 1e-9)
        cfg = dict(by_key(key)["verified"])
        trs = trades_with(key, cfg, df)
        if not trs:
            continue
        vals = [body[t["entry_row"]] for t in trs]
        print(f"\n{key} overall: {fmt(E.outcome_stats(trs))}")
        for label, st in bucket_stats(trs, vals, 3, "body"):
            if st and st["n"]:
                print(f"  {label:<12} {fmt(st)}")


def oos_check(key, cfg, select_fn, tag):
    """Run cfg on both halves restricted to select_fn(trades), report."""
    df = E.load_prep(key)
    n = len(df)
    cut = n // 2
    rows = []
    for name, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
        d = d.reset_index(drop=True)
        trs = trades_with(key, cfg, d)
        sel = [t for t in trs if select_fn(t, d)]
        rows.append((name, E.outcome_stats(sel)))
    print(f"  OOS {tag}:")
    for name, st in rows:
        print(f"    {name}: {fmt(st)}")


if __name__ == "__main__":
    # floor needed by partial_trade_e
    from config import BROKER_FLOOR
    for s in SYMBOLS:
        s["_floor"] = BROKER_FLOOR.get(s["key"], 1.0)
    test_a()
    test_c()
    test_d()
    test_e()
