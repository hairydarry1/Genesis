"""
run_index_pipeline.py — End-to-end pipeline for any Deriv synthetic index.

Runs the full lifecycle:
  1. Fetch 1-min candles from Deriv API
  2. Resample to multi-timeframe bars
  3. Precompute structural features
  4. Build ML dataset (states, targets, timestamps)
  5. Train the validated 8-feature tiny world model
  6. Walk-forward evaluation against baselines

Usage:
    python run_index_pipeline.py --symbol R_10 --name volatility_10
    python run_index_pipeline.py --symbol BOOM500 --name boom_500
    python run_index_pipeline.py --symbol CRASH500 --name crash_500
    python run_index_pipeline.py --symbol R_75 --name volatility_75 --skip-fetch
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Config per index type
# ---------------------------------------------------------------------------
INDEX_CONFIGS = {
    "volatility": {
        "vol_scale": 1.0,       # ATR thresholds relative to R75
        "spike_threshold": 3.0,  # multiplier of rolling std for spike detection
    },
    "boom": {
        "vol_scale": 0.5,       # Boom/Crash have different volatility characteristics
        "spike_threshold": 4.0,  # spikes are more extreme in boom/crash
    },
    "crash": {
        "vol_scale": 0.5,
        "spike_threshold": 4.0,
    },
}


def infer_index_type(symbol: str) -> str:
    """Infer index type from Deriv symbol."""
    s = symbol.upper()
    if "BOOM" in s:
        return "boom"
    elif "CRASH" in s:
        return "crash"
    else:
        return "volatility"


# ---------------------------------------------------------------------------
# Step 1: Fetch candles
# ---------------------------------------------------------------------------
async def fetch_candles(
    symbol: str,
    output_path: Path,
    target_days: int = 180,
    granularity: int = 60,
) -> Path:
    """Fetch 1-min candles from Deriv API with pagination."""
    from data.deriv_connection import DerivConnection

    conn = DerivConnection()
    await conn.connect()

    all_candles = []
    chunk_num = 0
    consecutive_timeouts = 0
    target_start = int(time.time()) - target_days * 86400

    print(f"\n{'='*60}")
    print(f"STEP 1: Fetching candles for {symbol}")
    print(f"  Target: {target_days} days, granularity: {granularity}s")
    print(f"{'='*60}")

    try:
        current_end = "latest"
        max_chunks = 3000

        while chunk_num < max_chunks:
            req = {
                "ticks_history": symbol,
                "end": current_end if isinstance(current_end, str) else int(current_end),
                "style": "candles",
                "count": 1000,
                "granularity": granularity,
                "adjust_start_time": 1,
            }

            try:
                resp = await conn.send(req, timeout=30)
                consecutive_timeouts = 0
            except TimeoutError:
                consecutive_timeouts += 1
                if consecutive_timeouts >= 5:
                    print(f"\n  5 consecutive timeouts - stopping.")
                    break
                await asyncio.sleep(5.0)
                continue

            if "error" in resp:
                print(f"\n  API error: {resp['error']}")
                break

            candles = resp.get("candles", [])
            if not candles:
                break

            all_candles.extend(candles)
            chunk_num += 1

            earliest_epoch = min(c["epoch"] for c in candles)
            if chunk_num % 20 == 0 or chunk_num == 1:
                ts = pd.Timestamp(earliest_epoch, unit="s", tz="UTC")
                print(f"  Chunk {chunk_num}: {len(all_candles):,} candles, earliest: {ts.date()}")

            if earliest_epoch <= target_start:
                print(f"\n  Reached target date ({target_days} days)")
                break

            current_end = int(earliest_epoch) - 1
            await asyncio.sleep(0.4)

    finally:
        await conn.close()

    if not all_candles:
        print("No candles fetched!")
        sys.exit(1)

    df = pd.DataFrame(all_candles)
    df = df.drop_duplicates(subset=["epoch"], keep="last")
    df = df.sort_values("epoch").reset_index(drop=True)
    df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(output_path, index=False, engine="pyarrow")

    span_days = (df["timestamp"].max() - df["timestamp"].min()).total_seconds() / 86400
    print(f"\n  Saved {len(df):,} candles to {output_path}")
    print(f"  Range: {df['timestamp'].min().date()} to {df['timestamp'].max().date()}")
    print(f"  Span: {span_days:.1f} days")
    print(f"  Price: {df['close'].min():.2f} to {df['close'].max():.2f}")

    # Also save tick-like format for the feature pipeline
    tick_df = df[["epoch", "timestamp"]].copy()
    tick_df["price"] = df["close"]
    # Save as ticks_<name>.parquet (strip _1min from candles filename)
    tick_name = output_path.stem.replace('candles_', '').replace('_1min', '')
    tick_path = output_path.parent / f"ticks_{tick_name}.parquet"
    tick_df.to_parquet(tick_path, index=False, engine="pyarrow")
    print(f"  Tick-like: {tick_path}")

    return output_path


# ---------------------------------------------------------------------------
# Step 2: Precompute structures
# ---------------------------------------------------------------------------
def precompute_structures(
    ticks_path: Path,
    structures_path: Path,
    freq: str = "1min",
) -> Path:
    """Precompute structural features."""
    from data.resample import resample_ticks
    from features.precompute import precompute_structures as _precompute, structures_to_dataframe

    print(f"\n{'='*60}")
    print(f"STEP 2: Precomputing structures")
    print(f"{'='*60}")

    ticks = pd.read_parquet(ticks_path, engine="pyarrow")
    print(f"  Loaded {len(ticks):,} ticks")

    t0 = time.time()
    bars = resample_ticks(ticks, freq=freq)
    print(f"  Resampled to {len(bars):,} {freq} bars in {time.time()-t0:.1f}s")

    t0 = time.time()
    structures = _precompute(bars, chunk_size=3000, overlap=500)
    elapsed = time.time() - t0
    print(f"  Detected {len(structures)} structures in {elapsed:.1f}s")

    df = structures_to_dataframe(structures)
    structures_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(structures_path, index=False, engine="pyarrow")
    print(f"  Saved to {structures_path}")

    return structures_path


# ---------------------------------------------------------------------------
# Step 3: Build dataset
# ---------------------------------------------------------------------------
def build_dataset(
    candles_path: Path,
    structures_path: Path,
    output_path: Path,
    horizon: int = 10,
    instrument_cfg: dict | None = None,
) -> Path:
    """Build the ML dataset (states, targets, timestamps)."""
    from data.resample import resample_ticks
    from features.state import StateBuilder

    print(f"\n{'='*60}")
    print(f"STEP 3: Building dataset (horizon={horizon})")
    print(f"{'='*60}")

    # Load candles
    candles_df = pd.read_parquet(candles_path, engine="pyarrow")
    # The build_dataset function needs 'timestamp' and 'close' columns
    if "timestamp" not in candles_df.columns:
        candles_df["timestamp"] = pd.to_datetime(candles_df["epoch"], unit="s", utc=True)
    candles_df = (
        candles_df.sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )
    print(f"  Candles: {len(candles_df):,}")

    # Build state builder (structures loaded lazily from parquet cache)
    tick_name = candles_path.stem.replace('candles_', '').replace('_1min', '')
    tick_path = candles_path.parent / f"ticks_{tick_name}.parquet"
    ticks = pd.read_parquet(tick_path, engine="pyarrow")

    state_builder = StateBuilder(
        ticks=ticks,
        structures_path=str(structures_path),
        timeframes=["1min", "15min", "1h"],
        instrument_cfg=instrument_cfg,
    )

    # Build states with the vectorized batch method
    n_bars = len(candles_df)
    min_lookback = 100
    valid_start = min_lookback
    valid_end = n_bars - horizon
    timestamps = candles_df["timestamp"].iloc[valid_start:valid_end].values
    print(f"  Valid range: [{valid_start}, {valid_end}) — {len(timestamps):,} samples")

    t0 = time.time()
    states, feature_names = state_builder.build_all_states(timestamps)

    # Build targets: log returns over the next `horizon` bars
    closes = candles_df["close"].values.astype(np.float64)
    targets = np.zeros((states.shape[0], horizon), dtype=np.float32)
    for i in range(states.shape[0]):
        bar_i = valid_start + i
        current_close = closes[bar_i]
        if current_close <= 0:
            continue
        for h in range(horizon):
            targets[i, h] = np.log(closes[bar_i + h + 1] / current_close)
    elapsed = time.time() - t0

    # Filter NaN rows
    nan_mask = np.any(np.isnan(states), axis=1) | np.any(np.isnan(targets), axis=1)
    n_nan = int(nan_mask.sum())
    if n_nan > 0:
        print(f"  Filtering {n_nan} NaN rows...")
        states = states[~nan_mask]
        targets = targets[~nan_mask]
        timestamps = timestamps[~nan_mask]

    # Normalize using train-portion stats only (prevents leakage)
    train_split = int(states.shape[0] * 0.85)
    feat_mean = np.nanmean(states[:train_split], axis=0).astype(np.float32)
    feat_std = np.nanstd(states[:train_split], axis=0).astype(np.float32)
    feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)
    states_norm = (states - feat_mean) / feat_std

    print(f"  Dataset: {states.shape[0]:,} samples, {states.shape[1]} features")
    print(f"  Time range: {pd.Timestamp(timestamps.min()).date()} to {pd.Timestamp(timestamps.max()).date()}")
    print(f"  Built in {elapsed:.1f}s")

    # Save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output_path,
        states=states_norm.astype(np.float32),
        targets=targets.astype(np.float32),
        timestamps=timestamps,
        feature_names=np.array(feature_names),
        feat_mean=feat_mean.astype(np.float32),
        feat_std=feat_std.astype(np.float32),
    )
    print(f"  Saved to {output_path}")

    return output_path


# ---------------------------------------------------------------------------
# Step 4: Train tiny model
# ---------------------------------------------------------------------------
def train_tiny_model(
    dataset_path: Path,
    output_dir: Path,
    epochs: int = 50,
    device: str | None = None,
) -> dict:
    """Train the validated 8-feature tiny world model."""
    from model.train_tiny import train_tiny

    print(f"\n{'='*60}")
    print(f"STEP 4: Training tiny model")
    print(f"{'='*60}")

    results = train_tiny(
        dataset_path=str(dataset_path),
        output_dir=str(output_dir),
        epochs=epochs,
        device_str=device,
    )

    return results


# ---------------------------------------------------------------------------
# Step 5: Walk-forward evaluation
# ---------------------------------------------------------------------------
def evaluate_model(
    checkpoint_path: Path,
    dataset_path: Path,
    device: str | None = None,
) -> dict:
    """Run walk-forward evaluation."""
    from model.train_tiny import evaluate_walk_forward

    print(f"\n{'='*60}")
    print(f"STEP 5: Walk-forward evaluation")
    print(f"{'='*60}")

    report = evaluate_walk_forward(
        checkpoint_path=str(checkpoint_path),
        dataset_path=str(dataset_path),
        device_str=device,
    )

    return report


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    symbol: str,
    name: str,
    target_days: int = 180,
    horizon: int = 10,
    epochs: int = 50,
    skip_fetch: bool = False,
    device: str | None = None,
) -> dict:
    """Run the full pipeline for a single index."""
    base_dir = Path("data")
    index_dir = Path(f"checkpoints_{name}")

    # Paths
    candles_path = base_dir / f"candles_{name}_1min.parquet"
    tick_path = base_dir / f"ticks_{name}.parquet"
    structures_path = base_dir / f"structures_{name}.parquet"
    dataset_path = base_dir / f"dataset_{name}_h{horizon}.npz"
    checkpoint_dir = index_dir

    print(f"\n{'#'*80}")
    print(f"# PIPELINE: {name} ({symbol})")
    print(f"# Candles: {candles_path}")
    print(f"# Dataset: {dataset_path}")
    print(f"# Checkpoints: {checkpoint_dir}")
    print(f"{'#'*80}")

    index_type = infer_index_type(symbol)
    cfg = INDEX_CONFIGS.get(index_type, INDEX_CONFIGS["volatility"])
    print(f"  Index type: {index_type}, config: {cfg}")

    # Step 1: Fetch candles
    if not skip_fetch or not candles_path.exists():
        asyncio.run(fetch_candles(symbol, candles_path, target_days=target_days))
    else:
        print(f"\n  Skipping fetch (candles exist: {candles_path})")

    # Step 2: Precompute structures
    if not structures_path.exists() and tick_path.exists():
        precompute_structures(tick_path, structures_path)
    elif structures_path.exists():
        print(f"\n  Skipping structure precompute (exists: {structures_path})")
    else:
        print(f"\n  WARNING: tick file not found at {tick_path}, skipping structures")

    # Step 3: Build dataset
    if not dataset_path.exists():
        build_dataset(candles_path, structures_path, dataset_path, horizon=horizon)
    else:
        print(f"\n  Skipping dataset build (exists: {dataset_path})")

    # Step 4: Train
    results = train_tiny_model(dataset_path, checkpoint_dir, epochs=epochs, device=device)

    # Step 5: Evaluate
    checkpoint_path = checkpoint_dir / "world_model_best.pt"
    if checkpoint_path.exists():
        report = evaluate_model(checkpoint_path, dataset_path, device=device)
    else:
        report = {"error": "no checkpoint found"}

    # Summary
    print(f"\n{'#'*80}")
    print(f"# SUMMARY: {name}")
    print(f"{'#'*80}")
    print(f"  Verdict: {report.get('verdict', 'UNKNOWN')}")
    agg = report.get("aggregate", {})
    print(f"  WM NLL: {agg.get('wm_nll', 'N/A')}")
    print(f"  RW NLL: {agg.get('rw_nll', 'N/A')}")
    print(f"  Beat RW: {agg.get('beat_rw', 'N/A')}")
    print(f"  Folds: {agg.get('n_beat', 0)}/{agg.get('n_folds', 0)}")
    print(f"  1sig: {agg.get('wm_1sig', 0):.1%}  2sig: {agg.get('wm_2sig', 0):.1%}")

    return report


def main():
    parser = argparse.ArgumentParser(description="End-to-end pipeline for any Deriv synthetic index")
    parser.add_argument("--symbol", required=True, help="Deriv API symbol (e.g. R_10, BOOM500, CRASH500)")
    parser.add_argument("--name", required=True, help="Short name for this index (e.g. volatility_10, boom_500)")
    parser.add_argument("--days", type=int, default=180, help="Days of historical data to fetch")
    parser.add_argument("--horizon", type=int, default=10, help="Prediction horizon in bars")
    parser.add_argument("--epochs", type=int, default=50, help="Training epochs")
    parser.add_argument("--skip-fetch", action="store_true", help="Skip data fetch if candles exist")
    parser.add_argument("--device", default=None, help="Device (cpu/cuda)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = run_pipeline(
        symbol=args.symbol,
        name=args.name,
        target_days=args.days,
        horizon=args.horizon,
        epochs=args.epochs,
        skip_fetch=args.skip_fetch,
        device=args.device,
    )

    # Save final report
    report_path = Path(f"checkpoints_{args.name}/final_report.json")
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
