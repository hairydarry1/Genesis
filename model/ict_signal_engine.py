"""
model/ict_signal_engine.py — ICT Signal Generation from Structure Detections.

Generates actionable trade signals from:
  - Fair Value Gaps (FVG) — entry at zone boundary
  - Order Blocks (OB) — entry at zone boundary
  - Liquidity Sweeps — entry after sweep confirmation
  - BOS/CHoCH — exit signals against position

Each signal includes:
  - Direction (+1 long, -1 short)
  - Entry price, stop loss, take profit
  - Triggering structure for context
  - Strength score

Usage:
    from model.ict_signal_engine import ICTSignalEngine

    engine = ICTSignalEngine()
    signals = engine.generate(state, narrative)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from features.structures import StructureInstance
    from model.market_narrative import MarketNarrative, LiquidityPool


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class TradeSignal:
    """A raw trading signal from the ICT engine."""
    source: str              # "ict_fvg", "ict_ob", "ict_sweep"
    direction: int           # +1 long, -1 short
    entry_price: float
    stop_loss: float
    take_profit: float
    strength: float          # 0-1
    reasoning: str
    triggering_structure: StructureInstance | None = None
    structure_type: str = ""  # "FVG", "OB", etc.

    def __repr__(self) -> str:
        side = "LONG" if self.direction == 1 else "SHORT"
        return (
            f"Signal({self.source}, {side}, "
            f"entry={self.entry_price:.2f}, "
            f"SL={self.stop_loss:.2f}, TP={self.take_profit:.2f}, "
            f"str={self.strength:.2f})"
        )


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class ICTSignalEngine:
    """
    Generates trade signals from ICT structure detections.

    Quality Filters:
      1. Structure confluence — multiple structures at same level = stronger
      2. Freshness — newer structures are more reliable
      3. Size — larger structures (ATR units) are more significant
      4. Zone depth — price at zone edge better than middle
      5. Narrative alignment — signal matches market bias
    """

    def __init__(
        self,
        rr_ratio: float = 2.0,
        max_sl_pips: float = 100.0,
        min_sl_pips: float = 20.0,
        require_confluence: bool = True,
    ):
        self.rr_ratio = rr_ratio
        self.max_sl_pips = max_sl_pips
        self.min_sl_pips = min_sl_pips
        self.require_confluence = require_confluence

    def _check_confluence(
        self,
        structure: StructureInstance,
        all_structures: list[StructureInstance],
        price: float,
    ) -> tuple[float, str]:
        """
        Check if other structures confirm this signal.
        Returns (bonus, description).
        """
        bonus = 0.0
        reasons = []
        zone_mid = (structure.zone_high + structure.zone_low) / 2
        zone_size = structure.zone_high - structure.zone_low

        # Find nearby structures (within 1.5x zone size)
        nearby = []
        for s in all_structures:
            if s is structure or s.age_bars > 30:
                continue
            s_mid = (s.zone_high + s.zone_low) / 2
            if abs(s_mid - zone_mid) < zone_size * 1.5:
                nearby.append(s)

        if nearby:
            # Count confirming structures (same direction)
            confirming = [s for s in nearby if s.direction == structure.direction]
            if confirming:
                bonus += min(len(confirming) * 0.05, 0.15)  # Up to 0.15 bonus
                reasons.append(f"{len(confirming)} confirming structures nearby")

            # Check for FVG + OB confluence (strongest)
            types_nearby = set(s.stype for s in nearby)
            if "FVG" in types_nearby and "OB" in types_nearby:
                bonus += 0.05
                reasons.append("FVG+OB confluence")

        return bonus, "; ".join(reasons) if reasons else "no confluence"

    def _compute_zone_depth(self, structure: StructureInstance, price: float) -> float:
        """
        Compute how deep into the zone price is (0=edge, 1=center).
        Edge entries are better — price is less likely to blow through.
        """
        zone_low = structure.zone_low
        zone_high = structure.zone_high
        zone_size = zone_high - zone_low

        if zone_size <= 0:
            return 0.5

        # Position within zone (0=bottom, 1=top)
        pos = (price - zone_low) / zone_size
        pos = max(0.0, min(1.0, pos))

        # For bullish: entry at top (pos=1) is good
        # For bearish: entry at bottom (pos=0) is good
        if structure.direction == "bullish":
            # Better when near top of zone
            depth = pos  # 1.0 = best (at top)
        else:
            # Better when near bottom of zone
            depth = 1.0 - pos  # 1.0 = best (at bottom)

        return depth

    def generate(
        self,
        structures: list[StructureInstance],
        current_price: float,
        position: int = 0,
        narrative_bias: str = "neutral",
        narrative_phase: str = "unknown",
        rolling_vol: float = 0.002,
    ) -> list[TradeSignal]:
        """
        Generate trade signals from current structures.
        """
        signals = []

        # Volatility filter: skip extreme vol
        if rolling_vol > 0.005:
            return signals  # Too volatile, no signals

        for s in structures:
            if s.age_bars > 30:
                continue

            # --- FVG Entry Signals ---
            if s.stype == "FVG" and not s.mitigated:
                sig = self._fvg_signal(s, current_price, narrative_bias, structures)
                if sig:
                    signals.append(sig)

            # --- Order Block Entry Signals ---
            elif s.stype == "OB" and not s.mitigated:
                sig = self._ob_signal(s, current_price, narrative_bias, structures)
                if sig:
                    signals.append(sig)

            # --- Liquidity Sweep Signals ---
            elif s.stype == "liquidity_sweep":
                sig = self._sweep_signal(s, current_price, narrative_bias)
                if sig:
                    signals.append(sig)

        # --- Exit Signals (if in position) ---
        if position != 0:
            exit_sigs = self._exit_signals(structures, current_price, position)
            signals.extend(exit_sigs)

        # Sort by strength
        signals.sort(key=lambda s: s.strength, reverse=True)

        # Apply quality filter: only keep top signals
        if self.require_confluence and len(signals) > 3:
            signals = signals[:3]  # Keep top 3

        return signals

    # ------------------------------------------------------------------
    # Signal generators
    # ------------------------------------------------------------------

    def _fvg_signal(
        self,
        fvg: StructureInstance,
        current_price: float,
        narrative_bias: str,
        all_structures: list[StructureInstance],
    ) -> TradeSignal | None:
        """Generate signal from Fair Value Gap with quality filters."""
        zone_low = fvg.zone_low
        zone_high = fvg.zone_high

        # Price must be at or inside the FVG zone
        if current_price < zone_low * 0.998 or current_price > zone_high * 1.002:
            return None

        if fvg.direction == "bullish":
            entry = zone_high
            sl = zone_low
            sl_distance = entry - sl
            tp = entry + sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            # Base strength
            strength = 0.5

            # 1. Narrative alignment (critical)
            if narrative_bias == "bullish":
                strength += 0.2
            elif narrative_bias == "bearish":
                strength -= 0.2  # Penalty for counter-trend

            # 2. Freshness (critical)
            if fvg.age_bars <= 5:
                strength += 0.15  # Very fresh
            elif fvg.age_bars <= 15:
                strength += 0.08  # Fresh
            elif fvg.age_bars <= 25:
                strength += 0.03  # Aging
            # >25: no bonus

            # 3. Structure size
            if fvg.size_ATR >= 0.8:
                strength += 0.1  # Large FVG
            elif fvg.size_ATR >= 0.5:
                strength += 0.05  # Medium FVG

            # 4. Zone depth (edge entries better)
            depth = self._compute_zone_depth(fvg, current_price)
            strength += depth * 0.05  # Up to 0.05 bonus

            # 5. Confluence check
            conf_bonus, conf_desc = self._check_confluence(fvg, all_structures, current_price)
            strength += conf_bonus

            # Minimum quality gate
            if strength < 0.6:
                return None

            return TradeSignal(
                source="ict_fvg",
                direction=1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bullish FVG [{zone_low:.0f}-{zone_high:.0f}] age={fvg.age_bars} {conf_desc}",
                triggering_structure=fvg,
                structure_type="FVG",
            )

        elif fvg.direction == "bearish":
            entry = zone_low
            sl = zone_high
            sl_distance = sl - entry
            tp = entry - sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            strength = 0.5

            if narrative_bias == "bearish":
                strength += 0.2
            elif narrative_bias == "bullish":
                strength -= 0.2

            if fvg.age_bars <= 5:
                strength += 0.15
            elif fvg.age_bars <= 15:
                strength += 0.08
            elif fvg.age_bars <= 25:
                strength += 0.03

            if fvg.size_ATR >= 0.8:
                strength += 0.1
            elif fvg.size_ATR >= 0.5:
                strength += 0.05

            depth = self._compute_zone_depth(fvg, current_price)
            strength += depth * 0.05

            conf_bonus, conf_desc = self._check_confluence(fvg, all_structures, current_price)
            strength += conf_bonus

            if strength < 0.6:
                return None

            return TradeSignal(
                source="ict_fvg",
                direction=-1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bearish FVG [{zone_low:.0f}-{zone_high:.0f}] age={fvg.age_bars} {conf_desc}",
                triggering_structure=fvg,
                structure_type="FVG",
            )

        return None

    def _ob_signal(
        self,
        ob: StructureInstance,
        current_price: float,
        narrative_bias: str,
        all_structures: list[StructureInstance],
    ) -> TradeSignal | None:
        """Generate signal from Order Block with quality filters."""
        zone_low = ob.zone_low
        zone_high = ob.zone_high

        if current_price < zone_low * 0.998 or current_price > zone_high * 1.002:
            return None

        if ob.direction == "bullish":
            entry = zone_high
            sl = zone_low
            sl_distance = entry - sl
            tp = entry + sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            strength = 0.55  # OB base (slightly higher than FVG)

            # 1. Narrative alignment
            if narrative_bias == "bullish":
                strength += 0.2
            elif narrative_bias == "bearish":
                strength -= 0.2

            # 2. Freshness
            if ob.age_bars <= 5:
                strength += 0.15
            elif ob.age_bars <= 15:
                strength += 0.08
            elif ob.age_bars <= 25:
                strength += 0.03

            # 3. Size
            if ob.size_ATR >= 0.8:
                strength += 0.1
            elif ob.size_ATR >= 0.5:
                strength += 0.05

            # 4. Zone depth
            depth = self._compute_zone_depth(ob, current_price)
            strength += depth * 0.05

            # 5. Confluence
            conf_bonus, conf_desc = self._check_confluence(ob, all_structures, current_price)
            strength += conf_bonus

            if strength < 0.6:
                return None

            return TradeSignal(
                source="ict_ob",
                direction=1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bullish OB [{zone_low:.0f}-{zone_high:.0f}] age={ob.age_bars} {conf_desc}",
                triggering_structure=ob,
                structure_type="OB",
            )

        elif ob.direction == "bearish":
            entry = zone_low
            sl = zone_high
            sl_distance = sl - entry
            tp = entry - sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            strength = 0.55

            if narrative_bias == "bearish":
                strength += 0.2
            elif narrative_bias == "bullish":
                strength -= 0.2

            if ob.age_bars <= 5:
                strength += 0.15
            elif ob.age_bars <= 15:
                strength += 0.08
            elif ob.age_bars <= 25:
                strength += 0.03

            if ob.size_ATR >= 0.8:
                strength += 0.1
            elif ob.size_ATR >= 0.5:
                strength += 0.05

            depth = self._compute_zone_depth(ob, current_price)
            strength += depth * 0.05

            conf_bonus, conf_desc = self._check_confluence(ob, all_structures, current_price)
            strength += conf_bonus

            if strength < 0.6:
                return None

            return TradeSignal(
                source="ict_ob",
                direction=-1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bearish OB [{zone_low:.0f}-{zone_high:.0f}] age={ob.age_bars} {conf_desc}",
                triggering_structure=ob,
                structure_type="OB",
            )

        return None

    def _sweep_signal(
        self,
        sweep: StructureInstance,
        current_price: float,
        narrative_bias: str,
    ) -> TradeSignal | None:
        """Generate signal from liquidity sweep."""
        if sweep.direction == "bullish":
            # Sweep below lows → long
            entry = current_price
            sl = sweep.zone_low
            sl_distance = entry - sl
            tp = entry + sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            strength = 0.7  # Sweeps are high-quality signals
            if narrative_bias == "bullish":
                strength += 0.1
            elif narrative_bias == "bearish":
                strength -= 0.1

            if sweep.age_bars <= 3:
                strength += 0.1  # Very fresh

            return TradeSignal(
                source="ict_sweep",
                direction=1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bullish sweep at {sweep.zone_low:.2f} — stops grabbed",
                triggering_structure=sweep,
                structure_type="liquidity_sweep",
            )

        elif sweep.direction == "bearish":
            # Sweep above highs → short
            entry = current_price
            sl = sweep.zone_high
            sl_distance = sl - entry
            tp = entry - sl_distance * self.rr_ratio

            if sl_distance < self.min_sl_pips or sl_distance > self.max_sl_pips:
                return None

            strength = 0.7
            if narrative_bias == "bearish":
                strength += 0.1
            elif narrative_bias == "bullish":
                strength -= 0.1

            if sweep.age_bars <= 3:
                strength += 0.1

            return TradeSignal(
                source="ict_sweep",
                direction=-1,
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                strength=max(0.1, min(1.0, strength)),
                reasoning=f"Bearish sweep at {sweep.zone_high:.2f} — stops grabbed",
                triggering_structure=sweep,
                structure_type="liquidity_sweep",
            )

        return None

    def _exit_signals(
        self,
        structures: list[StructureInstance],
        current_price: float,
        position: int,
    ) -> list[TradeSignal]:
        """Generate exit signals based on structure changes."""
        signals = []

        for s in structures:
            if s.age_bars > 10:
                continue

            # Long position + bearish BOS/CHoCH → exit
            if position == 1 and s.direction == "bearish" and s.stype in ("BOS", "CHoCH"):
                signals.append(TradeSignal(
                    source="ict_exit",
                    direction=0,  # Close
                    entry_price=current_price,
                    stop_loss=0,
                    take_profit=0,
                    strength=0.8,
                    reasoning=f"Bearish {s.stype} against long position",
                    triggering_structure=s,
                    structure_type=s.stype,
                ))

            # Short position + bullish BOS/CHoCH → exit
            elif position == -1 and s.direction == "bullish" and s.stype in ("BOS", "CHoCH"):
                signals.append(TradeSignal(
                    source="ict_exit",
                    direction=0,
                    entry_price=current_price,
                    stop_loss=0,
                    take_profit=0,
                    strength=0.8,
                    reasoning=f"Bullish {s.stype} against short position",
                    triggering_structure=s,
                    structure_type=s.stype,
                ))

        return signals
