"""
tests/test_market_narrative.py — Tests for market narrative builder.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from model.market_narrative import (
    NarrativeBuilder,
    MarketNarrative,
    MarketPhase,
    StructureTrend,
    EventType,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class FakeStructure:
    """Minimal structure-like object for testing."""
    def __init__(self, stype, direction, zone_low, zone_high, index=0, age_bars=5):
        self.stype = stype
        self.direction = direction
        self.zone_low = zone_low
        self.zone_high = zone_high
        self.index = index
        self.age_bars = age_bars
        self.size_ATR = 0.5
        self.mitigated = False
        self.timestamp = datetime(2026, 1, 1)


def _ts(hour=12, minute=0):
    return datetime(2026, 1, 1, hour, minute)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEventDetection:
    def test_bos_bullish_detected(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 52100, 100, _ts())

        events = [e for e in narrative.recent_events if e.event_type == EventType.BOS_BULLISH]
        assert len(events) == 1
        assert events[0].implication == "bullish"

    def test_bos_bearish_detected(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=2)]
        narrative = builder.update(structures, 50900, 100, _ts())

        events = [e for e in narrative.recent_events if e.event_type == EventType.BOS_BEARISH]
        assert len(events) == 1
        assert events[0].implication == "bearish"

    def test_choch_detected(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("CHoCH", "bullish", 51000, 52000, age_bars=1)]
        narrative = builder.update(structures, 52100, 100, _ts())

        events = [e for e in narrative.recent_events if e.event_type == EventType.CHOCH_BULLISH]
        assert len(events) == 1
        assert events[0].significance == 0.9

    def test_liquidity_sweep_detected(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("liquidity_sweep", "bullish", 50500, 51000, age_bars=2)]
        narrative = builder.update(structures, 51100, 100, _ts())

        events = [e for e in narrative.recent_events if e.event_type == EventType.LIQUIDITY_SWEEP]
        assert len(events) == 1
        assert "stops grabbed" in events[0].description

    def test_old_events_filtered(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=15)]
        narrative = builder.update(structures, 52100, 100, _ts())
        # age_bars > 10 should not be detected
        assert len(narrative.recent_events) == 0


class TestStructureAnalysis:
    def test_bullish_structure_from_bos(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bullish", 51000, 52000, age_bars=5),
            FakeStructure("BOS", "bullish", 52000, 53000, age_bars=2),
        ]
        narrative = builder.update(structures, 53100, 100, _ts())
        assert narrative.structure.trend == StructureTrend.BULLISH
        assert narrative.structure.confidence > 0.5

    def test_bearish_structure_from_bos(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bearish", 51000, 52000, age_bars=5),
            FakeStructure("BOS", "bearish", 50000, 51000, age_bars=2),
        ]
        narrative = builder.update(structures, 49900, 100, _ts())
        assert narrative.structure.trend == StructureTrend.BEARISH

    def test_ranging_structure(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bullish", 51000, 52000, age_bars=5),
            FakeStructure("BOS", "bearish", 51000, 52000, age_bars=2),
        ]
        narrative = builder.update(structures, 51500, 100, _ts())
        assert narrative.structure.trend == StructureTrend.RANGING

    def test_choch_overrides_bos(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bullish", 51000, 52000, age_bars=15),
            FakeStructure("CHoCH", "bearish", 50000, 51000, age_bars=2),
        ]
        narrative = builder.update(structures, 49900, 100, _ts())
        assert narrative.structure.trend == StructureTrend.BEARISH
        assert narrative.structure.last_choch_direction == "bearish"


class TestPhaseAnalysis:
    def test_markup_phase(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 52100, 100, _ts())
        assert narrative.phase.current_phase == MarketPhase.MARKUP
        assert narrative.phase.favor_trend_following is True

    def test_markdown_phase(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 49900, 100, _ts())
        assert narrative.phase.current_phase == MarketPhase.MARKDOWN
        assert narrative.phase.favor_trend_following is True

    def test_accumulation_phase(self):
        builder = NarrativeBuilder()
        # Ranging with bullish bias
        structures = [
            FakeStructure("BOS", "bullish", 51000, 52000, age_bars=15),
            FakeStructure("BOS", "bearish", 51000, 52000, age_bars=12),
        ]
        # Add some bullish events to bias toward accumulation
        narrative = builder.update(structures, 51500, 100, _ts())
        # The ranging + bullish bias should give accumulation
        assert narrative.phase.is_range_bound is True

    def test_phase_transitions(self):
        builder = NarrativeBuilder()
        # Start with markup
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        n1 = builder.update(structures, 52100, 100, _ts())
        assert n1.phase.current_phase == MarketPhase.MARKUP

        # Switch to bearish
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        n2 = builder.update(structures, 49900, 200, _ts(13))
        assert n2.phase.current_phase == MarketPhase.MARKDOWN


class TestLiquidityMapping:
    def test_buy_side_pool_created(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 53000, age_bars=5)]
        narrative = builder.update(structures, 52000, 100, _ts())
        assert narrative.liquidity.fresh_buy_side_count >= 1

    def test_sell_side_pool_created(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bearish", 49000, 51000, age_bars=5)]
        narrative = builder.update(structures, 50000, 100, _ts())
        assert narrative.liquidity.fresh_sell_side_count >= 1

    def test_sweep_reduces_fresh_count(self):
        builder = NarrativeBuilder()
        # Create a pool at 51000
        structures = [FakeStructure("BOS", "bullish", 50000, 51000, age_bars=5)]
        n1 = builder.update(structures, 50500, 100, _ts())
        fresh_before = n1.liquidity.fresh_buy_side_count

        # Price sweeps above 51000
        n2 = builder.update(structures, 51500, 110, _ts(12, 10))
        # Pool should be marked as swept
        assert n2.liquidity.fresh_buy_side_count <= fresh_before


class TestNarrativeSynthesis:
    def test_story_contains_phase(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 52100, 100, _ts())
        assert "markup" in narrative.story.lower()

    def test_bias_is_bullish_for_bullish_structure(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3),
            FakeStructure("BOS", "bullish", 52000, 53000, age_bars=1),
        ]
        narrative = builder.update(structures, 53100, 100, _ts())
        assert narrative.bias == "bullish"
        assert narrative.bias_strength > 0.5

    def test_bias_is_bearish_for_bearish_structure(self):
        builder = NarrativeBuilder()
        structures = [
            FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3),
            FakeStructure("BOS", "bearish", 50000, 51000, age_bars=1),
        ]
        narrative = builder.update(structures, 49900, 100, _ts())
        assert narrative.bias == "bearish"

    def test_favorable_setups_in_markup(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 52100, 100, _ts())
        assert any("long" in s.lower() for s in narrative.favorable_setups)
        assert any("don't short" in s.lower() for s in narrative.unfavorable_setups)

    def test_favorable_setups_in_markdown(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        narrative = builder.update(structures, 49900, 100, _ts())
        assert any("short" in s.lower() for s in narrative.favorable_setups)
        assert any("don't long" in s.lower() for s in narrative.unfavorable_setups)


class TestKeyLevels:
    def test_key_levels_populated(self):
        builder = NarrativeBuilder()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=5)]
        narrative = builder.update(structures, 51500, 100, _ts())
        assert len(narrative.key_levels) > 0
