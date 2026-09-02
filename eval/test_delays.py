"""
TEST DELAYS: Find optimal entry timing
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

def test_delays(symbol: str):
    """Test different entry delays"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"TESTING DELAYS: {symbol}")
    print(f"{'='*70}")
    
    results = []
    
    for delay in [0, 1, 2, 3, 4, 5, 6, 7, 8, 10]:
        trades = []
        
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            
            # Need enough room
            if spike_idx + delay + 6 >= len(prices):
                continue
            
            # Check gap to next spike
            if next_spike - spike_idx < delay + 3:
                continue
            
            # Determine spike direction
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            
            # Enter after delay (fade = opposite direction)
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            
            # Exit after 5 bars
            exit_bar = entry_bar + 5
            exit_price = prices[exit_bar]
            
            # PnL (fade = opposite direction)
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append(pnl)
        
        if trades:
            wins = sum(1 for t in trades if t > 0)
            losses = sum(1 for t in trades if t <= 0)
            total = len(trades)
            wr = wins / total * 100
            avg_win = np.mean([t for t in trades if t > 0]) if wins > 0 else 0
            avg_loss = np.mean([t for t in trades if t <= 0]) if losses > 0 else 0
            pf = abs(avg_win * wins / (avg_loss * losses)) if losses > 0 and avg_loss != 0 else float('inf')
            total_pnl = sum(trades)
            
            results.append({
                'delay': delay,
                'trades': total,
                'wr': wr,
                'pf': pf,
                'pnl': total_pnl,
                'avg_win': avg_win,
                'avg_loss': avg_loss
            })
    
    # Print results
    print(f"\n{'Delay':<8} {'Trades':<10} {'WR%':<10} {'PF':<10} {'PnL':<12} {'AvgWin':<10} {'AvgLoss':<10}")
    print("-" * 75)
    
    for r in results:
        print(f"{r['delay']:<8} {r['trades']:<10} {r['wr']:<10.1f} {r['pf']:<10.2f} {r['pnl']:<12.2f} {r['avg_win']:<10.4f} {r['avg_loss']:<10.4f}")
    
    # Find best
    if results:
        best = max(results, key=lambda x: x['pnl'])
        print(f"\nBest delay: {best['delay']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f})")
    
    return results

def main():
    """Test on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = test_delays(symbol)
        all_results[symbol] = results
    
    # Overall summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*70}")
    
    print(f"\nBest delay per symbol:")
    for symbol in symbols:
        if all_results[symbol]:
            best = max(all_results[symbol], key=lambda x: x['pnl'])
            print(f"  {symbol}: Delay={best['delay']}, WR={best['wr']:.1f}%, PnL={best['pnl']:.2f}")

if __name__ == '__main__':
    main()
