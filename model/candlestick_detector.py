"""
model/candlestick_detector.py — Candlestick pattern detection.

Detects patterns and contextualizes them relative to ICT structures
and liquidity levels. A pattern at a structure zone scores higher
than the same pattern in isolation.

Patterns detected:
  Reversal:  Engulfing, Pin Bar (Hammer/Shooting Star), Morning/Evening Star,
             Three White Soldiers / Three Black Crows
  Continuation: Three Bar Reversal, Inside Bar Breakout
  Indecision: Doji, Inside Bar (compression)

Usage:
    from model.candlestick_detector import CandlestickDetector

    detector = CandlestickDetector()
    patterns = detector.detect(bars_df, current_idx, structures, liquidity_pools)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from features.structures import StructureInstance, LiquidityPool


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class CandlestickPattern:
    """A detected candlestick pattern with context."""

    pattern_type: str       # e.g. "bullish_engulfing", "bearish_pin_bar"
    direction: str          # "bullish", "bearish", "neutral"
    bar_index: int
    significance: float     # 0-1

    # Context
    at_structure: bool      # Is this at an FVG/OB zone?
    at_liquidity: bool      # Is this at a liquidity level?

    # Scoring contribution
    confluence_bonus: float  # Points to add to confluence score

    def __repr__(self) -> str:
        ctx = []
        if self.at_structure:
            ctx.append("at_structure")
        if self.at_liquidity:
            ctx.append("at_liquidity")
        ctx_str = f" [{', '.join(ctx)}]" if ctx else ""
        return (
            f"Candle({self.pattern_type}, {self.direction}, "
            f"bonus={self.confluence_bonus:+.0f}{ctx_str})"
        )


# ---------------------------------------------------------------------------
# Detector
# ---------------------------------------------------------------------------

class CandlestickDetector:
    """
    Detects candlestick patterns and contextualizes them.

    Key insight: A pattern at a structure zone is worth MORE
    than a pattern in isolation.
    """

    def __init__(
        self,
        body_ratio_engulfing: float = 0.6,
        pin_wick_ratio: float = 0.3,
        pin_asymmetry: float = 2.0,
        doji_body_ratio: float = 0.1,
        structure_tolerance: float = 0.002,
        liquidity_tolerance: float = 0.002,
    ):
        # Engulfing: current body must cover this fraction of prev body
        self.body_ratio_engulfing = body_ratio_engulfing
        # Pin bar: body must be this fraction of total range or less
        self.pin_wick_ratio = pin_wick_ratio
        # Pin bar: dominant wick must be this × the other wick
        self.pin_asymmetry = pin_asymmetry
        # Doji: body must be this fraction of range or less
        self.doji_body_ratio = doji_body_ratio
        # How close price must be to structure zone (fraction)
        self.structure_tolerance = structure_tolerance
        # How close price must be to liquidity pool (fraction)
        self.liquidity_tolerance = liquidity_tolerance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect(
        self,
        bars: pd.DataFrame,
        current_idx: int,
        structures: list[StructureInstance] | None = None,
        liquidity_pools: list[LiquidityPool] | None = None,
    ) -> list[CandlestickPattern]:
        """
        Detect all patterns at the current bar.

        Parameters
        ----------
        bars : pd.DataFrame
            OHLCV with columns: open, high, low, close
        current_idx : int
            Index of the bar to analyze
        structures : list, optional
            Active structure instances for context
        liquidity_pools : list, optional
            Liquidity pools for context

        Returns
        -------
        list[CandlestickPattern]
            Detected patterns, sorted by significance
        """
        structures = structures or []
        liquidity_pools = liquidity_pools or []

        if current_idx < 2 or current_idx >= len(bars):
            return []

        patterns: list[CandlestickPattern] = []

        # Extract bar data
        curr = bars.iloc[current_idx]
        prev = bars.iloc[current_idx - 1]
        prev2 = bars.iloc[current_idx - 2]

        c_o = float(curr["open"])
        c_h = float(curr["high"])
        c_l = float(curr["low"])
        c_c = float(curr["close"])

        p_o = float(prev["open"])
        p_h = float(prev["high"])
        p_l = float(prev["low"])
        p_c = float(prev["close"])

        p2_o = float(prev2["open"])
        p2_h = float(prev2["high"])
        p2_l = float(prev2["low"])
        p2_c = float(prev2["close"])

        c_range = c_h - c_l
        p_range = p_h - p_l

        if c_range <= 0 or p_range <= 0:
            return []

        c_body = abs(c_c - c_o)
        p_body = abs(p_c - p_o)
        c_body_ratio = c_body / c_range
        p_body_ratio = p_body / p_range

        c_is_bullish = c_c > c_o
        c_is_bearish = c_c < c_o
        p_is_bullish = p_c > p_o
        p_is_bearish = p_c < p_o

        # --- Reversal Patterns ---

        # Bullish Engulfing
        p = self._bullish_engulfing(
            c_o, c_h, c_l, c_c, c_body, c_is_bullish,
            p_o, p_h, p_l, p_c, p_body, p_is_bearish,
            c_range, current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Bearish Engulfing
        p = self._bearish_engulfing(
            c_o, c_h, c_l, c_c, c_body, c_is_bearish,
            p_o, p_h, p_l, p_c, p_body, p_is_bullish,
            c_range, current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Bullish Pin Bar (Hammer)
        p = self._bullish_pin_bar(
            c_o, c_h, c_l, c_c, c_body, c_range, c_body_ratio,
            current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Bearish Pin Bar (Shooting Star)
        p = self._bearish_pin_bar(
            c_o, c_h, c_l, c_c, c_body, c_range, c_body_ratio,
            current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Morning Star (3-candle bullish reversal)
        p = self._morning_star(
            c_o, c_c, c_is_bullish, c_body,
            p_o, p_c, p_body, p_is_bearish,
            p2_o, p2_c, p2_body := abs(p2_c - p2_o), p2_is_bearish := p2_c < p2_o,
            c_range, current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Evening Star (3-candle bearish reversal)
        p = self._evening_star(
            c_o, c_c, c_is_bearish, c_body,
            p_o, p_c, p_body, p_is_bullish,
            p2_o, p2_c, p2_body, p2_is_bullish := p2_c > p2_o,
            c_range, current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Three White Soldiers
        p = self._three_white_soldiers(
            c_o, c_c, c_is_bullish, c_body,
            p_o, p_c, p_is_bullish, p_body,
            p2_o, p2_c, p2_is_bullish := p2_c > p2_o, p2_body,
            current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # Three Black Crows
        p = self._three_black_crows(
            c_o, c_c, c_is_bearish, c_body,
            p_o, p_c, p_is_bearish, p_body,
            p2_o, p2_c, p2_is_bearish := p2_c < p2_o, p2_body,
            current_idx, structures, liquidity_pools,
        )
        if p:
            patterns.append(p)

        # --- Indecision Patterns ---

        # Doji
        p = self._doji(c_body, c_range, current_idx)
        if p:
            patterns.append(p)

        # Inside Bar
        p = self._inside_bar(c_h, c_l, p_h, p_l, current_idx)
        if p:
            patterns.append(p)

        # Sort by significance descending
        patterns.sort(key=lambda pat: pat.significance, reverse=True)

        return patterns

    # ------------------------------------------------------------------
    # Reversal pattern detectors
    # ------------------------------------------------------------------

    def _bullish_engulfing(
        self, c_o, c_h, c_l, c_c, c_body, c_is_bullish,
        p_o, p_h, p_l, p_c, p_body, p_is_bearish,
        c_range, idx, structures, pools,
    ) -> CandlestickPattern | None:
        if not (p_is_bearish and c_is_bullish):
            return None
        # Current body engulfs previous body
        if not (c_c > p_o and c_o < p_c):
            return None
        # Body size check
        if c_body < p_body * self.body_ratio_engulfing:
            return None

        at_s = self._at_structure_zone(c_l, structures)
        at_l = self._at_liquidity(c_l, pools)
        bonus = self._compute_bonus(5.0, "engulfing", at_s, at_l)

        return CandlestickPattern(
            pattern_type="bullish_engulfing",
            direction="bullish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _bearish_engulfing(
        self, c_o, c_h, c_l, c_c, c_body, c_is_bearish,
        p_o, p_h, p_l, p_c, p_body, p_is_bullish,
        c_range, idx, structures, pools,
    ) -> CandlestickPattern | None:
        if not (p_is_bullish and c_is_bearish):
            return None
        if not (c_c < p_o and c_o > p_c):
            return None
        if c_body < p_body * self.body_ratio_engulfing:
            return None

        at_s = self._at_structure_zone(c_h, structures)
        at_l = self._at_liquidity(c_h, pools)
        bonus = self._compute_bonus(5.0, "engulfing", at_s, at_l)

        return CandlestickPattern(
            pattern_type="bearish_engulfing",
            direction="bearish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _bullish_pin_bar(
        self, c_o, c_h, c_l, c_c, c_body, c_range, c_body_ratio,
        idx, structures, pools,
    ) -> CandlestickPattern | None:
        if c_body_ratio > self.pin_wick_ratio:
            return None
        if c_range <= 0:
            return None

        upper_wick = c_h - max(c_o, c_c)
        lower_wick = min(c_o, c_c) - c_l

        # Hammer: long lower wick, small/no upper wick
        if lower_wick <= upper_wick * self.pin_asymmetry:
            return None
        if lower_wick <= 0:
            return None

        at_s = self._at_structure_zone(c_l, structures)
        at_l = self._at_liquidity(c_l, pools)
        bonus = self._compute_bonus(6.0, "pin_bar", at_s, at_l)

        return CandlestickPattern(
            pattern_type="bullish_pin_bar",
            direction="bullish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _bearish_pin_bar(
        self, c_o, c_h, c_l, c_c, c_body, c_range, c_body_ratio,
        idx, structures, pools,
    ) -> CandlestickPattern | None:
        if c_body_ratio > self.pin_wick_ratio:
            return None
        if c_range <= 0:
            return None

        upper_wick = c_h - max(c_o, c_c)
        lower_wick = min(c_o, c_c) - c_l

        # Shooting star: long upper wick, small/lower wick
        if upper_wick <= lower_wick * self.pin_asymmetry:
            return None
        if upper_wick <= 0:
            return None

        at_s = self._at_structure_zone(c_h, structures)
        at_l = self._at_liquidity(c_h, pools)
        bonus = self._compute_bonus(6.0, "pin_bar", at_s, at_l)

        return CandlestickPattern(
            pattern_type="bearish_pin_bar",
            direction="bearish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _morning_star(
        self, c_o, c_c, c_is_bullish, c_body,
        p_o, p_c, p_body, p_is_bearish,
        p2_o, p2_c, p2_body, p2_is_bearish,
        c_range, idx, structures, pools,
    ) -> CandlestickPattern | None:
        # 3-candle: big bearish → small body → big bullish
        if not (p2_is_bearish and p_is_bearish and c_is_bullish):
            return None
        # Middle bar has small body relative to neighbors
        avg_body = (p2_body + c_body) / 2
        if avg_body <= 0 or p_body > avg_body * 0.5:
            return None
        # Current closes above midpoint of first bar
        p2_mid = (p2_o + p2_c) / 2
        if c_c < p2_mid:
            return None

        zone_price = (c_l + c_o) / 2  # midpoint of current bar
        at_s = self._at_structure_zone(zone_price, structures)
        at_l = self._at_liquidity(zone_price, pools)
        bonus = self._compute_bonus(7.0, "star", at_s, at_l)

        return CandlestickPattern(
            pattern_type="morning_star",
            direction="bullish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _evening_star(
        self, c_o, c_c, c_is_bearish, c_body,
        p_o, p_c, p_body, p_is_bullish,
        p2_o, p2_c, p2_body, p2_is_bullish,
        c_range, idx, structures, pools,
    ) -> CandlestickPattern | None:
        if not (p2_is_bullish and p_is_bullish and c_is_bearish):
            return None
        avg_body = (p2_body + c_body) / 2
        if avg_body <= 0 or p_body > avg_body * 0.5:
            return None
        p2_mid = (p2_o + p2_c) / 2
        if c_c > p2_mid:
            return None

        zone_price = (c_h + c_o) / 2
        at_s = self._at_structure_zone(zone_price, structures)
        at_l = self._at_liquidity(zone_price, pools)
        bonus = self._compute_bonus(7.0, "star", at_s, at_l)

        return CandlestickPattern(
            pattern_type="evening_star",
            direction="bearish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _three_white_soldiers(
        self, c_o, c_c, c_is_bullish, c_body,
        p_o, p_c, p_is_bullish, p_body,
        p2_o, p2_c, p2_is_bullish, p2_body,
        idx, structures, pools,
    ) -> CandlestickPattern | None:
        if not (p2_is_bullish and p_is_bullish and c_is_bullish):
            return None
        # Each candle opens within prior body and closes higher
        if not (p_o > p2_o and c_o > p_o):
            return None
        if not (p_c > p2_c and c_c > p_c):
            return None
        # Bodies should be substantial
        if p2_body <= 0 or p_body <= 0 or c_body <= 0:
            return None

        at_s = self._at_structure_zone(c_c, structures)
        at_l = self._at_liquidity(c_c, pools)
        bonus = self._compute_bonus(6.0, "continuation", at_s, at_l)

        return CandlestickPattern(
            pattern_type="three_white_soldiers",
            direction="bullish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    def _three_black_crows(
        self, c_o, c_c, c_is_bearish, c_body,
        p_o, p_c, p_is_bearish, p_body,
        p2_o, p2_c, p2_is_bearish, p2_body,
        idx, structures, pools,
    ) -> CandlestickPattern | None:
        if not (p2_is_bearish and p_is_bearish and c_is_bearish):
            return None
        if not (p_o < p2_o and c_o < p_o):
            return None
        if not (p_c < p2_c and c_c < p_c):
            return None
        if p2_body <= 0 or p_body <= 0 or c_body <= 0:
            return None

        at_s = self._at_structure_zone(c_c, structures)
        at_l = self._at_liquidity(c_c, pools)
        bonus = self._compute_bonus(6.0, "continuation", at_s, at_l)

        return CandlestickPattern(
            pattern_type="three_black_crows",
            direction="bearish",
            bar_index=idx,
            significance=min(bonus / 10, 1.0),
            at_structure=at_s,
            at_liquidity=at_l,
            confluence_bonus=bonus,
        )

    # ------------------------------------------------------------------
    # Indecision pattern detectors
    # ------------------------------------------------------------------

    def _doji(self, c_body: float, c_range: float, idx: int) -> CandlestickPattern | None:
        if c_range <= 0:
            return None
        if c_body / c_range > self.doji_body_ratio:
            return None

        return CandlestickPattern(
            pattern_type="doji",
            direction="neutral",
            bar_index=idx,
            significance=0.4,
            at_structure=False,
            at_liquidity=False,
            confluence_bonus=-3.0,  # Penalty — indecision
        )

    def _inside_bar(
        self, c_h: float, c_l: float, p_h: float, p_l: float, idx: int,
    ) -> CandlestickPattern | None:
        if c_h >= p_h or c_l <= p_l:
            return None

        return CandlestickPattern(
            pattern_type="inside_bar",
            direction="neutral",
            bar_index=idx,
            significance=0.3,
            at_structure=False,
            at_liquidity=False,
            confluence_bonus=-2.0,  # Slight penalty — compressed
        )

    # ------------------------------------------------------------------
    # Context helpers
    # ------------------------------------------------------------------

    def _at_structure_zone(
        self, price: float, structures: list[StructureInstance],
    ) -> bool:
        """Check if price is near any structure zone."""
        tol = self.structure_tolerance
        for s in structures:
            zone_low = s.zone_low * (1 - tol)
            zone_high = s.zone_high * (1 + tol)
            if zone_low <= price <= zone_high:
                return True
        return False

    def _at_liquidity(
        self, price: float, pools: list[LiquidityPool],
    ) -> bool:
        """Check if price is near any liquidity pool."""
        tol = self.liquidity_tolerance
        for p in pools:
            if abs(p.price - price) / max(price, 1e-10) < tol:
                return True
        return False

    def _compute_bonus(
        self, base: float, pattern_kind: str,
        at_structure: bool, at_liquidity: bool,
    ) -> float:
        """Compute confluence bonus with context bonuses."""
        bonus = base
        if at_structure:
            bonus += 3.0
        if at_liquidity:
            bonus += 2.0
        return bonus
