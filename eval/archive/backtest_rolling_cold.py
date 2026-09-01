#!/usr/bin/env python3
"""Test rolling window COLD hours vs fixed COLD hours vs all hours."""

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
    df['date'] = df['time'].dt.date
    df['range'] = df['high'] - df['low']
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1
    return df

def get_cold_hours(subset, mult=3.0):
    """Compute COLD hours from a subset of data."""
    subset = detect_spikes(subset.copy(), mult)
    hourly = subset.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
    hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
    expected = subset['is_spike'].sum() / len(subset) * 100
    cold = hourly[hourly['rate'] < expected - 0.5].index.tolist()
    return sorted(cold)

def run_backtest(df, cold_hours, tp_pct, hold_bars, entry_delay, tick_exit=None):
    """Spike fade backtest."""
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
                exit_idx = j
                exit_price = prices[j]
                exit_reason = "tick_exit"
                break
            
            if direction == 1:
                pnl_pct = (entry_price - prices[j]) / entry_price
            else:
                pnl_pct = (prices[j] - entry_price) / entry_price
            
            if pnl_pct >= tp_pct:
                exit_idx = j
                exit_price = prices[j]
                exit_reason = "tp"
                break
            
            if is_spike[j]:
                if (direction == 1 and spike_dir[j] == 1) or (direction == -1 and spike_dir[j] == -1):
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
            'pnl': pnl,
            'reason': exit_reason,
            'bars_held': exit_idx - entry_idx,
        })
    
    return trades

def calc_stats(trades):
    if not trades:
        return {'n': 0, 'wr': 0, 'pf': 0, 'pnl': 0, 'avg': 0, 'sl': 0}
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls)
    wr = (pnls > 0).sum() / n * 100
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    pf = gp / gl if gl > 0 else float('inf')
    total = pnls.sum()
    avg = pnls.mean()
    sl = sum(1 for t in trades if t['reason'] == 'spike_against')
    return {'n': n, 'wr': wr, 'pf': pf, 'pnl': total, 'avg': avg, 'sl': sl}

def main():
    TP_PCT = 0.004
    HOLD_BARS = 8
    ENTRY_DELAY = 1
    SPIKE_MULT = 3.0
    TICK_EXIT = 6
    ROLLING_WINDOW = 7  # days
    
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        
        df = detect_spikes(df, SPIKE_MULT)
        dates = sorted(df['date'].unique())
        
        print(f"\n{'='*80}")
        print(f"  {sym.upper()} ({len(dates)} days, {len(df)} bars)")
        print(f"{'='*80}")
        
        # Overall fixed COLD hours
        fixed_cold = get_cold_hours(df, SPIKE_MULT)
        print(f"  Fixed COLD hours: {fixed_cold}")
        
        # Strategy 1: All hours (baseline)
        t_all = run_backtest(df, None, TP_PCT, HOLD_BARS, ENTRY_DELAY, None)
        s_all = calc_stats(t_all)
        
        # Strategy 2: Fixed COLD hours
        t_fixed = run_backtest(df, fixed_cold, TP_PCT, HOLD_BARS, ENTRY_DELAY, None)
        s_fixed = calc_stats(t_fixed)
        
        # Strategy 3: Fixed COLD + tick_exit=6
        t_fixed_tick = run_backtest(df, fixed_cold, TP_PCT, HOLD_BARS, ENTRY_DELAY, TICK_EXIT)
        s_fixed_tick = calc_stats(t_fixed_tick)
        
        # Strategy 4: Rolling window COLD hours (compute from previous 7 days)
        rolling_trades = []
        cooldown_until = 0
        
        for day_idx in range(len(dates)):
            current_date = dates[day_idx]
            
            # Get data for previous 7 days (excluding current day for lookback)
            lookback_start = max(0, day_idx - ROLLING_WINDOW)
            lookback_dates = dates[lookback_start:day_idx]  # exclude current
            
            if len(lookback_dates) < 3:
                # Not enough data, use all previous data
                lookback_dates = dates[:day_idx]
            
            if len(lookback_dates) < 1:
                continue
            
            # Compute COLD hours from lookback
            lookback_df = df[df['date'].isin(lookback_dates)]
            rolling_cold = get_cold_hours(lookback_df, SPIKE_MULT)
            
            # Run backtest on current day with rolling COLD hours
            day_df = df[df['date'] == current_date].copy()
            day_df = detect_spikes(day_df, SPIKE_MULT)
            
            day_trades = run_backtest(day_df, rolling_cold, TP_PCT, HOLD_BARS, ENTRY_DELAY, TICK_EXIT)
            rolling_trades.extend(day_trades)
        
        s_rolling = calc_stats(rolling_trades)
        
        # Strategy 5: Rolling window without tick_exit
        rolling_trades_no_tick = []
        for day_idx in range(len(dates)):
            current_date = dates[day_idx]
            lookback_start = max(0, day_idx - ROLLING_WINDOW)
            lookback_dates = dates[lookback_start:day_idx]
            
            if len(lookback_dates) < 1:
                continue
            
            lookback_df = df[df['date'].isin(lookback_dates)]
            rolling_cold = get_cold_hours(lookback_df, SPIKE_MULT)
            
            day_df = df[df['date'] == current_date].copy()
            day_df = detect_spikes(day_df, SPIKE_MULT)
            
            day_trades = run_backtest(day_df, rolling_cold, TP_PCT, HOLD_BARS, ENTRY_DELAY, None)
            rolling_trades_no_tick.extend(day_trades)
        
        s_rolling_no_tick = calc_stats(rolling_trades_no_tick)
        
        # Print results
        print(f"\n  {'Config':<35} {'Trades':>7} {'WR':>7} {'PF':>7} {'PnL':>10} {'SpikeLoss':>10}")
        print(f"  {'-'*35} {'-'*7} {'-'*7} {'-'*7} {'-'*10} {'-'*10}")
        
        for name, s in [
            ("1. All hours (baseline)", s_all),
            ("2. Fixed COLD hours", s_fixed),
            ("3. Fixed COLD + tick_exit=6", s_fixed_tick),
            ("4. Rolling 7d COLD + tick_exit=6", s_rolling),
            ("5. Rolling 7d COLD (no tick exit)", s_rolling_no_tick),
        ]:
            print(f"  {name:<35} {s['n']:>7} {s['wr']:>6.1f}% {s['pf']:>7.2f} {s['pnl']:>+.4f}% {s['sl']:>10}")
        
        # Improvement summary
        print(f"\n  Improvement over baseline:")
        for name, s in [("Fixed COLD", s_fixed), ("Fixed COLD+Tick", s_fixed_tick), 
                        ("Rolling COLD+Tick", s_rolling), ("Rolling COLD", s_rolling_no_tick)]:
            wr_d = s['wr'] - s_all['wr']
            pf_d = s['pf'] - s_all['pf']
            sl_s = s_all['sl'] - s['sl']
            print(f"    {name:<20} WR: {wr_d:>+.1f}%  PF: {pf_d:>+.2f}  SpikeLoss saved: {sl_s:>+.0f}")
        
        # Rolling window COLD hours used per day
        print(f"\n  Rolling COLD hours used (sample):")
        for day_idx in range(min(7, len(dates))):
            current_date = dates[day_idx]
            lookback_start = max(0, day_idx - ROLLING_WINDOW)
            lookback_dates = dates[lookback_start:day_idx]
            if len(lookback_dates) < 1:
                continue
            lookback_df = df[df['date'].isin(lookback_dates)]
            rc = get_cold_hours(lookback_df, SPIKE_MULT)
            fixed_match = len(set(rc) & set(fixed_cold))
            print(f"    {current_date}: {rc} (match with fixed: {fixed_match}/{max(len(rc),1)})")

if __name__ == '__main__':
    main()
