#!/usr/bin/env python3
"""
SL FILL QUALITY — measured from real ticks, not assumptions
===========================================================
Scenario (CRASH symbols, the money makers):
  We are LONG after a crash spike (drift resumes UP).
  A NEW crash spike (DOWN tick) hits our position and blows
  through the SL placed D points below entry.

What actually happens to price after that spike tick?
  tick 0 = the spike tick (entry -> bottom or cascade start)
  ticks 1..N = follow-through and recovery

Key questions:
  1. How deep does price go after the SL level is crossed?
  2. Does price RECOVER back above the SL / entry level? (drift is UP)
  3. Compare exit policies at the crossing:
       SL server fill  = fill near the bottom (max damage?)
       EA tick-detection close (market, 1-50 ticks later)
       NO SL, wait for recovery to entry, then exit
  4. If recovery is reliable, "holding through the SL" converts most
     SL losses into breakevens — SL becomes optional on CRASH.
"""

import numpy as np

def analyze(symbol, fn, direction=1):
    """direction=+1: we are LONG, adverse spike is DOWN (crash)."""
    prices = np.load(fn)
    n = len(prices)
    diffs = np.diff(prices)
    med = np.median(np.abs(diffs))
    print(f"\n{'#'*100}")
    print(f"# {symbol}: {n:,} ticks, median tick move {med:.4f}")
    print(f"{'#'*100}")

    # --- find adverse (crash: down) spike ticks ---
    # spike = single tick move down bigger than 40x median tick
    # (M1-range spikes; SL distances are in the 1-2 point range, median
    # tick ~0.001-0.01, so a 40x+ tick is a genuine spike event)
    spike_mult = 40.0
    adv_idx = np.where(diffs < -spike_mult * med)[0]   # crash spikes (down)
    print(f"Adverse spike ticks found: {len(adv_idx)} (move < -{spike_mult:.0f}x median)")

    # SL distances to test, in PRICE units — from EA logs:
    # crash1000 broker floor ~1.14, crash500 similar. Median M1 range
    # ~0.36 (1000) / similar. Use a sweep.
    look = 400  # ticks to watch after spike

    for sl_dist in ([1.14] if '1000' in symbol else [0.85]):
        print(f"\n--- SL distance = {sl_dist} (price units) ---")

        # Collect: for each adverse spike, entry = price just BEFORE the spike,
        # SL = entry - sl_dist. The spike takes price through SL.
        # We track the price path relative to ENTRY over the next `look` ticks.
        reached_sl = 0
        blown = 0
        results = []
        for i in adv_idx:
            if i < 5 or i + look >= n:
                continue
            entry = prices[i - 1]      # last tick before spike = approx entry
            sl_level = entry - sl_dist
            # spike must actually cross the SL
            if prices[i] > sl_level:
                continue
            reached_sl += 1
            path = prices[i:i + look]  # spike tick + follow ticks
            low = path.min()
            min_from_entry = entry - low          # max adverse excursion
            # recovery: first tick where price is back >= entry
            rec_idx = np.where(path >= entry)[0]
            rec_tick = rec_idx[0] if len(rec_idx) else -1
            # exit prices under different policies (ticks after spike tick)
            fills = {}
            for k in [0, 1, 3, 5, 10, 20]:
                if k < len(path):
                    fills[k] = path[k]
            results.append({
                'entry': entry, 'low': low, 'min_from_entry': min_from_entry,
                'blown_by': min_from_entry - sl_dist,  # how far past SL price went
                'rec_tick': rec_tick,
                'fills': fills,
            })
            if min_from_entry > sl_dist:
                blown += 1

        if not results:
            print("  no events crossed SL")
            continue

        arr = results
        print(f"  events crossing SL: {len(arr)} | {blown} blew further than SL ({blown/len(arr)*100:.0f}%)")

        # how deep past the SL does price go?
        b = np.array([r['blown_by'] for r in arr])
        print(f"  overshoot past SL:  median={np.median(b):.3f}  p90={np.percentile(b,90):.3f}  "
              f"p99={np.percentile(b,99):.3f}  max={b.max():.3f}")

        # recovery stats: does price get back to entry?
        recs = np.array([r['rec_tick'] for r in arr])
        n_rec = np.sum(recs >= 0)
        rec_ticks = recs[recs >= 0]
        print(f"  price recovers to ENTRY within {look} ticks: {n_rec}/{len(arr)} "
              f"({n_rec/len(arr)*100:.0f}%)  median ticks to recover: "
              f"{np.median(rec_ticks) if len(rec_ticks) else 'n/a'}")

        # --- exit policy comparison (PnL in price units per unit) ---
        # Policy A: SL fills AT the level (best-case server fill)
        # Policy B: fill at spike tick k (crossing) — server latency ~0
        # Policy C: market close detected k ticks later (EA detects & closes)
        # Policy D: NO SL — exit on first recovery to entry (drift pullback)
        # Policy E: NO SL — exit at +N ticks regardless (time exit after spike)
        pA = -sl_dist
        print(f"\n  exit policies (PnL in price units, per 1.0 position):")
        print(f"    {'policy':<46}{'avg PnL':>10}{'median':>9}{'p10':>8}{'p90':>8}")

        def stats(name, pnls):
            a = np.array(pnls)
            print(f"    {name:<46}{a.mean():>+10.3f}{np.median(a):>+9.3f}"
                  f"{np.percentile(a,10):>+8.3f}{np.percentile(a,90):>+8.3f}")

        stats("A: server SL fills at level", [-sl_dist] * len(arr))
        for k in [0, 3, 10, 30]:
            pnls = []
            for r in arr:
                f = r['fills'].get(k)
                pnls.append((f - r['entry']) if f is not None else (-sl_dist))
            stats(f"B: market close {k} ticks after spike tick", pnls)
        # D: no SL, exit at first tick >= entry (or worst at end of window)
        pnls = []
        for r in arr:
            if r['rec_tick'] >= 0:
                pnls.append(0.0)  # recovered to entry
            else:
                # exit at end of window
                pnls.append(r['fills'].get(399, 0) - r['entry'])
        stats("D: no SL, exit when back at entry (or window end)", pnls)
        # E: no SL, exit exactly 60 ticks after spike (time exit)
        pnls = []
        for r in arr:
            f = r['fills'].get(60)
            pnls.append((f - r['entry']) if f is not None else (-sl_dist))
        stats("E: no SL, time-exit 60 ticks after spike", pnls)

if __name__ == "__main__":
    analyze("CRASH1000", "data/ticks_crash1000_index.npy", direction=1)