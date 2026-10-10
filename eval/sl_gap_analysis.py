#!/usr/bin/env python3
"""
SL GAP-THROUGH ANALYSIS — the real fill mechanics (v2, sign-fixed)
==================================================================
CRASH (LONG): adverse = giant DOWN tick gapping through SL below entry.
BOOM  (SHORT): adverse = giant UP tick gapping through SL above entry.
The server SL fills at the post-gap price => loss = spike size, not SL.

PnL convention: LONG pnl = price - entry (crash), SHORT pnl = entry - price (boom).
Negative = loss. Recovery = price returns to entry (PnL >= 0).
"""

import numpy as np

def analyze(symbol, fn, adverse='down'):
    prices = np.load(fn)
    n = len(prices)
    diffs = np.diff(prices)
    med = np.median(np.abs(diffs))
    adverse_move = -diffs if adverse == 'down' else diffs
    tag = "DOWN (crash, LONG)" if adverse == 'down' else "UP (boom, SHORT)"
    print(f"\n{'#'*104}")
    print(f"# {symbol}: {n:,} ticks, median tick move {med:.4f}, adverse = {tag}")
    print(f"{'#'*104}")

    # si = index of the tick the big adverse move LANDED on
    si_all = np.where(adverse_move > 1.0)[0] + 1
    print(f"single adverse ticks > 1.0 pt: {len(si_all)}")

    for sl_dist in ([1.14] if '1000' in symbol else [0.85]):
        print(f"\n===== SL distance {sl_dist} pts =====")
        ev = []  # entry = pre-spike tick price
        for si in si_all:
            if si < 2 or si + 1200 >= n:
                continue
            entry = prices[si - 1]          # just before the adverse tick
            spike_price = prices[si]        # the spike low (crash) / high (boom)
            gap = abs(entry - spike_price)
            if gap < sl_dist:
                continue
            ev.append({'si': si, 'entry': entry, 'gap': gap})
        if not ev:
            print("no gap-through events found")
            continue
        gaps = np.array([e['gap'] for e in ev])
        print(f"gap-through events: {len(ev)}")
        print(f"gap size (real loss if server SL fills at spike tick): "
              f"median={np.median(gaps):.2f}  p25={np.percentile(gaps,25):.2f}  "
              f"p75={np.percentile(gaps,75):.2f}  p90={np.percentile(gaps,90):.2f}  "
              f"max={gaps.max():.2f}")
        slip = gaps - sl_dist
        print(f"slippage vs intended SL: median={np.median(slip):.2f} p90={np.percentile(slip,90):.2f} "
              f"max={slip.max():.2f} pts  ({np.median(slip)/sl_dist:.1f}x - {np.percentile(slip,90)/sl_dist:.1f}x intended loss)")

        # LONG for crash: pnl = price - entry (negative below entry). SHORT: entry - price.
        def pnl_of(price, entry):
            return (price - entry) if adverse == 'down' else (entry - price)

        # ---- RECOVERY (PnL >= 0 => back at/above entry for LONG, at/below for SHORT) ----
        print(f"\n  RECOVERY after gap (price back to ENTRY):")
        print(f"    {'horizon':<14}{'recovered %':>12}{'med ticks':>10}{'med PnL':>10}")
        for hz in [5, 10, 30, 60, 120, 300, 600, 1000]:
            rec_n, rec_ticks, pnls = 0, [], []
            for e in ev:
                seg = prices[e['si']:e['si'] + hz + 1]
                pn = pnl_of(seg, e['entry'])
                if (pn >= 0).any():
                    rec_n += 1
                    rec_ticks.append(int(np.where(pn >= 0)[0][0]))
                pnls.append(pn[-1])
            pnls = np.array(pnls)
            print(f"    {'%d ticks' % hz:<14}{rec_n/len(ev)*100:>11.1f}%"
                  f"{np.median(rec_ticks) if rec_ticks else float('nan'):>10.0f}"
                  f"{np.median(pnls):>+10.2f}")

        # ---- POLICY COMPARISON ----
        print(f"\n  EXIT POLICIES after gap-through (PnL pts / 1.0 pos):")
        print(f"    {'policy':<54}{'avg':>8}{'median':>8}{'p10':>8}{'p90':>8}{'worst':>8}")
        def show(name, arr):
            a = np.array(arr, dtype=float)
            print(f"    {name:<54}{a.mean():>+8.2f}{np.median(a):>+8.2f}"
                  f"{np.percentile(a,10):>+8.2f}{np.percentile(a,90):>+8.2f}{a.min():>8.2f}")

        # A: server SL fills AT the level (only possible if no gap)
        show("A0: ideal SL fill at level (not achievable on gap)", [-sl_dist] * len(ev))
        # A: server SL real fill = gap tick price
        show("A: server SL (real: fills at gap price)", [-e['gap'] for e in ev])
        for K in [1, 3, 10, 30, 60]:
            pnls = [pnl_of(prices[e['si'] + K], e['entry']) for e in ev]
            show(f"B: market close {K} tick(s) after gap tick", pnls)
        # C: no SL, exit on recovery to entry within 600 ticks else exit at 600
        pnls = []
        for e in ev:
            seg = prices[e['si']:e['si'] + 601]
            pn = pnl_of(seg, e['entry'])
            hits = np.where(pn >= 0)[0]
            pnls.append(0.0 if len(hits) else pn[-1])
        show("C: no SL, exit on recovery (600-tick emg)", pnls)

        # ---- CLUSTER RISK ----
        print(f"\n  CLUSTER RISK while waiting (another adverse gap >= SL within window):")
        for hz, label in [(60, '60 ticks'), (300, '300 ticks')]:
            hits = 0
            for e in ev:
                d = np.diff(prices[e['si']:e['si'] + hz])
                if len(d) and ((d.min() < -sl_dist) if adverse == 'down' else (d.max() > sl_dist)):
                    hits += 1
            print(f"    within {label}: {hits}/{len(ev)} ({hits/len(ev)*100:.1f}%)")

if __name__ == "__main__":
    targets = [("CRASH1000", "data/ticks_crash1000_index.npy", "down"),
               ("CRASH500", "data/ticks_crash_500_index.npy", "down"),
               ("BOOM1000", "data/ticks_boom1000_index.npy", "up"),
               ("BOOM500", "data/ticks_boom_500_index.npy", "up")]
    for sym, fn, adv in targets:
        analyze(sym, fn, adv)