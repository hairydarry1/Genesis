"""
BACKTEST: Relaxed Contingency System
Find the right balance between filtering and trading
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
    return cold_hours, hour_counts

def calculate_spike_sizes(prices: np.ndarray, spike_indices: np.ndarray):
    spike_sizes = []
    for idx in spike_indices:
        if idx + 1 < len(prices):
            size = abs(prices[idx + 1] - prices[idx])
            spike_sizes.append(size)
    return np.array(spike_sizes)

def backtest_relaxed(symbol: str, configs: list):
    """Test multiple relaxed configurations."""
    
    df = load_data(symbol)
    if df is None:
        print(f"No data for {symbol}")
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours, _ = calculate_cold_hours(spike_indices)
    spike_sizes = calculate_spike_sizes(prices, spike_indices)
    
    print(f"\n{'='*70}")
    print(f"SYMBOL: {symbol} ({len(prices)} bars, {len(spike_indices)} spikes)")
    print(f"{'='*70}")
    
    results = []
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        bars_held = 0
        recent_spikes = []
        
        for bar in range(50, len(prices)):
            current_price = prices[bar]
            current_hour = bar // 60 % 24
            
            # Update recent spikes
            recent_spikes = [s for s in recent_spikes if bar - s <= config['cluster_window']]
            
            is_spike = bar in spike_indices
            
            if is_spike:
                recent_spikes.append(bar)
                spike_idx = np.where(spike_indices == bar)[0][0]
                spike_size = spike_sizes[spike_idx]
                is_large_spike = spike_size >= np.percentile(spike_sizes, config['spike_size_percentile'])
            
            # Position management
            if in_position:
                bars_held += 1
                
                # Time exit
                if bars_held >= config['max_hold']:
                    pnl = current_price - entry_price if position_dir == 1 else entry_price - current_price
                    trades.append({'pnl': pnl, 'reason': 'TIME', 'bars': bars_held})
                    in_position = False
                    continue
                
                # Safety exit
                if is_spike and bar + 1 < len(prices):
                    spike_move = prices[bar + 1] - prices[bar]
                    against = (position_dir == 1 and spike_move < 0) or (position_dir == -1 and spike_move > 0)
                    if against:
                        pnl = current_price - entry_price if position_dir == 1 else entry_price - current_price
                        trades.append({'pnl': pnl, 'reason': 'SAFETY', 'bars': bars_held})
                        in_position = False
                        continue
            
            # Entry logic
            if not in_position and is_spike:
                # Count recent spikes
                cluster_count = len([s for s in recent_spikes if s != bar])
                
                # Cluster filter
                if cluster_count >= config['cluster_min']:
                    continue
                
                # Spike size filter
                if config['use_spike_size_filter'] and not is_large_spike:
                    continue
                
                # COLD hours filter
                if config['use_cold_hours'] and current_hour not in cold_hours:
                    continue
                
                # Entry timing (accept any bar after spike)
                bars_since_spike = 0
                for prev_bar in range(bar - 1, max(0, bar - 20), -1):
                    if prev_bar in spike_indices:
                        bars_since_spike = bar - prev_bar
                        break
                
                if bars_since_spike < config['min_delay'] or bars_since_spike > config['max_delay']:
                    continue
                
                # ENTER
                spike_move = prices[bar + 1] - prices[bar] if bar + 1 < len(prices) else 0
                position_dir = -1 if spike_move > 0 else 1
                entry_price = current_price
                bars_held = 0
                in_position = True
        
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
    
    # Print comparison table
    print(f"\n{'Config':<20} {'Trades':<8} {'WR%':<8} {'PF':<8} {'PnL':<10} {'AvgWin':<10} {'AvgLoss':<10}")
    print("-" * 80)
    
    for r in results:
        print(f"{r['config']:<20} {r['trades']:<8} {r['wr']:<8.1f} {r['pf']:<8.2f} {r['pnl']:<10.2f} {r['avg_win']:<10.4f} {r['avg_loss']:<10.4f}")
    
    return results

def main():
    """Test multiple relaxed configurations."""
    
    configs = [
        # Baseline: no filters
        {
            'name': 'No Filters',
            'cluster_window': 10,
            'cluster_min': 100,  # Effectively disabled
            'use_spike_size_filter': False,
            'use_cold_hours': False,
            'min_delay': 1,
            'max_delay': 100,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD hours only
        {
            'name': 'COLD Only',
            'cluster_window': 10,
            'cluster_min': 100,
            'use_spike_size_filter': False,
            'use_cold_hours': True,
            'min_delay': 1,
            'max_delay': 100,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD + Time exit only
        {
            'name': 'COLD+Time',
            'cluster_window': 10,
            'cluster_min': 100,
            'use_spike_size_filter': False,
            'use_cold_hours': True,
            'min_delay': 1,
            'max_delay': 100,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD + Spike size filter
        {
            'name': 'COLD+SpikeSize',
            'cluster_window': 10,
            'cluster_min': 100,
            'use_spike_size_filter': True,
            'use_cold_hours': True,
            'min_delay': 1,
            'max_delay': 100,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD + Entry timing (6-10 bars)
        {
            'name': 'COLD+Timing',
            'cluster_window': 10,
            'cluster_min': 100,
            'use_spike_size_filter': False,
            'use_cold_hours': True,
            'min_delay': 6,
            'max_delay': 10,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD + Timing + Spike size
        {
            'name': 'COLD+Timing+Size',
            'cluster_window': 10,
            'cluster_min': 100,
            'use_spike_size_filter': True,
            'use_cold_hours': True,
            'min_delay': 6,
            'max_delay': 10,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # COLD + Timing + Cluster (relaxed: 5+ spikes)
        {
            'name': 'COLD+Timing+Clust5',
            'cluster_window': 20,
            'cluster_min': 5,
            'use_spike_size_filter': False,
            'use_cold_hours': True,
            'min_delay': 6,
            'max_delay': 10,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
        # ALL FILTERS (most aggressive)
        {
            'name': 'ALL FILTERS',
            'cluster_window': 20,
            'cluster_min': 3,
            'use_spike_size_filter': True,
            'use_cold_hours': True,
            'min_delay': 6,
            'max_delay': 10,
            'max_hold': 6,
            'spike_size_percentile': 67,
        },
    ]
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = backtest_relaxed(symbol, configs)
        all_results[symbol] = results
    
    # Summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*70}")
    
    print(f"\nBest configuration per symbol:")
    for symbol in symbols:
        if all_results[symbol]:
            best = max(all_results[symbol], key=lambda x: x['pnl'])
            print(f"  {symbol}: {best['config']} (WR={best['wr']:.1f}%, PF={best['pf']:.2f}, PnL={best['pnl']:.2f})")

if __name__ == '__main__':
    main()
