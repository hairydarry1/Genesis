#!/usr/bin/env python3
"""Adversarial tests for the bull-FVG-touch short (2026-09-21, late).

The signal: after a bull FVG (3-bar gap) forms and price first trades back
through the gap's upper boundary, shorting for N bars made +1.3..1.5x spread
on R_25 across 2 windows (z -4.7..-5.2 vs return-permutation nulls).

If real, this is the first Vol-family structure ever found in this repo - so
try hard to kill it:
  A. regime       always-short baseline (mean 5-bar return * -1): if similar
                  per-event, the 'signal' is just a bearish week
  B. spacing      only first touch after >=30 quiet bars: independent events
  C. horizon      same stat at fwd = 5 / 15 / 60 bars: drift vs microstructure
  D. replication  4th window (19 days back) + 12d refetch with caching

Usage: python analysis/vol_fvg_adversarial.py
"""
import asyncio
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.deriv_connection import DerivConnection  # noqa: E402

SYMBOLS = ["R_25", "R_10"]
WINDOWS_DAYS = (12, 19)          # 12d = replication of the OOS run, 19d = new
SEEDS = 12
SPREAD = {"R_10": 0.188, "R_25": 0.301}
VPU_MIN = {"R_10": 0.50, "R_25": 0.50}  # $ per price-unit at min lot (MT5)


async def fetch24(conn, sym, t_end):
    t_start = t_end - 24 * 3600
    ts_all, px_all = [], []
    t_hi = t_end
    step = 2500
    while t_hi > t_start:
        t_lo = max(t_start, t_hi - step)
        resp = await conn.get_ticks_history(sym, start=int(t_lo),
                                            end=str(int(t_hi)), count=5000)
        hist = resp.get("history", {})
        if not hist.get("times"):
            break
        ts_all.append(np.asarray(hist["times"], dtype=np.int64))
        px_all.append(np.asarray(hist["prices"], dtype=np.float64))
        t_hi = int(hist["times"][0])
        await asyncio.sleep(0.12)
    if not ts_all:
        return None, None
    ts = np.concatenate(ts_all); px = np.concatenate(px_all)
    o = np.argsort(ts, kind="stable")
    ts, px = ts[o], px[o]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    return ts[keep], px[keep]


def bars(ts, px, sec=60):
    b = (ts // sec).astype(np.int64)
    ub = np.unique(b)
    op = px[np.searchsorted(b, ub, side="left")]
    cl = px[np.searchsorted(b, ub, side="right") - 1]
    hi = np.maximum.reduceat(px, np.searchsorted(b, ub, side="left"))
    lo = np.minimum.reduceat(px, np.searchsorted(b, ub, side="right") - 1)
    return ub * sec, op, hi, lo, cl


def touch_entries(ep, o, h, l, c, min_gap=0):
    """Bull-FVG first-touch indices; min_gap = min bars since previous entry."""
    n = len(c)
    fmt = np.where(l[2:] > h[:-2])[0]
    out = []
    last = -10**9
    for i in fmt:
        zi = i + 2
        if zi >= n:
            continue
        ztop = l[zi]
        if ztop <= h[i]:
            continue
        end = min(zi + 20, n)
        t = np.where(l[zi + 1:end] <= ztop)[0]
        if len(t):
            j = zi + 1 + int(t[0])
            if j - last >= min_gap:
                out.append(j)
                last = j
    return np.array(out, dtype=int)


def short_stat(c, idx, fwd):
    ok = idx + fwd < len(c)
    return float(np.mean(c[idx[ok] + fwd] - c[idx[ok]])) if ok.any() else float("nan")


def ret_surr(c, seed):
    rng = np.random.default_rng(seed)
    ret = np.diff(c)
    return np.concatenate(([c[0]], c[0] + np.cumsum(rng.permutation(ret))))


def surr_z(real, surr_vals):
    v = np.asarray(surr_vals, float)
    return (real - v.mean()) / (v.std(ddof=1) + 1e-12)


def analyze(sym, ts, px):
    ep, o, h, l, c = bars(ts, px)
    n = len(c)
    sp, vpu = SPREAD[sym], VPU_MIN[sym]
    print(f"\n===== {sym} ({n} M1 bars) spread={sp} VPU@min=${vpu}/u =====")
    for fwd in (5, 15, 60):
        idx_all = touch_entries(ep, o, h, l, c, min_gap=0)
        idx_gap = touch_entries(ep, o, h, l, c, min_gap=30)
        s_all = -short_stat(c, idx_all, fwd)   # short pnl
        s_gap = -short_stat(c, idx_gap, fwd)
        # always-short baseline: mean of c[t+fwd]-c[t] over all t
        t_idx = np.arange(0, n - fwd)
        base = -float(np.mean(c[t_idx + fwd] - c[t_idx]))
        print(f"fwd={fwd:3d} | FVG-short(all) {s_all:+.4f}u n={len(idx_all)}"
              f" | FVG-short(spaced30) {s_gap:+.4f}u n={len(idx_gap)}"
              f" | always-short {base:+.4f}u | net/all {s_all - sp:+.4f}u"
              f" = {(s_all - sp) * vpu:+.4f}$")
    # surrogate z for the headline stat (fwd=5, all entries, net)
    idx_all = touch_entries(ep, o, h, l, c, min_gap=0)
    real = -short_stat(c, idx_all, 5)
    svals = []
    for seed in range(SEEDS):
        c2 = ret_surr(c, seed)
        svals.append(-short_stat(c2, idx_all, 5))
    z = surr_z(real, svals)
    net = (real - sp) * vpu
    print(f"headline: gross {real:+.4f}u z={z:+.2f} | net {(real - sp):+.4f}u "
          f"= ${net:+.3f}/event x ~{len(idx_all)}/24h = ${net * len(idx_all):+.2f}/24h @min lot")


async def main():
    conn = DerivConnection()
    await conn.connect()
    now = int(np.time.time() if hasattr(np, "time") else __import__("time").time())
    for days in WINDOWS_DAYS:
        t_end = now - days * 24 * 3600
        for sym in SYMBOLS:
            ts, px = await fetch24(conn, sym, t_end)
            if ts is None:
                print(f"{sym} @-{days}d: no data")
                continue
            np.savez_compressed(f"data/synth_scan/ticks24h_{sym}_{days}d.npz",
                                ts=ts, px=px)
            analyze(sym, ts, px)
    if conn._ws:
        await conn._ws.close()


if __name__ == "__main__":
    asyncio.run(main())
