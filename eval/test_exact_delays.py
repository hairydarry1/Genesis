"""
TEST EXACT DELAYS: Use the proven logic from test_delays.py
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

def test_exact_delays(symbol: str):
    """Test exact delays with PROVEN logic"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"EXACT DELAYS: {symbol}")
    print(f"{'='*70}")
    
    # Test delay 1 with different exit strategies
    for delay in [1]:
        print(f"\n--- Delay = {delay} ---")
        
        # No defense
        trades = []
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            if spike_idx + delay + 6 >= len(prices):
                continue
            if next_spike - spike_idx < delay + 3:
                continue
            
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            exit_bar = entry_bar + 5
            exit_price = prices[exit_bar]
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': 'NO_DEFENSE'})
        
        if trades:
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / len(trades) * 100
            total_pnl = sum(t['pnl'] for t in trades)
            worst = min(t['pnl'] for t in trades)
            print(f"  No Defense: {len(trades)} trades, WR={wr:.1f}%, PnL={total_pnl:.2f}, Worst={worst:.2f}")
        
        # With time exit 3
        trades = []
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            if spike_idx + delay + 4 >= len(prices):
                continue
            if next_spike - spike_idx < delay + 3:
                continue
            
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            exit_bar = entry_bar + 3  # Exit after 3 bars
            exit_price = prices[exit_bar]
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': 'TIME3'})
        
        if trades:
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / len(trades) * 100
            total_pnl = sum(t['pnl'] for t in trades)
            worst = min(t['pnl'] for t in trades)
            print(f"  Time Exit 3: {len(trades)} trades, WR={wr:.1f}%, PnL={total_pnl:.2f}, Worst={worst:.2f}")
        
        # With skip double (interval=1)
        trades = []
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            if spike_idx + delay + 6 >= len(prices):
                continue
            if next_spike - spike_idx < delay + 3:
                continue
            
            # Skip if previous interval was 1
            if i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval == 1:
                    continue
            
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            exit_bar = entry_bar + 5
            exit_price = prices[exit_bar]
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': 'SKIP_DOUBLE'})
        
        if trades:
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / len(trades) * 100
            total_pnl = sum(t['pnl'] for t in trades)
            worst = min(t['pnl'] for t in trades)
            print(f"  Skip Double: {len(trades)} trades, WR={wr:.1f}%, PnL={total_pnl:.2f}, Worst={worst:.2f}")
        
        # With skip triple (interval<=2)
        trades = []
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            if spike_idx + delay + 6 >= len(prices):
                continue
            if next_spike - spike_idx < delay + 3:
                continue
            
            # Skip if previous interval was 1 or 2
            if i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval <= 2:
                    continue
            
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            exit_bar = entry_bar + 5
            exit_price = prices[exit_bar]
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': 'SKIP_TRIPLE'})
        
        if trades:
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / len(trades) * 100
            total_pnl = sum(t['pnl'] for t in trades)
            worst = min(t['pnl'] for t in trades)
            print(f"  Skip Triple: {len(trades)} trades, WR={wr:.1f}%, PnL={total_pnl:.2f}, Worst={worst:.2f}")
        
        # With skip cluster (interval<=3)
        trades = []
        for i, spike_idx in enumerate(spike_indices[:-1]):
            next_spike = spike_indices[i + 1]
            if spike_idx + delay + 6 >= len(prices):
                continue
            if next_spike - spike_idx < delay + 3:
                continue
            
            # Skip if previous interval was 1, 2, or 3
            if i > 0:
                prev_interval = spike_idx - spike_indices[i - 1]
                if prev_interval <= 3:
                    continue
            
            spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
            entry_bar = spike_idx + delay
            entry_price = prices[entry_bar]
            exit_bar = entry_bar + 5
            exit_price = prices[exit_bar]
            pnl = -spike_dir * (exit_price - entry_price)
            trades.append({'pnl': pnl, 'reason': 'SKIP_CLUSTER'})
        
        if trades:
            wins = sum(1 for t in trades if t['pnl'] > 0)
            wr = wins / len(trades) * 100
            total_pnl = sum(t['pnl'] for t in trades)
            worst = min(t['pnl'] for t in trades)
            print(f"  Skip Cluster: {len(trades)} trades, WR={wr:.1f}%, PnL={total_pnl:.2f}, Worst={worst:.2f}")

def main():
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    for symbol in symbols:
        test_exact_delays(symbol)

if __name__ == '__main__':
    main()
