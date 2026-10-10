"""
model/strategy_generator.py — LLM-powered strategy generation and testing.

The Strategy Generator uses an LLM to:
  1. Propose hypotheses about market patterns
  2. Translate hypotheses into feature combinations
  3. Auto-backtest each hypothesis
  4. Learn from results to propose better hypotheses

This creates a closed loop where the system actually improves over time.

Usage:
    from model.strategy_generator import StrategyGenerator
    gen = StrategyGenerator(llm_backend)
    result = gen.generate_and_test(candles_df, existing_features)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class StrategyHypothesis:
    """A hypothesis about market behavior that can be tested."""
    id: str
    description: str
    features_to_use: list[str]  # Feature names to include
    features_to_create: list[dict]  # New features to generate
    expected_direction: str  # "positive_correlation", "negative_correlation", "regime_dependent"
    confidence: float  # 0-1, LLM's confidence
    source: str  # "llm", "auto", "mutation"
    # Results (filled after backtest)
    backtest_sharpe: float | None = None
    backtest_return: float | None = None
    backtest_max_dd: float | None = None
    backtest_win_rate: float | None = None
    passed: bool = False
    error: str | None = None


@dataclass
class GenerationResult:
    """Result of one generation cycle."""
    hypotheses_tested: int
    hypotheses_passed: int
    best_hypothesis: StrategyHypothesis | None
    all_results: list[StrategyHypothesis]
    elapsed_seconds: float


class StrategyGenerator:
    """
    Uses LLM to generate and test trading strategy hypotheses.

    The generator:
    1. Sends market context to LLM
    2. LLM proposes a hypothesis
    3. We translate it into features
    4. We backtest it
    5. We feed results back to LLM for next iteration
    """

    SYSTEM_PROMPT = """You are a quantitative researcher for synthetic indices trading (Deriv Boom/Crash/Volatility).

Your job: propose testable hypotheses about market patterns that could be exploited for profit.

RULES:
1. Be specific — "momentum works" is useless, "5-bar RSI > 70 predicts 2-bar reversal with 60% accuracy" is useful
2. Think about regime — different patterns work in different volatility conditions
3. Consider cross-index signals — Boom and Crash are inversely correlated
4. Focus on risk-adjusted returns, not just raw returns
5. Be falsifiable — each hypothesis must be testable with backtesting

OUTPUT FORMAT (JSON only):
{
  "hypothesis": "1-2 sentence description of the pattern",
  "features": ["list", "of", "existing", "feature", "names", "to", "use"],
  "new_features": [
    {"name": "feature_name", "formula": "description of how to compute it", "category": "momentum|volatility|mean_reversion|correlation"}
  ],
  "expected_direction": "positive_correlation|negative_correlation|regime_dependent",
  "confidence": 0.0-1.0,
  "reasoning": "why this pattern should work"
}

FEEDBACK FORMAT:
When given backtest results, analyze why the hypothesis passed or failed and propose improvements.
If it failed, explain what went wrong and suggest a modified hypothesis.
If it passed, suggest how to strengthen or generalize it."""

    def __init__(self, llm_backend=None):
        self.llm = llm_backend
        self.history: list[StrategyHypothesis] = []
        self.generation_count = 0

    def generate_and_test(
        self,
        candles: pd.DataFrame,
        existing_features: dict[str, np.ndarray],
        targets: np.ndarray,
        n_hypotheses: int = 5,
        max_features: int = 8,
    ) -> GenerationResult:
        """
        Generate and test multiple strategy hypotheses.

        Returns the best hypothesis that passes backtesting.
        """
        t0 = time.time()
        tested = []
        passed = []

        for i in range(n_hypotheses):
            logger.info("hypothesis %d/%d", i + 1, n_hypotheses)

            # Generate hypothesis
            if self.llm:
                hypothesis = self._llm_generate(candles, existing_features, self.history)
            else:
                hypothesis = self._rule_based_generate(existing_features, i)

            if hypothesis is None:
                continue

            # Test hypothesis
            try:
                result = self._test_hypothesis(hypothesis, existing_features, targets, max_features)
                tested.append(result)
                if result.passed:
                    passed.append(result)
                    logger.info("  PASSED: sharpe=%.2f return=%.2f%%", result.backtest_sharpe or 0, (result.backtest_return or 0) * 100)
                else:
                    logger.info("  FAILED: %s", result.error or "low sharpe")
            except Exception as e:
                hypothesis.error = str(e)
                tested.append(hypothesis)
                logger.warning("  ERROR: %s", e)

            self.history.append(hypothesis)

        best = max(passed, key=lambda h: h.backtest_sharpe or -999) if passed else None
        self.generation_count += 1

        return GenerationResult(
            hypotheses_tested=len(tested),
            hypotheses_passed=len(passed),
            best_hypothesis=best,
            all_results=tested,
            elapsed_seconds=time.time() - t0,
        )

    def _llm_generate(self, candles, existing_features, history) -> StrategyHypothesis | None:
        """Use LLM to generate a hypothesis."""
        if not self.llm:
            return None

        # Build context for LLM
        recent = candles.tail(200)
        close = recent["close"].values
        returns = np.diff(np.log(np.maximum(close, 1e-10)))

        context = f"""Market data (last 200 bars):
- Current price: {close[-1]:.2f}
- Recent returns: mean={np.mean(returns):.6f}, std={np.std(returns):.6f}, skew={pd.Series(returns).skew():.3f}
- Volatility regime: {'high' if np.std(returns[-20:]) > np.std(returns) * 1.5 else 'low'}
- Trend: {'up' if np.mean(returns[-20:]) > 0 else 'down'}
- Available features: {list(existing_features.keys())[:20]}

Previous hypotheses tested: {len(history)}"""

        if history:
            recent_history = history[-3:]
            context += "\n\nRecent results:\n"
            for h in recent_history:
                status = "PASSED" if h.passed else "FAILED"
                context += f"- {h.description}: {status} (sharpe={h.backtest_sharpe:.2f})\n"

        try:
            response = self.llm.complete(self.SYSTEM_PROMPT, context, max_tokens=500)
            # Parse JSON from response
            json_start = response.find("{")
            json_end = response.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                data = json.loads(response[json_start:json_end])
                return StrategyHypothesis(
                    id=f"gen{self.generation_count}_llm",
                    description=data.get("hypothesis", "LLM hypothesis"),
                    features_to_use=data.get("features", []),
                    features_to_create=data.get("new_features", []),
                    expected_direction=data.get("expected_direction", "positive_correlation"),
                    confidence=data.get("confidence", 0.5),
                    source="llm",
                )
        except Exception as e:
            logger.warning("LLM generation failed: %s", e)

        return None

    def _rule_based_generate(self, existing_features: dict, seed: int) -> StrategyHypothesis:
        """Generate a hypothesis using rules when no LLM is available."""
        templates = [
            {
                "description": "Momentum reversal: high RSI predicts short-term mean reversion",
                "features": ["rsi_14", "momentum_5", "zscore_20"],
                "direction": "negative_correlation",
            },
            {
                "description": "Volatility breakout: expanding BB width predicts trend continuation",
                "features": ["bb_width_20", "hvol_20", "trend_strength"],
                "direction": "positive_correlation",
            },
            {
                "description": "Volume confirmation: rising volume confirms price direction",
                "features": ["ud_volume_ratio", "vwap_dist", "candle_body_ratio"],
                "direction": "positive_correlation",
            },
            {
                "description": "Regime switching: low-vol periods precede high-vol breakouts",
                "features": ["vol_regime_ratio", "hvol_20", "bb_width_20"],
                "direction": "regime_dependent",
            },
            {
                "description": "Trend following: ADX-like trend strength predicts continuation",
                "features": ["trend_strength", "adj_momentum", "spread_proxy"],
                "direction": "positive_correlation",
            },
            {
                "description": "Mean reversion: extreme z-scores predict reversion",
                "features": ["zscore_50", "rsi_divergence", "hvol_50"],
                "direction": "negative_correlation",
            },
            {
                "description": "Microstructure: shadow ratio predicts reversal pressure",
                "features": ["shadow_ratio", "price_impact", "candle_body_ratio"],
                "direction": "negative_correlation",
            },
            {
                "description": "Entropy regime: high entropy periods favor trend following",
                "features": ["entropy_20", "autocorr_20", "hvol_20"],
                "direction": "regime_dependent",
            },
        ]

        template = templates[seed % len(templates)]
        return StrategyHypothesis(
            id=f"gen{self.generation_count}_rule_{seed}",
            description=template["description"],
            features_to_use=[f for f in template["features"] if f in existing_features],
            features_to_create=[],
            expected_direction=template["direction"],
            confidence=0.5,
            source="rule",
        )

    def _test_hypothesis(
        self,
        hypothesis: StrategyHypothesis,
        existing_features: dict[str, np.ndarray],
        targets: np.ndarray,
        max_features: int,
    ) -> StrategyHypothesis:
        """Test a hypothesis by building a simple model and backtesting."""
        from sklearn.linear_model import Ridge
        from sklearn.model_selection import TimeSeriesSplit

        # Collect available features
        available = []
        for fname in hypothesis.features_to_use:
            if fname in existing_features:
                available.append(existing_features[fname])

        if len(available) == 0:
            hypothesis.error = "No available features"
            hypothesis.passed = False
            return hypothesis

        # Stack features
        X = np.column_stack(available[:max_features])
        y = targets[:, 0] if targets.ndim > 1 else targets  # Use first horizon step

        # Handle NaN
        mask = ~(np.isnan(X).any(axis=1) | np.isinf(X).any(axis=1) | np.isnan(y) | np.isinf(y))
        X = X[mask]
        y = y[mask]

        if len(X) < 500:
            hypothesis.error = f"Not enough data: {len(X)} samples"
            hypothesis.passed = False
            return hypothesis

        # Time-series cross-validation
        tscv = TimeSeriesSplit(n_splits=3)
        sharpes = []
        returns = []

        for train_idx, test_idx in tscv.split(X):
            X_train, X_test = X[train_idx], X[test_idx]
            y_train, y_test = y[train_idx], y[test_idx]

            # Simple linear model
            model = Ridge(alpha=1.0)
            model.fit(X_train, y_train)
            preds = model.predict(X_test)

            # Simulate trading: buy if predicted > 0, sell if < 0
            signals = np.sign(preds)
            strategy_returns = signals * y_test

            if np.std(strategy_returns) > 0:
                sharpe = np.mean(strategy_returns) / np.std(strategy_returns) * np.sqrt(252 * 24 * 60)
                sharpes.append(sharpe)
                returns.append(np.sum(strategy_returns))

        if sharpes:
            avg_sharpe = np.mean(sharpes)
            avg_return = np.mean(returns)
            hypothesis.backtest_sharpe = avg_sharpe
            hypothesis.backtest_return = avg_return

            # Pass criteria: Sharpe > 1.0 and positive return
            hypothesis.passed = avg_sharpe > 1.0 and avg_return > 0
            if not hypothesis.passed:
                hypothesis.error = f"Sharpe={avg_sharpe:.2f}, Return={avg_return*100:.2f}%"
        else:
            hypothesis.error = "No valid predictions"

        return hypothesis
