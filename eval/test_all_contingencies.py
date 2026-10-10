"""
TEST ALL CONTINGENCIES: Combined defense system
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

def test_all_contingencies(symbol: str):
    """Test ALL contingencies combined"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours = calculate_cold_hours(spike_indices)
    
    print(f"\n{'='*70}")
    print(f"ALL CONTINGENCIES: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # Base: No defense
        {'name': 'Base', 'delay': 1, 'exit_after': 5, 'skip_double': False, 'skip_triple': False, 'skip_cluster': False, 'cold_hours': False, 'safety_exit': False},
        # Time Exit 3 only
        {'name': 'Time3', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'skip_triple': False, 'skip_cluster': False, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Skip Double
        {'name': 'Time3+SkipD', 'delay': 1, 'exit_after': 3, 'skip_double': True, 'skip_triple': False, 'skip_cluster': False, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Skip Triple
        {'name': 'Time3+SkipT', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'skip_triple': True, 'skip_cluster': False, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Skip Cluster
        {'name': 'Time3+SkipC', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'skip_triple': False, 'skip_cluster': True, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Cold Hours
        {'name': 'Time3+Cold', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'skip_triple': False, 'skip_cluster': False, 'cold_hours': True, 'safety_exit': False},
        # Time3 + Safety Exit
        {'name': 'Time3+Safety', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'skip_triple': False, 'skip_cluster': False, 'cold_hours': False, 'safety_exit': True},
        # Time3 + ALL defenses
        {'name': 'Time3+ALL', 'delay': 1, 'exit_after': 3, 'skip_double': True, 'skip_triple': True, 'skip_cluster': True, 'cold_hours': True, 'safety_exit': True},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            
            if spike_idx + config['delay'] + config['exit_after'] + 1 >= len(prices):
                continue
            
            if next_spike - spike_idx < config['delay'] + 2:
                continue
            
            # CONTINGENCY 1: Skip double spike
            if config['skip_double'] and i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval == 1:
                    continue
            
            # CONTINGENCY 2: Skip triple spike
            if config['skip_triple'] and i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval <= 2:
                    continue
            
            # CONTINGENCY 3: Skip cluster
            if config['skip_cluster'] and i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval <= 3:
                    continue
            
            # CONTINGENCY 4: COLD hours
            if config['cold_hours']:
                hour = spike_idx // 60 % 24
                if hour not in cold_hours:
                    continue
            
            # Determine spike direction
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            
            # Enter at delay
            entry_bar = spike_idx + config['delay']
            entry_price = prices[entry_bar]
            
            # CONTINGENCY 5: Safety exit (check for spike against us)
            exit_bar = entry_bar + config['exit_after']
            exit_price = prices[exit_bar]
            exit_reason = 'TIME'
            
            if config['safety_exit']:
                # Check if spike happened against us during hold
                for bar in range(entry_bar + 1, min(exit_bar + 1, len(prices))):
                    if bar in spike_indices:
                        spike_move = prices[bar + 1] - prices[bar] if bar + 1 < len(prices) else 0
                        against = (spike_dir == 1 and spike_move < 0) or (spike_dir == -1 and spike_move > 0)
                        if against:
                            exit_bar = bar
                            exit_price = prices[bar]
                            exit_reason = 'SAFETY'
                            break
            
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': exit_reason})
        
        # Stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        worst = min(t['pnl'] for t in trades) if trades else 0
        avg_win = np.mean([t['pnl'] for t in trades if t['pnl'] > 0]) if wins > 0 else 0
        avg_loss = np.mean([t['pnl'] for t in trades if t['pnl'] <= 0]) if losses > 0 else 0
        
        # Exit breakdown
        time_exits = sum(1 for t in trades if t['reason'] == 'TIME')
        safety_exits = sum(1 for t in trades if t['reason'] == 'SAFETY')
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wr': wr,
            'pnl': total_pnl,
            'worst': worst,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'time_exits': time_exits,
            'safety_exits': safety_exits
        })
    
    # Print results
    print(f"\n{'Config':<15} {'Trades':<8} {'WR%':<8} {'PnL':<10} {'Worst':<10} {'AvgWin':<10} {'AvgLoss':<10}")
    print("-" * 85)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<8} {r['wr']:<8.1f} {r['pnl']:<10.2f} {r['worst']:<10.2f} {r['avg_win']:<10.4f} {r['avg_loss']:<10.4f}")
    
    # Find best (highest PnL with acceptable worst loss)
    if results:
        # Best PnL
        best_pnl = max(results, key=lambda x: x['pnl'])
        print(f"\nBest PnL: {best_pnl['config']} (WR={best_pnl['wr']:.1f}%, PnL={best_pnl['pnl']:.2f}, Worst={best_pnl['worst']:.2f})")
        
        # Best worst loss (with positive PnL)
        profitable = [r for r in results if r['pnl'] > 0]
        if profitable:
            best_worst = min(profitable, key=lambda x: abs(x['worst']))
            print(f"Best Defense: {best_worst['config']} (WR={best_worst['wr']:.1f}%, PnL={best_worst['pnl']:.2f}, Worst={best_worst['worst']:.2f})")
    
    return results

def main():
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = test_all_contingencies(symbol)
        all_results[symbol] = results
    
    # Overall summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*70}")
    
    print(f"\nBest defense per symbol:")
    for symbol in symbols:
        if all_results[symbol]:
            profitable = [r for r in all_results[symbol] if r['pnl'] > 0]
            if profitable:
                best = min(profitable, key=lambda x: abs(x['worst']))
                print(f"  {symbol}: {best['config']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f}, Worst={best['worst']:.2f})")

if __name__ == '__main__':
    main()
