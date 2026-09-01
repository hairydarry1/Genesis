"""Analyze spike patterns by hour to build session filter."""
import pandas as pd
import numpy as np

for sym in ['boom500', 'crash500']:
    df = pd.read_parquet(f'data/candles_{sym}_1min.parquet')
    
    # Find the timestamp column
    ts_col = [c for c in df.columns if 'time' in c.lower() or 'ts' in c.lower() or 'date' in c.lower()]
    print(f'\n{sym} columns: {list(df.columns)}')
    
    if ts_col:
        ts = ts_col[0]
        if df[ts].dtype in ['int64', 'float64']:
            df['dt'] = pd.to_datetime(df[ts], unit='s', utc=True)
        else:
            df['dt'] = pd.to_datetime(df[ts], utc=True)
    else:
        # Try index
        if df.index.dtype in ['int64', 'float64']:
            df['dt'] = pd.to_datetime(df.index, unit='s', utc=True)
        else:
            df['dt'] = pd.to_datetime(df.index, utc=True)
    
    df['hour'] = df['dt'].dt.hour
    
    # Detect spikes — look for large single-bar moves
    close_col = [c for c in df.columns if 'close' in c.lower()]
    high_col = [c for c in df.columns if 'high' in c.lower()]
    low_col = [c for c in df.columns if 'low' in c.lower()]
    
    if close_col:
        c = close_col[0]
        df['pct_change'] = df[c].pct_change()
        df['spike'] = df['pct_change'].abs() > 0.002  # 0.2% threshold
    else:
        print(f'  No close column found, skipping')
        continue
    
    spikes = df[df['spike']].copy()
    
    print(f'\n{"="*70}')
    print(f'  {sym.upper()} — SPIKE PATTERNS BY HOUR (UTC)')
    print(f'{"="*70}')
    print(f'  Total bars: {len(df):,}  Total spikes: {len(spikes):,}')
    print()
    
    total_spikes = len(spikes)
    
    print(f'  {"Hour":>6}  {"Spikes":>7}  {"Rate%":>7}  {"%Total":>7}  {"AvgPct":>9}  {"Median":>9}')
    print(f'  {"-"*6}  {"-"*7}  {"-"*7}  {"-"*7}  {"-"*9}  {"-"*9}')
    
    dead_hours = []
    hot_hours = []
    all_rates = []
    
    for h in range(24):
        hbars = len(df[df['hour'] == h])
        hspikes = spikes[spikes['hour'] == h]
        n = len(hspikes)
        rate = n / hbars * 100 if hbars > 0 else 0
        pct_total = n / total_spikes * 100 if total_spikes > 0 else 0
        all_rates.append(rate)
        
        avg_pct = hspikes['pct_change'].abs().mean() * 100 if n > 0 else 0
        med_pct = hspikes['pct_change'].abs().median() * 100 if n > 0 else 0
        
        marker = ''
        print(f'  {h:02d}:00  {n:>7}  {rate:>6.2f}%  {pct_total:>6.1f}%  {avg_pct:>8.3f}%  {med_pct:>8.3f}%{marker}')
    
    avg_rate = np.mean(all_rates)
    print(f'\n  Average spike rate: {avg_rate:.2f}%')
    
    for h in range(24):
        hbars = len(df[df['hour'] == h])
        hspikes = spikes[spikes['hour'] == h]
        rate = len(hspikes) / hbars * 100 if hbars > 0 else 0
        if rate < avg_rate * 0.5 and len(df[df['hour'] == h]) > 10:
            dead_hours.append(h)
        elif rate > avg_rate * 1.5:
            hot_hours.append(h)
    
    print(f'  Dead hours (<50% avg rate): {dead_hours}')
    print(f'  Hot hours (>150% avg rate):  {hot_hours}')
    
    # Quality by hour — which hours have best fade retrace
    print(f'\n  --- FADE RETRACE BY HOUR ---')
    print(f'  (positive gap = price moved in our favor after spike)')
    
    # Calculate retrace: after a spike, does price revert?
    if close_col:
        c = close_col[0]
        df['next_close'] = df[c].shift(-1)
        df['retrace'] = (df['next_close'] - df[c]) / df[c]
        
        # For up spikes (positive pct_change), retrace should be negative (price drops)
        # For down spikes (negative pct_change), retrace should be positive (price rises)
        # So retrace is favorable when: -pct_change * retrace > 0
        spikes_with_retrace = df[df['spike']].copy()
        spikes_with_retrace['favorable'] = -np.sign(spikes_with_retrace['pct_change']) * spikes_with_retrace['retrace'] > 0
        
        print(f'  {"Hour":>6}  {"Spikes":>7}  {"Favorable%":>11}  {"AvgRetrace":>11}')
        print(f'  {"-"*6}  {"-"*7}  {"-"*11}  {"-"*11}')
        
        for h in range(24):
            hs = spikes_with_retrace[spikes_with_retrace['hour'] == h]
            if len(hs) < 3:
                continue
            fav = hs['favorable'].mean() * 100
            avg_ret = hs['retrace'].abs().mean() * 100
            marker = ' <-- BEST' if fav > 60 else (' <-- WORST' if fav < 40 else '')
            print(f'  {h:02d}:00  {len(hs):>7}  {fav:>10.1f}%  {avg_ret:>10.3f}%{marker}')
