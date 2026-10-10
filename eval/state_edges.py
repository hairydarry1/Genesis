#!/usr/bin/env python3
"""
STATE-EDGE HUNTER
=================
The cluster finding said: BIG spikes in tight groups are followed by longer calm
(CRASH300). That is one *state-dependent* edge. This script hunts for more:
conditions observable at/after a spike that change what happens NEXT.

Concepts tested (crash symbols, fade = the tradeable direction):
  A) RECHARGE  - does a BIG spike (8x+) lengthen the gap to the NEXT big spike
                 vs a small spike? (single-spike version of the cluster effect)
  B) DRIFT-STRETCH - does a long updrift between crashes predict a bigger next
                 crash (snap) or a sooner one?
  C) SQUEEZE   - are bars right before a spike compressed? Does P(next bar is
                 a spike) rise after quiet bars?
  D) UP-STREAK - does the # of consecutive up-bars before the crash change how
                 long the post-crash calm lasts (fade survival)?
"""
import pandas as pd
import numpy as np

def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def prep(df):
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    df["dir"] = (df["close"] > df["open"]).astype(int)   # 1 = up bar, 0 = down
    df["upmove"] = (df["close"] - df["open"]).clip(lower=0)
    return df

def spike_idx(df, mult):
    rng = df["range"].values
    med = df["median_range"].values
    adv = (df["close"] < df["open"]).values
    m = (rng > mult * med) & adv
    m = m & np.isfinite(med)
    return np.where(m)[0]

# ---------------------------------------------------------------- A) RECHARGE
def recharge(df, sym):
    print(f"\n=== A) RECHARGE: {sym.upper()} — next-gap after spike by spike SIZE ===")
    idx_all = spike_idx(df, 3.0)
    rng = df["range"].values
    med = df["median_range"].values
    ratio = rng / med
    rows = []
    for i in idx_all:
        # gap (bars) to next crash spike (any >=3x)
        nxt = idx_all[idx_all > i]
        if len(nxt) == 0:
            continue
        gap = nxt[0] - i
        # gap to next BIG (>=8x) crash spike
        nxt_big = idx_all[(idx_all > i) & (ratio[idx_all] >= 8.0)]
        gap_big = nxt_big[0] - i if len(nxt_big) else np.nan
        rows.append((ratio[i], gap, gap_big))
    a = np.array([r for r in rows if np.isfinite(r[2])])
    if len(a) == 0:
        print("  no data"); return
    sizes = [(3, 5), (5, 8), (8, 12), (12, 999)]
    print(f"  {'spike size':<14}{'n':>6}{'med gap next':>14}{'med gap next BIG':>18}"
          f"{'P(next BIG <7b)':>16}")
    for lo, hi in sizes:
        sel = a[(a[:, 0] >= lo) & (a[:, 0] < hi)]
        if len(sel) == 0:
            continue
        p7 = (sel[:, 2] < 7).mean() * 100
        print(f"  {f'{lo}-{hi}x':<14}{len(sel):>6}{np.median(sel[:,1]):>14.1f}"
              f"{np.median(sel[:,2]):>18.1f}{p7:>15.1f}%")

# ------------------------------------------------- B) DRIFT STRETCH
def drift_stretch(df, sym):
    print(f"\n=== B) DRIFT-STRETCH: {sym.upper()} — updrift since last crash vs next crash ===")
    idx = spike_idx(df, 3.0)
    if len(idx) < 2:
        print("  no data"); return
    closes = df["close"].values
    upmove = df["upmove"].values
    rows = []
    for k in range(1, len(idx)):
        i0, i1 = idx[k - 1], idx[k]
        if i1 - i0 > 500:   # skip huge gaps (data holes)
            continue
        # low point after previous crash (approx min of closes between)
        drift = closes[i1] - closes[i0]          # net move since last crash
        upsum = upmove[i0:i1].sum()              # total up-movement
        # next crash size + next gap
        rows.append((drift, upsum, df["range"].values[i1], idx[k + 1] - i1 if k + 1 < len(idx) else np.nan))
    a = np.array([r for r in rows if np.isfinite(r[3])])
    # bucket by net drift
    qs = np.percentile(a[:, 0], [0, 33, 66, 100])
    print(f"  {'drift since last crash':<26}{'n':>6}{'median next size':>18}{'median next gap':>17}")
    for k in range(3):
        sel = a[(a[:, 0] >= qs[k]) & (a[:, 0] < qs[k + 1])]
        if len(sel) == 0:
            continue
        print(f"  {f'{qs[k]:+.2f} to {qs[k+1]:+.2f}':<26}{len(sel):>6}"
              f"{np.median(sel[:,2]):>18.2f}{np.median(sel[:,3]):>17.1f}")
    # correlation net drift -> next size
    corr = np.corrcoef(a[:, 0], a[:, 2])[0, 1]
    print(f"  corr(drift, next spike size) = {corr:+.3f}   (n={len(a)})")

# ------------------------------------------------- C) SQUEEZE
def squeeze(df, sym):
    print(f"\n=== C) SQUEEZE: {sym.upper()} — are pre-spike bars compressed? ===")
    rng = df["range"].values
    med = df["median_range"].values
    idx = spike_idx(df, 3.0)
    idx = idx[idx > 10]
    if len(idx) < 50:
        print("  no data"); return
    # baseline: distribution of the median of the prior-5-bar ranges (all bars)
    all_prev5 = np.array([np.median(rng[max(0, i - 5):i]) / med[i] for i in range(30, len(df))
                          if np.isfinite(med[i])])
    sp_prev5 = np.array([np.median(rng[i - 5:i]) / med[i] for i in idx])
    # last bar before spike
    all_last = np.array([rng[i - 1] / med[i] for i in range(30, len(df)) if np.isfinite(med[i])])
    sp_last = np.array([rng[i - 1] / med[i] for i in idx])
    def pct(vals, arr):
        return (arr < vals).mean() * 100
    print(f"  {'measure':<24}{'baseline median':>16}{'pre-spike median':>18}")
    print(f"  {'prev-5-bar range/median':<24}{np.median(all_prev5):>16.2f}"
          f"{np.median(sp_prev5):>18.2f}")
    print(f"  {'bar-before range/median':<24}{np.median(all_last):>16.2f}"
          f"{np.median(sp_last):>18.2f}")
    print(f"  pre-spike prev5 is below baseline in {pct(np.median(sp_prev5), all_prev5):.1f}% of bars"
          f"  (100 = spike always after quiet, 50 = random)")

# ------------------------------------------------- D) UP-STREAK
def up_streak(df, sym):
    print(f"\n=== D) UP-STREAK: {sym.upper()} — bars of updrift before crash vs calm after ===")
    dirv = df["dir"].values
    idx = spike_idx(df, 3.0)
    rows = []
    for i in idx:
        if i < 2:
            continue
        # streak of up bars immediately before the crash bar
        streak = 0
        j = i - 1
        while j >= 0 and dirv[j] == 1:
            streak += 1
            j -= 1
        # calm after: gap to next crash spike
        nxt = idx[idx > i]
        if len(nxt) == 0:
            continue
        gap = nxt[0] - i
        rows.append((streak, gap))
    a = np.array(rows)
    print(f"  {'up-bars before crash':<22}{'n':>6}{'med calm after (bars)':>22}{'P(calm>7b)':>12}")
    for lo, hi in [(0, 1), (1, 3), (3, 6), (6, 999)]:
        sel = a[(a[:, 0] >= lo) & (a[:, 0] < hi)]
        if len(sel) == 0:
            continue
        p7 = (sel[:, 1] > 7).mean() * 100
        print(f"  {f'{lo}-{hi} up bars':<22}{len(sel):>6}{np.median(sel[:,1]):>22.1f}{p7:>11.1f}%")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000"]:
        df = prep(load(sym))
        recharge(df, sym)
        drift_stretch(df, sym)
        squeeze(df, sym)
        up_streak(df, sym)
