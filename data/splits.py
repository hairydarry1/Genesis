"""
data/splits.py — Walk-forward time-series split generator.

Implements rolling/expanding window splits suitable for time-series data.
NO random shuffling — train/val always respect temporal order to prevent leakage.

Usage:
    from data.splits import WalkForwardSplitter

    splitter = WalkForwardSplitter(
        train_days=90,
        val_days=30,
        step_days=30,           # slide forward by this much each fold
        gap_days=0,             # gap between train and val (data leakage guard)
    )

    df = pd.read_parquet("data/ticks_R75.parquet")
    for fold, (train_idx, val_idx) in enumerate(splitter.split(df)):
        train_df = df.iloc[train_idx]
        val_df = df.iloc[val_idx]
        print(f"Fold {fold}: train={len(train_df)}, val={len(val_df)}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class SplitFold:
    """A single train/validation fold."""
    fold: int
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    val_start: pd.Timestamp
    val_end: pd.Timestamp
    train_indices: list[int] = field(default_factory=list)
    val_indices: list[int] = field(default_factory=list)

    @property
    def train_size(self) -> int:
        return len(self.train_indices)

    @property
    def val_size(self) -> int:
        return len(self.val_indices)

    def __repr__(self) -> str:
        return (
            f"Fold {self.fold}: "
            f"train [{self.train_start.date()} -> {self.train_end.date()}] ({self.train_size} pts), "
            f"val [{self.val_start.date()} -> {self.val_end.date()}] ({self.val_size} pts)"
        )


class WalkForwardSplitter:
    """
    Rolling walk-forward cross-validation splitter.

    Parameters
    ----------
    train_days : int
        Size of the training window in days.
    val_days : int
        Size of the validation window in days.
    step_days : int
        How far to slide the window forward each fold.
        If step_days < train_days, windows overlap (expanding training set).
        If step_days == train_days, windows are non-overlapping.
    gap_days : int
        Number of days to skip between train end and val start.
        Set > 0 to add a buffer that guards against look-ahead leakage.
    """

    def __init__(
        self,
        train_days: int = 90,
        val_days: int = 30,
        step_days: int = 30,
        gap_days: int = 0,
    ) -> None:
        if train_days <= 0:
            raise ValueError("train_days must be > 0")
        if val_days <= 0:
            raise ValueError("val_days must be > 0")
        if step_days <= 0:
            raise ValueError("step_days must be > 0")
        if gap_days < 0:
            raise ValueError("gap_days must be >= 0")

        self.train_days = train_days
        self.val_days = val_days
        self.step_days = step_days
        self.gap_days = gap_days

    def split(
        self,
        df: pd.DataFrame,
        time_col: str = "timestamp",
    ) -> Iterator[tuple[list[int], list[int]]]:
        """
        Generate train/validation index splits.

        Parameters
        ----------
        df : pd.DataFrame
            Must contain a timestamp column (or epoch column).
        time_col : str
            Name of the datetime column.  If "epoch", will convert from epoch seconds.

        Yields
        ------
        (train_indices, val_indices) tuples of integer positions into df.
        """
        for fold_info in self.split_info(df, time_col):
            yield fold_info.train_indices, fold_info.val_indices

    def split_info(
        self,
        df: pd.DataFrame,
        time_col: str = "timestamp",
    ) -> Iterator[SplitFold]:
        """
        Generate train/validation splits with full metadata.

        Yields SplitFold objects with indices and timestamps.
        """
        ts = self._get_timestamps(df, time_col)

        data_start = ts.min()
        data_end = ts.max()
        total_days = (data_end - data_start).days

        min_required = self.train_days + self.gap_days + self.val_days
        if total_days < min_required:
            logger.warning(
                "splits.insufficient_data",
                total_days=total_days,
                min_required=min_required,
            )
            return

        fold = 0
        offset_days = 0

        while True:
            train_start = data_start + pd.Timedelta(days=offset_days)
            train_end = train_start + pd.Timedelta(days=self.train_days)
            val_start = train_end + pd.Timedelta(days=self.gap_days)
            val_end = val_start + pd.Timedelta(days=self.val_days)

            if val_end > data_end + pd.Timedelta(hours=12):  # small tolerance
                break

            # Get indices
            train_mask = (ts >= train_start) & (ts < train_end)
            val_mask = (ts >= val_start) & (ts < val_end)

            train_idx = df.index[train_mask].tolist()
            val_idx = df.index[val_mask].tolist()

            if not train_idx or not val_idx:
                logger.warning("splits.empty_fold", fold=fold)
                offset_days += self.step_days
                continue

            info = SplitFold(
                fold=fold,
                train_start=train_start,
                train_end=train_end,
                val_start=val_start,
                val_end=val_end,
                train_indices=train_idx,
                val_indices=val_idx,
            )

            logger.debug("splits.fold", fold_info=str(info))
            yield info

            fold += 1
            offset_days += self.step_days

        logger.info("splits.complete", total_folds=fold)

    @staticmethod
    def _get_timestamps(df: pd.DataFrame, col: str) -> pd.Series:
        """Extract or build a datetime Series from the DataFrame."""
        if col in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[col]):
                return df[col]
            # Might be epoch seconds
            return pd.to_datetime(df[col], unit="s", utc=True)
        # Try to build from 'epoch' column
        if "epoch" in df.columns:
            return pd.to_datetime(df["epoch"], unit="s", utc=True)
        if "timestamp" in df.columns:
            return pd.to_datetime(df["timestamp"], utc=True)
        raise ValueError(f"Cannot find timestamp data in columns: {list(df.columns)}")

    def summary(self, df: pd.DataFrame, time_col: str = "timestamp") -> str:
        """Print a human-readable summary of all folds."""
        folds = list(self.split_info(df, time_col))
        lines = [
            f"Walk-forward splits: {len(folds)} folds",
            f"  Train window: {self.train_days} days",
            f"  Val window:   {self.val_days} days",
            f"  Step:         {self.step_days} days",
            f"  Gap:          {self.gap_days} days",
            "",
        ]
        for f in folds:
            lines.append(f"  {f}")

        text = "\n".join(lines)
        print(text)
        return text


# ---------------------------------------------------------------------------
# Convenience: split from a Parquet file
# ---------------------------------------------------------------------------

def load_and_split(
    path: str,
    train_days: int = 90,
    val_days: int = 30,
    step_days: int = 30,
    gap_days: int = 0,
    time_col: str = "timestamp",
) -> list[SplitFold]:
    """Load a Parquet file and return all split fold objects."""
    df = pd.read_parquet(path, engine="pyarrow")
    splitter = WalkForwardSplitter(
        train_days=train_days,
        val_days=val_days,
        step_days=step_days,
        gap_days=gap_days,
    )
    return list(splitter.split_info(df, time_col))


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Show walk-forward splits for a dataset")
    parser.add_argument("input", help="Input Parquet file")
    parser.add_argument("--train-days", type=int, default=90)
    parser.add_argument("--val-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--gap-days", type=int, default=0)
    parser.add_argument("--time-col", default="timestamp")
    args = parser.parse_args()

    folds = load_and_split(
        args.input,
        train_days=args.train_days,
        val_days=args.val_days,
        step_days=args.step_days,
        gap_days=args.gap_days,
        time_col=args.time_col,
    )

    print(f"Generated {len(folds)} walk-forward folds:")
    for f in folds:
        print(f"  {f}")


if __name__ == "__main__":
    main()
