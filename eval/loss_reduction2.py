#!/usr/bin/env python3
"""
LOSS REDUCTION RESEARCH — ROUND 2
=================================
Tune the levers that showed promise and test new ones:

1. BE arm threshold — arm breakeven at 0.3x / 0.5x / 1.0x of SL distance
2. BIG spike-size cap — try ratio caps 6/8/10/12 (tradeoff vs protection)
3. EARLY LOSS CUT — exit at bar close if trade is down > X% of SL distance
4. DAILY LOSS CAP — stop trading for the day after losing Y points
5. Combos of the best levers
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
                               'median': median_ranges[i],
                               'ratio': bar_range / median_ranges[i]})
    return spikes

def simulate(df, spikes, symbol, hold=3,
             be_arm=0.0,          # arm breakeven at N x sl_dist profit (0 = off)
             ratio_cap=999,       # skip spikes with ratio > cap
             early_cut=0.0,       # exit at close if down > N x sl_dist
             cooldown=0,          # skip N spikes after a loss
             daily_cap=0.0):      # stop for the day after losing > N points
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    times = df['time'].values if 'time' in df.columns else None
    floor = BROKER_FLOOR.get(symbol, 1.14)

    trades = []
    skip_cool = 0
    day = None
    day_pnl = 0.0

    for spike in spikes:
        idx = spike['idx']
        if idx + hold + 2 >= len(closes):
            continue
        if skip_cool > 0:
            skip_cool -= 1
            continue
        if spike['ratio'] > ratio_cap:
            continue

        # daily cap
        if times is not None:
            t = pd.Timestamp(times[idx])
            cur_day = t.date()
            if cur_day != day:
                day = cur_day
                day_pnl = 0.0
            if daily_cap > 0 and day_pnl <= -daily_cap:
                continue

        median = spike['median']
        sl_dist = max(1.0 * median, floor)
        tp_dist = 2.0 * sl_dist
        direction = 1 if spike['type'] == 'crash' else -1
        entry = closes[idx + 1]

        exit_price = None
        reason = 'timeout'
        be_armed = False

        for j in range(idx + 2, idx + hold + 2):
            if j >= len(closes):
                break
            hi, lo = highs[j], lows[j]
            best = (hi - entry) if direction == 1 else (entry - lo)
            worst = (lo - entry) if direction == 1 else (entry - hi)

            if worst <= -sl_dist:
                exit_price = entry - sl_dist * direction
                reason = 'sl'
                break
            if best >= tp_dist:
                exit_price = entry + tp_dist * direction
                reason = 'tp'
                break
            if be_arm > 0 and not be_armed and best >= be_arm * sl_dist:
                be_armed = True
            if be_armed and worst <= 0:
                exit_price = entry
                reason = 'be'
                break
            # early loss cut at bar close
            if early_cut > 0:
                c = closes[j]
                cur = (c - entry) if direction == 1 else (entry - c)
                if cur <= -early_cut * sl_dist:
                    exit_price = c
                    reason = 'earlycut'
                    break
        else:
            exit_price = closes[min(idx + hold + 1, len(closes) - 1)]
            reason = 'timeout'

        pnl = (exit_price - entry) * direction
        trades.append({'pnl': pnl, 'reason': reason, 'ratio': spike['ratio']})
        if times is not None:
            day_pnl += pnl
        if pnl <= 0 and cooldown > 0:
            skip_cool = cooldown

    return trades

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
            'avg_win': wins.mean() if len(wins) else 0,
            'avg_loss': losses.mean() if len(losses) else 0,
            'max_loss': pnls.min(), 'max_consec': max_streak,
            'max_dd': max_dd, 'expectancy': pnls.mean(),
            'reasons': {r: sum(1 for t in trades if t['reason'] == r) for r in set(t['reason'] for t in trades)}}

def show(symbol, label, **kw):
    df = load_candles(symbol)
    if df is None:
        return
    spikes = find_bar_spikes(df)
    trades = simulate(df, spikes, symbol, **kw)
    r = summarize(trades)
    if r is None:
        print(f"  {label:<38} NO TRADES")
        return
    print(f"  {label:<38}{r['trades']:>6}{r['wr']:>6.1f}%{r['pf']:>7.2f}{r['pnl']:>9.1f}"
          f"{r['max_loss']:>8.3f}{r['max_consec']:>6}{r['max_dd']:>8.1f}{r['expectancy']:>8.4f}"
          f"  [{r['reasons']}]")

if __name__ == "__main__":
    print(f"{'Config':<39}{'Trd':>6}{'WR%':>7}{'PF':>8}{'PnL':>10}{'MaxL':>8}{'MaxCL':>6}{'MaxDD':>8}{'Exp':>9}  reasons")
    print("=" * 150)
    for sym in ["crash1000", "boom1000", "crash500", "boom500"]:
        print(f"\n### {sym.upper()} (hold=3)")
        show(sym, "BASE", )
        show(sym, "BE arm=0.5x", be_arm=0.5)
        show(sym, "BE arm=0.3x", be_arm=0.3)
        show(sym, "BIG cap=6", ratio_cap=6)
        show(sym, "BIG cap=10", ratio_cap=10)
        show(sym, "BIG cap=12", ratio_cap=12)
        show(sym, "EARLY cut=0.5x", early_cut=0.5)
        show(sym, "EARLY cut=0.7x", early_cut=0.7)
        show(sym, "CD=3", cooldown=3)
        show(sym, "DAILY cap=10pt", daily_cap=10)
        show(sym, "DAILY cap=15pt", daily_cap=15)
        show(sym, "BE0.5 + CD3", be_arm=0.5, cooldown=3)
        show(sym, "BE0.3 + CD3 + DAILY15", be_arm=0.3, cooldown=3, daily_cap=15)
        show(sym, "FULL: BE0.3+CD3+DAILY15+CAP10", be_arm=0.3, cooldown=3, daily_cap=15, ratio_cap=10)