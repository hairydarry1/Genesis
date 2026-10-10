#!/usr/bin/env python3
"""
TICK-LEVEL LEVER TEST — with REAL gap-through fills
====================================================
The old M1 backtests (PF 2.26) never modeled intrabar gap fills and
are unrealistically optimistic. This simulates the EA exactly on the
tick stream: entry ~1 min after a crash spike tick, SL/TP fills at the
actual tick price (gaps fill at the gap price), timeout at hold.

Test the loss-reduction levers we built:
  - COOLDOWN: skip next K spikes after a losing trade
  - PARTIAL: enter half size, add half after 1 min only if profitable
  - hold time sweep
"""

import numpy as np

TICKS_PER_MIN = 58

def simulate(prices, sl_dist, entry_wait=58, hold_ticks=174,
             cooldown=0, partial=False, spike_med_mult=200.0):
    n = len(prices)
    diffs = np.diff(prices)
    med = np.median(np.abs(diffs))
    spikes = np.where(-diffs > spike_med_mult * med)[0] + 1
    tp_dist = 2.0 * sl_dist

    trades = []
    skip = 0
    for si in spikes:
        if skip > 0:
            skip -= 1
            continue
        entry_i = si + entry_wait
        if entry_i >= n - hold_ticks - 2:
            continue
        entry = prices[entry_i]
        sl_level = entry - sl_dist
        tp_level = entry + tp_dist

        def walk(start_price, start_idx, size, max_ticks):
            """Simulate one sub-position. Returns (exit_price, ticks_used)."""
            for k in range(1, max_ticks + 1):
                p = prices[start_idx + k]
                if p <= sl_level:
                    return p, k   # real gap fill
                if p >= tp_level:
                    return p, k
            return prices[min(start_idx + max_ticks, n - 1)], max_ticks

        if not partial:
            exit_p, _ = walk(entry, entry_i, 1.0, hold_ticks)
            trades.append({'pnl': exit_p - entry})
            if exit_p - entry <= 0 and cooldown > 0:
                skip = cooldown
        else:
            # half now
            p1, k1 = walk(entry, entry_i, 0.5, hold_ticks)
            pnl1 = (p1 - entry) * 0.5
            if pnl1 > 0 and k1 < hold_ticks:
                # add second half at same entry; walk rest of the hold
                p2, _ = walk(entry, entry_i + k1, 0.5, hold_ticks - k1)
                pnl = pnl1 + (p2 - entry) * 0.5
            else:
                pnl = pnl1
            trades.append({'pnl': pnl})
            if pnl <= 0 and cooldown > 0:
                skip = cooldown
    return trades

def summarize(trades):
    pnls = np.array([t['pnl'] for t in trades])
    wins, losses = pnls[pnls > 0], pnls[pnls <= 0]
    n = len(pnls)
    if n == 0:
        return None
    wr = len(wins) / n * 100
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    return {'n': n, 'wr': wr, 'pf': pf, 'pnl': pnls.sum(), 'avg': pnls.mean(),
            'avg_win': wins.mean() if len(wins) else 0,
            'avg_loss': losses.mean() if len(losses) else 0,
            'max_loss': pnls.min()}

def run(symbol, fn, sl_dist):
    prices = np.load(fn)
    print(f"\n{'='*118}")
    print(f"{symbol}: real gap fills | SL={sl_dist} | TP=2x | entry ~1min after spike")
    print(f"{'='*118}")
    print(f"  {'hold':<6}{'cfg':<30}{'trades':>7}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}"
          f"{'avgWin':>9}{'avgLoss':>9}{'maxLoss':>9}")
    print("  " + "-" * 106)
    for mins in [3, 5, 7, 10]:
        base = summarize(simulate(prices, sl_dist, hold_ticks=mins * TICKS_PER_MIN))
        cd = summarize(simulate(prices, sl_dist, hold_ticks=mins * TICKS_PER_MIN, cooldown=3))
        pr = summarize(simulate(prices, sl_dist, hold_ticks=mins * TICKS_PER_MIN, partial=True))
        prcd = summarize(simulate(prices, sl_dist, hold_ticks=mins * TICKS_PER_MIN,
                                  cooldown=3, partial=True))
        for label, r in [("full", base), ("+cooldown3", cd),
                         ("partial", pr), ("partial+cd3", prcd)]:
            if r is None:
                continue
            print(f"  {mins}min  {label:<30}{r['n']:>7}{r['wr']:>6.1f}%{r['pf']:>7.2f}"
                  f"{r['pnl']:>9.1f}{r['avg']:>+8.3f}{r['avg_win']:>+9.3f}"
                  f"{r['avg_loss']:>+9.3f}{r['max_loss']:>9.2f}")

if __name__ == "__main__":
    run("CRASH1000", "data/ticks_crash1000_index.npy", 1.14)
    run("CRASH500", "data/ticks_crash_500_index.npy", 0.85)