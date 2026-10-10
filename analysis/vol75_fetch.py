#!/usr/bin/env python3
"""Fetch + fingerprint Volatility 75 data (2026-09-21).

R_75 was never characterized in this repo's Vol work - it gets its own
fingerprint check (if any slot hosts a liquidity playbook, the 75-vol
instrument is the candidate).

Caches:
  data/synth_scan/v75_ticks_{0,5,12,19}d.npz   24h tick windows (R_75)
  data/synth_scan/v75_ticks_1hz.npz            24h (1HZ75V contrast)
  data/synth_scan/v75_m1_30d.npz               30d M1 candles (R_75)

Fingerprint: cadence, jump skew, P(same sign), VR(5), hour-of-day z.
Read-only.
"""
import asyncio
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))
from data.deriv_connection import DerivConnection  # noqa: E402

WINDOWS = [(0, "latest"), (5, None), (12, None), (19, None)]  # days back


async def fetch_ticks(conn, sym, t_end, hours=24):
    t_start, t_hi = t_end - hours * 3600, t_end
    TS, PX = [], []
    while t_hi > t_start:
        t_lo = max(t_start, t_hi - 4500 if "R_" in sym else t_hi - 4500)
        resp = await conn.get_ticks_history(sym, start=int(t_lo),
                                            end=str(int(t_hi)), count=5000)
        hist = resp.get("history", {})
        times = hist.get("times", [])
        if not times or int(times[0]) >= t_hi:
            break
        TS.append(np.asarray(times, np.int64))
        PX.append(np.asarray(hist["prices"], np.float64))
        t_hi = int(times[0])
        await asyncio.sleep(0.1)
    if not TS:
        return None, None
    ts = np.concatenate(TS); px = np.concatenate(PX)
    o = np.argsort(ts, kind="stable")
    ts, px = ts[o], px[o]
    keep = np.concatenate(([True], np.diff(ts) > 0))
    return ts[keep], px[keep]


async def fetch_candles(conn, sym, days=30):
    now = int(time.time())
    t_start, t_hi = now - days * 86400, now - 60
    rows = []
    while t_hi > t_start:
        t_lo = max(t_start, t_hi - 5000 * 60)
        resp = await conn.get_ticks_history(sym, start=int(t_lo),
                                            end=str(int(t_hi)), style="candles",
                                            granularity=60, count=5000)
        c = resp.get("candles", [])
        if not c:
            break
        rows = c + rows
        t_hi = int(c[0]["epoch"]) - 60
        await asyncio.sleep(0.1)
        if len(rows) and int(rows[0]["epoch"]) <= t_start + 60:
            break
    if not rows:
        return None
    ep = np.array([r["epoch"] for r in rows], np.int64)
    op = np.array([r["open"] for r in rows], float)
    hi = np.array([r["high"] for r in rows], float)
    lo = np.array([r["low"] for r in rows], float)
    cl = np.array([r["close"] for r in rows], float)
    keep = np.concatenate(([True], np.diff(ep) > 0))
    return np.column_stack([ep[keep], op[keep], hi[keep], lo[keep], cl[keep]])


def fingerprint(sym, ts, px):
    dt = np.diff(ts)
    mv = np.diff(px)
    nz = mv[mv != 0]
    up, dn = nz[nz > 0], -nz[nz < 0]
    sign = np.sign(nz)
    same = (sign[1:] == sign[:-1]).mean()
    z = (same - 0.5) / np.sqrt(0.25 / len(sign))
    mins = ts // 60
    ub, idx = np.unique(mins, return_index=True)
    cl = px[idx]
    ret = np.diff(cl) / cl[:-1]
    vr5 = np.convolve(ret, np.ones(5), "valid").var() / (5 * ret.var())
    print(f"  {sym}: {len(ts):,} ticks {(ts[-1]-ts[0])/3600:.1f}h | "
          f"cadence med {np.median(dt):.2f}s | skew up/dn "
          f"{up.mean()/dn.mean():.4f} | P(same) {100*same:.2f}% (z {z:+.2f}) | "
          f"VR5 {vr5:.3f}")


async def main():
    now = int(time.time())
    conn = DerivConnection()
    await conn.connect()

    for days, _ in WINDOWS:
        for sym in (["R_75"] if days in (0, 5, 12, 19) else []) + \
                   (["1HZ75V"] if days == 0 else []):
            t_end = now - days * 86400 if days else now - 30
            tag = f"{days}d" if days else ("1hz" if sym == "1HZ75V" else "0d")
            out = Path(f"data/synth_scan/v75_ticks_{tag}.npz")
            if out.exists():
                print(f"[skip cached] {sym} {tag}")
                continue
            ts, px = await fetch_ticks(conn, sym, t_end)
            if ts is None:
                print(f"{sym} {tag}: NO DATA")
                continue
            np.savez_compressed(out, ts=ts, px=px)
            print(f"[saved] {sym} {tag}: {len(ts):,} ticks", flush=True)

    out = Path("data/synth_scan/v75_m1_30d.npz")
    if not out.exists():
        m1 = await fetch_candles(conn, "R_75", 30)
        if m1 is not None:
            np.savez_compressed(out, bars=m1)
            print(f"[saved] R_75 M1 30d: {len(m1):,} bars", flush=True)

    if conn._ws:
        await conn._ws.close()

    for tag in ("0d", "5d", "12d", "19d", "1hz"):
        p = Path(f"data/synth_scan/v75_ticks_{tag}.npz")
        if p.exists():
            d = np.load(p)
            fingerprint(tag, d["ts"], d["px"])
    p = Path("data/synth_scan/v75_m1_30d.npz")
    if p.exists():
        m1 = np.load(p)["bars"]
        ret = np.diff(m1[:, 4]) / m1[:, 4][:-1]
        vr5 = np.convolve(ret, np.ones(5), "valid").var() / (5 * ret.var())
        print(f"  M1 30d: {len(m1):,} bars | VR5 {vr5:.3f}")


if __name__ == "__main__":
    asyncio.run(main())
