#!/usr/bin/env python3
"""SMC/ICT lens on Volatility indices (2026-09-21).

Translates the trader concepts into testable operations:
  swings        fractal pivots (k bars each side, confirmed k bars later)
  structure     consecutive swing labels HH/HL/LH/LL; persistence test:
                P(next swing same label | current) vs 0.5 (trend concept)
  BOS/CHoCH     close breaks last confirmed swing high/low; BOS = with trend,
                CHoCH = first break against it; forward return after event
  FVG           3-bar gap: bull low[i] > high[i-2], zone (high[i-2], low[i]);
                touch@N, full-fill@N, forward return after formation & touch
  liquidity SFP bar wicks through prior swing high but closes back under
  sweep         (bearish sweep of buyside liquidity); forward return after
  premium/discount: pullback depth <50% of impulse leg vs deeper; continuation rates

Control: row-shuffled surrogate (same bars, order destroyed) -> the SAME
pipeline. Real-vs-shuffle delta is the only thing that counts; patterns that
score equally on shuffled data are selection illusions.

Series: clean 30d M1 (V100 1s, V30 1s), M1 built from 24h tick caches
(1HZ10V/50V/100V, R_10), and 1s/2s bars from the same caches (LTF read).
Read-only: no live state, no MT5, no measurement files.
"""
import csv
import sys
from pathlib import Path

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

sys.path.insert(0, str(Path(__file__).parent.parent))

K = 3            # fractal strength
FILL_N = 20      # bars allowed for FVG touch/full fill
FWD = (1, 3, 5)  # forward-return horizons in bars

# ---------- spreads (Sep 10 live measurements, normalized keys) ----------
def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())

SPREAD = {}
for r in csv.DictReader(open("data/synth_scan/bar_scan_results.csv")):
    SPREAD[norm(r["name"])] = float(r["spread"])


def spread_for(sym):
    s = sym.lower()
    if "r_10" in s:
        return SPREAD.get(norm("Volatility 10 Index"))
    if "1hz10v" in s:
        return SPREAD.get(norm("Volatility 10 (1s) Index"))
    if "1hz50v" in s:
        return SPREAD.get(norm("Volatility 50 (1s) Index"))
    if "1hz100v" in s:
        return SPREAD.get(norm("Volatility 100 (1s) Index"))
    if "v100" in s:
        return SPREAD.get(norm("Volatility 100 (1s) Index"))
    if "v30" in s:
        return SPREAD.get(norm("Volatility 30 (1s) Index"))
    return None


# ---------- pivots ----------
def swing_masks(high, low, k=K):
    w = 2 * k + 1
    hmax = swv(high, w).max(axis=1)
    lmin = swv(low, w).min(axis=1)
    sh = np.zeros(len(high), bool)
    sl = np.zeros(len(low), bool)
    sh[k:len(high) - k] = high[k:len(high) - k] == hmax
    sl[k:len(low) - k] = low[k:len(low) - k] == lmin
    return sh, sl


def fwd_ret(close, idx, h):
    """close[idx+h] - close[idx] for valid idx (price units)."""
    ok = idx + h < len(close)
    out = np.full(idx.shape, np.nan)
    out[ok] = close[idx[ok] + h] - close[idx[ok]]
    return out


def scan(ep, o, h, l, c):
    """Return dict of SMC stats for one OHLC series."""
    n = len(c)
    sh_m, sl_m = swing_masks(h, l)
    sh_idx = np.where(sh_m)[0]
    sl_idx = np.where(sl_m)[0]
    res = {"n_bars": n, "n_SH": len(sh_idx), "n_SL": len(sl_idx)}

    # ----- structure labels: consecutive swing highs / lows -----
    def labels(px):
        return px[1:] > px[:-1]  # True = higher (HH for highs, HL for lows)

    lab_h = labels(h[sh_idx])
    lab_l = labels(l[sl_idx])
    def persist(lab):
        if len(lab) < 2:
            return np.nan, 0
        same = (lab[1:] == lab[:-1]).mean()
        z = (same - 0.5) / np.sqrt(0.25 / (len(lab) - 1))
        return z, len(lab) - 1
    res["persist_H_z"], res["persist_H_n"] = persist(lab_h)
    res["persist_L_z"], res["persist_L_n"] = persist(lab_l)

    # ----- BOS / CHoCH -----
    events = []  # (bar_idx, kind) kind in {BOS_up, CHoCH_up, BOS_dn, CHoCH_dn}
    trend = 0
    cur_sh = None  # (price, confirm_idx)
    cur_sl = None
    sh_conf = [(i + K, h[i]) for i in sh_idx]
    sl_conf = [(i + K, l[i]) for i in sl_idx]
    ih = il = 0
    for i in range(K, n):
        while ih < len(sh_conf) and sh_conf[ih][0] <= i:
            cur_sh = sh_conf[ih][1]; ih += 1
        while il < len(sl_conf) and sl_conf[il][0] <= i:
            cur_sl = sl_conf[il][1]; il += 1
        if cur_sh is not None and c[i] > cur_sh:
            events.append((i, "up", "BOS" if trend >= 0 else "CHoCH"))
            trend = 1; cur_sh = None
        elif cur_sl is not None and c[i] < cur_sl:
            events.append((i, "dn", "BOS" if trend <= 0 else "CHoCH"))
            trend = -1; cur_sl = None
    up_idx = np.array([e[0] for e in events if e[1] == "up"], dtype=int)
    dn_idx = np.array([e[0] for e in events if e[1] == "dn"], dtype=int)
    choch = np.array([e[0] for e in events if e[2] == "CHoCH"], dtype=int)
    res["n_BOS"] = len(events) - len(choch)
    res["n_CHoCH"] = len(choch)
    for hzn in FWD:
        fu = fwd_ret(c, up_idx, hzn) if len(up_idx) else np.array([])
        fd = fwd_ret(c, dn_idx, hzn) if len(dn_idx) else np.array([])
        # directional edge: up-events expected up, dn-events expected down
        d = np.concatenate([fu, -fd]) if len(fu) + len(fd) else np.array([])
        res[f"bos_fwd{hzn}"] = (np.nanmean(d) if len(d) else np.nan,
                                len(d))
    fc = fwd_ret(c, choch, 3) if len(choch) else np.array([])
    res["choch_fwd3"] = (np.nanmean(fc) if len(fc) else np.nan, len(fc))

    # ----- FVGs (classic 3-bar: zone between bar1 wick and bar3 wick) -----
    bull_fmt = np.where(l[2:] > h[:-2])[0]   # i = bar1; zone (h[i], l[i+2])
    bear_fmt = np.where(h[2:] < l[:-2])[0]   # i = bar1; zone (h[i+2], l[i])
    def fvg_stats(fmt_idx, is_bull):
        touch = []; full = []; fwd_fmt = []; fwd_touch = []
        for i in fmt_idx:
            zi = i + 2                        # third bar of the pattern
            if zi >= n:
                continue
            zbot, ztop = (h[i], l[zi]) if is_bull else (h[zi], l[i])
            end = min(zi + FILL_N, n)
            seg_l, seg_h, seg_c = l[zi + 1:end], h[zi + 1:end], c[zi + 1:end]
            if is_bull:
                t = seg_l <= ztop
                f = seg_l <= zbot
            else:
                t = seg_h >= zbot
                f = seg_h >= ztop
            touch.append(t.any() if len(t) else False)
            full.append(f.any() if len(f) else False)
            fwd_fmt.append(c[min(zi + 5, n - 1)] - c[zi])
            if t.any():
                j = zi + 1 + int(np.argmax(t))
                d = 1 if is_bull else -1
                fwd_touch.append(d * (c[min(j + 5, n - 1)] - c[j]))
        return (len(fmt_idx),
                100 * np.mean(touch) if touch else np.nan,
                100 * np.mean(full) if full else np.nan,
                np.nanmean(fwd_fmt) if fwd_fmt else np.nan,
                np.nanmean(fwd_touch) if fwd_touch else np.nan,
                len(fwd_touch))
    res["fvg_bull"] = fvg_stats(bull_fmt, True)
    res["fvg_bear"] = fvg_stats(bear_fmt, False)

    # ----- liquidity sweeps (SFP) -----
    sweep_idx = []
    for (ci, p) in sh_conf:  # buyside: wick above, close back under
        j0 = ci + 1
        seg = slice(j0, min(j0 + 40, n))
        over = np.where((h[seg] > p) & (c[seg] < p))[0]
        if len(over):
            sweep_idx.append(j0 + int(over[0]))
    for (ci, p) in sl_conf:  # sellside: wick under, close back over
        j0 = ci + 1
        seg = slice(j0, min(j0 + 40, n))
        under = np.where((l[seg] < p) & (c[seg] > p))[0]
        if len(under):
            sweep_idx.append(j0 + int(under[0]))
    sweep_idx = np.array(sorted(set(sweep_idx)), dtype=int)
    res["n_sweep"] = len(sweep_idx)
    return res, sweep_idx, sh_conf, sl_conf


def sweep_directional(ep, o, h, l, c, sh_conf, sl_conf):
    """Directional sweep stats: bearish SFP (above SH, close under) -> expect down."""
    n = len(c)
    dn, up = [], []
    for (ci, p) in sh_conf:
        j0 = ci + 1
        seg = slice(j0, min(j0 + 40, n))
        over = np.where((h[seg] > p) & (c[seg] < p))[0]
        if len(over):
            j = j0 + int(over[0])
            dn.append(c[min(j + 5, n - 1)] - c[j])
    for (ci, p) in sl_conf:
        j0 = ci + 1
        seg = slice(j0, min(j0 + 40, n))
        under = np.where((l[seg] < p) & (c[seg] > p))[0]
        if len(under):
            j = j0 + int(under[0])
            up.append(c[min(j + 5, n - 1)] - c[j])
    dn, up = np.array(dn), np.array(up)
    return (np.nanmean(dn) if len(dn) else np.nan, len(dn),
            np.nanmean(up) if len(up) else np.nan, len(up))


def summarize(sym, ep, o, h, l, c):
    res, sweep_idx, sh_conf, sl_conf = scan(ep, o, h, l, c)
    dn_m, dn_n, up_m, up_n = sweep_directional(ep, o, h, l, c, sh_conf, sl_conf)
    sp = spread_for(sym)
    lines = [f"\n=== {sym} === bars={res['n_bars']:,} SH={res['n_SH']:,} SL={res['n_SL']:,}"
             f" | spread={sp if sp else '?'}"]
    lines.append(f"structure : persist(HH|HH) z={res['persist_H_z']:+.2f} (n={res['persist_H_n']})"
                 f" persist(LL|LL) z={res['persist_L_z']:+.2f} (n={res['persist_L_n']})"
                 f" | BOS={res['n_BOS']} CHoCH={res['n_CHoCH']}")
    b1 = res["bos_fwd1"]; b3 = res["bos_fwd3"]
    lines.append(f"BOS edge  : fwd1 {b1[0]:+.4f}u (n={b1[1]}) fwd3 {b3[0]:+.4f}u (n={b3[1]})"
                 f" | CHoCH fwd3 {res['choch_fwd3'][0]:+.4f}u (n={res['choch_fwd3'][1]})")
    for tag in ("fvg_bull", "fvg_bear"):
        nn, t, f, ff, ft, nft = res[tag]
        lines.append(f"{tag:9s}: n={nn} touch@{FILL_N}={t:.1f}% full={f:.1f}% |"
                     f" fwd5 after form {ff:+.4f}u | after touch {ft:+.4f}u (n={nft})")
    lines.append(f"sweeps    : bearish(SFP@SH) {dn_m:+.4f}u (n={dn_n}) |"
                 f" bullish(SFP@SL) {up_m:+.4f}u (n={up_n})")
    if sp:
        best = max(abs(x) for x in (b1[0], b3[0], res['fvg_bull'][4],
                    res['fvg_bear'][4], dn_m, up_m) if x == x and not np.isnan(x))
        lines.append(f"vs spread : best |edge| {best:.4f}u = {best/sp:.2f}x spread")
    return "\n".join(lines)


def series_from_bars30(path):
    d = np.load(path)
    return d[:, 0], d[:, 1], d[:, 2], d[:, 3], d[:, 4]


def series_from_ticks(npz, bar_sec):
    d = np.load(npz)
    ts, px = d["ts"], d["px"]
    b = (ts // bar_sec).astype(np.int64)
    ub, inv = np.unique(b, return_inverse=True)
    op = px[np.searchsorted(b, ub, side="left")]
    cl = px[np.searchsorted(b, ub, side="right") - 1]
    hi = np.maximum.reduceat(px, np.searchsorted(b, ub, side="left"))
    lo = np.minimum.reduceat(px, np.searchsorted(b, ub, side="right") - 1)
    return ub * bar_sec, op, hi, lo, cl


def ret_surrogate(ep, o, h, l, c, seed=11):
    """Permuted-return surrogate: same return distribution, same per-bar wick
    geometry, sequence destroyed. VALID magnitude null for fwd-return stats
    (level-shuffle is not - it inflates return scale ~sqrt(n))."""
    rng = np.random.default_rng(seed)
    ret = np.diff(c)
    c2 = np.concatenate(([c[0]], c[0] + np.cumsum(rng.permutation(ret))))
    o2 = np.concatenate(([c2[0]], c2[:-1] + (o[1:] - c[:-1])))
    wu = h - np.maximum(o, c)   # upper wick per bar
    wd = np.minimum(o, c) - l   # lower wick per bar
    h2 = np.maximum(o2, c2) + wu
    l2 = np.minimum(o2, c2) - wd
    return ep, o2, h2, l2, c2


def main():
    jobs = []
    p100 = Path("data/synth_scan/bars30_Volatility_100_1s_Index.npy")
    p30 = Path("data/synth_scan/bars30_Volatility_30_1s_Index.npy")
    if p100.exists():
        jobs.append(("V100_1s_M1", series_from_bars30(p100)))
    if p30.exists():
        jobs.append(("V30_1s_M1", series_from_bars30(p30)))
    for s in ("1HZ10V", "1HZ50V", "1HZ100V", "R_10"):
        p = Path(f"data/synth_scan/ticks24h_{s}.npz")
        if p.exists():
            bar = 2 if s == "R_10" else 1
            jobs.append((f"{s}_LTF{bar}s", series_from_ticks(p, bar)))
            jobs.append((f"{s}_M1", series_from_ticks(p, 60)))
    print(f"scanning {len(jobs)} real series (+ level-shuffle + ret-surrogate controls)")
    for sym, (ep, o, h, l, c) in jobs:
        print(summarize(sym, ep, o, h, l, c))
        rng = np.random.default_rng(7)
        perm = rng.permutation(len(c))
        print(summarize(sym + " [SHUFFLED]", ep[perm], o[perm], h[perm],
                        l[perm], c[perm]))
        print(summarize(sym + " [RET-SURR]", *ret_surrogate(ep, o, h, l, c)))


if __name__ == "__main__":
    main()
