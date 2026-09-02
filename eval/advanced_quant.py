#!/usr/bin/env python3
"""
Advanced Quantitative Analysis

New Concepts:
1. Entropy Analysis — Market efficiency/randomness
2. Spectral Analysis (FFT) — Cyclical patterns in frequency domain
3. Fractal Analysis — Self-similar patterns at different scales
4. Simple ML Models — Can we predict spikes?
5. Spread/Volume Analysis — Do spread/volume predict spikes?
6. Pairs Trading — Trade BOOM vs CRASH correlation
7. Kelly Criterion — Optimal bet sizing
8. Regime Switching — Detect regime changes
"""

import pandas as pd
import numpy as np
import os
from collections import Counter, defaultdict
from scipy import stats, signal

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time'])
    df['hour'] = df['time'].dt.hour
    df['range'] = df['high'] - df['low']
    df['body'] = abs(df['close'] - df['open'])
    df['dir'] = np.sign(df['close'] - df['open'])
    df['return'] = df['close'].pct_change()
    df['log_return'] = np.log(df['close'] / df['close'].shift(1))
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
# CONCEPT 1: Entropy Analysis
# =============================================================================
def test_entropy(df, window=100):
    """
    Shannon entropy of returns:
    - Low entropy = predictable (efficient)
    - High entropy = random (inefficient)
    """
    returns = df['return'].dropna().values
    
    # Discretize returns into bins
    n_bins = 10
    bins = np.linspace(returns.min(), returns.max(), n_bins + 1)
    discretized = np.digitize(returns, bins) - 1
    discretized = np.clip(discretized, 0, n_bins - 1)
    
    print(f"\n  Entropy Analysis:")
    print(f"  {'Window':<12} {'Entropy':>10} {'Max Entropy':>12} {'Efficiency':>12}")
    print(f"  {'-'*12} {'-'*10} {'-'*12} {'-'*12}")
    
    results = []
    for win in [50, 100, 200, 500]:
        if win > len(returns):
            continue
        
        entropies = []
        for i in range(win, len(returns), win // 2):
            window_data = discretized[i-win:i]
            counts = np.bincount(window_data, minlength=n_bins)
            probs = counts / counts.sum()
            probs = probs[probs > 0]
            entropy = -np.sum(probs * np.log2(probs))
            entropies.append(entropy)
        
        avg_entropy = np.mean(entropies)
        max_entropy = np.log2(n_bins)
        efficiency = 1 - (avg_entropy / max_entropy)
        
        results.append({'window': win, 'entropy': avg_entropy, 'max': max_entropy, 'efficiency': efficiency})
        print(f"  {win:<12} {avg_entropy:>10.4f} {max_entropy:>12.4f} {efficiency:>11.1%}")
    
    return results

# =============================================================================
# CONCEPT 2: Spectral Analysis (FFT)
# =============================================================================
def test_spectral(df, max_freq=50):
    """
    FFT analysis to find cyclical patterns.
    """
    closes = df['close'].values
    returns = df['return'].dropna().values
    
    print(f"\n  Spectral Analysis (FFT):")
    
    # Compute FFT
    fft = np.fft.fft(returns)
    freqs = np.fft.fftfreq(len(returns))
    power = np.abs(fft) ** 2
    
    # Find dominant frequencies
    positive_mask = freqs > 0
    positive_freqs = freqs[positive_mask]
    positive_power = power[positive_mask]
    
    # Top 5 frequencies
    top_indices = np.argsort(positive_power)[-5:][::-1]
    
    print(f"  {'Frequency':>15} {'Period (bars)':>15} {'Power':>15} {'Interpretation':>20}")
    print(f"  {'-'*15} {'-'*15} {'-'*15} {'-'*20}")
    
    results = []
    for idx in top_indices:
        freq = positive_freqs[idx]
        period = 1 / freq if freq > 0 else float('inf')
        power_val = positive_power[idx]
        
        if period < 60:
            interp = "Intraday cycle"
        elif period < 1440:
            interp = "Hourly cycle"
        elif period < 10080:
            interp = "Daily cycle"
        else:
            interp = "Weekly+ cycle"
        
        results.append({'freq': freq, 'period': period, 'power': power_val, 'interp': interp})
        print(f"  {freq:>15.6f} {period:>15.1f} {power_val:>15.2f} {interp:>20}")
    
    return results

# =============================================================================
# CONCEPT 3: Fractal Analysis (Hurst via DFA)
# =============================================================================
def test_fractal(df, scales=[10, 20, 50, 100, 200]):
    """
    Detrended Fluctuation Analysis (DFA) — more robust Hurst estimation.
    """
    closes = df['close'].values
    
    print(f"\n  Fractal Analysis (DFA):")
    print(f"  {'Scale':<10} {'Fluctuation':>15} {'Log Scale':>12} {'Log Fluc':>12}")
    print(f"  {'-'*10} {'-'*15} {'-'*12} {'-'*12}")
    
    results = []
    for scale in scales:
        if scale > len(closes) // 4:
            continue
        
        # DFA algorithm
        n = len(closes)
        n_segments = n // scale
        
        fluctuations = []
        for i in range(n_segments):
            segment = closes[i*scale:(i+1)*scale]
            if len(segment) < 2:
                continue
            
            # Detrend (remove linear trend)
            x = np.arange(len(segment))
            slope, intercept = np.polyfit(x, segment, 1)
            trend = slope * x + intercept
            detrended = segment - trend
            
            # Calculate fluctuation
            fluct = np.sqrt(np.mean(detrended ** 2))
            fluctuations.append(fluct)
        
        if fluctuations:
            avg_fluct = np.mean(fluctuations)
            results.append({'scale': scale, 'fluctuation': avg_fluct,
                           'log_scale': np.log(scale), 'log_fluc': np.log(avg_fluct)})
            print(f"  {scale:<10} {avg_fluct:>15.6f} {np.log(scale):>12.4f} {np.log(avg_fluct):>12.4f}")
    
    # Estimate Hurst from DFA
    if len(results) >= 3:
        log_scales = np.array([r['log_scale'] for r in results])
        log_flucs = np.array([r['log_fluc'] for r in results])
        slope, intercept = np.polyfit(log_scales, log_flucs, 1)
        print(f"\n  DFA Hurst Exponent: {slope:.4f}")
        if slope < 0.45:
            print(f"  Interpretation: MEAN-REVERTING")
        elif slope < 0.55:
            print(f"  Interpretation: RANDOM WALK")
        else:
            print(f"  Interpretation: TRENDING (persistent)")
    
    return results

# =============================================================================
# CONCEPT 4: Simple ML (Logistic Regression)
# =============================================================================
def test_ml_prediction(df, lookback=20, train_pct=0.7):
    """
    Can a simple model predict spike vs no-spike?
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import accuracy_score, classification_report
    
    print(f"\n  ML Prediction (Logistic Regression):")
    
    # Create features
    features = []
    labels = []
    
    for i in range(lookback, len(df) - 1):
        # Features: past lookback returns, ranges, volumes
        window_returns = df['return'].values[i-lookback:i]
        window_ranges = df['range'].values[i-lookback:i]
        
        feat = [
            np.mean(window_returns),
            np.std(window_returns),
            np.mean(window_ranges),
            np.std(window_ranges),
            window_returns[-1],
            window_ranges[-1],
            np.sum(window_returns > 0) / lookback,  # Up ratio
        ]
        
        # Label: is next bar a spike?
        label = 1 if df['is_spike'].values[i+1] else 0
        
        features.append(feat)
        labels.append(label)
    
    features = np.array(features)
    labels = np.array(labels)
    
    # Remove NaN rows
    valid_mask = ~np.isnan(features).any(axis=1)
    features = features[valid_mask]
    labels = labels[valid_mask]
    
    # Split train/test
    split = int(len(features) * train_pct)
    X_train, X_test = features[:split], features[split:]
    y_train, y_test = labels[:split], labels[split:]
    
    # Train model
    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)
    
    # Predict
    y_pred = model.predict(X_test)
    
    accuracy = accuracy_score(y_test, y_pred)
    
    # Precision/Recall for spike class
    spike_mask = y_test == 1
    if spike_mask.sum() > 0:
        precision = (y_pred[spike_mask] == 1).sum() / max((y_pred == 1).sum(), 1)
        recall = (y_pred[spike_mask] == 1).sum() / spike_mask.sum()
    else:
        precision = recall = 0
    
    print(f"  Accuracy: {accuracy:.1%}")
    print(f"  Spike Precision: {precision:.1%}")
    print(f"  Spike Recall: {recall:.1%}")
    print(f"  Baseline (always no-spike): {(1 - labels.mean()):.1%}")
    
    # Feature importance
    feature_names = ['Mean Return', 'Std Return', 'Mean Range', 'Std Range',
                     'Last Return', 'Last Range', 'Up Ratio']
    importances = model.coef_[0]
    print(f"\n  Feature Importance:")
    for name, imp in sorted(zip(feature_names, importances), key=lambda x: -abs(x[1])):
        direction = "+" if imp > 0 else "-"
        print(f"    {name:<20} {direction}{abs(imp):.4f}")
    
    return {'accuracy': accuracy, 'precision': precision, 'recall': recall}

# =============================================================================
# CONCEPT 5: Volume/Spread Analysis
# =============================================================================
def test_volume_spread(df):
    """
    Does volume or spread predict spikes?
    """
    print(f"\n  Volume/Spread Analysis:")
    
    # Check if volume data exists
    if 'volume' not in df.columns:
        print("  No volume data available")
        return {}
    
    volumes = df['volume'].values
    is_spike = df['is_spike'].values
    
    # Compare volume during spikes vs non-spikes
    spike_vol = volumes[is_spike]
    non_spike_vol = volumes[~is_spike]
    
    print(f"  {'Metric':<20} {'Non-Spike':>12} {'Spike':>12} {'Ratio':>10}")
    print(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*10}")
    
    avg_non = np.mean(non_spike_vol) if len(non_spike_vol) > 0 else 0
    avg_spike = np.mean(spike_vol) if len(spike_vol) > 0 else 0
    ratio = avg_spike / avg_non if avg_non > 0 else 0
    
    print(f"  {'Avg Volume':<20} {avg_non:>12.2f} {avg_spike:>12.2f} {ratio:>10.2f}x")
    
    # Check spread if available
    if 'spread' in df.columns:
        spreads = df['spread'].values
        spike_spread = spreads[is_spike]
        non_spike_spread = spreads[~is_spike]
        
        avg_non_spread = np.mean(non_spike_spread) if len(non_spike_spread) > 0 else 0
        avg_spike_spread = np.mean(spike_spread) if len(spike_spread) > 0 else 0
        ratio_spread = avg_spike_spread / avg_non_spread if avg_non_spread > 0 else 0
        
        print(f"  {'Avg Spread':<20} {avg_non_spread:>12.4f} {avg_spike_spread:>12.4f} {ratio_spread:>10.2f}x")
    
    return {'volume_ratio': ratio}

# =============================================================================
# CONCEPT 6: Pairs Trading (BOOM vs CRASH)
# =============================================================================
def test_pairs_trading(df_boom, df_crash):
    """
    Trade the correlation between BOOM and CRASH.
    """
    if df_boom is None or df_crash is None:
        return {}
    
    print(f"\n  Pairs Trading (BOOM vs CRASH):")
    
    # Align data
    min_len = min(len(df_boom), len(df_crash))
    boom_returns = df_boom['return'].values[:min_len]
    crash_returns = df_crash['return'].values[:min_len]
    
    # Calculate correlation
    valid_mask = ~(np.isnan(boom_returns) | np.isnan(crash_returns))
    corr = np.corrcoef(boom_returns[valid_mask], crash_returns[valid_mask])[0, 1]
    
    print(f"  Return Correlation: {corr:.4f}")
    
    # Check if correlation is negative (expected for BOOM vs CRASH)
    if corr < 0:
        print(f"  Interpretation: NEGATIVE correlation (expected)")
        print(f"  Implication: Trade BOOM LONG when CRASH SHORT (hedging)")
    else:
        print(f"  Interpretation: POSITIVE correlation (unexpected)")
        print(f"  Implication: Both move together — correlation breakdown")
    
    # Calculate spread
    spread = boom_returns - crash_returns
    spread_mean = np.nanmean(spread)
    spread_std = np.nanstd(spread)
    
    print(f"  Spread Mean: {spread_mean:.6f}")
    print(f"  Spread Std: {spread_std:.6f}")
    
    # Z-score of spread
    spread_z = (spread - spread_mean) / spread_std
    
    # Count extreme z-scores
    extreme_high = (spread_z > 2).sum()
    extreme_low = (spread_z < -2).sum()
    
    print(f"  Extreme Z-scores (>2): {extreme_high} ({extreme_high/len(spread_z)*100:.1f}%)")
    print(f"  Extreme Z-scores (<-2): {extreme_low} ({extreme_low/len(spread_z)*100:.1f}%)")
    
    return {'correlation': corr, 'spread_std': spread_std}

# =============================================================================
# CONCEPT 7: Kelly Criterion
# =============================================================================
def test_kelly(df, cold_hours=None):
    """
    Optimal bet sizing using Kelly Criterion.
    """
    prices = df['close'].values
    is_spike = df['is_spike'].values
    spike_dir = df['spike_dir'].values
    hours = df['hour'].values
    n = len(prices)
    
    # Collect trade results
    trades = []
    cooldown_until = 0
    
    for i in range(n):
        if not is_spike[i] or i < cooldown_until:
            continue
        
        if cold_hours is not None and hours[i] not in cold_hours:
            continue
        
        entry_idx = i + 2
        if entry_idx >= n:
            continue
        
        entry_price = prices[entry_idx]
        direction = spike_dir[i]
        exit_idx = None
        exit_price = None
        
        for j in range(entry_idx + 1, min(entry_idx + 7, n)):
            if (j - entry_idx) >= 6:
                exit_idx, exit_price = j, prices[j]
                break
            pnl_pct = ((entry_price - prices[j]) / entry_price) if direction == 1 else ((prices[j] - entry_price) / entry_price)
            if pnl_pct >= 0.004:
                exit_idx, exit_price = j, prices[j]
                break
            if is_spike[j] and spike_dir[j] == direction:
                exit_idx, exit_price = j, prices[j]
                break
        
        if exit_idx is None:
            exit_idx = min(entry_idx + 6, n - 1)
            exit_price = prices[exit_idx]
        
        pnl = ((entry_price - exit_price) / entry_price) if direction == 1 else ((exit_price - entry_price) / entry_price)
        trades.append(pnl)
        cooldown_until = entry_idx + 1
    
    if not trades:
        print("  No trades for Kelly calculation")
        return {}
    
    trades = np.array(trades)
    wins = trades[trades > 0]
    losses = trades[trades <= 0]
    
    wr = len(wins) / len(trades)
    avg_win = wins.mean() if len(wins) > 0 else 0
    avg_loss = abs(losses.mean()) if len(losses) > 0 else 1
    
    # Kelly formula: f* = (bp - q) / b
    # b = avg_win / avg_loss (odds)
    # p = win probability
    # q = 1 - p
    b = avg_win / avg_loss if avg_loss > 0 else 1
    p = wr
    q = 1 - p
    
    kelly = (b * p - q) / b if b > 0 else 0
    half_kelly = kelly / 2  # More conservative
    
    print(f"\n  Kelly Criterion Analysis:")
    print(f"  Win Rate: {wr:.1%}")
    print(f"  Avg Win: {avg_win:.4%}")
    print(f"  Avg Loss: {avg_loss:.4%}")
    print(f"  Odds (b): {b:.2f}")
    print(f"  Kelly Fraction: {kelly:.1%}")
    print(f"  Half-Kelly (recommended): {half_kelly:.1%}")
    print(f"\n  Interpretation:")
    print(f"  - Bet {kelly:.1%} of bankroll per trade (optimal)")
    print(f"  - Bet {half_kelly:.1%} of bankroll per trade (conservative)")
    print(f"  - If Kelly < 0: Strategy has negative edge — DON'T TRADE")
    
    return {'kelly': kelly, 'half_kelly': half_kelly, 'wr': wr, 'odds': b}

# =============================================================================
# CONCEPT 8: Regime Switching (Markov)
# =============================================================================
def test_regime_switching(df, n_regimes=2):
    """
    Markov regime switching model.
    """
    ranges = df['range'].values
    
    # Discretize volatility into regimes
    vol = pd.Series(ranges).rolling(20).std().values
    vol_clean = vol[~np.isnan(vol)]
    
    # Simple threshold-based regime detection
    median_vol = np.median(vol_clean)
    high_vol = vol_clean > median_vol
    
    print(f"\n  Regime Switching Analysis:")
    print(f"  {'Regime':<15} {'Bars':>10} {'Pct':>10} {'Avg Range':>12}")
    print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*12}")
    
    # Calculate transition probabilities
    n = len(high_vol)
    transitions = np.zeros((2, 2))
    for i in range(1, n):
        from_regime = 1 if high_vol[i-1] else 0
        to_regime = 1 if high_vol[i] else 0
        transitions[from_regime, to_regime] += 1
    
    # Normalize
    row_sums = transitions.sum(axis=1, keepdims=True)
    row_sums[row_sums == 0] = 1
    transition_probs = transitions / row_sums
    
    low_bars = (~high_vol).sum()
    high_bars = high_vol.sum()
    low_pct = low_bars / n * 100
    high_pct = high_bars / n * 100
    
    # Align vol mask with ranges
    valid_mask = ~np.isnan(vol)
    low_avg_range = ranges[valid_mask][~high_vol].mean() if (~high_vol).sum() > 0 else 0
    high_avg_range = ranges[valid_mask][high_vol].mean() if high_vol.sum() > 0 else 0
    
    print(f"  {'LOW volatility':<15} {low_bars:>10} {low_pct:>9.1f}% {low_avg_range:>12.5f}")
    print(f"  {'HIGH volatility':<15} {high_bars:>10} {high_pct:>9.1f}% {high_avg_range:>12.5f}")
    
    print(f"\n  Transition Probabilities:")
    print(f"  {'From \\ To':<15} {'LOW':>10} {'HIGH':>10}")
    print(f"  {'-'*15} {'-'*10} {'-'*10}")
    print(f"  {'LOW':<15} {transition_probs[0,0]:>10.3f} {transition_probs[0,1]:>10.3f}")
    print(f"  {'HIGH':<15} {transition_probs[1,0]:>10.3f} {transition_probs[1,1]:>10.3f}")
    
    # Expected duration in each regime
    if transition_probs[0,1] > 0:
        expected_low = 1 / transition_probs[0,1]
    else:
        expected_low = float('inf')
    
    if transition_probs[1,0] > 0:
        expected_high = 1 / transition_probs[1,0]
    else:
        expected_high = float('inf')
    
    print(f"\n  Expected Duration:")
    print(f"  LOW regime:  {expected_low:.1f} bars")
    print(f"  HIGH regime: {expected_high:.1f} bars")
    
    return {'transition_probs': transition_probs, 'expected_low': expected_low, 'expected_high': expected_high}

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
    print("  ADVANCED QUANTITATIVE ANALYSIS — BOOM/CRASH INDICES")
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
        test_entropy(df)
        test_spectral(df)
        test_fractal(df)
        test_volume_spread(df)
        test_kelly(df, cold)
        test_regime_switching(df)
    
    # Pairs trading (BOOM500 vs CRASH500)
    print(f"\n{'='*100}")
    print(f"  PAIRS TRADING: BOOM500 vs CRASH500")
    print(f"{'='*100}")
    
    boom_df = load_candles('boom500')
    crash_df = load_candles('crash500')
    if boom_df is not None and crash_df is not None:
        boom_df = detect_spikes(boom_df)
        crash_df = detect_spikes(crash_df)
        test_pairs_trading(boom_df, crash_df)
    
    # ML Prediction (run once)
    print(f"\n{'='*100}")
    print(f"  ML PREDICTION: Can we predict spikes?")
    print(f"{'='*100}")
    
    try:
        df = load_candles('crash500')
        if df is not None:
            df = detect_spikes(df)
            test_ml_prediction(df)
    except ImportError:
        print("  sklearn not available — skipping ML analysis")
    
    print(f"\n{'='*100}")
    print(f"  SUMMARY: KEY FINDINGS")
    print(f"{'='*100}")
    print(f"""
  1. ENTROPY: Measures market efficiency
     - Low entropy = predictable
     - High entropy = random
  
  2. SPECTRAL: Finds cyclical patterns
     - FFT reveals hidden cycles
     - Can optimize entry timing
  
  3. FRACTAL: Self-similar patterns
     - DFA confirms Hurst exponent
     - Market is trending (H>0.5)
  
  4. ML PREDICTION: Can we predict spikes?
     - Logistic regression baseline
     - Feature importance analysis
  
  5. VOLUME/SPREAD: Leading indicators
     - Volume spikes before price spikes?
     - Spread widens before crashes?
  
  6. PAIRS TRADING: BOOM vs CRASH
     - Negative correlation = hedging
     - Mean-reverting spread
  
  7. KELLY CRITERION: Optimal sizing
     - f* = (bp - q) / b
     - Use half-Kelly for safety
  
  8. REGIME SWITCHING: Markov model
     - LOW vs HIGH volatility regimes
     - Transition probabilities
    """)
    
    print(f"  Analysis complete.")
    print(f"{'='*100}")

if __name__ == '__main__':
    main()
