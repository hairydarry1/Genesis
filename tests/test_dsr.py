"""Tests for eval/dsr.py — Deflated Sharpe Ratio math."""

import math

import numpy as np
import pytest
from scipy import stats

from eval.dsr import deflated_sharpe, expected_max_sharpe, sharpe_ratio


class TestSharpeRatio:
    def test_basic(self):
        rng = np.random.default_rng(0)
        r = rng.normal(0.001, 0.01, 1000)
        sr = sharpe_ratio(r)
        assert 0 < sr < 1

    def test_zero_std_guard(self):
        assert sharpe_ratio(np.full(10, 0.5)) == 0.0


class TestExpectedMaxSharpe:
    def test_increases_with_trials(self):
        vals = [expected_max_sharpe(n, 0.1) for n in (2, 10, 100, 1000)]
        assert vals == sorted(vals)

    def test_positive_for_multiple_trials(self):
        assert expected_max_sharpe(16, 0.2) > 0

    def test_degenerate(self):
        assert expected_max_sharpe(1, 0.1) == 0.0
        assert expected_max_sharpe(10, 0.0) == 0.0


class TestDeflatedSharpe:
    def _iid_returns(self, seed=0, mu=0.001, sigma=0.01, T=2000):
        return np.random.default_rng(seed).normal(mu, sigma, T)

    def test_perfect_skill_gives_high_dsr(self):
        """Large true edge with few trials -> DSR ~ 1."""
        r = self._iid_returns(mu=0.01)  # SR = 1.0, huge
        out = deflated_sharpe(r, n_trials=2)
        assert out["dsr"] > 0.99
        assert out["significant"]

    def test_no_edge_many_trials_gives_low_dsr(self):
        """Zero-mean noise with many trials -> DSR well below 0.95."""
        r = self._iid_returns(mu=0.0, seed=3)
        out = deflated_sharpe(r, n_trials=100)
        assert out["dsr"] < 0.6
        assert not out["significant"]

    def test_dsr_decreases_with_more_trials(self):
        r = self._iid_returns(seed=7)
        dsrs = [
            deflated_sharpe(r, n_trials=n)["dsr"] for n in (2, 8, 32, 128)
        ]
        assert dsrs == sorted(dsrs, reverse=True)

    def test_formula_matches_manual_computation(self):
        r = self._iid_returns(seed=42)
        n_trials = 16
        out = deflated_sharpe(r, n_trials=n_trials)

        sr = np.mean(r) / np.std(r, ddof=1)
        skew = stats.skew(r)
        kurt = stats.kurtosis(r, fisher=False)
        g = 0.5772156649015329
        z1 = stats.norm.ppf(1 - 1 / n_trials)
        z2 = stats.norm.ppf(1 - 1 / (n_trials * math.e))
        sr_var = (1 / (len(r) - 1)) * (1 - skew * sr + (kurt - 1) / 4 * sr ** 2)
        sr0 = math.sqrt(sr_var) * ((1 - g) * z1 + g * z2)
        denom = math.sqrt(max(1 - skew * sr + (kurt - 1) / 4 * sr ** 2, 1e-12))
        expected = stats.norm.cdf((sr - sr0) * math.sqrt(len(r) - 1) / denom)

        assert np.isclose(out["dsr"], expected, atol=1e-9)

    def test_trial_sharpes_used_for_variance(self):
        r = self._iid_returns(seed=1)
        trial_srs = [sharpe_ratio(np.random.default_rng(s).normal(0, 0.01, 2000)) for s in range(15)]
        out = deflated_sharpe(r, n_trials=16, trial_sharpes=trial_srs)
        # With real cross-trial variance the reported std should differ from
        # the theoretical single-series fallback.
        fallback = deflated_sharpe(r, n_trials=16)
        assert abs(out["sr_std_across_trials"] - fallback["sr_std_across_trials"]) > 1e-9

    def test_requires_min_samples(self):
        with pytest.raises(ValueError):
            deflated_sharpe(np.array([0.1, 0.2]), n_trials=5)
