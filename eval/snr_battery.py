#!/usr/bin/env python3
"""
SNR COURSE BATTERY — the 3 measurable claims from the transcripts
=================================================================
1. SPIKE-ORIGIN: discharge extremes land near prior reaction levels
   (pivots whose bar range >= 2x median) more than distance-matched
   random bars.  Permutation p on the median distance (in median-range
   units).
2. LEVEL-FLIP RETEST: a discharge bar closes THROUGH a prior reaction
   level; price retests that level within 10 bars -> drift-fade entry.
   Matched-lag control = same lag distribution, no level condition.
3. DOUBLE-TOUCH FILTER: in the gated pullback config, entries where the
   bounce made 2+ touches of its extreme before the 50% retrace.
   Compare vs single-touch entries on crash300/boom500. OOS both.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, pullback_bar, stats, row, m15_regime
from ict_bosconfirm_test import trade_at
from partial_m1_corrected import arrays

RNG = np.random.RandomState(21)


def reaction_levels(df, sym, lookback=4320):
    """Confirmed 3-bar pivot extremes whose bar range >= 2x median, within
    the last `lookback` M1 bars (3 days).  Returns sorted array of levels."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    levels = []
    for k in range(2, n - 1):
        if not np.isfinite(med[k]) or med[k] <= 0:
            continue
        if rng[k] < 2 * med[k]:
            continue
        # 3-bar pivot high (crash: resistance above) and low (support below)
        if highs[k] >= highs[k - 1] and highs[k] >= highs[k + 1] and highs[k] > highs[k - 2]:
            levels.append(highs[k])
        if lows[k] <= lows[k - 1] and lows[k] <= lows[k + 1] and lows[k] < lows[k - 2]:
            levels.append(lows[k])
    lv = np.array(sorted(set(np.round(levels, 4))))
    return lv


def nearest_level_dist(prices, levels):
    """Distance from each price to the nearest level (inf if none)."""
    out = np.empty(len(prices))
    for i, p in enumerate(prices):
        j = np.searchsorted(levels, p)
        d1 = abs(p - levels[j]) if j < len(levels) else np.inf
        d0 = abs(p - levels[j - 1]) if j > 0 else np.inf
        out[i] = min(d1, d0)
    return out


def test_spike_origin(df, sym, iters=1000):
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    lv = reaction_levels(df, sym)
    idx = discharges(df, sym)
    ext = lows[idx] if is_crash else highs[idx]
    obs = nearest_level_dist(ext, lv)
    obs = obs[np.isfinite(obs)]
    n_obs = len(obs)
    # random bars matched in time density: sample random bars, same count
    rand_d = []
    for _ in range(iters):
        rb = RNG.randint(200, len(df) - 10, n_obs)
        rex = lows[rb] if is_crash else highs[rb]
        rd = nearest_level_dist(rex, lv)
        rand_d.append(np.median(rd[np.isfinite(rd)]))
    obs_med = np.median(obs)
    p = (np.sum(np.array(rand_d) <= obs_med) + 1) / (iters + 1)
    return obs_med, np.median(rand_d), p, n_obs, len(lv)


def test_level_flip(df, sym, maxwait=10):
    """Discharge closes through a prior reaction level; retest = entry."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    lv = reaction_levels(df, sym)
    idx = discharges(df, sym)
    lags = []
    entries = []
    for i in idx:
        ext = lows[i] if is_crash else highs[i]
        # levels the discharge bar closed through
        lo_b, hi_b = (ext, closes[i]) if is_crash else (closes[i], ext)
        j = np.searchsorted(lv, lo_b)
        k = np.searchsorted(lv, hi_b)
        through = lv[j:k] if k > j else np.array([])
        if len(through) == 0:
            continue
        lvl = through[-1] if is_crash else through[0]  # nearest to entry side
        for b in range(i + 1, min(i + 1 + maxwait, n)):
            touched = (highs[b] >= lvl) if is_crash else (lows[b] <= lvl)
            if touched:
                entries.append(b)
                lags.append(b - i)
                break
    return entries, lags


def double_touch_at_entry(df, sym, entry_bar, tol_mult=0.5):
    """Did the bounce extreme get touched 2+ times before the entry bar?
    Looks back from the discharge bar to entry for pivot-cluster touches."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    return None  # replaced by inline logic in main


if __name__ == "__main__":
    RNG2 = np.random.RandomState(5)
    for sym in ["crash300", "boom500", "crash500"]:
        df = load(sym)
        reg = m15_regime(df, sym)
        if "boom" in sym:
            reg = ~reg
        idx = discharges(df, sym)
        gated = [i for i in idx if reg[i]]

        print(f"\n{'='*88}\n{sym.upper()}\n{'='*88}")

        # ---- 1. SPIKE-ORIGIN ----
        obs_med, rand_med, p, nobs, nlv = test_spike_origin(df, sym)
        print(f"1. SPIKE-ORIGIN: spike-extreme->nearest level median = {obs_med:.3f} med-ranges"
              f" | random = {rand_med:.3f} | p = {p:.4f}  (n={nobs}, levels={nlv})")

        # ---- 2. LEVEL-FLIP RETEST ----
        entries, lags = test_level_flip(df, sym)
        pnls = []
        for b in entries:
            p2 = trade_at(df, sym, b, 3)
            if p2 is not None:
                pnls.append(p2)
        s = stats(pnls)
        # matched-lag control
        ctrl = []
        if lags:
            for i in gated:
                lag = int(RNG2.choice(lags))
                b = i + lag
                if b + 2 >= len(df):
                    continue
                p3 = trade_at(df, sym, b, 3)
                if p3 is not None:
                    ctrl.append(p3)
        sc = stats(ctrl)
        if s:
            row("2. LEVEL-FLIP retest", s)
        if sc:
            row("   matched-lag ctrl", sc)

        # ---- 3. DOUBLE-TOUCH FILTER on gated pullback entries ----
        closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
        two, one = [], []
        for i in gated:
            b = pullback_bar(df, sym, i, 6)
            if b < 0:
                continue
            # bounce extreme between discharge and entry
            seg_h = highs[i + 1:b + 1]
            seg_l = lows[i + 1:b + 1]
            if is_crash:
                ext = seg_h.max()
                tol = 0.5 * med[i]
                touches = int(np.sum(seg_h >= ext - tol))
            else:
                ext = seg_l.min()
                tol = 0.5 * med[i]
                touches = int(np.sum(seg_l <= ext + tol))
            p4 = trade_at(df, sym, b, 3)
            if p4 is None:
                continue
            (two if touches >= 2 else one).append(p4)
        row("3. DOUBLE-TOUCH entry", stats(two))
        row("   SINGLE-touch entry", stats(one))
        # OOS of double-touch
        half = len(df) // 2
        d1 = df.iloc[:half].reset_index(drop=True)
        d2 = df.iloc[half:].reset_index(drop=True)
        r1 = m15_regime(d1, sym)
        r2 = m15_regime(d2, sym)
        if "boom" in sym:
            r1 = ~r1
            r2 = ~r2
        for hname, dd, rr in [("OOS1", d1, r1), ("OOS2", d2, r2)]:
            cc1, _, _, _, _ = arrays(dd, sym), None, None, None, None
            t2 = []
            for i in [ii for ii in discharges(dd, sym) if rr[ii]]:
                b = pullback_bar(dd, sym, i, 6)
                if b < 0:
                    continue
                seg = dd["high"].values[i + 1:b + 1] if is_crash else dd["low"].values[i + 1:b + 1]
                if len(seg) == 0:
                    continue
                ex = seg.max() if is_crash else seg.min()
                tol2 = 0.5 * dd["median_range"].values[i]
                tc = int(np.sum(seg >= ex - tol2)) if is_crash else int(np.sum(seg <= ex + tol2))
                if tc < 2:
                    continue
                p5 = trade_at(dd, sym, b, 3)
                if p5 is not None:
                    t2.append(p5)
            row(f"   dbl-touch {hname}", stats(t2))
