"""
eval/baselines.py — Naive statistical baselines for world model validation.

These are the bar the world model MUST clear.  If it can't beat these
out-of-sample, stop and rework features/architecture before continuing.

Baselines:
  1. Persistence:      "next price = current price"
  2. Mean Reversion:   "next price reverts toward recent rolling mean"
  3. Random Walk:      "next price = current price + noise from recent realized vol"

Each baseline predicts a distribution (mean + variance) over the next N ticks,
not just a point estimate.  This lets us compare calibration, not just accuracy.

Usage:
    from eval.baselines import PersistenceBaseline, MeanReversionBaseline, RandomWalkBaseline

    preds = PersistenceBaseline().predict(df, horizon=10)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Prediction result container
# ---------------------------------------------------------------------------

@dataclass
class BaselinePrediction:
    """Distributional prediction from a baseline."""
    mean: np.ndarray       # Predicted mean return for each step in horizon
    variance: np.ndarray   # Predicted variance for each step in horizon
    name: str = ""

    @property
    def std(self) -> np.ndarray:
        return np.sqrt(self.variance)

    @property
    def horizon(self) -> int:
        return len(self.mean)


# ---------------------------------------------------------------------------
# Abstract baseline
# ---------------------------------------------------------------------------

class Baseline(ABC):
    """Abstract base for all baselines."""

    name: str = "baseline"

    @abstractmethod
    def fit(self, prices: np.ndarray) -> None:
        """Fit baseline parameters on historical prices (call once per fold)."""
        ...

    @abstractmethod
    def predict(self, prices: np.ndarray, horizon: int) -> BaselinePrediction:
        """Predict distribution over next `horizon` price movements."""
        ...

    def predict_from_position(
        self,
        prices: np.ndarray,
        position: int,
        horizon: int,
    ) -> BaselinePrediction:
        """
        Predict from a specific position in the price array.

        Used during walk-forward evaluation: position is the index of the
        "current" price, and we predict horizon steps ahead.
        """
        # Fit on all data up to position (expanding window)
        self.fit(prices[: position + 1])
        return self.predict(prices, horizon)


# ---------------------------------------------------------------------------
# Baseline 1: Persistence (random walk, no drift)
# ---------------------------------------------------------------------------

class PersistenceBaseline(Baseline):
    """
    "Next price = current price."

    Mean return = 0 at all horizons.
    Variance = 0 (point prediction — no uncertainty estimate).
    This is the simplest possible baseline.
    """

    name = "persistence"

    def fit(self, prices: np.ndarray) -> None:
        pass  # Nothing to fit

    def predict(self, prices: np.ndarray, horizon: int) -> BaselinePrediction:
        # Predicted return = 0 (price stays the same)
        return BaselinePrediction(
            mean=np.zeros(horizon),
            variance=np.zeros(horizon),
            name=self.name,
        )


# ---------------------------------------------------------------------------
# Baseline 2: Mean Reversion
# ---------------------------------------------------------------------------

class MeanReversionBaseline(Baseline):
    """
    "Next price reverts toward recent rolling mean."

    Uses an Ornstein-Uhlenbeck-style mean reversion model:
        dr = theta * (mu - r) * dt + sigma * dW

    where:
        mu = rolling mean return
        theta = mean reversion speed
        sigma = rolling volatility
    """

    name = "mean_reversion"

    def __init__(self, lookback: int = 100, reversion_speed: float = 0.1) -> None:
        self.lookback = lookback
        self.reversion_speed = reversion_speed
        self._mu: float = 0.0
        self._sigma: float = 0.0

    def fit(self, prices: np.ndarray) -> None:
        if len(prices) < 3:
            self._mu = 0.0
            self._sigma = 0.001
            return

        # Compute log returns
        log_returns = np.diff(np.log(np.maximum(prices, 1e-10)))

        # Use recent lookback window
        recent = log_returns[-self.lookback:]
        self._mu = float(np.mean(recent))
        self._sigma = float(np.std(recent)) if len(recent) > 1 else 0.001

    def predict(self, prices: np.ndarray, horizon: int) -> BaselinePrediction:
        theta = self.reversion_speed
        mu = self._mu
        sigma = self._sigma

        # Current price level (as log return from last known)
        # Mean reversion: E[r_t] = (1 - theta)^t * r_0 + (1 - (1-theta)^t) * mu
        # For persistence baseline, r_0 = 0 (last return was ~mu)
        # Simplified: mean reverts to mu

        steps = np.arange(1, horizon + 1)
        decay = (1 - theta) ** steps

        # Mean: decays toward mu
        mean = (1 - decay) * mu

        # Variance: grows but bounded by sigma^2 / (2*theta) for OU process
        # Var[r_t] = sigma^2 / (2*theta) * (1 - (1-2*theta)^t)  for small theta
        # Approximation: variance grows as min(sigma^2 * t, sigma^2 / (2*theta))
        var_cumulative = sigma**2 * (1 - decay**2) / (2 * theta) if theta > 0 else sigma**2 * steps

        return BaselinePrediction(
            mean=mean,
            variance=var_cumulative,
            name=self.name,
        )


# ---------------------------------------------------------------------------
# Baseline 3: Random Walk with Historical Volatility
# ---------------------------------------------------------------------------

class RandomWalkBaseline(Baseline):
    """
    "Next price = current price + noise from recent realized volatility."

    Like persistence but with calibrated uncertainty:
    mean = 0, variance = sigma^2 * t (variance grows linearly with horizon).

    This is the "efficient market" null hypothesis.
    """

    name = "random_walk"

    def __init__(self, lookback: int = 100) -> None:
        self.lookback = lookback
        self._sigma: float = 0.001

    def fit(self, prices: np.ndarray) -> None:
        if len(prices) < 3:
            self._sigma = 0.001
            return

        log_returns = np.diff(np.log(np.maximum(prices, 1e-10)))
        recent = log_returns[-self.lookback:]
        self._sigma = float(np.std(recent)) if len(recent) > 1 else 0.001

    def predict(self, prices: np.ndarray, horizon: int) -> BaselinePrediction:
        steps = np.arange(1, horizon + 1)
        # Variance scales linearly with horizon (random walk)
        variance = self._sigma**2 * steps
        mean = np.zeros(horizon)

        return BaselinePrediction(
            mean=mean,
            variance=variance,
            name=self.name,
        )


# ---------------------------------------------------------------------------
# Baseline registry
# ---------------------------------------------------------------------------

ALL_BASELINES: dict[str, type[Baseline]] = {
    "persistence": PersistenceBaseline,
    "mean_reversion": MeanReversionBaseline,
    "random_walk": RandomWalkBaseline,
}


def get_baseline(name: str, **kwargs: Any) -> Baseline:
    """Get a baseline by name."""
    cls = ALL_BASELINES.get(name)
    if cls is None:
        raise ValueError(f"Unknown baseline: {name}. Available: {list(ALL_BASELINES.keys())}")
    return cls(**kwargs)


# ---------------------------------------------------------------------------
# Evaluation metrics (used by eval/validate.py)
# ---------------------------------------------------------------------------

def quantile_coverage(
    actual: np.ndarray,
    predicted_mean: np.ndarray,
    predicted_std: np.ndarray,
    quantiles: list[float] | None = None,
) -> dict[str, float]:
    """
    Check if predicted confidence intervals have the right coverage.

    For each quantile q, check what fraction of actuals fall within the
    predicted q-quantile interval.
    """
    if quantiles is None:
        quantiles = [0.10, 0.25, 0.50, 0.75, 0.90]

    from scipy import stats  # type: ignore

    results = {}
    for q in quantiles:
        # z-score for this quantile
        z = stats.norm.ppf(q)
        lower = predicted_mean - z * predicted_std
        upper = predicted_mean + z * predicted_std
        coverage = float(np.mean((actual >= lower) & (actual <= upper)))
        results[f"coverage_{q:.0%}"] = coverage

    return results


def mean_absolute_error(actual: np.ndarray, predicted_mean: np.ndarray) -> float:
    """Mean absolute error of point predictions."""
    return float(np.mean(np.abs(actual - predicted_mean)))


def negative_log_likelihood(
    actual: np.ndarray,
    predicted_mean: np.ndarray,
    predicted_var: np.ndarray,
) -> float:
    """
    Average negative log-likelihood under a Gaussian model.

    Lower is better.  This measures how well the predicted distribution
    explains the actual outcomes.
    """
    var = np.maximum(predicted_var, 1e-12)  # avoid log(0)
    nll = 0.5 * (np.log(2 * np.pi * var) + (actual - predicted_mean) ** 2 / var)
    return float(np.mean(nll))


def calibration_score(
    actual: np.ndarray,
    predicted_mean: np.ndarray,
    predicted_std: np.ndarray,
    n_bins: int = 10,
) -> dict[str, Any]:
    """
    Compute calibration: for each predicted confidence level, what's the actual coverage?

    Returns bins of predicted probability vs actual frequency.
    """
    from scipy import stats  # type: ignore

    # Compute standardized residuals
    std_resid = (actual - predicted_mean) / np.maximum(predicted_std, 1e-12)

    # Check coverage at different levels
    levels = np.linspace(0.05, 0.95, n_bins)
    predicted_coverage = []
    actual_coverage = []

    for level in levels:
        z = stats.norm.ppf(level)
        predicted_coverage.append(level)
        actual_coverage.append(float(np.mean(np.abs(std_resid) <= z)))

    return {
        "predicted_coverage": predicted_coverage,
        "actual_coverage": actual_coverage,
        "perfect_calibration_gap": float(np.mean(np.abs(
            np.array(predicted_coverage) - np.array(actual_coverage)
        ))),
    }
