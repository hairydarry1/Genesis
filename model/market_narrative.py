"""
model/market_narrative.py — Market Narrative Builder.

Continuously builds a story of what the market is doing:
  1. What just happened? (Event Detection)
  2. What phase are we in? (Accumulation/Markup/Distribution/Markdown)
  3. What's the structure? (Bullish/Bearish/Ranging/Broken)
  4. Where's the liquidity? (Buy-side/Sell-side pools)
  5. What's the narrative? (Human-readable story + actionable context)

Usage:
    from model.market_narrative import NarrativeBuilder

    builder = NarrativeBuilder()
    narrative = builder.update(structures, current_price, bar_idx, timestamp)
    print(narrative.story)
    print(f"Bias: {narrative.bias} ({narrative.bias_strength:.0%})")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from features.structures import StructureInstance


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EventType(Enum):
    """What just happened."""
    LIQUIDITY_SWEEP = "liquidity_sweep"
    BOS_BULLISH = "bos_bullish"
    BOS_BEARISH = "bos_bearish"
    CHOCH_BULLISH = "choch_bullish"
    CHOCH_BEARISH = "choch_bearish"
    FVG_CREATED = "fvg_created"
    FVG_MITIGATED = "fvg_mitigated"
    OB_CREATED = "ob_created"
    OB_MITIGATED = "ob_mitigated"
    EQH_FORMED = "eqh_formed"
    EQL_FORMED = "eql_formed"


class MarketPhase(Enum):
    """Wyckoff-style market phases."""
    ACCUMULATION = "accumulation"
    MARKUP = "markup"
    DISTRIBUTION = "distribution"
    MARKDOWN = "markdown"
    UNKNOWN = "unknown"


class StructureTrend(Enum):
    """Current structural trend."""
    UNKNOWN = "unknown"
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGING = "ranging"
    BROKEN = "broken"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MarketEvent:
    """A single structural event."""
    event_type: EventType
    timestamp: datetime
    price: float
    structure: StructureInstance | None = None
    description: str = ""
    significance: float = 0.5
    implication: str = "neutral"  # "bullish", "bearish", "neutral"
    age_bars: int = 0


@dataclass
class LiquidityPool:
    """A zone where stops are likely clustered."""
    price: float
    kind: str  # "buy_side" or "sell_side"
    strength: int = 1
    last_swept: datetime | None = None
    bars_since_swept: int = 0


@dataclass
class PhaseAnalysis:
    """Analysis of current market phase."""
    current_phase: MarketPhase = MarketPhase.UNKNOWN
    confidence: float = 0.2
    is_range_bound: bool = False
    range_high: float = 0.0
    range_low: float = 0.0
    phase_start_time: datetime | None = None
    bars_in_phase: int = 0
    expected_behavior: str = ""
    favor_trend_following: bool = False
    favor_mean_reversion: bool = False


@dataclass
class StructureAnalysis:
    """Analysis of current market structure."""
    trend: StructureTrend = StructureTrend.UNKNOWN
    confidence: float = 0.2
    last_swing_high: float = 0.0
    last_swing_low: float = 0.0
    prev_swing_high: float = 0.0
    prev_swing_low: float = 0.0
    structure_intact: bool = True
    last_bos_direction: str = ""
    last_choch_direction: str = ""
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0


@dataclass
class LiquidityMap:
    """Where liquidity sits in the market."""
    buy_side_pools: list[LiquidityPool] = field(default_factory=list)
    sell_side_pools: list[LiquidityPool] = field(default_factory=list)
    nearest_buy_side_distance: float = 0.0
    nearest_sell_side_distance: float = 0.0
    fresh_buy_side_count: int = 0
    fresh_sell_side_count: int = 0


@dataclass
class MarketNarrative:
    """The complete story of the market right now."""
    timestamp: datetime
    price: float
    recent_events: list[MarketEvent] = field(default_factory=list)
    phase: PhaseAnalysis = field(default_factory=PhaseAnalysis)
    structure: StructureAnalysis = field(default_factory=StructureAnalysis)
    liquidity: LiquidityMap = field(default_factory=LiquidityMap)
    story: str = ""
    bias: str = "neutral"
    bias_strength: float = 0.3
    key_levels: list[dict] = field(default_factory=list)
    watch_for: list[str] = field(default_factory=list)
    favorable_setups: list[str] = field(default_factory=list)
    unfavorable_setups: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class NarrativeBuilder:
    """
    Builds and maintains a continuous market narrative.

    Updates on every new bar, tracking:
    - Events (what just happened)
    - Phase (where are we in the cycle)
    - Structure (what's the trend)
    - Liquidity (where are the stops)
    """

    def __init__(self, lookback_events: int = 50):
        self.lookback_events = lookback_events
        self._events: list[MarketEvent] = []
        self._current_phase: MarketPhase = MarketPhase.UNKNOWN
        self._phase_start_bar: int = 0
        self._liquidity_pools: list[LiquidityPool] = []

    def update(
        self,
        structures: list[StructureInstance],
        current_price: float,
        bar_idx: int,
        timestamp: datetime,
    ) -> MarketNarrative:
        """
        Update narrative with new bar data.

        Parameters
        ----------
        structures : list[StructureInstance]
            All active structures up to current bar
        current_price : float
            Current bar's close price
        bar_idx : int
            Current bar index
        timestamp : datetime
            Current bar timestamp

        Returns
        -------
        MarketNarrative
            Updated narrative with story, bias, and insights
        """
        # 1. Detect new events
        new_events = self._detect_events(structures, current_price, bar_idx, timestamp)
        self._events.extend(new_events)
        self._events = self._events[-self.lookback_events:]

        # 2. Update liquidity mapping
        self._update_liquidity(structures, current_price)

        # 3. Analyze structure
        structure_analysis = self._analyze_structure(structures, current_price)

        # 4. Analyze phase
        phase_analysis = self._analyze_phase(structure_analysis, current_price, bar_idx)

        # 5. Build liquidity map
        liquidity_map = self._build_liquidity_map(current_price)

        # 6. Synthesize narrative
        story, bias, bias_strength = self._synthesize(
            new_events, phase_analysis, structure_analysis, liquidity_map, current_price
        )

        # 7. Generate insights
        key_levels, watch_for, favorable, unfavorable = self._generate_insights(
            phase_analysis, structure_analysis, liquidity_map, current_price
        )

        return MarketNarrative(
            timestamp=timestamp,
            price=current_price,
            recent_events=self._events[-20:],
            phase=phase_analysis,
            structure=structure_analysis,
            liquidity=liquidity_map,
            story=story,
            bias=bias,
            bias_strength=bias_strength,
            key_levels=key_levels,
            watch_for=watch_for,
            favorable_setups=favorable,
            unfavorable_setups=unfavorable,
        )

    # ------------------------------------------------------------------
    # Event detection
    # ------------------------------------------------------------------

    def _detect_events(
        self,
        structures: list[StructureInstance],
        current_price: float,
        bar_idx: int,
        timestamp: datetime,
    ) -> list[MarketEvent]:
        """Detect new structural events from current structures."""
        events = []

        for s in structures:
            # Only recent events matter
            if s.age_bars > 10:
                continue

            if s.stype == "BOS":
                evt_type = EventType.BOS_BULLISH if s.direction == "bullish" else EventType.BOS_BEARISH
                events.append(MarketEvent(
                    event_type=evt_type,
                    timestamp=timestamp,
                    price=current_price,
                    structure=s,
                    description=(
                        f"Bullish BOS: broke above {s.zone_low:.2f}"
                        if s.direction == "bullish"
                        else f"Bearish BOS: broke below {s.zone_high:.2f}"
                    ),
                    significance=0.8,
                    implication=s.direction,
                    age_bars=s.age_bars,
                ))

            elif s.stype == "CHoCH":
                evt_type = EventType.CHOCH_BULLISH if s.direction == "bullish" else EventType.CHOCH_BEARISH
                events.append(MarketEvent(
                    event_type=evt_type,
                    timestamp=timestamp,
                    price=current_price,
                    structure=s,
                    description=(
                        "Bullish CHoCH: trend reversal to bullish"
                        if s.direction == "bullish"
                        else "Bearish CHoCH: trend reversal to bearish"
                    ),
                    significance=0.9,
                    implication=s.direction,
                    age_bars=s.age_bars,
                ))

            elif s.stype == "liquidity_sweep":
                events.append(MarketEvent(
                    event_type=EventType.LIQUIDITY_SWEEP,
                    timestamp=timestamp,
                    price=current_price,
                    structure=s,
                    description=(
                        f"Bullish sweep at {s.zone_low:.2f} — stops grabbed below"
                        if s.direction == "bullish"
                        else f"Bearish sweep at {s.zone_high:.2f} — stops grabbed above"
                    ),
                    significance=0.85,
                    implication=s.direction,
                    age_bars=s.age_bars,
                ))

            elif s.stype == "FVG" and s.age_bars <= 3:
                events.append(MarketEvent(
                    event_type=EventType.FVG_CREATED,
                    timestamp=timestamp,
                    price=current_price,
                    structure=s,
                    description=f"{s.direction.capitalize()} FVG at [{s.zone_low:.2f}, {s.zone_high:.2f}]",
                    significance=0.6,
                    implication=s.direction,
                    age_bars=s.age_bars,
                ))

            elif s.stype == "OB" and s.age_bars <= 5:
                events.append(MarketEvent(
                    event_type=EventType.OB_CREATED,
                    timestamp=timestamp,
                    price=current_price,
                    structure=s,
                    description=f"{s.direction.capitalize()} OB at [{s.zone_low:.2f}, {s.zone_high:.2f}]",
                    significance=0.7,
                    implication=s.direction,
                    age_bars=s.age_bars,
                ))

        return events

    # ------------------------------------------------------------------
    # Structure analysis
    # ------------------------------------------------------------------

    def _analyze_structure(
        self,
        structures: list[StructureInstance],
        current_price: float,
    ) -> StructureAnalysis:
        """Analyze current market structure from detected structures."""
        bos_events = [s for s in structures if s.stype == "BOS" and s.age_bars < 100]
        choch_events = [s for s in structures if s.stype == "CHoCH" and s.age_bars < 100]

        if not bos_events and not choch_events:
            return StructureAnalysis(trend=StructureTrend.UNKNOWN, confidence=0.2)

        # Check for recent CHoCH (trend reversal)
        recent_choch = max(choch_events, key=lambda s: s.index) if choch_events else None
        if recent_choch and recent_choch.age_bars < 20:
            trend = StructureTrend.BULLISH if recent_choch.direction == "bullish" else StructureTrend.BEARISH
            return StructureAnalysis(
                trend=trend,
                confidence=0.8,
                structure_intact=True,
                last_choch_direction=recent_choch.direction,
                last_bos_direction=recent_choch.direction,
            )

        # Determine from BOS sequence
        bullish_count = sum(1 for s in bos_events if s.direction == "bullish")
        bearish_count = sum(1 for s in bos_events if s.direction == "bearish")

        if bullish_count > bearish_count * 1.5:
            trend = StructureTrend.BULLISH
            conf = min(bullish_count / max(bullish_count + bearish_count, 1), 0.9)
        elif bearish_count > bullish_count * 1.5:
            trend = StructureTrend.BEARISH
            conf = min(bearish_count / max(bullish_count + bearish_count, 1), 0.9)
        else:
            trend = StructureTrend.RANGING
            conf = 0.5

        last_bos = max(bos_events, key=lambda s: s.index)
        return StructureAnalysis(
            trend=trend,
            confidence=conf,
            last_bos_direction=last_bos.direction,
        )

    # ------------------------------------------------------------------
    # Phase analysis
    # ------------------------------------------------------------------

    def _analyze_phase(
        self,
        structure: StructureAnalysis,
        current_price: float,
        bar_idx: int,
    ) -> PhaseAnalysis:
        """Detect Wyckoff-style market phases."""

        if structure.trend == StructureTrend.BULLISH:
            if self._current_phase != MarketPhase.MARKUP:
                self._current_phase = MarketPhase.MARKUP
                self._phase_start_bar = bar_idx
            return PhaseAnalysis(
                current_phase=MarketPhase.MARKUP,
                confidence=structure.confidence * 0.8,
                bars_in_phase=bar_idx - self._phase_start_bar,
                expected_behavior="Trend continuation, buy dips",
                favor_trend_following=True,
                favor_mean_reversion=False,
            )

        elif structure.trend == StructureTrend.BEARISH:
            if self._current_phase != MarketPhase.MARKDOWN:
                self._current_phase = MarketPhase.MARKDOWN
                self._phase_start_bar = bar_idx
            return PhaseAnalysis(
                current_phase=MarketPhase.MARKDOWN,
                confidence=structure.confidence * 0.8,
                bars_in_phase=bar_idx - self._phase_start_bar,
                expected_behavior="Trend continuation, sell rallies",
                favor_trend_following=True,
                favor_mean_reversion=False,
            )

        elif structure.trend == StructureTrend.RANGING:
            recent_events = self._events[-10:]
            bullish_events = sum(1 for e in recent_events if e.implication == "bullish")
            bearish_events = sum(1 for e in recent_events if e.implication == "bearish")

            if bullish_events > bearish_events:
                phase = MarketPhase.ACCUMULATION
                expected = "Accumulation — expect breakout to upside"
            else:
                phase = MarketPhase.DISTRIBUTION
                expected = "Distribution — expect breakdown to downside"

            if self._current_phase != phase:
                self._current_phase = phase
                self._phase_start_bar = bar_idx

            return PhaseAnalysis(
                current_phase=phase,
                confidence=0.4,
                is_range_bound=True,
                bars_in_phase=bar_idx - self._phase_start_bar,
                expected_behavior=expected,
                favor_trend_following=False,
                favor_mean_reversion=True,
            )

        return PhaseAnalysis(current_phase=MarketPhase.UNKNOWN, confidence=0.2)

    # ------------------------------------------------------------------
    # Liquidity tracking
    # ------------------------------------------------------------------

    def _update_liquidity(
        self,
        structures: list[StructureInstance],
        current_price: float,
    ):
        """Track liquidity pools from structures."""
        for s in structures:
            if s.stype in ("BOS", "EQH", "EQL"):
                # Buy-side: pools above current price
                if s.zone_high > current_price:
                    self._add_or_update_pool(s.zone_high, "buy_side")
                # Sell-side: pools below current price
                if s.zone_low < current_price:
                    self._add_or_update_pool(s.zone_low, "sell_side")

        # Check if any pools were swept
        for pool in self._liquidity_pools:
            if pool.kind == "buy_side" and current_price > pool.price:
                pool.last_swept = datetime.now()
                pool.bars_since_swept = 0
            elif pool.kind == "sell_side" and current_price < pool.price:
                pool.last_swept = datetime.now()
                pool.bars_since_swept = 0
            else:
                pool.bars_since_swept += 1

    def _add_or_update_pool(self, price: float, kind: str, tolerance: float = 0.002):
        """Add or update a liquidity pool."""
        for pool in self._liquidity_pools:
            if pool.kind == kind and abs(pool.price - price) / max(price, 1e-10) < tolerance:
                pool.strength += 1
                return
        self._liquidity_pools.append(LiquidityPool(price=price, kind=kind))

    def _build_liquidity_map(self, current_price: float) -> LiquidityMap:
        """Build a map of where liquidity sits."""
        buy_side = sorted(
            [p for p in self._liquidity_pools if p.kind == "buy_side" and p.price > current_price],
            key=lambda p: p.price,
        )
        sell_side = sorted(
            [p for p in self._liquidity_pools if p.kind == "sell_side" and p.price < current_price],
            key=lambda p: p.price,
            reverse=True,
        )

        return LiquidityMap(
            buy_side_pools=buy_side[:5],
            sell_side_pools=sell_side[:5],
            nearest_buy_side_distance=(buy_side[0].price - current_price) / current_price if buy_side else 0,
            nearest_sell_side_distance=(current_price - sell_side[0].price) / current_price if sell_side else 0,
            fresh_buy_side_count=sum(1 for p in buy_side if not p.last_swept),
            fresh_sell_side_count=sum(1 for p in sell_side if not p.last_swept),
        )

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------

    def _synthesize(
        self,
        new_events: list[MarketEvent],
        phase: PhaseAnalysis,
        structure: StructureAnalysis,
        liquidity: LiquidityMap,
        current_price: float,
    ) -> tuple[str, str, float]:
        """Synthesize all analysis into a narrative story."""
        parts = []
        bias_parts = []

        # Recent events
        if new_events:
            parts.append("Recent activity:")
            for event in new_events[-3:]:
                parts.append(f"  • {event.description}")
                bias_parts.append(event.implication)

        # Phase
        parts.append(f"\nMarket phase: {phase.current_phase.value} ({phase.confidence:.0%} confidence)")
        parts.append(f"  Expected: {phase.expected_behavior}")

        # Structure
        parts.append(f"\nStructure: {structure.trend.value} ({structure.confidence:.0%} confidence)")
        if structure.last_choch_direction:
            parts.append(f"  Last CHoCH: {structure.last_choch_direction}")
        if structure.last_bos_direction:
            parts.append(f"  Last BOS: {structure.last_bos_direction}")

        # Liquidity
        parts.append(f"\nLiquidity:")
        parts.append(f"  Buy-side pools: {liquidity.fresh_buy_side_count} (nearest: {liquidity.nearest_buy_side_distance*100:.2f}%)")
        parts.append(f"  Sell-side pools: {liquidity.fresh_sell_side_count} (nearest: {liquidity.nearest_sell_side_distance*100:.2f}%)")

        # Determine bias
        if bias_parts:
            bullish_count = sum(1 for b in bias_parts if b == "bullish")
            bearish_count = sum(1 for b in bias_parts if b == "bearish")
            total = len(bias_parts)

            if bullish_count > bearish_count:
                bias, bias_strength = "bullish", bullish_count / total
            elif bearish_count > bullish_count:
                bias, bias_strength = "bearish", bearish_count / total
            else:
                bias, bias_strength = "neutral", 0.5
        else:
            bias, bias_strength = "neutral", 0.3

        return "\n".join(parts), bias, bias_strength

    def _generate_insights(
        self,
        phase: PhaseAnalysis,
        structure: StructureAnalysis,
        liquidity: LiquidityMap,
        current_price: float,
    ) -> tuple[list[dict], list[str], list[str], list[str]]:
        """Generate actionable trading insights."""
        key_levels = []
        watch_for = []
        favorable = []
        unfavorable = []

        if structure.last_swing_high > 0:
            key_levels.append({"price": structure.last_swing_high, "type": "resistance", "source": "swing_high"})
        if structure.last_swing_low > 0:
            key_levels.append({"price": structure.last_swing_low, "type": "support", "source": "swing_low"})

        if liquidity.buy_side_pools:
            key_levels.append({"price": liquidity.buy_side_pools[0].price, "type": "buy_side_liquidity"})
        if liquidity.sell_side_pools:
            key_levels.append({"price": liquidity.sell_side_pools[0].price, "type": "sell_side_liquidity"})

        if structure.trend == StructureTrend.BULLISH:
            watch_for.append(f"BOS continuation above {structure.last_swing_high:.2f}")
            watch_for.append(f"CHoCH reversal below {structure.last_swing_low:.2f}")
            favorable.append("Long at FVG/OB retest")
            favorable.append("Long at discount zone")
            unfavorable.append("Don't short against the trend")
        elif structure.trend == StructureTrend.BEARISH:
            watch_for.append(f"BOS continuation below {structure.last_swing_low:.2f}")
            watch_for.append(f"CHoCH reversal above {structure.last_swing_high:.2f}")
            favorable.append("Short at FVG/OB retest")
            favorable.append("Short at premium zone")
            unfavorable.append("Don't long against the trend")
        else:
            watch_for.append("BOS in either direction")
            favorable.append("Range trades at extremes")
            unfavorable.append("Avoid mid-range entries")

        return key_levels, watch_for, favorable, unfavorable
