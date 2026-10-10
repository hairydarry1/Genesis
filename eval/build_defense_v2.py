"""
BUILD DEFENSE V2: Fixed PnL calculation
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

def test_defense_v2(symbol: str):
    """Test defense with CORRECT PnL calculation"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"DEFENSE V2: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # Base: Delay 1, no defense
        {'name': 'Base (D1)', 'delay': 1, 'max_hold': 5, 'defense': 'none'},
        # Time exit 3
        {'name': 'Time=3', 'delay': 1, 'max_hold': 3, 'defense': 'none'},
        # Time exit 2
        {'name': 'Time=2', 'delay': 1, 'max_hold': 2, 'defense': 'none'},
        # Skip if spike against us
        {'name': 'SkipAgainst', 'delay': 1, 'max_hold': 5, 'defense': 'skip_against'},
        # Exit on spike against
        {'name': 'ExitOnSpike', 'delay': 1, 'max_hold': 5, 'defense': 'exit_on_spike'},
        # Combined
        {'name': 'Combined', 'delay': 1, 'max_hold': 3, 'defense': 'exit_on_spike'},
    ]
    
    results = []
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        entry_bar = 0
        bars_held = 0
        last_spike_dir = 0
        
        for bar in range(50, len(prices)):
            current_price = prices[bar]
            is_spike = bar in spike_indices
            
            if is_spike:
                last_spike_dir = 1 if prices[bar + 1] > prices[bar] else -1 if bar + 1 < len(prices) else 0
            
            # Position management
            if in_position:
                bars_held += 1
                
                # Time exit
                if bars_held >= config['max_hold']:
                    pnl = (current_price - entry_price) * position_dir
                    trades.append({'pnl': pnl, 'reason': 'TIME'})
                    in_position = False
                    continue
                
                # Exit on spike against us
                if config['defense'] == 'exit_on_spike' and is_spike and bar + 1 < len(prices):
                    spike_move = prices[bar + 1] - prices[bar]
                    against = (position_dir == 1 and spike_move < 0) or (position_dir == -1 and spike_move > 0)
                    if against:
                        pnl = (current_price - entry_price) * position_dir
                        trades.append({'pnl': pnl, 'reason': 'SPIKE_EXIT'})
                        in_position = False
                        continue
            
            # Entry logic
            if not in_position and is_spike:
                # Skip if last spike was against us (we'd be fading the wrong way)
                if config['defense'] == 'skip_against' and last_spike_dir != 0:
                    # We want to fade spikes that go in the drift direction
                    # If last spike was against drift, skip
                    continue
                
                # Check delay
                bars_since_spike = 0
                for prev_bar in range(bar - 1, max(0, bar - 20), -1):
                    if prev_bar in spike_indices:
                        bars_since_spike = bar - prev_bar
                        break
                
                if bars_since_spike != config['delay']:
                    continue
                
                # ENTER (fade)
                spike_dir = 1 if prices[bar + 1] > prices[bar] else -1 if bar + 1 < len(prices) else 0
                position_dir = -spike_dir  # Fade
                entry_price = current_price
                entry_bar = bar
                bars_held = 0
                in_position = True
        
        # Close remaining
        if in_position:
            pnl = (prices[-1] - entry_price) * position_dir
            trades.append({'pnl': pnl, 'reason': 'END'})
        
        # Stats
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        worst_loss = min(t['pnl'] for t in trades) if trades else 0
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wr': wr,
            'pnl': total_pnl,
            'worst_loss': worst_loss
        })
    
    # Print
    print(f"\n{'Config':<15} {'Trades':<10} {'WR%':<10} {'PnL':<12} {'WorstLoss':<12}")
    print("-" * 65)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<10} {r['wr']:<10.1f} {r['pnl']:<12.2f} {r['worst_loss']:<12.2f}")
    
    if results:
        best = max(results, key=lambda x: x['pnl'])
        print(f"\nBest: {best['config']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f}, Worst={best['worst_loss']:.2f})")
    
    return results

def main():
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    for symbol in symbols:
        test_defense_v2(symbol)

if __name__ == '__main__':
    main()
