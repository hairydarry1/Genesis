"""
FIND RIGHT CONTINGENCIES
Balance between protection and trading frequency
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

def backtest_with_contingencies(symbol: str):
    """Test different contingency combinations"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours = calculate_cold_hours(spike_indices)
    
    print(f"\n{'='*70}")
    print(f"CONTINGENCY TESTING: {symbol}")
    print(f"{'='*70}")
    print(f"Spikes: {len(spike_indices)}, Median: {median:.5f}")
    
    results = []
    
    # Config 1: Base (3-bar delay, no contingencies)
    configs = [
        {
            'name': 'Base (3-bar)',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': False,
            'safety_exit': False,
            'cluster_filter': False,
            'buildup_exit': False,
        },
        # Config 2: Base + COLD hours
        {
            'name': '+COLD Hours',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': True,
            'safety_exit': False,
            'cluster_filter': False,
            'buildup_exit': False,
        },
        # Config 3: Base + Safety exit
        {
            'name': '+Safety Exit',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': False,
            'safety_exit': True,
            'cluster_filter': False,
            'buildup_exit': False,
        },
        # Config 4: Base + Time exit (already have this)
        {
            'name': '+Time Exit',
            'delay': 3,
            'exit_after': 3,  # Tighter exit
            'cold_hours': False,
            'safety_exit': False,
            'cluster_filter': False,
            'buildup_exit': False,
        },
        # Config 5: COLD + Safety
        {
            'name': '+COLD+Safety',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': True,
            'safety_exit': True,
            'cluster_filter': False,
            'buildup_exit': False,
        },
        # Config 6: COLD + Safety + Cluster (relaxed)
        {
            'name': '+COLD+Safety+Clust',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': True,
            'safety_exit': True,
            'cluster_filter': True,
            'cluster_min': 5,  # Relaxed: 5+ spikes
            'buildup_exit': False,
        },
        # Config 7: All contingencies (relaxed)
        {
            'name': 'All Relaxed',
            'delay': 3,
            'exit_after': 5,
            'cold_hours': True,
            'safety_exit': True,
            'cluster_filter': True,
            'cluster_min': 5,
            'buildup_exit': True,
            'buildup_threshold': 2.0,  # Relaxed: 2x median
        },
        # Config 8: Safety + Time exit only
        {
            'name': 'Safety+Time',
            'delay': 3,
            'exit_after': 3,
            'cold_hours': False,
            'safety_exit': True,
            'cluster_filter': False,
            'buildup_exit': False,
        },
    ]
    
    for config in configs:
        trades = []
        in_position = False
        position_dir = 0
        entry_price = 0
        bars_held = 0
        recent_spikes = []
        
        # For buildup detection
        tick_sizes = []
        
        for bar in range(50, len(prices)):
            current_price = prices[bar]
            current_hour = bar // 60 % 24
            
            # Update recent spikes
            recent_spikes = [s for s in recent_spikes if bar - s <= 20]
            
            is_spike = bar in spike_indices
            
            if is_spike:
                recent_spikes.append(bar)
            
            # Position management
            if in_position:
                bars_held += 1
                
                # Time exit
                if bars_held >= config['exit_after']:
                    pnl = (current_price - entry_price) * position_dir
                    trades.append({'pnl': pnl, 'reason': 'TIME', 'bars': bars_held})
                    in_position = False
                    continue
                
                # Safety exit (new spike against position)
                if config['safety_exit'] and is_spike and bar + 1 < len(prices):
                    spike_move = prices[bar + 1] - prices[bar]
                    against = (position_dir == 1 and spike_move < 0) or (position_dir == -1 and spike_move > 0)
                    if against:
                        pnl = (current_price - entry_price) * position_dir
                        trades.append({'pnl': pnl, 'reason': 'SAFETY', 'bars': bars_held})
                        in_position = False
                        continue
                
                # Buildup exit (tick size getting larger)
                if config['buildup_exit'] and bar > 0:
                    tick_size = abs(prices[bar] - prices[bar - 1])
                    tick_sizes.append(tick_size)
                    if len(tick_sizes) > 100:
                        tick_sizes.pop(0)
                    
                    if len(tick_sizes) > 10:
                        median_tick = np.median(tick_sizes)
                        if tick_size > median_tick * config['buildup_threshold']:
                            pnl = (current_price - entry_price) * position_dir
                            trades.append({'pnl': pnl, 'reason': 'BUILDUP', 'bars': bars_held})
                            in_position = False
                            continue
            
            # Entry logic
            if not in_position and is_spike:
                # Count recent spikes
                cluster_count = len([s for s in recent_spikes if s != bar])
                
                # Cluster filter
                if config['cluster_filter'] and cluster_count >= config['cluster_min']:
                    continue
                
                # COLD hours filter
                if config['cold_hours'] and current_hour not in cold_hours:
                    continue
                
                # Check delay
                bars_since_spike = 0
                for prev_bar in range(bar - 1, max(0, bar - 20), -1):
                    if prev_bar in spike_indices:
                        bars_since_spike = bar - prev_bar
                        break
                
                if bars_since_spike != config['delay']:
                    continue
                
                # ENTER (fade the spike)
                spike_dir = 1 if prices[bar + 1] > prices[bar] else -1 if bar + 1 < len(prices) else 0
                position_dir = -spike_dir  # Fade
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
        
        # Exit breakdown
        exit_reasons = {}
        for t in trades:
            reason = t['reason']
            if reason not in exit_reasons:
                exit_reasons[reason] = 0
            exit_reasons[reason] += 1
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wins': wins,
            'losses': losses,
            'wr': wr,
            'pf': pf,
            'pnl': total_pnl,
            'exits': exit_reasons
        })
    
    # Print results
    print(f"\n{'Config':<20} {'Trades':<8} {'WR%':<8} {'PF':<8} {'PnL':<10} {'Exits'}")
    print("-" * 90)
    
    for r in results:
        exit_str = ', '.join([f"{k}:{v}" for k, v in r['exits'].items()])
        print(f"{r['config']:<20} {r['trades']:<8} {r['wr']:<8.1f} {r['pf']:<8.2f} {r['pnl']:<10.2f} {exit_str}")
    
    # Find best (highest PnL with reasonable trades)
    if results:
        # Filter for configs with at least 100 trades
        valid = [r for r in results if r['trades'] >= 100]
        if valid:
            best = max(valid, key=lambda x: x['pnl'])
            print(f"\nBest: {best['config']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f}, Trades={best['trades']})")
    
    return results

def main():
    """Run on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = backtest_with_contingencies(symbol)
        all_results[symbol] = results
    
    # Overall summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*70}")
    
    print(f"\nBest configuration per symbol:")
    for symbol in symbols:
        if all_results[symbol]:
            valid = [r for r in all_results[symbol] if r['trades'] >= 100]
            if valid:
                best = max(valid, key=lambda x: x['pnl'])
                print(f"  {symbol}: {best['config']} (WR={best['wr']:.1f}%, PnL={best['pnl']:.2f})")

if __name__ == '__main__':
    main()
