"""
REAL BACKTEST: With lot sizes, spread, slippage
Shows actual dollar amounts
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

def real_backtest(symbol: str, config, capital=1000, lot_size=0.01, spread=0.0001, slippage=0.00005):
    """Real backtest with actual trading costs"""
    
    df = load_data(symbol)
    if df is None:
        return None
    
    prices = df['close'].values
    spike_indices, threshold, median = detect_spikes(prices)
    cold_hours = calculate_cold_hours(spike_indices)
    
    # Calculate point value (simplified)
    # Assume 1 lot = $10 per point movement
    point_value = 10.0
    
    trades = []
    equity = capital
    peak_equity = capital
    max_drawdown = 0
    max_drawdown_pct = 0
    
    for i, spike_idx in enumerate(spike_indices[:-1]):
        next_spike = spike_indices[i + 1]
        
        if spike_idx + config['delay'] + config['exit_after'] + 1 >= len(prices):
            continue
        
        if next_spike - spike_idx < config['delay'] + 2:
            continue
        
        # Skip conditions
        if config.get('skip_double') and i > 0:
            if spike_idx - spike_indices[i - 1] == 1:
                continue
        
        if config.get('skip_triple') and i > 0:
            if spike_idx - spike_indices[i - 1] <= 2:
                continue
        
        if config.get('skip_cluster') and i > 0:
            if spike_idx - spike_indices[i - 1] <= 3:
                continue
        
        if config.get('cold_hours'):
            hour = spike_idx // 60 % 24
            if hour not in cold_hours:
                continue
        
        # Determine spike direction
        spike_dir = 1 if prices[spike_idx + 1] > prices[spike_idx] else -1
        
        # Entry
        entry_bar = spike_idx + config['delay']
        entry_price = prices[entry_bar] + spread * spike_dir + slippage * spike_dir
        
        # Exit
        exit_bar = entry_bar + config['exit_after']
        exit_price = prices[exit_bar] - spread * spike_dir - slippage * spike_dir
        
        # Check safety exit
        if config.get('safety_exit'):
            for bar in range(entry_bar + 1, min(exit_bar + 1, len(prices))):
                if bar in spike_indices:
                    spike_move = prices[bar + 1] - prices[bar] if bar + 1 < len(prices) else 0
                    against = (spike_dir == 1 and spike_move < 0) or (spike_dir == -1 and spike_move > 0)
                    if against:
                        exit_bar = bar
                        exit_price = prices[bar] - spread * spike_dir - slippage * spike_dir
                        break
        
        # Calculate PnL
        price_diff = (exit_price - entry_price) * (-spike_dir)  # Fade
        pnl_points = price_diff
        pnl_dollars = pnl_points * point_value * lot_size
        
        equity += pnl_dollars
        peak_equity = max(peak_equity, equity)
        drawdown = (peak_equity - equity) / peak_equity * 100
        max_drawdown = max(max_drawdown, drawdown)
        
        trades.append({
            'entry': entry_price,
            'exit': exit_price,
            'pnl_points': pnl_points,
            'pnl_dollars': pnl_dollars,
            'equity': equity,
            'drawdown': drawdown
        })
    
    if not trades:
        return None
    
    # Stats
    wins = sum(1 for t in trades if t['pnl_dollars'] > 0)
    losses = sum(1 for t in trades if t['pnl_dollars'] <= 0)
    total = len(trades)
    wr = wins / total * 100
    total_pnl = sum(t['pnl_dollars'] for t in trades)
    avg_win = np.mean([t['pnl_dollars'] for t in trades if t['pnl_dollars'] > 0]) if wins > 0 else 0
    avg_loss = np.mean([t['pnl_dollars'] for t in trades if t['pnl_dollars'] <= 0]) if losses > 0 else 0
    worst_loss = min(t['pnl_dollars'] for t in trades)
    best_win = max(t['pnl_dollars'] for t in trades)
    
    # Monthly estimate (30 days)
    if len(trades) > 0:
        days_span = (spike_indices[-1] - spike_indices[0]) / 60 / 24  # Convert bars to days
        trades_per_day = total / days_span if days_span > 0 else 0
        monthly_trades = trades_per_day * 30
        monthly_pnl = total_pnl / days_span * 30 if days_span > 0 else 0
    else:
        monthly_trades = 0
        monthly_pnl = 0
    
    return {
        'symbol': symbol,
        'config': config['name'],
        'trades': total,
        'wins': wins,
        'losses': losses,
        'wr': wr,
        'total_pnl': total_pnl,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'worst_loss': worst_loss,
        'best_win': best_win,
        'max_drawdown': max_drawdown,
        'final_equity': equity,
        'monthly_trades': monthly_trades,
        'monthly_pnl': monthly_pnl,
        'roi': (equity - capital) / capital * 100
    }

def main():
    """Run real backtest on all symbols"""
    
    symbols = ['CRASH500', 'BOOM500', 'CRASH1000', 'BOOM1000']
    
    configs = [
        # Base
        {'name': 'Base (No Defense)', 'delay': 1, 'exit_after': 5, 'skip_double': False, 'cold_hours': False, 'safety_exit': False},
        # Time3
        {'name': 'Time Exit 3', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Skip Double
        {'name': 'Time3 + Skip Double', 'delay': 1, 'exit_after': 3, 'skip_double': True, 'cold_hours': False, 'safety_exit': False},
        # Time3 + Cold Hours
        {'name': 'Time3 + Cold Hours', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'cold_hours': True, 'safety_exit': False},
        # Time3 + Safety Exit
        {'name': 'Time3 + Safety Exit', 'delay': 1, 'exit_after': 3, 'skip_double': False, 'cold_hours': False, 'safety_exit': True},
        # Time3 + ALL
        {'name': 'Time3 + ALL Defenses', 'delay': 1, 'exit_after': 3, 'skip_double': True, 'cold_hours': True, 'safety_exit': True},
    ]
    
    capital = 1000
    lot_size = 0.01
    
    print(f"\n{'='*100}")
    print(f"REAL BACKTEST: ${capital} Capital, {lot_size} Lot Size")
    print(f"{'='*100}")
    
    all_results = {}
    
    for symbol in symbols:
        print(f"\n{'='*100}")
        print(f"{symbol}")
        print(f"{'='*100}")
        
        symbol_results = []
        
        for config in configs:
            result = real_backtest(symbol, config, capital=capital, lot_size=lot_size)
            if result:
                symbol_results.append(result)
        
        all_results[symbol] = symbol_results
        
        # Print results
        print(f"\n{'Config':<25} {'Trades':<8} {'WR%':<8} {'PnL':<12} {'AvgWin':<10} {'AvgLoss':<10} {'Worst':<10} {'MaxDD%':<10} {'Monthly':<12}")
        print("-" * 110)
        
        for r in symbol_results:
            print(f"{r['config']:<25} {r['trades']:<8} {r['wr']:<8.1f} ${r['total_pnl']:<11.2f} ${r['avg_win']:<9.2f} ${r['avg_loss']:<9.2f} ${r['worst_loss']:<9.2f} {r['max_drawdown']:<10.2f} ${r['monthly_pnl']:<11.2f}")
    
    # Overall summary
    print(f"\n{'='*100}")
    print(f"OVERALL SUMMARY")
    print(f"{'='*100}")
    
    print(f"\n{'Symbol':<15} {'Best Config':<25} {'Total PnL':<12} {'Monthly':<12} {'MaxDD%':<10} {'WR%':<8}")
    print("-" * 85)
    
    for symbol in symbols:
        if all_results[symbol]:
            best = max(all_results[symbol], key=lambda x: x['total_pnl'])
            print(f"{symbol:<15} {best['config']:<25} ${best['total_pnl']:<11.2f} ${best['monthly_pnl']:<11.2f} {best['max_drawdown']:<10.2f} {best['wr']:<8.1f}")
    
    # Combined portfolio
    print(f"\n{'='*100}")
    print(f"COMBINED PORTFOLIO (${capital} per symbol = ${capital * 4} total)")
    print(f"{'='*100}")
    
    for config in configs:
        total_pnl = 0
        total_trades = 0
        total_wins = 0
        
        for symbol in symbols:
            if all_results[symbol]:
                result = next((r for r in all_results[symbol] if r['config'] == config['name']), None)
                if result:
                    total_pnl += result['total_pnl']
                    total_trades += result['trades']
                    total_wins += result['wins']
        
        if total_trades > 0:
            wr = total_wins / total_trades * 100
            print(f"\n{config['name']}:")
            print(f"  Total PnL: ${total_pnl:.2f}")
            print(f"  Total Trades: {total_trades}")
            print(f"  Win Rate: {wr:.1f}%")
            print(f"  ROI: {total_pnl / (capital * 4) * 100:.1f}%")

if __name__ == '__main__':
    main()
