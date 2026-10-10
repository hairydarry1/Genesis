#!/usr/bin/env python3
"""Volatility-index generator characterization via the Deriv API (read-only).

Question (2026-09-21): Vol indices are crypto-PRNG, constant-vol markets - does
the GENERATOR leave ownable structure that the Sep 10 bar-scan (persistence
logic only) could never see?

Tests per symbol, on ~48h of tick history (1s cadence family = 1 tick/s):
  cadence        inter-tick delta distribution; gap events (feed or generator?)
  quote-change   fraction of ticks that move the quote at all
  jump skew      mean up-jump vs mean |down-jump| (Boom/Crash-style asymmetry)
  runs           sign-run count vs random-walk expectation (z-score)
  tick alternation  P(same sign as prev move) among nonzero moves
  bar ACF/VR     1-min return ACF(1..5) + VR(5) - bar-scale efficiency
  vol clustering AC(1) of rolling 5-min absolute returns
  hour-of-day    mean |ret| per UTC hour; max/min ratio + top-hour z-score

Caches ticks to data/synth_scan/ticks48h_{sym}.npz for later backtests.
Read-only: touches no live state, no MT5, no measurement files.
"""
import asyncio
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.deriv_connection import DerivConnection  # noqa: E402

SYMBOLS = ["1HZ10V", "1HZ50V", "1HZ100V", "R_10"]  # 1s family + one 2s contrast
HOURS = 24
COUNT = 5000            # API max per call
SLEEP_BETWEEN = 0.15


async def fetch_symbol(conn, sym, t_end, t_start):
    """Page backwards from t_end to t_start via start/end epochs."""
    ts_all, px_all = [], []
    t_hi = t_end
    while t_hi > t_start:
        step = COUNT if "1HZ" in sym else COUNT // 2  # seconds of history per call
        t_lo = max(t_start, t_hi - step)
        resp = await conn.get_ticks_history(sym, start=int(t_lo), end=str(int(t_hi)),
                                            count=COUNT)
        hist = resp.get("history", {})
        times = hist.get("times", [])
        prices = hist.get("prices", [])
        if not times:
            break
        ts_all.append(np.asarray(times, dtype=np.int64))
        px_all.append(np.asarray(prices, dtype=np.float64))
        print(f"  [{sym}] page: {len(times)} ticks -> {t_lo}..{t_hi} "
              f"(at {(t_hi - t_start)/3600:.1f}h back)", flush=True)
        t_hi = int(times[0])  # continue from the oldest tick just fetched
        await asyncio.sleep(SLEEP_BETWEEN)
    if not ts_all:
        return None, None
    ts = np.concatenate(ts_all)
    px = np.concatenate(px_all)
    order = np.argsort(ts, kind="stable")
    ts, px = ts[order], px[order]
    keep = np.concatenate(([True], np.diff(ts) > 0))  # dedupe same-second repeats
    return ts[keep], px[keep]


def acf(x, k):
    x = x - x.mean()
    return float((x[:-k] @ x[k:]) / (x @ x)) if len(x) > k + 10 else float("nan")


def characterize(sym, ts, px):
    out = [f"\n===== {sym} =====  {len(ts):,} ticks, "
           f"{(ts[-1]-ts[0])/3600:.1f}h, {px[0]:.4g} -> {px[-1]:.4g}"]
    dt = np.diff(ts)
    out.append(f"cadence   : median {np.median(dt):.2f}s | "
               f"P95 {np.percentile(dt, 95):.2f}s | gaps>2s: {(dt > 2).sum()} "
               f"({100*(dt > 2).mean():.2f}%) | gaps>10s: {(dt > 10).sum()}")
    moves = np.diff(px)
    nz = moves[moves != 0]
    out.append(f"quotes    : changed {100*len(nz)/len(moves):.1f}% of ticks | "
               f"median |jump| {np.median(np.abs(nz)) if len(nz) else 0:.4g}")
    up, dn = nz[nz > 0], -nz[nz < 0]
    if len(up) and len(dn):
        skew = up.mean() / dn.mean() if dn.mean() > 0 else float("inf")
        out.append(f"jump skew : mean up {up.mean():.4g} vs mean down {dn.mean():.4g} "
               f"-> ratio {skew:.3f} (1.0 = symmetric)")
    sign = np.sign(nz)
    same = (sign[1:] == sign[:-1]).mean()
    z_same = (same - 0.5) / np.sqrt(0.25 / len(sign)) if len(sign) > 100 else 0
    out.append(f"runs      : P(same sign) {100*same:.2f}% -> "
               f"z {z_same:+.2f} vs coin (n={len(sign):,})")
    # 1-min bars from ticks
    mins = ts // 60
    uniq, idx = np.unique(mins, return_index=True)
    closes = px[idx]
    o = np.searchsorted(mins, uniq, side="left")
    c = np.searchsorted(mins, uniq, side="right") - 1
    op = px[o]
    cl = px[c]
    ret1 = np.diff(cl) / cl[:-1]
    rng = np.abs(cl - op) / op
    ac = [acf(ret1, k) for k in (1, 2, 3, 5)]
    # proper variance ratio: Var(5-bar move) / (5 * Var(1-bar move))
    r5 = np.convolve(ret1, np.ones(5), mode="valid")  # overlapping 5-bar sums
    vr = r5.var() / (5 * ret1.var()) if len(ret1) > 100 else float("nan")
    se1 = 1 / np.sqrt(len(ret1))  # rough SE for ACF under iid
    out.append(f"1m bars   : n={len(ret1):,} | ACF1..5 {ac[0]:+.4f} {ac[1]:+.4f} "
               f"{ac[2]:+.4f} {ac[3]:+.4f} (SE~{se1:.4f}) | VR(5) {vr:.3f}")
    # vol clustering: rolling 5-min mean |ret|, AC(1). NOTE: overlapping
    # windows mechanically induce AC ~ (w-1)/w = 0.80 even for iid input.
    w = 5
    rabs = np.abs(ret1)
    roll = np.convolve(rabs, np.ones(w) / w, mode="valid")
    ac_roll = acf(roll, 1)
    mech = (w - 1) / w
    out.append(f"vol clust : AC(1) of 5m |ret| = {ac_roll:+.3f} "
               f"(mechanical floor ~{mech:.2f}; excess "
               f"{ac_roll - mech:+.3f})")
    # hour-of-day on |ret| aggregated per UTC hour (uniq are MINUTE epochs!)
    hours = ((uniq[1:] // 60) % 24).astype(int)
    hmean = np.array([rabs[hours == h].mean() if (hours == h).any() else np.nan
                      for h in range(24)])
    valid = hmean[~np.isnan(hmean)]
    if len(valid) == 24:
        hmax, hmin = hmean.argmax(), hmean.argmin()
        pooled = rabs.mean()
        top = rabs[hours == hmax]
        sem = top.std() / np.sqrt(len(top))
        out.append(f"hour-of-day: min h{hmin:02d} {hmean[hmin]:.3e} | max h{hmax:02d} "
                   f"{hmean[hmax]:.3e} | ratio {hmean[hmax]/hmean[hmin]:.2f}x | "
                   f"top-hour z {(top.mean()-pooled)/sem:+.2f}")
    else:
        out.append(f"hour-of-day: only {len(valid)}/24 hours present")
    return "\n".join(out), dict(ts=ts, px=px)


async def main():
    import os
    cached = [s for s in SYMBOLS if os.path.exists(f"data/synth_scan/ticks24h_{s}.npz")]
    if "--refetch" not in sys.argv and len(cached) == len(SYMBOLS):
        for sym in SYMBOLS:
            d = np.load(f"data/synth_scan/ticks24h_{sym}.npz")
            text, _ = characterize(sym, d["ts"], d["px"])
            print(text, flush=True)
        return
    now = int(time.time())
    t_start, t_end = now - HOURS * 3600, now - 5
    conn = DerivConnection()
    await conn.connect()

    async def guarded(sym):
        try:
            return sym, await fetch_symbol(conn, sym, t_end, t_start)
        except Exception as e:
            print(f"{sym}: FETCH FAILED: {e}")
            return sym, (None, None)

    results = await asyncio.gather(*[guarded(s) for s in SYMBOLS])
    for sym, (ts, px) in results:
        if ts is None or len(ts) < 5000:
            print(f"{sym}: insufficient data ({0 if ts is None else len(ts)} ticks)")
            continue
        text, data = characterize(sym, ts, px)
        print(text, flush=True)
        np.savez_compressed(f"data/synth_scan/ticks24h_{sym}.npz",
                            ts=data["ts"], px=data["px"])
    if conn._ws:
        await conn._ws.close()


if __name__ == "__main__":
    asyncio.run(main())
