#!/usr/bin/env python3
"""Deriv synthetic universe scan: discover symbols + measure LIVE spreads.

Phase-persistence edges are worthless if the spread toll eats them, so this
measures the live spread (from actual quotes, not the static symbol_info.spread
field that misled the Flip study) and computes a spread-to-tick-move ratio for
every synthetic. Output: a pre-screen table that decides which symbols get the
full tick-level phase scan.

Usage: python analysis/deriv_scan.py
"""
import os
import re
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.flip_tick_store import mt5_init

# symbol-name patterns that identify Deriv synthetics
SYNTH_PAT = re.compile(
    r"(Volatility|Boom|Crash|Jump|Range Break|Step|DEX|Drift|dAI|DXm?)", re.I)
EXCLUDE = re.compile(r"(Micro|1yr|500 Index$)", re.I)  # keep raw

def main():
    mt5 = mt5_init()
    try:
        all_syms = mt5.symbols_get()
        if all_syms is None:
            print("symbols_get failed:", mt5.last_error()); return
        names = [s.name for s in all_syms if SYNTH_PAT.search(s.name)
                 and not EXCLUDE.search(s.name)]
        # enable quotes
        for n in names:
            mt5.symbol_select(n, True)
        print(f"synthetic candidates: {len(names)}\n")

        rows = []
        for n in sorted(names):
            info = mt5.symbol_info(n)
            tick = mt5.symbol_info_tick(n)
            if info is None or tick is None:
                continue
            pt = info.point
            spread = None
            if tick.bid > 0 and tick.ask > tick.bid:
                spread = tick.ask - tick.bid          # LIVE, in price units
            # static field for comparison
            static = info.spread * pt
            rows.append(dict(
                name=n, digits=info.digits, pt=pt,
                bid=tick.bid, spread=spread, static=static,
                vol=info.trade_tick_value, tvp=info.trade_tick_value_profit,
                vmin=info.volume_min, step=info.volume_step,
            ))

        # print table
        print(f"{'symbol':28s} {'digits':>6s} {'bid':>12s} {'liveSprd':>10s} "
              f"{'staticSprd':>10s} {'$perUnit@0.1':>12s}")
        for r in rows:
            dollar = r["tvp"] if r["tvp"] else r["vol"] or 0
            print(f"{r['name']:28s} {r['digits']:>6d} {r['bid']:>12.4f} "
                  f"{(r['spread'] if r['spread'] is not None else float('nan')):>10.4f} "
                  f"{r['static']:>10.4f} {dollar:>12.4f}")
        print(f"\n({len(rows)} of {len(names)} quoted OK)")
    finally:
        mt5.shutdown()

if __name__ == "__main__":
    main()
