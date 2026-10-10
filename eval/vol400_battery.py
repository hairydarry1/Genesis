#!/usr/bin/env python3
"""
VOL400 (Spot Up - Volatility Up Index) BATTERY
==============================================
Tests whether anything from the Boom/Crash playbook transfers to Deriv's
over-400 vol product (the "Spot Up - Volatility Up Index"):
  A. Structure: are retracement bars size-capped? drift after discharge?
  B. Tick hazard: does P(discharge) rise with ticks-since-last?
  C. Fade (short the up-spike) vs Chase (long the up-spike) EV on the
     corrected engine, with the real broker floor (10.00 pts).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import partial_m1_corrected as eng

SYM = "vol400"
FLOOR = 10.0
SPIKE = 3.0
DFILE = Path("../data/vol400_Spot_Up__Volatility_Up_Index_1min.parquet")
TICKFILE = Path("../data/ticks_vol400_Spot_Up__Volatility_Up_Index.npy")


def load():
    df = pd.read_parquet(DFILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def structure(df):
    rng = df["range"].values
    med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    dn = ~up
    ok = np.isfinite(med) & (med > 0)
    upm = np.nanmedian(rng[up & ok]); dnm = np.nanmedian(rng[dn & ok])
    print(f"\n=== A. STRUCTURE ===")
    print(f"  bars: {len(df):,}  up bars: {up.sum():,}  down bars: {dn.sum():,}")
    print(f"  median bar range  up={upm:.4f}  down={dnm:.4f}  ratio={upm/max(dnm,1e-9):.1f}x")
    for thr in (1.5, 2.0, 3.0):
        upb = (rng > thr * np.nanmedian(rng)) & up & ok
        dnb = (rng > thr * np.nanmedian(rng)) & dn & ok
        print(f"  bars > {thr:.1f}x all-median: up={upb.sum():,}  down={dnb.sum():,}")
    # drift after big up bars (discharge direction) vs after big down bars
    big = np.where((rng > 2.5 * med) & ok)[0]
    upbig = [i for i in big if up[i]]
    dnbig = [i for i in big if dn[i]]
    closes = df["close"].values
    for name, idxs in (("after UP(discharge) bars", upbig), ("after DOWN bars", dnbig)):
        moves = {h: [] for h in (1, 3, 5)}
        for i in idxs:
            for h in (1, 3, 5):
                j = i + h
                if j < len(closes):
                    moves[h].append(closes[j] - closes[i])
        line = "  " + name + ": "
        line += " | ".join(f"+{h}: {np.mean(moves[h]):+.3f}" for h in (1, 3, 5))
        print(line)


def tick_hazard():
    px = np.load(TICKFILE)
    d = np.diff(px)
    up_t = d > 0
    # discharge = big single-tick down moves (like crash) OR check both
    thr = np.percentile(np.abs(d), 99.9)
    print(f"\n=== B. TICK HAZARD ===")
    print(f"  ticks: {len(px):,}  up-ticks: {up_t.sum():,}  down-ticks: {(~up_t).sum():,}")
    print(f"  max single-tick move: +{d.max():.3f} / {d.min():.3f}")
    # count ticks >= thr in each direction (discharges)
    sp = np.where(np.abs(d) >= thr)[0]
    print(f"  discharges (|tick|>={thr:.3f} = p99.9): {len(sp):,}  "
          f"({len(sp)/len(d)*100:.2f}% of ticks)")
    if len(sp) < 100:
        print("  too few discharges for hazard — skipping")
        return
    # gap in TICKS between discharges
    gaps = np.diff(sp)
    base = 1.0 / gaps.mean()
    print(f"  gap distribution: median={np.median(gaps):.0f}  p25={np.percentile(gaps,25):.0f}  "
          f"p75={np.percentile(gaps,75):.0f}")
    # hazard per gap bucket (in units of ~50 ticks)
    maxgap = int(np.percentile(gaps, 97))
    edges = list(range(0, maxgap + 1, 50))
    for a, b in zip(edges[:-1], edges[1:]):
        cnt = ((gaps >= a) & (gaps < b)).sum()
        tot = gaps[(gaps >= a) & (gaps < b)].sum() if cnt else 0
        rate = cnt / max(tot, 1e-9)
        print(f"  gap {a:5d}-{b:5d}: n={cnt:5d}  rate={rate:.5f}  ratio={rate/base:.2f}x base")


def ev():
    df = load()
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, SYM)
    # vol400up: discharge = up bars (adv True), fade = SHORT
    def run(direction, entry_off, hold, cluster, gap_floor=False):
        idx = eng.spikes(df, SYM)
        trades = []
        skip = 0
        for k, i in enumerate(idx):
            if skip > 0:
                skip -= 1
                continue
            if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
                continue
            e = i + entry_off
            if e + 2 >= n:
                continue
            entry = closes[e]
            sl_dist = max(med[i], FLOOR) if gap_floor else max(med[i], FLOOR)
            # fade: short after up-spike. chase: long.
            # For a SHORT: sl = entry + d, tp = entry - 2d
            # For a LONG:  sl = entry - d, tp = entry + 2d
            if direction == "fade":   # SHORT
                sl_level, tp_level = entry + sl_dist, entry - 2 * sl_dist
                sign = -1
            else:                     # LONG (chase the up-spike)
                sl_level, tp_level = entry - sl_dist, entry + 2 * sl_dist
                sign = +1
            # one-shot (no partial) for a clean EV read
            p, r = eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                 entry, sl_level, tp_level, e + 1, hold, n)
            trades.append(sign * (p - entry))
        pnl = np.array(trades)
        wins = pnl[pnl > 0]; losses = pnl[pnl <= 0]
        pf = wins.sum() / abs(losses.sum()) if len(losses) and losses.sum() != 0 else float("inf")
        print(f"  {direction:6s} entry+{entry_off} hold={hold:2d} cluster={int(cluster)}: "
              f"n={len(pnl):4d}  WR={100*(pnl>0).mean():5.1f}%  PF={pf:5.2f}  "
              f"EV={pnl.mean():+.3f} pts  worst={pnl.min():+.1f}")
        return pnl

    print(f"\n=== C. FADE vs CHASE EV (floor={FLOOR}, spike>{SPIKE}x median) ===")
    for direction in ("fade", "chase"):
        for entry_off in (1, 2, 3):
            for hold in (1, 2, 5):
                run(direction, entry_off, hold, cluster=True)


if __name__ == "__main__":
    df = load()
    structure(df)
    tick_hazard()
    ev()