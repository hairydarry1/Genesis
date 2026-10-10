"""
model/confluence_scorer.py — Narrative-Aware Confluence Scoring.

Scores trade signals based on confluence of multiple factors:
  1. Structure Quality (0-25) — size, freshness, mitigation
  2. Multi-TF Alignment (0-25) — 15m + 1h trends agree
  3. Phase Fit (0-20) — signal matches current market phase
  4. Premium/Discount (0-20) — price in favorable zone
  5. Liquidity Context (0-15) — supporting sweep, targets
  6. WM Agreement (0-15) — world model confirms
  7. Candlestick Confirm (0-10) — pattern confirms entry

The narrative modifies scores:
  - Markup + bullish signal → boost
  - Counter-trend → penalty
  - Strong bias → boost/penalty

Usage:
    from model.confluence_scorer import ConfluenceScorer

    scorer = ConfluenceScorer()
    scored = scorer.score(signal, state, narrative)
    if scored.passed:
        print(f"Score: {scored.total_score}/100, Size: {scored.suggested_size}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from model.ict_signal_engine import TradeSignal
    from model.market_narrative import MarketNarrative, MarketPhase
    from model.candlestick_detector import CandlestickPattern


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ScorerConfig:
    """Configuration for scoring."""
    min_confluence: float = 60.0
    # Phase modifiers
    trend_phase_boost: float = 1.15
    counter_trend_penalty: float = 0.7
    narrative_alignment_boost: float = 1.1
    narrative_misalignment_penalty: float = 0.8


@dataclass
class ScoreBreakdown:
    """Detailed breakdown of scoring."""
    structure_quality: float = 0.0      # 0-25
    multitf_alignment: float = 0.0      # 0-25
    phase_fit: float = 0.0              # 0-20
    premium_discount: float = 0.0       # 0-20
    liquidity_context: float = 0.0      # 0-15
    wm_agreement: float = 0.0           # 0-15
    candlestick_confirm: float = 0.0    # 0-10
    raw_total: float = 0.0
    phase_modifier: float = 1.0
    modified_total: float = 0.0


@dataclass
class ScoredSignal:
    """A signal with confluence scoring."""
    signal: TradeSignal
    narrative_bias: str
    breakdown: ScoreBreakdown
    total_score: float
    passed: bool
    rejection_reason: str = ""
    suggested_size: float = 0.0
    narrative_context: str = ""


# ---------------------------------------------------------------------------
# Scorer
# ---------------------------------------------------------------------------

class ConfluenceScorer:
    """
    Scores signals based on confluence, weighted by narrative context.

    The key insight: the same signal scores differently depending on
    the market phase and narrative bias.
    """

    def __init__(self, config: ScorerConfig | None = None):
        self.cfg = config or ScorerConfig()

    def score(
        self,
        signal: TradeSignal,
        structures: list | None = None,
        features: dict[str, float] | None = None,
        narrative: MarketNarrative | None = None,
        patterns: list[CandlestickPattern] | None = None,
        trend_15min: str = "flat",
        trend_1h: str = "flat",
        premium_discount_position: float = 0.5,
    ) -> ScoredSignal:
        """
        Score a single signal against the current context.

        Parameters
        ----------
        signal : TradeSignal
            The signal to score
        structures : list, optional
            Active structures for quality scoring
        features : dict, optional
            Feature dict for WM agreement
        narrative : MarketNarrative, optional
            Current market narrative
        patterns : list, optional
            Detected candlestick patterns
        trend_15min, trend_1h : str
            Trend directions ("up", "down", "flat")
        premium_discount_position : float
            0 = at low (discount), 1 = at high (premium)

        Returns
        -------
        ScoredSignal
            Scored signal with breakdown and verdict
        """
        breakdown = ScoreBreakdown()
        structures = structures or []
        features = features or {}
        patterns = patterns or []

        # Factor 1: Structure Quality (0-25)
        breakdown.structure_quality = self._score_structure(signal, structures)

        # Factor 2: Multi-TF Alignment (0-25)
        breakdown.multitf_alignment = self._score_multitf(signal, trend_15min, trend_1h)

        # Factor 3: Phase Fit (0-20)
        breakdown.phase_fit = self._score_phase(signal, narrative)

        # Factor 4: Premium/Discount (0-20)
        breakdown.premium_discount = self._score_premium_discount(signal, premium_discount_position)

        # Factor 5: Liquidity Context (0-15)
        breakdown.liquidity_context = self._score_liquidity(signal, narrative)

        # Factor 6: WM Agreement (0-15)
        breakdown.wm_agreement = self._score_wm(signal, features)

        # Factor 7: Candlestick Confirm (0-10)
        breakdown.candlestick_confirm = self._score_candlesticks(signal, patterns)

        # Raw total
        raw_total = (
            breakdown.structure_quality +
            breakdown.multitf_alignment +
            breakdown.phase_fit +
            breakdown.premium_discount +
            breakdown.liquidity_context +
            breakdown.wm_agreement +
            breakdown.candlestick_confirm
        )
        breakdown.raw_total = min(raw_total, 100.0)

        # Phase modifier
        phase_mod = self._phase_modifier(signal, narrative)
        breakdown.phase_modifier = phase_mod

        # Modified total
        modified_total = raw_total * phase_mod
        breakdown.modified_total = min(modified_total, 100.0)

        # Verdict
        passed = breakdown.modified_total >= self.cfg.min_confluence
        rejection_reason = "" if passed else f"Score {breakdown.modified_total:.0f} < min {self.cfg.min_confluence:.0f}"

        # Sizing
        suggested_size = self._compute_size(breakdown.modified_total, narrative)

        # Narrative context
        narrative_context = ""
        if narrative:
            narrative_context = f"{narrative.phase.current_phase.value}_{narrative.structure.trend.value}_{signal.structure_type}"

        return ScoredSignal(
            signal=signal,
            narrative_bias=narrative.bias if narrative else "neutral",
            breakdown=breakdown,
            total_score=breakdown.modified_total,
            passed=passed,
            rejection_reason=rejection_reason,
            suggested_size=suggested_size,
            narrative_context=narrative_context,
        )

    # ------------------------------------------------------------------
    # Factor scorers
    # ------------------------------------------------------------------

    def _score_structure(self, signal: TradeSignal, structures: list) -> float:
        """Score based on triggering structure quality."""
        s = signal.triggering_structure
        if not s:
            return 5.0

        score = 0.0

        # Size
        if s.size_ATR >= 0.8:
            score += 10
        elif s.size_ATR >= 0.5:
            score += 7
        elif s.size_ATR >= 0.3:
            score += 4
        else:
            score += 1

        # Freshness
        if s.age_bars <= 10:
            score += 8
        elif s.age_bars <= 30:
            score += 5
        elif s.age_bars <= 60:
            score += 2
        else:
            score += 0

        # Mitigation
        if not s.mitigated:
            score += 7
        else:
            score += 2

        return min(score, 25.0)

    def _score_multitf(self, signal: TradeSignal, trend_15min: str, trend_1h: str) -> float:
        """Score based on multi-timeframe alignment."""
        score = 0.0
        direction = "bullish" if signal.direction == 1 else "bearish"

        # 15-minute
        if direction == "bullish" and trend_15min == "up":
            score += 12
        elif direction == "bearish" and trend_15min == "down":
            score += 12
        elif trend_15min == "flat":
            score += 6
        # else: against = 0

        # 1-hour
        if direction == "bullish" and trend_1h == "up":
            score += 13
        elif direction == "bearish" and trend_1h == "down":
            score += 13
        elif trend_1h == "flat":
            score += 5

        return min(score, 25.0)

    def _score_phase(self, signal: TradeSignal, narrative: MarketNarrative | None) -> float:
        """Score based on phase fit."""
        if not narrative:
            return 10.0  # Neutral if no narrative

        phase = narrative.phase.current_phase
        score = 0.0
        direction = "bullish" if signal.direction == 1 else "bearish"

        from model.market_narrative import MarketPhase

        if phase == MarketPhase.MARKUP:
            if direction == "bullish":
                score = 18  # Long in markup = trend alignment
                if signal.structure_type in ("BOS", "FVG", "OB"):
                    score += 2
            else:
                score = 4  # Counter-trend
                if narrative.structure.last_choch_direction == "bearish":
                    score += 8  # CHoCH reversal valid

        elif phase == MarketPhase.MARKDOWN:
            if direction == "bearish":
                score = 18
                if signal.structure_type in ("BOS", "FVG", "OB"):
                    score += 2
            else:
                score = 4
                if narrative.structure.last_choch_direction == "bullish":
                    score += 8

        elif phase == MarketPhase.ACCUMULATION:
            if signal.structure_type in ("EQH", "EQL", "liquidity_sweep"):
                score = 16  # Mean reversion ideal
            elif direction == "bullish" and signal.structure_type in ("FVG", "OB"):
                score = 12
            else:
                score = 6

        elif phase == MarketPhase.DISTRIBUTION:
            if signal.structure_type in ("EQH", "EQL", "liquidity_sweep"):
                score = 16
            elif direction == "bearish" and signal.structure_type in ("FVG", "OB"):
                score = 12
            else:
                score = 6

        else:
            score = 8  # Unknown phase

        return min(score, 20.0)

    def _score_premium_discount(self, signal: TradeSignal, position: float) -> float:
        """Score based on price position in range."""
        score = 0.0

        if signal.direction == 1:  # Longs should be in discount
            if position < 0.3:
                score = 20
            elif position < 0.5:
                score = 15
            elif position < 0.7:
                score = 8
            else:
                score = 2
        else:  # Shorts should be in premium
            if position > 0.7:
                score = 20
            elif position > 0.5:
                score = 15
            elif position > 0.3:
                score = 8
            else:
                score = 2

        return min(score, 20.0)

    def _score_liquidity(self, signal: TradeSignal, narrative: MarketNarrative | None) -> float:
        """Score based on liquidity context."""
        if not narrative:
            return 5.0

        score = 0.0
        liq = narrative.liquidity

        # Supporting sweep
        recent_sweeps = [
            e for e in narrative.recent_events[-5:]
            if e.event_type.value == "liquidity_sweep"
        ]
        supporting = any(
            (signal.direction == 1 and e.implication == "bullish") or
            (signal.direction == -1 and e.implication == "bearish")
            for e in recent_sweeps
        )
        if supporting:
            score += 10
        else:
            score += 3

        # Targets available
        if signal.direction == 1 and liq.fresh_buy_side_count > 0:
            score += 5
        elif signal.direction == -1 and liq.fresh_sell_side_count > 0:
            score += 5
        else:
            score += 1

        return min(score, 15.0)

    def _score_wm(self, signal: TradeSignal, features: dict[str, float]) -> float:
        """Score based on world model agreement."""
        score = 0.0

        momentum = features.get("1min_momentum_score", 0)
        snr = abs(features.get("1min_vol_breakout", 0))

        if signal.direction == 1 and momentum > 0:
            score += 10
        elif signal.direction == -1 and momentum < 0:
            score += 10
        else:
            score += 2

        if snr > 1.5:
            score += 5
        elif snr > 0.8:
            score += 3
        else:
            score += 1

        return min(score, 15.0)

    def _score_candlesticks(self, signal: TradeSignal, patterns: list[CandlestickPattern]) -> float:
        """Score based on candlestick confirmation."""
        score = 5.0  # Neutral base

        direction = "bullish" if signal.direction == 1 else "bearish"

        confirming = [p for p in patterns if p.direction == direction and p.confluence_bonus > 0]
        if confirming:
            best = max(confirming, key=lambda p: p.confluence_bonus)
            score += best.confluence_bonus

        conflicting = [p for p in patterns if p.direction != "neutral" and p.direction != direction]
        if conflicting:
            score -= 3

        indecision = [p for p in patterns if p.pattern_type in ("doji", "inside_bar")]
        if indecision:
            score -= 2

        return max(min(score, 10.0), 0.0)

    # ------------------------------------------------------------------
    # Narrative modifiers
    # ------------------------------------------------------------------

    def _phase_modifier(self, signal: TradeSignal, narrative: MarketNarrative | None) -> float:
        """Apply global multiplier based on narrative context."""
        if not narrative:
            return 1.0

        mod = 1.0
        phase = narrative.phase.current_phase
        direction = "bullish" if signal.direction == 1 else "bearish"

        from model.market_narrative import MarketPhase

        if phase in (MarketPhase.MARKUP, MarketPhase.MARKDOWN):
            trend_dir = "bullish" if phase == MarketPhase.MARKUP else "bearish"
            if direction == trend_dir:
                mod *= self.cfg.trend_phase_boost
            else:
                mod *= self.cfg.counter_trend_penalty

            # Extra boost if structure also confirms
            if narrative.structure.trend.value == trend_dir:
                mod *= 1.1

        elif phase in (MarketPhase.ACCUMULATION, MarketPhase.DISTRIBUTION):
            mod *= 0.9
            if signal.structure_type in ("EQH", "EQL", "liquidity_sweep"):
                mod *= 1.2

        # Narrative bias alignment
        if narrative.bias_strength > 0.7:
            if direction == narrative.bias:
                mod *= self.cfg.narrative_alignment_boost
            else:
                mod *= self.cfg.narrative_misalignment_penalty

        return mod

    def _compute_size(self, score: float, narrative: MarketNarrative | None) -> float:
        """Compute position size based on score and narrative."""
        if score >= 90:
            base = 1.0
        elif score >= 75:
            base = 0.75
        elif score >= 60:
            base = 0.5
        else:
            base = 0.25

        if narrative:
            from model.market_narrative import MarketPhase
            if narrative.phase.current_phase in (MarketPhase.MARKUP, MarketPhase.MARKDOWN):
                base *= 1.1
            elif narrative.phase.current_phase in (MarketPhase.ACCUMULATION, MarketPhase.DISTRIBUTION):
                base *= 0.8

        return min(base, 1.0)
