# -*- coding: utf-8 -*-
"""
data/fetch_1000_ticks.py -- Fetch tick data for BOOM1000/CRASH1000 from MT5
"""
import sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)


MT5_LOGIN = 32341786
MT5_PASSWORD = "@Mikolo123"
MT5_SERVER = "Deriv-Demo"


def connect():
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER, timeout=10000):
        print(f"  FAILED: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server}")
    return True


def fetch_ticks(symbol_name, days=30):
    print(f"\n  Fetching {days} days of ticks for {symbol_name}...")
    
    # Make sure symbol is visible
    mt5.symbol_select(symbol_name, True)
    
    end = datetime.now()
    start = end - timedelta(days=days)
    
    # Pull in chunks (MT5 has limits)
    chunk_days = 7
    all_prices = []
    
    current_start = start
    while current_start < end:
        current_end = min(current_start + timedelta(days=chunk_days), end)
        ticks = mt5.copy_ticks_range(symbol_name, current_start, current_end, mt5.COPY_TICKS_ALL)
        
        if ticks is not None and len(ticks) > 0:
            prices = np.array([t['bid'] for t in ticks], dtype=float)
            all_prices.extend(prices.tolist())
            print(f"    {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}: {len(ticks)} ticks")
        else:
            print(f"    {current_start.strftime('%Y-%m-%d')} to {current_end.strftime('%Y-%m-%d')}: 0 ticks")
        
        current_start = current_end
    
    return np.array(all_prices)


def analyze_ticks(prices, symbol):
    print(f"\n  {symbol}: {len(prices):,} ticks")
    
    changes = np.abs(np.diff(prices))
    positive = changes[changes > 0]
    if len(positive) == 0:
        print("    No price changes!")
        return
    
    median = np.median(positive)
    threshold = median * 3.0
    spike_indices = np.where(changes > threshold)[0]
    
    print(f"    Spikes: {len(spike_indices)}")
    print(f"    Median change: {median:.5f}")
    
    # Buildup analysis
    is_crash = "crash" in symbol.lower()
    buildups = []
    for idx in spike_indices:
        count = 0
        for j in range(idx - 1, max(idx - 200, -1), -1):
            if j < 0:
                break
            tick_change = prices[j + 1] - prices[j]
            if is_crash:
                if tick_change > 0:  # uptick = buildup
                    count += 1
                else:
                    break
            else:
                if tick_change < 0:  # downtick = buildup
                    count += 1
                else:
                    break
        buildups.append(count)
    
    buildups = np.array(buildups)
    
    print(f"\n    BUILDUP TICKS BEFORE SPIKE:")
    print(f"      Mean:   {buildups.mean():.1f}")
    print(f"      Median: {np.median(buildups):.0f}")
    
    for t in [5, 8, 10, 12, 15, 20, 25, 30]:
        caught = (buildups >= t).sum()
        pct = caught / len(buildups) * 100
        print(f"      >= {t:2d} ticks: {caught:5d} ({pct:5.1f}%)")


def main():
    print("\n" + "=" * 70)
    print("  FETCHING TICK DATA FOR BOOM/CRASH 1000")
    print("=" * 70)
    
    if not connect():
        return
    
    symbols = {
        "Boom 1000 Index": "boom1000",
        "Crash 1000 Index": "crash1000",
    }
    
    for symbol_name, code in symbols.items():
        prices = fetch_ticks(symbol_name, days=30)
        
        if len(prices) > 0:
            # Save
            out_path = Path(f"data/ticks_{code}_index.npy")
            np.save(out_path, prices)
            print(f"  Saved to {out_path}")
            
            # Analyze
            analyze_ticks(prices, code)
        else:
            print(f"  No tick data retrieved for {symbol_name}")
    
    mt5.shutdown()
    
    print(f"\n{'='*70}")
    print(f"  DONE -- Compare with BOOM/CRASH 500 buildup")
    print(f"{'='*70}")


if __name__ == "__main__":
    main()
