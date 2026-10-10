"""
model/decision_fusion.py — Fuses signals from World Model, LLM, and PPO policy.

The fusion layer combines:
  1. World Model: statistical prediction (returns + uncertainty)
  2. LLM Strategy Agent: qualitative reasoning (regime + risk)
  3. PPO Policy: learned buy/sell/hold from RL training

Each signal carries a confidence weight. The fusion layer produces
a single, calibrated trading decision.

Usage:
    fusion = DecisionFusion(config)
    decision = fusion.fuse(wm_prediction, llm_decision, ppo_action, market_state)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from model.strategy_agent import LLMDecision, MarketContext
from model.risk_management import RiskManager, RiskConfig

logger = logging.getLogger(__name__)


@dataclass
class WMPrediction:
    """World model prediction summary."""
    mean_returns: np.ndarray  # shape (horizon,)
    std_returns: np.ndarray   # shape (horizon,)
    snr: float = 0.0          # signal-to-noise ratio
    cumulative_mean: float = 0.0
    cumulative_std: float = 0.0


@dataclass
class FusedDecision:
    """Final trading decision after signal fusion."""
    action: str = "hold"          # buy/sell/hold
    size: float = 0.0             # position size (fraction of equity)
    stop_loss: float = 0.0        # stop loss price
    take_profit: float = 0.0      # take profit price
    confidence: float = 0.0       # overall confidence 0-1

    # Component contributions
    wm_contribution: float = 0.0
    llm_contribution: float = 0.0
    ppo_contribution: float = 0.0
    risk_adjustment: float = 1.0

    # Metadata
    reasoning: str = ""
    risk_notes: str = ""
    regime: str = "unknown"

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "size": self.size,
            "stop_loss": self.stop_loss,
            "take_profit": self.take_profit,
            "confidence": self.confidence,
            "wm_contribution": self.wm_contribution,
            "llm_contribution": self.llm_contribution,
            "ppo_contribution": self.ppo_contribution,
            "risk_adjustment": self.risk_adjustment,
            "reasoning": self.reasoning,
            "risk_notes": self.risk_notes,
            "regime": self.regime,
        }


@dataclass
class FusionConfig:
    """Configuration for signal fusion weights."""
    # Signal weights (normalized internally)
    wm_weight: float = 0.4       # World model contribution
    llm_weight: float = 0.35     # LLM reasoning contribution
    ppo_weight: float = 0.25     # PPO policy contribution

    # Minimum confidence thresholds
    min_confidence_to_trade: float = 0.2
    min_snr_to_trade: float = 0.15

    # Size limits
    max_position_size: float = 1.5  # max fraction of equity
    base_position_size: float = 0.5  # base position when all signals agree

    # Agreement bonus: when signals agree, boost confidence
    agreement_bonus: float = 0.15

    # Disagreement penalty: when signals disagree, reduce size
    disagreement_penalty: float = 0.5

    # LLM override: LLM can veto trades even if WM + PPO agree
    llm_veto_enabled: bool = True

    # Regime multipliers
    regime_multipliers: dict = field(default_factory=lambda: {
        "calm": 1.0,
        "choppy": 0.7,
        "spike": 0.4,
    })


class DecisionFusion:
    """
    Fuses signals from multiple sources into a single trading decision.

    The fusion process:
    1. Extract directional signal from each source
    2. Compute agreement/disagreement
    3. Apply risk management adjustments
    4. Produce final decision with size, stops, and reasoning
    """

    def __init__(
        self,
        config: FusionConfig | None = None,
        risk_manager: RiskManager | None = None,
    ):
        self.cfg = config or FusionConfig()
        self.risk_mgr = risk_manager or RiskManager()

        # Normalize weights
        total = self.cfg.wm_weight + self.cfg.llm_weight + self.cfg.ppo_weight
        self._wm_w = self.cfg.wm_weight / total
        self._llm_w = self.cfg.llm_weight / total
        self._ppo_w = self.cfg.ppo_weight / total

    def fuse(
        self,
        wm_pred: WMPrediction,
        llm_decision: LLMDecision | None,
        ppo_action: dict | None,
        market_ctx: MarketContext,
        current_equity: float = 10000.0,
        current_drawdown: float = 0.0,
        current_position: float = 0.0,
    ) -> FusedDecision:
        """
        Fuse all signals into a final trading decision.

        Parameters
        ----------
        wm_pred : WM Prediction
            World model prediction
        llm_decision : LLMDecision or None
            LLM strategy agent decision (None if LLM unavailable)
        ppo_action : dict or None
            PPO policy output {"action": "buy/sell/hold", "confidence": float}
        market_ctx : MarketContext
            Full market context
        current_equity, current_drawdown, current_position
            Portfolio state
        """
        result = FusedDecision(regime=self._classify_regime(market_ctx))

        # --- 1. Extract directional signals (−1 to +1) ---
        wm_signal = self._wm_signal(wm_pred)
        llm_signal = self._llm_signal(llm_decision)
        ppo_signal = self._ppo_signal(ppo_action)

        # --- 2. Weighted fusion ---
        raw_signal = (
            self._wm_w * wm_signal +
            self._llm_w * llm_signal +
            self._ppo_w * ppo_signal
        )

        # --- 3. Agreement analysis ---
        signals = [wm_signal, llm_signal, ppo_signal]
        agreement = self._compute_agreement(signals)
        n_active = sum(1 for s in signals if abs(s) > 0.05)

        # --- 4. Determine action ---
        if abs(raw_signal) < 0.05 or n_active < 2:
            result.action = "hold"
            result.confidence = max(abs(raw_signal), 0.1)
        elif raw_signal > 0:
            result.action = "buy"
            result.confidence = min(abs(raw_signal), 1.0)
        else:
            result.action = "sell"
            result.confidence = min(abs(raw_signal), 1.0)

        # --- 5. Size computation ---
        base_size = self.cfg.base_position_size

        # Agreement boost
        if agreement > 0.7:
            base_size *= (1.0 + self.cfg.agreement_bonus)
        elif agreement < 0.3:
            base_size *= self.cfg.disagreement_penalty

        # Confidence scaling
        base_size *= result.confidence

        # Regime adjustment
        regime_mult = self.cfg.regime_multipliers.get(result.regime, 1.0)
        base_size *= regime_mult

        # LLM size override
        if llm_decision is not None:
            base_size *= llm_decision.size_multiplier

        # --- 6. Risk management ---
        risk_size = self.risk_mgr.compute_position_size(
            confidence=wm_pred.snr,
            current_drawdown=current_drawdown,
            volatility=market_ctx.realized_vol,
            index_type="generic",
        )

        # Final size: min of fusion size and risk-managed size
        result.size = min(base_size, risk_size, self.cfg.max_position_size)
        result.size = max(0.0, result.size)

        # --- 7. Stops ---
        current_price = market_ctx.current_price
        if llm_decision is not None:
            sl_pct = llm_decision.stop_loss_pct
            tp_pct = llm_decision.take_profit_pct
        else:
            # Default: based on volatility
            vol = market_ctx.realized_vol if market_ctx.realized_vol > 0 else 0.001
            sl_pct = max(0.001, min(0.01, vol * 3))
            tp_pct = max(0.002, min(0.02, vol * 6))

        if result.action == "buy":
            result.stop_loss = current_price * (1 - sl_pct)
            result.take_profit = current_price * (1 + tp_pct)
        elif result.action == "sell":
            result.stop_loss = current_price * (1 + sl_pct)
            result.take_profit = current_price * (1 - tp_pct)

        # --- 8. Confidence adjustment ---
        if agreement > 0.7:
            result.confidence = min(result.confidence + 0.1, 1.0)
        if current_drawdown > 0.05:
            result.confidence *= 0.8
        if market_ctx.vol_regime > 1.5:
            result.confidence *= 0.7

        # --- 9. LLM veto ---
        if self.cfg.llm_veto_enabled and llm_decision is not None:
            if llm_decision.action == "hold" and abs(raw_signal) < 0.3:
                result.action = "hold"
                result.size = 0.0
                result.reasoning += " [LLM veto: hold recommended]"

        # --- 10. Build reasoning ---
        result.wm_contribution = self._wm_w * wm_signal
        result.llm_contribution = self._llm_w * llm_signal
        result.ppo_contribution = self._ppo_w * ppo_signal
        result.risk_adjustment = risk_size / max(base_size, 1e-8) if base_size > 0 else 1.0

        result.reasoning = (
            f"WM={wm_signal:+.2f} LLM={llm_signal:+.2f} PPO={ppo_signal:+.2f} "
            f"=> raw={raw_signal:+.2f} agree={agreement:.0%} "
            f"regime={result.regime} size={result.size:.2f}"
        )

        if llm_decision and llm_decision.reasoning:
            result.risk_notes = llm_decision.reasoning

        logger.info(
            "fused_decision action=%s size=%.3f conf=%.2f %s",
            result.action, result.size, result.confidence, result.reasoning,
        )

        return result

    # --- Signal extractors ---

    def _wm_signal(self, pred: WMPrediction) -> float:
        """Extract directional signal from world model prediction."""
        if pred.snr == 0 and pred.cumulative_mean == 0:
            return 0.0
        # Use cumulative return direction weighted by SNR
        signal = np.clip(pred.snr * 0.5, -1.0, 1.0)
        return float(signal)

    def _llm_signal(self, decision: LLMDecision | None) -> float:
        """Extract directional signal from LLM decision."""
        if decision is None:
            return 0.0
        action_map = {"buy": 1.0, "sell": -1.0, "hold": 0.0}
        raw = action_map.get(decision.action, 0.0)
        return raw * decision.confidence

    def _ppo_signal(self, action: dict | None) -> float:
        """Extract directional signal from PPO policy."""
        if action is None:
            return 0.0
        act = action.get("action", "hold")
        conf = action.get("confidence", 0.5)
        action_map = {"buy": 1.0, "sell": -1.0, "hold": 0.0, 1: 1.0, 0: 0.0, -1: -1.0}
        raw = action_map.get(act, 0.0)
        return float(raw) * float(conf)

    def _compute_agreement(self, signals: list[float]) -> float:
        """Compute agreement between signals (0 = total disagreement, 1 = perfect agreement)."""
        active = [s for s in signals if abs(s) > 0.05]
        if len(active) < 2:
            return 0.5  # not enough info

        signs = [np.sign(s) for s in active]
        if all(s == signs[0] for s in signs):
            return 1.0  # all agree
        elif all(s != signs[0] for s in signs[1:]):
            return 0.0  # all disagree
        else:
            return 0.5  # partial agreement

    def _classify_regime(self, ctx: MarketContext) -> str:
        """Classify market regime from context."""
        vol = ctx.vol_regime
        z = abs(ctx.price_z_score)
        streak = abs(ctx.streak)

        if vol > 1.5 or z > 2.0:
            return "spike"
        elif vol < 0.8 and z < 1.0 and streak < 5:
            return "calm"
        else:
            return "choppy"
