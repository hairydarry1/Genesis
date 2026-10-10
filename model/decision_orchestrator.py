"""
model/decision_orchestrator.py — Central Decision Orchestrator.

Coordinates all trading modules into a single pipeline:
  1. State Assembly — build FullMarketState from raw data
  2. Narrative Update — track market story
  3. Exit Check — manage existing positions
  4. Signal Generation — ICT signals from structures
  5. Confluence Scoring — score and rank signals
  6. Risk Gate — final risk check
  7. Execution — open/modify/close positions

Usage:
    from model.decision_orchestrator import DecisionOrchestrator

    orch = DecisionOrchestrator(state_builder, risk_manager)
    decision = orch.on_bar(candle)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import numpy as np

from features.structures import StructureInstance, detect_structures
from model.candlestick_detector import CandlestickDetector, CandlestickPattern
from model.ict_signal_engine import ICTSignalEngine, TradeSignal
from model.market_narrative import NarrativeBuilder, MarketNarrative
from model.confluence_scorer import ConfluenceScorer, ScoredSignal, ScorerConfig


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class FullMarketState:
    """Complete market state at a point in time."""
    timestamp: datetime
    price: float
    bar: dict
    structures: list[StructureInstance]
    nearest_fvg: StructureInstance | None = None
    nearest_ob: StructureInstance | None = None
    recent_bos: StructureInstance | None = None
    recent_choch: StructureInstance | None = None
    rolling_vol: float = 0.002
    vol_regime: str = "normal"
    premium_discount_position: float = 0.5
    trend_15min: str = "flat"
    trend_1h: str = "flat"
    position: int = 0
    equity: float = 10000.0
    peak_equity: float = 10000.0
    entry_price: float | None = None
    bars_held: int = 0
    candlestick_patterns: list[CandlestickPattern] = field(default_factory=list)


@dataclass
class Decision:
    """Final trading decision."""
    action: str              # "open_long", "open_short", "close", "hold"
    direction: int           # +1, -1, 0
    size: float
    entry_price: float
    stop_loss: float
    take_profit: float
    confidence: float
    signal_source: str
    confluence_score: float
    reasoning: str
    risk_veto: bool = False


@dataclass
class ExitDecision:
    """Decision about an existing position."""
    should_exit: bool
    reason: str
    exit_type: str
    new_stop_loss: float | None = None
    new_take_profit: float | None = None


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class DecisionOrchestrator:
    """
    Central brain that orchestrates all trading decisions.

    Pipeline per bar:
      1. Assemble state
      2. Update narrative
      3. Check exits (if in position)
      4. Generate signals
      5. Score and rank
      6. Risk gate
      7. Execute
    """

    def __init__(
        self,
        risk_manager=None,
        min_confluence: float = 60.0,
        initial_equity: float = 10000.0,
        atr_period: int = 14,
        swing_lookback: int = 5,
    ):
        self.min_confluence = min_confluence
        self.initial_equity = initial_equity

        # Components
        self.narrative_builder = NarrativeBuilder()
        self.signal_engine = ICTSignalEngine()
        self.scorer = ConfluenceScorer(ScorerConfig(min_confluence=min_confluence))
        self.candlestick_detector = CandlestickDetector()
        self.risk_manager = risk_manager

        # Config
        self.atr_period = atr_period
        self.swing_lookback = swing_lookback

        # Trailing stop config
        self.trailing_activation_pct = 0.0015  # Activate after 0.15% profit
        self.trailing_distance_pct = 0.001    # Trail 0.1% behind best
        self.trailing_activated = False
        self.best_price = 0.0  # Best price since entry (highest for long, lowest for short)
        # Take profit / stop loss (base values, scaled by ATR)
        self.tp_pct = 0.008    # Base take profit at 0.8%
        self.sl_pct = 0.002    # Base stop loss at 0.2%
        self.min_hold_bars = 3  # Minimum bars before any exit allowed
        # Volatility-adjusted exit scaling
        self.use_atr_exits = True          # Scale TP/SL by ATR
        self.atr_tp_multiplier = 1.5      # TP = base * (atr / normal_vol)
        self.atr_sl_multiplier = 1.0      # SL = base * (atr / normal_vol)
        self.atr_normal_vol = 0.002       # Reference vol for scaling
        # Time-based exit
        self.losing_exit_bars = 20        # Exit losing trades after 20 bars
        self.flat_exit_bars = 40          # Exit flat/breakeven trades after 40 bars
        # BOS-based dynamic stop
        self.sl_to_breakeven = True       # Move SL to breakeven after favorable BOS
        self.sl_breakeven_activated = False
        # Entry quality tracking
        self.entry_confluence = 0.0       # Confluence score at entry
        self.entry_exit_bars = 15         # Low-confidence exits after 15 bars

        # Portfolio state
        self.position = 0
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.entry_price = 0.0
        self.bars_held = 0
        self.trade_log: list[dict] = []

        # Candle buffer for structure detection
        self._candles: list[dict] = []
        # Structure caching
        self._cached_structures: list[StructureInstance] = []
        self._structure_cache_bar: int = 0
        self._structure_recalc_interval: int = 20  # Recalc every N bars

    def on_bar(
        self,
        candle: dict,
        features: dict[str, float] | None = None,
        trend_15min: str = "flat",
        trend_1h: str = "flat",
    ) -> Decision | ExitDecision | None:
        """
        Process a new bar through the full pipeline.

        Parameters
        ----------
        candle : dict
            OHLCV bar with keys: epoch/open/high/low/close/volume
        features : dict, optional
            Computed features for WM agreement scoring
        trend_15min, trend_1h : str
            Trend directions from multi-TF analysis

        Returns
        -------
        Decision | ExitDecision | None
        """
        features = features or {}

        # Add candle to buffer
        self._candles.append(candle)
        if len(self._candles) > 2000:
            self._candles = self._candles[-2000:]

        # --- Stage 1: Assemble State ---
        state = self._assemble_state(
            candle, features, trend_15min, trend_1h
        )

        # --- Stage 2: Update Narrative ---
        narrative = self.narrative_builder.update(
            structures=state.structures,
            current_price=state.price,
            bar_idx=len(self._candles) - 1,
            timestamp=state.timestamp,
        )

        # --- Stage 3: Exit Check (if in position) ---
        if self.position != 0:
            self.bars_held += 1
            exit_decision = self._check_exit(state, narrative)
            if exit_decision.should_exit:
                self._execute_exit(exit_decision, state, narrative)
                return exit_decision

        # --- Stage 4-6: Signal Generation → Scoring → Risk Gate ---
        decision = self._generate_and_score(state, narrative, features)

        # --- Stage 7: Execute ---
        if decision and decision.action != "hold":
            self._execute_entry(decision, state, narrative)

        return decision

    # ------------------------------------------------------------------
    # Stage 1: State Assembly
    # ------------------------------------------------------------------

    def _assemble_state(
        self,
        candle: dict,
        features: dict[str, float],
        trend_15min: str,
        trend_1h: str,
    ) -> FullMarketState:
        """Build FullMarketState from raw data."""
        timestamp = datetime.utcfromtimestamp(candle.get("epoch", 0))
        price = float(candle.get("close", 0))

        # Detect structures (with caching for speed)
        import pandas as pd
        from datetime import datetime as _dt
        n_candles = len(self._candles)
        structures: list[StructureInstance] = self._cached_structures

        # Only re-detect every N bars or if cache is empty
        if (n_candles - self._structure_cache_bar >= self._structure_recalc_interval
                or not self._cached_structures):
            if n_candles >= 30:
                bars_df = pd.DataFrame(self._candles[-500:])  # Use last 500 bars max
                # Add timestamp column from epoch if missing
                if "timestamp" not in bars_df.columns:
                    bars_df["timestamp"] = pd.to_datetime(
                        bars_df["epoch"], unit="s", utc=True
                    ).dt.tz_localize(None)
                try:
                    new_structures = detect_structures(
                        bars_df,
                        atr_period=self.atr_period,
                        swing_lookback=self.swing_lookback,
                        current_index=len(bars_df) - 1,
                    )
                    # Adjust indices to global
                    offset = n_candles - len(bars_df)
                    for s in new_structures:
                        s.index += offset
                    structures = new_structures
                    self._cached_structures = structures
                    self._structure_cache_bar = n_candles
                except Exception:
                    structures = self._cached_structures

        # Find nearest structures
        nearest_fvg = self._find_nearest(structures, price, "FVG")
        nearest_ob = self._find_nearest(structures, price, "OB")
        recent_bos = self._find_most_recent(structures, "BOS", max_age=50)
        recent_choch = self._find_most_recent(structures, "CHoCH", max_age=50)

        # Rolling volatility
        rolling_vol = self._compute_rolling_vol()

        # Premium/discount position
        pd_position = self._compute_pd_position(structures, price)

        # Candlestick patterns
        patterns = []
        if len(self._candles) >= 3:
            bars_list = [self._candles[i] for i in range(max(0, len(self._candles)-200), len(self._candles))]
            bars_small = pd.DataFrame(bars_list)
            try:
                patterns = self.candlestick_detector.detect(
                    bars_small, len(bars_small) - 1, structures
                )
            except Exception:
                patterns = []

        return FullMarketState(
            timestamp=timestamp,
            price=price,
            bar=candle,
            structures=structures,
            nearest_fvg=nearest_fvg,
            nearest_ob=nearest_ob,
            recent_bos=recent_bos,
            recent_choch=recent_choch,
            rolling_vol=rolling_vol,
            vol_regime=self._classify_vol(rolling_vol),
            premium_discount_position=pd_position,
            trend_15min=trend_15min,
            trend_1h=trend_1h,
            position=self.position,
            equity=self.equity,
            peak_equity=self.peak_equity,
            entry_price=self.entry_price if self.position != 0 else None,
            bars_held=self.bars_held,
            candlestick_patterns=patterns,
        )

    # ------------------------------------------------------------------
    # Stage 3: Exit Check
    # ------------------------------------------------------------------

    def _compute_atr(self, period: int = 14) -> float:
        """Compute Average True Range from recent candles."""
        if len(self._candles) < period + 1:
            return self.atr_normal_vol * self.entry_price if self.entry_price else 0.0

        trs = []
        candles = self._candles[-period - 1:]
        for i in range(1, len(candles)):
            h = candles[i]["high"]
            l = candles[i]["low"]
            pc = candles[i - 1]["close"]
            tr = max(h - l, abs(h - pc), abs(l - pc))
            trs.append(tr)
        return float(np.mean(trs)) if trs else 0.0

    def _vol_adjusted_tp(self) -> float:
        """Compute volatility-adjusted take profit."""
        if not self.use_atr_exits or self.entry_price == 0:
            return self.tp_pct
        atr = self._compute_atr()
        vol_ratio = max(0.5, min(3.0, atr / (self.atr_normal_vol * self.entry_price)))
        return self.tp_pct * (1.0 + (vol_ratio - 1.0) * self.atr_tp_multiplier * 0.3)

    def _vol_adjusted_sl(self) -> float:
        """Compute volatility-adjusted stop loss."""
        if not self.use_atr_exits or self.entry_price == 0:
            return self.sl_pct
        atr = self._compute_atr()
        vol_ratio = max(0.5, min(3.0, atr / (self.atr_normal_vol * self.entry_price)))
        return self.sl_pct * max(0.5, vol_ratio * self.atr_sl_multiplier)

    def _check_exit(self, state: FullMarketState, narrative: MarketNarrative) -> ExitDecision:
        """Check if position should be closed."""
        # Minimum hold period — don't exit too early
        if self.bars_held < self.min_hold_bars:
            return ExitDecision(should_exit=False, reason="", exit_type="none")

        # Update best price for trailing stop
        if self.position == 1:  # Long
            if state.price > self.best_price:
                self.best_price = state.price
        elif self.position == -1:  # Short
            if self.best_price == 0 or state.price < self.best_price:
                self.best_price = state.price

        # Check trailing stop activation
        if self.position == 1:
            profit_pct = (self.best_price - self.entry_price) / self.entry_price
            if profit_pct >= self.trailing_activation_pct:
                self.trailing_activated = True
        elif self.position == -1:
            profit_pct = (self.entry_price - self.best_price) / self.entry_price
            if profit_pct >= self.trailing_activation_pct:
                self.trailing_activated = True

        # 1. Trailing stop (highest priority when activated)
        if self.trailing_activated:
            if self.position == 1:
                trailing_stop = self.best_price * (1 - self.trailing_distance_pct)
                if state.price <= trailing_stop:
                    return ExitDecision(
                        should_exit=True,
                        reason=f"Trailing stop hit (best={self.best_price:.2f}, stop={trailing_stop:.2f})",
                        exit_type="trailing_stop",
                    )
            elif self.position == -1:
                trailing_stop = self.best_price * (1 + self.trailing_distance_pct)
                if state.price >= trailing_stop:
                    return ExitDecision(
                        should_exit=True,
                        reason=f"Trailing stop hit (best={self.best_price:.2f}, stop={trailing_stop:.2f})",
                        exit_type="trailing_stop",
                    )

        # 2. Structure-based exit (BOS/CHoCH against position)
        if self.position == 1 and state.recent_choch:
            if state.recent_choch.direction == "bearish" and state.recent_choch.age_bars <= 10:
                return ExitDecision(
                    should_exit=True,
                    reason="Bearish CHoCH against long position",
                    exit_type="structure_breach",
                )

        if self.position == -1 and state.recent_choch:
            if state.recent_choch.direction == "bullish" and state.recent_choch.age_bars <= 10:
                return ExitDecision(
                    should_exit=True,
                    reason="Bullish CHoCH against short position",
                    exit_type="structure_breach",
                )

        if self.position == 1 and state.recent_bos:
            if state.recent_bos.direction == "bearish" and state.recent_bos.age_bars <= 5:
                return ExitDecision(
                    should_exit=True,
                    reason="Bearish BOS against long position",
                    exit_type="structure_breach",
                )

        if self.position == -1 and state.recent_bos:
            if state.recent_bos.direction == "bullish" and state.recent_bos.age_bars <= 5:
                return ExitDecision(
                    should_exit=True,
                    reason="Bullish BOS against short position",
                    exit_type="structure_breach",
                )

        # 3. BOS-based dynamic stop — move SL to breakeven after favorable structure
        #    Only activates when trade is already in profit (reduces false exits)
        if self.sl_to_breakeven and not self.sl_breakeven_activated:
            favorable_bos = False
            if self.position == 1 and state.recent_bos:
                if state.recent_bos.direction == "bullish" and state.recent_bos.age_bars <= 8:
                    favorable_bos = True
            elif self.position == -1 and state.recent_bos:
                if state.recent_bos.direction == "bearish" and state.recent_bos.age_bars <= 8:
                    favorable_bos = True
            # Only activate if trade is already in profit (0.15%+)
            current_pnl = self._current_pnl_pct(state)
            if favorable_bos and current_pnl > 0.0015:
                self.sl_breakeven_activated = True
                # Move SL to breakeven + small buffer (0.05%)
                if self.position == 1:
                    new_sl = self.entry_price * 1.0005
                    if state.price <= new_sl:
                        return ExitDecision(
                            should_exit=True,
                            reason=f"Breakeven stop hit after favorable BOS (pnl={current_pnl*100:.2f}%)",
                            exit_type="breakeven_stop",
                        )
                elif self.position == -1:
                    new_sl = self.entry_price * 0.9995
                    if state.price >= new_sl:
                        return ExitDecision(
                            should_exit=True,
                            reason=f"Breakeven stop hit after favorable BOS (pnl={current_pnl*100:.2f}%)",
                            exit_type="breakeven_stop",
                        )

        # 4. Max hold period
        if self.bars_held >= 60:
            return ExitDecision(
                should_exit=True,
                reason=f"Max hold period ({self.bars_held} bars)",
                exit_type="max_hold",
            )

        # 5. Time-based exits
        current_pnl_pct = self._current_pnl_pct(state)
        # Exit losing trades after N bars
        if self.bars_held >= self.losing_exit_bars and current_pnl_pct < 0:
            return ExitDecision(
                should_exit=True,
                reason=f"Time exit: losing after {self.bars_held} bars (pnl={current_pnl_pct*100:.2f}%)",
                exit_type="time_exit",
            )
        # Exit flat trades after N bars (no momentum)
        if self.bars_held >= self.flat_exit_bars and abs(current_pnl_pct) < 0.001:
            return ExitDecision(
                should_exit=True,
                reason=f"Time exit: flat after {self.bars_held} bars",
                exit_type="time_exit",
            )

        # 6. Low-confidence early exit
        if self.entry_confluence < 70 and self.bars_held >= self.entry_exit_bars:
            if current_pnl_pct <= 0:
                return ExitDecision(
                    should_exit=True,
                    reason=f"Low-confidence exit (score={self.entry_confluence:.0f}) after {self.bars_held} bars",
                    exit_type="confidence_exit",
                )

        # 7. Volatility-adjusted take profit
        vol_tp = self._vol_adjusted_tp()
        if self.position == 1 and state.price >= self.entry_price * (1 + vol_tp):
            return ExitDecision(
                should_exit=True,
                reason=f"Take profit hit ({vol_tp*100:.2f}%)",
                exit_type="take_profit",
            )
        if self.position == -1 and state.price <= self.entry_price * (1 - vol_tp):
            return ExitDecision(
                should_exit=True,
                reason=f"Take profit hit ({vol_tp*100:.2f}%)",
                exit_type="take_profit",
            )

        # 8. Volatility-adjusted stop loss
        vol_sl = self._vol_adjusted_sl()
        if self.position == 1 and state.price <= self.entry_price * (1 - vol_sl):
            return ExitDecision(
                should_exit=True,
                reason=f"Stop loss hit ({vol_sl*100:.2f}%)",
                exit_type="stop_loss",
            )
        if self.position == -1 and state.price >= self.entry_price * (1 + vol_sl):
            return ExitDecision(
                should_exit=True,
                reason=f"Stop loss hit ({vol_sl*100:.2f}%)",
                exit_type="stop_loss",
            )

        return ExitDecision(should_exit=False, reason="", exit_type="none")

    # ------------------------------------------------------------------
    # Stages 4-6: Signal → Score → Risk Gate
    # ------------------------------------------------------------------

    def _generate_and_score(
        self,
        state: FullMarketState,
        narrative: MarketNarrative,
        features: dict[str, float],
    ) -> Decision | None:
        """Generate signals, score, and apply risk gate."""
        # Generate signals
        signals = self.signal_engine.generate(
            structures=state.structures,
            current_price=state.price,
            position=self.position,
            narrative_bias=narrative.bias,
            narrative_phase=narrative.phase.current_phase.value,
        )

        if not signals:
            return None

        # Score each signal
        scored: list[ScoredSignal] = []
        for sig in signals:
            s = self.scorer.score(
                signal=sig,
                structures=state.structures,
                features=features,
                narrative=narrative,
                patterns=state.candlestick_patterns,
                trend_15min=state.trend_15min,
                trend_1h=state.trend_1h,
                premium_discount_position=state.premium_discount_position,
            )
            scored.append(s)

        # Sort by score
        scored.sort(key=lambda s: s.total_score, reverse=True)

        best = scored[0]

        # Risk gate
        if not best.passed:
            return Decision(
                action="hold",
                direction=0,
                size=0,
                entry_price=state.price,
                stop_loss=0,
                take_profit=0,
                confidence=0,
                signal_source="none",
                confluence_score=best.total_score,
                reasoning=f"Rejected: {best.rejection_reason}",
            )

        # Don't open if already in position
        if self.position != 0 and best.signal.direction == self.position:
            return Decision(
                action="hold",
                direction=0,
                size=0,
                entry_price=state.price,
                stop_loss=0,
                take_profit=0,
                confidence=0,
                signal_source=best.signal.source,
                confluence_score=best.total_score,
                reasoning="Already in position",
            )

        action = "open_long" if best.signal.direction == 1 else "open_short"

        return Decision(
            action=action,
            direction=best.signal.direction,
            size=best.suggested_size,
            entry_price=best.signal.entry_price,
            stop_loss=best.signal.stop_loss,
            take_profit=best.signal.take_profit,
            confidence=best.total_score / 100,
            signal_source=best.signal.source,
            confluence_score=best.total_score,
            reasoning=best.signal.reasoning,
        )

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _execute_entry(self, decision: Decision, state: FullMarketState, narrative: MarketNarrative):
        """Execute a new position."""
        self.position = decision.direction
        self.entry_price = state.price
        self.bars_held = 0
        self.best_price = state.price
        self.trailing_activated = False
        self.sl_breakeven_activated = False
        self.entry_confluence = decision.confluence_score

        self.trade_log.append({
            "timestamp": state.timestamp.isoformat(),
            "action": "open",
            "side": "long" if decision.direction == 1 else "short",
            "entry_price": state.price,
            "stop_loss": decision.stop_loss,
            "take_profit": decision.take_profit,
            "size": decision.size,
            "confluence": decision.confluence_score,
            "source": decision.signal_source,
            "reasoning": decision.reasoning,
            "narrative_phase": narrative.phase.current_phase.value,
            "narrative_bias": narrative.bias,
        })

    def _execute_exit(self, exit_decision: ExitDecision, state: FullMarketState, narrative: MarketNarrative):
        """Execute closing a position."""
        pnl = (state.price - self.entry_price) * self.position

        # Record trailing stop info in trade log
        trail_info = ""
        if self.trailing_activated:
            if self.position == 1:
                trail_info = f"trailing_stop_at={self.best_price * (1 - self.trailing_distance_pct):.2f}"
            else:
                trail_info = f"trailing_stop_at={self.best_price * (1 + self.trailing_distance_pct):.2f}"

        self.trade_log.append({
            "timestamp": state.timestamp.isoformat(),
            "action": "close",
            "side": "long" if self.position == 1 else "short",
            "entry_price": self.entry_price,
            "exit_price": state.price,
            "pnl": pnl,
            "exit_reason": exit_decision.reason,
            "exit_type": exit_decision.exit_type,
            "bars_held": self.bars_held,
        })

        self.equity += pnl
        self.peak_equity = max(self.peak_equity, self.equity)
        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_nearest(self, structures: list, price: float, stype: str) -> StructureInstance | None:
        """Find nearest structure of given type."""
        candidates = [s for s in structures if s.stype == stype and not s.mitigated]
        if not candidates:
            return None
        return min(candidates, key=lambda s: abs((s.zone_high + s.zone_low) / 2 - price))

    def _find_most_recent(self, structures: list, stype: str, max_age: int = 50) -> StructureInstance | None:
        """Find most recent structure of given type."""
        candidates = [s for s in structures if s.stype == stype and s.age_bars <= max_age]
        if not candidates:
            return None
        return max(candidates, key=lambda s: s.index)

    def _compute_rolling_vol(self, window: int = 100) -> float:
        """Compute rolling volatility from recent candles."""
        if len(self._candles) < window + 1:
            return 0.002
        closes = np.array([c["close"] for c in self._candles[-window-1:]])
        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        return float(np.std(returns)) + 1e-10

    def _current_pnl_pct(self, state: FullMarketState) -> float:
        """Current unrealized PnL as a percentage."""
        if self.entry_price == 0 or self.position == 0:
            return 0.0
        return (state.price - self.entry_price) / self.entry_price * self.position

    def _classify_vol(self, vol: float) -> str:
        if vol < 0.001:
            return "calm"
        elif vol < 0.003:
            return "normal"
        else:
            return "volatile"

    def _compute_pd_position(self, structures: list, price: float) -> float:
        """Compute premium/discount position (0=discount, 1=premium)."""
        swing_highs = [s for s in structures if s.stype in ("BOS", "EQH") and s.zone_high > price]
        swing_lows = [s for s in structures if s.stype in ("BOS", "EQL") and s.zone_low < price]

        if not swing_highs or not swing_lows:
            return 0.5

        high = min(s.zone_high for s in swing_highs)
        low = max(s.zone_low for s in swing_lows)
        rng = high - low

        if rng <= 0:
            return 0.5

        return max(0.0, min(1.0, (price - low) / rng))

    def get_trade_log(self) -> list[dict]:
        """Get the trade log."""
        return list(self.trade_log)

    def get_summary(self) -> dict:
        """Get performance summary."""
        if not self.trade_log:
            return {"trades": 0, "pnl": 0, "win_rate": 0}

        closes = [t for t in self.trade_log if t["action"] == "close"]
        if not closes:
            return {"trades": 0, "pnl": 0, "win_rate": 0}

        pnls = [t["pnl"] for t in closes]
        wins = sum(1 for p in pnls if p > 0)

        return {
            "trades": len(closes),
            "pnl": sum(pnls),
            "win_rate": wins / len(closes) if closes else 0,
            "equity": self.equity,
            "max_equity": self.peak_equity,
        }
