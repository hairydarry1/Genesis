"""
model/precompute_dataset.py — Pre-compute and cache (states, targets) for fast training.

Saves to data/dataset_R75_1min.npz so training loads instantly.

Usage:
    python -m model.precompute_dataset
    python -m model.precompute_dataset --horizon 10 --min-bars 20000
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd

from features.state import StateBuilder


def main() -> None:
    parser = argparse.ArgumentParser(description="Pre-compute dataset (states + targets)")
    parser.add_argument("--data", default="data/candles_R75_1min.parquet")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--min-bars", type=int, default=0, help="0 = use all")
    parser.add_argument("--output", default=None, help="Output path (default: data/dataset_R75_h{horizon}.npz)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Load candles
    print(f"Loading candles from {args.data}...")
    candles = pd.read_parquet(args.data, engine="pyarrow")
    candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True).dt.tz_localize(None)
    candles = candles.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)

    if args.min_bars > 0:
        candles = candles.iloc[:args.min_bars].copy()

    print(f"Candles: {len(candles)} bars, {candles.timestamp.min()} to {candles.timestamp.max()}")

    # Build state builder
    structs_path = str(Path(args.data).parent / "structures_R75.parquet")
    print(f"Building StateBuilder with structures from {structs_path}...")
    t0 = time.time()
    sb = StateBuilder(candles, structures_path=structs_path)
    print(f"StateBuilder ready in {time.time()-t0:.1f}s")

    # Compute targets (future returns) directly
    print("Computing targets...")
    closes = candles["close"].values.astype(np.float64)
    n_bars = len(candles)
    min_lookback = 100
    horizon = args.horizon

    valid_start = min_lookback
    valid_end = n_bars - horizon

    print(f"Valid range: [{valid_start}, {valid_end}) — {valid_end - valid_start} samples")

    # Build timestamp array for all valid positions
    timestamps = candles["timestamp"].iloc[valid_start:valid_end].values
    print(f"Timestamps: {len(timestamps)}")

    # Use batch method for states
    print("Building all states with batch method...")
    t0 = time.time()
    states, feature_names = sb.build_all_states(timestamps)
    elapsed = time.time() - t0
    print(f"States built: {states.shape} in {elapsed:.1f}s ({states.shape[0]/elapsed:.0f} samples/s)")

    # Build targets
    print("Building targets...")
    targets = np.zeros((states.shape[0], horizon), dtype=np.float32)
    for i in range(states.shape[0]):
        bar_i = valid_start + i
        current_close = closes[bar_i]
        for h in range(horizon):
            targets[i, h] = np.log(closes[bar_i + h + 1] / current_close) if current_close > 0 else 0.0

    # Filter NaN rows
    nan_mask = np.any(np.isnan(states), axis=1) | np.any(np.isnan(targets), axis=1)
    n_nan = int(nan_mask.sum())
    if n_nan > 0:
        print(f"Filtering {n_nan} NaN rows...")
        states = states[~nan_mask]
        targets = targets[~nan_mask]
        timestamps = timestamps[~nan_mask]

    print(f"Final dataset: {states.shape[0]} samples, {states.shape[1]} features, horizon={horizon}")

    # --- Z-score normalization ---
    # Compute mean/std on first 85% (training portion) to prevent data leakage
    train_split = int(states.shape[0] * 0.85)
    train_states = states[:train_split]

    feat_mean = np.nanmean(train_states, axis=0).astype(np.float32)
    feat_std = np.nanstd(train_states, axis=0).astype(np.float32)
    feat_std = np.where(feat_std < 1e-8, 1.0, feat_std)  # avoid division by zero

    # Apply z-score normalization
    states = (states - feat_mean) / feat_std
    print(f"Applied z-score normalization: mean range [{feat_mean.min():.4f}, {feat_mean.max():.4f}], std range [{feat_std.min():.6f}, {feat_std.max():.6f}]")
    print(f"Normalized states range: [{np.nanmin(states):.4f}, {np.nanmax(states):.4f}]")

    # Save
    output = args.output or f"data/dataset_R75_h{horizon}.npz"
    Path(output).parent.mkdir(parents=True, exist_ok=True)

    np.savez_compressed(
        output,
        states=states,
        targets=targets,
        timestamps=timestamps.astype("datetime64[ns]"),
        feature_names=np.array(feature_names),
        feat_mean=feat_mean,
        feat_std=feat_std,
    )
    size_mb = Path(output).stat().st_size / 1e6
    print(f"Saved to {output} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
