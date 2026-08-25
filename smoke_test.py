"""
smoke_test.py - End-to-end pipeline smoke test with synthetic data.

Validates that all modules import, run, and produce sane outputs.
No real Deriv connection required.
"""

import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd


def generate_synthetic_ticks(n_ticks: int = 50_000, days: int = 90) -> pd.DataFrame:
    """Generate synthetic tick data that mimics a volatile index."""
    rng = np.random.default_rng(42)
    start = pd.Timestamp("2025-01-01", tz="UTC")

    # Random walk with regime-switching volatility
    base_vol = 0.0001
    vol_states = rng.choice([0.5, 1.0, 2.0, 3.0], size=n_ticks, p=[0.4, 0.3, 0.2, 0.1])
    vols = base_vol * vol_states

    # Occasional spikes (boom/crash style)
    spike_mask = rng.random(n_ticks) < 0.002
    spikes = rng.choice([-1, 1], size=n_ticks) * rng.exponential(0.01, size=n_ticks)
    spikes[~spike_mask] = 0.0

    log_returns = rng.normal(0, vols) + spikes
    prices = 1000.0 * np.exp(np.cumsum(log_returns))

    # Generate timestamps spread across `days` days
    seconds_per_day = 86400
    total_seconds = days * seconds_per_day
    # Random timestamps within the window
    random_offsets = np.sort(rng.integers(0, total_seconds, size=n_ticks))
    timestamps = [start + pd.Timedelta(seconds=int(s)) for s in random_offsets]

    return pd.DataFrame({
        "timestamp": timestamps,
        "epoch": [int(t.timestamp()) for t in timestamps],
        "price": prices,
    })


def test_resample(ticks: pd.DataFrame) -> None:
    """Test resampling at multiple frequencies."""
    from data.resample import resample_ticks, resample_all, validate_bars

    print("\n--- Resample Test ---")

    # Single frequency
    bars_1m = resample_ticks(ticks, freq="1min")
    print(f"1min bars: {len(bars_1m)} rows, columns={list(bars_1m.columns)}")
    assert len(bars_1m) > 0, "Expected non-empty 1min bars"
    assert bars_1m["open"].notna().all(), "Found NaN in open"

    # Multiple frequencies
    all_bars = resample_all(ticks, freqs=["1min", "15min", "1h"])
    for freq, bars in all_bars.items():
        diag = validate_bars(bars, freq)
        status = "OK" if not diag["has_gaps"] else f"GAPS({diag['gap_count']})"
        print(f"  {freq}: {len(bars)} bars — {status}")

    print("[OK] Resample test passed")


def test_splits(ticks: pd.DataFrame) -> None:
    """Test walk-forward splitting."""
    from data.splits import WalkForwardSplitter

    print("\n--- Split Test ---")

    splitter = WalkForwardSplitter(train_days=30, val_days=10, step_days=10)
    folds = list(splitter.split_info(ticks))

    print(f"Generated {len(folds)} folds")
    for f in folds[:3]:
        print(f"  {f}")
    if len(folds) > 3:
        print(f"  ... and {len(folds) - 3} more")

    assert len(folds) > 0, "Expected at least one fold"

    # Check no temporal leakage
    for f in folds:
        assert f.train_end <= f.val_start, f"Fold {f.fold}: train ends after val starts!"

    # Check indices are valid
    for f in folds:
        assert all(0 <= i < len(ticks) for i in f.train_indices)
        assert all(0 <= i < len(ticks) for i in f.val_indices)

    print("[OK] Split test passed")


def test_baselines() -> None:
    """Test baseline predictions."""
    from eval.baselines import (
        PersistenceBaseline,
        MeanReversionBaseline,
        RandomWalkBaseline,
        quantile_coverage,
        mean_absolute_error,
        negative_log_likelihood,
        calibration_score,
    )

    print("\n--- Baseline Test ---")

    rng = np.random.default_rng(123)
    n = 5000
    log_returns = rng.normal(0, 0.001, size=n)
    prices = 1000.0 * np.exp(np.cumsum(log_returns))

    horizon = 10
    position = len(prices) - horizon - 1
    actual_returns = np.diff(np.log(prices[position: position + horizon + 1]))

    for BaselineCls in [PersistenceBaseline, MeanReversionBaseline, RandomWalkBaseline]:
        bl = BaselineCls()
        pred = bl.predict_from_position(prices, position, horizon)

        mae = mean_absolute_error(actual_returns, pred.mean)
        nll = negative_log_likelihood(actual_returns, pred.mean, pred.variance)

        print(f"  {bl.name}: MAE={mae:.6f}, NLL={nll:.4f}, horizon={pred.horizon}")
        assert pred.horizon == horizon
        assert len(pred.mean) == horizon
        assert len(pred.variance) == horizon

    # Test calibration
    std = np.full_like(actual_returns, 0.001)
    cal = calibration_score(actual_returns, np.zeros(horizon), std)
    print(f"  Calibration gap: {cal['perfect_calibration_gap']:.4f}")

    print("[OK] Baseline test passed")


def test_parquet_io(ticks: pd.DataFrame) -> None:
    """Test Parquet save/load round-trip."""
    from data.ingest import TickIngester

    print("\n--- Parquet I/O Test ---")

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "test_ticks.parquet"
        TickIngester.save(ticks, path)
        loaded = TickIngester.load(path)

        assert len(loaded) == len(ticks), f"Row count mismatch: {len(loaded)} vs {len(ticks)}"
        assert set(ticks.columns).issubset(set(loaded.columns)), f"Missing columns: {set(ticks.columns) - set(loaded.columns)}"

    print(f"[OK] Parquet I/O test passed (saved/loaded {len(ticks)} rows)")


def main() -> None:
    print("=" * 60)
    print("SYNTHETICS BRAIN - Pipeline Smoke Test")
    print("=" * 60)

    print("\nGenerating 10k synthetic ticks across 90 days...")
    ticks = generate_synthetic_ticks(n_ticks=10_000, days=90)
    print(f"  Shape: {ticks.shape}")
    print(f"  Date range: {ticks['timestamp'].min()} -> {ticks['timestamp'].max()}")
    print(f"  Price range: {ticks['price'].min():.2f} -> {ticks['price'].max():.2f}")

    tests = [
        ("Resample", lambda: test_resample(ticks)),
        ("Splits", lambda: test_splits(ticks)),
        ("Parquet I/O", lambda: test_parquet_io(ticks)),
        ("Baselines", test_baselines),
    ]

    passed = 0
    failed = 0

    for name, fn in tests:
        try:
            fn()
            passed += 1
        except Exception as exc:
            print(f"[FAIL] {name} FAILED: {exc}")
            import traceback
            traceback.print_exc()
            failed += 1

    print("\n" + "=" * 60)
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)} tests")
    print("=" * 60)

    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
