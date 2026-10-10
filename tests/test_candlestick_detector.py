"""
tests/test_candlestick_detector.py — Tests for candlestick pattern detection.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from model.candlestick_detector import CandlestickDetector, CandlestickPattern


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bars(rows: list[dict]) -> pd.DataFrame:
    """Build a small OHLCV DataFrame from dicts."""
    return pd.DataFrame(rows)


def _bar(o, h, l, c) -> dict:
    return {"open": o, "high": h, "low": l, "close": c, "volume": 100}


class FakeStructure:
    """Minimal structure-like object for testing."""
    def __init__(self, zone_low, zone_high, stype="FVG", direction="bullish"):
        self.zone_low = zone_low
        self.zone_high = zone_high
        self.stype = stype
        self.direction = direction


class FakePool:
    """Minimal liquidity pool for testing."""
    def __init__(self, price, kind="buy_side"):
        self.price = price
        self.kind = kind


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestBullishEngulfing:
    def test_basic(self):
        # Need 3 bars: filler, prev (bearish), curr (bullish engulfing)
        bars = _make_bars([
            _bar(100, 101, 99, 100),     # filler
            _bar(105, 108, 102, 103),     # prev: open=105, close=103 (bearish)
            _bar(102, 110, 101, 106),     # curr: open=102, close=106 (bullish)
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "bullish_engulfing" for p in pats)

    def test_not_engulfing_small_body(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),     # filler
            _bar(105, 108, 102, 103),     # prev: bearish, body=2
            _bar(103.5, 104, 103, 103.8), # curr: tiny body=0.3
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert not any(p.pattern_type == "bullish_engulfing" for p in pats)

    def test_at_structure_gets_bonus(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(105, 108, 102, 103),
            _bar(102, 110, 101, 106),
        ])
        structures = [FakeStructure(100, 107)]
        det = CandlestickDetector()
        pats = det.detect(bars, 2, structures=structures)
        eng = [p for p in pats if p.pattern_type == "bullish_engulfing"]
        assert len(eng) == 1
        assert eng[0].at_structure is True
        assert eng[0].confluence_bonus > 5  # Base 5 + 3 structure


class TestBearishEngulfing:
    def test_basic(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),     # filler
            _bar(103, 108, 101, 106),     # prev: bullish
            _bar(107, 110, 100, 102),     # curr: bearish engulfing
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "bearish_engulfing" for p in pats)


class TestBullishPinBar:
    def test_hammer(self):
        # Small body at top, long lower wick
        bars = _make_bars([
            _bar(100, 101, 99, 100),      # filler
            _bar(100, 101, 99, 100),      # prev (normal bar)
            _bar(100, 100.5, 95, 100.2),   # hammer: body=0.2, range=5.5, wick ratio=0.04
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "bullish_pin_bar" for p in pats)

    def test_at_liquidity_gets_bonus(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 101, 99, 100),
            _bar(100, 100.5, 95, 100.2),
        ])
        pools = [FakePool(95.1)]  # Within 0.2% tolerance of low=95
        det = CandlestickDetector()
        pats = det.detect(bars, 2, liquidity_pools=pools)
        pins = [p for p in pats if p.pattern_type == "bullish_pin_bar"]
        assert len(pins) == 1
        assert pins[0].at_liquidity is True
        assert pins[0].confluence_bonus >= 8  # Base 6 + 2 liquidity


class TestBearishPinBar:
    def test_shooting_star(self):
        # Small body at bottom, long upper wick
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 101, 99, 100),
            _bar(100, 105, 99.8, 100.2),  # body=0.2, range=5.2, upper wick=4.8
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "bearish_pin_bar" for p in pats)


class TestDoji:
    def test_detects_doji(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 101, 99, 100),
            _bar(100, 105, 95, 100.01),  # body ≈ 0, range = 10
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "doji" for p in pats)

    def test_no_doji_on_large_body(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 101, 99, 100),
            _bar(100, 110, 99, 108),   # body = 8, range = 11 → 72%
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert not any(p.pattern_type == "doji" for p in pats)

    def test_doji_has_negative_bonus(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 101, 99, 100),
            _bar(100, 105, 95, 100.01),
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        dojis = [p for p in pats if p.pattern_type == "doji"]
        assert len(dojis) == 1
        assert dojis[0].confluence_bonus < 0  # Penalty


class TestInsideBar:
    def test_detects_inside_bar(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 110, 90, 105),   # prev: range 90-110
            _bar(101, 108, 92, 104),   # curr: inside prev (108<110, 92>90)
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert any(p.pattern_type == "inside_bar" for p in pats)

    def test_no_inside_bar_when_broken(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(100, 110, 90, 105),
            _bar(101, 112, 89, 104),   # breaks above 110
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        assert not any(p.pattern_type == "inside_bar" for p in pats)


class TestContextualization:
    def test_multiple_patterns_sorted_by_significance(self):
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(105, 108, 102, 103),  # prev: bearish
            _bar(102, 110, 101, 106),  # curr: bullish engulfing
        ])
        structures = [FakeStructure(100, 107)]
        det = CandlestickDetector()
        pats = det.detect(bars, 2, structures=structures)
        if len(pats) > 1:
            for i in range(len(pats) - 1):
                assert pats[i].significance >= pats[i + 1].significance

    def test_no_patterns_on_normal_bar(self):
        # Use clearly non-pattern bars: mixed direction, moderate bodies
        bars = _make_bars([
            _bar(100, 101, 99, 100),
            _bar(101, 105, 100, 103),   # prev: bullish, body=2, range=5
            _bar(99, 100, 97, 98),       # curr: bearish, body=1, range=3
        ])
        det = CandlestickDetector()
        pats = det.detect(bars, 2)
        # Should have no reversal patterns
        reversal = [p for p in pats if p.direction != "neutral"]
        assert len(reversal) == 0

    def test_insufficient_bars_returns_empty(self):
        bars = _make_bars([_bar(100, 105, 95, 100)])
        det = CandlestickDetector()
        assert det.detect(bars, 0) == []
        assert det.detect(bars, 1) == []  # idx >= len(bars)
