"""
model/dataset.py — PyTorch Dataset for world model training.

Builds (state_vector, target) pairs from candle data.
Target = log return over the next H bars (multi-horizon).

Usage:
    from model.dataset import build_dataset
    X, y, ts = build_dataset(candles_df, state_builder, horizon=10)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import Dataset

logger = logging.getLogger(__name__)


class WorldModelDataset(Dataset):
    """
    PyTorch Dataset for the world model.

    Each sample is a (state_vector, target_returns) pair:
      - state_vector: float32 array of shape (n_features,)
      - target_returns: float32 array of shape (horizon,) — log returns
                        from current bar to each of the next H bars
    """

    def __init__(
        self,
        states: np.ndarray,
        targets: np.ndarray,
        timestamps: np.ndarray,
    ) -> None:
        """
        Parameters
        ----------
        states : np.ndarray of shape (n_samples, n_features)
        targets : np.ndarray of shape (n_samples, horizon)
        timestamps : np.ndarray of datetime64, shape (n_samples,)
        """
        assert states.shape[0] == targets.shape[0] == timestamps.shape[0]
        self.states = torch.tensor(states, dtype=torch.float32)
        self.targets = torch.tensor(targets, dtype=torch.float32)
        self.timestamps = timestamps

    def __len__(self) -> int:
        return len(self.states)

    def __getitem__(self, idx: int) -> tuple[torch.Tensor, torch.Tensor]:
        return self.states[idx], self.targets[idx]


def build_dataset(
    candles_df: pd.DataFrame,
    state_builder: Any,
    horizon: int = 10,
    min_lookback: int = 100,
    time_col: str = "timestamp",
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Build (states, targets, timestamps) arrays from candle data.

    For each bar at index i (where i >= min_lookback and i < n_bars - horizon):
      - state = state_builder.build_state(timestamp[i])
      - target = log returns [close[i+h] / close[i] for h in 1..horizon]

    Parameters
    ----------
    candles_df : pd.DataFrame
        Candle data with 'timestamp' and 'close' columns.
    state_builder : StateBuilder
        Initialized state builder with precomputed structures.
    horizon : int
        Number of future bars to predict.
    min_lookback : int
        Minimum bars of history required before we start sampling.
    time_col : str
        Name of the timestamp column.

    Returns
    -------
    states : np.ndarray of shape (n_samples, n_features)
    targets : np.ndarray of shape (n_samples, horizon)
    timestamps : np.ndarray of datetime64, shape (n_samples,)
    """
    df = candles_df.sort_values(time_col).reset_index(drop=True)
    closes = df["close"].values.astype(np.float64)
    n_bars = len(df)

    # We need min_lookback bars of history for feature building,
    # and horizon future bars for targets
    valid_start = min_lookback
    valid_end = n_bars - horizon

    if valid_start >= valid_end:
        raise ValueError(
            f"Not enough data: {n_bars} bars, need at least "
            f"{min_lookback + horizon} bars"
        )

    logger.info(
        "dataset.build started n_bars=%d valid_range=[%d, %d) horizon=%d",
        n_bars, valid_start, valid_end, horizon,
    )

    # Build states and targets for each valid position
    # Process in batches to provide progress
    states_list: list[np.ndarray] = []
    targets_list: list[np.ndarray] = []
    ts_list: list[pd.Timestamp] = []

    n_valid = valid_end - valid_start
    report_interval = max(1, n_valid // 20)  # report ~5%

    for i in range(valid_start, valid_end):
        ts = df[time_col].iloc[i]

        # Build state vector
        state = state_builder.build_state(ts)
        state_vec = np.array(
            [state[k] for k in sorted(state.keys())], dtype=np.float32
        )

        # Build target: log returns for next H bars
        current_close = closes[i]
        future_returns = np.array(
            [
                np.log(closes[i + h] / current_close)
                for h in range(1, horizon + 1)
            ],
            dtype=np.float32,
        )

        # Skip if any NaN in state or target
        if np.any(np.isnan(state_vec)) or np.any(np.isnan(future_returns)):
            continue

        states_list.append(state_vec)
        targets_list.append(future_returns)
        ts_list.append(ts)

        if (i - valid_start) % report_interval == 0:
            logger.info(
                "dataset.progress %.0f%% (%d/%d)",
                (i - valid_start) / n_valid * 100,
                i - valid_start,
                n_valid,
            )

    states = np.stack(states_list)
    targets = np.stack(targets_list)
    timestamps = np.array(ts_list)

    logger.info(
        "dataset.build complete n_samples=%d n_features=%d horizon=%d",
        len(states), states.shape[1], horizon,
    )

    return states, targets, timestamps
