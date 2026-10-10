"""
data/download_history.py — Download historical candle data from MT5.

Fetches 1-min, 5-min, and 15-min candles for all BOOM/CRASH variants.
Saves as parquet files in data/ directory.

Usage:
    python data/download_history.py                    # Default: 30 days all symbols
    python data/download_history.py --days 90          # 90 days
    python data/download_history.py --symbol BOOM500   # Single symbol
    python data/download_history.py --timeframes 1,5,15  # Multiple timeframes
"""

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("  MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

try:
    import pandas as pd
except ImportError:
    print("  pandas not installed. Run: pip install pandas pyarrow")
    sys.exit(1)


# Symbol mapping
SYMBOLS = {
    "BOOM500":   "Boom 500 Index",
    "CRASH500":  "Crash 500 Index",
    "BOOM1000":  "Boom 1000 Index",
    "CRASH1000": "Crash 1000 Index",
    "BOOM300":   "Boom 300 Index",
    "CRASH300":  "Crash 300 Index",
    "BOOM150":   "Boom 150 Index",
    "CRASH150":  "Crash 150 Index",
}

# Timeframe mapping
TIMEFRAMES = {
    1:  mt5.TIMEFRAME_M1,
    5:  mt5.TIMEFRAME_M5,
    15: mt5.TIMEFRAME_M15,
    30: mt5.TIMEFRAME_M30,
    60: mt5.TIMEFRAME_H1,
}


def connect_mt5(login: int = 0, password: str = "", server: str = "Deriv-Demo") -> bool:
    """Connect to MT5 terminal."""
    init_args = {"timeout": 10000}
    if login:
        init_args["login"] = login
    if password:
        init_args["password"] = password
    if server:
        init_args["server"] = server
    # Try auto-detect path
    default_paths = [
        r"C:\Program Files\MetaTrader 5\terminal64.exe",
        r"C:\Program Files\Deriv\Deriv MT5\terminal64.exe",
    ]
    for p in default_paths:
        import os
        if os.path.exists(p):
            init_args["path"] = p
            break

    if not mt5.initialize(**init_args):
        error = mt5.last_error()
        print(f"  MT5 init failed: {error}")
        return False

    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server} | Balance: {info.balance:.2f}")
    return True


def download_candles(mt5_symbol: str, days: int, timeframe_key: int) -> pd.DataFrame | None:
    """Download candles for a symbol and timeframe."""
    tf = TIMEFRAMES.get(timeframe_key)
    if tf is None:
        print(f"    Unsupported timeframe: {timeframe_key}min")
        return None

    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)

    # Fetch in chunks to avoid API limits
    all_candles = []
    chunk_days = min(days, 30)  # 30-day chunks
    current_start = start

    while current_start < end:
        current_end = min(current_start + timedelta(days=chunk_days), end)

        try:
            rates = mt5.copy_rates_range(mt5_symbol, tf, current_start, current_end)
        except Exception as e:
            print(f"    Error fetching chunk: {e}")
            break

        if rates is not None and len(rates) > 0:
            # Convert numpy structured array to DataFrame preserving column names
            chunk_df = pd.DataFrame(rates.tolist(), columns=rates.dtype.names)
            all_candles.append(chunk_df)
        else:
            break

        current_start = current_end
        time.sleep(0.1)  # Rate limit

    if not all_candles:
        return None

    df = pd.concat(all_candles, ignore_index=True)
    df = df.drop_duplicates(subset=["time"])
    df = df.sort_values("time").reset_index(drop=True)

    # Rename for consistency
    df = df.rename(columns={
        "time": "epoch",
        "open": "open",
        "high": "high",
        "low": "low",
        "close": "close",
        "tick_volume": "volume",
        "spread": "spread",
    })

    # Keep relevant columns
    cols = ["epoch", "open", "high", "low", "close"]
    if "volume" in df.columns:
        cols.append("volume")
    if "spread" in df.columns:
        cols.append("spread")
    df = df[[c for c in cols if c in df.columns]]

    return df


def compute_spikes(df: pd.DataFrame) -> pd.DataFrame:
    """Add spike detection columns to the dataframe."""
    df = df.copy()
    df["pct_change"] = df["close"].pct_change()

    # Rolling median volatility
    df["median_vol"] = df["pct_change"].abs().rolling(100, min_periods=20).median()

    # Spike detection: 3x median
    df["spike"] = df["pct_change"].abs() > df["median_vol"] * 3
    df["spike_pct"] = df["pct_change"].where(df["spike"], 0)

    # Direction
    df["spike_dir"] = 0
    df.loc[df["spike"] & (df["pct_change"] > 0), "spike_dir"] = 1
    df.loc[df["spike"] & (df["pct_change"] < 0), "spike_dir"] = -1

    # Gap since last spike
    spike_indices = df.index[df["spike"]].tolist()
    df["gap_since_spike"] = 0
    for i, idx in enumerate(spike_indices):
        if i > 0:
            gap = idx - spike_indices[i - 1]
            df.loc[idx, "gap_since_spike"] = gap

    return df


def main():
    parser = argparse.ArgumentParser(description="Download MT5 historical data")
    parser.add_argument("--symbol", default=None, help="Single symbol (e.g. BOOM500)")
    parser.add_argument("--days", type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--timeframes", type=str, default="1", help="Comma-separated minutes (1,5,15)")
    parser.add_argument("--no-spike-detection", action="store_true", help="Skip spike detection")
    parser.add_argument("--login", type=int, default=0, help="MT5 login")
    parser.add_argument("--password", type=str, default="", help="MT5 password")
    parser.add_argument("--server", type=str, default="Deriv-Demo", help="MT5 server")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  MT5 HISTORICAL DATA DOWNLOADER")
    print(f"{'='*60}")

    if not connect_mt5(args.login, args.password, args.server):
        return

    try:
        symbols = {args.symbol: SYMBOLS[args.symbol]} if args.symbol else SYMBOLS
        timeframes = [int(t.strip()) for t in args.timeframes.split(",")]

        total_files = 0
        total_rows = 0

        for short_name, mt5_name in symbols.items():
            # Check if symbol exists
            info = mt5.symbol_info(mt5_name)
            if info is None:
                print(f"\n  {short_name}: Symbol not found, skipping")
                continue

            # Enable the symbol
            if not mt5.symbol_select(mt5_name, True):
                print(f"\n  {short_name}: Failed to select, skipping")
                continue

            for tf_min in timeframes:
                print(f"\n  {short_name} @ {tf_min}min ({mt5_name})...")
                df = download_candles(mt5_name, args.days, tf_min)

                if df is None or len(df) == 0:
                    print(f"    No data returned")
                    continue

                # Compute spikes
                if not args.no_spike_detection:
                    df = compute_spikes(df)
                    n_spikes = df["spike"].sum()
                    print(f"    Got {len(df):,} candles | {n_spikes:,} spikes ({n_spikes/len(df)*100:.1f}%)")
                else:
                    print(f"    Got {len(df):,} candles")

                # Save
                out_dir = Path(__file__).parent
                filename = out_dir / f"candles_{short_name.lower()}_{tf_min}min.parquet"
                df.to_parquet(filename, index=False)
                print(f"    Saved: {filename}")
                total_files += 1
                total_rows += len(df)

        print(f"\n{'='*60}")
        print(f"  DONE: {total_files} files, {total_rows:,} total rows")
        print(f"{'='*60}\n")

    finally:
        mt5.shutdown()
        print("  MT5 disconnected")


if __name__ == "__main__":
    main()
