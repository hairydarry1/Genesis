#!/usr/bin/env python3
"""Phase-persistence scan across the whole quoted Deriv synthetic universe.

For every symbol with a live quote:
  1. fetch trailing 24 h of ticks (cached to data/synth_scan/{sym}.npy)
  2. detect large moves exactly like the Flip study (median-normalized deltas)
  3. measure: alternation rate (P(same dir) - 50% = persistence), grind-follow,
     run lengths, and the live-spread toll ratio (spread / median tick delta)
  4. rank: persistence signal x tradeability

The Flip lesson applied up front: an edge is only real net of the LIVE spread,
so the toll ratio is computed from actual quotes, not symbol_info.spread.

Usage: python analysis/synth_phase_scan.py            # full scan
       python analysis/synth_phase_scan.py --cached   # re-rank from cache only
"""
import glob
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis.flip_mechanics import large_moves, runs_of_direction

CACHE_DIR = "data/synth_scan"
SYNTH_PAT = re.compile(
    r"(Volatility|Boom|Crash|Jump|Range Break|Step|DEX|Drift|dAI|DXm?|Trek|"
    r"VolSwitch|Vol over|Exponential)", re.I)
EXCLUDE = re.compile(r"(1yr)", re.I)


def universe(mt5):
    names = [s.name for s in mt5.symbols_get()
             if SYNTH_PAT.search(s.name) and not EXCLUDE.search(s.name)]
    quoted = []
    for n in names:
        mt5.symbol_select(n, True)
        t = mt5.symbol_info_tick(n)
        i = mt5.symbol_info(n)
        if t and i and t.bid > 0 and t.ask > t.bid:
            quoted.append((n, t.ask - t.bid, i.point, i.digits,
                           i.trade_tick_value_profit, i.volume_min))
    return sorted(quoted)


def metrics(ms, px, spread, mults=(3.0, 5.0, 8.0)):
    d = np.diff(px)
    ad = np.abs(d)
    med = np.median(ad[ad > 0]) if (ad > 0).any() else 0.0
    if med <= 0:
        return None
    out = {
        "ticks": len(px), "med_delta": med,
        "toll": spread / med,          # spread in units of median tick delta
        "hours": (ms[-1] - ms[0]) / 3.6e6,
    }
    for m in mults:
        moves, _ = large_moves(ms, px, spike_mult=m)
        out[f"moves@{m:g}"] = len(moves)
        if len(moves) >= 60:
            dirs = [dd for _, dd, _ in moves]
            same = np.mean([a == b for a, b in zip(dirs, dirs[1:])])
            out[f"same@{m:g}"] = same          # 0.5 = coin
            # grind-follow: sign of small-tick drift between moves vs prev dir
            big = np.zeros(len(d), bool)
            big[[i for i, _, _ in moves]] = True
            small = np.where(big, 0.0, d)
            idxs = [i for i, _, _ in moves]
            segs = [np.sign(small[a:b].sum()) for a, b in zip(idxs, idxs[1:])]
            prev = dirs[:-1]
            out[f"grind@{m:g}"] = np.mean([s == p for s, p in zip(segs, prev) if s != 0]) \
                if sum(1 for s in segs if s != 0) >= 30 else np.nan
        else:
            out[f"same@{m:g}"] = np.nan
            out[f"grind@{m:g}"] = np.nan
    return out


def fetch(mt5, name, now):
    start = now - timedelta(hours=24)
    t = mt5.copy_ticks_range(name, start, now, mt5.COPY_TICKS_ALL)
    if t is None or len(t) == 0:
        return None
    arr = np.column_stack([t["time_msc"].astype(np.int64),
                           t["bid"].astype(np.float64)])
    return arr[arr[:, 1] > 0]


def main():
    os.makedirs(CACHE_DIR, exist_ok=True)
    use_cache = "--cached" in sys.argv
    rows = []
    if not use_cache:
        from data.flip_tick_store import mt5_init
        mt5 = mt5_init()
        try:
            uni = universe(mt5)
            now = datetime.now(timezone.utc)
            print(f"quoted symbols: {len(uni)}", flush=True)
            for k, (name, spread, pt, digits, tvp, vmin) in enumerate(uni):
                safe = re.sub(r"[^A-Za-z0-9]+", "_", name)
                path = f"{CACHE_DIR}/{safe}.npy"
                arr = np.load(path) if os.path.exists(path) else fetch(mt5, name, now)
                if arr is None or len(arr) < 1000:
                    print(f"[{k+1}/{len(uni)}] {name:32s} no/sparse ticks", flush=True)
                    continue
                if not os.path.exists(path):
                    np.save(path, arr)
                m = metrics(arr[:, 0], arr[:, 1], spread)
                if m is None:
                    continue
                m.update(name=name, spread=spread, digits=digits, tvp=tvp, vmin=vmin)
                rows.append(m)
                print(f"[{k+1}/{len(uni)}] {name:32s} ticks={m['ticks']:>8,} "
                      f"toll={m['toll']:>6.2f} same@3={m['same@3.0'] if not np.isnan(m['same@3.0']) else float('nan'):.3f} "
                      f"same@5={m['same@5.0'] if not np.isnan(m['same@5.0']) else float('nan'):.3f}", flush=True)
        finally:
            mt5.shutdown()
    else:
        for path in sorted(glob.glob(f"{CACHE_DIR}/*.npy")):
            arr = np.load(path)
            rows.append({"name": os.path.basename(path)[:-4], "ticks": len(arr)})

    if use_cache:
        print(f"(cached tick files: {len(rows)}) — full metrics need live spreads; "
              f"rerun without --cached for the ranked table")
        return

    # ---- ranking -------------------------------------------------------
    print("\n" + "=" * 96)
    print("RANKED: persistence (same@3 - 0.5) among symbols with toll < 3 and >= 60 moves @3")
    print("=" * 96)
    hdr = (f"{'symbol':32s} {'ticks':>8s} {'toll':>6s} {'same@3':>7s} {'grind@3':>8s} "
           f"{'same@5':>7s} {'moves@3':>8s}")
    print(hdr)
    viable = []
    for m in rows:
        if np.isnan(m.get("same@3.0", np.nan)) or m["toll"] >= 3.0:
            continue
        viable.append(m)
    viable.sort(key=lambda m: -m["same@3.0"])
    for m in viable:
        print(f"{m['name']:32s} {m['ticks']:>8,} {m['toll']:>6.2f} "
              f"{m['same@3.0']:>7.3f} {m.get('grind@3.0', float('nan')):>8.3f} "
              f"{m['same@5.0'] if not np.isnan(m['same@5.0']) else float('nan'):>7.3f} "
              f"{m['moves@3.0']:>8,}")
    print(f"\n{len(viable)} of {len(rows)} passed the toll screen")

    # worst tolls for the record
    print("\nHighest toll ratios (untradeable for a phase strategy):")
    for m in sorted(rows, key=lambda m: -m["toll"])[:10]:
        print(f"  {m['name']:32s} toll={m['toll']:.1f} spread={m['spread']}")


if __name__ == "__main__":
    main()
