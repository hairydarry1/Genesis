"""
TEST TICK EXIT: Use tick size buildup as protection
If ticks get larger, exit before next spike
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

def test_tick_exit(symbol: str):
    """Test tick size as EXIT signal"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"TICK EXIT TESTING: {symbol}")
    print(f"{'='*70}")
    
    results = []
    
    # Test different tick exit thresholds
    for tick_threshold in [1.5, 1.8, 2.0, 2.5, 3.0]:
        trades = []
        
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            
            # Need enough room
            if spike_idx + 10 >= len(prices):
                continue
            
            # Determine spike direction
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            
            # Enter at delay 1 (fade)
            entry_bar = spike_idx + 1
            entry_price = prices[entry_bar]
            
            # Calculate median tick size from recent data
            recent_changes = np.abs(np.diff(prices[max(0, spike_idx-100):spike_idx]))
            if len(recent_changes) > 0:
                median_tick = np.median(recent_changes)
            else:
                median_tick = median
            
            # Hold position and check for tick exit
            exit_bar = None
            exit_price = None
            exit_reason = None
            
            for bar in range(entry_bar + 1, min(entry_bar + 10, len(prices))):
                # Check if tick size exceeds threshold
                tick_size = abs(prices[bar] - prices[bar - 1])
                
                if tick_size > median_tick * tick_threshold:
                    # Tick size spike detected - EXIT EARLY
                    exit_bar = bar
                    exit_price = prices[bar]
                    exit_reason = "TICK_EXIT"
                    break
                
                # Normal time exit at bar 5
                if bar >= entry_bar + 5:
                    exit_bar = bar
                    exit_price = prices[bar]
                    exit_reason = "TIME_EXIT"
                    break
            
            if exit_bar is None:
                exit_bar = entry_bar + 5
                exit_price = prices[min(exit_bar, len(prices) - 1)]
                exit_reason = "TIME_EXIT"
            
            # Calculate PnL (fade = opposite direction)
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': exit_reason})
        
        # Calculate stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        
        # Exit breakdown
        tick_exits = sum(1 for t in trades if t['reason'] == 'TICK_EXIT')
        time_exits = sum(1 for t in trades if t['reason'] == 'TIME_EXIT')
        
        results.append({
            'threshold': tick_threshold,
            'trades': total,
            'wr': wr,
            'pnl': total_pnl,
            'tick_exits': tick_exits,
            'time_exits': time_exits
        })
    
    # Print results
    print(f"\n{'Threshold':<12} {'Trades':<10} {'WR%':<10} {'PnL':<12} {'TickExit':<10} {'TimeExit':<10}")
    print("-" * 65)
    
    for r in results:
        print(f"{r['threshold']:<12} {r['trades']:<10} {r['wr']:<10.1f} {r['pnl']:<12.2f} {r['tick_exits']:<10} {r['time_exits']:<10}")
    
    # Find best
    if results:
        best = max(results, key=lambda x: x['pnl'])
        print(f"\nBest threshold: {best['threshold']}x (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f})")
    
    return results

def test_combined_protection(symbol: str):
    """Test COMBINED protection: tick exit + skip double spikes"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"COMBINED PROTECTION: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # Base: Delay 1, no protection
        {'name': 'Base', 'delay': 1, 'skip_double': False, 'tick_exit': False, 'tick_thresh': 2.0},
        # + Skip double spikes
        {'name': '+SkipDouble', 'delay': 1, 'skip_double': True, 'tick_exit': False, 'tick_thresh': 2.0},
        # + Tick exit 2.0x
        {'name': '+TickExit2.0', 'delay': 1, 'skip_double': False, 'tick_exit': True, 'tick_thresh': 2.0},
        # + Tick exit 1.5x (tighter)
        {'name': '+TickExit1.5', 'delay': 1, 'skip_double': False, 'tick_exit': True, 'tick_thresh': 1.5},
        # ALL: Skip double + Tick exit
        {'name': 'ALL', 'delay': 1, 'skip_double': True, 'tick_exit': True, 'tick_thresh': 2.0},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            
            if spike_idx + 10 >= len(prices):
                continue
            
            # Check skip double spike
            if config['skip_double'] and i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval <= 2:  # Skip if previous spike was 1-2 bars ago
                    continue
            
            # Determine spike direction
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            
            # Enter at delay 1
            entry_bar = spike_idx + 1
            entry_price = prices[entry_bar]
            
            # Median tick from recent data
            recent_changes = np.abs(np.diff(prices[max(0, spike_idx-100):spike_idx]))
            median_tick = np.median(recent_changes) if len(recent_changes) > 0 else median
            
            # Hold and check exits
            exit_bar = None
            exit_price = None
            exit_reason = None
            
            for bar in range(entry_bar + 1, min(entry_bar + 10, len(prices))):
                # Tick exit
                if config['tick_exit']:
                    tick_size = abs(prices[bar] - prices[bar - 1])
                    if tick_size > median_tick * config['tick_thresh']:
                        exit_bar = bar
                        exit_price = prices[bar]
                        exit_reason = "TICK_EXIT"
                        break
                
                # Time exit
                if bar >= entry_bar + 5:
                    exit_bar = bar
                    exit_price = prices[bar]
                    exit_reason = "TIME_EXIT"
                    break
            
            if exit_bar is None:
                exit_bar = entry_bar + 5
                exit_price = prices[min(exit_bar, len(prices) - 1)]
                exit_reason = "TIME_EXIT"
            
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': exit_reason})
        
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
    
    # Print
    print(f"\n{'Config':<20} {'Trades':<10} {'WR%':<10} {'PnL':<12}")
    print("-" * 55)
    
    for r in results:
        print(f"{r['config']:<20} {r['trades']:<10} {r['wr']:<10.1f} {r['pnl']:<12.2f}")
    
    if results:
        best = max(results, key=lambda x: x['pnl'])
        print(f"\nBest: {best['config']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f})")
    
    return results

def main():
    """Run all tests"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    for symbol in symbols:
        test_tick_exit(symbol)
        test_combined_protection(symbol)

if __name__ == '__main__':
    main()
