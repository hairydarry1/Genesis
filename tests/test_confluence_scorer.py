"""
tests/test_confluence_scorer.py — Tests for confluence scorer.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from model.confluence_scorer import ConfluenceScorer, ScoredSignal
from model.ict_signal_engine import TradeSignal
from model.market_narrative import (
    NarrativeBuilder,
    MarketNarrative,
    MarketPhase,
    StructureTrend,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeStructure:
    def __init__(self, stype, direction, zone_low, zone_high, age_bars=5, size_ATR=0.5, mitigated=False):
        self.stype = stype
        self.direction = direction
        self.zone_low = zone_low
        self.zone_high = zone_high
        self.age_bars = age_bars
        self.size_ATR = size_ATR
        self.mitigated = mitigated
        self.index = 0


def _make_signal(direction=1, structure_type="FVG", entry=52000, sl=51900, tp=52200):
    return TradeSignal(
        source="ict_fvg",
        direction=direction,
        entry_price=entry,
        stop_loss=sl,
        take_profit=tp,
        strength=0.7,
        reasoning="test",
        structure_type=structure_type,
    )


def _make_narrative(bias="bullish", phase=MarketPhase.MARKUP, trend=StructureTrend.BULLISH):
    builder = NarrativeBuilder()
    structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
    return builder.update(structures, 52100, 100, datetime(2026, 1, 1))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHighConfluence:
    def test_bullish_fvg_in_markup(self):
        """Best case: bullish signal in markup with structure alignment."""
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1, structure_type="FVG")
        structures = [FakeStructure("FVG", "bullish", 51900, 52000, age_bars=5, size_ATR=0.8)]
        narrative = _make_narrative(bias="bullish", phase=MarketPhase.MARKUP)

        scored = scorer.score(
            signal,
            structures=structures,
            narrative=narrative,
            trend_15min="up",
            trend_1h="up",
            premium_discount_position=0.3,
        )

        assert scored.total_score >= 80
        assert scored.passed is True
        assert scored.suggested_size >= 0.75


class TestLowConfluence:
    def test_long_in_markdown(self):
        """Worst case: counter-trend in markdown."""
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1, structure_type="FVG")
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]

        builder = NarrativeBuilder()
        bearish_structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        narrative = builder.update(bearish_structures, 49900, 100, datetime(2026, 1, 1))

        scored = scorer.score(
            signal,
            structures=structures,
            narrative=narrative,
            trend_15min="down",
            trend_1h="down",
            premium_discount_position=0.3,
        )

        assert scored.total_score < 60
        assert scored.passed is False


class TestStructureScoring:
    def test_large_fresh_structure_high_score(self):
        scorer = ConfluenceScorer()
        struct = FakeStructure("FVG", "bullish", 51900, 52000, age_bars=3, size_ATR=0.9)
        signal = _make_signal(direction=1)
        signal.triggering_structure = struct

        scored = scorer.score(signal, structures=[struct])
        assert scored.breakdown.structure_quality >= 20

    def test_small_old_structure_low_score(self):
        scorer = ConfluenceScorer()
        struct = FakeStructure("FVG", "bullish", 51900, 52000, age_bars=80, size_ATR=0.2)
        signal = _make_signal(direction=1)
        signal.triggering_structure = struct

        scored = scorer.score(signal, structures=[struct])
        assert scored.breakdown.structure_quality <= 10


class TestMultiTFScoring:
    def test_aligned_trends_high_score(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        scored = scorer.score(signal, trend_15min="up", trend_1h="up")
        assert scored.breakdown.multitf_alignment >= 20

    def test_against_trends_low_score(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        scored = scorer.score(signal, trend_15min="down", trend_1h="down")
        assert scored.breakdown.multitf_alignment == 0

    def test_mixed_trends_medium_score(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        scored = scorer.score(signal, trend_15min="up", trend_1h="flat")
        assert 10 < scored.breakdown.multitf_alignment < 20


class TestPhaseScoring:
    def test_long_in_markup_high(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1, structure_type="FVG")
        narrative = _make_narrative(phase=MarketPhase.MARKUP)

        scored = scorer.score(signal, narrative=narrative)
        assert scored.breakdown.phase_fit >= 15

    def test_short_in_markup_low(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=-1, structure_type="FVG")
        narrative = _make_narrative(phase=MarketPhase.MARKUP)

        scored = scorer.score(signal, narrative=narrative)
        assert scored.breakdown.phase_fit <= 10


class TestPremiumDiscountScoring:
    def test_long_in_discount_high(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        scored = scorer.score(signal, premium_discount_position=0.2)
        assert scored.breakdown.premium_discount >= 15

    def test_long_in_premium_low(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        scored = scorer.score(signal, premium_discount_position=0.8)
        assert scored.breakdown.premium_discount <= 5


class TestCandlestickScoring:
    def test_confirming_pattern_boosts(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        class FakePattern:
            pattern_type = "bullish_engulfing"
            direction = "bullish"
            confluence_bonus = 8

        scored = scorer.score(signal, patterns=[FakePattern()])
        assert scored.breakdown.candlestick_confirm > 5

    def test_conflicting_pattern_reduces(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)

        class FakePattern:
            pattern_type = "bearish_engulfing"
            direction = "bearish"
            confluence_bonus = 5

        scored = scorer.score(signal, patterns=[FakePattern()])
        assert scored.breakdown.candlestick_confirm < 5


class TestPhaseModifier:
    def test_markup_bullish_boost(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)
        narrative = _make_narrative(phase=MarketPhase.MARKUP)

        scored = scorer.score(signal, narrative=narrative)
        assert scored.breakdown.phase_modifier > 1.0

    def test_markdown_bullish_penalty(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)
        builder = NarrativeBuilder()
        bearish = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        narrative = builder.update(bearish, 49900, 100, datetime(2026, 1, 1))

        scored = scorer.score(signal, narrative=narrative)
        assert scored.breakdown.phase_modifier < 1.0


class TestSizingLogic:
    def test_high_score_full_size(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)
        narrative = _make_narrative(phase=MarketPhase.MARKUP)

        scored = scorer.score(signal, narrative=narrative, trend_15min="up", trend_1h="up")
        if scored.total_score >= 90:
            assert scored.suggested_size >= 0.9

    def test_low_score_rejected(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1)
        builder = NarrativeBuilder()
        bearish = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        narrative = builder.update(bearish, 49900, 100, datetime(2026, 1, 1))

        scored = scorer.score(
            signal, narrative=narrative,
            trend_15min="down", trend_1h="down",
            premium_discount_position=0.8,
        )
        if scored.total_score < 60:
            assert scored.passed is False


class TestNarrativeContext:
    def test_narrative_context_string(self):
        scorer = ConfluenceScorer()
        signal = _make_signal(direction=1, structure_type="FVG")
        narrative = _make_narrative()

        scored = scorer.score(signal, narrative=narrative)
        assert "markup" in scored.narrative_context
        assert "bullish" in scored.narrative_context
        assert "FVG" in scored.narrative_context
