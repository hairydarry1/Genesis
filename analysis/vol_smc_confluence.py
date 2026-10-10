#!/usr/bin/env python3
"""HTF->LTF confluence backtest for Volatility indices (2026-09-21, late).

The SMC playbook, end to end (short side; long is mirrored):
  HTF map (M15): confirmed fractal swings = liquidity pools. Keep the
    UNMITIGATED ones (price hasn't closed beyond since confirmation).
  Trigger (M1):
    1. SWEEP     M1 high trades above an unmitigated HTF swing high
    2. REJECT    a close back below that level within 5 bars
    3. DISPLACE  a bearish 3-bar FVG formed between sweep and rejection
    4. ENTRY     short at first retrace into the FVG zone top (limit, 20 bars)
    5. STOP      sweep extreme + buffer; TARGET nearest unmitigated HTF swing
                 low (or 2R if none); timeout 240 bars
  Exits are bar-by-bar (worst case: if both stop and target graze the same
  bar, the LOSS is counted - conservative).

Controls:
  A. LEVEL-CONTROL: identical trigger but levels are random M1 swing highs
     (matched count/recency-ish). If HTF-conditioned == random-level, the
     HTF map adds nothing.
  B. Baselines: always-short over same windows; spread toll per trade.

Windows: V100 1s & V30 1s (30d M1 from bars30_*), R_10/R_25 24h windows.
Read-only; no live state touched.
"""
import csv
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent.parent))

K = 3
REJECT_BARS = 5
ENTRY_WINDOW = 20
TIMEOUT = 240
TP_R_FALLBACK = 2.0
BUFFER_MULT = 0.5

SPREAD = {"Volatility 100 (1s)": 0.380, "Volatility 30 (1s)": 0.586,
          "R_10": 0.188, "R_25": 0.301}


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


def unmitigated_levels(h, l, c, sh, sl):
    """Confirmed swing levels never closed beyond after confirmation."""
    hi_lv, lo_lv = [], []
    for i in sh:
        ci = i + K
        if not (c[ci + 1:] > h[i]).any():
            hi_lv.append((ci, h[i]))
    for i in sl:
        ci = i + K
        if not (c[ci + 1:] < l[i]).any():
            lo_lv.append((ci, l[i]))
    return hi_lv, lo_lv


def bearish_fvg_in(h, l, i0, i1):
    """Return (zone_top, zone_bottom, form_bar) of the LAST bearish 3-bar FVG
    with formation bar in [i0, i1], else None. Bearish FVG: l[i] > h[i+2]."""
    best = None
    for i in range(i0, min(i1, len(l) - 3) + 1):
        if l[i] > h[i + 2]:
            best = (l[i], h[i + 2], i + 2)
    return best


def run_short_engine(ep, o, h, l, c, levels_high, levels_low, spread):
    """Walk M1 bars; return list of trade dicts for the SHORT playbook."""
    n = len(c)
    med_rng = float(np.median(h - l))
    buf = BUFFER_MULT * med_rng
    trades = []
    active_until = -1
    for li, lvl in levels_high:
        if li + 2 >= n or li < active_until:
            continue
        # sweep: first bar after confirmation whose high exceeds lvl
        seg = np.where(h[li + 1:] > lvl)[0]
        if not len(seg):
            continue
        sw = li + 1 + int(seg[0])
        if sw - li > 4000:   # stale level
            continue
        # rejection: close back below within REJECT_BARS
        rej = next((sw + j for j in range(REJECT_BARS)
                    if sw + j < n and c[sw + j] < lvl), None)
        if rej is None:
            continue
        fvg = bearish_fvg_in(h, l, sw, rej)
        if fvg is None:
            continue
        ztop, zbot, form = fvg
        # entry: first bar after form whose high reaches ztop
        e_seg = np.where(h[form + 1:min(form + 1 + ENTRY_WINDOW, n)] >= ztop)[0]
        if not len(e_seg):
            continue
        e = form + 1 + int(e_seg[0])
        stop = max(h[sw:rej + 1]) + buf
        # target: nearest unmitigated HTF swing low below entry
        lows_below = [p for (ci2, p) in levels_low if ci2 < e and p < c[e]]
        risk = stop - ztop
        tgt = min(lows_below, key=lambda p: c[e] - p) if lows_below \
            else ztop - TP_R_FALLBACK * risk
        if tgt >= ztop or risk <= 0:
            continue
        # walk to outcome (conservative: loss wins ties)
        out = None
        for j in range(e + 1, min(e + TIMEOUT, n)):
            if l[j] <= stop:
                out = ("stop", stop); break
            if h[j] >= tgt:
                out = ("target", tgt); break
        if out is None:
            out = ("timeout", c[min(e + TIMEOUT, n - 1)])
        pnl = (ztop - out[1]) - spread  # short: entry - exit, minus toll
        trades.append(dict(win=out[0] == "target", pnl=pnl,
                           kind=out[0], e=e, lvl=lvl))
        active_until = e + 5
    return trades


def random_level_control(ep, o, h, l, c, lo_lv, n_target, spread, seed=3):
    """Same engine, but swing-high levels resampled from M1 swings at random
    (not HTF M15). Matches event count to the real run."""
    rng = np.random.default_rng(seed)
    sh1, sl1 = swings(h, l, K)
    _, lo1 = unmitigated_levels(h, l, c, sh1, sl1)
    if not len(sh1):
        return []
    picks = rng.choice(len(sh1), size=min(n_target, len(sh1)), replace=False)
    lv = [(int(sh1[i]) + K, float(h[sh1[i]])) for i in sorted(picks)]
    return run_short_engine(ep, o, h, l, c, lv, lo1, spread)


def report(name, trades, spread, vpu):
    if not trades:
        print(f"  {name}: 0 events")
        return
    pnl = np.array([t["pnl"] for t in trades])
    wr = 100 * np.mean([t["win"] for t in trades])
    print(f"  {name}: n={len(pnl)} WR={wr:.0f}% avgPnL={pnl.mean():+.3f}u "
          f"net-of-spread=${pnl.mean() * vpu:+.3f}/trade | "
          f"total ${pnl.sum() * vpu:+.2f} | stops={sum(t['kind']=='stop' for t in trades)} "
          f"tgts={sum(t['kind']=='target' for t in trades)} "
          f"to={sum(t['kind']=='timeout' for t in trades)}")


def study(name, ep, o, h, l, c, spread, vpu):
    print(f"\n===== {name} ({len(c):,} M1 bars, spread {spread}) =====")
    ep15, o5, h5, l5, c5 = resample(ep, o, h, l, c, 900)
    sh15, sl15 = swings(h5, l5)
    hi_lv, lo_lv = unmitigated_levels(h5, l5, c5, sh15, sl15)
    print(f"HTF M15: {len(c5)} bars, {len(hi_lv)} unmitigated highs, "
          f"{len(lo_lv)} lows")
    real = run_short_engine(ep, o, h, l, c, hi_lv, lo_lv, spread)
    report("HTF confluence SHORT", real, spread, vpu)
    ctrl = random_level_control(ep, o, h, l, c, lo_lv,
                                n_target=max(len(real), 1) * 3, spread=spread)
    report("random-level control ", ctrl, spread, vpu)
    t_idx = np.arange(0, len(c) - 5)
    base = -float(np.mean(c[t_idx + 5] - c[t_idx]))
    print(f"  always-short 5-bar baseline: {base:+.4f}u | "
          f"spread paid per trade: {spread}u")
    return real, ctrl


def main():
    # $/unit at min lot from MT5 specs measured earlier this session
    specs = {"Volatility 100 (1s)": (0.38, 0.5), "Volatility 30 (1s)": (0.586, 0.5),
             "R_10": (0.188, 0.5), "R_25": (0.301, 0.5)}
    import MetaTrader5 as mt5
    mt5.initialize()
    for code, mtn in [("Volatility 100 (1s)", "Volatility 100 (1s) Index"),
                      ("Volatility 30 (1s)", "Volatility 30 (1s) Index")]:
        info = mt5.symbol_info(mtn)
        if info:
            vpu = info.trade_tick_value / info.trade_tick_size * info.volume_min
            specs[code] = (SPREAD[code], vpu)
            print(f"{code}: min_lot={info.volume_min} $/unit=${vpu:.4f}")
    mt5.shutdown()

    for sym, path in [("Volatility 100 (1s)", "data/synth_scan/bars30_Volatility_100_1s_Index.npy"),
                      ("Volatility 30 (1s)", "data/synth_scan/bars30_Volatility_30_1s_Index.npy")]:
        d = np.load(path)
        study(sym, d[:, 0], d[:, 1], d[:, 2], d[:, 3], d[:, 4], *specs[sym])
    for sym in ("R_10", "R_25"):
        for days in (12, 19):
            d = np.load(f"data/synth_scan/ticks12h_{sym}_{days}d.npz")
            ts, px = d["ts"], d["px"].astype(float)
            b = (ts // 60).astype(np.int64)
            ub = np.unique(b)
            left = np.searchsorted(b, ub, side="left")
            right = np.searchsorted(b, ub, side="right")
            ep1 = ub * 60.0
            o1 = px[left]; c1 = px[right - 1]
            h1 = np.maximum.reduceat(px, left)
            l1 = np.minimum.reduceat(px, right - 1)
            study(f"{sym} -{days}d", ep1, o1, h1, l1, c1, *specs[sym])


if __name__ == "__main__":
    main()
