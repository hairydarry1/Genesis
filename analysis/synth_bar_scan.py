#!/usr/bin/env python3
"""Stage-1 phase-structure scan over the whole quoted synthetic universe, on M1 bars.

Why bars: the terminal holds no local tick history for un-streamed symbols
(each tick pull = ~100 s server roundtrip, often empty). M1 bars come back in
~0.5 s/symbol, which makes a full-universe screen feasible. Symbols that pass
get a live tick recording later (stage 2) — the Flip lesson is that final
numbers must come from ticks.

Metrics per symbol (72h of M1 bars):
  - persistence: same-direction rate of median-normalized large bar moves
    (bar-scale analog of the Flip spike detector)
  - grind: sign of small-move drift between large moves vs the large-move dir
  - lag-1 / lag-5 autocorrelation of signed bar returns
  - variance ratio VR(5): >1 trending, <1 mean-reverting
  - spike bars: share of bars with range > 5x median range (Boom/Crash shape)
  - drift: |net move| / gross move (0.0 = pure churn, 1.0 = pure trend)
  - toll: live spread / median |bar return| (comparative screen only)

Usage: python analysis/synth_bar_scan.py [--cached]
"""
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.flip_tick_store import mt5_init

CACHE_DIR = "data/synth_scan"
SYNTH_PAT = re.compile(
    r"(Volatility|Boom|Crash|Jump|Range Break|Step|DEX|Drift|dAI|DXm?|Trek|"
    r"VolSwitch|Vol over|Exponential)", re.I)
EXCLUDE = re.compile(r"(1yr)", re.I)


def universe(mt5):
    out = []
    for s in mt5.symbols_get():
        n = s.name
        if not SYNTH_PAT.search(n) or EXCLUDE.search(n):
            continue
        mt5.symbol_select(n, True)
        t, i = mt5.symbol_info_tick(n), mt5.symbol_info(n)
        if t and i and t.bid > 0 and t.ask > t.bid:
            out.append((n, float(t.ask - t.bid), float(i.point), int(i.digits),
                        float(i.trade_tick_value_profit or i.trade_tick_value or 0),
                        float(i.volume_min), float(i.volume_step)))
    return sorted(out)


def bar_metrics(open_, high, low, close, spread):
    ret = np.diff(close)
    ar = np.abs(ret)
    pos = ar[ar > 0]
    if len(pos) < 50:
        return None
    med = np.median(pos)
    rng = high - low
    med_rng = np.median(rng[rng > 0]) if (rng > 0).any() else 0.0

    out = {
        "bars": len(close), "med_ret": med, "med_rng": med_rng,
        "toll_bar": spread / med if med > 0 else np.inf,
    }

    # variance ratio VR(5)
    if len(ret) >= 100:
        v1 = np.var(ret)
        s5 = np.convolve(ret, np.ones(5), "valid")
        out["vr5"] = float(np.var(s5) / (5 * v1)) if v1 > 0 else np.nan
        den = np.sqrt(np.var(ret) * np.var(ret[:-1]))
        out["ac1"] = float(np.mean((ret[:-1] - ret.mean()) * (ret[1:] - ret.mean())) / den) if den > 0 else np.nan
        out["drift"] = float(abs(ret.sum()) / ar.sum())
    else:
        out["vr5"] = out["ac1"] = out["drift"] = np.nan

    # spike-bar share (Boom/Crash shape) + spike direction runs on bar closes
    if med_rng > 0:
        out["spike_bars"] = float(np.mean(rng > 5 * med_rng))
    else:
        out["spike_bars"] = np.nan

    for m in (2.0, 3.0, 5.0):
        big = np.where(ar > m * med)[0]
        out[f"moves@{m:g}"] = len(big)
        if len(big) >= 60:
            dirs = np.sign(ret[big])
            out[f"same@{m:g}"] = float(np.mean(dirs[1:] == dirs[:-1]))
            # grind: small-move drift between big moves vs previous big dir
            small = np.where(np.isin(np.arange(len(ret)), big), 0.0, ret)
            segs = [np.sign(small[a:b].sum()) for a, b in zip(big, big[1:])]
            nz = [(s, dirs[k]) for k, s in enumerate(segs) if s != 0]
            out[f"grind@{m:g}"] = float(np.mean([s == d for s, d in nz])) if len(nz) >= 30 else np.nan
        else:
            out[f"same@{m:g}"] = out[f"grind@{m:g}"] = np.nan
    return out


def fetch_bars(mt5, name, now, hours=72):
    r = mt5.copy_rates_range(name, mt5.TIMEFRAME_M1,
                             now - timedelta(hours=hours), now)
    if r is None or len(r) == 0:
        return None
    return np.column_stack([r["open"], r["high"], r["low"], r["close"],
                            r["tick_volume"]]).astype(np.float64)


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    cached_only = "--cached" in sys.argv
    rows = []
    if not cached_only:
        mt5 = mt5_init()
        try:
            uni = universe(mt5)
            now = datetime.now(timezone.utc)
            print(f"quoted symbols: {len(uni)}", flush=True)
            for k, (name, spread, pt, digits, tvp, vmin, vstep) in enumerate(uni):
                safe = re.sub(r"[^A-Za-z0-9]+", "_", name)
                path = f"{CACHE_DIR}/bars_{safe}.npy"
                if os.path.exists(path):
                    bars = np.load(path)
                else:
                    bars = fetch_bars(mt5, name, now)
                    if bars is not None:
                        np.save(path, bars)
                if bars is None or len(bars) < 200:
                    print(f"[{k+1}/{len(uni)}] {name:32s} NO BAR HISTORY", flush=True)
                    continue
                m = bar_metrics(bars[:, 0], bars[:, 1], bars[:, 2], bars[:, 3], spread)
                if m is None:
                    print(f"[{k+1}/{len(uni)}] {name:32s} degenerate closes", flush=True)
                    continue
                m.update(name=name, spread=spread, digits=digits, tvp=tvp,
                         vmin=vmin, vstep=vstep)
                rows.append(m)
                print(f"[{k+1}/{len(uni)}] {name:32s} bars={m['bars']:>5,} "
                      f"toll={m['toll_bar']:>5.2f} same@3={m['same@3']:.3f} "
                      f"vr5={m['vr5']:.2f} spike%={m['spike_bars']*100 if not np.isnan(m['spike_bars']) else float('nan'):.1f}", flush=True)
        finally:
            mt5.shutdown()
    else:
        import csv
        with open(f"{CACHE_DIR}/bar_scan_results.csv") as f:
            rows = list(csv.DictReader(f))
            for r in rows:
                for k in r:
                    try:
                        r[k] = float(r[k])
                    except (ValueError, TypeError):
                        pass

    if not rows:
        print("nothing scanned")
        return

    # persist results
    import csv
    cols = ["name", "bars", "spread", "toll_bar", "med_ret", "med_rng", "vr5",
            "ac1", "drift", "spike_bars",
            "moves@2", "same@2", "grind@2", "moves@3", "same@3", "grind@3",
            "moves@5", "same@5", "grind@5", "tvp", "vmin", "vstep"]
    with open(f"{CACHE_DIR}/bar_scan_results.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for m in rows:
            w.writerow({c: m.get(c, "") for c in cols})

    # ---- ranked output --------------------------------------------------
    def fmt(x, nd=3):
        return f"{x:.{nd}f}" if isinstance(x, float) and not np.isnan(x) else "  -  "

    print("\n" + "=" * 104)
    print("A. PERSISTENCE RANKING (large-move same-dir rate @3, >=60 moves, toll_bar < 0.5)")
    print("=" * 104)
    print(f"{'symbol':30s} {'toll':>5s} {'same@3':>7s} {'grind@3':>8s} {'vr5':>6s} "
          f"{'spike%':>7s} {'moves@3':>7s} {'drift':>6s}")
    pers = [m for m in rows if not np.isnan(m["same@3"]) and m["moves@3"] >= 60
            and m["toll_bar"] < 0.5]
    pers.sort(key=lambda m: -m["same@3"])
    for m in pers:
        print(f"{m['name']:30s} {m['toll_bar']:>5.2f} {m['same@3']:>7.3f} "
              f"{fmt(m['grind@3']):>8s} {fmt(m['vr5'], 2):>6s} "
              f"{m['spike_bars']*100:>6.1f}% {m['moves@3']:>7,} {fmt(m['drift']):>6s}")

    print("\n" + "=" * 104)
    print("B. MEAN-REVERSION RANKING (VR(5) < 0.85, >=200 bars, toll_bar < 0.5)")
    print("=" * 104)
    mr = [m for m in rows if not np.isnan(m["vr5"]) and m["vr5"] < 0.85
          and m["bars"] >= 200 and m["toll_bar"] < 0.5]
    mr.sort(key=lambda m: m["vr5"])
    print(f"{'symbol':30s} {'toll':>5s} {'vr5':>6s} {'ac1':>7s} {'drift':>6s} {'same@3':>7s}")
    for m in mr:
        print(f"{m['name']:30s} {m['toll_bar']:>5.2f} {m['vr5']:>6.2f} "
              f"{fmt(m['ac1']):>7s} {fmt(m['drift']):>6s} {fmt(m['same@3.0']):>7s}")

    print("\n" + "=" * 104)
    print("C. EXCLUDED BY TOLL (toll_bar >= 0.5, best structure) — candidates only with tighter friction")
    print("=" * 104)
    big = [m for m in rows if m["toll_bar"] >= 0.5 and not np.isnan(m["same@3"])]
    big.sort(key=lambda m: -(m["same@3"] - 0.5) / m["toll_bar"])
    for m in big[:15]:
        print(f"{m['name']:30s} toll={m['toll_bar']:>5.2f} same@3={m['same@3']:.3f} spread={m['spread']}")
    print(f"\nresults: {len(rows)} scanned, {len(pers)} persistence, {len(mr)} mean-reversion")


if __name__ == "__main__":
    main()
