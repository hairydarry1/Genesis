"""
data/fetch_candles.py — Fetch 90 days of 1-min candle data from Deriv API.

Deriv caps at 1000 candles per request, so we paginate backwards.
1-min candles are the densest we can get from the candle API, and we
resample to coarser timeframes in the feature pipeline.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pandas as pd
import structlog

from data.deriv_connection import DerivConnection

logger = structlog.get_logger(__name__)

SYMBOL = "R_75"
GRANULARITY = 60  # 1-minute candles
CANDLES_PER_CHUNK = 1000
TARGET_DAYS = 180  # 6 months for multi-fold walk-forward validation
OUTPUT_PATH = Path("data/candles_R75_1min.parquet")

# Rate limiting
PAUSE_BETWEEN_CHUNKS = 0.4  # seconds
PAUSE_ON_TIMEOUT = 5.0
MAX_TIMEOUT_RETRIES = 5
MAX_CHUNKS = 3000  # safety limit (~180 days at 1min resolution)


async def fetch_candles() -> None:
    """Fetch 1-minute candles going backwards from now."""
    conn = DerivConnection()
    await conn.connect()

    all_candles = []
    chunk_num = 0
    consecutive_timeouts = 0
    target_start = int(time.time()) - TARGET_DAYS * 86400

    print(f"Fetching {GRANULARITY}s candles for {SYMBOL}")
    print(f"Target: {TARGET_DAYS} days back (epoch <= {target_start})")
    print(f"Rate: {CANDLES_PER_CHUNK} candles/chunk, {PAUSE_BETWEEN_CHUNKS}s pause")
    print()

    try:
        current_end = "latest"

        while chunk_num < MAX_CHUNKS:
            req = {
                "ticks_history": SYMBOL,
                "end": current_end if isinstance(current_end, str) else int(current_end),
                "style": "candles",
                "count": CANDLES_PER_CHUNK,
                "granularity": GRANULARITY,
                "adjust_start_time": 1,
            }

            try:
                resp = await conn.send(req, timeout=30)
                consecutive_timeouts = 0
            except TimeoutError:
                consecutive_timeouts += 1
                if consecutive_timeouts >= MAX_TIMEOUT_RETRIES:
                    print(f"\n  {MAX_TIMEOUT_RETRIES} consecutive timeouts - stopping.")
                    break
                print(f"  Timeout on chunk {chunk_num}, retrying in {PAUSE_ON_TIMEOUT}s...")
                await asyncio.sleep(PAUSE_ON_TIMEOUT)
                continue

            if "error" in resp:
                print(f"\n  API error: {resp['error']}")
                break

            candles = resp.get("candles", [])
            if not candles:
                print(f"\n  No candles returned at chunk {chunk_num}")
                break

            all_candles.extend(candles)
            chunk_num += 1

            earliest_epoch = min(c["epoch"] for c in candles)
            latest_epoch = max(c["epoch"] for c in candles)

            if chunk_num % 20 == 0 or chunk_num == 1:
                earliest_ts = pd.Timestamp(earliest_epoch, unit="s", tz="UTC")
                print(f"  Chunk {chunk_num}: {len(all_candles):,} candles, "
                      f"earliest: {earliest_ts.date()}")

            # Check if we've reached the target
            if earliest_epoch <= target_start:
                print(f"\n  Reached target date ({TARGET_DAYS} days)")
                break

            # Move end pointer just before the earliest candle of this chunk
            current_end = int(earliest_epoch) - 1

            await asyncio.sleep(PAUSE_BETWEEN_CHUNKS)

    finally:
        await conn.close()

    if not all_candles:
        print("No candles fetched!")
        return

    # Build DataFrame
    df = pd.DataFrame(all_candles)
    df = df.drop_duplicates(subset=["epoch"], keep="last")
    df = df.sort_values("epoch").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True)

    # Rename to match tick schema for compatibility
    # candles have: epoch, open, high, low, close
    # We'll keep them as-is since they're already OHLCV

    # Save
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_PATH, index=False, engine="pyarrow")

    span_days = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400

    print(f"\nSaved {len(df):,} candles to {OUTPUT_PATH}")
    print(f"  Range: {df['timestamp'].min()} to {df['timestamp'].max()}")
    print(f"  Span: {span_days:.1f} days")
    print(f"  Price range: {df['close'].min():.2f} to {df['close'].max():.2f}")

    # Also convert to tick-like format for the feature pipeline
    # (resample.py expects 'timestamp' and 'price' columns)
    tick_df = df[["epoch", "timestamp"]].copy()
    tick_df["price"] = df["close"]  # use close as the price

    tick_path = Path("data/ticks_R75.parquet")
    tick_df.to_parquet(tick_path, index=False, engine="pyarrow")
    print(f"\nAlso saved {len(tick_df):,} tick-like records to {tick_path}")


def main() -> None:
    asyncio.run(fetch_candles())


if __name__ == "__main__":
    main()
