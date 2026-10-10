#!/usr/bin/env python3
"""
HOLD-TIME OPTIMIZATION — full TICK-LEVEL simulation (gold standard)
====================================================================
Replicates the EA on the raw tick stream with REAL gap fills:
- CRASH symbol, we go LONG (fade the crash spike, drift resumes UP).
- Entry: ~1 minute (≈60 ticks) after a crash spike tick completes
  (equivalent to the EA entering at the close of the M1 bar after the
  spike bar). Entry = price at that tick.
- SL at entry - D, TP at entry + 2D. Fills happen TICK BY TICK:
  * If a tick trades at/below the SL level, SL fills at that tick's
    price (a single-tick gap fills at the gap price — exactly how the
    server behaves).
  * TP similarly at the first tick at/above.
- Timeout exit at hold_ticks after entry (≈ hold M1 bars × ticks/min).
- Sweep hold to find where drift gains stop beating gap-exposure cost.

No SL-level assumptions anywhere. PnL is points per 1.0 position.
"""

import numpy as np

TICKS_PER_MIN = 58  # measured: ~2.5M ticks / 30 days

def find_crash_spike_ticks(prices, med, mult=200.0):
    """Single-tick crash moves (down) bigger than mult x median tick."""
    diffs = np.diff(prices)
    return np.where(-diffs > mult * med)[0] + 1  # index of the moved-to tick

def simulate(prices, sl_dist, entry_wait=58, hold_ticks=174, spike_med_mult=200.0,
             med_override=None):
    """
    sl_dist: SL distance in price pts
    entry_wait: ticks after spike before entering (58 = ~1 min)
    hold_ticks: max ticks in trade (174 = 3 min)
    """
    n = len(prices)
    diffs = np.diff(prices)
    med = np.median(np.abs(diffs)) if med_override is None else med_override
    spikes = find_crash_spike_ticks(prices, med, spike_med_mult)
    tp_dist = 2.0 * sl_dist

    trades = []
    for si in spikes:
        entry_i = si + entry_wait
        if entry_i >= n - hold_ticks:
            continue
        entry = prices[entry_i]
        sl_level = entry - sl_dist
        tp_level = entry + tp_dist

        exit_i = None
        exit_price = None
        reason = None
        for k in range(1, hold_ticks + 1):
            p = prices[entry_i + k]
            if p <= sl_level:
                exit_price = p          # TRUE fill: the gap tick price
                reason = 'sl'
                exit_i = k
                break
            if p >= tp_level:
                exit_price = p
                reason = 'tp'
                exit_i = k
                break
        if exit_price is None:
            exit_price = prices[min(entry_i + hold_ticks, n - 1)]
            reason = 'timeout'
            exit_i = hold_ticks
        trades.append({'pnl': exit_price - entry, 'reason': reason,
                       'ticks_held': exit_i, 'entry': entry, 'sl': sl_dist})
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
            'max_loss': pnls.min(),
            'worst10': np.percentile(pnls, 10)}

def run(symbol, fn, sl_dist):
    prices = np.load(fn)
    print(f"\n{'='*110}")
    print(f"{symbol}: {len(prices):,} ticks | SL={sl_dist} | TP=2x SL | entry ~1 min after spike")
    print(f"{'='*110}")
    print(f"  {'hold(min)':<10}{'hold(ticks)':<12}{'trades':>7}{'WR%':>7}{'PF':>7}"
          f"{'PnL':>9}{'avg':>8}{'avgWin':>9}{'avgLoss':>9}{'maxLoss':>9}{'p10':>8}")
    print("  " + "-" * 104)
    for mins in [1, 2, 3, 4, 5, 7, 10, 15, 20, 30]:
        r = summarize(simulate(prices, sl_dist, entry_wait=TICKS_PER_MIN,
                               hold_ticks=mins * TICKS_PER_MIN))
        if r is None:
            continue
        print(f"  {mins:<10}{mins*TICKS_PER_MIN:<12}{r['n']:>7}{r['wr']:>6.1f}%"
              f"{r['pf']:>7.2f}{r['pnl']:>9.1f}{r['avg']:>+8.3f}{r['avg_win']:>+9.3f}"
              f"{r['avg_loss']:>+9.3f}{r['max_loss']:>9.2f}{r['worst10']:>+8.2f}")

    # breakdown of exit reasons at 3 min
    tr = simulate(prices, sl_dist, entry_wait=TICKS_PER_MIN, hold_ticks=3 * TICKS_PER_MIN)
    reasons = {}
    for t in tr:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
    print(f"\n  exit mix @ 3-min hold: {reasons}")

if __name__ == "__main__":
    run("CRASH1000", "data/ticks_crash1000_index.npy", 1.14)
    run("CRASH500", "data/ticks_crash_500_index.npy", 0.85)