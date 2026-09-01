# -*- coding: utf-8 -*-
"""
eval/analyze_1000_buildup.py -- Estimate buildup for BOOM1000/CRASH1000 from M1 data

We don't have tick data for 1000 indices, but we can:
1. Detect spikes from M1 candles
2. Measure bar-range patterns before spikes
3. Estimate how many ticks of buildup would precede each spike
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')


def load_candles(symbol, timeframe="1min"):
    path = Path(f"data/candles_{symbol.lower()}_{timeframe}.parquet")
    if not path.exists():
        return None
    return pd.read_parquet(path)


def detect_spikes(df, threshold_mult=3.0):
    """Detect spikes from M1 candle ranges."""
    ranges = df['high'] - df['low']
    median = ranges.median()
    threshold = median * threshold_mult
    spike_mask = ranges > threshold
    return spike_mask, median, threshold


def analyze_buildup(df, spike_mask, symbol):
    """Analyze bar patterns before each spike."""
    print(f"\n{'='*70}")
    print(f"  {symbol} -- BUILDUP ANALYSIS FROM M1 DATA")
    print(f"{'='*70}")
    
    closes = df['close'].values
    opens = df['open'].values
    highs = df['high'].values
    lows = df['low'].values
    ranges = (highs - lows)
    spike_indices = np.where(spike_mask)[0]
    
    print(f"  Total M1 bars: {len(df)}")
    print(f"  Total spikes:  {len(spike_indices)}")
    print(f"  Spike rate:    {len(spike_indices)/len(df)*100:.1f}% of bars")
    
    # For each spike, analyze the bars BEFORE it
    buildup_direction = []  # True = price was moving toward spike direction
    
    for idx in spike_indices:
        if idx < 5:
            continue
        
        # Is this a BOOM spike (close > open) or CRASH spike (close < open)?
        is_boom_spike = closes[idx] > opens[idx]
        
        # Count consecutive bars before spike where price moves in spike direction
        consecutive = 0
        for j in range(idx - 1, max(idx - 50, -1), -1):
            if is_boom_spike:
                # BOOM spike UP: buildup = bars where close > open (uptrend)
                if closes[j] > opens[j]:
                    consecutive += 1
                else:
                    break
            else:
                # CRASH spike DOWN: buildup = bars where close < open (downtrend)
                if closes[j] < opens[j]:
                    consecutive += 1
                else:
                    break
        
        buildup_direction.append({
            'index': idx,
            'is_boom_spike': is_boom_spike,
            'consecutive_bars': consecutive,
            'spike_range': ranges[idx],
        })
    
    if not buildup_direction:
        print("  No spikes with enough lookback")
        return
    
    # Statistics
    builds = [b['consecutive_bars'] for b in buildup_direction]
    builds = np.array(builds)
    
    print(f"\n  BUILDUP BARS BEFORE SPIKE:")
    print(f"    Mean:   {builds.mean():.1f} bars")
    print(f"    Median: {np.median(builds):.0f} bars")
    print(f"    Max:    {builds.max()}")
    
    # Distribution
    print(f"\n  DISTRIBUTION:")
    for t in [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 15, 20, 25, 30]:
        count = (builds >= t).sum()
        pct = count / len(builds) * 100
        print(f"    >= {t:2d} bars: {count:5d} ({pct:5.1f}%)")
    
    # Estimate tick buildup (1000 = ~2x ticks vs 500)
    # If 500 index has ~60 ticks per M1 bar on average
    # 1000 index has more ticks per bar (longer intervals)
    # But from M1 data, we can only measure bar-level buildup
    
    # Compare to BOOM/CRASH 500
    print(f"\n  ESTIMATE: If each M1 bar = ~60 ticks:")
    print(f"    Mean buildup: ~{builds.mean() * 60:.0f} ticks")
    print(f"    Median buildup: ~{np.median(builds) * 60:.0f} ticks")
    
    # Spike size comparison
    spike_ranges = np.array([b['spike_range'] for b in buildup_direction])
    print(f"\n  SPIKE SIZE:")
    print(f"    Mean range:  {spike_ranges.mean():.4f}")
    print(f"    Median range: {np.median(spike_ranges):.4f}")
    
    return buildup_direction


def main():
    print("\n" + "=" * 70)
    print("  BOOM/CRASH 1000 -- BUILDUP ESTIMATION")
    print("=" * 70)
    print("""
  We don't have tick data for 1000 indices.
  Analyzing M1 candle data to estimate buildup patterns.
  
  Key question: Do 1000 indices have MORE drift before spikes?
    """)
    
    results = {}
    
    for symbol in ["boom1000", "crash1000"]:
        df = load_candles(symbol, "1min")
        if df is None:
            print(f"\n  {symbol}: No data found")
            continue
        
        spike_mask, median, threshold = detect_spikes(df)
        buildup = analyze_buildup(df, spike_mask, symbol)
        if buildup:
            results[symbol] = buildup
    
    # Compare 500 vs 1000
    if len(results) == 2:
        print(f"\n{'='*70}")
        print(f"  500 vs 1000 COMPARISON")
        print(f"{'='*70}")
        
        for sym, builds in results.items():
            bars = [b['consecutive_bars'] for b in builds]
            print(f"\n  {sym}:")
            print(f"    Mean buildup:   {np.mean(bars):.1f} bars (~{np.mean(bars)*60:.0f} ticks)")
            print(f"    Median buildup: {np.median(bars):.0f} bars (~{np.median(bars)*60:.0f} ticks)")
            print(f"    >=8 bars:       {(np.array(bars) >= 8).sum()}/{len(bars)} ({(np.array(bars) >= 8).sum()/len(bars)*100:.1f}%)")
    
    print(f"\n{'='*70}")
    print(f"  VERDICT")
    print(f"{'='*70}")
    print("""
  If 1000 indices show MORE buildup bars, the velocity exit
  would be MORE effective on them (higher catch rate).
  
  The velocity threshold of 12 ticks might need adjustment:
  - 500 indices: threshold=12 (current)
  - 1000 indices: threshold might be LOWER (less buildup needed)
                  or HIGHER (more buildup available)
  
  Without tick data, we can only estimate. To get real numbers,
  we need to pull tick data for BOOM1000/CRASH1000.
    """)


if __name__ == "__main__":
    main()
