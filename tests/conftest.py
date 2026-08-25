"""Shared pytest fixtures."""

import sys
from pathlib import Path

import numpy as np
import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def tiny_dataset(tmp_path):
    """Small deterministic dataset .npz for TradingEnv / model tests."""
    rng = np.random.default_rng(123)
    n, feat_dim, horizon = 500, 8, 10
    states = rng.normal(0, 1, (n, feat_dim)).astype(np.float32)
    targets = rng.normal(0, 0.002, (n, horizon)).astype(np.float32)
    timestamps = np.arange(np.datetime64("2026-01-01T00:00"), n, dtype="datetime64[m]")
    names = np.array([f"feat_{i}" for i in range(feat_dim)])
    path = tmp_path / "tiny.npz"
    np.savez(
        path,
        states=states,
        targets=targets,
        timestamps=timestamps,
        feature_names=names,
        feat_mean=states.mean(axis=0),
        feat_std=states.std(axis=0),
    )
    return {"path": str(path), "states": states, "targets": targets}


@pytest.fixture
def env_config():
    from env.trading_env import TradingEnvConfig

    return TradingEnvConfig(max_steps=100)


@pytest.fixture
def env(tiny_dataset, env_config):
    from env.trading_env import TradingEnv

    return TradingEnv(dataset_path=tiny_dataset["path"], config=env_config)
