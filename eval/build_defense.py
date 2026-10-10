"""
BUILD DEFENSE: Robust protection against catastrophic spikes
Goal: Never let one spike wipe out accumulated profits
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

def simulate_trades(prices, spike_indices, config):
    """Simulate trades with defense system"""
    
    trades = []
    equity_curve = []
    equity = 10000  # Starting equity
    peak_equity = equity
    max_drawdown = 0
    in_position = False
    position_dir = 0
    entry_price = 0
    entry_bar = 0
    bars_held = 0
    
    # Track recent spikes for defense
    recent_spike_times = []
    recent_spike_dirs = []
    consecutive_against = 0
    
    for bar in range(50, len(prices)):
        current_price = prices[bar]
        
        # Track recent spikes
        recent_spike_times = [t for t in recent_spike_times if bar - t <= config['defense_lookback']]
        recent_spike_dirs = [d for d, t in zip(recent_spike_dirs, recent_spike_times) if bar - t <= config['defense_lookback']]
        
        is_spike = bar in spike_indices
        
        if is_spike:
            spike_dir = 1 if prices[bar + 1] > prices[bar] else -1 if bar + 1 < len(prices) else 0
            recent_spike_times.append(bar)
            recent_spike_dirs.append(spike_dir)
        
        # Position management
        if in_position:
            bars_held += 1
            
            # DEFENSE 1: Time exit (aggressive)
            if bars_held >= config['max_hold']:
                pnl = (current_price - entry_price) * position_dir
                equity += pnl * config['position_size']
                trades.append({'pnl': pnl * config['position_size'], 'reason': 'TIME', 'bars': bars_held})
                in_position = False
                peak_equity = max(peak_equity, equity)
                max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)
                continue
            
            # DEFENSE 2: Quick exit on new spike against us
            if is_spike and bar + 1 < len(prices):
                spike_move = prices[bar + 1] - prices[bar]
                against = (position_dir == 1 and spike_move < 0) or (position_dir == -1 and spike_move > 0)
                
                if against:
                    # IMMEDIATE EXIT
                    pnl = (current_price - entry_price) * position_dir
                    equity += pnl * config['position_size']
                    trades.append({'pnl': pnl * config['position_size'], 'reason': 'SPIKE_DEFENSE', 'bars': bars_held})
                    in_position = False
                    consecutive_against += 1
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)
                    continue
            
            # DEFENSE 3: Exit if multiple spikes against us recently
            if config['use_multi_spike_defense']:
                recent_against = sum(1 for d in recent_spike_dirs if d == -position_dir)
                if recent_against >= config['multi_spike_threshold']:
                    pnl = (current_price - entry_price) * position_dir
                    equity += pnl * config['position_size']
                    trades.append({'pnl': pnl * config['position_size'], 'reason': 'MULTI_SPIKE_DEFENSE', 'bars': bars_held})
                    in_position = False
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)
                    continue
            
            # DEFENSE 4: Equity-based stop (if lost X% from peak)
            if config['use_equity_stop']:
                if (peak_equity - equity) / peak_equity > config['equity_stop_pct']:
                    pnl = (current_price - entry_price) * position_dir
                    equity += pnl * config['position_size']
                    trades.append({'pnl': pnl * config['position_size'], 'reason': 'EQUITY_STOP', 'bars': bars_held})
                    in_position = False
                    peak_equity = max(peak_equity, equity)
                    max_drawdown = max(max_drawdown, (peak_equity - equity) / peak_equity * 100)
                    continue
        
        # Entry logic
        if not in_position and is_spike:
            # Check if we should enter
            if config['skip_recent_spike']:
                # Skip if recent spike was against us
                if len(recent_spike_dirs) > 0 and recent_spike_dirs[-1] == -1 if IsCrash() else 1:
                    continue
            
            # Check delay
            bars_since_spike = 0
            for prev_bar in range(bar - 1, max(0, bar - 20), -1):
                if prev_bar in spike_indices:
                    bars_since_spike = bar - prev_bar
                    break
            
            if bars_since_spike != config['delay']:
                continue
            
            # ENTER
            spike_dir = 1 if prices[bar + 1] > prices[bar] else -1 if bar + 1 < len(prices) else 0
            position_dir = -spike_dir  # Fade
            entry_price = current_price
            entry_bar = bar
            bars_held = 0
            in_position = True
            consecutive_against = 0
    
    # Close remaining position
    if in_position:
        pnl = (prices[-1] - entry_price) * position_dir
        equity += pnl * config['position_size']
        trades.append({'pnl': pnl * config['position_size'], 'reason': 'END', 'bars': bars_held})
    
    return trades, equity, max_drawdown

def IsCrash():
    return True  # Simplified

def test_defense_systems(symbol: str):
    """Test different defense configurations"""
    
    df = load_data(symbol)
    if df is None:
        return
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    
    print(f"\n{'='*70}")
    print(f"DEFENSE SYSTEM TESTING: {symbol}")
    print(f"{'='*70}")
    
    configs = [
        # No defense
        {
            'name': 'No Defense',
            'delay': 1,
            'max_hold': 5,
            'position_size': 1.0,
            'skip_recent_spike': False,
            'use_multi_spike_defense': False,
            'multi_spike_threshold': 3,
            'defense_lookback': 10,
            'use_equity_stop': False,
            'equity_stop_pct': 0.05,
        },
        # Time exit only
        {
            'name': 'Time Exit',
            'delay': 1,
            'max_hold': 3,  # Tighter
            'position_size': 1.0,
            'skip_recent_spike': False,
            'use_multi_spike_defense': False,
            'multi_spike_threshold': 3,
            'defense_lookback': 10,
            'use_equity_stop': False,
            'equity_stop_pct': 0.05,
        },
        # Spike defense only
        {
            'name': 'Spike Defense',
            'delay': 1,
            'max_hold': 5,
            'position_size': 1.0,
            'skip_recent_spike': True,  # Skip if recent spike against us
            'use_multi_spike_defense': False,
            'multi_spike_threshold': 3,
            'defense_lookback': 10,
            'use_equity_stop': False,
            'equity_stop_pct': 0.05,
        },
        # Multi-spike defense
        {
            'name': 'Multi-Spike',
            'delay': 1,
            'max_hold': 5,
            'position_size': 1.0,
            'skip_recent_spike': False,
            'use_multi_spike_defense': True,
            'multi_spike_threshold': 2,  # Exit if 2+ spikes against us
            'defense_lookback': 10,
            'use_equity_stop': False,
            'equity_stop_pct': 0.05,
        },
        # Equity stop
        {
            'name': 'Equity Stop',
            'delay': 1,
            'max_hold': 5,
            'position_size': 1.0,
            'skip_recent_spike': False,
            'use_multi_spike_defense': False,
            'multi_spike_threshold': 3,
            'defense_lookback': 10,
            'use_equity_stop': True,
            'equity_stop_pct': 0.02,  # Stop at 2% drawdown
        },
        # ALL DEFENSE
        {
            'name': 'ALL DEFENSE',
            'delay': 1,
            'max_hold': 3,
            'position_size': 0.5,  # Smaller position
            'skip_recent_spike': True,
            'use_multi_spike_defense': True,
            'multi_spike_threshold': 2,
            'defense_lookback': 10,
            'use_equity_stop': True,
            'equity_stop_pct': 0.02,
        },
        # AGGRESSIVE DEFENSE
        {
            'name': 'Aggressive',
            'delay': 1,
            'max_hold': 2,  # Very tight
            'position_size': 0.25,  # Very small
            'skip_recent_spike': True,
            'use_multi_spike_defense': True,
            'multi_spike_threshold': 1,  # Exit on ANY spike against us
            'defense_lookback': 5,
            'use_equity_stop': True,
            'equity_stop_pct': 0.01,  # 1% stop
        },
    ]
    
    results = []
    
    for config in configs:
        trades, final_equity, max_dd = simulate_trades(prices, spike_indices, config)
        
        wins = sum(1 for t in trades if t['pnl'] > 0)
        losses = sum(1 for t in trades if t['pnl'] <= 0)
        total = len(trades)
        wr = wins / total * 100 if total > 0 else 0
        total_pnl = sum(t['pnl'] for t in trades)
        
        # Worst single loss
        worst_loss = min(t['pnl'] for t in trades) if trades else 0
        
        # Profit factor
        gross_profit = sum(t['pnl'] for t in trades if t['pnl'] > 0)
        gross_loss = abs(sum(t['pnl'] for t in trades if t['pnl'] <= 0))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        results.append({
            'config': config['name'],
            'trades': total,
            'wr': wr,
            'pnl': total_pnl,
            'worst_loss': worst_loss,
            'max_dd': max_dd,
            'final_equity': final_equity,
            'pf': pf
        })
    
    # Print results
    print(f"\n{'Config':<15} {'Trades':<8} {'WR%':<8} {'PnL':<10} {'WorstLoss':<12} {'MaxDD%':<10} {'PF':<8}")
    print("-" * 85)
    
    for r in results:
        print(f"{r['config']:<15} {r['trades']:<8} {r['wr']:<8.1f} {r['pnl']:<10.2f} {r['worst_loss']:<12.2f} {r['max_dd']:<10.2f} {r['pf']:<8.2f}")
    
    # Find best (lowest worst loss with positive PnL)
    profitable = [r for r in results if r['pnl'] > 0]
    if profitable:
        best = min(profitable, key=lambda x: abs(x['worst_loss']))
        print(f"\nBest Defense: {best['config']}")
        print(f"  Worst loss: {best['worst_loss']:.2f}")
        print(f"  Max drawdown: {best['max_dd']:.2f}%")
        print(f"  PnL: {best['pnl']:.2f}")
        print(f"  WR: {best['wr']:.1f}%")
    
    return results

def main():
    """Test on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    all_results = {}
    for symbol in symbols:
        results = test_defense_systems(symbol)
        all_results[symbol] = results
    
    # Overall summary
    print(f"\n{'='*70}")
    print(f"OVERALL SUMMARY: DEFENSE SYSTEMS")
    print(f"{'='*70}")
    
    print(f"\nBest defense per symbol:")
    for symbol in symbols:
        if all_results[symbol]:
            profitable = [r for r in all_results[symbol] if r['pnl'] > 0]
            if profitable:
                best = min(profitable, key=lambda x: abs(x['worst_loss']))
                print(f"  {symbol}: {best['config']} (Worst: {best['worst_loss']:.2f}, MaxDD: {best['max_dd']:.2f}%, PnL: {best['pnl']:.2f})")

if __name__ == '__main__':
    main()
