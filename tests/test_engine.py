"""
tests/test_engine.py — Tests for backtest/engine.py.

Covers:
  - ConfidenceGate routing boundaries and threshold validation.
  - A full engine run on a synthetic dataset (untrained policy, no WM
    checkpoints): produces a well-formed decision log, sane metrics,
    valid gate routes, and consistent equity accounting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backtest.engine import BacktestConfig, BacktestEngine, ConfidenceGate
from backtest.governor_sim import GovernorConfig


# ---------------------------------------------------------------------------
# ConfidenceGate unit tests
# ---------------------------------------------------------------------------

def test_gate_routing_boundaries():
    gate = ConfidenceGate(auto_threshold=0.6, advisory_threshold=0.4)
    assert gate.route(0.6) == "auto"        # exactly at auto threshold
    assert gate.route(0.9) == "auto"
    assert gate.route(0.59) == "advisory"
    assert gate.route(0.4) == "advisory"    # exactly at advisory threshold
    assert gate.route(0.39) == "discard"
    assert gate.route(0.0) == "discard"


def test_gate_invalid_thresholds_rejected():
    with pytest.raises(ValueError):
        ConfidenceGate(auto_threshold=0.3, advisory_threshold=0.5)  # adv > auto
    with pytest.raises(ValueError):
        ConfidenceGate(auto_threshold=0.0, advisory_threshold=0.0)
    with pytest.raises(ValueError):
        ConfidenceGate(auto_threshold=1.2, advisory_threshold=0.5)


# ---------------------------------------------------------------------------
# Full engine run (untrained policy, no checkpoints — pipeline mechanics only)
# ---------------------------------------------------------------------------

@pytest.fixture
def tiny_named_dataset(tmp_path):
    """Dataset npz whose feature names match TINY_FEATURES so the engine's
    tiny_indices mapping resolves (unlike conftest's feat_i fixture)."""
    from model.train_tiny import TINY_FEATURES

    rng = np.random.default_rng(7)
    n = 300
    feat_dim = len(TINY_FEATURES)
    states = rng.normal(0, 1, (n, feat_dim)).astype(np.float32)
    # Slight momentum in targets so trades aren't pure noise
    targets = (0.0005 * states[:, 0:1] + rng.normal(0, 0.002, (n, 10))).astype(np.float32)
    timestamps = np.arange(
        np.datetime64("2026-01-01T00:00"), n, dtype="datetime64[m]"
    )
    path = tmp_path / "tiny_named.npz"
    np.savez(
        path,
        states=states,
        targets=targets,
        timestamps=timestamps,
        feature_names=np.array(TINY_FEATURES),
        feat_mean=states.mean(axis=0),
        feat_std=states.std(axis=0),
    )
    return {"path": str(path), "n": n}


def _run_engine(dataset_path: str) -> dict:
    engine = BacktestEngine(
        dataset_path=dataset_path,
        world_model_path=None,
        policy_path=None,
        config=BacktestConfig(governor=GovernorConfig(max_drawdown_pct=1.0)),
        device_str="cpu",
    )
    return engine.run(verbose=False)


def test_engine_full_run_produces_valid_log(tiny_named_dataset):
    result = _run_engine(tiny_named_dataset["path"])

    m = result["metrics"]
    assert m["n_decisions"] == tiny_named_dataset["n"]
    assert len(result["decisions"]) == m["n_decisions"]

    # Every decision record is well-formed
    for d in result["decisions"]:
        assert d["gate_route"] in ("auto", "advisory", "discard")
        assert d["raw_action"] in (0, 1, 2)
        assert d["executed_action"] in (0, 1, 2)
        # Advisory/discard routes must NOT execute the raw action
        if d["gate_route"] != "auto":
            assert d["executed_action"] == 0
        assert 0.0 <= d["confidence"] <= 1.0
        assert isinstance(d["governor_veto"], bool)

    # Routes histogram matches decision records
    route_counts: dict[str, int] = {}
    for d in result["decisions"]:
        route_counts[d["gate_route"]] = route_counts.get(d["gate_route"], 0) + 1
    assert m["routes"] == route_counts

    # Equity accounting: last equity_after equals reported final equity
    assert result["metrics"]["final_equity"] == pytest.approx(
        result["decisions"][-1]["equity_after"], rel=1e-6
    )


def test_engine_metrics_sane(tiny_named_dataset):
    result = _run_engine(tiny_named_dataset["path"])
    m = result["metrics"]

    eq0 = result["decisions"][0]["equity_before"]
    assert m["total_return_pct"] == pytest.approx(
        (m["final_equity"] / eq0 - 1) * 100, rel=1e-6
    )
    assert m["max_drawdown_pct"] >= 0.0
    assert m["trades_closed"] <= m["n_decisions"]
    if m["trades_closed"]:
        assert 0.0 <= m["win_rate"] <= 1.0
    assert set(m["actions_executed"]) <= {"0", "1", "2"}
