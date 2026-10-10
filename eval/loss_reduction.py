#!/usr/bin/env python3
"""
LOSS REDUCTION RESEARCH
=======================
Goal: reduce the SIZE and FREQUENCY of losses (esp. cluster damage).

Models the EA realistically:
- Entry: close of bar 1 AFTER the spike bar (delayed entry, proven best)
- SL distance = max(1x median range, broker floor)   [EA floors SL]
- TP distance = 2x SL distance                        [EA preserves 2:1 ratio]
- Exit when TP hit, SL hit, or after hold bars (timeout at close)

Loss-reduction layers tested:
  BE  = Breakeven stop: once trade is +1.0x SL-distance in profit, SL -> entry
  BIG = Skip spikes with ratio > 8x (large spikes continue -> fade loses)
  CD  = Cooldown: after a loss, skip the next 3 spikes (breaks cluster chains)
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = "data"

# Broker minimum stop distance (points) from live logs: Crash1000=1.14, Crash300=2.43
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
                               'median': median_ranges[i],
                               'ratio': bar_range / median_ranges[i]})
    return spikes

def simulate(df, spikes, symbol, hold, use_be, skip_big, cooldown, ratio_cap=8.0, be_profit_mult=1.0):
    """Simulate trades with realistic SL/TP. Returns list of trade dicts."""
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    floor = BROKER_FLOOR.get(symbol, 1.14)

    trades = []
    skipped_cool = 0
    skipped_big = 0

    for spike in spikes:
        idx = spike['idx']
        if idx + hold + 2 >= len(closes):
            continue

        # Cooldown: skip after recent loss
        if skipped_cool > 0:
            skipped_cool -= 1
            continue

        # Spike size filter
        if skip_big and spike['ratio'] > ratio_cap:
            skipped_big += 1
            continue

        median = spike['median']
        sl_dist = max(1.0 * median, floor)
        tp_dist = 2.0 * sl_dist

        direction = 1 if spike['type'] == 'crash' else -1  # crash -> long, boom -> short
        entry_idx = idx + 1
        entry = closes[entry_idx]

        # Walk bars
        exit_price = None
        exit_reason = 'timeout'
        be_armed = False  # breakeven armed once we saw enough profit

        for j in range(entry_idx + 1, entry_idx + hold + 1):
            if j >= len(closes):
                break
            hi, lo = highs[j], lows[j]
            best = (hi - entry) if direction == 1 else (entry - lo)
            worst = (lo - entry) if direction == 1 else (entry - hi)

            # Check TP / SL intrabar (conservative: worst first)
            if worst <= -sl_dist:
                exit_price = entry - sl_dist * direction
                exit_reason = 'sl'
                break
            if best >= tp_dist:
                exit_price = entry + tp_dist * direction
                exit_reason = 'tp'
                break

            # Breakeven: arm at +be_profit_mult * sl_dist, then SL -> entry
            if use_be and not be_armed and best >= be_profit_mult * sl_dist:
                be_armed = True
            if use_be and be_armed and worst <= 0:
                exit_price = entry
                exit_reason = 'be'
                break
        else:
            exit_price = closes[min(entry_idx + hold, len(closes) - 1)]
            exit_reason = 'timeout'

        pnl = (exit_price - entry) * direction

        trades.append({'pnl': pnl, 'reason': exit_reason, 'ratio': spike['ratio']})
        if pnl <= 0 and cooldown:
            skipped_cool = 3  # skip next 3 spikes

    return trades, skipped_big

def summarize(trades):
    if not trades:
        return None
    pnls = np.array([t['pnl'] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    total = len(pnls)
    wr = len(wins) / total * 100
    win_sum = wins.sum()
    loss_sum = abs(losses.sum())
    pf = win_sum / loss_sum if loss_sum > 0 else 999

    # max consecutive losses
    streak = max_streak = 0
    for p in pnls:
        if p <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0

    # max drawdown on cumulative PnL
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = dd.max()

    return {'trades': total, 'wr': wr, 'pf': pf, 'pnl': pnls.sum(),
            'avg_win': wins.mean() if len(wins) else 0,
            'avg_loss': losses.mean() if len(losses) else 0,
            'max_loss': pnls.min(), 'max_consec': max_streak,
            'max_dd': max_dd, 'expectancy': pnls.mean()}

def run(symbol, hold):
    df = load_candles(symbol)
    if df is None:
        print(f"{symbol}: no data")
        return
    spikes = find_bar_spikes(df)
    print(f"\n{'='*118}")
    print(f"{symbol.upper()} — {len(df)} bars, {len(spikes)} spikes, hold={hold}")
    print(f"{'='*118}")
    print(f"{'Config':<34}{'Trades':>7}{'WR%':>7}{'PF':>7}{'PnL(pt)':>9}{'AvgWin':>9}{'AvgLoss':>9}{'MaxLoss':>9}{'MaxCL':>6}{'MaxDD':>9}{'Exp':>8}")
    print("-" * 118)

    configs = [
        ("BASE (no layers)",            False, False, False),
        ("+ BE (breakeven stop)",       True,  False, False),
        ("+ BIG (skip ratio>8)",        False, True,  False),
        ("+ CD (cooldown 3)",           False, False, True),
        ("BE + BIG",                    True,  True,  False),
        ("BE + CD",                     True,  False, True),
        ("ALL (BE+BIG+CD)",             True,  True,  True),
    ]
    for name, be, big, cd in configs:
        trades, skipped = simulate(df, spikes, symbol, hold, be, big, cd)
        r = summarize(trades)
        if r is None:
            print(f"{name:<34} NO TRADES")
            continue
        print(f"{name:<34}{r['trades']:>7}{r['wr']:>6.1f}%{r['pf']:>7.2f}"
              f"{r['pnl']:>9.1f}{r['avg_win']:>9.3f}{r['avg_loss']:>9.3f}"
              f"{r['max_loss']:>9.3f}{r['max_consec']:>6}{r['max_dd']:>9.1f}{r['expectancy']:>8.4f}")
    return spikes

if __name__ == "__main__":
    # Hold per symbol: 1 and 3 are the most relevant (CRASH partial-entry research said hold=1-3)
    for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
        run(sym, 3)
    print("\n\n=== HOLD SWEEP for the best config (ALL layers) ===")
    for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
        df = load_candles(sym)
        if df is None:
            continue
        spikes = find_bar_spikes(df)
        best = None
        for hold in [1, 2, 3, 5, 7, 10]:
            trades, _ = simulate(df, spikes, sym, hold, True, True, True)
            r = summarize(trades)
            if r and (best is None or r['expectancy'] > best[1]['expectancy']):
                best = (hold, r)
        if best:
            h, r = best
            print(f"{sym.upper():<12} best hold={h}  trades={r['trades']:>5} WR={r['wr']:.1f}% "
                  f"PF={r['pf']:.2f} PnL={r['pnl']:>8.1f} maxDD={r['max_dd']:>7.1f} maxCL={r['max_consec']}")