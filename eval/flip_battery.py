#!/usr/bin/env python3
"""
FLIP BATTERY — full vol400 analysis on all four Crash Boom Flip products
========================================================================
For each of Flip 150/300/500/1000 (floors 0.517/8.628/3.058/1.878):
  A. STRUCTURE      median bar, direction size ratio, reversion-by-threshold
  B. DIRECTIONAL FADE  short-after-UP + long-after-DOWN, e+1/e+2 x h=1/2,
                     partial entry, OOS halves
  C. QUIET+COOLDOWN stack on the best config
  D. LOTTERY c      K=2/3/5 clean race (short side)
  E. TICK HAZARD    decaying vs flat (clustering signature)
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

FLOORS = {150: 0.517, 300: 8.628, 500: 3.058, 1000: 1.878}
BASE = Path("../data")


def load(tag):
    f = BASE / f"candles_Crash_Boom_Flip_{tag}_Index_1min.parquet"
    df = pd.read_parquet(f)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def walk_short(closes, highs, lows, rng, med, adv, entry, sl_d, e, h, n):
    sl_l, tp_l = entry + sl_d, entry - 2 * sl_d
    for j in range(e, min(e + h, n)):
        if highs[j] >= sl_l:
            return highs[j] if (adv[j] and rng[j] > 3 * med[j]) else sl_l
        if lows[j] <= tp_l:
            return tp_l
    return closes[min(e + h - 1, n - 1)]


def walk_long(closes, highs, lows, rng, med, adv, entry, sl_d, e, h, n):
    sl_l, tp_l = entry - sl_d, entry + 2 * sl_d
    for j in range(e, min(e + h, n)):
        if lows[j] <= sl_l:
            return lows[j] if ((not adv[j]) and rng[j] > 3 * med[j]) else sl_l
        if highs[j] >= tp_l:
            return tp_l
    return closes[min(e + h - 1, n - 1)]


def fade_set(df, side, thr, entry_off, hold, floor, partial, lo=0, hi=None):
    """side: 'S' short-after-UP-discharge, 'L' long-after-DOWN-discharge."""
    n = len(df)
    hi = n if hi is None else hi
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    rng = df["range"].values; med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    if side == "S":
        trig = np.where((rng > thr * med) & ok & up)[0]
    else:
        trig = np.where((rng > thr * med) & ok & ~up)[0]
    trig = [i for i in trig if lo <= i < hi]
    pnls = []
    for i in trig:
        e = i + entry_off
        if e + 3 >= n:
            continue
        entry = closes[e]
        sl_d = max(med[i], floor)
        if side == "S":
            pA = walk_short(closes, highs, lows, rng, med, up, entry, sl_d, e + 1, hold, n)
            a_open = not (highs[e + 1] >= entry + sl_d or lows[e + 1] <= entry - 2 * sl_d)
            pnl = entry - pA
            if partial and a_open and closes[e + 1] < entry:
                pB = walk_short(closes, highs, lows, rng, med, up, entry, sl_d, e + 2, hold - 1, n)
                pnl = 0.5 * (entry - pA) + 0.5 * (entry - pB)
            elif partial:
                pnl = 0.5 * (entry - pA)
        else:
            pA = walk_long(closes, highs, lows, rng, med, up, entry, sl_d, e + 1, hold, n)
            a_open = not (lows[e + 1] <= entry - sl_d or highs[e + 1] >= entry + 2 * sl_d)
            pnl = pA - entry
            if partial and a_open and closes[e + 1] > entry:
                pB = walk_long(closes, highs, lows, rng, med, up, entry, sl_d, e + 2, hold - 1, n)
                pnl = 0.5 * (pA - entry) + 0.5 * (pB - entry)
            elif partial:
                pnl = 0.5 * (pA - entry)
        pnls.append(pnl)
    return np.array(pnls)


def rep(name, pnls, floor=None):
    if len(pnls) < 5:
        print(f"  {name:46s}: n={len(pnls)} (too few)"); return None
    w = pnls[pnls > 0]; l = pnls[pnls <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
    rr = f" {pnls.mean()/floor:+.3f}R" if floor else ""
    print(f"  {name:46s}: n={len(pnls):4d} WR={100*(pnls>0).mean():5.1f}% PF={pf:5.2f} "
          f"EV={pnls.mean():+.3f}{rr} worst={pnls.min():+.2f}")
    return pnls


def sectionA(df, tag):
    rng = df["range"].values; med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    closes = df["close"].values
    ok = np.isfinite(med) & (med > 0)
    print(f"\n=== A. STRUCTURE Flip {tag} ===")
    print(f"  median bar {np.nanmedian(rng):.3f}  up/dn size ratio "
          f"{np.nanmedian(rng[up])/np.nanmedian(rng[~up]):.2f}  net {closes[-1]-closes[0]:+.1f}")
    for thr in (2.0, 2.5, 3.0):
        nxt2 = np.zeros(len(df)); nxt2[:-2] = closes[2:] - closes[:-2]
        m = (rng > thr * med) & ok
        print(f"  >{thr:.1f}x: n={m.sum():5d} 2-bar aftermath mean={nxt2[m].mean():+.3f} "
              f"frac<0={(nxt2[m]<0).mean():.3f}")


def sectionB(df, tag, floor):
    n = len(df); mid = n // 2
    print(f"\n=== B. DIRECTIONAL FADE Flip {tag} (floor={floor}) ===")
    for side, nm in (("S", "short-after-UP"), ("L", "long-after-DOWN")):
        for thr in (2.0, 2.5, 3.0):
            for eo in (1, 2):
                p = fade_set(df, side, thr, eo, 2, floor, partial=True)
                rep(f"{nm} >{thr:.1f}x e+{eo} h2 partial", p, floor)
    # best on each side by EV at full sample — just report the sweep summary below


def sectionC(df, tag, floor, side, thr, eo, hold):
    n = len(df); mid = n // 2
    print(f"\n=== C. STACK & OOS Flip {tag} ({'S' if side=='S' else 'L'} >{thr}x e+{eo} h{hold}) ===")
    base = fade_set(df, side, thr, eo, hold, floor, partial=True)
    rep("partial full", base, floor)
    rep("partial OOS1", fade_set(df, side, thr, eo, hold, floor, partial=True, lo=0, hi=mid), floor)
    rep("partial OOS2", fade_set(df, side, thr, eo, hold, floor, partial=True, lo=mid), floor)
    # quiet-regime filter: skip if >=2 bars >2x med in last 20
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    rng = df["range"].values; med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    trig = np.where((rng > thr * med) & ok & (up if side == "S" else ~up))[0]
    pnls, cd = [], 0
    for i in trig:
        if cd > 0:
            cd -= 1
            continue
        loq = max(0, i - 20)
        if (rng[loq:i] > 2.0 * med[loq:i]).sum() >= 2:
            continue
        e = i + eo
        if e + 3 >= n:
            continue
        entry = closes[e]; sl_d = max(med[i], floor)
        if side == "S":
            pA = walk_short(closes, highs, lows, rng, med, up, entry, sl_d, e + 1, hold, n)
            a_open = not (highs[e + 1] >= entry + sl_d or lows[e + 1] <= entry - 2 * sl_d)
            pnl = 0.5 * (entry - pA)
            if a_open and closes[e + 1] < entry:
                pB = walk_short(closes, highs, lows, rng, med, up, entry, sl_d, e + 2, hold - 1, n)
                pnl = 0.5 * (entry - pA) + 0.5 * (entry - pB)
        else:
            pA = walk_long(closes, highs, lows, rng, med, up, entry, sl_d, e + 1, hold, n)
            a_open = not (lows[e + 1] <= entry - sl_d or highs[e + 1] >= entry + 2 * sl_d)
            pnl = 0.5 * (pA - entry)
            if a_open and closes[e + 1] > entry:
                pB = walk_long(closes, highs, lows, rng, med, up, entry, sl_d, e + 2, hold - 1, n)
                pnl = 0.5 * (pA - entry) + 0.5 * (pB - entry)
        pnls.append(pnl)
        if pnl < 0:
            cd = 2
    rep("quiet + cooldown2", np.array(pnls), floor)


def sectionD(df, tag, floor):
    print(f"\n=== D. LOTTERY Flip {tag} (short, clean race, 30-bar limit) ===")
    n = len(df)
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    rng = df["range"].values; med = df["median_range"].values
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.0 * med) & ok & up)[0]
    rngs = np.random.RandomState(7)
    rand = np.sort(rngs.choice(np.where(ok)[0], size=min(len(big), 2000), replace=False))
    for K in (2, 3, 5):
        def race(idxs):
            out = []
            for i in idxs:
                e = i + 1
                if e + 1 >= n:
                    continue
                entry = closes[e]
                R = max(med[i], floor)
                sl, tp = entry + R, entry - K * R
                hit = None
                for j in range(e + 1, min(e + 1 + 30, n)):
                    if highs[j] >= sl:
                        hit = -1; break
                    if lows[j] <= tp:
                        hit = K; break
                out.append(0.0 if hit is None else hit)
            return np.array(out)
        ob, or_ = race(big), race(rand[:len(big)])
        pw = (ob > 0).mean(); pr = (or_ > 0).mean()
        print(f"  K={K}: trig c={pw*K:.3f} (be {K/(K+1):.3f}) EV={ob.mean():+.2f}R | "
              f"random c={pr*K:.3f} EV={or_.mean():+.2f}R")


def sectionE(tag):
    tpath = BASE / f"ticks_Crash_Boom_Flip_{tag}_Index.npy"
    if not tpath.exists():
        print(f"\n=== E. HAZARD Flip {tag}: no ticks file ===")
        return
    px = np.load(tpath)
    d = np.diff(px)
    thr = np.percentile(np.abs(d), 99.9)
    sp = np.where(np.abs(d) >= thr)[0]
    print(f"\n=== E. TICK HAZARD Flip {tag} (p99.9 |tick|>={thr:.3f}, n={len(sp)}) ===")
    if len(sp) < 100:
        print("  too few discharges"); return
    gaps = np.diff(sp)
    base = 1.0 / gaps.mean()
    edges = list(range(0, int(np.percentile(gaps, 95)) + 1, 100))
    ratios = []
    for a, b in zip(edges[:-1], edges[1:]):
        cnt = ((gaps >= a) & (gaps < b)).sum()
        tot = gaps[(gaps >= a) & (gaps < b)].sum() if cnt else 1
        ratios.append(cnt / max(tot, 1e-9) / base)
    print(f"  gap dist median={np.median(gaps):.0f} p25={np.percentile(gaps,25):.0f} "
          f"p75={np.percentile(gaps,75):.0f}")
    print(f"  hazard buckets 1..{len(ratios)}: first={ratios[0]:.2f}x "
          f"mid={ratios[len(ratios)//2]:.2f}x last={ratios[-1]:.2f}x base  "
          f"({'DECAYING (clustering)' if ratios[0] > 2 and ratios[0] > ratios[-1]*3 else 'flat/other'})")


if __name__ == "__main__":
    for tag in (150, 300, 500, 1000):
        df = load(tag)
        floor = FLOORS[tag]
        sectionA(df, tag)
        sectionB(df, tag, floor)
        sectionD(df, tag, floor)
        sectionE(tag)
    # deep passes per symbol with best configs (chosen after reading section B)
    print("\n\n########## DEEP PASSES ##########")
    for tag, side, thr, eo in ((150, "S", 2.0, 1), (150, "L", 2.0, 1),
                               (500, "S", 2.0, 1), (1000, "S", 2.0, 1),
                               (300, "S", 2.5, 1), (300, "S", 2.0, 2)):
        df = load(tag)
        sectionC(df, tag, FLOORS[tag], side, thr, eo, 2)