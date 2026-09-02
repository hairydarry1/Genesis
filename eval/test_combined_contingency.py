#!/usr/bin/env python3
"""
COMBINED CONTINGENCY SYSTEM BACKTEST
=====================================
Testing ALL exploits together on real M1 data.
"""

import numpy as np
import pandas as pd
import os

DATA_DIR = "data"

def load_candles(symbol):
    fpath = os.path.join(DATA_DIR, f"candles_{symbol}_1min.parquet")
    if not os.path.exists(fpath):
        return None
    return pd.read_parquet(fpath)

def find_spikes(df, threshold=3.0):
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    ranges = highs - lows
    n = len(ranges)
    median_ranges = np.full(n, 0.01)
    for i in range(20, n):
        median_ranges[i] = np.median(ranges[max(0,i-20):i])
    
    spikes = []
    for i in range(1, n-10):
        if median_ranges[i] > 0 and ranges[i] > threshold * median_ranges[i]:
            t = 'boom' if closes[i] > opens[i] else 'crash'
            spikes.append({
                'type': t, 'idx': i, 'range': ranges[i],
                'median': median_ranges[i], 'ratio': ranges[i]/median_ranges[i],
                'close': closes[i], 'open': opens[i]
            })
    return spikes

def backtest_system(df, spikes, config, name):
    """Backtest with a specific configuration"""
    closes = df['close'].values
    opens = df['open'].values
    n = len(closes)
    velocity = np.diff(closes)
    
    # Price normalization for price level filter
    price_min = np.min(closes)
    price_max = np.max(closes)
    normalized = (closes - price_min) / (price_max - price_min) if price_max > price_min else np.zeros(n)
    
    # Z-score for M1 extreme filter
    window = 20
    z_scores = np.full(n, 0.0)
    for i in range(window, n):
        mean = np.mean(closes[i-window:i])
        std = np.std(closes[i-window:i])
        if std > 0:
            z_scores[i] = (closes[i] - mean) / std
    
    wins = losses = 0
    total_pnl = 0
    trade_pnls = []
    max_consec = 0
    streak = 0
    skipped_reasons = {}
    
    for i, spike in enumerate(spikes):
        idx = spike['idx']
        if idx + config['hold'] + 1 >= n:
            continue
        
        skip = False
        skip_reason = ''
        
        # FILTER 1: Spike size filter
        if 'skip_ratio_min' in config and spike['ratio'] < config['skip_ratio_min']:
            skip = True
            skip_reason = 'size_small'
        if 'skip_ratio_max' in config and spike['ratio'] > config['skip_ratio_max']:
            skip = True
            skip_reason = 'size_large'
        
        # FILTER 2: Price level filter
        if 'skip_price_extreme' in config:
            extreme = config['skip_price_extreme']
            if normalized[idx] < extreme or normalized[idx] > (1 - extreme):
                skip = True
                skip_reason = 'price_extreme'
        
        # FILTER 3: M1 extreme filter
        if 'skip_z_extreme' in config and abs(z_scores[idx]) > config['skip_z_extreme']:
            skip = True
            skip_reason = 'z_extreme'
        
        # FILTER 4: Cluster avoidance
        if 'cluster_window' in config and i > 0:
            interval = spike['idx'] - spikes[i-1]['idx']
            if interval < config['cluster_window']:
                skip = True
                skip_reason = 'cluster'
        
        if skip:
            skipped_reasons[skip_reason] = skipped_reasons.get(skip_reason, 0) + 1
            continue
        
        # Entry
        entry = closes[idx + 1]
        
        # Exit with drift acceleration detection
        exit_price = None
        exit_reason = 'timeout'
        
        for bar in range(2, config['hold'] + 2):
            if idx + bar >= n:
                break
            
            # Drift acceleration check
            if 'accel_exit' in config and idx + bar >= 10:
                drift_10 = np.mean(velocity[idx+bar-10:idx+bar])
                drift_3 = np.mean(velocity[idx+bar-3:idx+bar])
                
                if abs(drift_10) > 0.001:
                    acceleration = abs(drift_3) / abs(drift_10)
                else:
                    acceleration = 0
                
                if acceleration > config['accel_exit']:
                    # Check if acceleration is in dangerous direction
                    if spike['type'] == 'crash' and drift_3 < 0:
                        exit_price = closes[idx+bar]
                        exit_reason = 'accel_exit'
                        break
                    elif spike['type'] == 'boom' and drift_3 > 0:
                        exit_price = closes[idx+bar]
                        exit_reason = 'accel_exit'
                        break
        
        if exit_price is None:
            exit_price = closes[idx + config['hold'] + 1]
        
        # PnL
        if spike['type'] == 'crash':
            pnl = exit_price - entry
        else:
            pnl = entry - exit_price
        
        trade_pnls.append(pnl)
        total_pnl += pnl
        
        if pnl > 0:
            wins += 1
            streak = 0
        else:
            losses += 1
            streak += 1
            max_consec = max(max_consec, streak)
    
    total = wins + losses
    skipped_total = sum(skipped_reasons.values())
    
    if total == 0:
        return None
    
    wr = wins / total * 100
    avg_win = np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0
    avg_loss = np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
    win_sum = sum(p for p in trade_pnls if p > 0)
    loss_sum = abs(sum(p for p in trade_pnls if p <= 0))
    pf = win_sum / loss_sum if loss_sum > 0 else 999
    
    return {
        'name': name,
        'trades': total,
        'skipped': skipped_total,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'pf': pf,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'total_pnl': total_pnl,
        'max_consec': max_consec,
        'skipped_reasons': skipped_reasons
    }

# ============================================================
# TEST CONFIGURATIONS
# ============================================================

configs = {
    'Baseline (no filters)': {
        'hold': 3,
    },
    
    'Price Level Filter only': {
        'hold': 3,
        'skip_price_extreme': 0.20,
    },
    
    'M1 Extreme Filter only': {
        'hold': 3,
        'skip_z_extreme': 3.0,
    },
    
    'Cluster Avoidance only': {
        'hold': 3,
        'cluster_window': 10,
    },
    
    'Drift Acceleration Exit only': {
        'hold': 5,
        'accel_exit': 5.0,
    },
    
    'Price + Cluster': {
        'hold': 3,
        'skip_price_extreme': 0.20,
        'cluster_window': 10,
    },
    
    'Price + Accel': {
        'hold': 5,
        'skip_price_extreme': 0.20,
        'accel_exit': 5.0,
    },
    
    'ALL LAYERS (CRASH)': {
        'hold': 7,
        'skip_price_extreme': 0.20,
        'skip_z_extreme': 3.0,
        'cluster_window': 10,
        'accel_exit': 5.0,
    },
    
    'ALL LAYERS (BOOM)': {
        'hold': 3,
        'skip_price_extreme': 0.15,
        'skip_z_extreme': 3.0,
        'cluster_window': 10,
        'accel_exit': 5.0,
    },
}

# Run backtests
print("=" * 100)
print("COMBINED CONTINGENCY SYSTEM BACKTEST")
print("=" * 100)

for sym in ['crash1000', 'boom1000']:
    df = load_candles(sym)
    if df is None:
        continue
    
    spikes = find_spikes(df)
    print(f"\n{'='*100}")
    print(f"{sym.upper()} - {len(df)} bars, {len(spikes)} spikes")
    print(f"{'='*100}")
    
    print(f"\n{'Config':<30} {'Trades':>7} {'Skip':>6} {'WR%':>7} {'PF':>7} {'PnL':>10} {'MaxCL':>6} {'AvgWin':>9} {'AvgLoss':>9}")
    print("-" * 100)
    
    for name, config in configs.items():
        # Use appropriate config for each symbol
        if 'CRASH' in name and sym != 'crash1000':
            continue
        if 'BOOM' in name and sym != 'boom1000':
            continue
        
        result = backtest_system(df, spikes, config, name)
        if result is None:
            print(f"  {name:<30} {'NO TRADES':>7}")
            continue
        
        print(f"  {name:<30} {result['trades']:>7} {result['skipped']:>6} {result['wr']:>6.1f}% {result['pf']:>7.2f} {result['total_pnl']:>10.2f} {result['max_consec']:>6} {result['avg_win']:>9.4f} {result['avg_loss']:>9.4f}")
        
        if result['skipped_reasons']:
            reasons = ', '.join(f"{k}:{v}" for k, v in sorted(result['skipped_reasons'].items()))
            print(f"    Skipped: {reasons}")

print("\n" + "=" * 100)
print("BACKTEST COMPLETE")
print("=" * 100)
