"""
data/resample.py — Resample raw ticks into fixed-interval OHLC(V) bars.

Supports multiple resolutions so features can be built at parallel timeframes.

Usage:
    from data.resample import resample_ticks
    bars_1s = resample_ticks(ticks_df, freq="1s")
    bars_1m = resample_ticks(ticks_df, freq="1min")
    bars_15m = resample_ticks(ticks_df, freq="15min")
    bars_1h = resample_ticks(ticks_df, freq="1h")

    # Or batch all standard resolutions:
    from data.resample import resample_all
    all_bars = resample_all(ticks_df, freqs=["5s", "1min", "15min", "1h"])
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# Standard resolutions for the world model's multi-timeframe features
DEFAULT_FREQS = ["5s", "1min", "15min", "1h"]


def resample_ticks(
    ticks: pd.DataFrame,
    freq: str,
    min_ticks_per_bar: int = 1,
) -> pd.DataFrame:
    """
    Resample a raw tick DataFrame into OHLC bars at a given frequency.

    Parameters
    ----------
    ticks : pd.DataFrame
        Must contain columns: ['timestamp', 'price'].
        'timestamp' should be timezone-aware (UTC) datetime.
    freq : str
        Pandas-compatible frequency string, e.g. "1s", "5s", "1min", "15min", "1h".
    min_ticks_per_bar : int
        Minimum ticks required to form a bar.  Bars with fewer ticks are dropped
        to avoid noisy micro-bars during low-activity periods.

    Returns
    -------
    pd.DataFrame with columns:
        [timestamp, open, high, low, close, volume, n_ticks, vwap]
    """
    df = ticks.copy()

    if "timestamp" not in df.columns or "price" not in df.columns:
        raise ValueError("ticks DataFrame must have 'timestamp' and 'price' columns")

    # Ensure datetime index for resampling
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    # Resample
    grouped = df["price"].resample(freq)

    bars = pd.DataFrame()
    bars["open"] = grouped.first()
    bars["high"] = grouped.max()
    bars["low"] = grouped.min()
    bars["close"] = grouped.last()
    bars["volume"] = grouped.count()  # number of ticks in bar
    bars["n_ticks"] = grouped.count()

    # VWAP (volume-weighted average price) — approximate with mean of ticks
    bars["vwap"] = grouped.mean()

    # Drop bars with insufficient ticks
    bars = bars.dropna(subset=["open", "close"])
    bars = bars[bars["n_ticks"] >= min_ticks_per_bar]

    # Reset index and add epoch column
    bars = bars.reset_index()
    bars["epoch"] = bars["timestamp"].astype("int64") // 10**9

    # Reorder columns
    bars = bars[["timestamp", "epoch", "open", "high", "low", "close", "volume", "n_ticks", "vwap"]]

    logger.info(
        "resample.complete",
        freq=freq,
        input_ticks=len(df),
        output_bars=len(bars),
    )
    return bars


def resample_all(
    ticks: pd.DataFrame,
    freqs: list[str] | None = None,
    min_ticks_per_bar: int = 1,
) -> dict[str, pd.DataFrame]:
    """
    Resample ticks into multiple timeframes.

    Returns a dict mapping frequency string -> bar DataFrame.
    """
    if freqs is None:
        freqs = DEFAULT_FREQS

    result: dict[str, pd.DataFrame] = {}
    for freq in freqs:
        result[freq] = resample_ticks(ticks, freq=freq, min_ticks_per_bar=min_ticks_per_bar)

    logger.info("resample.all_complete", freqs=freqs, bar_counts={k: len(v) for k, v in result.items()})
    return result


def resample_bars(
    bars: pd.DataFrame,
    freq: str,
    source_freq: str = "1min",
) -> pd.DataFrame:
    """
    Resample existing OHLCV bars into a coarser frequency.

    Parameters
    ----------
    bars : pd.DataFrame
        Must contain columns: ['timestamp', 'open', 'high', 'low', 'close'].
        Optionally: 'volume', 'n_ticks', 'vwap'.
    freq : str
        Target frequency, e.g. "15min", "1h".
    source_freq : str
        The frequency of the input bars, used for volume counting.

    Returns
    -------
    pd.DataFrame with OHLCV bars at the target frequency.
    """
    df = bars.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.set_index("timestamp").sort_index()

    has_volume = "volume" in df.columns

    agg_dict = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
    }
    if has_volume:
        agg_dict["volume"] = "sum"

    result = df.resample(freq).agg(agg_dict).dropna(subset=["open", "close"])
    result["n_ticks"] = 1  # each row is one source bar
    if has_volume:
        result["vwap"] = (result["close"] + result["open"]) / 2  # approx

    result = result.reset_index()
    result["epoch"] = result["timestamp"].astype("int64") // 10**9

    # Reorder columns to match resample_ticks output
    cols = ["timestamp", "epoch", "open", "high", "low", "close", "volume"] if has_volume else [
        "timestamp", "epoch", "open", "high", "low", "close"
    ]
    result = result[[c for c in cols if c in result.columns]]

    logger.info("resample_bars.complete", freq=freq, input_bars=len(bars), output_bars=len(result))
    return result


def validate_bars(bars: pd.DataFrame, freq: str) -> dict[str, Any]:
    """
    Sanity-check a bar DataFrame for gaps and consistency.

    Returns a dict of diagnostic info:
        - expected_count: how many bars should exist in the time span
        - actual_count: how many bars we have
        - gap_count: number of missing bars
        - has_gaps: bool
    """
    if len(bars) < 2:
        return {
            "expected_count": len(bars),
            "actual_count": len(bars),
            "gap_count": 0,
            "has_gaps": False,
        }

    ts = bars["timestamp"].sort_values().reset_index(drop=True)
    diffs = ts.diff().dropna()

    # Expected interval in nanoseconds
    freq_ns = pd.tseries.frequencies.to_offset(freq).nanos
    if freq_ns is None or freq_ns == 0:
        freq_ns = pd.Timedelta(freq).total_seconds() * 1e9

    # Count gaps: bars where the gap is more than 1.5x the expected interval
    threshold_ns = freq_ns * 1.5
    gap_mask = diffs.dt.total_seconds() * 1e9 > threshold_ns
    gap_count = int(gap_mask.sum())

    # Expected number of bars from first to last
    total_span = (ts.iloc[-1] - ts.iloc[0]).total_seconds() * 1e9
    expected = int(total_span / freq_ns) + 1

    result = {
        "expected_count": expected,
        "actual_count": len(bars),
        "gap_count": gap_count,
        "has_gaps": gap_count > 0,
    }

    if gap_count > 0:
        logger.warning("resample.gaps_detected", freq=freq, gaps=gap_count)

    return result


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: resample a tick Parquet file into bar Parquet files."""
    import argparse

    parser = argparse.ArgumentParser(description="Resample ticks to OHLC bars")
    parser.add_argument("input", help="Input tick Parquet file")
    parser.add_argument("--output-dir", default="data/bars", help="Output directory for bar Parquet files")
    parser.add_argument("--freqs", nargs="+", default=DEFAULT_FREQS, help="Frequencies to resample at")
    parser.add_argument("--min-ticks", type=int, default=1, help="Min ticks per bar")
    args = parser.parse_args()

    df = pd.read_parquet(args.input, engine="pyarrow")
    logger.info("resample.loaded", rows=len(df), path=args.input)

    all_bars = resample_all(df, freqs=args.freqs, min_ticks_per_bar=args.min_ticks)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    for freq, bars in all_bars.items():
        # Replace / in freq name for filename (e.g. "1min" -> "1min")
        safe_name = freq.replace("/", "_")
        path = out_dir / f"bars_{safe_name}.parquet"
        bars.to_parquet(path, index=False, engine="pyarrow")
        print(f"Saved {len(bars)} bars to {path}")

    # Validate
    for freq, bars in all_bars.items():
        diag = validate_bars(bars, freq)
        status = "OK" if not diag["has_gaps"] else f"GAPS: {diag['gap_count']}"
        print(f"  {freq}: {diag['actual_count']} bars, expected {diag['expected_count']} — {status}")


if __name__ == "__main__":
    main()
