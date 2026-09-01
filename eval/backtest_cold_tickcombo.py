#!/usr/bin/env python3
"""Backtest combining COLD hours filter + tick count protection."""

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
    """Spike fade backtest with COLD hours + optional tick count exit."""
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
        
        entry_idx = i + entry_delay
        if entry_idx >= n:
            continue
        
        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        
        exit_idx = None
        exit_price = None
        exit_reason = "timeout"
        
        for j in range(entry_idx + 1, min(entry_idx + hold_bars + 1, n)):
            # Tick count exit
            if tick_exit is not None and (j - entry_idx) >= tick_exit:
                exit_idx = j
                exit_price = prices[j]
                exit_reason = "tick_exit"
                break
            
            # TP check
            if direction == 1:
                pnl_pct = (entry_price - prices[j]) / entry_price
            else:
                pnl_pct = (prices[j] - entry_price) / entry_price
            
            if pnl_pct >= tp_pct:
                exit_idx = j
                exit_price = prices[j]
                exit_reason = "tp"
                break
            
            # Spike against us
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

def summarize(trades, name):
    if not trades:
        print(f"  {name:<30} No trades")
        return
    
    pnls = np.array([t['pnl'] for t in trades])
    n = len(pnls)
    wr = (pnls > 0).sum() / n * 100
    total = pnls.sum()
    avg = pnls.mean()
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    pf = gp / gl if gl > 0 else float('inf')
    bars = np.mean([t['bars_held'] for t in trades])
    
    rc = Counter(t['reason'] for t in trades)
    
    # Key metrics
    spike_losses = rc.get('spike_against', 0)
    tick_exits = rc.get('tick_exit', 0)
    timeouts = rc.get('timeout', 0)
    tps = rc.get('tp', 0)
    
    print(f"  {name:<30} Trades={n:>5} WR={wr:>5.1f}% PF={pf:>5.2f} PnL={total:>+.4f}% AvgBars={bars:>4.1f} SpikeLoss={spike_losses:>4} TickExit={tick_exits:>4}")

def main():
    TP_PCT = 0.004
    HOLD_BARS = 8
    ENTRY_DELAY = 1
    SPIKE_MULT = 3.0
    
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    
    # Test different tick_exit values
    TICK_EXITS = [None, 3, 4, 5, 6, 7, 8]
    
    all_results = {}
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        
        df = detect_spikes(df, SPIKE_MULT)
        
        # Identify COLD hours
        hourly = df.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
        hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
        expected = df['is_spike'].sum() / len(df) * 100
        cold_hours = sorted(hourly[hourly['rate'] < expected - 0.5].index.tolist())
        
        print(f"\n{'='*80}")
        print(f"  {sym.upper()} | COLD hours: {cold_hours}")
        print(f"{'='*80}")
        
        print(f"\n  {'Config':<30} {'Trades':>7} {'WR':>7} {'PF':>7} {'PnL':>10} {'AvgBars':>8} {'SpikeLoss':>10} {'TickExit':>9}")
        print(f"  {'-'*30} {'-'*7} {'-'*7} {'-'*7} {'-'*10} {'-'*8} {'-'*10} {'-'*9}")
        
        sym_results = []
        
        # Test 1: All hours, no tick exit (baseline)
        t = run_backtest(df, None, TP_PCT, HOLD_BARS, ENTRY_DELAY, None)
        summarize(t, "All hours, no limit")
        sym_results.append(('all_no_limit', t))
        
        # Test 2: All hours, tick exit=6
        t = run_backtest(df, None, TP_PCT, HOLD_BARS, ENTRY_DELAY, 6)
        summarize(t, "All hours, tick_exit=6")
        sym_results.append(('all_tick6', t))
        
        # Test 3: COLD hours, no tick exit
        t = run_backtest(df, cold_hours, TP_PCT, HOLD_BARS, ENTRY_DELAY, None)
        summarize(t, "COLD hours, no limit")
        sym_results.append(('cold_no_limit', t))
        
        # Test 4-8: COLD hours + different tick exits
        for te in TICK_EXITS[1:]:  # skip None
            t = run_backtest(df, cold_hours, TP_PCT, HOLD_BARS, ENTRY_DELAY, te)
            summarize(t, f"COLD hours, tick_exit={te}")
            sym_results.append((f'cold_tick{te}', t))
        
        all_results[sym] = sym_results
    
    # Cross-symbol comparison table
    print(f"\n{'='*80}")
    print(f"  CROSS-SYMBOL COMPARISON: Best Configs")
    print(f"{'='*80}")
    
    print(f"\n  {'Symbol':<10} {'All/NoLim':<15} {'All/Tick6':<15} {'COLD/NoLim':<15} {'COLD/Tick5':<15} {'COLD/Tick6':<15}")
    print(f"  {'':10} {'WR / PF / PnL':<15} {'WR / PF / PnL':<15} {'WR / PF / PnL':<15} {'WR / PF / PnL':<15} {'WR / PF / PnL':<15}")
    print(f"  {'-'*10} {'-'*15} {'-'*15} {'-'*15} {'-'*15} {'-'*15}")
    
    for sym in symbols:
        if sym not in all_results:
            continue
        
        configs = {}
        for name, trades in all_results[sym]:
            if not trades:
                continue
            pnls = np.array([t['pnl'] for t in trades])
            n = len(pnls)
            wr = (pnls > 0).sum() / n * 100
            gp = pnls[pnls > 0].sum()
            gl = abs(pnls[pnls <= 0].sum())
            pf = gp / gl if gl > 0 else float('inf')
            total = pnls.sum()
            spike_loss = sum(1 for t in trades if t['reason'] == 'spike_against')
            configs[name] = (wr, pf, total, spike_loss)
        
        def fmt(key):
            if key in configs:
                wr, pf, total, sl = configs[key]
                return f"{wr:>4.1f}/{pf:>4.2f}/{total:>+.3f}%"
            return "N/A"
        
        def fmt_sl(key):
            return str(configs[key][3]) if key in configs else "N/A"
        
        print(f"  {sym:<10} {fmt('all_no_limit'):<15} {fmt('all_tick6'):<15} {fmt('cold_no_limit'):<15} {fmt('cold_tick5'):<15} {fmt('cold_tick6'):<15}")
    
    # Summary of improvements
    print(f"\n{'='*80}")
    print(f"  IMPROVEMENT OVER BASELINE (All hours, no limit)")
    print(f"{'='*80}")
    
    print(f"\n  {'Symbol':<10} {'Config':<25} {'WR Delta':>10} {'PF Delta':>10} {'SpikeLoss Saved':>16}")
    print(f"  {'-'*10} {'-'*25} {'-'*10} {'-'*10} {'-'*16}")
    
    for sym in symbols:
        if sym not in all_results:
            continue
        
        base_wr, base_pf, base_total, base_sl = 0, 0, 0, 0
        for name, trades in all_results[sym]:
            if name == 'all_no_limit' and trades:
                pnls = np.array([t['pnl'] for t in trades])
                n = len(pnls)
                base_wr = (pnls > 0).sum() / n * 100
                gp = pnls[pnls > 0].sum()
                gl = abs(pnls[pnls <= 0].sum())
                base_pf = gp / gl if gl > 0 else float('inf')
                base_sl = sum(1 for t in trades if t['reason'] == 'spike_against')
        
        for name, trades in all_results[sym]:
            if not trades or name == 'all_no_limit':
                continue
            if 'cold' not in name:
                continue
            
            pnls = np.array([t['pnl'] for t in trades])
            n = len(pnls)
            wr = (pnls > 0).sum() / n * 100
            gp = pnls[pnls > 0].sum()
            gl = abs(pnls[pnls <= 0].sum())
            pf = gp / gl if gl > 0 else float('inf')
            sl = sum(1 for t in trades if t['reason'] == 'spike_against')
            
            wr_delta = wr - base_wr
            pf_delta = pf - base_pf
            sl_saved = base_sl - sl
            
            print(f"  {sym:<10} {name:<25} {wr_delta:>+9.1f}% {pf_delta:>+10.2f} {sl_saved:>+16d}")

if __name__ == '__main__':
    main()
