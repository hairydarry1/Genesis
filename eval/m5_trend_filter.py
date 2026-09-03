#!/usr/bin/env python3
"""
M5 TREND FILTER TEST
Does the M5 timeframe trend improve M1 spike fade entries?
"""
import pandas as pd
import numpy as np
from pathlib import Path

def ema(prices, period):
    return prices.ewm(span=period, adjust=False).mean()

def analyze(symbol, slug):
    print(f"\n{'='*80}")
    print(f"  M5 TREND FILTER: {symbol}")
    print(f"{'='*80}")
    
    # Load M1 data
    path = Path("data") / f"candles_{slug}_1min.parquet"
    df = pd.read_parquet(path)
    df.columns = [c.strip().lower() for c in df.columns]
    df["bar_range"] = df["high"] - df["low"]
    nb = len(df)
    
    # Detect M1 spikes
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
    print(f"  M1 bars: {nb} | Target spikes: {len(target_pos)}")
    print(f"  Drift: {'UP' if drift_up else 'DOWN'}")
    
    # ═══════════════════════════════════════════════════════════
    # RESAMPLE TO M5
    # ═══════════════════════════════════════════════════════════
    df.index = pd.to_datetime(df["time"])
    m5 = df.resample("5min").agg({
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "bar_range": "sum"
    }).dropna()
    
    print(f"  M5 bars: {len(m5)}")
    
    # Calculate M5 EMAs
    m5["ema20"] = ema(m5["close"], 20)
    m5["ema50"] = ema(m5["close"], 50)
    m5["ema200"] = ema(m5["close"], 200)
    m5["rsi14"] = 0  # placeholder
    
    # RSI on M5
    delta = m5["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
    rs = gain / loss
    m5["rsi14"] = 100 - (100 / (1 + rs))
    
    # Map M5 trend back to M1 bars
    # For each M1 bar, find which M5 bar it belongs to
    m5_times = m5.index
    
    def get_m5_trend(m1_time):
        """Get M5 trend at the time of this M1 bar."""
        # Find the M5 bar that contains this M1 time
        idx = m5_times.searchsorted(m1_time)
        if idx >= len(m5_times):
            idx = len(m5_times) - 1
        if idx < 0:
            idx = 0
        
        row = m5.iloc[idx]
        
        # M5 trend: price vs EMAs
        above_ema200 = row["close"] > row["ema200"]
        above_ema50 = row["close"] > row["ema50"]
        ema20_above_ema50 = row["ema20"] > row["ema50"]
        rsi = row["rsi14"]
        
        return {
            "above_ema200": above_ema200,
            "above_ema50": above_ema50,
            "ema20_above_ema50": ema20_above_ema50,
            "rsi": rsi
        }
    
    # Get M5 trend for each M1 spike
    m5_trends = []
    for pos in target_pos:
        m1_time = df.iloc[pos]["time"]
        if isinstance(m1_time, str):
            m1_time = pd.Timestamp(m1_time)
        trend = get_m5_trend(m1_time)
        m5_trends.append(trend)
    
    # ═══════════════════════════════════════════════════════════
    # TEST 1: M5 TREND FILTER (EMA200)
    # ═══════════════════════════════════════════════════════════
    print(f"\n  TEST 1: M5 EMA200 TREND FILTER")
    print(f"  {'-'*60}")
    
    closes = df["close"].values
    
    for hold in [1, 2, 3, 5, 7]:
        # All trades
        profits_all = []
        for pos in target_pos:
            if pos + hold >= nb:
                continue
            e = closes[pos]
            x = closes[pos + hold]
            profits_all.append((e - x) if drift_up else (x - e))
        
        # With M5 trend filter
        profits_filtered = []
        for i, pos in enumerate(target_pos):
            if pos + hold >= nb:
                continue
            
            trend = m5_trends[i]
            
            # For CRASH (drift UP, we LONG): want M5 in uptrend
            # For BOOM (drift DOWN, we SHORT): want M5 in downtrend
            if drift_up:
                if trend["above_ema200"] and trend["ema20_above_ema50"]:
                    pass  # Good trend, take trade
                else:
                    continue
            else:
                if not trend["above_ema200"] and not trend["ema20_above_ema50"]:
                    pass  # Good trend, take trade
                else:
                    continue
            
            e = closes[pos]
            x = closes[pos + hold]
            profits_filtered.append((e - x) if drift_up else (x - e))
        
        if profits_all and profits_filtered:
            wr_all = np.mean([p > 0 for p in profits_all])
            w_all = sum(p for p in profits_all if p > 0)
            l_all = abs(sum(p for p in profits_all if p < 0))
            pf_all = w_all / max(l_all, 1e-10)
            
            wr_filt = np.mean([p > 0 for p in profits_filtered])
            w_filt = sum(p for p in profits_filtered if p > 0)
            l_filt = abs(sum(p for p in profits_filtered if p < 0))
            pf_filt = w_filt / max(l_filt, 1e-10)
            
            print(f"  Hold={hold:2d}:")
            print(f"    All:       WR={wr_all:.1%} PF={pf_all:.2f} trades={len(profits_all)} PnL={sum(profits_all):.4f}")
            print(f"    +M5 filter: WR={wr_filt:.1%} PF={pf_filt:.2f} trades={len(profits_filtered)} PnL={sum(profits_filtered):.4f}")
            print(f"    Improvement: PF {pf_all:.2f} -> {pf_filt:.2f} ({(pf_filt/pf_all-1)*100:+.0f}%)")
    
    # ═══════════════════════════════════════════════════════════
    # TEST 2: M5 RSI FILTER
    # ═══════════════════════════════════════════════════════════
    print(f"\n  TEST 2: M5 RSI FILTER")
    print(f"  {'-'*60}")
    
    hold = 5
    for rsi_threshold in [30, 40, 50, 60, 70]:
        profits = []
        for i, pos in enumerate(target_pos):
            if pos + hold >= nb:
                continue
            
            trend = m5_trends[i]
            rsi = trend["rsi"]
            
            if np.isnan(rsi):
                continue
            
            # For CRASH: want RSI high (overbought) before spike
            # For BOOM: want RSI low (oversold) before spike
            if drift_up:
                if rsi < rsi_threshold:
                    continue
            else:
                if rsi > (100 - rsi_threshold):
                    continue
            
            e = closes[pos]
            x = closes[pos + hold]
            profits.append((e - x) if drift_up else (x - e))
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            w = sum(p for p in profits if p > 0)
            l = abs(sum(p for p in profits if p < 0))
            pf = w / max(l, 1e-10)
            print(f"    RSI {'>' if drift_up else '<'} {rsi_threshold}: WR={wr:.1%} PF={pf:.2f} trades={len(profits)} PnL={sum(profits):.4f}")
    
    # ═══════════════════════════════════════════════════════════
    # TEST 3: M5 COMBINED FILTER (EMA200 + RSI)
    # ═══════════════════════════════════════════════════════════
    print(f"\n  TEST 3: M5 COMBINED FILTER (EMA200 + RSI + Partial Entry)")
    print(f"  {'-'*60}")
    
    hold = 5
    
    # Baseline: no filter, full entry
    profits_base = []
    for pos in target_pos:
        if pos + hold >= nb:
            continue
        e = closes[pos]
        x = closes[pos + hold]
        profits_base.append((e - x) if drift_up else (x - e))
    
    # M5 filter + partial entry
    profits_m5_partial = []
    for i, pos in enumerate(target_pos):
        if pos + hold >= nb or pos + 1 >= nb:
            continue
        
        trend = m5_trends[i]
        
        # M5 trend filter
        if drift_up:
            if not (trend["above_ema200"] and trend["ema20_above_ema50"]):
                continue
        else:
            if not (not trend["above_ema200"] and not trend["ema20_above_ema50"]):
                continue
        
        e = closes[pos]
        p1 = closes[pos + 1]
        x = closes[pos + hold]
        
        # Partial entry
        if drift_up:
            h1 = (e - p1) * 0.5
            h2 = ((e if e > p1 else p1) - x) * 0.5
        else:
            h1 = (p1 - e) * 0.5
            h2 = (x - (e if p1 <= e else p1)) * 0.5
        profits_m5_partial.append(h1 + h2)
    
    # M5 filter only (no partial)
    profits_m5_only = []
    for i, pos in enumerate(target_pos):
        if pos + hold >= nb:
            continue
        
        trend = m5_trends[i]
        
        if drift_up:
            if not (trend["above_ema200"] and trend["ema20_above_ema50"]):
                continue
        else:
            if not (not trend["above_ema200"] and not trend["ema20_above_ema50"]):
                continue
        
        e = closes[pos]
        x = closes[pos + hold]
        profits_m5_only.append((e - x) if drift_up else (x - e))
    
    # Partial only (no M5 filter)
    profits_partial_only = []
    for pos in target_pos:
        if pos + hold >= nb or pos + 1 >= nb:
            continue
        e = closes[pos]
        p1 = closes[pos + 1]
        x = closes[pos + hold]
        if drift_up:
            h1 = (e - p1) * 0.5
            h2 = ((e if e > p1 else p1) - x) * 0.5
        else:
            h1 = (p1 - e) * 0.5
            h2 = (x - (e if p1 <= e else p1)) * 0.5
        profits_partial_only.append(h1 + h2)
    
    for label, profits in [("Baseline (no filter)", profits_base),
                            ("+M5 filter only", profits_m5_only),
                            ("+Partial only", profits_partial_only),
                            ("+M5 filter + Partial", profits_m5_partial)]:
        if profits:
            wr = np.mean([p > 0 for p in profits])
            w = sum(p for p in profits if p > 0)
            l = abs(sum(p for p in profits if p < 0))
            pf = w / max(l, 1e-10)
            print(f"    {label:<25}: WR={wr:.1%} PF={pf:.2f} trades={len(profits)} PnL={sum(profits):.4f}")
    
    # ═══════════════════════════════════════════════════════════
    # OUT-OF-SAMPLE
    # ═══════════════════════════════════════════════════════════
    print(f"\n  OUT-OF-SAMPLE (M5 filter + Partial, Hold={hold})")
    split = len(target_pos) // 2
    
    for label, subset in [("TRAIN", target_pos[:split]), ("TEST", target_pos[split:])]:
        profits = []
        for i, pos in enumerate(subset):
            if i >= len(m5_trends):
                break
            if pos + hold >= nb or pos + 1 >= nb:
                continue
            
            trend = m5_trends[i]
            
            if drift_up:
                if not (trend["above_ema200"] and trend["ema20_above_ema50"]):
                    continue
            else:
                if not (not trend["above_ema200"] and not trend["ema20_above_ema50"]):
                    continue
            
            e = closes[pos]
            p1 = closes[pos + 1]
            x = closes[pos + hold]
            
            if drift_up:
                h1 = (e - p1) * 0.5
                h2 = ((e if e > p1 else p1) - x) * 0.5
            else:
                h1 = (p1 - e) * 0.5
                h2 = (x - (e if p1 <= e else p1)) * 0.5
            profits.append(h1 + h2)
        
        if profits:
            wr = np.mean([p > 0 for p in profits])
            w = sum(p for p in profits if p > 0)
            l = abs(sum(p for p in profits if p < 0))
            pf = w / max(l, 1e-10)
            print(f"    {label}: trades={len(profits)} WR={wr:.1%} PF={pf:.2f} PnL={sum(profits):.4f}")

# RUN
print("="*80)
print("  M5 TREND FILTER TEST")
print("="*80)

for sym, slug in [("BOOM1000","boom1000"),("CRASH1000","crash1000"),
                   ("BOOM500","boom500"),("CRASH500","crash500")]:
    try:
        analyze(sym, slug)
    except Exception as e:
        print(f"\n  ERROR {sym}: {e}")
        import traceback
        traceback.print_exc()
