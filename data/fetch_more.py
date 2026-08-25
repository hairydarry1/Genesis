"""
data/fetch_more.py — Continue fetching tick history from Deriv API.

Resumes from the earliest epoch in the existing Parquet file.
Handles timeouts gracefully by pausing and retrying.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pandas as pd
import structlog

from data.deriv_connection import DerivConnection
from data.ingest import TickIngester

logger = structlog.get_logger(__name__)

PARQUET_PATH = Path("data/ticks_R75.parquet")
SYMBOL = "R_75"
CHUNK_SIZE = 1000  # Deriv caps at 1000 per request
PAUSE_BETWEEN_CHUNKS = 0.5  # seconds
PAUSE_ON_TIMEOUT = 5.0       # seconds to wait after a timeout before retrying
MAX_TIMEOUT_RETRIES = 3


async def fetch_more_ticks() -> None:
    """Load existing data, then fetch more ticks going backwards."""
    # Load existing data to find the earliest epoch
    if PARQUET_PATH.exists():
        existing = pd.read_parquet(PARQUET_PATH, engine="pyarrow")
        earliest_epoch = int(existing["epoch"].min())
        print(f"Existing data: {len(existing):,} ticks, earliest epoch: {earliest_epoch}")
        print(f"  Range: {existing['timestamp'].min()} to {existing['timestamp'].max()}")
    else:
        earliest_epoch = None
        existing = pd.DataFrame()
        print("No existing data found, starting fresh")

    conn = DerivConnection()
    await conn.connect()

    try:
        ingester = TickIngester(conn, symbol=SYMBOL)

        # Fetch all available history going backwards from earliest_epoch
        print(f"\nFetching more ticks for {SYMBOL}...")
        print(f"  Starting from epoch: {earliest_epoch}")
        print()

        new_records = []
        current_end = earliest_epoch - 1 if earliest_epoch else "latest"
        total_new = 0
        chunk_num = 0
        consecutive_timeouts = 0

        while True:
            count = CHUNK_SIZE

            req = {
                "ticks_history": SYMBOL,
                "end": current_end if isinstance(current_end, str) else int(current_end),
                "style": "ticks",
                "count": count,
                "adjust_start_time": 1,
            }

            try:
                resp = await conn.send(req, timeout=30)
                consecutive_timeouts = 0  # reset on success
            except TimeoutError:
                consecutive_timeouts += 1
                if consecutive_timeouts >= MAX_TIMEOUT_RETRIES:
                    print(f"\n  {MAX_TIMEOUT_RETRIES} consecutive timeouts — stopping.")
                    break
                print(f"  Timeout on chunk {chunk_num}, retrying in {PAUSE_ON_TIMEOUT}s... "
                      f"({consecutive_timeouts}/{MAX_TIMEOUT_RETRIES})")
                await asyncio.sleep(PAUSE_ON_TIMEOUT)
                continue

            if "error" in resp:
                print(f"\n  API error: {resp['error']}")
                break

            history = resp.get("history", {})
            prices = history.get("prices", [])
            times = history.get("times", [])

            if not prices or not times:
                print(f"\n  No more data returned at chunk {chunk_num}")
                break

            records = [{"epoch": int(t), "price": float(p)} for t, p in zip(times, prices)]
            new_records.extend(records)
            total_new += len(records)
            chunk_num += 1

            earliest_epoch_new = records[0]["epoch"]
            latest_epoch_new = records[-1]["epoch"]

            if chunk_num % 20 == 0:
                print(f"  Chunk {chunk_num}: +{total_new:,} new ticks "
                      f"(earliest: {pd.Timestamp(earliest_epoch_new, unit='s', tz='UTC')})")

            # Move end pointer before the earliest record of this chunk
            current_end = int(earliest_epoch_new) - 1

            # Check if we've gone far enough back (target: 90 days)
            target_start = int(time.time()) - 90 * 86400
            if earliest_epoch_new <= target_start:
                print(f"\n  Reached target date ({90} days back)")
                break

            # Detect stall
            if chunk_num > 1 and earliest_epoch_new == earliest_epoch_new:
                pass  # will be caught by timeout eventually

            await asyncio.sleep(PAUSE_BETWEEN_CHUNKS)

        # Combine old and new data
        if new_records:
            new_df = pd.DataFrame(new_records)
            new_df = new_df.drop_duplicates(subset=["epoch"], keep="last")
            new_df = new_df.sort_values("epoch").reset_index(drop=True)
            new_df["timestamp"] = pd.to_datetime(new_df["epoch"], unit="s", utc=True)

            if len(existing) > 0:
                combined = pd.concat([new_df, existing], ignore_index=True)
                combined = combined.drop_duplicates(subset=["epoch"], keep="last")
                combined = combined.sort_values("epoch").reset_index(drop=True)
            else:
                combined = new_df

            # Save
            combined.to_parquet(PARQUET_PATH, index=False, engine="pyarrow")

            span_days = (combined["timestamp"].max() - combined["timestamp"].min()).total_seconds() / 86400
            print(f"\nSaved {len(combined):,} total ticks to {PARQUET_PATH}")
            print(f"  Range: {combined['timestamp'].min()} to {combined['timestamp'].max()}")
            print(f"  Span: {span_days:.1f} days")
            print(f"  Price: {combined['price'].min():.2f} to {combined['price'].max():.2f}")
        else:
            print("\nNo new data fetched.")

    finally:
        await conn.close()


def main() -> None:
    asyncio.run(fetch_more_ticks())


if __name__ == "__main__":
    main()
