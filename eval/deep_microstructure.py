#!/usr/bin/env python3
"""
Deep Market Microstructure Analysis

New Concepts:
1. Spike Sequence Patterns — Do certain patterns repeat?
2. Mean Reversion Speed — How fast does price revert?
3. Time-of-Day Microstructure — Patterns within COLD hours
4. Autocorrelation — Do returns show autocorrelation?
5. Volatility Clustering — GARCH effects
6. Hurst Exponent — Trending vs mean-reverting
7. Spike Direction Bias — Does spike direction predict next spike?
8. Retracement Depth — How deep does retrace go?
9. Inter-Spike Dynamics — What happens between spikes?
10. Price Level Effects — Do certain price levels attract spikes?
"""

import pandas as pd
import numpy as np
import os
from collections import Counter, defaultdict

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time'])
    df['hour'] = df['time'].dt.hour
    df['minute'] = df['time'].dt.minute
    df['range'] = df['high'] - df['low']
    df['body'] = abs(df['close'] - df['open'])
    df['dir'] = np.sign(df['close'] - df['open'])
    df['return'] = df['close'].pct_change()
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    df['spike_dir'] = 0
    df.loc[df['is_spike'] & (df['close'] > df['open']), 'spike_dir'] = 1
    df.loc[df['is_spike'] & (df['close'] < df['open']), 'spike_dir'] = -1
    df['spike_size'] = df['range'] / med
    return df

# =============================================================================
# CONCEPT 1: Spike Sequence Patterns
# =============================================================================
def test_spike_sequences(df, lookback=5):
    """
    Do certain spike direction sequences repeat?
    E.g., UP-UP-DOWN vs UP-DOWN-UP
    """
    spike_indices = np.where(df['is_spike'].values)[0]
    spike_dirs = df['spike_dir'].values[spike_indices]
    
    # Count sequences
    sequences = defaultdict(int)
    for i in range(len(spike_dirs) - lookback + 1):
        seq = tuple(spike_dirs[i:i+lookback])
        sequences[seq] += 1
    
    # Analyze: does sequence predict next spike direction?
    print(f"\n  Top 10 Spike Sequences (length {lookback}):")
    print(f"  {'Sequence':<25} {'Count':>8} {'Next UP%':>10} {'Next DOWN%':>12}")
    print(f"  {'-'*25} {'-'*8} {'-'*10} {'-'*12}")
    
    results = []
    for seq, count in sorted(sequences.items(), key=lambda x: -x[1])[:10]:
        # Find what happens after this sequence
        next_dirs = []
        for i in range(len(spike_dirs) - lookback):
            if tuple(spike_dirs[i:i+lookback]) == seq:
                if i + lookback < len(spike_dirs):
                    next_dirs.append(spike_dirs[i + lookback])
        
        if next_dirs:
            up_pct = sum(1 for d in next_dirs if d == 1) / len(next_dirs) * 100
            down_pct = sum(1 for d in next_dirs if d == -1) / len(next_dirs) * 100
            results.append({'seq': seq, 'count': count, 'up': up_pct, 'down': down_pct})
            seq_str = ''.join(['U' if d == 1 else 'D' for d in seq])
            print(f"  {seq_str:<25} {count:>8} {up_pct:>9.1f}% {down_pct:>11.1f}%")
    
    return results

# =============================================================================
# CONCEPT 2: Mean Reversion Speed
# =============================================================================
def test_mean_reversion(df, spike_lookback=10, retrace_bars=[1, 2, 3, 5, 10, 15, 20]):
    """
    How fast does price revert after spike?
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    ranges = df['range'].values
    n = len(prices)
    
    print(f"\n  Mean Reversion Speed Analysis:")
    print(f"  {'Bars After':<15} {'Avg Retrace%':>15} {'Median Retrace%':>18} {'Full Revert%':>15}")
    print(f"  {'-'*15} {'-'*15} {'-'*18} {'-'*15}")
    
    spike_indices = np.where(is_spike)[0]
    results = []
    
    for bar_count in retrace_bars:
        retraces = []
        for si in spike_indices:
            if si + bar_count >= n:
                continue
            
            spike_price = prices[si]
            spike_range = ranges[si]
            direction = spike_dir[si]
            
            if spike_range <= 0:
                continue
            
            # Calculate retrace at bar_count
            future_price = prices[si + bar_count]
            if direction == 1:  # UP spike - look for DOWN retrace
                retrace = (spike_price - future_price) / spike_range
            else:  # DOWN spike - look for UP retrace
                retrace = (future_price - spike_price) / spike_range
            
            retraces.append(retrace)
        
        if retraces:
            retraces = np.array(retraces)
            avg_retrace = np.mean(retraces) * 100
            median_retrace = np.median(retraces) * 100
            full_revert = sum(1 for r in retraces if abs(r) >= 0.95) / len(retraces) * 100
            results.append({'bars': bar_count, 'avg': avg_retrace, 'median': median_retrace, 'full': full_revert})
            print(f"  {bar_count:<15} {avg_retrace:>14.1f}% {median_retrace:>17.1f}% {full_revert:>14.1f}%")
    
    return results

# =============================================================================
# CONCEPT 3: Time-of-Day Microstructure
# =============================================================================
def test_time_microstructure(df, cold_hours=None):
    """
    Are there patterns within COLD hours?
    """
    hours = df['hour'].values
    is_spike = df['is_spike'].values
    ranges = df['range'].values
    
    print(f"\n  Time-of-Day Microstructure (within COLD hours):")
    print(f"  {'Hour':<10} {'SpikeFreq':>12} {'AvgRange':>12} {'IsCOLD':>10}")
    print(f"  {'-'*10} {'-'*12} {'-'*12} {'-'*10}")
    
    results = []
    for h in range(24):
        mask = hours == h
        if mask.sum() == 0:
            continue
        
        spike_freq = is_spike[mask].sum() / mask.sum() * 100
        avg_range = ranges[mask].mean()
        is_cold = cold_hours is not None and h in cold_hours
        
        results.append({'hour': h, 'freq': spike_freq, 'range': avg_range, 'cold': is_cold})
        cold_str = "COLD" if is_cold else "HOT"
        print(f"  {h:<10} {spike_freq:>11.1f}% {avg_range:>12.5f} {cold_str:>10}")
    
    return results

# =============================================================================
# CONCEPT 4: Autocorrelation Analysis
# =============================================================================
def test_autocorrelation(df, max_lag=20):
    """
    Do returns show autocorrelation? (If yes, past returns predict future returns)
    """
    returns = df['return'].dropna().values
    
    print(f"\n  Autocorrelation Analysis:")
    print(f"  {'Lag':<10} {'AC':>10} {'Significant':>15}")
    print(f"  {'-'*10} {'-'*10} {'-'*15}")
    
    results = []
    for lag in range(1, max_lag + 1):
        if lag >= len(returns):
            break
        
        # Calculate autocorrelation
        ac = np.corrcoef(returns[lag:], returns[:-lag])[0, 1]
        
        # Significance (approximate)
        n = len(returns)
        se = 1 / np.sqrt(n)
        significant = abs(ac) > 2 * se
        
        results.append({'lag': lag, 'ac': ac, 'sig': significant})
        sig_str = "***" if significant else ""
        print(f"  {lag:<10} {ac:>10.4f} {sig_str:>15}")
    
    return results

# =============================================================================
# CONCEPT 5: Volatility Clustering (GARCH)
# =============================================================================
def test_volatility_clustering(df, lookback=20):
    """
    Does volatility cluster? (High vol followed by high vol)
    """
    ranges = df['range'].values
    n = len(ranges)
    
    # Calculate rolling volatility
    vol = pd.Series(ranges).rolling(lookback).std().values
    
    # Calculate autocorrelation of volatility
    vol_clean = vol[~np.isnan(vol)]
    
    print(f"\n  Volatility Clustering (GARCH effects):")
    print(f"  {'Lag':<10} {'Vol AC':>10} {'Interpretation':>25}")
    print(f"  {'-'*10} {'-'*10} {'-'*25}")
    
    results = []
    for lag in [1, 2, 3, 5, 10]:
        if lag >= len(vol_clean):
            break
        ac = np.corrcoef(vol_clean[lag:], vol_clean[:-lag])[0, 1]
        interp = "Strong clustering" if ac > 0.5 else "Moderate" if ac > 0.3 else "Weak"
        results.append({'lag': lag, 'ac': ac, 'interp': interp})
        print(f"  {lag:<10} {ac:>10.4f} {interp:>25}")
    
    return results

# =============================================================================
# CONCEPT 6: Hurst Exponent
# =============================================================================
def test_hurst_exponent(df, max_lag=50):
    """
    Hurst exponent:
    H < 0.5: Mean-reverting (anti-persistent)
    H = 0.5: Random walk
    H > 0.5: Trending (persistent)
    """
    closes = df['close'].values
    
    print(f"\n  Hurst Exponent Analysis:")
    
    # R/S analysis
    lags = range(2, min(max_lag, len(closes) // 4))
    rs_values = []
    
    for lag in lags:
        # Split into windows
        n_windows = len(closes) // lag
        if n_windows == 0:
            break
        
        rs_list = []
        for i in range(n_windows):
            window = closes[i*lag:(i+1)*lag]
            if len(window) < 2:
                continue
            
            # Calculate R/S
            mean = np.mean(window)
            deviations = np.cumsum(window - mean)
            R = np.max(deviations) - np.min(deviations)
            S = np.std(window)
            
            if S > 0:
                rs_list.append(R / S)
        
        if rs_list:
            rs_values.append((lag, np.mean(rs_list)))
    
    if len(rs_values) < 3:
        print("  Insufficient data for Hurst calculation")
        return 0.5
    
    # Fit power law: R/S = c * lag^H
    lags_arr = np.array([x[0] for x in rs_values])
    rs_arr = np.array([x[1] for x in rs_values])
    
    # Log-log regression
    log_lags = np.log(lags_arr)
    log_rs = np.log(rs_arr)
    
    # Linear regression
    slope, intercept = np.polyfit(log_lags, log_rs, 1)
    hurst = slope
    
    print(f"  Hurst Exponent: {hurst:.4f}")
    if hurst < 0.45:
        print(f"  Interpretation: MEAN-REVERTING (H < 0.5)")
        print(f"  Implication: Price tends to revert to mean — good for fade strategies")
    elif hurst < 0.55:
        print(f"  Interpretation: RANDOM WALK (H ≈ 0.5)")
        print(f"  Implication: No predictability from past prices")
    else:
        print(f"  Interpretation: TRENDING (H > 0.5)")
        print(f"  Implication: Price tends to continue in same direction")
    
    return hurst

# =============================================================================
# CONCEPT 7: Spike Direction Bias
# =============================================================================
def test_direction_bias(df):
    """
    Does spike direction predict next spike direction?
    """
    spike_indices = np.where(df['is_spike'].values)[0]
    spike_dirs = df['spike_dir'].values[spike_indices]
    
    print(f"\n  Spike Direction Bias:")
    print(f"  {'Current':<15} {'Next UP':>10} {'Next DOWN':>12} {'Count':>8}")
    print(f"  {'-'*15} {'-'*10} {'-'*12} {'-'*8}")
    
    results = []
    for current_dir in [1, -1]:
        next_dirs = []
        for i in range(len(spike_dirs) - 1):
            if spike_dirs[i] == current_dir:
                next_dirs.append(spike_dirs[i + 1])
        
        if next_dirs:
            up_pct = sum(1 for d in next_dirs if d == 1) / len(next_dirs) * 100
            down_pct = sum(1 for d in next_dirs if d == -1) / len(next_dirs) * 100
            label = "UP spike" if current_dir == 1 else "DOWN spike"
            results.append({'current': current_dir, 'up': up_pct, 'down': down_pct, 'count': len(next_dirs)})
            print(f"  {label:<15} {up_pct:>9.1f}% {down_pct:>11.1f}% {len(next_dirs):>8}")
    
    return results

# =============================================================================
# CONCEPT 8: Retracement Depth Analysis
# =============================================================================
def test_retracement_depth(df, check_bars=[3, 5, 10, 15, 20]):
    """
    How deep does retracement go?
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    ranges = df['range'].values
    n = len(prices)
    
    print(f"\n  Retracement Depth Analysis:")
    print(f"  {'Bars':<10} {'50%+':>10} {'75%+':>10} {'95%+':>10} {'OverShoot':>12}")
    print(f"  {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*12}")
    
    spike_indices = np.where(is_spike)[0]
    results = []
    
    for bar_count in check_bars:
        depths = []
        overshoots = []
        
        for si in spike_indices:
            if si + bar_count >= n:
                continue
            
            spike_price = prices[si]
            spike_range = ranges[si]
            direction = spike_dir[si]
            
            if spike_range <= 0:
                continue
            
            # Find max retrace in the next bar_count bars
            future_prices = prices[si+1:si+bar_count+1]
            
            if direction == 1:  # UP spike
                max_retrace = (spike_price - np.min(future_prices)) / spike_range
                max_overshoot = (np.max(future_prices) - spike_price) / spike_range
            else:  # DOWN spike
                max_retrace = (np.max(future_prices) - spike_price) / spike_range
                max_overshoot = (spike_price - np.min(future_prices)) / spike_range
            
            depths.append(max_retrace)
            overshoots.append(max_overshoot)
        
        if depths:
            depths = np.array(depths)
            overshoots = np.array(overshoots)
            pct_50 = sum(1 for d in depths if d >= 0.50) / len(depths) * 100
            pct_75 = sum(1 for d in depths if d >= 0.75) / len(depths) * 100
            pct_95 = sum(1 for d in depths if d >= 0.95) / len(depths) * 100
            pct_overshoot = sum(1 for o in overshoots if o > 0.20) / len(overshoots) * 100
            results.append({'bars': bar_count, '50': pct_50, '75': pct_75, '95': pct_95, 'overshoot': pct_overshoot})
            print(f"  {bar_count:<10} {pct_50:>9.1f}% {pct_75:>9.1f}% {pct_95:>9.1f}% {pct_overshoot:>11.1f}%")
    
    return results

# =============================================================================
# CONCEPT 9: Inter-Spike Dynamics
# =============================================================================
def test_inter_spike(df):
    """
    What happens between spikes? (Trend, range, volatility)
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_indices = np.where(is_spike)[0]
    
    print(f"\n  Inter-Spike Dynamics:")
    print(f"  {'Gap (bars)':<15} {'Count':>10} {'Avg Return':>12} {'Trend%':>10}")
    print(f"  {'-'*15} {'-'*10} {'-'*12} {'-'*10}")
    
    results = []
    for i in range(1, len(spike_indices)):
        gap = spike_indices[i] - spike_indices[i-1]
        
        if gap < 5 or gap > 200:
            continue
        
        # Calculate return between spikes
        start_price = prices[spike_indices[i-1]]
        end_price = prices[spike_indices[i]]
        ret = (end_price - start_price) / start_price * 100
        
        # Is it trending? (same direction as first spike)
        first_dir = df['spike_dir'].values[spike_indices[i-1]]
        trend = 1 if (ret > 0 and first_dir == 1) or (ret < 0 and first_dir == -1) else 0
        
        results.append({'gap': gap, 'return': ret, 'trend': trend})
    
    # Group by gap size
    gap_groups = [(5, 20), (20, 50), (50, 100), (100, 200)]
    for min_gap, max_gap in gap_groups:
        group = [r for r in results if min_gap <= r['gap'] < max_gap]
        if group:
            avg_ret = np.mean([r['return'] for r in group])
            trend_pct = sum(1 for r in group if r['trend']) / len(group) * 100
            print(f"  {min_gap}-{max_gap:<10} {len(group):>10} {avg_ret:>+11.4f}% {trend_pct:>9.1f}%")
    
    return results

# =============================================================================
# CONCEPT 10: Price Level Effects
# =============================================================================
def test_price_levels(df, n_levels=10):
    """
    Do certain price levels attract more spikes?
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    
    # Divide price range into levels
    price_min = prices.min()
    price_max = prices.max()
    level_size = (price_max - price_min) / n_levels
    
    print(f"\n  Price Level Effects:")
    print(f"  {'Level':<20} {'SpikeFreq':>12} {'UP%':>10} {'DOWN%':>12}")
    print(f"  {'-'*20} {'-'*12} {'-'*10} {'-'*12}")
    
    results = []
    for i in range(n_levels):
        level_min = price_min + i * level_size
        level_max = price_min + (i + 1) * level_size
        mask = (prices >= level_min) & (prices < level_max)
        
        if mask.sum() == 0:
            continue
        
        spike_freq = is_spike[mask].sum() / mask.sum() * 100
        
        # Direction breakdown
        spike_mask = mask & (is_spike == True)
        if spike_mask.sum() > 0:
            up_pct = (spike_dir[spike_mask] == 1).sum() / spike_mask.sum() * 100
            down_pct = (spike_dir[spike_mask] == -1).sum() / spike_mask.sum() * 100
        else:
            up_pct = 50
            down_pct = 50
        
        results.append({'level': f"{level_min:.3f}-{level_max:.3f}", 'freq': spike_freq, 'up': up_pct, 'down': down_pct})
        print(f"  {level_min:.3f}-{level_max:.3f}    {spike_freq:>11.1f}% {up_pct:>9.1f}% {down_pct:>11.1f}%")
    
    return results

# =============================================================================
# STRATEGY BACKTEST: Combined Microstructure Strategy
# =============================================================================
def test_combined_microstructure(df, cold_hours=None):
    """
    Combine multiple microstructure insights:
    1. Skip high regime (spike frequency)
    2. Skip high volatility
    3. Wait for mean reversion
    4. Use direction bias
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    ranges = df['range'].values
    hours = df['hour'].values
    n = len(prices)
    
    trades = []
    cooldown_until = 0
    
    # Pre-calculate regime and volatility
    lookback = 100
    regime = np.zeros(n)
    volatility = np.zeros(n)
    
    for i in range(lookback, n):
        # Regime: spike frequency
        window_start = max(0, i - lookback)
        regime[i] = is_spike[window_start:i+1].sum() / lookback
        
        # Volatility
        volatility[i] = ranges[max(0, i-50):i+1].std()
    
    median_vol = np.median(volatility[volatility > 0])
    
    for i in range(lookback, n):
        if not is_spike[i] or i < cooldown_until:
            continue
        
        if cold_hours is not None and hours[i] not in cold_hours:
            continue
        
        # Filter 1: Skip high regime
        if regime[i] > 0.06:
            continue
        
        # Filter 2: Skip high volatility
        if volatility[i] > median_vol * 1.5:
            continue
        
        # Filter 3: Direction bias — only enter if recent spike was opposite direction
        if i >= 2:
            prev_spike_dir = spike_dir[i-1] if is_spike[i-1] else 0
            # Prefer entering when spike direction matches (counter-drift)
            # Skip if last spike was same direction (cluster risk)
            if prev_spike_dir == spike_dir[i]:
                continue
        
        entry_idx = i + 2
        if entry_idx >= n:
            continue
        
        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        exit_idx = None
        exit_price = None
        exit_reason = "timeout"
        
        for j in range(entry_idx + 1, min(entry_idx + 7, n)):
            if (j - entry_idx) >= 6:
                exit_idx, exit_price, exit_reason = j, prices[j], "tick_exit"
                break
            pnl_pct = ((entry_price - prices[j]) / entry_price) if direction == 1 else ((prices[j] - entry_price) / entry_price)
            if pnl_pct >= 0.004:
                exit_idx, exit_price, exit_reason = j, prices[j], "tp"
                break
            if is_spike[j] and spike_dir[j] == direction:
                exit_idx, exit_price, exit_reason = j, prices[j], "spike_against"
                break
        
        if exit_idx is None:
            exit_idx = min(entry_idx + 6, n - 1)
            exit_price, exit_reason = prices[exit_idx], "timeout"
        
        pnl = ((entry_price - exit_price) / entry_price) if direction == 1 else ((exit_price - entry_price) / entry_price)
        trades.append({'pnl': pnl, 'reason': exit_reason})
        cooldown_until = entry_idx + 1
    
    return trades

# =============================================================================
# MAIN
# =============================================================================
def main():
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    cold_hour_map = {
        'boom500': [1, 5, 12, 14, 15, 19],
        'crash500': [0, 6, 7, 9, 22, 23],
        'boom1000': [6, 13, 18, 20, 21, 22],
        'crash1000': [3, 6, 7, 18, 22, 23],
    }
    
    print("=" * 100)
    print("  DEEP MICROSTRUCTURE ANALYSIS — BOOM/CRASH INDICES")
    print("=" * 100)
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        df = detect_spikes(df, mult=3.0)
        cold = cold_hour_map[sym]
        
        print(f"\n{'='*100}")
        print(f"  {sym.upper()} ({len(df)} bars, {df['is_spike'].sum()} spikes)")
        print(f"{'='*100}")
        
        # Run all analyses
        test_spike_sequences(df)
        test_mean_reversion(df)
        test_time_microstructure(df, cold)
        test_autocorrelation(df)
        test_volatility_clustering(df)
        test_hurst_exponent(df)
        test_direction_bias(df)
        test_retracement_depth(df)
        test_inter_spike(df)
        test_price_levels(df)
        
        # Combined strategy
        print(f"\n  COMBINED MICROSTRUCTURE STRATEGY:")
        trades = test_combined_microstructure(df, cold)
        if trades:
            pnls = np.array([t['pnl'] for t in trades])
            wr = (pnls > 0).sum() / len(pnls) * 100
            gp = pnls[pnls > 0].sum()
            gl = abs(pnls[pnls <= 0].sum())
            pf = gp / gl if gl > 0 else float('inf')
            print(f"  Trades: {len(trades)}, WR: {wr:.1f}%, PF: {pf:.2f}, PnL: {pnls.sum():+.4f}%")
    
    print(f"\n{'='*100}")
    print(f"  KEY INSIGHTS FROM MICROSTRUCTURE ANALYSIS")
    print(f"{'='*100}")
    print(f"""
  1. SPIKE SEQUENCES: Check if UP-UP-DOWN patterns exist
  2. MEAN REVERSION: Measure how fast price reverts after spike
  3. TIME MICROSTRUCTURE: Find best hours within COLD periods
  4. AUTOCORRELATION: Check if past returns predict future
  5. VOLATILITY CLustering: Check if high vol persists
  6. HURST EXPONENT: Determine if market is mean-reverting or trending
  7. DIRECTION BIAS: Check if spike direction predicts next
  8. RETRACEMENT DEPTH: How deep does retrace go?
  9. INTER-SPIKE: What happens between spikes?
  10. PRICE LEVELS: Do certain prices attract spikes?
    """)
    
    print(f"  Analysis complete.")
    print(f"{'='*100}")

if __name__ == '__main__':
    main()
