#!/usr/bin/env python3
"""Volatility 75 liquidity strategy (2026-09-21).

The user's brief: trade V75 the professional way - setups with TP/SL, at the
spots where people got liquified (old HTF extremes). Engine:

  HTF map (M15): confirmed fractal swings (K=5); keep UNMITIGATED ones
    = price never closed beyond after confirmation (live pools).
  Trigger (M1), both directions (short shown; long mirrored):
    SWEEP    M1 high wicks above an unmitigated M15 high (stops of longs run)
    REJECT   a close back below the level within 5 bars (trap confirmed)
    DISPLACE a bearish 3-bar FVG forms between sweep and rejection
    ENTRY    limit at zone top + retrace (0.25R beyond zone) within 20 bars
    STOP     sweep extreme +/- buffer (0.5x ATR20 of M15)
    TARGET   nearest unmitigated opposite pool beyond entry; fallback 2R
    TIMEOUT  240 bars -> market exit
  Conservative fills: spread charged per round trip; if a bar grazes both
    stop and target the LOSS is counted.

Sizing: lots = max(min_lot, round(risk_usd / (stop_dist * $/unit))) capped by
  margin sanity; risk_usd fixed $0.50 default ($10 book, 5%).

Discipline: variant grid tuned on the 30d M1 series ONLY, then the chosen
  config is verified on 4 independent 24h windows + 1HZ75V (window-count
  rule from the Sep 21 SMC work).

Also profiles LIQUIDATION SPOTS: every sweep of an unmitigated M15 pool
  (with/without rejection) and what price does next (the user's map of
  where people got liquified).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

# ---- V75 contract reality (MT5, measured 2026-09-21) ----
VPU = {"R_75": 0.0001, "1HZ75V": 0.05}      # $ per price-unit at min lot
MIN_LOT = {"R_75": 0.01, "1HZ75V": 0.05}
SPREAD = {"R_75": 14.79, "1HZ75V": 1.80}    # price units
PRICE_SCALE = {}                            # filled at runtime (for ATR %)

K_HTF = 5
REJECT_BARS = 5
ENTRY_WINDOW = 20
TIMEOUT = 240
FALLBACK_R = 2.0
RISK_USD = 0.50


def m1_from_bars(bars):
    return bars[:, 0], bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]


def m1_from_ticks(ts, px):
    b = (ts // 60).astype(np.int64)
    ub = np.unique(b)
    left = np.searchsorted(b, ub, side="left")
    right = np.searchsorted(b, ub, side="right")
    return (ub * 60.0, px[left], np.maximum.reduceat(px, left),
            np.minimum.reduceat(px, right - 1), px[right - 1])


def resample(ep, o, h, l, c, sec):
    b = (ep // sec).astype(np.int64)
    ub = np.unique(b)
    left = np.searchsorted(b, ub, side="left")
    right = np.searchsorted(b, ub, side="right")
    return (ub * sec, o[left], np.maximum.reduceat(h, left),
            np.minimum.reduceat(l, left), c[right - 1])


def swings(h, l, k):
    from numpy.lib.stride_tricks import sliding_window_view as swv
    w = 2 * k + 1
    sh = np.where(h[k:len(h) - k] == swv(h, w).max(axis=1))[0] + k
    sl = np.where(l[k:len(l) - k] == swv(l, w).min(axis=1))[0] + k
    return sh, sl


def pools(h, l, c, sh, sl):
    hi, lo = [], []
    for i in sh:
        if not (c[i + K_HTF + 1:] > h[i]).any():
            hi.append((i + K_HTF, h[i]))
    for i in sl:
        if not (c[i + K_HTF + 1:] < l[i]).any():
            lo.append((i + K_HTF, l[i]))
    return hi, lo


def fvg_between(h, l, i0, i1, bearish=True):
    best = None
    for i in range(max(i0, 0), min(i1, len(l) - 3) + 1):
        if bearish and l[i] > h[i + 2]:
            best = (l[i], h[i + 2], i + 2)
        if not bearish and h[i] < l[i + 2]:
            best = (h[i], l[i + 2], i + 2)   # bull: zone top=l[i], bottom=l[i+2]
    return best


def atr(arr_h, arr_l, arr_c, n=20):
    tr = np.maximum(arr_h[1:] - arr_l[1:],
                    np.maximum(np.abs(arr_h[1:] - arr_c[:-1]),
                               np.abs(arr_l[1:] - arr_c[:-1])))
    out = np.empty(len(arr_c))
    out[0] = tr[0] if len(tr) else 0
    for i in range(1, len(out)):
        out[i] = (out[i - 1] * (n - 1) + tr[i - 1]) / n
    return out


def run(sym, ep, o, h, l, c, retrace_r=0.25, buf_mult=0.5, verbose=False):
    """Full engine; returns (trades, sweep_profile)."""
    n = len(c)
    sp = SPREAD[sym]
    vpu = VPU[sym]
    min_lot = MIN_LOT[sym]
    ep15, o5, h5, l5, c5 = resample(ep, o, h, l, c, 900)
    atr15 = atr(h5, l5, c5)
    sh15, sl15 = pools_sh_sl = swings(h5, l5, K_HTF)
    hi_lv, lo_lv = pools(h5, l5, c5, sh15, sl15)
    trades = []
    profile = dict(sweep=0, reject=0, entry=0)
    active_until = -1
    for direction, levels in (("short", hi_lv), ("long", lo_lv)):
        for li, lvl in levels:
            if li + 2 >= n or li < active_until:
                continue
            seg = np.where((h[li + 1:] > lvl) if direction == "short"
                           else (l[li + 1:] < lvl))[0]
            if not len(seg) or seg[0] - li > 7200:   # 5 days max wait
                continue
            sw = li + 1 + int(seg[0])
            profile["sweep"] += 1
            rej = next((sw + j for j in range(REJECT_BARS)
                        if sw + j < n and ((c[sw + j] < lvl) if direction == "short"
                                           else (c[sw + j] > lvl))), None)
            if rej is None:
                continue
            profile["reject"] += 1
            f = fvg_between(h, l, sw, rej, bearish=(direction == "short"))
            if f is None:
                continue
            ztop, zbot, form = f
            a = atr15[min(np.searchsorted(ep15, ep[sw]), len(atr15) - 1)]
            risk0 = ztop - zbot
            if direction == "short":
                entry_px = ztop + retrace_r * risk0
            else:
                entry_px = zbot - retrace_r * risk0
            e_seg = np.where((h[form + 1:min(form + 1 + ENTRY_WINDOW, n)] >= entry_px)
                             if direction == "short"
                             else (l[form + 1:min(form + 1 + ENTRY_WINDOW, n)] <= entry_px))[0]
            if not len(e_seg):
                continue
            e = form + 1 + int(e_seg[0])
            if direction == "short":
                stop = max(h[sw:rej + 1]) + buf_mult * a
                opp = [p for (ci2, p) in lo_lv if ci2 < e and p < entry_px]
                tgt = max(opp, key=lambda p: p) if opp else entry_px - FALLBACK_R * (stop - entry_px)
                if tgt >= entry_px - sp:
                    continue
            else:
                stop = min(l[sw:rej + 1]) - buf_mult * a
                opp = [p for (ci2, p) in hi_lv if ci2 < e and p > entry_px]
                tgt = min(opp) if opp else entry_px + FALLBACK_R * (stop * 0 + (stop - entry_px) * -1)
                if tgt <= entry_px + sp:
                    continue
            stop_dist = abs(stop - entry_px)
            if stop_dist <= 0 or tgt is None:
                continue
            lots = max(min_lot, round(RISK_USD / max(stop_dist * vpu, 1e-9), 2))
            if lots > 100:
                lots = 100
            out = None
            for j in range(e + 1, min(e + TIMEOUT, n)):
                if direction == "short":
                    hit_stop = h[j] >= stop
                    hit_tgt = l[j] <= tgt
                else:
                    hit_stop = l[j] <= stop
                    hit_tgt = h[j] >= tgt
                if hit_stop:
                    out = ("stop", stop); break
                if hit_tgt:
                    out = ("target", tgt); break
            if out is None:
                out = ("timeout", c[min(e + TIMEOUT, n - 1)])
            exit_px = out[1]
            pnl_u = (exit_px - entry_px) if direction == "long" else (entry_px - exit_px)
            pnl_u -= sp
            pnl_usd = pnl_u * vpu * lots
            trades.append(dict(win=out[0] == "target", kind=out[0], dir=direction,
                               pnl_u=pnl_u, pnl=pnl_usd, lots=lots,
                               stop_dist=stop_dist, e=e))
            profile["entry"] += 1
            active_until = e + 5
    return trades, profile


def report(name, trades, profile, vpu):
    print(f"\n--- {name} ---")
    print(f"  sweeps={profile['sweep']} rejected={profile['reject']} entries={profile['entry']}")
    if not trades:
        print("  no trades")
        return
    pnl = np.array([t["pnl"] for t in trades])
    wr = 100 * np.mean([t["win"] for t in trades])
    kinds = {k: sum(1 for t in trades if t["kind"] == k) for k in ("stop", "target", "timeout")}
    print(f"  n={len(pnl)} WR={wr:.0f}% kinds={kinds}")
    print(f"  avgPnL=${pnl.mean():+.3f} total=${pnl.sum():+.2f} | "
          f"maxDD=${min(np.minimum.accumulate(np.cumsum(pnl))):.2f} | "
          f"lot range {min(t['lots'] for t in trades)}-{max(t['lots'] for t in trades)}")


def main():
    # ---- in-sample: 30d M1 candles ----
    p = Path("data/synth_scan/v75_m1_30d.npz")
    m1 = np.load(p)["bars"] if p.exists() else None
    grid = [(0.25, 0.5), (0.0, 0.5), (0.25, 1.0), (0.5, 0.5), (0.0, 1.0)]
    best = None
    if m1 is not None:
        ep, o, h, l, c = m1_from_bars(m1)
        print(f"R_75 30d M1: {len(c):,} bars, price {c[0]:.0f} -> {c[-1]:.0f}")
        for rt, buf in grid:
            tr, pf = run("R_75", ep, o, h, l, c, rt, buf)
            pnl = sum(t["pnl"] for t in tr) if tr else 0
            print(f"  grid retrace={rt} buf={buf}: n={len(tr)} total=${pnl:+.2f}")
            if best is None or pnl > best[0]:
                best = (pnl, rt, buf)
    print(f"\nchosen variant: retrace={best[1]} buf={best[2]}" if best else "\nno 30d data")

    # ---- OOS: 4x24h tick windows + 1HZ75V ----
    rt, buf = (best[1], best[2]) if best else (0.25, 0.5)
    for tag, sym in [("0d", "R_75"), ("5d", "R_75"), ("12d", "R_75"),
                     ("19d", "R_75"), ("1hz", "1HZ75V")]:
        p = Path(f"data/synth_scan/v75_ticks_{tag}.npz")
        if not p.exists():
            continue
        d = np.load(p)
        ep, o, h, l, c = m1_from_ticks(d["ts"], d["px"].astype(float))
        tr, pf = run(sym, ep, o, h, l, c, rt, buf)
        report(f"{sym} {tag} (chosen variant)", tr, pf, VPU[sym])


if __name__ == "__main__":
    main()
