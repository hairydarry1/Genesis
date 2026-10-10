"""
tests/test_lookahead_audit.py — Tests for backtest/lookahead_audit.py.

Covers:
  - A causal (leak-free) feature pipeline passes the audit cleanly.
  - A deliberately leaking feature is DETECTED with the offending name.
  - compare_states tolerance semantics.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.lookahead_audit import audit_candles, compare_states
from features.state import StateBuilder


def make_synthetic_candles(n: int = 900, seed: int = 42) -> pd.DataFrame:
    """Synthetic 1-min OHLCV candles (random walk with mean reversion)."""
    rng = np.random.default_rng(seed)
    timestamps = pd.date_range("2026-06-01", periods=n, freq="1min")
    price = 50000.0
    rows = []
    for i in range(n):
        drift = -0.00001 * (price - 50000)
        shock = rng.normal(0, 0.0004)
        if rng.random() < 0.005:
            shock *= 5  # occasional spike
        o = price
        c = price * (1 + drift + shock)
        h = max(o, c) * (1 + abs(rng.normal(0, 0.0002)))
        lo = min(o, c) * (1 - abs(rng.normal(0, 0.0002)))
        rows.append({
            "timestamp": timestamps[i],
            "open": o, "high": h, "low": lo, "close": c,
            "volume": float(rng.integers(10, 100)),
        })
        price = c
    return pd.DataFrame(rows)


class TestCompareStates:
    def test_identical_states_pass(self):
        d = {"a": 1.0, "b": -0.5}
        assert compare_states(d, dict(d), tolerance=1e-9) == []

    def test_value_mismatch_flagged(self):
        full = {"a": 1.0}
        trunc = {"a": 1.1}
        m = compare_states(full, trunc, tolerance=1e-9)
        assert len(m) == 1 and m[0]["feature"] == "a"

    def test_relative_tolerance_allows_small_diff(self):
        full = {"x": 1000.0}
        trunc = {"x": 1000.0000005}  # diff 5e-7 << tol*1000
        assert compare_states(full, trunc, tolerance=1e-9) == []

    def test_missing_key_flagged(self):
        m = compare_states({"a": 1.0, "b": 2.0}, {"a": 1.0}, tolerance=1e-9)
        assert len(m) == 1 and m[0]["kind"] == "missing_in_truncated"


class LeakyBuilder(StateBuilder):
    """Deliberately peeks 5 bars into the future — the audit must catch it."""

    def _extract_volatility_features(self, tf: str, bar_idx: int) -> dict[str, float]:
        feats = super()._extract_volatility_features(tf, bar_idx)
        closes = self._bars[tf]["close"].values
        future_idx = min(bar_idx + 5, len(closes) - 1)
        current = closes[bar_idx]
        feats["leak_future_return"] = (
            float(closes[future_idx] / current) - 1.0 if current > 0 else 0.0
        )
        return feats


class TestAuditCandles:
    # NOTE: audits run with timeframes=["1min"] only. The multi-timeframe
    # builder currently has a KNOWN forming-coarse-bar lookahead (a 15m/1h bar
    # stamped at the decision time is included before it has closed), which is
    # tracked separately — these tests validate the AUDIT mechanism itself.
    KW = {"swing_lookback": 3, "timeframes": ["1min"]}

    def test_clean_pipeline_passes(self):
        candles = make_synthetic_candles()
        report = audit_candles(
            candles,
            n_probes=3,
            warmup_bars=300,
            verbose=False,
            builder_kwargs=dict(self.KW),
        )
        assert report.verdict == "PASS"
        for p in report.probes:
            assert p["ok"], f"unexpected mismatches: {p['mismatches'][:5]}"

    def test_deliberate_leak_detected(self):
        candles = make_synthetic_candles(seed=7)

        original_init = StateBuilder.__init__

        def leaky_init(self, *args, **kwargs):
            original_init(self, *args, **kwargs)

        # Build via subclass without touching builder_kwargs plumbing
        import backtest.lookahead_audit as mod

        real_builder = mod.StateBuilder
        mod.StateBuilder = LeakyBuilder
        try:
            report = audit_candles(
                candles,
                n_probes=3,
                warmup_bars=300,
                verbose=False,
                builder_kwargs=dict(self.KW),
            )
        finally:
            mod.StateBuilder = real_builder

        assert report.verdict == "FAIL"
        assert "leak_future_return" in report.feature_offenders

    def test_insufficient_bars_raise(self):
        candles = make_synthetic_candles(200)
        with pytest.raises(ValueError):
            audit_candles(candles, n_probes=3, warmup_bars=300, verbose=False)

    def test_report_summarize_shape(self):
        candles = make_synthetic_candles()
        report = audit_candles(
            candles,
            n_probes=2,
            warmup_bars=300,
            verbose=False,
            builder_kwargs=dict(self.KW),
        )
        s = report.summarize()
        assert s["verdict"] == "PASS"
        assert s["n_probes"] == 2
        assert s["n_leaky_probes"] == 0
        assert s["features_flagged"] == []
