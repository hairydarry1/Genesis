#!/usr/bin/env python3
"""
COMPREHENSIVE ANALYSIS
======================
1. Test on ALL symbols (BOOM/CRASH 300, 500, 1000)
2. Research trailing exits
3. Correlation analysis
4. New strategy concepts
"""
import numpy as np
import pandas as pd
import statistics
from collections import defaultdict

def load_all_data():
    """Load all available data"""
    data = {}
    
    # NPY files (tick data)
    for name, path in [('BOOM1000', 'data/ticks_boom1000_index.npy'),
                        ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        try:
            prices = np.load(path)[:300000]
            data[name] = {'prices': prices, 'type': 'tick'}
        except:
            pass
    
    # Parquet files (bar data)
    for name, path in [('BOOM500', 'data/ticks_boom_500.parquet'),
                        ('CRASH500', 'data/ticks_crash_500.parquet')]:
        try:
            df = pd.read_parquet(path)
            data[name] = {'prices': df['price'].values[:60000], 'type': 'bar'}
        except:
            pass
    
    return data

def detect_spikes(prices, data_type='tick'):
    if data_type == 'tick':
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        idx = np.where(moves > 3 * med)[0]
        return idx, med
    else:
        ranges = [abs(prices[i] - prices[i-1]) for i in range(1, len(prices))]
        lookback = 100
        spikes = []
        for i in range(lookback, len(ranges)):
            window = ranges[i-lookback:i]
            med = sorted(window)[len(window)//2]
            if med > 0 and ranges[i] > 3 * med:
                spikes.append(i)
        return np.array(spikes), np.median(ranges)

def test_basic_strategy(prices, spike_idx, med, is_boom):
    """Test basic fade strategy"""
    wins = 0
    losses = 0
    pnls = []
    
    for idx in spike_idx:
        entry_idx = idx + 1
        if entry_idx + 3 >= len(prices):
            continue
        
        entry_price = prices[entry_idx]
        exit_price = prices[min(entry_idx + 3, len(prices) - 1)]
        
        if is_boom:
            pnl = entry_price - exit_price
        else:
            pnl = exit_price - entry_price
        
        pnls.append(pnl)
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    
    total = wins + losses
    wr = wins / total * 100 if total > 0 else 0
    avg = statistics.mean(pnls) if pnls else 0
    pf = sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0)) if sum(p for p in pnls if p < 0) != 0 else 999
    
    return {'wr': wr, 'avg': avg, 'pf': pf, 'trades': total}

def test_trailing_exit(prices, spike_idx, med, is_boom):
    """Test trailing exit (move to breakeven after X pips)"""
    results = {}
    
    for trail_at in [0.5, 1.0, 1.5]:
        wins = 0
        losses = 0
        pnls = []
        
        for idx in spike_idx:
            entry_idx = idx + 1
            if entry_idx + 5 >= len(prices):
                continue
            
            entry_price = prices[entry_idx]
            tp_dist = 1.0 * med
            sl_dist = 0.5 * med
            trail_dist = trail_at * med
            
            best_pnl = 0
            exit_pnl = None
            
            for hold in range(1, 6):
                check_idx = entry_idx + hold
                if check_idx >= len(prices):
                    break
                
                if is_boom:
                    pnl = entry_price - prices[check_idx]
                else:
                    pnl = prices[check_idx] - entry_price
                
                best_pnl = max(best_pnl, pnl)
                
                # Trailing exit: if price moved trail_at in our favor, move SL to breakeven
                if best_pnl >= trail_dist:
                    # Now SL is at breakeven - minimum pnl is 0
                    exit_pnl = max(0, pnl)
                    break
                
                # Normal SL
                if pnl <= -sl_dist:
                    exit_pnl = -sl_dist
                    break
                
                # Normal TP
                if pnl >= tp_dist:
                    exit_pnl = tp_dist
                    break
            
            if exit_pnl is None:
                exit_idx = min(entry_idx + 3, len(prices) - 1)
                if is_boom:
                    exit_pnl = max(0, entry_price - prices[exit_idx])
                else:
                    exit_pnl = max(0, prices[exit_idx] - entry_price)
            
            pnls.append(exit_pnl)
            if exit_pnl > 0:
                wins += 1
            else:
                losses += 1
        
        total = wins + losses
        if total > 0:
            wr = wins / total * 100
            avg = statistics.mean(pnls)
            pf = sum(p for p in pnls if p > 0) / abs(sum(p for p in pnls if p < 0)) if sum(p for p in pnls if p < 0) != 0 else 999
            results[trail_at] = {'wr': wr, 'avg': avg, 'pf': pf}
    
    return results

def test_correlation(data):
    """Test if BOOM and CRASH spikes are correlated"""
    print(f"\n--- CORRELATION ANALYSIS ---")
    
    # Get spike times for each symbol
    spike_times = {}
    for name, d in data.items():
        spike_idx, _ = detect_spikes(d['prices'], d['type'])
        spike_times[name] = set(spike_idx.tolist())
    
    # Check if BOOM1000 and CRASH1000 spikes happen at same time
    if 'BOOM1000' in spike_times and 'CRASH1000' in spike_times:
        boom = spike_times['BOOM1000']
        crash = spike_times['CRASH1000']
        
        # Same tick?
        same_tick = len(boom & crash)
        print(f"  BOOM1000 & CRASH1000 same-tick spikes: {same_tick}")
        
        # Within 1 tick?
        boom_near = set()
        for t in boom:
            boom_near.update([t-1, t, t+1])
        crash_near = set()
        for t in crash:
            crash_near.update([t-1, t, t+1])
        near = len(boom_near & crash_near)
        print(f"  Within 1 tick: {near}")
        
        # Within 5 ticks?
        boom_near5 = set()
        for t in boom:
            boom_near5.update(range(t-5, t+6))
        crash_near5 = set()
        for t in crash:
            crash_near5.update(range(t-5, t+6))
        near5 = len(boom_near5 & crash_near5)
        print(f"  Within 5 ticks: {near5}")
        
        # Are they independent? (Expected vs Actual)
        total_ticks = min(len(data['BOOM1000']['prices']), len(data['CRASH1000']['prices']))
        p_boom = len(boom) / total_ticks
        p_crash = len(crash) / total_ticks
        expected_same = p_boom * p_crash * total_ticks
        print(f"  Expected same-tick (if independent): {expected_same:.1f}")
        print(f"  Actual: {same_tick}")
        print(f"  {'CORRELATED' if same_tick > expected_same * 2 else 'INDEPENDENT'}")

def test_new_strategies(prices, spike_idx, med, is_boom, name):
    """Test new strategy concepts"""
    print(f"\n--- NEW STRATEGIES ({name}) ---")
    
    # Strategy: Wait longer, bigger TP
    print(f"\n  Strategy: Patient Fade (wait 2, TP 2x, SL 1x)")
    wins = 0
    losses = 0
    for idx in spike_idx:
        entry_idx = idx + 2
        if entry_idx + 5 >= len(prices):
            continue
        entry_price = prices[entry_idx]
        exit_price = prices[min(entry_idx + 5, len(prices) - 1)]
        if is_boom:
            pnl = entry_price - exit_price
        else:
            pnl = exit_price - entry_price
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    total = wins + losses
    if total > 0:
        print(f"    WR: {wins/total*100:.1f}% ({wins}/{total})")
    
    # Strategy: Fade only LARGE spikes
    print(f"\n  Strategy: Large Spike Only (>5x median)")
    wins = 0
    losses = 0
    for idx in spike_idx:
        if idx >= len(prices) or idx == 0:
            continue
        move = abs(prices[idx] - prices[idx-1])
        if move < 5 * med:
            continue
        entry_idx = idx + 1
        if entry_idx + 3 >= len(prices):
            continue
        entry_price = prices[entry_idx]
        exit_price = prices[min(entry_idx + 3, len(prices) - 1)]
        if is_boom:
            pnl = entry_price - exit_price
        else:
            pnl = exit_price - entry_price
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    total = wins + losses
    if total > 0:
        print(f"    WR: {wins/total*100:.1f}% ({wins}/{total})")
    else:
        print(f"    No large spikes found")
    
    # Strategy: Fade only SMALL spikes (<5x median)
    print(f"\n  Strategy: Small Spike Only (<5x median)")
    wins = 0
    losses = 0
    for idx in spike_idx:
        if idx >= len(prices) or idx == 0:
            continue
        move = abs(prices[idx] - prices[idx-1])
        if move >= 5 * med:
            continue
        entry_idx = idx + 1
        if entry_idx + 3 >= len(prices):
            continue
        entry_price = prices[entry_idx]
        exit_price = prices[min(entry_idx + 3, len(prices) - 1)]
        if is_boom:
            pnl = entry_price - exit_price
        else:
            pnl = exit_price - entry_price
        if pnl > 0:
            wins += 1
        else:
            losses += 1
    total = wins + losses
    if total > 0:
        print(f"    WR: {wins/total*100:.1f}% ({wins}/{total})")

def main():
    print("="*60)
    print("COMPREHENSIVE ANALYSIS")
    print("="*60)
    
    data = load_all_data()
    print(f"Loaded {len(data)} symbols")
    
    all_results = {}
    
    for name, d in data.items():
        print(f"\n{'#'*60}")
        print(f"# {name}")
        print(f"{'#'*60}")
        
        prices = d['prices']
        data_type = d['type']
        spike_idx, med = detect_spikes(prices, data_type)
        is_boom = 'BOOM' in name
        
        print(f"  Data: {len(prices)} {'ticks' if data_type == 'tick' else 'bars'}")
        print(f"  Spikes: {len(spike_idx)}")
        print(f"  Median: {med:.4f}")
        
        # Basic strategy
        basic = test_basic_strategy(prices, spike_idx, med, is_boom)
        print(f"\n  Basic Strategy:")
        print(f"    WR: {basic['wr']:.1f}%  PF: {basic['pf']:.2f}  Trades: {basic['trades']}")
        
        # Trailing exit
        trailing = test_trailing_exit(prices, spike_idx, med, is_boom)
        print(f"\n  Trailing Exit:")
        for trail_at, res in trailing.items():
            print(f"    Trail @{trail_at}x: WR={res['wr']:.1f}%  PF={res['pf']:.2f}")
        
        # New strategies
        test_new_strategies(prices, spike_idx, med, is_boom, name)
        
        all_results[name] = basic
    
    # Correlation
    test_correlation(data)
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"\n{'Symbol':<15} {'WR%':<8} {'PF':<8} {'Trades':<8}")
    print("-" * 39)
    for name, res in all_results.items():
        print(f"{name:<15} {res['wr']:<8.1f} {res['pf']:<8.2f} {res['trades']:<8}")

if __name__ == "__main__":
    main()
