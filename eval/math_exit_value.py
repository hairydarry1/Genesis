#!/usr/bin/env python3
"""
STATE-DEPENDENT EXIT VALUE
==========================
For every trade, simulate a decision at each bar boundary while open:
  - If I CLOSE now (at this bar's close), PnL = current.
  - If I HOLD, what is the expected final PnL given the state reached?

We include intrabar SL/TP: a trade that would hit SL inside the next bar
is capped at -SL. We measure E[final | state] for states = (bar in trade,
current PnL in units of SL distance). If E[final | state] < current PnL,
cutting at that state is mathematically better than holding.
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = "data"
BROKER_FLOOR = {"crash1000": 1.14, "crash500": 1.14, "boom1000": 1.14, "boom500": 1.14,
                "crash300": 2.43, "boom300": 2.43}

def load_candles(symbol):
    fpath = os.path.join(DATA_DIR, f"candles_{symbol}_1min.parquet")
    if not os.path.exists(fpath):
        return None
    return pd.read_parquet(fpath)

def find_bar_spikes(df, threshold=3.0):
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    ranges = highs - lows
    n = len(ranges)
    median_ranges = np.full(n, 0.01)
    for i in range(20, n):
        median_ranges[i] = np.median(ranges[max(0, i-20):i])
    spikes = []
    for i in range(1, n - 12):
        bar_range = ranges[i]
        if median_ranges[i] > 0 and bar_range > threshold * median_ranges[i]:
            is_boom = closes[i] > opens[i]
            is_crash = closes[i] < opens[i]
            if is_boom or is_crash:
                spikes.append({'type': 'boom' if is_boom else 'crash',
                               'idx': i, 'range': bar_range,
                               'median': median_ranges[i]})
    return spikes

def simulate_paths(df, spikes, symbol, hold=5):
    """Return per-trade path: list of (close_pnl, hit_tp, hit_sl) per bar j."""
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    floor = BROKER_FLOOR.get(symbol, 1.14)
    out = []
    for spike in spikes:
        idx = spike['idx']
        if idx + hold + 2 >= len(closes):
            continue
        median = spike['median']
        sl_dist = max(1.0 * median, floor)
        tp_dist = 2.0 * sl_dist
        direction = 1 if spike['type'] == 'crash' else -1
        entry = closes[idx + 1]
        path = []
        exited = False
        for j in range(idx + 2, idx + hold + 2):
            if j >= len(closes):
                break
            hi, lo = highs[j], lows[j]
            best = (hi - entry) if direction == 1 else (entry - lo)
            worst = (lo - entry) if direction == 1 else (entry - hi)
            close_pnl = (closes[j] - entry) * direction
            hit_tp = best >= tp_dist
            hit_sl = worst <= -sl_dist
            path.append({'close_pnl': close_pnl, 'hit_tp': hit_tp, 'hit_sl': hit_sl,
                         'tp': tp_dist, 'sl': sl_dist, 'dir': direction})
        out.append(path)
    return out

def final_pnl_from_state(path, bar_idx):
    """Simulate holding from bar bar_idx to the end; bar_idx is 0-based
    into path = bars AFTER entry. Returns realized final pnl if held
    with SL/TP active."""
    if bar_idx >= len(path):
        return path[-1]['close_pnl']
    cur = 0.0
    for b in path[bar_idx:]:
        if b['hit_sl']:
            return -b['sl']
        if b['hit_tp']:
            return b['tp']
        cur = b['close_pnl']
    return cur

for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
    df = load_candles(sym)
    if df is None:
        continue
    spikes = find_bar_spikes(df)
    paths = simulate_paths(df, spikes, sym, hold=5)
    print(f"\n{'='*100}")
    print(f"{sym.upper()} — decision table (hold vs cut at bar close)")
    print(f"{'='*100}")
    print(f"  {'bar':<5}{'state (pnl/SL)':<18}{'n':>6}{'E[cut now]':>12}{'E[hold]':>10}{'edge of hold':>13}{'verdict':>10}")
    for bar_idx in range(0, 5):
        # gather states across all trades at this bar boundary (still open)
        rows = []
        for path in paths:
            if len(path) <= bar_idx:
                continue
            cur = path[bar_idx]['close_pnl']
            if path[bar_idx]['hit_sl'] or path[bar_idx]['hit_tp']:
                continue  # trade would already be closed by SL/TP intrabar
            sl = path[bar_idx]['sl']
            fin = final_pnl_from_state(path, bar_idx + 1)
            rows.append((cur, fin, sl))
        if len(rows) < 200:
            continue
        arr = np.array(rows)
        # bin by cur/sl
        for lo, hi in [(-1.0, -0.4), (-0.4, -0.15), (-0.15, 0.0), (0.0, 0.15),
                       (0.15, 0.4), (0.4, 0.8), (0.8, 1.2), (1.2, 2.0)]:
            mask = (arr[:, 0] / arr[:, 2] >= lo) & (arr[:, 0] / arr[:, 2] < hi)
            sub = arr[mask]
            if len(sub) < 100:
                continue
            e_cut = sub[:, 0].mean()
            e_hold = sub[:, 1].mean()
            edge = e_hold - e_cut
            verdict = "HOLD" if edge > 0.02 else ("CUT" if edge < -0.02 else "~tie")
            print(f"  bar{bar_idx+1:<3}{lo:+.1f}..{hi:+.1f}          {len(sub):>6}"
                  f"{e_cut:>12.4f}{e_hold:>10.4f}{edge:>+13.4f}{verdict:>10}")