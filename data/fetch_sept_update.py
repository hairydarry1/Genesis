"""
data/fetch_sept_update.py — Fetch Sep 1-7 data for Phase 1 re-verification.

Downloads 1-min candles and tick history for BOOM500/CRASH500/BOOM1000/CRASH1000
covering 2026-09-01 00:00 UTC -> 2026-09-07 23:59 UTC.

Saves to SEPARATE files so August baselines stay untouched:
    data/candles_{sym}_1min_sep.parquet   (time column, matches existing format)
    data/ticks_{sym}_sep.npy              (float64 price series)

Requires: Deriv MT5 terminal running and logged in.
Usage: python data/fetch_sept_update.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

# Reuse credentials from the tick connector (demo account)
from data.mt5_tick_connector import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

SYMBOLS = {
    "boom500":   "Boom 500 Index",
    "crash500":  "Crash 500 Index",
    "boom1000":  "Boom 1000 Index",
    "crash1000": "Crash 1000 Index",
}

START = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)  # exclusive upper bound
DATA_DIR = Path(__file__).parent


def connect() -> bool:
    kwargs = {"timeout": 10000}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        kwargs["login"] = MT5_LOGIN
        kwargs["password"] = MT5_PASSWORD
        kwargs["server"] = MT5_SERVER
    if not mt5.initialize(**kwargs):
        print(f"MT5 init failed: {mt5.last_error()}")
        print("Make sure the Deriv MT5 terminal is RUNNING and logged in.")
        return False
    info = mt5.account_info()
    if info:
        print(f"Connected: {info.login} @ {info.server} | Balance: {info.balance:.2f}")
    return True


def fetch_candles(mt5_symbol: str) -> pd.DataFrame | None:
    rates = mt5.copy_rates_range(mt5_symbol, mt5.TIMEFRAME_M1, START, END)
    if rates is None or len(rates) == 0:
        return None
    df = pd.DataFrame(rates.tolist(), columns=rates.dtype.names)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    # epoch (seconds) -> naive UTC datetime, matching existing parquet format
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume", "spread", "real_volume"]]


def fetch_ticks(mt5_symbol: str) -> np.ndarray | None:
    """Fetch tick prices in daily chunks (copy_ticks_range can cap on long ranges)."""
    chunks = []
    day = START
    while day < END:
        day_end = min(day.replace(hour=23, minute=59, second=59), END)
        ticks = mt5.copy_ticks_range(mt5_symbol, day, day_end, mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks) > 0:
            chunks.append(pd.DataFrame(ticks.tolist(), columns=ticks.dtype.names))
        day += pd.Timedelta(days=1)
    if not chunks:
        return None
    df = pd.concat(chunks, ignore_index=True).drop_duplicates(subset=["time_msc"])
    df = df.sort_values("time_msc").reset_index(drop=True)
    return df["bid"].to_numpy(dtype=np.float64)


def main() -> None:
    print("=" * 60)
    print("  SEP 1-7 DATA FETCH (verification window)")
    print("=" * 60)

    if not connect():
        sys.exit(1)

    try:
        for short, mt5_name in SYMBOLS.items():
            info = mt5.symbol_info(mt5_name)
            if info is None or not mt5.symbol_select(mt5_name, True):
                print(f"\n  {short}: symbol not available, skipping")
                continue

            print(f"\n  {short} ({mt5_name})")

            df = fetch_candles(mt5_name)
            if df is None or len(df) == 0:
                print("    No candle data returned")
            else:
                out = DATA_DIR / f"candles_{short}_1min_sep.parquet"
                df.to_parquet(out, index=False)
                print(f"    Candles: {len(df):,} bars  {df['time'].min()} -> {df['time'].max()}")
                print(f"    Saved: {out.name}")

            ticks = fetch_ticks(mt5_name)
            if ticks is None or len(ticks) == 0:
                print("    No tick data returned")
            else:
                out = DATA_DIR / f"ticks_{short}_sep.npy"
                np.save(out, ticks)
                print(f"    Ticks:   {len(ticks):,}  first {ticks[0]:.3f}  last {ticks[-1]:.3f}")
                print(f"    Saved: {out.name}")

        print("\n" + "=" * 60)
        print("  DONE")
        print("=" * 60)
    finally:
        mt5.shutdown()
        print("MT5 disconnected")


if __name__ == "__main__":
    main()
