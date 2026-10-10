#!/usr/bin/env python3
"""
COOLDOWN VALIDATION
===================
The loss-cooldown (skip N spikes after a loss) was the only lever that
cut losses WITHOUT hurting expectancy. Validate it:
1. Out-of-sample: does it help on unseen data halves?
2. Tune N (1..6 spikes)
3. Compare spike-cooldown vs bar-cooldown
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = "data"
BROKER_FLOOR = {"crash1000": 1.14, "crash500": 1.14, "boom1000": 1.14, "boom500": 1.14}

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
                               'median': median_ranges[i],
                               'ratio': bar_range / median_ranges[i]})
    return spikes

def simulate(df, spikes, symbol, hold=3, cooldown=0, cool_by_bars=False):
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    floor = BROKER_FLOOR.get(symbol, 1.14)

    trades = []
    skip_cool = 0
    last_loss_bar = -9999

    for spike in spikes:
        idx = spike['idx']
        if idx + hold + 2 >= len(closes):
            continue

        if cool_by_bars:
            if idx - last_loss_bar <= cooldown:
                continue
        else:
            if skip_cool > 0:
                skip_cool -= 1
                continue

        median = spike['median']
        sl_dist = max(1.0 * median, floor)
        tp_dist = 2.0 * sl_dist
        direction = 1 if spike['type'] == 'crash' else -1
        entry = closes[idx + 1]

        exit_price = None
        for j in range(idx + 2, idx + hold + 2):
            if j >= len(closes):
                break
            hi, lo = highs[j], lows[j]
            best = (hi - entry) if direction == 1 else (entry - lo)
            worst = (lo - entry) if direction == 1 else (entry - hi)
            if worst <= -sl_dist:
                exit_price = entry - sl_dist * direction
                break
            if best >= tp_dist:
                exit_price = entry + tp_dist * direction
                break
        else:
            exit_price = closes[min(idx + hold + 1, len(closes) - 1)]

        pnl = (exit_price - entry) * direction
        trades.append({'pnl': pnl, 'idx': idx})
        if pnl <= 0:
            skip_cool = cooldown
            last_loss_bar = idx

    return trades

def summarize(trades):
    if not trades:
        return None
    pnls = np.array([t['pnl'] for t in trades])
    wins = pnls[pnls > 0]
    total = len(pnls)
    wr = len(wins) / total * 100
    win_sum = wins.sum()
    loss_sum = abs(pnls[pnls <= 0].sum())
    pf = win_sum / loss_sum if loss_sum > 0 else 999
    streak = max_streak = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    cum = np.cumsum(pnls)
    max_dd = (np.maximum.accumulate(cum) - cum).max()
    return {'trades': total, 'wr': wr, 'pf': pf, 'pnl': pnls.sum(),
            'max_consec': max_streak, 'max_dd': max_dd, 'expectancy': pnls.mean()}

print("=" * 120)
print("OUT-OF-SAMPLE VALIDATION: cooldown after loss, hold=3")
print("=" * 120)

for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
    df = load_candles(sym)
    if df is None:
        continue
    spikes = find_bar_spikes(df)
    mid = len(spikes) // 2
    half1, half2 = spikes[:mid], spikes[mid:]

    print(f"\n--- {sym.upper()} (train={len(half1)} spikes, test={len(half2)} spikes) ---")
    print(f"  {'Config':<18}{'Half':<7}{'Trades':>7}{'WR%':>7}{'PF':>7}{'PnL':>9}{'MaxCL':>6}{'MaxDD':>8}{'Exp':>8}")
    for cd in [0, 3]:
        for name, h in [("train", half1), ("test", half2)]:
            trades = simulate(df, h, sym, hold=3, cooldown=cd)
            r = summarize(trades)
            if r is None:
                continue
            label = f"CD={cd}" if cd else "BASE"
            print(f"  {label:<18}{name:<7}{r['trades']:>7}{r['wr']:>6.1f}%{r['pf']:>7.2f}"
                  f"{r['pnl']:>9.1f}{r['max_consec']:>6}{r['max_dd']:>8.1f}{r['expectancy']:>8.4f}")

print("\n" + "=" * 120)
print("COOLDOWN LENGTH TUNING (full data, hold=3)")
print("=" * 120)
print(f"  {'Symbol':<12}{'CD':>4}{'Trades':>8}{'WR%':>7}{'PF':>7}{'PnL':>9}{'MaxCL':>6}{'MaxDD':>8}{'Exp':>8}")
for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
    df = load_candles(sym)
    if df is None:
        continue
    spikes = find_bar_spikes(df)
    for cd in [0, 1, 2, 3, 4, 5, 6]:
        trades = simulate(df, spikes, sym, hold=3, cooldown=cd)
        r = summarize(trades)
        if r is None:
            continue
        print(f"  {sym:<12}{cd:>4}{r['trades']:>8}{r['wr']:>6.1f}%{r['pf']:>7.2f}"
              f"{r['pnl']:>9.1f}{r['max_consec']:>6}{r['max_dd']:>8.1f}{r['expectancy']:>8.4f}")
    print()

print("=" * 120)
print("BAR-BASED COOLDOWN vs SPIKE-BASED COOLDOWN (CD=60 bars vs CD=3 spikes)")
print("=" * 120)
for sym in ["crash1000", "boom1000"]:
    df = load_candles(sym)
    if df is None:
        continue
    spikes = find_bar_spikes(df)
    print(f"  {sym.upper():<12}", end="")
    for label, kw in [("BASE", dict(cooldown=0)),
                      ("3 spikes", dict(cooldown=3)),
                      ("60 bars", dict(cooldown=60, cool_by_bars=True))]:
        trades = simulate(df, spikes, sym, hold=3, **kw)
        r = summarize(trades)
        if r:
            print(f"  {label:<10} Trd={r['trades']:>5} WR={r['wr']:.1f}% PF={r['pf']:.2f} "
                  f"PnL={r['pnl']:>8.1f} MaxCL={r['max_consec']} MaxDD={r['max_dd']:.1f}", end="")
    print()