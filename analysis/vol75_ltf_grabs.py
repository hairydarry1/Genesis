#!/usr/bin/env python3
"""V75 LTF liquidation grabs (2026-09-22).

M5-stale-level version starves (9-14 grabs/30d, both modes lose). This drops
the playbook to M1 swings on the tick windows: same chain (stale level ->
grab -> reclaim/break -> ATR stop -> R target) but with enough events to be
a real statistical object. Fixed config, NO tuning: in-sample = 30d M1 file,
OOS = 5 independent 24h tick windows. Verdict = replication, not p-values.

  GRAB     M1 wick through a stale M1 fractal level (K=2, age>=15 bars)
  FADE     reclaim (close back inside within 3 bars) -> trade opposite grab
  JOIN     close beyond within 3 bars -> trade with the break
  STOP     grab extreme +/- 0.5 x ATR20(M1); loss wins ties; spread 1x/RT
  TARGET   1.0R and 1.5R variants
Also prints the liquidation map: fwd path after every grab.
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

SPREAD = {"R_75": 14.79, "1HZ75V": 1.80, "R_10": 0.188, "R_25": 0.301,
          "1HZ10V": 0.38, "1HZ50V": 47.46, "1HZ100V": 0.39}

K = 2
MIN_AGE = 15        # M1 bars the level sat untouched before the grab
MAX_WAIT = 240      # give up waiting for the grab after 4h
REJ = 3
BUF = 0.5
TPRS = (1.0, 1.5)
TMO = 60            # 1h timeout
SP_COOLDOWN = 5     # bars before same level re-arms (approx via active_until)


def m1_from_ticks(ts, px):
    b = (ts // 60).astype(np.int64)
    ub = np.unique(b)
    left = np.searchsorted(b, ub, side="left")
    right = np.searchsorted(b, ub, side="right")
    return ub * 60.0, px[left], np.maximum.reduceat(px, left), \
        np.minimum.reduceat(px, right - 1), px[right - 1]


def resample(ep, o, h, l, c, sec):
    b = (ep // sec).astype(np.int64)
    ub = np.unique(b)
    left = np.searchsorted(b, ub, side="left")
    right = np.searchsorted(b, ub, side="right")
    return ub * sec, o[left], np.maximum.reduceat(h, left), \
        np.minimum.reduceat(l, left), c[right - 1]


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


def run(sym, ep, o, h, l, c, mode, tp_r):
    n = len(c)
    sp = SPREAD[sym]
    ep5, o5, h5, l5, c5 = resample(ep, o, h, l, c, 300)
    atr5 = atr_wilder(h5, l5, c5)
    atr1 = atr_wilder(h, l, c)
    sh, sl = swings(h, l, K)
    trades = []
    grabs = []

    def atr1_for(t):
        j = np.searchsorted(ep5, t)
        return atr1[min(j, n - 1)]

    def atr5_for(t):
        j = np.searchsorted(ep5, t)
        return atr5[min(j, len(atr5) - 1)]

    for lv, is_high in ((sh, True), (sl, False)):
        active_until = -1
        for i in lv:
            ci = i + K
            if ci + 2 >= n or ci < active_until:
                continue
            lvl = h[i] if is_high else l[i]
            cond = (h[ci + 1:] > lvl) if is_high else (l[ci + 1:] < lvl)
            seg = np.where(cond)[0]
            if not len(seg) or seg[0] > MAX_WAIT or seg[0] < MIN_AGE:
                continue
            sw = ci + 1 + int(seg[0])
            ext = h[sw:sw + REJ].max() if is_high else l[sw:sw + REJ].min()
            beyond = abs(ext - lvl)
            a5 = atr5_for(ep[sw])
            rec = next((sw + j for j in range(REJ)
                        if sw + j < n and ((c[sw + j] < lvl) if is_high
                                           else (c[sw + j] > lvl))), None)
            brk = next((sw + j for j in range(REJ)
                        if sw + j < n and ((c[sw + j] > lvl) if is_high
                                           else (c[sw + j] < lvl))), None)
            f5 = c[min(sw + 5, n - 1)] - c[sw]
            f15 = c[min(sw + 15, n - 1)] - c[sw]
            f60 = c[min(sw + 60, n - 1)] - c[sw]
            grabs.append(dict(sw=sw, is_high=is_high, beyond=beyond,
                              reclaimed=rec is not None, f5=f5, f15=f15,
                              f60=f60))
            if mode == "fade":
                if rec is None:
                    continue
                e, entry_px, is_long = rec, c[rec], not is_high
                stop = ext - BUF * a5 if is_long else ext + BUF * a5
            else:
                if brk is None:
                    continue
                e, entry_px, is_long = brk, c[brk], is_high
                stop = ext - BUF * a5 if is_long else ext + BUF * a5
            risk = (entry_px - stop) if is_long else (stop - entry_px)
            if risk <= sp or risk <= 0:
                continue
            tp = entry_px + tp_r * risk if is_long else entry_px - tp_r * risk
            out = None
            for j in range(e + 1, min(e + TMO, n)):
                hs = l[j] <= stop if is_long else h[j] >= stop
                ht = h[j] >= tp if is_long else l[j] <= tp
                if hs:
                    out = ("stop", stop); break
                if ht:
                    out = ("target", tp); break
            if out is None:
                out = ("timeout", c[min(e + TMO, n - 1)])
            x = out[1]
            pnl_u = ((x - entry_px) if is_long else (entry_px - x)) - sp
            trades.append(dict(win=out[0] == "target", kind=out[0],
                               pnl_u=pnl_u, e=e))
            active_until = e + SP_COOLDOWN
    return trades, grabs


def report(name, trades, grabs):
    rec = sum(1 for g in grabs if g["reclaimed"])
    hi = [g for g in grabs if g["is_high"]]
    lo = [g for g in grabs if not g["is_high"]]
    r_g = [g for g in grabs if g["reclaimed"]]
    nr = [g for g in grabs if not g["reclaimed"]]

    def m(lst, k):
        return float(np.mean([g[k] for g in lst])) if lst else float("nan")
    print(f"\n=== {name} ===")
    print(f"  grabs n={len(grabs)} (high {len(hi)} / low {len(lo)}) "
          f"reclaim {rec}/{len(grabs)} = {100*rec/max(len(grabs),1):.0f}%")
    print(f"  ALL   fwd5 {m(grabs,'f5'):+7.1f}u  fwd15 {m(grabs,'f15'):+7.1f}u  "
          f"fwd60 {m(grabs,'f60'):+7.1f}u")
    print(f"  FADE-check reclaimed  fwd15 {m(r_g,'f15'):+7.1f}u | "
          f"not-recld fwd15 {m(nr,'f15'):+7.1f}u")
    if trades:
        pnl = np.array([t["pnl_u"] for t in trades])
        wr = 100 * np.mean([t["win"] for t in trades])
        kinds = {k: sum(1 for t in trades if t["kind"] == k)
                 for k in ("stop", "target", "timeout")}
        print(f"  TRADES n={len(pnl)} WR={wr:.0f}% kinds={kinds} "
              f"avg {pnl.mean():+.1f}u net {pnl.mean()*len(pnl):+.0f}u "
              f"({pnl.mean()/SPREAD['R_75'] if 'R_75' in name else pnl.mean()/SPREAD['1HZ75V']:+.2f}x spread)")


def main():
    # ---- family replication: fade config on every cached window/symbol ----
    print("########## FAMILY REPLICATION (fade, fixed config) ##########")
    fam = [
        ("data/synth_scan/ticks24h_R_10.npz", "R_10"),
        ("data/synth_scan/ticks12h_R_10_12d.npz", "R_10"),
        ("data/synth_scan/ticks12h_R_10_19d.npz", "R_10"),
        ("data/synth_scan/ticks12h_R_25_12d.npz", "R_25"),
        ("data/synth_scan/ticks12h_R_25_19d.npz", "R_25"),
        ("data/synth_scan/ticks24h_1HZ10V.npz", "1HZ10V"),
        ("data/synth_scan/ticks48h_1HZ10V.npz", "1HZ10V"),
        ("data/synth_scan/ticks24h_1HZ50V.npz", "1HZ50V"),
        ("data/synth_scan/ticks48h_1HZ50V.npz", "1HZ50V"),
        ("data/synth_scan/ticks24h_1HZ100V.npz", "1HZ100V"),
        ("data/synth_scan/ticks48h_1HZ100V.npz", "1HZ100V"),
    ]
    for path, sym in fam:
        p = Path(path)
        if not p.exists():
            continue
        dd = np.load(p)
        ep, o, h, l, c = m1_from_ticks(dd["ts"], dd["px"].astype(float))
        for tpr in TPRS:
            tr, gr = run(sym, ep, o, h, l, c, "fade", tpr)
            report(f"{sym} {p.stem} fade tp={tpr}R (OOS)", tr, gr)

    # ---- V75 in-sample + OOS battery ----
    d = np.load("data/synth_scan/v75_m1_30d.npz")
    bars = d["bars"]
    ep, o, h, l, c = bars[:, 0], bars[:, 1], bars[:, 2], bars[:, 3], bars[:, 4]
    for mode in ("fade", "join"):
        for tpr in TPRS:
            tr, gr = run("R_75", ep, o, h, l, c, mode, tpr)
            report(f"R_75 30d M1 {mode} tp={tpr}R (IN-SAMPLE)", tr, gr)

    # OOS: tick windows
    for tag in ("0d", "5d", "12d", "19d"):
        p = Path(f"data/synth_scan/v75_ticks_{tag}.npz")
        if not p.exists():
            continue
        dd = np.load(p)
        ep, o, h, l, c = m1_from_ticks(dd["ts"], dd["px"].astype(float))
        for mode in ("fade", "join"):
            for tpr in TPRS:
                tr, gr = run("R_75", ep, o, h, l, c, mode, tpr)
                report(f"R_75 {tag} M1 {mode} tp={tpr}R (OOS)", tr, gr)
    p = Path("data/synth_scan/v75_ticks_1hz.npz")
    if p.exists():
        dd = np.load(p)
        ep, o, h, l, c = m1_from_ticks(dd["ts"], dd["px"].astype(float))
        for mode in ("fade", "join"):
            for tpr in TPRS:
                tr, gr = run("1HZ75V", ep, o, h, l, c, mode, tpr)
                report(f"1HZ75V 1hz M1 {mode} tp={tpr}R (OOS)", tr, gr)


if __name__ == "__main__":
    main()
