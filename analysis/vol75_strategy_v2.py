#!/usr/bin/env python3
"""V75 Liquidation Reversion v2 (2026-09-21, late).

v1 (full HTF confluence) starved: 1 trade/30d. This is the tradeable core
the user asked for: "spots where people got liquified" = sweeps of confirmed
M5 swing extremes, traded back with professional TP/SL:

  LEVEL    every confirmed M5 fractal swing (K=3), long/short side
  SWEEP    M1 wick trades beyond the level (stops ran = liquidation event)
  RECLAIM  close back inside within `rej_bars` (trap confirmed)
  ENTRY    market on the reclaim close
  STOP     sweep extreme +/- `buf` x ATR20(M5)   [where the trap would be wrong]
  TARGET   `tp_r` x risk distance                [min 1x spread away]
  TIMEOUT  `tmo` bars -> market exit
  Conservative: loss counted when a bar grazes both stop and target; spread
    charged once per round trip; sizing = risk_usd / stop distance, capped.

Also profiles LIQUIDATION SPOTS: every sweep (with/without reclaim) and the
  forward M1 path after it - the map of where people got liquified and what
  happens next.

Windows: R_75 30d M1 (tuning) + 4x24h tick windows + 1HZ75V (OOS).
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

VPU = {"R_75": 0.0001, "1HZ75V": 0.05}
MIN_LOT = {"R_75": 0.01, "1HZ75V": 0.05}
SPREAD = {"R_75": 14.79, "1HZ75V": 1.80}

K = 3
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


def swings(h, l, k=K):
    from numpy.lib.stride_tricks import sliding_window_view as swv
    w = 2 * k + 1
    sh = np.where(h[k:len(h) - k] == swv(h, w).max(axis=1))[0] + k
    sl = np.where(l[k:len(l) - k] == swv(l, w).min(axis=1))[0] + k
    return sh, sl


def atr_wilder(h, l, c, n=20):
    tr = np.maximum(h[1:] - l[1:],
                    np.maximum(np.abs(h[1:] - c[:-1]),
                               np.abs(l[1:] - c[:-1])))
    out = np.empty(len(c))
    out[0] = tr[0] if len(tr) else 0
    for i in range(1, len(out)):
        out[i] = (out[i - 1] * (n - 1) + tr[i - 1]) / n
    return out


def run(sym, ep, o, h, l, c, rej_bars=3, buf=0.5, tp_r=1.5, tmo=120,
        min_age=60, max_wait=1440, collect=False, mode="reversion"):
    """mode='reversion': reclaim close back inside -> trade opposite the grab.
    mode='momentum':  close-through within rej_bars -> trade WITH the break."""
    n = len(c)
    sp = SPREAD[sym]
    vpu = VPU[sym]
    min_lot = MIN_LOT[sym]
    ep5, o5, h5, l5, c5 = resample(ep, o, h, l, c, 300)
    atr5 = atr_wilder(h5, l5, c5)
    sh5, sl5 = swings(h5, l5, K)
    atr_at = {}
    trades = []
    profile = dict(sweeps=0, reclaims=0, entries=0)
    sweeps_all = []
    active_until = -1

    def atr_for(ts_value):
        j = np.searchsorted(ep5, ts_value)
        return atr5[min(j, len(atr5) - 1)]

    for levels, direction, is_high in ((sh5, "short", True), (sl5, "long", False)):
        for i in levels:
            ci = i + K
            if ci + 2 >= n or ci < active_until:
                continue
            lvl = h5[i] if is_high else l5[i]
            cond = (h[ci + 1:] > lvl) if is_high else (l[ci + 1:] < lvl)
            seg = np.where(cond)[0]
            # seg[0] = M1 bars from confirmation to first touch (true age of grab)
            if not len(seg) or seg[0] > max_wait:
                continue
            if seg[0] < min_age:      # touched immediately = momentum, not a pool
                continue
            sw = ci + 1 + int(seg[0])
            profile["sweeps"] += 1
            ext = h[sw:sw + rej_bars].max() if is_high else l[sw:sw + rej_bars].min()
            rec = next((sw + j for j in range(rej_bars)
                        if sw + j < n and ((c[sw + j] < lvl) if is_high
                                           else (c[sw + j] > lvl))), None)
            # ---- liquidation-spot profile (independent of trading) ----
            if collect:
                end = min(sw + 61, n)
                f5 = c[min(sw + 5, n - 1)] - c[sw]
                f15 = c[min(sw + 15, n - 1)] - c[sw]
                f60 = c[min(sw + 60, n - 1)] - c[sw]
                sweeps_all.append(dict(sw=sw, is_high=is_high, lvl=lvl,
                                       age=int(seg[0]),
                                       beyond=float(ext - lvl if is_high else lvl - ext),
                                       reclaimed=rec is not None,
                                       f5=float(f5), f15=float(f15), f60=float(f60)))
            if mode == "reversion":
                if rec is None:
                    continue
                profile["reclaims"] += 1
                e = rec
                entry_px = c[e]
                is_long = not is_high
            else:
                # join the break: first close beyond the level after the grab
                e2 = next((sw + j for j in range(rej_bars)
                           if sw + j < n and ((c[sw + j] > lvl) if is_high
                                              else (c[sw + j] < lvl))), None)
                if e2 is None:
                    continue          # no break within window: stand down
                e = e2
                entry_px = c[e]
                is_long = is_high
            a = atr_for(ep[e])
            if is_long:
                stop = ext - buf * a   # stop back inside the level
                risk = entry_px - stop
            else:
                stop = ext + buf * a
                risk = stop - entry_px
            if risk <= sp or risk <= 0:
                continue
            tp = entry_px + tp_r * risk if is_long else entry_px - tp_r * risk
            lots = max(min_lot, round(RISK_USD / max(risk * vpu, 1e-9), 2))
            if lots > 500:
                lots = 500
            out = None
            for j in range(e + 1, min(e + tmo, n)):
                if is_long:
                    hs, ht = l[j] <= stop, h[j] >= tp
                else:
                    hs, ht = h[j] >= stop, l[j] <= tp
                if hs:                     # loss wins ties
                    out = ("stop", stop); break
                if ht:
                    out = ("target", tp); break
            if out is None:
                out = ("timeout", c[min(e + tmo, n - 1)])
            exit_px = out[1]
            pnl_u = (exit_px - entry_px) if is_long else (entry_px - exit_px)
            pnl_u -= sp
            trades.append(dict(win=out[0] == "target", kind=out[0],
                               dir="long" if is_long else "short",
                               pnl_u=pnl_u, pnl=pnl_u * vpu * lots, lots=lots,
                               risk_u=risk, e=e, sw=sw))
            profile["entries"] += 1
            active_until = e + 5
    return (trades, profile, sweeps_all) if collect else (trades, profile, None)


def report(name, trades, profile):
    print(f"\n--- {name} ---")
    print(f"  sweeps={profile['sweeps']} reclaims={profile['reclaims']} entries={profile['entries']}")
    if not trades:
        print("  no trades")
        return
    pnl = np.array([t["pnl"] for t in trades])
    wr = 100 * np.mean([t["win"] for t in trades])
    kinds = {k: sum(1 for t in trades if t["kind"] == k) for k in ("stop", "target", "timeout")}
    eq = np.cumsum(pnl)
    print(f"  n={len(pnl)} WR={wr:.0f}% kinds={kinds} | "
          f"avg ${pnl.mean():+.3f} total ${pnl.sum():+.2f} maxDD ${-min(eq.min(), 0):.2f}")


def profile_sweeps(sweeps_all, name):
    if not sweeps_all:
        return
    arr = sweeps_all
    rec = [s for s in arr if s["reclaimed"]]
    norec = [s for s in arr if not s["reclaimed"]]
    hi = [s for s in arr if s["is_high"]]
    lo = [s for s in arr if not s["is_high"]]
    def m(lst, k):
        return float(np.mean([s[k] for s in lst])) if lst else float("nan")
    print(f"\n=== LIQUIDATION MAP {name}: {len(arr)} stale-level grabs "
          f"({len(rec)} reclaimed {100*len(rec)/len(arr):.0f}%) "
          f"ages med {np.median([s['age'] for s in arr]):.0f} bars ===")
    print(f"  all grabs    fwd5 {m(arr,'f5'):+.1f}u fwd15 {m(arr,'f15'):+.1f}u "
          f"fwd60 {m(arr,'f60'):+.1f}u")
    print(f"  HIGH swept (n={len(hi)}): fwd15 {m(hi,'f15'):+.1f}u | "
          f"LOW swept (n={len(lo)}): fwd15 {m(lo,'f15'):+.1f}u")
    print(f"  RECLAIMED (n={len(rec)}): fwd5 {m(rec,'f5'):+.1f}u "
          f"fwd15 {m(rec,'f15'):+.1f}u fwd60 {m(rec,'f60'):+.1f}u  <- reversion payload")
    print(f"  NOT recld (n={len(norec)}): fwd5 {m(norec,'f5'):+.1f}u "
          f"fwd15 {m(norec,'f15'):+.1f}u fwd60 {m(norec,'f60'):+.1f}u  <- continuation risk")
    med = float(np.median([s["beyond"] for s in arr]))
    big = [s for s in arr if s["beyond"] > med]
    small = [s for s in arr if s["beyond"] <= med]
    def pct(lst):
        return 100 * sum(s["reclaimed"] for s in lst) / len(lst) if lst else float("nan")
    print(f"  DEEP grabs (>{med:.0f}u beyond, n={len(big)}): reclaim {pct(big):.0f}% "
          f"fwd15 {m(big,'f15'):+.1f}u | SHALLOW (n={len(small)}): "
          f"reclaim {pct(small):.0f}% fwd15 {m(small,'f15'):+.1f}u")


def main():
    grid = [(60, 3, 0.5, 1.5), (60, 3, 0.5, 2.0), (240, 3, 0.5, 1.5),
            (240, 3, 0.5, 2.0), (720, 3, 0.5, 1.5), (720, 5, 0.5, 2.0),
            (720, 5, 0.25, 2.0), (1440, 5, 0.5, 2.0)]
    p = Path("data/synth_scan/v75_m1_30d.npz")
    best = None
    if p.exists():
        ep, o, h, l, c = m1_from_bars(np.load(p)["bars"])
        print(f"R_75 30d M1 tuning: {len(c):,} bars")
        for ma, rb, bf, tpr in grid:
            tr, pf, sw = run("R_75", ep, o, h, l, c, rb, bf, tpr, 120,
                             min_age=ma, collect=True)
            tot = sum(t["pnl"] for t in tr) if tr else 0
            print(f"  age>={ma/60:.0f}h rej={rb} buf={bf} tp={tpr}R: "
                  f"grabs={pf['sweeps']} n={len(tr)} "
                  f"WR={100*sum(t['win'] for t in tr)/max(len(tr),1):.0f}% total=${tot:+.2f}")
            if best is None or (len(tr) >= 3 and tot > best[0]):
                best = (tot, ma, rb, bf, tpr, sw)
    if best:
        print(f"\nchosen: age>={best[1]/60:.0f}h rej={best[2]} buf={best[3]} tp={best[4]}R")
        profile_sweeps(best[5], "R_75 30d (tuning)")
    rb, bf, tpr = (best[2], best[3], best[4]) if best else (3, 0.5, 1.5)
    ma = best[1] if best else 60
    if p.exists():
        trm, pfm, swm = run("R_75", ep, o, h, l, c, rb, bf, tpr, 120,
                            min_age=ma, collect=True, mode="momentum")
        report("R_75 30d MOMENTUM mirror (join the break)", trm, pfm)
        if swm:
            profile_sweeps(swm, "R_75 30d momentum grabs")


    for tag in ("0d", "5d", "12d", "19d", "1hz"):
        sym = "1HZ75V" if tag == "1hz" else "R_75"
        p = Path(f"data/synth_scan/v75_ticks_{tag}.npz")
        if not p.exists():
            continue
        d = np.load(p)
        ep, o, h, l, c = m1_from_ticks(d["ts"], d["px"].astype(float))
        tr, pf, sw = run(sym, ep, o, h, l, c, rb, bf, tpr, 120,
                         min_age=ma, collect=True)
        report(f"{sym} {tag} (OOS)", tr, pf)
        if sw:
            profile_sweeps(sw, f"{sym} {tag}")


if __name__ == "__main__":
    main()
