#!/usr/bin/env python3
"""
eval/voc_gap.py — Tick-level loss-gap measurement for the Vol over family.

The sim fills SL-gaps at bar extremes; live fills gap through the stop by
the true tick move. We have bid/ask ticks (~7d). For every simulated
spike entry, walk the real ticks forward from the entry bar's last tick
and record: (a) does price hit SL or TP first, (b) if SL, how FAR through
the stop level did the tick stream go before the first tick beyond it.

m = realized loss / stop distance  (boom1000 measured m=2.33 live)
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

# key -> (slug, floor pts, is_crash)
SYMS = {
    "voc400": ("Vol_over_Crash_400", 3.60, True),
    "vob400": ("Vol_over_Boom_400", 3.60, False),
    "voc550": ("Vol_over_Crash_550", 3.20, True),
    "vob550": ("Vol_over_Boom_550", 3.20, False),
    "voc750": ("Vol_over_Crash_750", 2.50, True),
    "vob750": ("Vol_over_Boom_750", 2.50, False),
}


def load_ticks(key):
    slug = SYMS[key][0]
    b = DATA / f"ticks_{slug}.npy"
    a = DATA / f"ticks_{slug}_ask.npy"
    if not b.exists() or not a.exists():
        return None
    bids = np.load(b)
    asks = np.load(a)
    return (bids + asks) / 2.0, np.arange(len(bids))


def measure(key, n_tries=4000, seed=11):
    """Random entries with engine partial geometry, but walk REAL ticks.
    SL = max(med, floor) computed from a candle median; we approximate med
    by sampling candles. Simpler: use floor as stop (worst case for gap),
    and also floor*2. Walk ticks: find entry tick = first tick >= entry
    time (approx by index). This is a first-order estimate."""
    ticks, _ = load_ticks(key)
    if ticks is None:
        return None
    floor = SYMS[key][1]
    rng = np.random.default_rng(seed)
    n = len(ticks)
    gaps_sl = []
    outcomes = []
    wins = 0
    i = 0
    tries = 0
    while tries < n_tries and i < n - 5000:
        tries += 1
        # random entry point, engine-like: entry at tick, SL below (crash)
        # or above (boom) by max(med, floor); med ~ floor scale unknown so
        # test both floor and 2*floor
        entry = ticks[i]
        for mult in (1.0, 2.0):
            sl_d = floor * mult
            tp_d = 2 * sl_d
            hit = None
            j = i + 1
            while j < min(i + 2000, n - 1):
                px = ticks[j]
                if SYMS[key][2]:  # crash: long, SL below
                    if px <= entry - sl_d:
                        # how far through? min tick from here within 3 ticks
                        thru = (entry - sl_d - px)
                        gaps_sl.append((mult, thru / sl_d))
                        hit = "sl"
                        break
                    if px >= entry + tp_d:
                        hit = "tp"
                        break
                else:
                    if px >= entry + sl_d:
                        thru = (px - (entry + sl_d))
                        gaps_sl.append((mult, thru / sl_d))
                        hit = "sl"
                        break
                    if px <= entry - tp_d:
                        hit = "tp"
                        break
                j += 1
            if hit is None:
                continue
            if hit == "tp":
                wins += 1
        # advance randomly
        i += rng.integers(200, 3000)
    if not gaps_sl:
        return None
    g = np.array([x[1] for x in gaps_sl])
    gm1 = g[np.array([x[0] for x in gaps_sl]) == 1.0]
    gm2 = g[np.array([x[0] for x in gaps_sl]) == 2.0]
    out = []
    for nm, arr in (("1x floor", gm1), ("2x floor", gm2)):
        if len(arr):
            out.append(f"{nm}: m median {np.median(arr):.2f} p90 "
                       f"{np.percentile(arr,90):.2f} max {arr.max():.2f} "
                       f"(n={len(arr)})")
    return "; ".join(out)


def main():
    for key in SYMS:
        res = measure(key)
        print(f"{key:<8} {SYMS[key][0]:<22} -> {res if res else 'no ticks'}")


if __name__ == "__main__":
    main()