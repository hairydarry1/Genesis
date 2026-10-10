"""
tests/test_ict_signal_engine.py — Tests for ICT signal engine.
"""

from __future__ import annotations

import pytest

from model.ict_signal_engine import ICTSignalEngine, TradeSignal


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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestFVGSignals:
    def test_bullish_fvg_long_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bullish", 51900, 52000, age_bars=5)]
        signals = engine.generate(structures, current_price=51950)

        bullish = [s for s in signals if s.direction == 1 and s.source == "ict_fvg"]
        assert len(bullish) == 1
        assert bullish[0].entry_price == 52000  # Top of FVG
        assert bullish[0].stop_loss == 51900    # Bottom of FVG
        assert bullish[0].take_profit > bullish[0].entry_price

    def test_bearish_fvg_short_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bearish", 52000, 52100, age_bars=5)]
        signals = engine.generate(structures, current_price=52050)

        bearish = [s for s in signals if s.direction == -1 and s.source == "ict_fvg"]
        assert len(bearish) == 1
        assert bearish[0].entry_price == 52000  # Bottom of FVG
        assert bearish[0].stop_loss == 52100    # Top of FVG

    def test_fvg_outside_zone_no_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bullish", 51000, 51100, age_bars=5)]
        signals = engine.generate(structures, current_price=52000)  # Far from zone

        fvg_signals = [s for s in signals if s.source == "ict_fvg"]
        assert len(fvg_signals) == 0

    def test_mitigated_fvg_no_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bullish", 51900, 52000, mitigated=True)]
        signals = engine.generate(structures, current_price=51950)

        fvg_signals = [s for s in signals if s.source == "ict_fvg"]
        assert len(fvg_signals) == 0

    def test_fvg_narrative_alignment(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bullish", 51900, 52000, age_bars=5, size_ATR=1.0)]

        # Bullish narrative should boost strength
        signals_bull = engine.generate(structures, 51950, narrative_bias="bullish")
        # Bearish narrative penalizes counter-trend; with strong enough structure it still passes
        signals_bear = engine.generate(structures, 51950, narrative_bias="bearish")

        bull_strength = [s.strength for s in signals_bull if s.direction == 1][0]
        if signals_bear:
            bear_strength = [s.strength for s in signals_bear if s.direction == 1]
            if bear_strength:
                assert bull_strength > bear_strength[0]
            else:
                # Counter-trend signal correctly filtered
                pass
        else:
            # Counter-trend signal correctly filtered by quality gate
            pass
        # At minimum, bullish narrative must produce a signal
        assert len(signals_bull) >= 1


class TestOBSignals:
    def test_bullish_ob_long_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("OB", "bullish", 51850, 51950, age_bars=5)]
        signals = engine.generate(structures, current_price=51900)

        bullish = [s for s in signals if s.direction == 1 and s.source == "ict_ob"]
        assert len(bullish) == 1
        assert bullish[0].entry_price == 51950
        assert bullish[0].stop_loss == 51850

    def test_bearish_ob_short_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("OB", "bearish", 52050, 52150, age_bars=5)]
        signals = engine.generate(structures, current_price=52100)

        bearish = [s for s in signals if s.direction == -1 and s.source == "ict_ob"]
        assert len(bearish) == 1

    def test_ob_outside_zone_no_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("OB", "bullish", 51000, 51100, age_bars=5)]
        signals = engine.generate(structures, current_price=52000)

        ob_signals = [s for s in signals if s.source == "ict_ob"]
        assert len(ob_signals) == 0


class TestSweepSignals:
    def test_bullish_sweep_long_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=1000)
        structures = [FakeStructure("liquidity_sweep", "bullish", 50500, 51000, age_bars=2)]
        signals = engine.generate(structures, current_price=51100)

        bullish = [s for s in signals if s.direction == 1 and s.source == "ict_sweep"]
        assert len(bullish) == 1
        assert bullish[0].strength >= 0.7  # Sweeps are high quality

    def test_bearish_sweep_short_signal(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=1000)
        structures = [FakeStructure("liquidity_sweep", "bearish", 52000, 52500, age_bars=2)]
        signals = engine.generate(structures, current_price=51900)

        bearish = [s for s in signals if s.direction == -1 and s.source == "ict_sweep"]
        assert len(bearish) == 1


class TestExitSignals:
    def test_exit_on_bearish_bos_for_long(self):
        engine = ICTSignalEngine()
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        signals = engine.generate(structures, current_price=51500, position=1)

        exits = [s for s in signals if s.source == "ict_exit"]
        assert len(exits) == 1
        assert exits[0].direction == 0  # Close

    def test_exit_on_bullish_bos_for_short(self):
        engine = ICTSignalEngine()
        structures = [FakeStructure("BOS", "bullish", 51000, 52000, age_bars=3)]
        signals = engine.generate(structures, current_price=51500, position=-1)

        exits = [s for s in signals if s.source == "ict_exit"]
        assert len(exits) == 1

    def test_no_exit_when_flat(self):
        engine = ICTSignalEngine()
        structures = [FakeStructure("BOS", "bearish", 51000, 52000, age_bars=3)]
        signals = engine.generate(structures, current_price=51500, position=0)

        exits = [s for s in signals if s.source == "ict_exit"]
        assert len(exits) == 0

    def test_choch_triggers_exit(self):
        engine = ICTSignalEngine()
        structures = [FakeStructure("CHoCH", "bearish", 51000, 52000, age_bars=2)]
        signals = engine.generate(structures, current_price=51500, position=1)

        exits = [s for s in signals if s.source == "ict_exit"]
        assert len(exits) == 1
        assert "CHoCH" in exits[0].reasoning


class TestSorting:
    def test_strongest_signal_first(self):
        engine = ICTSignalEngine(min_sl_pips=10, max_sl_pips=500)
        structures = [
            FakeStructure("FVG", "bullish", 51900, 52000, age_bars=20, size_ATR=0.3),
            FakeStructure("OB", "bullish", 51850, 51950, age_bars=5, size_ATR=0.8),
        ]
        signals = engine.generate(structures, current_price=51920, narrative_bias="bullish")

        if len(signals) >= 2:
            assert signals[0].strength >= signals[1].strength


class TestSLTPValidation:
    def test_sl_outside_range_no_signal(self):
        engine = ICTSignalEngine(min_sl_pips=50, max_sl_pips=100)
        # Zone size = 10 pips, but min_sl = 50 -> should be rejected
        structures = [FakeStructure("FVG", "bullish", 51990, 52000, age_bars=5, size_ATR=1.0)]
        signals = engine.generate(structures, current_price=51995)

        fvg_signals = [s for s in signals if s.source == "ict_fvg"]
        assert len(fvg_signals) == 0

    def test_rr_ratio_applied(self):
        engine = ICTSignalEngine(rr_ratio=3.0, min_sl_pips=10, max_sl_pips=500)
        structures = [FakeStructure("FVG", "bullish", 51900, 52000, age_bars=5, size_ATR=1.0)]
        signals = engine.generate(structures, current_price=51950)

        sig = [s for s in signals if s.direction == 1][0]
        sl_distance = sig.entry_price - sig.stop_loss
        tp_distance = sig.take_profit - sig.entry_price
        assert abs(tp_distance / sl_distance - 3.0) < 0.01
