#!/usr/bin/env python3
"""Stage-2 deep dive: 30-day weekly-stability screen for bar-scan shortlist.

For each candidate symbol, pulls 30d of M1 bars and computes the same
large-move persistence metrics per UTC-week. A candidate only advances to
tick-recording if its persistence holds in EVERY week (n ~ 450-700 moves/week
makes the weekly estimate tight), not just in the 72h snapshot.

Also prints the dollar frame: live spread x $/unit (0.1 lot) = round-trip toll,
and the drift ratio (net/gross move) that separates one-way trends from churn.

Usage: python analysis/synth_deep_dive.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.flip_tick_store import mt5_init

SYMBOLS = [
    # (name, live_spread)  -- spreads re-measured live at run time
    "Skew Step Index 4 Up",
    "Skew Step Index 4 Down",
    "Skew Step Index 5 Up",
    "Skew Step Index 5 Down",
    "Range Break 100 Index",
    "Range Break 200 Index",
    "Volatility 100 (1s) Index",
    "Volatility 30 (1s) Index",
    "Trek Up Index",
    "Trek Down Index",
    "Vol over Crash 400",
    "Vol over Crash 550",
]
DAYS = 30
DAY = 86_400


def week_metrics(close, times, mult=3.0):
    ret = np.diff(close)
    ar = np.abs(ret)
    pos = ar[ar > 0]
    if len(pos) < 100:
        return None
    med = np.median(pos)
    big = np.where(ar > mult * med)[0]
    out = {"n_moves": len(big)}
    if len(big) >= 30:
        dirs = np.sign(ret[big])
        same = np.mean(dirs[1:] == dirs[:-1])
        # binomial z vs 0.5 (pairs are the sample; they overlap — z is a
        # magnitude guide, not a strict test)
        n = len(dirs) - 1
        z = (same - 0.5) / np.sqrt(0.25 / n)
        out["same"] = float(same)
        out["z"] = float(z)
        small = np.where(np.isin(np.arange(len(ret)), big), 0.0, ret)
        segs = [np.sign(small[a:b].sum()) for a, b in zip(big, big[1:])]
        nz = [(s, dirs[k]) for k, s in enumerate(segs) if s != 0]
        out["grind"] = float(np.mean([s == d for s, d in nz])) if len(nz) >= 20 else np.nan
    else:
        out["same"] = out["z"] = out["grind"] = np.nan
    s5 = np.convolve(ret, np.ones(5), "valid")
    v1 = np.var(ret)
    out["vr5"] = float(np.var(s5) / (5 * v1)) if v1 > 0 else np.nan
    out["drift"] = float(abs(ret.sum()) / ar.sum())
    out["med_ret"] = float(med)
    return out


def main():
    mt5 = mt5_init()
    now = datetime.now(timezone.utc)
    t0 = now - timedelta(days=DAYS)
    try:
        print(f"{'symbol':28s} {'wk':>3s} {'same@3':>7s} {'z':>5s} {'grind':>6s} "
              f"{'vr5':>5s} {'drift':>6s} {'moves':>6s}")
        summary = []
        for name in SYMBOLS:
            mt5.symbol_select(name, True)
            r = mt5.copy_rates_range(name, mt5.TIMEFRAME_M1, t0, now)
            if r is None or len(r) < 1000:
                print(f"{name:28s}  NO HISTORY"); continue
            close = np.asarray(r["close"], float)
            times = np.asarray(r["time"], np.int64)
            info = mt5.symbol_info(name)
            tick = mt5.symbol_info_tick(name)
            spread = float(tick.ask - tick.bid) if (tick and tick.ask > tick.bid) else float("nan")
            tvp = float(info.trade_tick_value_profit or info.trade_tick_value or 0)
            cost_rt = spread * tvp  # $ per round trip at 0.1 lot

            weeks = (times - times[0]) // (7 * DAY)
            wm = []
            for w in np.unique(weeks):
                m = week_metrics(close[weeks == w], times[weeks == w])
                if m:
                    m["week"] = int(w)
                    wm.append(m)
            print(f"\n{name}  (live spread {spread:.4g}, ${cost_rt:.4f}/RT @0.1 lot)")
            for m in wm:
                print(f"{'':28s} {m['week']:>3d} {m['same']:>7.3f} {m['z']:>5.1f} "
                      f"{m['grind']:>6.3f} {m['vr5']:>5.2f} {m['drift']:>6.3f} {m['n_moves']:>6d}")
            sames = [m["same"] for m in wm if not np.isnan(m["same"])]
            if sames:
                ok = all(s > 0.5 for s in sames)
                summary.append((name, np.mean(sames), min(sames), max(sames),
                                len(sames), ok, cost_rt, spread))
        print("\n" + "=" * 100)
        print("WEEKLY STABILITY SUMMARY (candidate advances only if same@3 > 0.5 in EVERY week)")
        print("=" * 100)
        print(f"{'symbol':28s} {'weeks':>5s} {'mean':>6s} {'min':>6s} {'max':>6s} "
              f"{'all>0.5':>7s} {'$/RT@0.1':>9s}")
        for name, mean, mn, mx, nw, ok, cost, spread in sorted(summary, key=lambda x: -x[1]):
            print(f"{name:28s} {nw:>5d} {mean:>6.3f} {mn:>6.3f} {mx:>6.3f} "
                  f"{'YES' if ok else 'no':>7s} {cost:>9.4f}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
