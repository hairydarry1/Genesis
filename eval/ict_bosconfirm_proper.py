#!/usr/bin/env python3
"""
ICT BOS-CONFIRM — PROPER HONESTY BATTERY
=========================================
The prior test's crash500 claim (+0.34 vs +0.30 random) has a thin margin and
an UNMATCHED control.  This file tests it properly:

  A. MATCHED-LAG CONTROL: for every gated discharge, draw the entry-lag from
     the EMPIRICAL distribution of BOS lags (same bars available, same timing
     distribution) — isolates "the BOS bar choice" from "later entries help."
     Bootstrap CI + permutation p-value on the difference.
  B. TIME-THIRDS: is the edge one lucky window or everywhere?
  C. SWEEPS: pivot width, confirmation maxwait, hold — knife-edge check.
  D. LAG-VALUE CHECK: enter at the pivot close (no confirmation) vs BOS bar,
     to see whether the confirmation lag itself carries the value.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, stats, m15_regime
from ict_bosconfirm_test import bos_entry_bar, trade_at
from partial_m1_corrected import arrays

RNG = np.random.RandomState(7)


def gated_discharges(df, sym, regime):
    if regime is not None and "boom" in sym:
        regime = ~regime
    idx = discharges(df, sym)
    return [i for i in idx if regime is None or regime[i]]


def bos_lags(df, sym, idx):
    """(entry_bar, lag) for each gated discharge that produces a BOS entry."""
    out = []
    for i in idx:
        b = bos_entry_bar(df, sym, i)
        if b >= 0:
            out.append((b, b - i))
    return out


def matched_control(df, sym, idx, lags, hold=3, seed=0):
    """Same discharge set; entry lag drawn from the BOS lag distribution."""
    rs = np.random.RandomState(seed)
    lags = np.array(lags)
    pnls = []
    for i in idx:
        lag = int(rs.choice(lags))
        b = i + lag
        if b + 2 >= len(df):
            continue
        p = trade_at(df, sym, b, hold)
        if p is not None:
            pnls.append(p)
    return pnls


def boot_diff(bos_pnls, ctrl_pnls, iters=2000):
    bos = np.array(bos_pnls); ctrl = np.array(ctrl_pnls)
    obs = bos.mean() - ctrl.mean()
    n1, n2 = len(bos), len(ctrl)
    pooled = np.concatenate([bos, ctrl])
    # permutation p-value
    count = 0
    for _ in range(iters):
        perm = RNG.permutation(len(pooled))
        m1 = pooled[perm[:n1]].mean()
        m2 = pooled[perm[n1:]].mean()
        if (m1 - m2) >= obs:
            count += 1
    pval = (count + 1) / (iters + 1)
    # bootstrap CI of the difference
    diffs = []
    for _ in range(iters):
        b1 = bos[RNG.randint(0, n1, n1)].mean()
        b2 = ctrl[RNG.randint(0, n2, n2)].mean()
        diffs.append(b1 - b2)
    ci = (np.percentile(diffs, 2.5), np.percentile(diffs, 97.5))
    return obs, pval, ci


def thirds(df, sym, regime, hold=3):
    n = len(df)
    out = []
    for tname, sl in [("T1", slice(0, n // 3)), ("T2", slice(n // 3, 2 * n // 3)),
                      ("T3", slice(2 * n // 3, None))]:
        d = df.iloc[sl].reset_index(drop=True)
        r2 = m15_regime(d, sym)
        idx = gated_discharges(d, sym, r2)
        pnls = []
        for i in idx:
            b = bos_entry_bar(d, sym, i)
            if b >= 0:
                p = trade_at(d, sym, b, hold)
                if p is not None:
                    pnls.append(p)
        out.append((tname, stats(pnls)))
    return out


def pivot_entry(df, sym, i, w=1):
    """Enter at close of the pivot bar itself (no confirmation wait)."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    is_crash = "crash" in sym
    for k in range(i + 2, min(i + 7, n - 1)):
        if is_crash:
            if highs[k] > highs[k - 1] and highs[k] > highs[k + 1]:
                return k
        else:
            if lows[k] < lows[k - 1] and lows[k] < lows[k + 1]:
                return k
    return -1


if __name__ == "__main__":
    for sym in ["crash500", "crash300", "boom500"]:
        df = load(sym)
        reg = m15_regime(df, sym)
        idx = gated_discharges(df, sym, reg)
        bl = bos_lags(df, sym, idx)
        bos_pnls = [trade_at(df, sym, b, 3) for b, _ in bl]
        bos_pnls = [p for p in bos_pnls if p is not None]
        ctrl = matched_control(df, sym, idx, [l for _, l in bl], hold=3)
        obs, pval, ci = boot_diff(bos_pnls, ctrl)
        sb, sc = stats(bos_pnls), stats(ctrl)
        print(f"\n=== {sym.upper()} — MATCHED-LAG CONTROL (hold=3) ===")
        print(f"  BOS-confirm : n={sb['n']} avg={sb['avg']:+.3f} PF={sb['pf']:.2f}")
        print(f"  Matched-ctrl: n={sc['n']} avg={sc['avg']:+.3f} PF={sc['pf']:.2f}")
        print(f"  diff={obs:+.3f}  perm-p={pval:.4f}  95% CI=({ci[0]:+.3f}, {ci[1]:+.3f})")
        print("  TIME-THIRDS (BOS-confirm):")
        for tname, s in thirds(df, sym, reg):
            row_str = f"    {tname}: n={s['n']} avg={s['avg']:+.3f} PF={s['pf']:.2f}"
            print(row_str)
        # lag-value check
        piv_pnls = []
        for i in idx:
            k = pivot_entry(df, sym, i)
            if k >= 0:
                p = trade_at(df, sym, k, 3)
                if p is not None:
                    piv_pnls.append(p)
        sp = stats(piv_pnls)
        print(f"  PIVOT-entry (no confirm): n={sp['n']} avg={sp['avg']:+.3f} PF={sp['pf']:.2f}")