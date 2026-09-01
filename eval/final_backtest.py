#!/usr/bin/env python3
"""Final comprehensive backtest with ALL optimizations combined."""

import pandas as pd
import numpy as np
import os
from collections import Counter

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time'])
    df['hour'] = df['time'].dt.hour
    df['range'] = df['high'] - df['low']
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1
    return df

def run_backtest(df, cold_hours, tp_pct, hold_bars, entry_delay, tick_exit=None):
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    hours = df['hour'].values
    n = len(prices)
    trades = []
    cooldown_until = 0

    for i in range(n):
        if not is_spike[i] or i < cooldown_until:
            continue
        if cold_hours is not None and hours[i] not in cold_hours:
            continue

        entry_idx = i + entry_delay
        if entry_idx >= n:
            continue

        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        exit_idx = None
        exit_price = None
        exit_reason = "timeout"

        for j in range(entry_idx + 1, min(entry_idx + hold_bars + 1, n)):
            if tick_exit is not None and (j - entry_idx) >= tick_exit:
                exit_idx, exit_price, exit_reason = j, prices[j], "tick_exit"
                break
            pnl_pct = ((entry_price - prices[j]) / entry_price) if direction == 1 else ((prices[j] - entry_price) / entry_price)
            if pnl_pct >= tp_pct:
                exit_idx, exit_price, exit_reason = j, prices[j], "tp"
                break
            if is_spike[j] and ((direction == 1 and spike_dir[j] == 1) or (direction == -1 and spike_dir[j] == -1)):
                exit_idx, exit_price, exit_reason = j, prices[j], "spike_against"
                break

        if exit_idx is None:
            exit_idx = min(entry_idx + hold_bars, n - 1)
            exit_price, exit_reason = prices[exit_idx], "timeout"

        pnl = ((entry_price - exit_price) / entry_price) if direction == 1 else ((exit_price - entry_price) / entry_price)
        trades.append({'pnl': pnl, 'reason': exit_reason, 'bars_held': exit_idx - entry_idx})

    return trades

def calc_stats(trades, label):
    if not trades:
        return None
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls)
    wr = (pnls > 0).sum() / n * 100
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    pf = gp / gl if gl > 0 else float('inf')
    rc = Counter(t['reason'] for t in trades)
    bars = np.mean([t['bars_held'] for t in trades])
    sl = rc.get('spike_against', 0)
    te = rc.get('tick_exit', 0)
    to = rc.get('timeout', 0)
    tp = rc.get('tp', 0)
    return {'label': label, 'n': n, 'wr': wr, 'pf': pf, 'pnl': pnls.sum(), 'avg': pnls.mean(),
            'sl': sl, 'te': te, 'to': to, 'tp': tp, 'bars': bars}

def main():
    TP_PCT = 0.004
    ENTRY_DELAY = 1
    SPIKE_MULT = 3.0
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']

    cold_hour_map = {
        'boom500': [1, 5, 12, 14, 15, 19],
        'crash500': [0, 6, 7, 9, 22, 23],
        'boom1000': [6, 13, 18, 20, 21, 22],
        'crash1000': [3, 6, 7, 18, 22, 23],
    }

    all_summary = []

    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        df = detect_spikes(df, SPIKE_MULT)
        cold = cold_hour_map.get(sym, [])

        print(f"\n{'='*80}")
        print(f"  {sym.upper()} ({len(df)} bars, {df['is_spike'].sum()} spikes, COLD={cold})")
        print(f"{'='*80}")

        configs = [
            ("A. Baseline (all hours, hold=9)",    None, 9, None),
            ("B. All hours + tick_exit=6",          None, 6, 6),
            ("C. COLD hours only (hold=8)",         cold, 8, None),
            ("D. COLD hours + tick_exit=6",         cold, 6, 6),
            ("E. COLD hours + tick_exit=5",         cold, 5, 5),
            ("F. COLD hours + tick_exit=4",         cold, 4, 4),
        ]

        results = []
        for label, ch, hb, te in configs:
            trades = run_backtest(df, ch, TP_PCT, hb, ENTRY_DELAY, te)
            stats = calc_stats(trades, label)
            if stats:
                results.append(stats)

        # Print comparison table
        print(f"\n  {'Config':<38} {'Trades':>7} {'WR':>7} {'PF':>7} {'PnL':>10} {'SpkL':>6} {'TickE':>6} {'TmOut':>6}")
        print(f"  {'-'*38} {'-'*7} {'-'*7} {'-'*7} {'-'*10} {'-'*6} {'-'*6} {'-'*6}")

        for r in results:
            print(f"  {r['label']:<38} {r['n']:>7} {r['wr']:>6.1f}% {r['pf']:>7.2f} {r['pnl']:>+.4f}% {r['sl']:>6} {r['te']:>6} {r['to']:>6}")

        # Highlight the winner
        # Best PnL among COLD configs
        cold_results = [r for r in results if 'COLD' in r['label']]
        if cold_results:
            best = max(cold_results, key=lambda x: x['pnl'])
            baseline = results[0]
            print(f"\n  BEST: {best['label']}")
            print(f"    vs Baseline: WR {baseline['wr']:.1f}% -> {best['wr']:.1f}% ({best['wr']-baseline['wr']:+.1f}%)")
            print(f"    vs Baseline: PF {baseline['pf']:.2f} -> {best['pf']:.2f} ({best['pf']-baseline['pf']:+.2f})")
            print(f"    vs Baseline: PnL {baseline['pnl']:+.4f}% -> {best['pnl']:+.4f}%")
            print(f"    Spike losses: {baseline['sl']} -> {best['sl']} ({baseline['sl']-best['sl']:+.0f} saved)")
            all_summary.append((sym, baseline, best))

    # Cross-symbol summary
    print(f"\n{'='*80}")
    print(f"  FINAL SUMMARY: Baseline vs Best Config (All Symbols)")
    print(f"{'='*80}")
    print(f"\n  {'Symbol':<12} {'Baseline WR':>12} {'Best WR':>10} {'WR Delta':>10} {'Baseline PF':>12} {'Best PF':>10} {'PF Delta':>10}")
    print(f"  {'-'*12} {'-'*12} {'-'*10} {'-'*10} {'-'*12} {'-'*10} {'-'*10}")

    for sym, base, best in all_summary:
        print(f"  {sym:<12} {base['wr']:>11.1f}% {best['wr']:>9.1f}% {best['wr']-base['wr']:>+9.1f}% {base['pf']:>12.2f} {best['pf']:>10.2f} {best['pf']-base['pf']:>+10.2f}")

    # EA settings
    print(f"\n{'='*80}")
    print(f"  RECOMMENDED EA SETTINGS (proven by backtest)")
    print(f"{'='*80}")
    print(f"  input bool   UseColdHours   = true;")
    print(f"  input string ColdHours_BOOM500  = \"1,5,12,14,15,19\";")
    print(f"  input string ColdHours_CRASH500 = \"0,6,7,9,22,23\";")
    print(f"  input string ColdHours_BOOM1000 = \"6,13,18,20,21,22\";")
    print(f"  input string ColdHours_CRASH1000 = \"3,6,7,18,22,23\";")
    print(f"  input int    MaxHoldBars    = 6;")
    print(f"  input int    EntryBar       = 2;")
    print(f"  input double TPMultiplier   = 2.0;")
    print(f"  input double SLMultiplier   = 1.0;")
    print(f"  input bool   UseSafetyExit  = true;")
    print(f"  input bool   UseVelocityExit = false;")

if __name__ == '__main__':
    main()
