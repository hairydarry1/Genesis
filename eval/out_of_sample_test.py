#!/usr/bin/env python3
"""
OUT-OF-SAMPLE VALIDATION
=========================
Split data into 4 quarters. Train on Q1-Q3, test on Q4.
If strategy works on unseen data, it's real.
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

def backtest_quarter(df_spikes, closes, velocity, normalized, z_scores, hold, filters):
    """Backtest on a specific quarter"""
    n = len(closes)
    wins = losses = 0
    total_pnl = 0
    trade_pnls = []
    max_consec = 0
    streak = 0
    
    for i, spike in enumerate(df_spikes):
        idx = spike['idx']
        if idx + hold + 1 >= n:
            continue
        
        skip = False
        
        # Filters
        if 'skip_price_extreme' in filters:
            extreme = filters['skip_price_extreme']
            if normalized[idx] < extreme or normalized[idx] > (1 - extreme):
                skip = True
        
        if 'skip_z_extreme' in filters and abs(z_scores[idx]) > filters['skip_z_extreme']:
            skip = True
        
        if 'cluster_window' in filters and i > 0:
            interval = spike['idx'] - df_spikes[i-1]['idx']
            if interval < filters['cluster_window']:
                skip = True
        
        if skip:
            continue
        
        entry = closes[idx + 1]
        
        # Exit with accel detection
        exit_price = None
        if 'accel_exit' in filters:
            for bar in range(2, hold + 2):
                if idx + bar >= n:
                    break
                if idx + bar >= 10:
                    drift_10 = np.mean(velocity[idx+bar-10:idx+bar])
                    drift_3 = np.mean(velocity[idx+bar-3:idx+bar])
                    if abs(drift_10) > 0.001:
                        accel = abs(drift_3) / abs(drift_10)
                    else:
                        accel = 0
                    if accel > filters['accel_exit']:
                        if spike['type'] == 'crash' and drift_3 < 0:
                            exit_price = closes[idx+bar]
                            break
                        elif spike['type'] == 'boom' and drift_3 > 0:
                            exit_price = closes[idx+bar]
                            break
        
        if exit_price is None:
            exit_price = closes[idx + hold + 1]
        
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
    if total == 0:
        return None
    
    wr = wins / total * 100
    win_sum = sum(p for p in trade_pnls if p > 0)
    loss_sum = abs(sum(p for p in trade_pnls if p <= 0))
    pf = win_sum / loss_sum if loss_sum > 0 else 999
    
    return {
        'trades': total,
        'wr': wr,
        'pf': pf,
        'pnl': total_pnl,
        'max_consec': max_consec,
        'avg_win': np.mean([p for p in trade_pnls if p > 0]) if wins > 0 else 0,
        'avg_loss': np.mean([p for p in trade_pnls if p <= 0]) if losses > 0 else 0
    }

# Configurations to test
configs = {
    'Baseline': {'hold': 3},
    'Price Filter': {'hold': 3, 'skip_price_extreme': 0.20},
    'Cluster': {'hold': 3, 'cluster_window': 10},
    'Price+Cluster': {'hold': 3, 'skip_price_extreme': 0.20, 'cluster_window': 10},
    'Accel Exit': {'hold': 5, 'accel_exit': 5.0},
    'ALL LAYERS': {'hold': 5, 'skip_price_extreme': 0.20, 'cluster_window': 10, 'accel_exit': 5.0, 'skip_z_extreme': 3.0},
}

print("=" * 100)
print("OUT-OF-SAMPLE VALIDATION")
print("Training: Q1-Q3 | Testing: Q4 (unseen data)")
print("=" * 100)

for sym in ['crash1000', 'boom1000']:
    df = load_candles(sym)
    if df is None:
        continue
    
    closes = df['close'].values
    n = len(closes)
    velocity = np.diff(closes)
    
    # Price normalization
    price_min = np.min(closes)
    price_max = np.max(closes)
    normalized = (closes - price_min) / (price_max - price_min) if price_max > price_min else np.zeros(n)
    
    # Z-scores
    window = 20
    z_scores = np.full(n, 0.0)
    for i in range(window, n):
        mean = np.mean(closes[i-window:i])
        std = np.std(closes[i-window:i])
        if std > 0:
            z_scores[i] = (closes[i] - mean) / std
    
    # Split into 4 quarters
    q_size = n // 4
    quarters = [
        (0, q_size),
        (q_size, 2*q_size),
        (2*q_size, 3*q_size),
        (3*q_size, n)
    ]
    
    all_spikes = find_spikes(df)
    
    # Split spikes by quarter
    quarter_spikes = []
    for q_start, q_end in quarters:
        q_spikes = [s for s in all_spikes if q_start <= s['idx'] < q_end]
        quarter_spikes.append(q_spikes)
    
    print(f"\n{'='*100}")
    print(f"{sym.upper()} - {n} bars, {len(all_spikes)} spikes")
    print(f"Q1: {len(quarter_spikes[0])} spikes | Q2: {len(quarter_spikes[1])} spikes | Q3: {len(quarter_spikes[2])} spikes | Q4: {len(quarter_spikes[3])} spikes")
    print(f"{'='*100}")
    
    for config_name, filters in configs.items():
        hold = filters['hold']
        
        # Train on Q1-Q3
        train_spikes = quarter_spikes[0] + quarter_spikes[1] + quarter_spikes[2]
        
        # Create a mock dataframe for training
        train_df = df.iloc[quarters[0][0]:quarters[2][1]]
        train_closes = closes[quarters[0][0]:quarters[2][1]]
        train_velocity = velocity[quarters[0][0]:quarters[2][1]]
        train_normalized = normalized[quarters[0][0]:quarters[2][1]]
        train_z = z_scores[quarters[0][0]:quarters[2][1]]
        
        # Adjust spike indices for training data
        train_spikes_adj = []
        for s in train_spikes:
            adj = s.copy()
            adj['idx'] = s['idx'] - quarters[0][0]
            train_spikes_adj.append(adj)
        
        train_result = backtest_quarter(train_spikes_adj, train_closes, train_velocity, train_normalized, train_z, hold, filters)
        
        # Test on Q4 (unseen)
        test_spikes = quarter_spikes[3]
        test_closes = closes[quarters[3][0]:]
        test_velocity = velocity[quarters[3][0]:]
        test_normalized = normalized[quarters[3][0]:]
        test_z = z_scores[quarters[3][0]:]
        
        # Adjust spike indices for test data
        test_spikes_adj = []
        for s in test_spikes:
            adj = s.copy()
            adj['idx'] = s['idx'] - quarters[3][0]
            test_spikes_adj.append(adj)
        
        test_result = backtest_quarter(test_spikes_adj, test_closes, test_velocity, test_normalized, test_z, hold, filters)
        
        if train_result and test_result:
            # Check if test results are similar to train
            wr_diff = abs(train_result['wr'] - test_result['wr'])
            pnl_per_trade_train = train_result['pnl'] / train_result['trades']
            pnl_per_trade_test = test_result['pnl'] / test_result['trades']
            
            overfit_risk = 'LOW' if wr_diff < 3 else ('MEDIUM' if wr_diff < 6 else 'HIGH')
            
            print(f"\n  {config_name}:")
            print(f"    TRAIN (Q1-Q3): {train_result['trades']}T WR={train_result['wr']:.1f}% PF={train_result['pf']:.2f} PnL=${train_result['pnl']:.2f} MaxCL={train_result['max_consec']}")
            print(f"    TEST  (Q4):    {test_result['trades']}T WR={test_result['wr']:.1f}% PF={test_result['pf']:.2f} PnL=${test_result['pnl']:.2f} MaxCL={test_result['max_consec']}")
            print(f"    WR Delta: {wr_diff:.1f}% | Overfit Risk: {overfit_risk}")
            print(f"    PnL/Trade: Train=${pnl_per_trade_train:.4f} Test=${pnl_per_trade_test:.4f}")

print("\n" + "=" * 100)
print("VALIDATION COMPLETE")
print("=" * 100)
