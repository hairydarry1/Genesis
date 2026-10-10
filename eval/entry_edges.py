#!/usr/bin/env python3
"""
ENTRY-EDGE SCAN — corrected M1 engine
======================================
Wins are timeout-driven: at hold 2-3, most fade PnL = the drift from entry to
the timeout close. So entry quality = P(strong reliable drift after THIS
spike). Scan which observable spike features predict it:

  A. SPIKE SIZE   : adverse bar range / median (3-5x, 5-8x, 8-12x, 12x+)
  C. ENTRY BAR    : how the bar we enter on closes (weak/mid/strong reversal)
  D. DRIFT RUN    : net up-move in the 10 bars before the crash
  E. BIG-CLUSTER  : this spike >=8x AND another >=8x within the prior 5 bars
  F. GAP SINCE    : bars since the previous spike (1-5 / 6-15 / 16-30 / 30+)

Bucket stats use FULL-size hold=2 (pure entry-quality, no protection noise).
The winner is then re-checked through the real PARTIAL+CD3 pipeline.
"""
import sys
sys.path.insert(0, "eval")
import numpy as np
from defence_axes import load, prep, spike_info, trade, stats, pipeline
import pandas as pd

def bucket_report(df, sym, buckets, hold=2):
    """buckets: list of (label, mask over spike positions)."""
    idx, rng, med = spike_info(df, sym)
    closes = df["close"].values
    n = len(df)
    print(f"\n  --- {sym.upper()} entry buckets (FULL size, hold={hold}) ---")
    print(f"  {'bucket':<20}{'n':>6}{'WR%':>7}{'PF':>7}{'avg':>8}  drift(bar {hold})")
    for label, mask in buckets:
        sel = idx[mask]
        res = []
        for i in sel:
            tr = trade(df, sym, i, hold, 2.0, 0.0)
            if tr is None:
                continue
            e = i + 1
            drift = (closes[min(e + hold, n - 1)] - closes[e]) if "crash" in sym \
                    else (closes[e] - closes[min(e + hold, n - 1)])
            res.append((tr["pnl"], drift))
        if not res:
            continue
        pnls = np.array([r[0] for r in res])
        drifts = np.array([r[1] for r in res])
        wins = pnls[pnls > 0]
        pf = wins.sum() / abs(pnls[pnls <= 0].sum()) if (pnls <= 0).any() else 999
        print(f"  {label:<20}{len(pnls):>6}{len(wins)/len(pnls)*100:>6.1f}%"
              f"{pf:>7.2f}{pnls.mean():>+8.3f}  {drifts.mean():+.3f}")

def make_buckets(df, sym, idx, rng, med):
    closes = df["close"].values; opens = df["open"].values
    highs = df["high"].values; lows = df["low"].values
    n = len(df)
    ratio = rng / med
    A = [("size 3-5x", ratio[idx] < 5), ("size 5-8x", (ratio[idx] >= 5) & (ratio[idx] < 8)),
         ("size 8-12x", (ratio[idx] >= 8) & (ratio[idx] < 12)), ("size 12x+", ratio[idx] >= 12)]
    # C: close position of entry bar (bar after spike); crash wants strong UP close
    e_idx = idx + 1
    rng1 = np.where(e_idx < n, highs[e_idx] - lows[e_idx], np.nan)
    cp = np.where(e_idx < n, (closes[e_idx] - lows[e_idx]) / np.maximum(rng1, 1e-12), np.nan)
    is_crash = "crash" in sym
    # for crash, up-close is good; for boom, down-close is good
    fav = cp if is_crash else (1 - cp)
    C = [("entrybar wk<0.33", fav < 0.33), ("entrybar mid", (fav >= 0.33) & (fav <= 0.66)),
         ("entrybar str>0.66", fav > 0.66)]
    # D: drift run over 10 bars before the spike (excluding the spike bar)
    run = np.full(len(idx), np.nan)
    for k, i in enumerate(idx):
        if i >= 10:
            run[k] = (closes[i - 1] - closes[i - 10]) if is_crash else (closes[i - 10] - closes[i - 1])
    rq = np.nanpercentile(run, [33, 66])
    D = [("run lo", run <= rq[0]), ("run mid", (run > rq[0]) & (run <= rq[1])), ("run hi", run > rq[1])]
    # E: big-cluster gate (>=8x now and >=1 >=8x in prior 5 bars)
    big = ratio[idx] >= 8
    e_big = np.zeros(len(idx), dtype=bool)
    for k in range(len(idx)):
        if big[k]:
            j = k - 1
            while j >= 0 and idx[k] - idx[j] <= 5:
                if big[j]:
                    e_big[k] = True
                    break
                j -= 1
    E = [("big+big5 (cluster)", e_big), ("NOT cluster", big & ~e_big)]
    # F: gap since previous spike
    gap = np.full(len(idx), 999)
    gap[1:] = idx[1:] - idx[:-1]
    F = [("gap 1-5b", gap <= 5), ("gap 6-15b", (gap > 5) & (gap <= 15)),
         ("gap 16-30b", (gap > 15) & (gap <= 30)), ("gap 30b+", gap > 30)]
    return [("A", A), ("C", C), ("D", D), ("E", E), ("F", F)]

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = prep(load(sym))
        idx, rng, med = spike_info(df, sym)
        for gname, buckets in make_buckets(df, sym, idx, rng, med):
            bucket_report(df, sym, buckets)
        # base full-size reference
        base = [t["pnl"] for t in [trade(df, sym, i, 2, 2.0, 0.0) for i in idx] if t is not None]
        pnls = np.array(base)
        wins = pnls[pnls > 0]
        pf = wins.sum() / abs(pnls[pnls <= 0].sum()) if (pnls <= 0).any() else 999
        print(f"  --- {sym.upper()} base FULL hold=2: n={len(pnls)} WR={len(wins)/len(pnls)*100:.1f}% "
              f"PF={pf:.2f} avg={pnls.mean():+.3f} ---")
