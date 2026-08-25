"""
data/ingest.py — Pull historical tick data from Deriv API and store as Parquet.

Uses DerivConnection from deriv_connection.py for all websocket communication.

Usage:
    # As a script (public endpoint, no auth needed):
    python -m data.ingest --symbol R_75 --output data/ticks_R75.parquet --days 90

    # As a module:
    from data.deriv_connection import DerivConnection
    from data.ingest import TickIngester

    conn = DerivConnection()
    await conn.connect()
    ingester = TickIngester(conn, symbol="R_75")
    df = await ingester.fetch_all_ticks(start_epoch=..., end_epoch=...)
    ingester.save(df, "data/ticks_R75.parquet")
    await conn.close()
"""

from __future__ import annotations

import asyncio
import argparse
import time
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from data.deriv_connection import DerivConnection

logger = structlog.get_logger(__name__)

# Deriv API limits the count parameter — we paginate in chunks
MAX_CHUNK_SIZE = 5000

TICK_COLUMNS = ["epoch", "price"]
CANDLE_COLUMNS = ["epoch", "open", "high", "low", "close"]


class TickIngester:
    """
    Fetch historical ticks from Deriv and store to Parquet.

    Parameters
    ----------
    connection : DerivConnection
        An already-connected DerivConnection instance.
    symbol : str
        E.g. "R_75", "1HZ10V".
    """

    def __init__(self, connection: DerivConnection, symbol: str) -> None:
        self.conn = connection
        self.symbol = symbol

    # -- Fetch historical data ------------------------------------------------

    async def fetch_all_ticks(
        self,
        start_epoch: int | None = None,
        end_epoch: int | str = "latest",
        style: str = "ticks",
        max_ticks: int | None = None,
    ) -> pd.DataFrame:
        """
        Fetch tick history by paginating backwards from *end_epoch*.

        Parameters
        ----------
        start_epoch : int | None
            Earliest epoch to fetch.  None means go as far back as the API allows.
        end_epoch : int | str
            End point — "latest" or an epoch timestamp.
        style : str
            "ticks" for raw tick data, "candles" for OHLC bars.
        max_ticks : int | None
            Hard cap on total ticks to fetch.  None = no limit (fetch all available).

        Returns
        -------
        pd.DataFrame with columns [epoch, price] (ticks) or [epoch, open, high, low, close] (candles).
        """
        log = logger.bind(symbol=self.symbol, style=style)
        log.info("ingest.fetch_started", start_epoch=start_epoch, end_epoch=end_epoch)

        all_records: list[dict[str, Any]] = []
        current_end = end_epoch
        total_fetched = 0
        chunk_num = 0
        seen_earliest: int | None = None

        while True:
            count = MAX_CHUNK_SIZE
            if max_ticks is not None:
                count = min(count, max_ticks - total_fetched)

            if count <= 0:
                break

            req: dict[str, Any] = {
                "ticks_history": self.symbol,
                "end": current_end if isinstance(current_end, str) else int(current_end),
                "style": style,
                "count": count,
                "adjust_start_time": 1,
            }
            if start_epoch is not None:
                req["start"] = start_epoch

            log.debug("ingest.fetch_chunk", count=count, end=current_end)

            try:
                resp = await self.conn.send(req)
            except Exception as exc:
                log.error("ingest.fetch_error", error=str(exc))
                break

            # Check for API-level errors (Deriv returns {"error": {...}})
            if "error" in resp:
                log.error("ingest.api_error", error=resp["error"])
                break

            # Parse response based on style
            if style == "ticks":
                records = self._parse_ticks(resp)
            elif style == "candles":
                records = self._parse_candles(resp)
            else:
                raise ValueError(f"Unknown style: {style}")

            if not records:
                log.info("ingest.no_more_data", total_fetched=total_fetched)
                break

            all_records.extend(records)
            total_fetched += len(records)
            chunk_num += 1

            # The API returns ticks in chronological order — records[0] is the
            # oldest tick in this chunk.  Move the end pointer just before it
            # so the next request picks up where this one left off.
            earliest_epoch = records[0]["epoch"]

            # Progress log every 10 chunks so long pulls stay visible
            if chunk_num % 10 == 0 or chunk_num == 1:
                latest_epoch = records[-1]["epoch"]
                log.info(
                    "ingest.progress",
                    chunk=chunk_num,
                    chunk_size=len(records),
                    total=total_fetched,
                    earliest_epoch=earliest_epoch,
                    latest_epoch=latest_epoch,
                )

            # Safety: detect stalled pagination (API returning identical data)
            if seen_earliest is not None and earliest_epoch == seen_earliest:
                log.warning(
                    "ingest.stalled",
                    earliest_epoch=earliest_epoch,
                    total_fetched=total_fetched,
                )
                break
            seen_earliest = earliest_epoch

            # Stop when we've reached or passed the requested start boundary
            if earliest_epoch <= (start_epoch or 0):
                log.info(
                    "ingest.reached_start",
                    earliest_epoch=earliest_epoch,
                    total_fetched=total_fetched,
                    chunks=chunk_num,
                )
                break

            current_end = int(earliest_epoch) - 1

            # Respect rate limits — small pause between chunks
            await asyncio.sleep(0.3)

            # The public endpoint may silently cap count below what we asked
            # for.  That is NOT end-of-data — keep paginating until the API
            # returns an empty response or we hit the start boundary above.
            if len(records) < count:
                log.debug(
                    "ingest.chunk_capped",
                    requested=count,
                    received=len(records),
                )

        # Build DataFrame
        df = self._records_to_df(all_records, style)
        log.info(
            "ingest.fetch_complete",
            rows=len(df),
            start=df["epoch"].min() if len(df) else None,
            end=df["epoch"].max() if len(df) else None,
        )
        return df

    # -- Parse API responses ---------------------------------------------------

    @staticmethod
    def _parse_ticks(resp: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract tick records from a ticks_history response."""
        history = resp.get("history", {})
        prices = history.get("prices", [])
        times = history.get("times", [])
        if not prices or not times:
            return []
        return [{"epoch": int(t), "price": float(p)} for t, p in zip(times, prices)]

    @staticmethod
    def _parse_candles(resp: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract candle records from a ticks_history response (style=candles)."""
        candles = resp.get("candles", [])
        return [
            {
                "epoch": int(c["epoch"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
            for c in candles
        ]

    # -- DataFrame conversion --------------------------------------------------

    @staticmethod
    def _records_to_df(records: list[dict], style: str) -> pd.DataFrame:
        if not records:
            cols = TICK_COLUMNS if style == "ticks" else CANDLE_COLUMNS
            return pd.DataFrame(columns=cols)

        df = pd.DataFrame(records)
        # Deduplicate by epoch (keep last)
        df = df.drop_duplicates(subset=["epoch"], keep="last")
        # Sort chronologically
        df = df.sort_values("epoch").reset_index(drop=True)
        # Convert epoch to datetime
        df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
        return df

    # -- Storage ---------------------------------------------------------------

    @staticmethod
    def save(df: pd.DataFrame, path: str | Path) -> Path:
        """
        Save tick/candle DataFrame to Parquet.

        Creates parent directories if needed.
        """
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out, index=False, engine="pyarrow")
        logger.info("ingest.saved", path=str(out), rows=len(df))
        return out

    @staticmethod
    def load(path: str | Path) -> pd.DataFrame:
        """Load a previously saved tick/candle Parquet file."""
        df = pd.read_parquet(path, engine="pyarrow")
        logger.info("ingest.loaded", path=str(path), rows=len(df))
        return df


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

async def _main(
    symbol: str,
    output: str,
    days: int,
    style: str,
) -> None:
    conn = DerivConnection()
    await conn.connect()

    try:
        ingester = TickIngester(conn, symbol)
        now = int(time.time())
        start = now - days * 86400

        print(
            f"Fetching {style} for {symbol}, "
            f"{days} days back from now …"
        )

        df = await ingester.fetch_all_ticks(
            start_epoch=start,
            end_epoch="latest",
            style=style,
        )

        if len(df) == 0:
            print("No data returned — check symbol and network.")
            return

        saved = ingester.save(df, output)
        ts_min = df["timestamp"].min()
        ts_max = df["timestamp"].max()
        span_days = (ts_max - ts_min).total_seconds() / 86400
        print(
            f"Saved {len(df):,} records to {saved}\n"
            f"  Range : {ts_min} → {ts_max}  ({span_days:.1f} days)\n"
            f"  Price : {df['price'].min():.2f} → {df['price'].max():.2f}"
        )
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest Deriv tick history to Parquet")
    parser.add_argument("--symbol", type=str, default="R_75", help="Symbol (default: R_75)")
    parser.add_argument("--output", type=str, default="data/ticks_R75.parquet", help="Output Parquet path")
    parser.add_argument("--days", type=int, default=90, help="How many days of history (default: 90)")
    parser.add_argument("--style", type=str, default="ticks", choices=["ticks", "candles"], help="Data style")
    args = parser.parse_args()

    asyncio.run(_main(args.symbol, args.output, args.days, args.style))


if __name__ == "__main__":
    main()
