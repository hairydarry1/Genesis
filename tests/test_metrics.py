"""
tests/test_metrics.py — Tests for backtest/metrics.py.

Uses synthetic equity curves with hand-computable drawdowns, trade lists with
known profit factors, and perfectly-calibrated confidence sequences.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.metrics import (
    calibration_stats,
    calmar_ratio,
    compute_metrics,
    drawdown_stats,
    drawdown_series,
    profit_factor,
    realized_correctness,
    regime_breakdown,
    sharpe_ratio,
    win_rate,
)


# ---------------------------------------------------------------------------
# Drawdown
# ---------------------------------------------------------------------------

def test_drawdown_zero_for_monotonic_growth():
    eq = np.linspace(100, 200, 50)
    assert drawdown_series(eq).max() == pytest.approx(0.0)
    stats = drawdown_stats(eq)
    assert stats["max_drawdown_pct"] == pytest.approx(0.0)
    assert stats["longest_underwater_bars"] == 0


def test_drawdown_depth_and_duration_exact():
    # Hand-computed sawtooth: peak at idx2 (110), trough at idx5 (88),
    # recovery at idx7 (110). Max DD = 20%, underwater for 5 bars.
    eq = np.array([100, 105, 110, 100, 92, 88, 95, 110, 112])
    dd = drawdown_series(eq)
    assert dd.max() == pytest.approx((110 - 88) / 110)
    stats = drawdown_stats(eq)
    assert stats["max_drawdown_pct"] == pytest.approx(20.0)
    # Underwater from bar 3 through bar 6 -> longest stretch below peak = 4
    assert stats["longest_underwater_bars"] == 4


def test_flat_curve_has_no_drawdown():
    eq = np.full(20, 100.0)
    stats = drawdown_stats(eq)
    assert stats["max_drawdown_pct"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Trade stats
# ---------------------------------------------------------------------------

def test_win_rate_and_profit_factor():
    trades = [10.0, -4.0, 6.0, -1.0, 5.0]
    assert win_rate(trades) == pytest.approx(3 / 5)
    pf = profit_factor(trades)
    assert pf == pytest.approx(21.0 / 5.0)


def test_profit_factor_edge_cases():
    assert profit_factor([5.0, 3.0]) == float("inf")     # no losses
    assert profit_factor([-5.0, -1.0]) == 0.0            # no gains
    assert profit_factor([]) is None
    assert win_rate([]) is None


def test_sharpe_zero_variance_is_none():
    assert sharpe_ratio(np.ones(10)) is None
    assert sharpe_ratio(np.array([0.01, -0.01, 0.02])) is not None


def test_calmar_positive_when_dd_exists():
    eq = np.array([100, 120, 96, 108, 130])   # max DD 20%
    c = calmar_ratio(eq, periods_per_year=252)
    assert c is not None and c > 0
    assert calmar_ratio(np.linspace(1, 2, 10)) is None  # no drawdown


# ---------------------------------------------------------------------------
# Regimes
# ---------------------------------------------------------------------------

def test_regime_masks_partition_all_bars():
    rng = np.random.default_rng(42)
    r = rng.normal(0, 0.001, 5000)
    masks = {
        "calm": np.abs(r) < np.std(r) * 0.5,
        "choppy": (np.abs(r) >= np.std(r) * 0.5) & (np.abs(r) < np.std(r) * 1.5),
        "spike": np.abs(r) >= np.std(r) * 1.5,
    }
    total = sum(int(m.sum()) for m in masks.values())
    assert total == len(r)
    assert not any((masks[a] & masks[b]).any() for a in masks for b in masks if a != b)


def test_regime_breakdown_reports_separately():
    # Construct equity where 'spike' bars carry all the gains
    # Need >= 5 spike bars for regime_breakdown's n<5 filter
    returns = np.concatenate([
        np.zeros(50),                       # calm-ish flat
        np.array([0.05, -0.04, 0.06, 0.08, -0.03, 0.07]),  # spikes (6 bars)
    ])
    eq = 100 * np.cumprod(1 + returns)
    breakdown = regime_breakdown(eq)
    assert set(breakdown) <= {"calm", "choppy", "spike"}
    assert "spike" in breakdown
    assert breakdown["spike"]["n_bars"] > 0


# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def test_calibration_perfect_confidence():
    confs = np.array([0.9, 0.9, 0.9, 0.9])
    corrects = np.array([1, 1, 1, 0])
    stats = calibration_stats(confs, corrects)
    assert stats["overall_accuracy"] == pytest.approx(0.75)
    assert stats["overall_mean_confidence"] == pytest.approx(0.9)
    assert stats["mean_gap"] == pytest.approx(0.15)
    assert len(stats["bins"]) >= 1


def test_calibration_bins_are_ordered():
    rng = np.random.default_rng(7)
    confs = rng.uniform(0, 1, 1000)
    corrects = rng.random(1000) < confs      # honest forecaster
    stats = calibration_stats(confs, corrects, n_bins=5)
    ranges = [b["range"] for b in stats["bins"]]
    assert ranges == sorted(ranges)
    # An honest forecaster's per-bin gap should be small
    for b in stats["bins"]:
        assert abs(b["gap"]) < 0.2


def test_realized_correctness_from_decisions():
    decisions = [
        {"executed_action": 1, "confidence": 0.8, "equity_before": 100, "equity_after": 101},
        {"executed_action": 1, "confidence": 0.7, "equity_before": 101, "equity_after": 99},
        {"executed_action": 0, "confidence": 0.3, "equity_before": 99, "equity_after": 99},
        {"executed_action": 2, "confidence": 0.6, "equity_before": 99, "equity_after": 98},  # short that lost
    ]
    confs, corr = realized_correctness(decisions)
    assert corr[0] == True and corr[1] == False
    assert np.isnan(corr[2])                      # flat excluded
    assert corr[3] == False                       # short losing = incorrect


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

def test_compute_metrics_aggregates_everything():
    eq = np.array([100, 105, 110, 100, 92, 88, 95, 110, 112], dtype=float)
    decisions = [
        {"executed_action": 1, "confidence": 0.8, "equity_before": 100, "equity_after": 110},
        {"executed_action": 0, "confidence": 0.2, "equity_before": 110, "equity_after": 110},
    ]
    m = compute_metrics(eq, trade_returns=[2.0, -1.0, 1.5], decisions=decisions)
    assert m["max_drawdown_pct"] == pytest.approx(20.0)
    assert m["win_rate"] == pytest.approx(2 / 3)
    assert m["profit_factor"] == pytest.approx(3.5)
    assert m["calibration"]["n_decisions"] == 1  # flat decisions excluded from calibration
    assert "regime_breakdown" in m
