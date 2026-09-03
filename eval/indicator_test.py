#!/usr/bin/env python3
"""
INDICATOR TEST
Test every spike prediction indicator people use on YouTube/forums.
RSI, EMA, Stochastic, MACD, tick count, volume, etc.
"""
import pandas as pd
import numpy as np
from pathlib import Path

def rsi(prices, period=14):
    delta = prices.diff()
    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    return 100 - (100 / (1 + rs))

def ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def stoch(df, k_period=14, d_period=3):
    low_min = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    k = 100 * (df["close"] - low_min) / (high_max - low_min)
    d = k.rolling(d_period).mean()
    return k, d

def macd(prices, fast=12, slow=26, signal=9):
    ema_fast = ema(prices, fast)
    ema_slow = ema(prices, slow)
    macd_line = ema_fast - ema_slow
    signal_line = ema(macd_line, signal)
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram

def analyze(symbol, slug):
    print(f"\n{'='*80}")
    print(f"  INDICATOR TEST: {symbol}")
    print(f"{'='*80}")
    
    path = Path("data") / f"candles_{slug}_1min.parquet"
    df = pd.read_parquet(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["bar_range"] = df["high"] - df["low"]
    nb = len(df)
    
    df["median_range"] = df["bar_range"].rolling(100, min_periods=20).median()
    df["is_spike"] = df["bar_range"] > 3.0 * df["median_range"]
    
    up_pct = (df["close"] > df["open"]).mean()
    drift_up = up_pct > 0.5
    
    spikes = df[df["is_spike"]]
    if drift_up:
        target_idx = spikes[spikes["close"] < spikes["open"]].index.tolist()
    else:
        target_idx = spikes[spikes["close"] > spikes["open"]].index.tolist()
    
    target_pos = [df.index.get_loc(i) for i in target_idx]
    spike_set = set(target_pos)
    print(f"  Target spikes: {len(target_pos)}")
    
    # Calculate all indicators
    df["rsi14"] = rsi(df["close"], 14)
    df["rsi7"] = rsi(df["close"], 7)
    df["ema20"] = ema(df["close"], 20)
    df["ema50"] = ema(df["close"], 50)
    df["ema200"] = ema(df["close"], 200)
    df["k"], df["d"] = stoch(df, 14, 3)
    df["macd_line"], df["macd_signal"], df["macd_hist"] = macd(df["close"])
    df["tick_count"] = 0
    tc = 0
    for i in range(nb):
        if i in spike_set:
            tc = 0
        else:
            tc += 1
        df.iloc[i, df.columns.get_loc("tick_count")] = tc
    
    # ═══════════════════════════════════════════════════════════
    # TEST EACH INDICATOR
    # ═══════════════════════════════════════════════════════════
    print(f"\n  INDICATOR RESULTS (did spike happen within N bars of signal?)")
    print(f"  {'-'*70}")
    
    signals = {}
    
    # 1. RSI Overbought/Oversold
    if drift_up:
        # Crash: we want RSI overbought before spike
        signals["RSI14 > 70"] = (df["rsi14"] > 70).values
        signals["RSI14 > 80"] = (df["rsi14"] > 80).values
        signals["RSI14 > 90"] = (df["rsi14"] > 90).values
        signals["RSI7 > 70"] = (df["rsi7"] > 70).values
        signals["RSI7 > 80"] = (df["rsi7"] > 80).values
    else:
        # Boom: we want RSI oversold before spike
        signals["RSI14 < 30"] = (df["rsi14"] < 30).values
        signals["RSI14 < 20"] = (df["rsi14"] < 20).values
        signals["RSI14 < 10"] = (df["rsi14"] < 10).values
        signals["RSI7 < 30"] = (df["rsi7"] < 30).values
        signals["RSI7 < 20"] = (df["rsi7"] < 20).values
    
    # 2. Stochastic
    if drift_up:
        signals["Stoch K > 80"] = (df["k"] > 80).values
        signals["Stoch K > 90"] = (df["k"] > 90).values
        signals["Stoch K > D (bull)"] = (df["k"] > df["d"]).values
    else:
        signals["Stoch K < 20"] = (df["k"] < 20).values
        signals["Stoch K < 10"] = (df["k"] < 10).values
        signals["Stoch K < D (bear)"] = (df["k"] < df["d"]).values
    
    # 3. EMA Trend
    if drift_up:
        signals["Price > EMA200"] = (df["close"] > df["ema200"]).values
        signals["Price < EMA200"] = (df["close"] < df["ema200"]).values
        signals["EMA20 > EMA50"] = (df["ema20"] > df["ema50"]).values
    else:
        signals["Price < EMA200"] = (df["close"] < df["ema200"]).values
        signals["Price > EMA200"] = (df["close"] > df["ema200"]).values
        signals["EMA20 < EMA50"] = (df["ema20"] < df["ema50"]).values
    
    # 4. MACD
    signals["MACD > Signal"] = (df["macd_line"] > df["macd_signal"]).values
    signals["MACD < Signal"] = (df["macd_line"] < df["macd_signal"]).values
    signals["MACD Hist > 0"] = (df["macd_hist"] > 0).values
    
    # 5. Tick Count
    signals["Tick count > 100"] = (df["tick_count"] > 100).values
    signals["Tick count > 200"] = (df["tick_count"] > 200).values
    signals["Tick count > 500"] = (df["tick_count"] > 500).values
    signals["Tick count > 700"] = (df["tick_count"] > 700).values
    
    # 6. Bar range (small bars = exhaustion?)
    signals["Range < 50% median"] = (df["bar_range"] < 0.5 * df["median_range"]).values
    signals["Range < 25% median"] = (df["bar_range"] < 0.25 * df["median_range"]).values
    
    # 7. Consecutive same-direction bars
    if drift_up:
        same_dir = (df["close"] > df["open"]).astype(int)
    else:
        same_dir = (df["close"] < df["open"]).astype(int)
    
    consec = []
    count = 0
    for v in same_dir:
        if v:
            count += 1
        else:
            count = 0
        consec.append(count)
    df["consec"] = consec
    
    signals["Consec >= 3"] = (df["consec"] >= 3).values
    signals["Consec >= 5"] = (df["consec"] >= 5).values
    signals["Consec >= 7"] = (df["consec"] >= 7).values
    signals["Consec >= 10"] = (df["consec"] >= 10).values
    
    # 8. Price distance from EMA
    df["ema200_dist"] = (df["close"] - df["ema200"]).abs() / df["close"] * 100
    signals["EMA200 dist > 0.1%"] = (df["ema200_dist"] > 0.1).values
    signals["EMA200 dist > 0.5%"] = (df["ema200_dist"] > 0.5).values
    
    # ═══════════════════════════════════════════════════════════
    # EVALUATE EACH SIGNAL
    # ═══════════════════════════════════════════════════════════
    results = []
    
    for name, sig in signals.items():
        # For each signal, check if a spike happened within 1-5 bars AFTER
        signal_bars = np.where(sig)[0]
        if len(signal_bars) == 0:
            continue
        
        # Count how many signals had a spike within N bars
        for hold in [1, 3, 5]:
            hits = 0
            for sb in signal_bars:
                for h in range(1, hold + 1):
                    if sb + h < nb and (sb + h) in spike_set:
                        hits += 1
                        break
            
            total_signals = len(signal_bars)
            hit_rate = hits / total_signals if total_signals > 0 else 0
            
            # Baseline: what's the random chance of a spike within hold bars?
            all_spike_bars = len(spike_set)
            baseline = min(1.0, all_spike_bars * hold / nb)
            
            lift = hit_rate / max(baseline, 0.001)
            
            results.append({
                "signal": name,
                "hold": hold,
                "total": total_signals,
                "hits": hits,
                "hit_rate": hit_rate,
                "baseline": baseline,
                "lift": lift
            })
    
    # Print results sorted by lift
    results.sort(key=lambda x: x["lift"], reverse=True)
    
    print(f"\n  TOP SIGNALS BY LIFT (actual vs random)")
    print(f"  {'-'*70}")
    print(f"  {'Signal':<25} {'Hold':>4} {'Signals':>8} {'Hits':>6} {'HitRate':>8} {'Base':>6} {'Lift':>6}")
    print(f"  {'-'*70}")
    
    for r in results[:30]:
        marker = " ***" if r["lift"] > 1.5 else " **" if r["lift"] > 1.2 else " *" if r["lift"] > 1.1 else ""
        print(f"  {r['signal']:<25} {r['hold']:>4} {r['total']:>8} {r['hits']:>6} {r['hit_rate']:>7.1%} {r['baseline']:>5.1%} {r['lift']:>5.2f}x{marker}")
    
    # ═══════════════════════════════════════════════════════════
    # COMBINE TOP SIGNALS
    # ═══════════════════════════════════════════════════════════
    print(f"\n  COMBINED SIGNALS (intersection)")
    print(f"  {'-'*70}")
    
    # Try combining RSI + Stochastic + Consec
    if drift_up:
        combo1 = (df["rsi14"] > 70) & (df["k"] > 80)
        combo2 = (df["rsi14"] > 80) & (df["k"] > 90)
        combo3 = (df["rsi14"] > 70) & (df["consec"] >= 5)
    else:
        combo1 = (df["rsi14"] < 30) & (df["k"] < 20)
        combo2 = (df["rsi14"] < 20) & (df["k"] < 10)
        combo3 = (df["rsi14"] < 30) & (df["consec"] >= 5)
    
    for name, combo in [("RSI+Stoch", combo1), ("RSI80+Stoch90", combo2), ("RSI+Consec5", combo3)]:
        combo_bars = np.where(combo.values)[0]
        if len(combo_bars) == 0:
            continue
        
        for hold in [1, 3, 5]:
            hits = 0
            for sb in combo_bars:
                for h in range(1, hold + 1):
                    if sb + h < nb and (sb + h) in spike_set:
                        hits += 1
                        break
            
            total = len(combo_bars)
            hit_rate = hits / total if total > 0 else 0
            baseline = min(1.0, len(spike_set) * hold / nb)
            lift = hit_rate / max(baseline, 0.001)
            
            print(f"  {name:<15} Hold={hold}: Signals={total:5d} Hits={hits:4d} Rate={hit_rate:.1%} Lift={lift:.2f}x")
    
    return results

# RUN
print("="*80)
print("  INDICATOR TEST: Every YouTube Strategy")
print("="*80)

for sym, slug in [("BOOM1000","boom1000"),("CRASH1000","crash1000"),
                   ("BOOM500","boom500"),("CRASH500","crash500")]:
    try:
        analyze(sym, slug)
    except Exception as e:
        print(f"\n  ERROR {sym}: {e}")
        import traceback
        traceback.print_exc()
