#!/usr/bin/env python3
"""FVG follow-up (2026-09-21): the bull-FVG-touch short on R_10 M1 scored
z=-5.08 vs a 20-seed return-permutation null on the cached 24h window.

This script asks three questions:
  1. OOS: does it replicate on a DIFFERENT time window (5 days back)?
  2. Family: does it replicate on sibling symbols (1HZ10V, R_25, R_100)?
  3. Mechanism: is it the FVG or just "short after any violent 3-bar up-run"?
     (naked-run control: same 3-bar impulse magnitude, no gap required)

Null for every stat: 12-seed return-permutation ensemble -> z-score.
Usage: python analysis/vol_fvg_followup.py
"""
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.deriv_connection import DerivConnection  # noqa: E402
from analysis.vol_smc_scan import (  # noqa: E402
    scan, ret_surrogate, series_from_ticks, spread_for)

WINDOW_START = int(sys.argv[1]) * 24 * 3600 if len(sys.argv) > 1 else 12 * 24 * 3600  # days back
WINDOW_H = 24
SYMBOLS = ["R_10", "R_25"]
SEEDS = 12

# corrected live spreads (Sep 10 scan); note r_10 must not match r_100
SPREAD_FIX = {"R_10": 0.188, "R_25": 0.301, "R_100": 0.230, "1HZ10V": 0.380}
VPU = {}  # filled from API active_symbols: $ value per 1.0 price unit at min lot


async def fetch_window(conn, sym, t_end, t_start):
    ts_all, px_all = [], []
    t_hi = t_end
    step = 5000 if "1HZ" in sym else 2500
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
    order = np.argsort(ts, kind="stable")
    ts, px = ts[order], px[order]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    return ts[keep], px[keep]


def bull_fvg_touch_stat(ep, o, h, l, c, fill_n=20, fwd=5):
    """Signed mean 5-bar return after first touch of bull-FVG zones (short).
    Vectorized-light version reusing scan()'s semantics."""
    n = len(c)
    fmt = np.where(l[2:] > h[:-2])[0]
    pnl = []
    for i in fmt:
        zi = i + 2
        if zi >= n:
            continue
        ztop = l[zi]
        if ztop <= h[i]:
            continue
        end = min(zi + fill_n, n)
        t = l[zi + 1:end] <= ztop
        if t.any():
            j = zi + 1 + int(np.argmax(t))
            pnl.append(c[min(j + fwd, n - 1)] - c[j])
    return np.mean(pnl) if pnl else np.nan, len(pnl)


def naked_run_short_stat(ep, o, h, l, c, k=2.0, fwd=5):
    """Control: short after ANY 3-bar up-run of >= k * bar-sigma total move,
    no gap requirement, same 5-bar horizon. If this matches the FVG stat,
    the gap is incidental; if the FVG stat beats it, the gap matters."""
    n = len(c)
    run3 = c[2:] - c[:-2]  # 3-bar move ending at bar i+2
    sig = run3.std()
    idx = np.where(run3 >= k * sig)[0]
    pnl = []
    for i in idx:
        j = i + 2
        pnl.append(c[min(j + fwd, n - 1)] - c[j])
    return np.mean(pnl) if pnl else np.nan, len(pnl)


def zscore(real, surr_vals):
    v = np.asarray(surr_vals, float)
    return (real - v.mean()) / (v.std(ddof=1) + 1e-12)


async def main():
    now = int(time.time())
    t_start = now - WINDOW_START
    t_end = t_start + WINDOW_H * 3600
    conn = DerivConnection()
    await conn.connect()

    async def guarded(sym):
        try:
            return sym, await fetch_window(conn, sym, t_end, t_start)
        except Exception as e:
            print(f"{sym}: FETCH FAILED: {e}")
            return sym, (None, None)

    results = await asyncio.gather(*[guarded(s) for s in SYMBOLS])
    try:
        syms_meta = await conn.get_active_symbols()
        for m in syms_meta:
            if m.get("symbol") in SYMBOLS:
                # Deriv: pip/pip_value -> $ per price-unit at 1 unit exposure
                VPU[m["symbol"]] = (float(m.get("pip_value", 0)) /
                                    float(m.get("pip", 1))) if m.get("pip") else None
    except Exception as e:
        print(f"active_symbols failed (VPU unknown): {e}")
    if conn._ws:
        await conn._ws.close()

    for sym, (ts, px) in results:
        if ts is None:
            print(f"{sym}: no data")
            continue
        ep, o, h, l, c = series_from_ticks_arr(ts, px, 60)
        sp = SPREAD_FIX.get(sym) or spread_for(sym)
        # OOS real stats
        f_real, n_f = bull_fvg_touch_stat(ep, o, h, l, c)
        r_real, n_r = naked_run_short_stat(ep, o, h, l, c)
        # limit-fill variant: short filled at zone top (ztop) on first touch,
        # exit 5 bars later at close. Fill price = ztop, not the touch close.
        n = len(c)
        fmt = np.where(l[2:] > h[:-2])[0]
        limit_pnl = []
        for i in fmt:
            zi = i + 2
            if zi >= n:
                continue
            ztop = l[zi]
            if ztop <= h[i]:
                continue
            end = min(zi + 20, n)
            t = l[zi + 1:end] <= ztop
            if t.any():
                j = zi + 1 + int(np.argmax(t))
                limit_pnl.append(ztop - c[min(j + 5, n - 1)])  # short: sell ztop
        limit_mean = np.mean(limit_pnl) if limit_pnl else float("nan")
        # surrogate ensembles
        f_s, r_s = [], []
        for seed in range(SEEDS):
            s = ret_surrogate(ep, o, h, l, c, seed=seed)
            fv, _ = bull_fvg_touch_stat(*s)
            rv, _ = naked_run_short_stat(*s)
            f_s.append(fv); r_s.append(rv)
        zf = zscore(f_real, f_s) if f_real == f_real else float("nan")
        zr = zscore(r_real, r_s) if r_real == r_real else float("nan")
        print(f"\n=== {sym} OOS (5d back, {len(c)} M1 bars, spread {sp}) ===")
        print(f"bull-FVG touch short : {f_real:+.4f}u (n={n_f}) "
              f"-> z {zf:+.2f} vs surrogate {np.mean(f_s):+.4f}+/-{np.std(f_s):.4f}")
        print(f"naked 3-bar run short: {r_real:+.4f}u (n={n_r}) "
              f"-> z {zr:+.2f} vs surrogate {np.mean(r_s):+.4f}+/-{np.std(r_s):.4f}")
        if f_real == f_real and r_real == r_real and n_f and n_r:
            print(f"gap-vs-naked         : FVG {f_real:+.4f} vs naked {r_real:+.4f} "
                  f"-> gap adds {f_real - r_real:+.4f}u")
        if limit_pnl:
            print(f"limit-fill variant   : short @ ztop -> {limit_mean:+.4f}u "
                  f"(n={len(limit_pnl)}; bar-close variant {f_real:+.4f})")
        if sp and f_real == f_real:
            short_gross = -f_real  # stat is the long's pnl
            net = short_gross - sp
            print(f"economics (SHORT)    : gross {short_gross:+.4f}u - spread {sp:.3f}u "
                  f"= net {net:+.4f}u/event = {net/sp:+.2f}x spread | "
                  f"events/24h ~{n_f}")
            v = VPU.get(sym)
            if v:
                print(f"$ at min lot         : {net * v:+.4f}/event -> "
                      f"{net * v * n_f:+.2f}/24h")


def series_from_ticks_arr(ts, px, bar_sec):
    b = (ts // bar_sec).astype(np.int64)
    ub = np.unique(b)
    op = px[np.searchsorted(b, ub, side="left")]
    cl = px[np.searchsorted(b, ub, side="right") - 1]
    hi = np.maximum.reduceat(px, np.searchsorted(b, ub, side="left"))
    lo = np.minimum.reduceat(px, np.searchsorted(b, ub, side="right") - 1)
    return ub * bar_sec, op, hi, lo, cl


if __name__ == "__main__":
    asyncio.run(main())
