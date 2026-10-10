#!/usr/bin/env python3
"""
FLIP-SPIKE TEST — ride the drift, then flip and wait for the spike
==================================================================
User's idea (CRASH framing; boom mirrored):
  1. After a crash spike, price updrifts (the fade's trade). Ride it for
     D bars.
  2. At bar i+D (entry close), FLIP SHORT and hold up to H bars waiting
     for the next crash spike. SL above at max(median, broker floor)
     caps the no-spike loss. If the spike fires, the down-move gaps
     THROUGH the TP in our favor (size-cap finding: up moves never spike,
     so the short's SL fills at level, not gapped).

This is the delayed/long-hold/capped-loss version of the chase test —
exactly the refinements the user asked for. Same corrected accounting:
SL/TP at level except gap-through on adverse spike bars (for the short,
adverse = up bars, which are size-capped -> no gap fill needed).

Also measures the core timing question directly:
  % of inter-spike gaps where the drift exceeds the SL distance BEFORE
  the next spike arrives (= the rate at which the flip gets stopped out
  before it can win).
"""
import pandas as pd
import numpy as np
import sys
sys.path.insert(0, ".")
from partial_m1_corrected import load, prep, arrays, stats, pipeline, BROKER_FLOOR, SPIKE_MULT


def flip_trade(df, sym, i, D, H, tp_mult=2.0, drift_ok_only=False):
    """Enter at close of bar i+D, betting on the NEXT spike.
    crash: SHORT (wait for the down-spike), SL above entry.
    boom:  LONG  (wait for the up-spike),   SL below entry."""
    n = len(df)
    closes = df["close"].values
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    e = i + D
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    if is_crash:  # SHORT
        sl_level = entry + sl_dist
        tp_level = entry - tp_mult * sl_dist
    else:         # LONG
        sl_level = entry - sl_dist
        tp_level = entry + tp_mult * sl_dist

    if drift_ok_only:
        # require drift in the fade direction before flipping
        if is_crash and closes[e] <= closes[i + 1]:
            return None
        if not is_crash and closes[e] >= closes[i + 1]:
            return None

    for j in range(e + 1, min(e + 1 + H, n)):
        if is_crash:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        else:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        if hit_sl:
            # gap-through on an adverse MAIN-direction spike bar
            adv_j = (closes[j] < opens[j]) if is_crash else (closes[j] > opens[j])
            if adv_j and rng[j] > SPIKE_MULT * med[j]:
                fill = highs[j] if is_crash else lows[j]
            else:
                fill = sl_level
            return (entry - fill) if is_crash else (fill - entry), "sl"
        if hit_tp:
            return (entry - tp_level) if is_crash else (tp_level - entry), "tp"
    p = closes[min(e + H, n - 1)]
    return (entry - p) if is_crash else (p - entry), "timeout"


def ride_long(df, sym, i, D):
    """Long drift-ride from close[i+1] to close[i+D] (or SL below if hit)."""
    n = len(df)
    closes = df["close"].values
    lows = df["high"].values if False else df["low"].values
    highs = df["low"].values if False else df["high"].values
    rng = df["range"].values
    med = df["median_range"].values
    e0 = i + 1
    e = i + D
    if e >= n:
        return None
    entry = closes[e0]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    sl_level = entry - sl_dist
    for j in range(e0 + 1, e + 1):
        if lows[j] <= sl_level:
            # gap-through only on a main-direction (crash down) spike bar
            adv_j = closes[j] < df["open"].values[j]
            if adv_j and rng[j] > SPIKE_MULT * med[j]:
                return lows[j] - entry, "sl"
            return sl_level - entry, "sl"
    return closes[e] - entry, "ride"


def gap_stats(df, sym):
    """Bars between consecutive qualifying spikes; % of gaps where drift
    exceeds the SL distance before the next spike."""
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    idx = np.where((rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2))[0]
    gaps = np.diff(idx)
    closes = df["close"].values
    floor = BROKER_FLOOR.get(sym, 1.0)
    med_all = med[idx[:-1]]
    sl_dists = np.maximum(med_all, floor)
    # adverse-direction excursion before next spike:
    #   crash (short waits): how far UP can price run (vs SL above)
    #   boom  (long  waits): how far DOWN can price run (vs SL below)
    drift_over_sl = 0
    total = len(idx) - 1
    for k in range(total):
        a, b = idx[k], idx[k + 1]
        seg = closes[a + 1:b + 1]
        if len(seg) == 0:
            continue
        if is_crash:
            drift = seg.max() - closes[a]
        else:
            drift = closes[a] - seg.min()
        if drift > sl_dists[k]:
            drift_over_sl += 1
    return gaps, 100.0 * drift_over_sl / max(total, 1)


def run_flips(df, sym, D, H, tp_mult=2.0, drift_ok_only=False):
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    idx = np.where((rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2))[0]
    trs = []
    for i in idx:
        tr = flip_trade(df, sym, i, D, H, tp_mult, drift_ok_only)
        if tr is None:
            continue
        pnl, r = tr
        trs.append({"pnl": pnl, "nhalves": 1, "reason": r})
    return trs


def row(label, s):
    if s is None:
        print(f"  {label:<44}  none")
        return
    reasons = {}
    for t in s.get("reasons", []):
        reasons[t] = reasons.get(t, 0) + 1
    rstr = " ".join(f"{k}:{v}" for k, v in sorted(reasons.items()))
    print(f"  {label:<44}{s['n']:>5}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}  {rstr}")


def stat_full(trs):
    s = stats(trs)
    if s is None:
        return None
    s["reasons"] = [t["reason"] for t in trs]
    return s


if __name__ == "__main__":
    SYMS = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]
    print("=" * 116)
    print("FLIP-SPIKE: gap structure + short-after-D-bar sweep (crash) / long-after-D-bar (boom)")
    print("=" * 116)
    for sym in SYMS:
        df = prep(load(sym))
        gaps, drift_pct = gap_stats(df, sym)
        med_gap = np.median(gaps)
        is_crash = "crash" in sym
        side = "SHORT (crash, wait for down-spike)" if is_crash else "LONG (boom, wait for up-spike)"
        print(f"\n--- {sym.upper()} | gaps: median={med_gap:.0f} bars, "
              f"drift exceeds SL distance before next spike: {drift_pct:.0f}% of gaps ---")
        print(f"  flip side: {side}")
        best = None
        for D in [1, 2, 3, 5, 7]:
            for H in [5, 10, 20]:
                for dok in [False, True]:
                    tr = run_flips(df, sym, D, H, 2.0, dok)
                    s = stat_full(tr)
                    label = f"D={D} H={H} driftOK={int(dok)}"
                    row(label, s)
                    if s and (best is None or s["pf"] > best[1]["pf"]):
                        best = ((D, H, dok), s)
        if best:
            print(f"  >> best flip: D={best[0][0]} H={best[0][1]} driftOK={int(best[0][2])} PF={best[1]['pf']:.2f}")