"""
features/precompute.py — Pre-compute structures and cache to disk.

Avoids the 13s+ per-call cost of detecting structures on the fly.
Run once after data fetch, then StateBuilder loads from cache.

Usage:
    python -m features.precompute --input data/ticks_R75.parquet --output data/structures_R75.parquet
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import pandas as pd
import structlog

from data.resample import resample_ticks
from features.structures import (
    StructureInstance,
    detect_structures,
    detect_swing_points,
    compute_atr,
)

logger = structlog.get_logger(__name__)

# Chunk size for structure detection (balances memory vs speed)
CHUNK_SIZE = 3000
OVERLAP = 500  # overlap between chunks to avoid edge artifacts


def precompute_structures(
    bars: pd.DataFrame,
    atr_period: int = 14,
    swing_lookback: int = 5,
    instrument_cfg: dict | None = None,
    chunk_size: int = CHUNK_SIZE,
    overlap: int = OVERLAP,
) -> list[StructureInstance]:
    """
    Detect structures across the full bar series in chunks.
    
    Uses overlapping windows to avoid missing structures at chunk boundaries.
    Deduplicates overlapping detections.
    """
    n_bars = len(bars)
    all_structures: list[StructureInstance] = []
    seen = set()  # (stype, index, direction) to deduplicate
    
    start = 0
    chunk_num = 0
    
    while start < n_bars:
        end = min(start + chunk_size, n_bars)
        chunk = bars.iloc[start:end].copy().reset_index(drop=True)
        
        structures = detect_structures(
            chunk,
            atr_period=atr_period,
            swing_lookback=swing_lookback,
            instrument_cfg=instrument_cfg,
        )
        
        # Adjust indices to global position and deduplicate
        for s in structures:
            global_idx = s.index + start
            key = (s.stype, global_idx, s.direction)
            if key not in seen:
                s.index = global_idx
                all_structures.append(s)
                seen.add(key)
        
        chunk_num += 1
        if chunk_num % 10 == 0:
            logger.info(
                "precompute.progress",
                chunk=chunk_num,
                start=start,
                end=end,
                total_structures=len(all_structures),
            )
        
        # Move forward with overlap
        start = end - overlap if end < n_bars else n_bars
    
    all_structures.sort(key=lambda s: s.index)
    
    logger.info(
        "precompute.complete",
        n_bars=n_bars,
        n_structures=len(all_structures),
        chunks=chunk_num,
    )
    
    return all_structures


def structures_to_dataframe(structures: list[StructureInstance]) -> pd.DataFrame:
    """Convert structures to DataFrame for Parquet storage."""
    if not structures:
        return pd.DataFrame()
    
    rows = []
    for s in structures:
        rows.append({
            "stype": s.stype,
            "direction": s.direction,
            "zone_high": s.zone_high,
            "zone_low": s.zone_low,
            "index": s.index,
            "timestamp": s.timestamp,
            "size_ATR": s.size_ATR,
            "age_bars": s.age_bars,
        })
    return pd.DataFrame(rows)


def load_structures(path: str | Path) -> list[StructureInstance]:
    """Load pre-computed structures from Parquet."""
    df = pd.read_parquet(path, engine="pyarrow")
    structures = []
    for _, row in df.iterrows():
        structures.append(StructureInstance(
            stype=row["stype"],
            zone_high=row["zone_high"],
            zone_low=row["zone_low"],
            index=int(row["index"]),
            timestamp=pd.Timestamp(row["timestamp"]),
            direction=row["direction"],
            size_ATR=row["size_ATR"],
            age_bars=int(row["age_bars"]),
        ))
    return structures


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-compute structures for a dataset")
    parser.add_argument("--input", default="data/ticks_R75.parquet")
    parser.add_argument("--output", default="data/structures_R75.parquet")
    parser.add_argument("--freq", default="1min", help="Bar frequency for resampling")
    parser.add_argument("--atr-period", type=int, default=14)
    parser.add_argument("--swing-lookback", type=int, default=5)
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = parser.parse_args()
    
    # Load ticks
    ticks = pd.read_parquet(args.input, engine="pyarrow")
    print(f"Loaded {len(ticks)} ticks")
    
    # Resample to bars
    t0 = time.time()
    bars = resample_ticks(ticks, freq=args.freq)
    print(f"Resampled to {len(bars)} {args.freq} bars in {time.time()-t0:.1f}s")
    
    # Detect structures
    t0 = time.time()
    structures = precompute_structures(
        bars,
        atr_period=args.atr_period,
        swing_lookback=args.swing_lookback,
        chunk_size=args.chunk_size,
    )
    elapsed = time.time() - t0
    print(f"Detected {len(structures)} structures in {elapsed:.1f}s")
    
    # Save to Parquet
    df = structures_to_dataframe(structures)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(args.output, index=False, engine="pyarrow")
    print(f"Saved to {args.output}")


if __name__ == "__main__":
    main()
