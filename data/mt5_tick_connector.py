"""
data/mt5_tick_connector.py — Connect to Deriv MT5 and pull tick data.

Usage:
    1. Fill in your Deriv MT5 credentials below
    2. Make sure Deriv MT5 terminal is RUNNING
    3. Run: python data/mt5_tick_connector.py

This will:
    - Connect to your Deriv MT5 account
    - Pull tick history for BOOM500/CRASH500
    - Show tick granularity and spike patterns
    - Optionally stream live ticks
"""

import MetaTrader5 as mt5
import numpy as np
import time
import sys
from datetime import datetime, timedelta


# ============================================================================
# FILL IN YOUR DERIV MT5 CREDENTIALS
# ============================================================================

MT5_LOGIN = 32341786           # Your MT5 login number (from Deriv)
MT5_PASSWORD = "@Mikolo123"         # Your MT5 password
MT5_SERVER = "Deriv-Demo" # Server name (check your MT5 terminal)

# Path to MT5 terminal (auto-detect if empty)
MT5_PATH = ""             # Leave empty to auto-detect, or set full path

# ============================================================================


def connect_mt5():
    """Connect to Deriv MT5."""
    init_kwargs = {"timeout": 10000}
    if MT5_PATH:
        init_kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        init_kwargs["login"] = MT5_LOGIN
    if MT5_PASSWORD:
        init_kwargs["password"] = MT5_PASSWORD
    if MT5_SERVER:
        init_kwargs["server"] = MT5_SERVER

    print("Connecting to MT5...")
    if not mt5.initialize(**init_kwargs):
        error = mt5.last_error()
        print(f"  FAILED: {error}")
        print()
        print("  Make sure:")
        print("  1. Deriv MT5 terminal is RUNNING")
        print("  2. You're logged in to your account")
        print("  3. Credentials are correct")
        return False

    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server}")
        print(f"  Balance: {info.balance} {info.currency}")
    return True


def find_boom_crash_symbols():
    """Find available BOOM/CRASH symbols."""
    symbols = mt5.symbols_get()
    bc_symbols = []
    for s in symbols:
        name_lower = s.name.lower()
        if any(x in name_lower for x in ["boom", "crash"]):
            bc_symbols.append(s)
            # Enable if not visible
            if not s.visible:
                mt5.symbol_select(s.name, True)
    return bc_symbols


def pull_tick_history(symbol_name: str, days: int = 7):
    """Pull tick history from MT5."""
    print(f"\nPulling {days} days of tick history for {symbol_name}...")

    end = datetime.now()
    start = end - timedelta(days=days)

    # Get all ticks in range
    ticks = mt5.copy_ticks_range(symbol_name, start, end, mt5.COPY_TICKS_ALL)

    if ticks is None or len(ticks) == 0:
        print(f"  No ticks found. Error: {mt5.last_error()}")
        return None

    print(f"  Got {len(ticks)} ticks")
    return ticks


def analyze_ticks(ticks, symbol_name: str):
    """Analyze tick data for spikes and patterns."""
    # Convert to arrays
    times = np.array([t['time_msc'] for t in ticks], dtype=np.float64) / 1000.0  # ms -> seconds
    prices = np.array([t['bid'] for t in ticks], dtype=float)

    n = len(prices)
    dt = np.diff(times)
    dp = np.diff(prices)
    abs_dp = np.abs(dp)

    print(f"\n{'='*65}")
    print(f"  {symbol_name} TICK ANALYSIS ({n} ticks)")
    print(f"{'='*65}")

    # Timing
    print(f"\n  TICK TIMING:")
    print(f"    Total ticks: {n}")
    print(f"    Time span: {(times[-1]-times[0])/3600:.1f} hours")
    print(f"    Tick rate: {n/(times[-1]-times[0]):.1f} ticks/sec")
    print(f"    dt mean: {dt.mean()*1000:.1f}ms")
    print(f"    dt median: {np.median(dt)*1000:.1f}ms")
    print(f"    dt min: {dt.min()*1000:.1f}ms")
    print(f"    dt max: {dt.max()*1000:.1f}ms")

    # Price changes
    print(f"\n  PRICE CHANGES:")
    print(f"    Non-zero: {(dp!=0).sum()}/{len(dp)} ({(dp!=0).mean()*100:.1f}%)")
    print(f"    Unique prices: {len(np.unique(prices))}")
    print(f"    Price range: {prices.min():.4f} - {prices.max():.4f}")

    # Spike detection
    med = np.median(abs_dp[abs_dp > 0]) if (abs_dp > 0).any() else 0
    thr = med * 3

    sp_mask = abs_dp > thr
    sp_idx = np.where(sp_mask)[0]
    n_sp = len(sp_idx)
    gaps = np.diff(sp_idx) if n_sp > 1 else np.array([])

    # Direction
    if "boom" in symbol_name.lower():
        up = (dp[sp_idx] > 0).sum()
        dn = (dp[sp_idx] < 0).sum()
        print(f"\n  SPIKES: {n_sp} ({n_sp/len(dp)*100:.2f}%)")
        print(f"    Up (boom): {up}  Down: {dn}")
    else:
        up = (dp[sp_idx] > 0).sum()
        dn = (dp[sp_idx] < 0).sum()
        print(f"\n  SPIKES: {n_sp} ({n_sp/len(dp)*100:.2f}%)")
        print(f"    Up: {up}  Down (crash): {dn}")

    if n_sp > 1:
        print(f"\n  TICK GAP BETWEEN SPIKES:")
        print(f"    Mean:   {gaps.mean():.0f} ticks")
        print(f"    Median: {np.median(gaps):.0f} ticks")
        print(f"    Std:    {gaps.std():.0f} ticks")
        print(f"    Min:    {gaps.min()}  Max: {gaps.max()}")

        # Gap distribution
        print(f"\n  GAP DISTRIBUTION:")
        for lo, hi, label in [(1,100,'1-100'), (101,300,'101-300'), (301,600,'301-600'),
                               (601,1000,'601-1000'), (1001,99999,'1000+')]:
            c = ((gaps >= lo) & (gaps <= hi)).sum()
            p = c / len(gaps) * 100
            print(f"    {label:>10s}: {c:5d} ({p:5.1f}%)")

        # Verify 500/1000 claim
        expected = 500 if "500" in symbol_name else 1000
        actual = np.median(gaps)
        print(f"\n  CLAIM vs REALITY:")
        print(f"    Claimed: ~{expected} ticks between spikes")
        print(f"    Actual median: {actual:.0f} ticks")
        print(f"    Off by: {abs(actual-expected)/expected*100:.1f}%")

    # Spike size
    sp_sizes = abs_dp[sp_idx]
    mults = sp_sizes / med if med > 0 else sp_sizes
    print(f"\n  SPIKE SIZE (multiples of median):")
    for lo, hi, label in [(3,5,'3-5x'), (5,10,'5-10x'), (10,20,'10-20x'), (20,999,'20x+')]:
        mask = (mults >= lo) & (mults < hi)
        c = mask.sum()
        if c > 0:
            sub_gaps = gaps[:-1] if len(gaps) > 1 else gaps
            sub_mask = mask[:len(sub_gaps)]
            avg_gap = sub_gaps[sub_mask].mean() if sub_mask.sum() > 0 else 0
            print(f"    {label:>6s}: {c:4d} spikes  avg gap after: {avg_gap:.0f} ticks")

    print(f"{'='*65}")


def stream_live_ticks(symbol_name: str, duration_sec: int = 60):
    """Stream live ticks and analyze in real-time."""
    print(f"\nStreaming live ticks for {symbol_name} ({duration_sec}s)...")

    ticks = []
    spikes = []
    t0 = time.time()
    last_price = 0

    # Subscribe
    if not mt5.symbol_select(symbol_name, True):
        print(f"  Failed to select {symbol_name}")
        return

    print(f"  Collecting ticks... (Ctrl+C to stop)")
    try:
        while time.time() - t0 < duration_sec:
            tick = mt5.symbol_info_tick(symbol_name)
            if tick:
                price = tick.bid
                if price != last_price:
                    ticks.append({
                        'time': time.time(),
                        'epoch': tick.time,
                        'price': price,
                        'last_price': last_price,
                        'change': price - last_price if last_price > 0 else 0,
                    })

                    # Check for spike
                    if len(ticks) > 10:
                        recent = np.array([t['change'] for t in ticks[-10:]])
                        med = np.median(np.abs(recent))
                        if med > 0 and abs(ticks[-1]['change']) > med * 5:
                            spikes.append(len(ticks))
                            print(f"  *** SPIKE at tick {len(ticks)}: {ticks[-1]['change']:+.4f} ({ticks[-1]['change']/price*100:+.4f}%)")

                    last_price = price
            time.sleep(0.001)  # 1ms poll

    except KeyboardInterrupt:
        pass

    if ticks:
        prices = np.array([t['price'] for t in ticks])
        changes = np.array([t['change'] for t in ticks[1:]])
        times_arr = np.array([t['time'] for t in ticks])

        print(f"\n  Results:")
        print(f"    Collected: {len(ticks)} ticks in {time.time()-t0:.1f}s")
        print(f"    Tick rate: {len(ticks)/(time.time()-t0):.0f}/sec")
        print(f"    Unique prices: {len(np.unique(prices))}")
        print(f"    Non-zero changes: {(changes!=0).sum()}/{len(changes)}")
        print(f"    Spikes detected: {len(spikes)}")
        if len(spikes) > 1:
            gaps = np.diff(spikes)
            print(f"    Avg gap between spikes: {gaps.mean():.0f} ticks")


def main():
    if not connect_mt5():
        return

    try:
        # Find BOOM/CRASH symbols
        symbols = find_boom_crash_symbols()
        if not symbols:
            print("\nNo BOOM/CRASH symbols found!")
            print("Make sure Deriv MT5 is set up with synthetic indices.")
            return

        print(f"\nAvailable BOOM/CRASH symbols:")
        for s in symbols:
            print(f"  {s.name:25s}  spread={s.spread}  digits={s.digits}")

        # Pull tick history for key symbols only
        key_symbols = [s for s in symbols if any(x in s.name for x in ['Boom 500', 'Crash 500', 'Boom 1000', 'Crash 1000'])]
        if not key_symbols:
            key_symbols = symbols[:4]  # Fallback: first 4
        
        for s in key_symbols:
            try:
                ticks = pull_tick_history(s.name, days=3)
                if ticks is not None and len(ticks) > 100:
                    analyze_ticks(ticks, s.name)
            except Exception as e:
                print(f"  Error analyzing {s.name}: {e}")

        # Optional: stream live ticks
        print(f"\n{'='*65}")
        print(f"  LIVE TICK STREAMING")
        print(f"{'='*65}")

        target = symbols[0].name  # First symbol
        stream_live_ticks(target, duration_sec=30)

    finally:
        mt5.shutdown()
        print("\nMT5 disconnected.")


if __name__ == "__main__":
    main()
