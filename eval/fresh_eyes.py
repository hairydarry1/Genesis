#!/usr/bin/env python3
"""
FRESH EYES ANALYSIS
===================
Forget everything we think we know.
Look at the data with completely fresh eyes.
What ACTUALLY works?
"""
import numpy as np
import statistics

def fresh_analysis():
    for name, filepath in [('BOOM1000', 'data/ticks_boom1000_index.npy'),
                            ('CRASH1000', 'data/ticks_crash1000_index.npy')]:
        prices = np.load(filepath)[:300000]
        moves = np.diff(prices)
        abs_moves = np.abs(moves)
        med = np.median(abs_moves)
        spike_idx = np.where(abs_moves > 3 * med)[0]
        is_boom = 'BOOM' in name
        
        print(f"\n{'='*60}")
        print(f"FRESH EYES: {name}")
        print(f"{'='*60}")
        print(f"Data: {len(prices)} ticks, {len(spike_idx)} spikes")
        
        # QUESTION 1: What if we just follow the spike?
        print(f"\n--- Q1: FOLLOW THE SPIKE (momentum) ---")
        for delay in [0, 1, 2, 3]:
            wins = 0
            total = 0
            pnls = []
            for idx in spike_idx:
                entry_idx = idx + delay
                if entry_idx + 3 >= len(prices):
                    continue
                entry = prices[entry_idx]
                exit_p = prices[min(entry_idx + 3, len(prices)-1)]
                # Follow = same direction as spike
                if is_boom:
                    pnl = exit_p - entry  # Long after UP spike
                else:
                    pnl = entry - exit_p  # Short after DOWN spike
                pnls.append(pnl)
                if pnl > 0: wins += 1
                total += 1
            if total > 0:
                print(f"  Delay={delay}: WR={wins/total*100:.1f}% ({wins}/{total}) Avg={statistics.mean(pnls):+.6f}")
        
        # QUESTION 2: What about random entry?
        print(f"\n--- Q2: RANDOM ENTRY ---")
        wins = 0
        total = 0
        for _ in range(5000):
            idx = np.random.randint(100, len(prices)-10)
            direction = np.random.choice([-1, 1])
            entry = prices[idx]
            exit_p = prices[min(idx+3, len(prices)-1)]
            pnl = (exit_p - entry) * direction
            if pnl > 0: wins += 1
            total += 1
        print(f"  Random: WR={wins/total*100:.1f}% ({wins}/{total})")
        
        # QUESTION 3: What about buying the dip after DOWN moves?
        print(f"\n--- Q3: BUY THE DIP (after N consecutive down ticks) ---")
        for n in [3, 5, 10, 20]:
            wins = 0
            total = 0
            for i in range(n+100, len(prices)-10):
                # Check if last n ticks were all down
                all_down = all(prices[i-j] < prices[i-j-1] for j in range(n))
                if not all_down:
                    continue
                # Buy after N down ticks
                entry = prices[i]
                exit_p = prices[min(i+3, len(prices)-1)]
                pnl = exit_p - entry
                if pnl > 0: wins += 1
                total += 1
            if total > 0:
                print(f"  After {n:2d} down ticks: WR={wins/total*100:.1f}% ({wins}/{total})")
        
        # QUESTION 4: What about selling the rally?
        print(f"\n--- Q4: SELL THE RALLY (after N consecutive up ticks) ---")
        for n in [3, 5, 10, 20]:
            wins = 0
            total = 0
            for i in range(n+100, len(prices)-10):
                all_up = all(prices[i-j] > prices[i-j-1] for j in range(n))
                if not all_up:
                    continue
                entry = prices[i]
                exit_p = prices[min(i+3, len(prices)-1)]
                pnl = entry - exit_p  # Short after up ticks
                if pnl > 0: wins += 1
                total += 1
            if total > 0:
                print(f"  After {n:2d} up ticks: WR={wins/total*100:.1f}% ({wins}/{total})")
        
        # QUESTION 5: What if we look at the OVERALL trend?
        print(f"\n--- Q5: TREND FILTER ---")
        # Use 100-tick moving average
        ma100 = np.convolve(prices, np.ones(100)/100, mode='valid')
        
        for ma_period in [50, 100, 200]:
            ma = np.convolve(prices, np.ones(ma_period)/ma_period, mode='valid')
            wins = 0
            total = 0
            for idx in spike_idx:
                if idx + ma_period >= len(ma) or idx + 3 >= len(prices):
                    continue
                # Only trade if price is above/below MA
                if is_boom:
                    # Only short (fade) if price is above MA
                    if prices[idx] > ma[idx]:
                        entry = prices[idx+1]
                        exit_p = prices[min(idx+4, len(prices)-1)]
                        pnl = entry - exit_p
                        if pnl > 0: wins += 1
                        total += 1
                else:
                    # Only long (fade) if price is below MA
                    if prices[idx] < ma[idx]:
                        entry = prices[idx+1]
                        exit_p = prices[min(idx+4, len(prices)-1)]
                        pnl = exit_p - entry
                        if pnl > 0: wins += 1
                        total += 1
            if total > 0:
                print(f"  MA{ma_period} filter: WR={wins/total*100:.1f}% ({wins}/{total})")
        
        # QUESTION 6: What's the OPTIMAL hold time?
        print(f"\n--- Q6: OPTIMAL HOLD TIME ---")
        for hold in [1, 2, 3, 5, 10, 20]:
            wins = 0
            total = 0
            pnls = []
            for idx in spike_idx:
                entry_idx = idx + 1
                if entry_idx + hold >= len(prices):
                    continue
                entry = prices[entry_idx]
                exit_p = prices[min(entry_idx + hold, len(prices)-1)]
                if is_boom:
                    pnl = entry - exit_p  # Fade
                else:
                    pnl = exit_p - entry  # Fade
                pnls.append(pnl)
                if pnl > 0: wins += 1
                total += 1
            if total > 0:
                avg = statistics.mean(pnls)
                print(f"  Hold={hold:2d}: WR={wins/total*100:.1f}% Avg={avg:+.6f} Total={sum(pnls):+.4f}")
        
        # QUESTION 7: What about a completely different approach?
        print(f"\n--- Q7: GRID TRADING (buy dips, sell rallies) ---")
        grid_size = med * 5
        position = 0
        entry_price = 0
        total_pnl = 0
        trades = 0
        
        for i in range(100, len(prices)):
            if position == 0:
                # Check for spike
                if i > 0 and abs(prices[i] - prices[i-1]) > 3 * med:
                    # Enter opposite to spike
                    if prices[i] > prices[i-1]:
                        position = -1  # Short
                    else:
                        position = 1   # Long
                    entry_price = prices[i]
            else:
                # Check for exit
                pnl = position * (prices[i] - entry_price)
                if abs(pnl) >= grid_size or i - spike_idx[np.argmin(np.abs(spike_idx - i))] > 5:
                    total_pnl += pnl
                    trades += 1
                    position = 0
        
        print(f"  Grid (size={grid_size:.4f}): Trades={trades} PnL={total_pnl:+.4f}")
        
        # QUESTION 8: What's the actual distribution of returns?
        print(f"\n--- Q8: RETURN DISTRIBUTION ---")
        fade_returns = []
        for idx in spike_idx:
            entry_idx = idx + 1
            if entry_idx + 3 >= len(prices):
                continue
            entry = prices[entry_idx]
            exit_p = prices[min(entry_idx + 3, len(prices)-1)]
            if is_boom:
                ret = (entry - exit_p) / entry * 100
            else:
                ret = (exit_p - entry) / entry * 100
            fade_returns.append(ret)
        
        if fade_returns:
            print(f"  Mean: {statistics.mean(fade_returns):+.4f}%")
            print(f"  Median: {statistics.median(fade_returns):+.4f}%")
            print(f"  Std: {statistics.stdev(fade_returns):.4f}%")
            print(f"  Min: {min(fade_returns):+.4f}%")
            print(f"  Max: {max(fade_returns):+.4f}%")
            print(f"  Skew: {statistics.mean([(r-statistics.mean(fade_returns))**3 for r in fade_returns]) / statistics.stdev(fade_returns)**3:.2f}")

if __name__ == "__main__":
    fresh_analysis()
