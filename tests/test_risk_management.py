"""
tests/test_risk_management.py — Tests for model/risk_management.py.

Covers:
  - Position size scales with confidence, drawdown, and volatility.
  - Drawdown stop zeroes the position.
  - Portfolio limits count only OTHER indices' exposure (target-replacement
    semantics) and respect the single-index cap.
  - should_trade gates on expected return / drawdown / confidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from model.risk_management import RiskConfig, RiskManager


def test_size_zeroes_at_drawdown_stop():
    mgr = RiskManager()
    assert mgr.compute_position_size(
        confidence=1.5, current_drawdown=0.15, volatility=0.001
    ) == 0.0


def test_size_decreases_with_drawdown():
    mgr = RiskManager()
    sizes = [
        mgr.compute_position_size(1.0, dd, 0.001)
        for dd in (0.0, 0.06, 0.12)
    ]
    assert sizes[0] > sizes[1] > sizes[2] >= 0.0


def test_confidence_increases_size():
    mgr = RiskManager()
    low = mgr.compute_position_size(0.2, 0.0, 0.001)
    high = mgr.compute_position_size(2.0, 0.0, 0.001)
    assert high > low > 0.0


def test_volatility_scaling_reduces_size_when_volatile():
    mgr = RiskManager()
    calm = mgr.compute_position_size(1.0, 0.0, volatility=0.0005)
    wild = mgr.compute_position_size(1.0, 0.0, volatility=0.004)
    assert calm > wild


def test_portfolio_limits_use_replacement_semantics():
    cfg = RiskConfig(max_portfolio_exposure=3.0, max_single_index_exposure=1.5)
    mgr = RiskManager(cfg)

    # Index already at full single-index exposure proposing the same again:
    # replacement must be allowed in full (old bug clamped it to half).
    mgr.update_state("r75", position=1.5, equity=10_000, peak_equity=10_000)
    size = mgr.compute_position_size(1.0, 0.0, 0.001, index_type="r75")

    # Same as a manager with NO existing state — own position doesn't block it
    fresh = RiskManager(cfg)
    assert size == pytest.approx(fresh.compute_position_size(1.0, 0.0, 0.001, index_type="r75"))
    assert size > 0

    # But total portfolio cap counts other indices' exposure
    mgr.update_state("vol10", position=1.5, equity=10_000, peak_equity=10_000)
    mgr.update_state("boom_500", position=1.5, equity=10_000, peak_equity=10_000)
    # others_total = 3.0 >= max_portfolio_exposure -> no room left
    size = mgr.compute_position_size(1.0, 0.0, 0.001, index_type="crash_500")
    assert size == 0.0


def test_should_trade_gates():
    mgr = RiskManager()
    assert mgr.should_trade(expected_return=0.01, confidence=0.8,
                            current_drawdown=0.01, volatility=0.001)
    # Below min expected profit
    assert not mgr.should_trade(expected_return=0.0001, confidence=0.8,
                                current_drawdown=0.01, volatility=0.001)
    # Drawdown at stop level
    assert not mgr.should_trade(expected_return=0.01, confidence=0.8,
                                current_drawdown=0.15, volatility=0.001)
    # Confidence too low
    assert not mgr.should_trade(expected_return=0.01, confidence=0.05,
                                current_drawdown=0.01, volatility=0.001)
