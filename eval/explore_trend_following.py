"""
EXPLORE: Trend Following Approach
Since spikes continue 80% of the time, what if we TRADE WITH them?

Hypothesis: Enter in spike direction, ride the continuation
"""

import numpy as np
import pandas as pd
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

def load_data(symbol: str) -> pd.DataFrame:
    symbol_lower = symbol.lower()
    filename = f'data/candles_{symbol_lower}_1min.parquet'
    if Path(filename).exists():
        return pd.read_parquet(filename)
    return None

def detect_spikes(prices: np.ndarray, threshold_multiplier: float = 3.0):
    changes = np.abs(np.diff(prices))
    positive_changes = changes[changes > 0]
    median = np.median(positive_changes)
    threshold = median * threshold_multiplier
    spike_mask = changes > threshold
    return np.where(spike_mask)[0], threshold, median

def calculate_cold_hours(spike_indices: np.ndarray, n_hours: int = 24):
    hours = spike_indices // 60
    hour_counts = np.zeros(n_hours)
    for h in hours:
        hour_counts[h % n_hours] += 1
    cold_threshold = np.percentile(hour_counts, 25)
    cold_hours = np.where(hour_counts <= cold_threshold)[0]
    return cold_hours

def backtest_trend_following(symbol: str):
    """Test trend following: enter IN spike direction"""
    
    df = load_data(symbol)
    if df is None:
        print(f"No data for {symbol}")
        return
    
    prices = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours = calculate_cold_hours(spike_indices)
    
    print(f"\n{'='*70}")
    print(f"TREND FOLLOWING: {symbol}")
    print(f"{'='*70}")
    print(f"Spikes: {len(spike_indices)}")
    
    configs = [
        # No delay - enter immediately after spike
        {'name': 'Immediate', 'delay': 0, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': False},
        # 1 bar delay
        {'name': '1 Bar Delay', 'delay': 1, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': False},
        # 2 bar delay
        {'name': '2 Bar Delay', 'delay': 2, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': False},
        # COLD hours only
        {'name': 'COLD Only', 'delay': 1, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': True},
        # Wider TP
        {'name': 'Wide TP', 'delay': 1, 'tp_mult': 2.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': False},
        # Tighter SL
        {'name': 'Tight SL', 'delay': 1, 'tp_mult': 1.0, 'sl_mult': 0.3, 'max_hold': 5, 'cold': False},
        # Longer hold
        {'name': 'Long Hold', 'delay': 1, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 10, 'cold': False},
        # COLD + Wide TP
        {'name': 'COLD+WideTP', 'delay': 1, 'tp_mult': 2.0, 'sl_mult': 0.5, 'max_hold': 5, 'cold': True},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        entry_bar = 0
        bars_held = 0
        tp_price = 0
        sl_price = 0
        
        for bar in range(50, len(prices)):
            current_price = prices[bar]
            current_high = highs[bar]
            current_low = lows[bar]
            current_hour = bar // 60 % 24
            
            # Position management
            if in_position:
                bars_held += 1
                
                # Check TP/SL
                if position_dir == 1:  # Long
                    if current_high >= tp_price:
                        pnl = tp_price - entry_price
                        trades.append({'pnl': pnl, 'reason': 'TP', 'bars': bars_held})
                        in_position = False
                        continue
                    if current_low <= sl_price:
                        pnl = sl_price - entry_price
                        trades.append({'pnl': pnl, 'reason': 'SL', 'bars': bars_held})
                        in_position = False
                        continue
                else:  # Short
                    if current_low <= tp_price:
                        pnl = entry_price - tp_price
                        trades.append({'pnl': pnl, 'reason': 'TP', 'bars': bars_held})
                        in_position = False
                        continue
                    if current_high >= sl_price:
                        pnl = entry_price - sl_price
                        trades.append({'pnl': pnl, 'reason': 'SL', 'bars': bars_held})
                        in_position = False
                        continue
                
                # Timeout
                if bars_held >= config['max_hold']:
                    pnl = (current_price - entry_price) * position_dir
                    trades.append({'pnl': pnl, 'reason': 'TIMEOUT', 'bars': bars_held})
                    in_position = False
                    continue
            
            # Entry logic
            if not in_position and bar in spike_indices:
                # Check delay
                spike_idx = np.where(spike_indices == bar)[0][0]
                if spike_idx < len(spike_indices) - 1:
                    next_spike = spike_indices[spike_idx + 1]
                    bars_since_spike = bar - (spike_indices[spike_idx - 1] if spike_idx > 0 else 0)
                else:
                    bars_since_spike = 100
                
                if bars_since_spike != config['delay'] + 1:
                    continue
                
                # COLD hours filter
                if config['cold'] and current_hour not in cold_hours:
                    continue
                
                # Determine spike direction
                if bar + 1 < len(prices):
                    spike_direction = 1 if prices[bar + 1] > prices[bar] else -1
                else:
                    continue
                
                # ENTER IN SPIKE DIRECTION (trend following)
                position_dir = spike_direction
                entry_price = current_price
                entry_bar = bar
                bars_held = 0
                
                # Set TP/SL
                tp_distance = median * config['tp_mult']
                sl_distance = median * config['sl_mult']
                
                if position_dir == 1:  # Long
                    tp_price = entry_price + tp_distance
                    sl_price = entry_price - sl_distance
                else:  # Short
                    tp_price = entry_price - tp_distance
                    sl_price = entry_price + sl_distance
        
        # Calculate stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        avg_win = np.mean([t['pnl'] for t in trades if t['pnl'] > 0]) if wins > 0 else 0
        avg_loss = np.mean([t['pnl'] for t in trades if t['pnl'] <= 0]) if losses > 0 else 0
        pf = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else float('inf')
        total_pnl = sum(t['pnl'] for t in trades)
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wins': wins,
            'losses': losses,
            'wr': wr,
            'pf': pf,
            'pnl': total_pnl,
            'avg_win': avg_win,
            'avg_loss': avg_loss
        })
    
    # Print results
    print(f"\n{'Config':<15} {'Trades':<8} {'WR%':<8} {'PF':<8} {'PnL':<10} {'AvgWin':<10} {'AvgLoss':<10}")
    print("-" * 75)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<8} {r['wr']:<8.1f} {r['pf']:<8.2f} {r['pnl']:<10.2f} {r['avg_win']:<10.4f} {r['avg_loss']:<10.4f}")
    
    # Find best
    if results:
        best = max(results, key=lambda x: x['pnl'])
        print(f"\nBest: {best['config']} (PnL={best['pnl']:.2f}, WR={best['wr']:.1f}%)")
    
    return results

def backtest_momentum(symbol: str):
    """Test momentum: enter when price shows momentum"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    
    print(f"\n{'='*70}")
    print(f"MOMENTUM: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # 3-bar momentum
        {'name': '3-Bar Mom', 'lookback': 3, 'threshold': 0.5, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
        # 5-bar momentum
        {'name': '5-Bar Mom', 'lookback': 5, 'threshold': 0.5, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
        # 10-bar momentum
        {'name': '10-Bar Mom', 'lookback': 10, 'threshold': 0.5, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
        # Strong momentum
        {'name': 'Strong Mom', 'lookback': 5, 'threshold': 1.0, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        bars_held = 0
        tp_price = 0
        sl_price = 0
        
        median_change = np.median(np.abs(np.diff(prices[-1000:])))
        
        for bar in range(config['lookback'] + 50, len(prices)):
            current_price = prices[bar]
            
            # Position management
            if in_position:
                bars_held += 1
                
                if position_dir == 1:
                    if current_price >= tp_price:
                        trades.append({'pnl': tp_price - entry_price, 'reason': 'TP'})
                        in_position = False
                        continue
                    if current_price <= sl_price:
                        trades.append({'pnl': sl_price - entry_price, 'reason': 'SL'})
                        in_position = False
                        continue
                else:
                    if current_price <= tp_price:
                        trades.append({'pnl': entry_price - tp_price, 'reason': 'TP'})
                        in_position = False
                        continue
                    if current_price >= sl_price:
                        trades.append({'pnl': entry_price - sl_price, 'reason': 'SL'})
                        in_position = False
                        continue
                
                if bars_held >= config['max_hold']:
                    pnl = (current_price - entry_price) * position_dir
                    trades.append({'pnl': pnl, 'reason': 'TIMEOUT'})
                    in_position = False
                    continue
            
            # Momentum entry
            if not in_position:
                momentum = prices[bar] - prices[bar - config['lookback']]
                
                if abs(momentum) > median_change * config['threshold']:
                    # Enter in momentum direction
                    position_dir = 1 if momentum > 0 else -1
                    entry_price = current_price
                    bars_held = 0
                    
                    tp_distance = median_change * config['tp_mult']
                    sl_distance = median_change * config['sl_mult']
                    
                    if position_dir == 1:
                        tp_price = entry_price + tp_distance
                        sl_price = entry_price - sl_distance
                    else:
                        tp_price = entry_price - tp_distance
                        sl_price = entry_price + sl_distance
        
        # Stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wr': wr,
            'pnl': total_pnl
        })
    
    print(f"\n{'Config':<15} {'Trades':<8} {'WR%':<8} {'PnL':<10}")
    print("-" * 45)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<8} {r['wr']:<8.1f} {r['pnl']:<10.2f}")
    
    return results

def backtest_breakout(symbol: str):
    """Test breakout: enter when price breaks out of range"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    print(f"\n{'='*70}")
    print(f"BREAKOUT: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # 20-bar breakout
        {'name': '20-Bar BO', 'lookback': 20, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
        # 50-bar breakout
        {'name': '50-Bar BO', 'lookback': 50, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
        # 100-bar breakout
        {'name': '100-Bar BO', 'lookback': 100, 'tp_mult': 1.0, 'sl_mult': 0.5, 'max_hold': 5},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        bars_held = 0
        tp_price = 0
        sl_price = 0
        
        median_change = np.median(np.abs(np.diff(prices[-1000:])))
        
        for bar in range(config['lookback'] + 50, len(prices)):
            current_price = prices[bar]
            current_high = highs[bar]
            current_low = lows[bar]
            
            # Position management
            if in_position:
                bars_held += 1
                
                if position_dir == 1:
                    if current_high >= tp_price:
                        trades.append({'pnl': tp_price - entry_price, 'reason': 'TP'})
                        in_position = False
                        continue
                    if current_low <= sl_price:
                        trades.append({'pnl': sl_price - entry_price, 'reason': 'SL'})
                        in_position = False
                        continue
                else:
                    if current_low <= tp_price:
                        trades.append({'pnl': entry_price - tp_price, 'reason': 'TP'})
                        in_position = False
                        continue
                    if current_high >= sl_price:
                        trades.append({'pnl': entry_price - sl_price, 'reason': 'SL'})
                        in_position = False
                        continue
                
                if bars_held >= config['max_hold']:
                    pnl = (current_price - entry_price) * position_dir
                    trades.append({'pnl': pnl, 'reason': 'TIMEOUT'})
                    in_position = False
                    continue
            
            # Breakout entry
            if not in_position:
                lookback_high = np.max(highs[bar - config['lookback']:bar])
                lookback_low = np.min(lows[bar - config['lookback']:bar])
                
                # Breakout UP
                if current_high > lookback_high:
                    position_dir = 1
                    entry_price = current_high
                    bars_held = 0
                    
                    tp_distance = median_change * config['tp_mult']
                    sl_distance = median_change * config['sl_mult']
                    tp_price = entry_price + tp_distance
                    sl_price = entry_price - sl_distance
                
                # Breakout DOWN
                elif current_low < lookback_low:
                    position_dir = -1
                    entry_price = current_low
                    bars_held = 0
                    
                    tp_distance = median_change * config['tp_mult']
                    sl_distance = median_change * config['sl_mult']
                    tp_price = entry_price - tp_distance
                    sl_price = entry_price + sl_distance
        
        # Stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wr': wr,
            'pnl': total_pnl
        })
    
    print(f"\n{'Config':<15} {'Trades':<8} {'WR%':<8} {'PnL':<10}")
    print("-" * 45)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<8} {r['wr']:<8.1f} {r['pnl']:<10.2f}")
    
    return results

def main():
    """Run all explorations."""
    
    symbol = 'CRASH500'
    
    print(f"\n{'#'*70}")
    print(f"# EXPLORING DIFFERENT APPROACHES")
    print(f"# Symbol: {symbol}")
    print(f"{'#'*70}")
    
    # Trend following
    tf_results = backtest_trend_following(symbol)
    
    # Momentum
    mom_results = backtest_momentum(symbol)
    
    # Breakout
    bo_results = backtest_breakout(symbol)
    
    # Summary
    print(f"\n{'='*70}")
    print(f"SUMMARY: ALL APPROACHES")
    print(f"{'='*70}")
    
    all_results = []
    
    if tf_results:
        for r in tf_results:
            all_results.append(('TF', r['config'], r['pnl'], r['wr']))
    
    if mom_results:
        for r in mom_results:
            all_results.append(('MOM', r['config'], r['pnl'], r['wr']))
    
    if bo_results:
        for r in bo_results:
            all_results.append(('BO', r['config'], r['pnl'], r['wr']))
    
    if all_results:
        all_results.sort(key=lambda x: x[2], reverse=True)
        
        print(f"\n{'Type':<6} {'Config':<15} {'PnL':<10} {'WR%':<8}")
        print("-" * 45)
        
        for r in all_results[:10]:
            print(f"{r[0]:<6} {r[1]:<15} {r[2]:<10.2f} {r[3]:<8.1f}")
        
        print(f"\nBest overall: {all_results[0][0]}-{all_results[0][1]} (PnL={all_results[0][2]:.2f}, WR={all_results[0][3]:.1f}%)")

if __name__ == '__main__':
    main()
