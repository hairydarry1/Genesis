#!/usr/bin/env python3
"""Backtest spike fade strategy: all hours vs COLD hours only."""

import pandas as pd
import numpy as np
import os

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
    """Detect spikes from candle ranges."""
    med = df['range'].median()
    thresh = mult * med
    df['is_spike'] = df['range'] > thresh
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1   # UP spike
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1  # DOWN spike
    return df

def run_backtest(df, cold_hours, tp_pct, hold_bars, entry_delay):
    """Spike fade backtest with optional hour filter."""
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    hours = df['hour'].values
    n = len(prices)
    
    trades = []
    cooldown_until = 0
    
    for i in range(n):
        if not is_spike[i]:
            continue
        if i < cooldown_until:
            continue
        
        # Hour filter
        if cold_hours is not None and hours[i] not in cold_hours:
            continue
        
        # Entry after delay
        entry_idx = i + entry_delay
        if entry_idx >= n:
            continue
        
        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        
        # If UP spike on BOOM → price spiked up → we SELL (fade)
        # If DOWN spike on CRASH → price spiked down → we BUY (fade)
        # Direction: +1 = UP spike → short entry, -1 = DOWN spike → long entry
        
        exit_idx = None
        exit_price = None
        exit_reason = "timeout"
        
        for j in range(entry_idx + 1, min(entry_idx + hold_bars + 1, n)):
            # TP check
            if direction == 1:  # UP spike → short
                pnl_pct = (entry_price - prices[j]) / entry_price
            else:  # DOWN spike → long
                pnl_pct = (prices[j] - entry_price) / entry_price
            
            if pnl_pct >= tp_pct:
                exit_idx = j
                exit_price = prices[j]
                exit_reason = "tp"
                break
            
            # Spike against us
            if is_spike[j]:
                if (direction == 1 and spike_dir[j] == 1) or (direction == -1 and spike_dir[j] == -1):
                    # Same direction spike = against us
                    exit_idx = j
                    exit_price = prices[j]
                    exit_reason = "spike_against"
                    break
        
        if exit_idx is None:
            exit_idx = min(entry_idx + hold_bars, n - 1)
            exit_price = prices[exit_idx]
            exit_reason = "timeout"
        
        if direction == 1:
            pnl = (entry_price - exit_price) / entry_price
        else:
            pnl = (exit_price - entry_price) / entry_price
        
        trades.append({
            'entry_idx': entry_idx,
            'exit_idx': exit_idx,
            'entry': entry_price,
            'exit': exit_price,
            'pnl': pnl,
            'reason': exit_reason,
            'direction': 'short' if direction == 1 else 'long',
            'hour': hours[i],
        })
    
    return trades

def summarize(trades, name, expected_spikes_per_trade):
    if not trades:
        print(f"  {name}: No trades")
        return
    
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls)
    wr = (pnls > 0).sum() / n * 100
    total = pnls.sum()
    avg = pnls.mean()
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    pf = gp / gl if gl > 0 else float('inf')
    
    from collections import Counter
    rc = Counter(t['reason'] for t in trades)
    
    print(f"  {name}:")
    print(f"    Trades: {n} | WR: {wr:.1f}% | PnL: {total:+.4f}% | Avg: {avg:+.4f}% | PF: {pf:.2f}")
    for r in sorted(rc):
        rp = np.array([t['pnl'] for t in trades if t['reason'] == r])
        rw = (rp > 0).sum() / len(rp) * 100 if len(rp) > 0 else 0
        print(f"      {r}: {rc[r]} trades ({rw:.0f}% WR, PnL={rp.sum():+.4f}%)")

def main():
    # Parameters
    TP_PCT = 0.004
    HOLD_BARS = 8
    ENTRY_DELAY = 1
    SPIKE_MULT = 3.0
    
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        
        df = detect_spikes(df, SPIKE_MULT)
        total_spikes = df['is_spike'].sum()
        
        print(f"\n{'='*70}")
        print(f"  {sym.upper()} ({len(df)} bars, {total_spikes} spikes)")
        print(f"{'='*70}")
        
        # Identify COLD hours for this symbol
        hourly = df.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
        hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
        expected_rate = total_spikes / len(df) * 100
        
        cold_threshold = expected_rate - 0.5  # Below average
        cold_hours = sorted(hourly[hourly['rate'] < cold_threshold].index.tolist())
        
        # Also test top 3 safest hours
        safest_3 = sorted(hourly.nsmallest(3, 'rate').index.tolist())
        safest_6 = sorted(hourly.nsmallest(6, 'rate').index.tolist())
        
        print(f"  Expected spike rate: {expected_rate:.1f}%")
        print(f"  COLD hours (< {cold_threshold:.1f}%): {cold_hours}")
        print(f"  Safest 3 hours: {safest_3}")
        print(f"  Safest 6 hours: {safest_6}")
        
        # Run backtests
        print(f"\n  --- ALL HOURS ---")
        t_all = run_backtest(df, None, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        summarize(t_all, "All hours", total_spikes / len(t_all) if t_all else 0)
        
        print(f"\n  --- COLD HOURS ONLY ---")
        t_cold = run_backtest(df, cold_hours, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        summarize(t_cold, "COLD hours", total_spikes / len(t_cold) if t_cold else 0)
        
        print(f"\n  --- SAFEST 3 HOURS ---")
        t_3 = run_backtest(df, safest_3, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        summarize(t_3, "Safest 3 hours", total_spikes / len(t_3) if t_3 else 0)
        
        print(f"\n  --- SAFEST 6 HOURS ---")
        t_6 = run_backtest(df, safest_6, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        summarize(t_6, "Safest 6 hours", total_spikes / len(t_6) if t_6 else 0)
        
        # Comparison
        p_all = sum(t['pnl'] for t in t_all) if t_all else 0
        p_cold = sum(t['pnl'] for t in t_cold) if t_cold else 0
        p_3 = sum(t['pnl'] for t in t_3) if t_3 else 0
        p_6 = sum(t['pnl'] for t in t_6) if t_6 else 0
        
        wr_all = (np.array([t['pnl'] for t in t_all]) > 0).mean() * 100 if t_all else 0
        wr_cold = (np.array([t['pnl'] for t in t_cold]) > 0).mean() * 100 if t_cold else 0
        wr_3 = (np.array([t['pnl'] for t in t_3]) > 0).mean() * 100 if t_3 else 0
        wr_6 = (np.array([t['pnl'] for t in t_6]) > 0).mean() * 100 if t_6 else 0
        
        n_all = len(t_all)
        n_cold = len(t_cold)
        n_3 = len(t_3)
        n_6 = len(t_6)
        
        # Per-trade PnL
        avg_all = p_all / n_all if n_all else 0
        avg_cold = p_cold / n_cold if n_cold else 0
        avg_3 = p_3 / n_3 if n_3 else 0
        avg_6 = p_6 / n_6 if n_6 else 0
        
        print(f"\n  --- COMPARISON ---")
        print(f"  {'Filter':<20} {'Trades':>7} {'WR':>7} {'Total PnL':>12} {'Avg/Trade':>12} {'Trades/Hr':>10}")
        print(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*12} {'-'*12} {'-'*10}")
        
        hours_in_data = len(df) / 60  # M1 bars to hours
        print(f"  {'All hours':<20} {n_all:>7} {wr_all:>6.1f}% {p_all:>+12.4f}% {avg_all:>+12.4f}% {n_all/hours_in_data:>10.1f}")
        print(f"  {'COLD hours':<20} {n_cold:>7} {wr_cold:>6.1f}% {p_cold:>+12.4f}% {avg_cold:>+12.4f}% {n_cold/hours_in_data:>10.1f}")
        print(f"  {'Safest 3 hrs':<20} {n_3:>7} {wr_3:>6.1f}% {p_3:>+12.4f}% {avg_3:>+12.4f}% {n_3/hours_in_data:>10.1f}")
        print(f"  {'Safest 6 hrs':<20} {n_6:>7} {wr_6:>6.1f}% {p_6:>+12.4f}% {avg_6:>+12.4f}% {n_6/hours_in_data:>10.1f}")
        
        # Win rate improvement
        if wr_all > 0:
            print(f"\n  WR improvement (COLD vs All): {wr_cold - wr_all:>+.1f}%")
            print(f"  Avg PnL improvement (COLD vs All): {avg_cold - avg_all:>+.4f}%")
    
    # Cross-symbol summary
    print(f"\n{'='*70}")
    print(f"  CROSS-SYMBOL SUMMARY")
    print(f"{'='*70}")
    
    all_results = []
    for sym in symbols:
        df = load_candles(sym)
        if df is None: continue
        df = detect_spikes(df, SPIKE_MULT)
        
        hourly = df.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
        hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
        expected = df['is_spike'].sum() / len(df) * 100
        cold_h = sorted(hourly[hourly['rate'] < expected - 0.5].index.tolist())
        
        t_all = run_backtest(df, None, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        t_cold = run_backtest(df, cold_h, TP_PCT, HOLD_BARS, ENTRY_DELAY)
        
        wr_all = (np.array([t['pnl'] for t in t_all]) > 0).mean() * 100 if t_all else 0
        wr_cold = (np.array([t['pnl'] for t in t_cold]) > 0).mean() * 100 if t_cold else 0
        avg_all = np.mean([t['pnl'] for t in t_all]) if t_all else 0
        avg_cold = np.mean([t['pnl'] for t in t_cold]) if t_cold else 0
        
        all_results.append((sym, wr_all, wr_cold, avg_all, avg_cold, len(t_all), len(t_cold)))
    
    print(f"  {'Symbol':<12} {'WR All':>8} {'WR COLD':>8} {'WR Diff':>8} {'Avg All':>10} {'Avg COLD':>10} {'Avg Diff':>10}")
    print(f"  {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*10} {'-'*10} {'-'*10}")
    for sym, wa, wc, aa, ac, na, nc in all_results:
        print(f"  {sym:<12} {wa:>7.1f}% {wc:>7.1f}% {wc-wa:>+7.1f}% {aa:>+9.4f}% {ac:>+9.4f}% {ac-aa:>+9.4f}%")

if __name__ == '__main__':
    main()
