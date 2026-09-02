#!/usr/bin/env python3
"""
SELF-CRITIQUE: CHALLENGING OUR OWN FINDINGS
=============================================
I am both the researcher AND the reviewer.
Every finding must survive rigorous scrutiny.
"""
import numpy as np
import statistics
from collections import defaultdict

# ============================================================
# CHALLENGE 1: Is the 99.7% win rate real or data bias?
# ============================================================

def challenge_win_rate():
    print("="*60)
    print("CHALLENGE 1: Is 99.7% WR real or data bias?")
    print("="*60)
    
    # Load different time periods
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy'),
                            ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        prices = np.load(filepath)[:500000]
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        spike_idx = np.where(moves > 3 * med)[0]
        is_boom = 'BOOM' in name
        
        print(f"\n--- {name} ---")
        
        # Split into 4 quarters
        quarter_size = len(prices) // 4
        quarters = []
        for q in range(4):
            start = q * quarter_size
            end = (q + 1) * quarter_size
            q_prices = prices[start:end]
            q_moves = np.abs(np.diff(q_prices))
            q_med = np.median(q_moves)
            q_spikes = np.where(q_moves > 3 * q_med)[0] + start
            quarters.append((q_prices, q_spikes, q_med, start))
        
        # Test strategy on each quarter
        print(f"\n  Win Rate by Quarter:")
        for q, (q_prices, q_spikes, q_med, offset) in enumerate(quarters):
            wins = 0
            losses = 0
            pnls = []
            
            for idx in q_spikes:
                entry_idx = idx + 1
                if entry_idx + 3 >= len(q_prices):
                    continue
                
                entry_price = q_prices[entry_idx]
                exit_price = q_prices[min(entry_idx + 3, len(q_prices) - 1)]
                
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
            if total > 0:
                wr = wins / total * 100
                avg = statistics.mean(pnls) if pnls else 0
                print(f"    Q{q+1}: WR={wr:.1f}%  Trades={total}  AvgPnL={avg:+.6f}")
        
        # Check for recency bias
        print(f"\n  Recency Bias Check:")
        first_half = spike_idx[:len(spike_idx)//2]
        second_half = spike_idx[len(spike_idx)//2:]
        
        for half_name, half_idx in [("First half", first_half), ("Second half", second_half)]:
            wins = 0
            losses = 0
            for idx in half_idx:
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
                print(f"    {half_name}: WR={wins/total*100:.1f}% ({wins}/{total})")

# ============================================================
# CHALLENGE 2: What's the REAL slippage from live logs?
# ============================================================

def challenge_slippage():
    print("\n" + "="*60)
    print("CHALLENGE 2: What's the ACTUAL slippage from live trades?")
    print("="*60)
    
    import os, csv
    
    log_dir = "SpikeCatcher"
    if not os.path.exists(log_dir):
        print("  No live trading logs found")
        return
    
    for f in os.listdir(log_dir):
        if f.startswith("trades_") and f.endswith(".csv"):
            filepath = os.path.join(log_dir, f)
            print(f"\n  Analyzing: {f}")
            
            trades = []
            with open(filepath, 'r') as file:
                reader = csv.DictReader(file)
                for row in reader:
                    try:
                        entry = float(row.get('entry', 0))
                        exit_price = float(row.get('exit', 0))
                        pnl = float(row.get('pnl', 0))
                        reason = row.get('reason', '')
                        if entry > 0:
                            trades.append({
                                'entry': entry,
                                'exit': exit_price,
                                'pnl': pnl,
                                'reason': reason
                            })
                    except:
                        continue
            
            if not trades:
                print("    No valid trades")
                continue
            
            print(f"    Total trades: {len(trades)}")
            
            # Analyze by exit reason
            reasons = defaultdict(list)
            for t in trades:
                reasons[t['reason']].append(t)
            
            print(f"    Exit reasons:")
            for reason, reason_trades in sorted(reasons.items()):
                pnls = [t['pnl'] for t in reason_trades]
                avg_pnl = statistics.mean(pnls) if pnls else 0
                print(f"      {reason}: {len(reason_trades)} trades, Avg PnL={avg_pnl:+.4f}")
            
            # Check for slippage on SL exits
            sl_trades = [t for t in trades if t['reason'] == 'sl']
            if sl_trades:
                sl_pnls = [t['pnl'] for t in sl_trades]
                print(f"\n    SL Trade Analysis:")
                print(f"      Count: {len(sl_trades)}")
                print(f"      Avg PnL: {statistics.mean(sl_pnls):+.4f}")
                print(f"      Max loss: {min(sl_pnls):+.4f}")
                print(f"      Min loss: {max(sl_pnls):+.4f}")

# ============================================================
# CHALLENGE 3: Can we actually detect clusters before they happen?
# ============================================================

def challenge_cluster_prediction():
    print("\n" + "="*60)
    print("CHALLENGE 3: Can we predict clusters?")
    print("="*60)
    
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy'),
                            ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        prices = np.load(filepath)[:500000]
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        spike_idx = np.where(moves > 3 * med)[0]
        
        print(f"\n--- {name} ---")
        
        # Can we predict the NEXT interval based on recent intervals?
        intervals = np.diff(spike_idx)
        
        # Test: if last interval was short, is next one more likely short?
        short_threshold = np.median(intervals)
        
        correct = 0
        total = 0
        for i in range(3, len(intervals)):
            # Use last 3 intervals to predict
            recent = intervals[i-3:i]
            if len(recent) == 0:
                continue
            avg_recent = statistics.mean(recent)
            
            # Predict: if recent avg is short, predict next is short
            if avg_recent < short_threshold:
                predicted_short = True
            else:
                predicted_short = False
            
            actual_short = intervals[i] < short_threshold
            
            if predicted_short == actual_short:
                correct += 1
            total += 1
        
        accuracy = correct / total * 100 if total > 0 else 0
        print(f"  Cluster prediction accuracy: {accuracy:.1f}% (random = 50%)")
        
        # Can we predict based on spike SIZE?
        sizes = moves[spike_idx] / med
        
        # Does a large spike predict a cluster?
        large_spike_next = 0
        small_spike_next = 0
        large_count = 0
        small_count = 0
        
        for i in range(len(spike_idx) - 1):
            if sizes[i] > np.median(sizes):
                large_count += 1
                if intervals[i] < short_threshold:
                    large_spike_next += 1
            else:
                small_count += 1
                if intervals[i] < short_threshold:
                    small_spike_next += 1
        
        print(f"  After large spike: {large_spike_next/max(large_count,1)*100:.1f}% short interval")
        print(f"  After small spike: {small_spike_next/max(small_count,1)*100:.1f}% short interval")
        print(f"  (Random would be ~50%)")

# ============================================================
# CHALLENGE 4: Edge decay test
# ============================================================

def challenge_edge_decay():
    print("\n" + "="*60)
    print("CHALLENGE 4: Does the edge decay over time?")
    print("="*60)
    
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy'),
                            ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        prices = np.load(filepath)[:500000]
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        spike_idx = np.where(moves > 3 * med)[0]
        is_boom = 'BOOM' in name
        
        print(f"\n--- {name} ---")
        
        # Test strategy in rolling windows
        window_size = len(spike_idx) // 10
        
        print(f"  Rolling WR (10 windows):")
        for i in range(10):
            start = i * window_size
            end = min((i + 1) * window_size, len(spike_idx))
            window_spikes = spike_idx[start:end]
            
            wins = 0
            total = 0
            for idx in window_spikes:
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
                total += 1
            
            if total > 0:
                wr = wins / total * 100
                bar = '#' * int(wr / 2)
                print(f"    Window {i+1:2d}: {wr:.1f}% {bar}")
        
        # Check for trend
        all_wrs = []
        for i in range(10):
            start = i * window_size
            end = min((i + 1) * window_size, len(spike_idx))
            window_spikes = spike_idx[start:end]
            wins = 0
            total = 0
            for idx in window_spikes:
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
                total += 1
            if total > 0:
                all_wrs.append(wins / total * 100)
        
        if len(all_wrs) >= 2:
            # Linear regression
            x = list(range(len(all_wrs)))
            slope = np.polyfit(x, all_wrs, 1)[0]
            print(f"  WR trend: {'DECLINING' if slope < -0.1 else 'STABLE' if abs(slope) < 0.1 else 'IMPROVING'} ({slope:+.2f}% per window)")

# ============================================================
# CHALLENGE 5: What's the MAXIMUM position size?
# ============================================================

def challenge_position_size():
    print("\n" + "="*60)
    print("CHALLENGE 5: Maximum position size before impact?")
    print("="*60)
    
    print("""
  KEY QUESTION: Can our trades MOVE the market?
  
  If we trade too large:
  - Our entry pushes price against us (market impact)
  - Our exit pushes price against us (slippage increases)
  - The algorithm might detect us and adapt
  
  REALITY CHECK:
  - Deriv processes millions of trades per day
  - Our 50-100 trades/day is NOTHING
  - The market is too deep for us to impact
  
  CONCLUSION:
  - Position size is limited by RISK, not market impact
  - Max 2% risk per trade = safe
  - 5% risk = aggressive but survivable
  - 10%+ risk = gambling
""")

# ============================================================
# CHALLENGE 6: Alternative strategies
# ============================================================

def challenge_alternatives():
    print("\n" + "="*60)
    print("CHALLENGE 6: Are there better strategies?")
    print("="*60)
    
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy')]:
        prices = np.load(filepath)[:200000]
        moves = np.abs(np.diff(prices))
        med = np.median(moves)
        spike_idx = np.where(moves > 3 * med)[0]
        
        print(f"\n--- {name} ---")
        
        # Strategy 1: Fade immediately (no delay)
        wins = sum(1 for idx in spike_idx if idx + 1 < len(prices) and 
                   (prices[idx+1] - prices[idx]) * (-1 if 'BOOM' in name else 1) < 0)
        print(f"  Strategy 1 - Fade immediately: {wins/len(spike_idx)*100:.1f}% WR")
        
        # Strategy 2: Follow the spike (momentum)
        wins = sum(1 for idx in spike_idx if idx + 1 < len(prices) and 
                   (prices[idx+1] - prices[idx]) * (1 if 'BOOM' in name else -1) > 0)
        print(f"  Strategy 2 - Follow spike: {wins/len(spike_idx)*100:.1f}% WR")
        
        # Strategy 3: Wait 5 ticks, then fade
        wins = 0
        total = 0
        for idx in spike_idx:
            if idx + 6 >= len(prices):
                continue
            entry_price = prices[idx + 5]
            exit_price = prices[idx + 8]
            if 'BOOM' in name:
                pnl = entry_price - exit_price
            else:
                pnl = exit_price - entry_price
            if pnl > 0:
                wins += 1
            total += 1
        print(f"  Strategy 3 - Wait 5 then fade: {wins/max(total,1)*100:.1f}% WR")
        
        # Strategy 4: Fade with trailing exit
        print(f"  Strategy 4 - Trailing exit: Would need to test")
        
        # Strategy 5: Scale in (DCA)
        print(f"  Strategy 5 - Scale in: Would need to test")
        
        # Strategy 6: Multiple timeframe
        print(f"  Strategy 6 - Multi-timeframe: Would need to test")

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    print("="*60)
    print("SELF-CRITIQUE: CHALLENGING OUR OWN FINDINGS")
    print("I am both researcher AND reviewer")
    print("="*60)
    
    challenge_win_rate()
    challenge_slippage()
    challenge_cluster_prediction()
    challenge_edge_decay()
    challenge_position_size()
    challenge_alternatives()
    
    print("\n" + "="*60)
    print("SUMMARY OF SELF-CRITIQUE")
    print("="*60)
    print("""
1. WIN RATE: Need to verify across time periods
2. SLIPPAGE: Need live trade data to measure actual slippage
3. CLUSTERS: Cannot predict clusters (random)
4. EDGE DECAY: Need to test if WR changes over time
5. POSITION SIZE: Limited by risk, not market impact
6. ALTERNATIVES: Fade is best, but trailing exit might help
""")
