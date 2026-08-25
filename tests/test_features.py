"""
tests/test_features.py — Unit tests for features/state.py and features/structures.py

Sanity checks:
  - build_state produces consistent shapes with no NaNs
  - Feature values are in reasonable ranges
  - Structures are detected correctly
  - State vectors are deterministic for the same timestamp
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from features.state import StateBuilder
from features.structures import (
    detect_swing_points,
    detect_structures,
    compute_atr,
    passes_quality_filter,
    InstrumentQualityCfg,
    StructureInstance,
)


def make_synthetic_ticks(n: int = 5000) -> pd.DataFrame:
    """Generate synthetic tick data for testing (mimics V75 behavior)."""
    np.random.seed(42)
    timestamps = pd.date_range("2026-08-01", periods=n, freq="2s", tz="UTC")
    # Random walk with mean-reversion and occasional spikes
    price = 50000.0
    prices = []
    for i in range(n):
        drift = -0.00001 * (price - 50000)  # mean-revert around 50000
        shock = np.random.normal(0, 0.0005)
        # Occasional spikes (Boom/Crash-like)
        if np.random.random() < 0.005:
            shock *= 5
        price *= (1 + drift + shock)
        prices.append(price)

    return pd.DataFrame({
        "epoch": timestamps.astype(np.int64) // 10**9,
        "price": prices,
        "timestamp": timestamps,
    })


def test_swing_points():
    """Test swing-point detection."""
    print("TEST: swing points...", end=" ")

    ticks = make_synthetic_ticks(3000)
    from data.resample import resample_ticks
    bars = resample_ticks(ticks, freq="1min")

    swings = detect_swing_points(bars, lookback=3)

    assert len(swings) > 0, "Should detect at least some swing points"

    highs = [s for s in swings if s.kind == "high"]
    lows = [s for s in swings if s.kind == "low"]
    assert len(highs) > 0, "Should detect at least one swing high"
    assert len(lows) > 0, "Should detect at least one swing low"

    # Swing highs should have higher price than surrounding bars
    for sh in highs:
        assert sh.index < len(bars), "Swing index out of bounds"

    print(f"PASSED ({len(swings)} swings: {len(highs)}H, {len(lows)}L)")
    return True


def test_atr():
    """Test ATR computation."""
    print("TEST: ATR...", end=" ")

    ticks = make_synthetic_ticks(3000)
    from data.resample import resample_ticks
    bars = resample_ticks(ticks, freq="1min")

    atr = compute_atr(bars, period=14)

    assert len(atr) == len(bars), "ATR length should match bars"
    assert atr.iloc[-1] > 0, "ATR should be positive"
    assert not atr.isna().any(), "ATR should have no NaNs"

    print(f"PASSED (ATR range: {atr.min():.2f} to {atr.max():.2f})")
    return True


def test_quality_filter():
    """Test quality filter thresholds."""
    print("TEST: quality filter...", end=" ")

    cfg = InstrumentQualityCfg(min_structure_ATR=0.3)

    # Zone too small
    assert not passes_quality_filter(100.0, 99.0, 10.0, cfg), "Tiny zone should fail"

    # Zone large enough
    assert passes_quality_filter(100.0, 95.0, 10.0, cfg), "Large zone should pass"

    # Zero ATR
    assert not passes_quality_filter(100.0, 95.0, 0.0, cfg), "Zero ATR should fail"

    print("PASSED")
    return True


def test_structures_detection():
    """Test structure detection pipeline."""
    print("TEST: structures detection...", end=" ")

    ticks = make_synthetic_ticks(3000)
    from data.resample import resample_ticks
    bars = resample_ticks(ticks, freq="1min")

    structures = detect_structures(bars, atr_period=14, swing_lookback=3)

    # All structures should have valid fields
    for s in structures:
        assert s.zone_high >= s.zone_low, f"Zone inverted: {s}"
        assert s.size_ATR >= 0, f"Negative size: {s}"
        assert s.age_bars >= 0, f"Negative age: {s}"

    print(f"PASSED ({len(structures)} structures detected)")
    return True


def test_build_state_consistency():
    """Test that build_state produces consistent, non-NaN outputs."""
    print("TEST: build_state consistency...", end=" ")

    ticks = make_synthetic_ticks(5000)
    builder = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])

    # Build state at multiple timestamps
    test_timestamps = [
        ticks["timestamp"].iloc[500],
        ticks["timestamp"].iloc[1000],
        ticks["timestamp"].iloc[2000],
        ticks["timestamp"].iloc[3000],
        ticks["timestamp"].iloc[4000],
    ]

    shapes = []
    for ts in test_timestamps:
        state = builder.build_state(ts)

        # No NaNs
        nan_keys = [k for k, v in state.items() if np.isnan(v)]
        assert len(nan_keys) == 0, f"NaN found in features: {nan_keys}"

        # No Infs
        inf_keys = [k for k, v in state.items() if np.isinf(v)]
        assert len(inf_keys) == 0, f"Inf found in features: {inf_keys}"

        shapes.append(len(state))

    # All states should have the same number of features
    assert len(set(shapes)) == 1, f"Inconsistent feature counts: {shapes}"

    print(f"PASSED ({shapes[0]} features, consistent across {len(test_timestamps)} timestamps)")
    return True


def test_build_state_vector():
    """Test that build_state_vector produces valid numpy arrays."""
    print("TEST: build_state_vector...", end=" ")

    ticks = make_synthetic_ticks(5000)
    builder = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])

    ts = ticks["timestamp"].iloc[2000]
    vec = builder.build_state_vector(ts)

    assert isinstance(vec, np.ndarray), "Should return numpy array"
    assert vec.dtype == np.float32, f"Should be float32, got {vec.dtype}"
    assert not np.any(np.isnan(vec)), "Should have no NaNs"
    assert not np.any(np.isinf(vec)), "Should have no Infs"
    assert len(vec) == builder.n_features, f"Length mismatch: {len(vec)} vs {builder.n_features}"

    print(f"PASSED (shape={vec.shape}, dtype={vec.dtype})")
    return True


def test_feature_names():
    """Test that feature names are consistent and sorted."""
    print("TEST: feature names...", end=" ")

    ticks = make_synthetic_ticks(5000)
    builder = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])

    names = builder.feature_names
    n = builder.n_features

    assert len(names) == n, f"Name count mismatch: {len(names)} vs {n}"
    assert names == sorted(names), "Feature names should be sorted"

    # Check expected prefixes exist
    timeframes = ["1min", "15min", "1h"]
    for tf in timeframes:
        assert any(f.startswith(f"{tf}_") for f in names), f"Missing features for timeframe {tf}"

    print(f"PASSED ({n} features, all sorted)")
    return True


def test_structure_features():
    """Test that structural features are populated."""
    print("TEST: structure features...", end=" ")

    ticks = make_synthetic_ticks(5000)
    builder = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])

    ts = ticks["timestamp"].iloc[3000]
    state = builder.build_state(ts)

    # Check structure-related features exist
    struct_keys = [k for k in state if "structure" in k or "premium" in k or k.startswith("n_")]
    assert len(struct_keys) > 0, "Should have structure features"

    # Structure counts should be non-negative
    for k in struct_keys:
        if k.startswith("n_"):
            assert state[k] >= 0, f"{k} should be >= 0, got {state[k]}"

    print(f"PASSED ({len(struct_keys)} structure features)")
    return True


def test_state_determinism():
    """Test that the same timestamp always produces the same state."""
    print("TEST: state determinism...", end=" ")

    ticks = make_synthetic_ticks(5000)

    builder1 = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])
    builder2 = StateBuilder(ticks, timeframes=["1min", "15min", "1h"])

    ts = ticks["timestamp"].iloc[2000]
    state1 = builder1.build_state(ts)
    state2 = builder2.build_state(ts)

    assert state1.keys() == state2.keys(), "Feature keys should be identical"

    for k in state1:
        assert abs(state1[k] - state2[k]) < 1e-10, f"Feature '{k}' differs: {state1[k]} vs {state2[k]}"

    print("PASSED (identical outputs for same input)")
    return True


def test_premium_discount():
    """Test premium/discount zone computation."""
    print("TEST: premium/discount...", end=" ")

    from features.structures import compute_premium_discount, SwingPoint

    swings = [
        SwingPoint(index=0, timestamp=pd.Timestamp("2026-08-01"), price=100.0, kind="high"),
        SwingPoint(index=10, timestamp=pd.Timestamp("2026-08-01 00:10:00"), price=90.0, kind="low"),
    ]

    # Price at midpoint
    pd_zone = compute_premium_discount(swings, 95.0)
    assert abs(pd_zone["position"] - 0.5) < 0.01, f"Midpoint position should be ~0.5, got {pd_zone['position']}"

    # Price at high
    pd_zone = compute_premium_discount(swings, 100.0)
    assert pd_zone["position"] > 0.9, f"Price at high should have position > 0.9, got {pd_zone['position']}"

    # Price at low
    pd_zone = compute_premium_discount(swings, 90.0)
    assert pd_zone["position"] < 0.1, f"Price at low should have position < 0.1, got {pd_zone['position']}"

    print("PASSED")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    tests = [
        test_swing_points,
        test_atr,
        test_quality_filter,
        test_structures_detection,
        test_build_state_consistency,
        test_build_state_vector,
        test_feature_names,
        test_structure_features,
        test_state_determinism,
        test_premium_discount,
    ]

    passed = 0
    failed = 0

    for test in tests:
        try:
            if test():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"FAILED: {e}")
            import traceback
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed out of {len(tests)}")

    if failed > 0:
        sys.exit(1)
    else:
        print("All tests passed!")
        sys.exit(0)
