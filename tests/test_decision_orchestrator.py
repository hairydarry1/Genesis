"""
tests/test_decision_orchestrator.py — Tests for decision orchestrator.
"""

from __future__ import annotations

import pytest

from model.decision_orchestrator import DecisionOrchestrator, Decision, ExitDecision


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(epoch=1000000, o=100, h=105, l=95, c=102):
    return {"epoch": epoch, "open": o, "high": h, "low": l, "close": c, "volume": 100}


def _seed_candles(n=50, base_price=50000, trend="up"):
    """Generate a sequence of candles with a trend."""
    candles = []
    price = base_price
    for i in range(n):
        if trend == "up":
            drift = 10
        elif trend == "down":
            drift = -10
        else:
            drift = 0

        o = price
        c = price + drift
        h = max(o, c) + 5
        l = min(o, c) - 5
        candles.append(_make_candle(epoch=1000000 + i * 60, o=o, h=h, l=l, c=c))
        price = c
    return candles


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestInitialization:
    def test_initial_state(self):
        orch = DecisionOrchestrator()
        assert orch.position == 0
        assert orch.equity == 10000.0
        assert orch.bars_held == 0
        assert len(orch.trade_log) == 0


class TestStateAssembly:
    def test_single_bar_no_crash(self):
        orch = DecisionOrchestrator()
        result = orch.on_bar(_make_candle())
        # Should not crash, result may be None (no signals from 1 bar)
        assert result is None or isinstance(result, (Decision, ExitDecision))

    def test_state_assembles_with_enough_bars(self):
        orch = DecisionOrchestrator()
        for candle in _seed_candles(30):
            orch.on_bar(candle)
        # After 30 bars, we should have some state
        assert len(orch._candles) == 30


class TestNarrativeIntegration:
    def test_narrative_updates(self):
        orch = DecisionOrchestrator()
        for candle in _seed_candles(30, trend="up"):
            orch.on_bar(candle)

        narrative = orch.narrative_builder.update(
            structures=[], current_price=50300, bar_idx=29,
            timestamp=__import__("datetime").datetime.now(),
        )
        # Narrative should be built
        assert narrative is not None
        assert narrative.story != ""


class TestSignalGeneration:
    def test_generates_signals_with_structures(self):
        orch = DecisionOrchestrator(min_confluence=0)  # Accept all
        # Seed with trending data to create structures
        for candle in _seed_candles(40, trend="up"):
            orch.on_bar(candle)

        # After 40 bars with structures, we might get a signal
        # Just verify no crash
        result = orch.on_bar(_make_candle(epoch=1000000 + 40 * 60, c=50500))
        assert result is None or isinstance(result, (Decision, ExitDecision))


class TestExitLogic:
    def test_exit_on_max_hold(self):
        orch = DecisionOrchestrator()
        # Manually set position
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 65

        state = orch._assemble_state(_make_candle(c=50100), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50100, 65, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "max_hold"

    def test_exit_on_stop_loss_long(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 5

        state = orch._assemble_state(_make_candle(c=49800), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 49800, 5, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "stop_loss"

    def test_exit_on_take_profit_short(self):
        orch = DecisionOrchestrator()
        orch.position = -1
        orch.entry_price = 50000
        orch.bars_held = 5

        # Short TP triggers when price <= entry * (1 - tp_pct) = 49600 (TP=0.8%)
        state = orch._assemble_state(_make_candle(c=49500), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 49500, 5, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "take_profit"


class TestExecution:
    def test_execute_entry_opens_position(self):
        orch = DecisionOrchestrator()
        decision = Decision(
            action="open_long", direction=1, size=0.5,
            entry_price=50000, stop_loss=49900, take_profit=50200,
            confidence=0.8, signal_source="ict_fvg", confluence_score=75,
            reasoning="test",
        )
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50000, 0, __import__("datetime").datetime.now())

        state = orch._assemble_state(_make_candle(c=50000), {}, "flat", "flat")
        orch._execute_entry(decision, state, narrative)

        assert orch.position == 1
        assert orch.entry_price == 50000
        assert orch.bars_held == 0
        assert len(orch.trade_log) == 1

    def test_execute_exit_closes_position(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 10

        exit_dec = ExitDecision(should_exit=True, reason="test", exit_type="test")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50100, 10, __import__("datetime").datetime.now())

        state = orch._assemble_state(_make_candle(c=50100), {}, "flat", "flat")
        orch._execute_exit(exit_dec, state, narrative)

        assert orch.position == 0
        assert orch.equity == 10100  # 10000 + (50100-50000)*1
        assert len(orch.trade_log) == 1


class TestSummary:
    def test_empty_summary(self):
        orch = DecisionOrchestrator()
        summary = orch.get_summary()
        assert summary["trades"] == 0

    def test_summary_after_trades(self):
        orch = DecisionOrchestrator()
        orch.trade_log = [
            {"action": "close", "pnl": 100},
            {"action": "close", "pnl": -50},
            {"action": "close", "pnl": 200},
        ]
        summary = orch.get_summary()
        assert summary["trades"] == 3
        assert summary["pnl"] == 250
        assert summary["win_rate"] == 2 / 3


class TestTimeBasedExit:
    def test_losing_trade_exits_after_n_bars(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 25  # > losing_exit_bars (20)
        orch.losing_exit_bars = 20

        # Price is below entry (losing)
        state = orch._assemble_state(_make_candle(c=49950), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 49950, 25, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "time_exit"
        assert "losing" in exit_dec.reason

    def test_winning_trade_not_exited_by_time(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 25
        orch.losing_exit_bars = 20

        # Price is above entry (winning)
        state = orch._assemble_state(_make_candle(c=50100), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50100, 25, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        # Should not exit just because of time (it's winning)
        assert not (exit_dec.should_exit and exit_dec.exit_type == "time_exit")

    def test_flat_trade_exits_after_long_hold(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 45  # > flat_exit_bars (40)
        orch.flat_exit_bars = 40
        orch.losing_exit_bars = 50  # Won't trigger

        # Price essentially flat
        state = orch._assemble_state(_make_candle(c=50001), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50001, 45, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "time_exit"
        assert "flat" in exit_dec.reason


class TestBOSBreakevenStop:
    def test_breakeven_stop_after_favorable_bos(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 5
        orch.sl_to_breakeven = True

        # Create a favorable bullish BOS
        from features.structures import StructureInstance
        bos = StructureInstance(
            stype="BOS", direction="bullish", index=100,
            zone_low=50100, zone_high=50200, size_ATR=1.0,
            age_bars=3, mitigated=False, timestamp=__import__("datetime").datetime.now(),
        )

        state = orch._assemble_state(_make_candle(c=50100), {}, "flat", "flat")
        state.recent_bos = bos

        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50100, 5, __import__("datetime").datetime.now())

        # First call activates breakeven
        exit_dec = orch._check_exit(state, narrative)
        # Should not exit yet (just activated)
        assert orch.sl_breakeven_activated is True

    def test_breakeven_activated_flag_prevents_recheck(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 5
        orch.sl_to_breakeven = True
        orch.sl_breakeven_activated = True  # Already activated

        from features.structures import StructureInstance
        bos = StructureInstance(
            stype="BOS", direction="bullish", index=100,
            zone_low=50100, zone_high=50200, size_ATR=1.0,
            age_bars=3, mitigated=False, timestamp=__import__("datetime").datetime.now(),
        )
        state = orch._assemble_state(_make_candle(c=50100), {}, "flat", "flat")
        state.recent_bos = bos

        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50100, 5, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        # Should not re-activate
        assert exit_dec.should_exit is False


class TestLowConfidenceExit:
    def test_low_confidence_losing_exits(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 20  # > entry_exit_bars (15)
        orch.entry_confluence = 55  # < 70
        orch.losing_exit_bars = 50  # Won't trigger
        orch.flat_exit_bars = 60   # Won't trigger

        state = orch._assemble_state(_make_candle(c=49999), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 49999, 20, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        assert exit_dec.should_exit is True
        assert exit_dec.exit_type == "confidence_exit"
        assert "55" in exit_dec.reason

    def test_high_confidence_not_exited(self):
        orch = DecisionOrchestrator()
        orch.position = 1
        orch.entry_price = 50000
        orch.bars_held = 20
        orch.entry_confluence = 85  # > 70
        orch.losing_exit_bars = 50
        orch.flat_exit_bars = 60

        state = orch._assemble_state(_make_candle(c=49999), {}, "flat", "flat")
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 49999, 20, __import__("datetime").datetime.now())

        exit_dec = orch._check_exit(state, narrative)
        # High confidence trade should not exit
        assert not (exit_dec.should_exit and exit_dec.exit_type == "confidence_exit")


class TestVolAdjustedExits:
    def _make_high_vol_orch(self):
        orch = DecisionOrchestrator()
        orch.entry_price = 50000
        orch.use_atr_exits = True
        orch.atr_normal_vol = 0.002
        # Add candles with high volatility (wider ranges = bigger ATR)
        for i in range(20):
            orch._candles.append({
                "open": 50000 + i * 50,
                "high": 50300 + i * 50,  # 300pt range
                "low": 49900 + i * 50,
                "close": 50100 + i * 50,
                "volume": 1.0,
            })
        return orch

    def test_tp_scaled_by_vol(self):
        orch = self._make_high_vol_orch()
        tp = orch._vol_adjusted_tp()
        assert tp > 0
        assert tp > orch.tp_pct  # Higher vol = wider TP

    def test_sl_scaled_by_vol(self):
        orch = self._make_high_vol_orch()
        sl = orch._vol_adjusted_sl()
        assert sl > 0
        assert sl >= orch.sl_pct  # Higher vol = wider SL (at minimum equal)

    def test_atr_computation(self):
        orch = DecisionOrchestrator()
        # Add enough candles
        for i in range(20):
            orch._candles.append({
                "open": 50000 + i,
                "high": 50010 + i,
                "low": 49990 + i,
                "close": 50005 + i,
                "volume": 1.0,
            })
        atr = orch._compute_atr(14)
        assert atr > 0


class TestEntryReset:
    def test_entry_resets_flags(self):
        orch = DecisionOrchestrator()
        decision = Decision(
            action="open_long", direction=1, size=0.5,
            entry_price=50000, stop_loss=49900, take_profit=50200,
            confidence=0.8, signal_source="ict_fvg", confluence_score=85,
            reasoning="test",
        )
        from model.market_narrative import NarrativeBuilder
        builder = NarrativeBuilder()
        narrative = builder.update([], 50000, 0, __import__("datetime").datetime.now())

        state = orch._assemble_state(_make_candle(c=50000), {}, "flat", "flat")
        orch._execute_entry(decision, state, narrative)

        assert orch.entry_confluence == 85
        assert orch.sl_breakeven_activated is False
        assert orch.trailing_activated is False
