#!/usr/bin/env python3
"""
eval/voc_whatis.py — What IS Vol over Crash 400, without boom/crash glasses?

The boom/crash framing assumes: one-directional discharge + guaranteed
reversion + a drift to ride. This product may instead be an oscillating /
mean-reverting high-vol process where that framing is wrong. Measure:

  A) Price path: trend/oscillation structure (hourly path, rolling slope,
     long-lag autocorrelation of returns, how much of the range is
     retraced, drawdown depth/age).
  B) Symmetry: conditional next-1/2-bar move after BIG bars in EACH
     direction (up-bars AND down-bars), vs baseline. No direction assumed.
  C) Spike timing directly: does the post-spike conditional move actually
     depend on spike SIZE or TIME-SINCE-LAST-SPIKE (hazard structure),
     rather than just direction?
  D) Regime: does the engine's edge (or the raw post-crash move) depend on
     whether price is in an up-leg or down-leg of the oscillation?
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"


def load(key):
    slug = {"voc400": "Vol_over_Crash_400"}[key]
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    return df


def main():
    df = load("voc400")
    o = df["open"].values; h = df["high"].values
    l = df["low"].values; c = df["close"].values
    n = len(df)
    t = pd.to_datetime(df["epoch"], unit="s")
    rng_ = h - l
    med = pd.Series(rng_).rolling(100, min_periods=20).median().values
    print(f"n={n} bars, span {(t.max()-t.min()).days} days")
    print(f"close: min {c.min():.1f} max {c.max():.1f} first {c[0]:.1f} "
          f"last {c[-1]:.1f}")

    # ---- A) price path structure
    # hourly closes
    dfh = df.set_index(t).resample("1h").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"})
    ch = dfh["close"].values
    nh = len(ch)
    print(f"\n[A] hourly path: {nh} hours")
    # oscillation: total variation vs net change
    net = c[-1] - c[0]
    tv = np.abs(np.diff(c)).sum()
    print(f"    net change {net:+.0f} pts vs total 1-min variation {tv:.0f} "
          f"({100*abs(net)/tv:.0f}% directional, {100*(1-abs(net)/tv):.0f}% oscillation)")
    # how much of the max range is retraced
    rng_path = c.max() - c.min()
    print(f"    close range {rng_path:.0f} pts; high-low range "
          f"{(h.max()-l.min()):.0f} pts")
    # long-lag autocorrelation of 1h returns
    r1 = np.diff(ch)
    print("    hourly return AC1..AC24:")
    for lag in (1, 6, 12, 24, 48, 72):
        if lag >= len(r1):
            continue
        ac = np.corrcoef(r1[:-lag], r1[lag:])[0, 1]
        print(f"      lag{lag:>3}: {ac:+.3f}")
    # rolling 24h slope sign distribution
    win = 24 * 60
    slopes = []
    for i in range(win, n, win // 2):
        slopes.append(c[i] - c[i - win])
    slopes = np.array(slopes)
    print(f"    24h-window slopes: {len(slopes)} windows, "
          f"up {100*(slopes>0).mean():.0f}% down {100*(slopes<0).mean():.0f}%, "
          f"mean {slopes.mean():+.0f} pts/24h")
    # drawdown structure from a running peak (is it a drifter or oscillator?)
    peak = np.maximum.accumulate(c)
    dd = (c - peak) / peak * 100
    print(f"    max drawdown from running peak: {dd.min():.1f}%")
    # recovery: after the deepest 5 drawdowns, how fast recovered
    print(f"    deepest drawdown depth: {sorted(dd)[:3]}%")

    # ---- B) symmetry — conditional next-bar moves after big bars both ways
    print(f"\n[B] conditional next-2-bar close move after big bars "
          f"(median bar {np.nanmedian(rng_):.2f} pts)")
    body = c - o
    print(f"    {'signal':<28}{'n':>7}{'next2 mean':>11}{'next2 med':>10}"
          f"{'p>0':>7}")
    base_next = []
    for i in range(150, n - 3):
        base_next.append(c[i + 2] - c[i])
    base_next = np.array(base_next)
    print(f"    {'ALL bars (baseline)':<28}{len(base_next):>7}"
          f"{base_next.mean():>+11.3f}{np.median(base_next):>+10.3f}"
          f"{100*(base_next>0).mean():>6.0f}%")
    for label, mask in [
        ("down bar rng>2x med", (body < 0) & (rng_ > 2 * med)),
        ("down bar rng>3x med", (body < 0) & (rng_ > 3 * med)),
        ("down bar rng>5x med", (body < 0) & (rng_ > 5 * med)),
        ("down bar rng>8x med", (body < 0) & (rng_ > 8 * med)),
        ("up bar rng>2x med", (body > 0) & (rng_ > 2 * med)),
        ("up bar rng>3x med", (body > 0) & (rng_ > 3 * med)),
    ]:
        idx = np.where(mask & np.isfinite(med))[0]
        idx = idx[idx < n - 3]
        if len(idx) < 10:
            print(f"    {label:<28}{len(idx):>7}  (too few)")
            continue
        nx = c[idx + 2] - c[idx]
        print(f"    {label:<28}{len(idx):>7}{nx.mean():>+11.3f}"
              f"{np.median(nx):>+10.3f}{100*(nx>0).mean():>6.0f}%")

    # ---- C) does SIZE or TIME-SINCE-LAST matter (hazard), beyond direction
    print(f"\n[C] post-crash (down, >3x med) next-2 move by spike size")
    big = (body < 0) & (rng_ > 3 * med) & np.isfinite(med)
    idx = np.where(big)[0]
    idx = idx[idx < n - 3]
    sizes = rng_[idx] / med[idx]
    nx = c[idx + 2] - c[idx]
    for lo, hi in [(3, 5), (5, 8), (8, 12), (12, 99)]:
        m = (sizes >= lo) & (sizes < hi)
        if m.sum() < 10:
            continue
        print(f"    crash {lo}-{hi}x med ({m.sum():>5}): "
              f"next2 {nx[m].mean():>+7.3f} pts, p>0 {100*(nx[m]>0).mean():.0f}%")
    # time since last big bar
    print("    post-crash next-2 by time since previous big bar:")
    prev = np.full(len(idx), 9999)
    last = -9999
    allbig = np.where(big)[0]
    for k, i in enumerate(idx):
        past = allbig[allbig < i]
        prev[k] = i - past[-1] if len(past) else 9999
    for lo, hi in [(0, 5), (5, 15), (15, 60), (60, 9999)]:
        m = (prev >= lo) & (prev < hi)
        if m.sum() < 10:
            continue
        print(f"    since {lo}-{hi} bars ({m.sum():>5}): "
              f"next2 {nx[m].mean():>+7.3f} pts, p>0 {100*(nx[m]>0).mean():.0f}%")

    # ---- D) regime dependence: post-crash move in up-leg vs down-leg
    print(f"\n[D] post-crash next-2 move split by 12h macro-leg")
    win = 12 * 60
    mac = np.full(n, np.nan)
    for i in range(win, n):
        mac[i] = c[i] - c[i - win]
    in_up = mac[idx] > 0
    print(f"    after crash in UP-leg ({in_up.sum():>5}): "
          f"next2 {nx[in_up].mean():>+7.3f} pts, p>0 {100*(nx[in_up]>0).mean():.0f}%")
    print(f"    after crash in DOWN-leg ({(~in_up).sum():>5}): "
          f"next2 {nx[~in_up].mean():>+7.3f} pts, p>0 "
          f"{100*(nx[~in_up]>0).mean():.0f}%")


if __name__ == "__main__":
    main()