#!/usr/bin/env python3
"""Test if COLD hours are stable across different months."""

import pandas as pd
import numpy as np
import os

def load_candles(sym):
    f = f'data/candles_{sym.lower()}_1min.parquet'
    if not os.path.exists(f):
        return None
    df = pd.read_parquet(f)
    df['time'] = pd.to_datetime(df['time'])
    df['hour'] = df['time'].dt.hour
    df['month'] = df['time'].dt.month
    df['year_month'] = df['time'].dt.to_period('M')
    df['range'] = df['high'] - df['low']
    return df

def detect_spikes(df, mult=3.0):
    med = df['range'].median()
    df['is_spike'] = df['range'] > mult * med
    return df

def get_cold_hours(df, mult=3.0):
    """Get COLD hours for a subset of data."""
    df = detect_spikes(df, mult)
    hourly = df.groupby('hour').agg(bars=('is_spike', 'count'), spikes=('is_spike', 'sum'))
    hourly['rate'] = hourly['spikes'] / hourly['bars'] * 100
    expected = df['is_spike'].sum() / len(df) * 100
    cold = hourly[hourly['rate'] < expected - 0.5].index.tolist()
    return sorted(cold), hourly

def main():
    symbols = ['boom500', 'crash500', 'boom1000', 'crash1000']
    SPIKE_MULT = 3.0
    
    for sym in symbols:
        df = load_candles(sym)
        if df is None:
            continue
        
        months = sorted(df['year_month'].unique())
        
        print(f"\n{'='*80}")
        print(f"  {sym.upper()} ({len(df)} bars, {len(months)} months: {months[0]} to {months[-1]})")
        print(f"{'='*80}")
        
        # Overall COLD hours
        overall_cold, overall_hourly = get_cold_hours(df, SPIKE_MULT)
        print(f"\n  Overall COLD hours: {overall_cold}")
        
        # Per-month COLD hours
        monthly_cold = {}
        monthly_hourly = {}
        
        print(f"\n  {'Month':<12} {'COLD Hours':<25} {'Spikes':>7} {'Rate':>7} {'Overlap':>8}")
        print(f"  {'-'*12} {'-'*25} {'-'*7} {'-'*7} {'-'*8}")
        
        for m in months:
            mdf = df[df['year_month'] == m]
            if len(mdf) < 100:
                continue
            
            cold_h, hourly = get_cold_hours(mdf, SPIKE_MULT)
            monthly_cold[m] = cold_h
            monthly_hourly[m] = hourly
            
            # Overlap with overall
            overlap = len(set(cold_h) & set(overall_cold))
            total = len(set(cold_h) | set(overall_cold))
            jaccard = overlap / total if total > 0 else 0
            
            cold_str = ','.join(str(h) for h in cold_h)
            if len(cold_str) > 23:
                cold_str = cold_str[:20] + "..."
            
            spikes = mdf['is_spike'].sum()
            rate = spikes / len(mdf) * 100
            
            print(f"  {str(m):<12} [{cold_str:<23}] {spikes:>7} {rate:>6.1f}% {jaccard:>7.0%}")
        
        # Stability analysis: how often does each hour appear as COLD?
        print(f"\n  Hour stability (% of months it's COLD):")
        print(f"  {'Hour':>6} {'% COLD':>8} {'Pattern':>30}")
        print(f"  {'-'*6} {'-'*8} {'-'*30}")
        
        for h in range(24):
            count = sum(1 for m, cold in monthly_cold.items() if h in cold)
            n_months = len(monthly_cold)
            pct = count / n_months * 100 if n_months > 0 else 0
            
            pattern = ""
            for m in months:
                if m in monthly_cold:
                    pattern += "X" if h in monthly_cold[m] else "."
            
            bar = "#" * int(pct / 5)
            print(f"  {h:>6} {pct:>7.0f}% {pattern:<30} {bar}")
        
        # Consensus COLD hours (appear in >50% of months)
        consensus = [h for h in range(24) 
                     if sum(1 for m, cold in monthly_cold.items() if h in cold) > len(monthly_cold) * 0.5]
        
        print(f"\n  Consensus COLD hours (appear in >50% of months): {consensus}")
        print(f"  Overall COLD hours: {overall_cold}")
        
        # Check if consensus matches overall
        match = len(set(consensus) & set(overall_cold))
        print(f"  Agreement: {match}/{max(len(consensus), len(overall_cold))} hours match")
        
        # Month-to-month correlation
        print(f"\n  Month-to-month COLD hour Jaccard similarity:")
        m_list = list(monthly_cold.keys())
        for i in range(len(m_list) - 1):
            m1, m2 = m_list[i], m_list[i+1]
            c1, c2 = set(monthly_cold[m1]), set(monthly_cold[m2])
            overlap = len(c1 & c2)
            union = len(c1 | c2)
            jaccard = overlap / union if union > 0 else 0
            print(f"    {m1} vs {m2}: {jaccard:.0%} ({overlap}/{union} shared)")

if __name__ == '__main__':
    main()
