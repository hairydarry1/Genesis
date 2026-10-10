"""
model/strategy_brain.py — Unified Thinking Layer

Connects:
  1. World Model predictions (market direction + confidence)
  2. Trade Thinker (post-mortem analysis per trade)
  3. Adaptive Learner (auto-adjusts parameters based on patterns)
  4. Performance Monitor (edge degradation detection)

This is the "brain" that sits between raw signals and trade execution.
Every signal goes through the brain before becoming a trade.
Every trade outcome feeds back into the brain for learning.

Architecture:
  Signal → Brain.evaluate() → conviction score + adjusted params
  Trade close → Brain.analyze() → diagnosis + category + fix
  Every N trades → Brain.adapt() → parameter adjustments
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

from model.trade_thinker import (
    MarketContext, TradeOutcome, PostMortemAnalyzer,
    AdaptiveLearner, ContextBuilder,
)
from model.performance_monitor import PerformanceMonitor, PerformanceStatus


# ---------------------------------------------------------------------------
# Signal evaluation result
# ---------------------------------------------------------------------------

@dataclass
class BrainDecision:
    """Output of the brain's evaluation of a potential trade."""
    should_trade: bool = True
    conviction: float = 0.5          # 0-1, how confident we are
    adjusted_tp: float = 0.0         # TP adjusted by brain (0 = use default)
    adjusted_sl: float = 0.0         # SL adjusted by brain (0 = use default)
    adjusted_risk_pct: float = 0.0   # Risk adjusted by brain (0 = use default)
    reason: str = ""                 # Why we should/shouldn't trade
    regime: str = "normal"           # Current market regime
    world_model_bias: str = "flat"   # WM direction bias
    health_score: float = 1.0        # System health 0-1
    adaptive_params: dict = field(default_factory=dict)  # Current adaptive params


# ---------------------------------------------------------------------------
# Strategy Brain — the unified thinking layer
# ---------------------------------------------------------------------------

class StrategyBrain:
    """
    Unified thinking layer that sits between signal generation and execution.
    
    Flow:
      1. Signal generated (SMC or Drift Fade)
      2. Brain.evaluate(context) → decision (trade or skip, adjusted params)
      3. Trade executes with brain's adjusted parameters
      4. Trade closes → Brain.analyze(outcome) → diagnosis
      5. Every N trades → Brain.adapt() → auto-adjust parameters
    """

    def __init__(self, symbol: str, data_dir: str = "checkpoints_tiny"):
        self.symbol = symbol
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # Sub-systems
        self.thinker = AdaptiveLearner()
        self.monitor = PerformanceMonitor()
        self.context_builder = ContextBuilder()

        # World model state (updated by external caller)
        self._wm_bias: str = "flat"
        self._wm_confidence: float = 0.0
        self._wm_snr: float = 0.0

        # Regime tracking
        self._current_regime: str = "normal"
        self._regime_history: deque[str] = deque(maxlen=100)

        # Learning rate tracking
        self._total_adjustments: int = 0
        self._last_adapt_trade: int = 0
        self._adapt_interval: int = 20  # Re-evaluate every N trades

        # Trade log for persistence
        self._trade_log: list[dict] = []
        self._log_file = self.data_dir / f"brain_log_{symbol}.json"

        # Load previous state if exists
        self._load_state()

    # -------------------------------------------------------------------
    # Core: Evaluate a potential trade
    # -------------------------------------------------------------------

    def evaluate(self, context: MarketContext, strategy_type: str = "drift") -> BrainDecision:
        """
        Evaluate whether to take a trade based on context.
        
        Args:
            context: Rich market context at potential entry
            strategy_type: "smc" or "drift"
            
        Returns:
            BrainDecision with conviction, adjusted params, and reasoning
        """
        decision = BrainDecision()

        # 1. Check adaptive filters (from post-mortem learning)
        should_trade, reason = self.thinker.should_trade(context)
        if not should_trade:
            decision.should_trade = False
            decision.reason = f"[ADAPTIVE] {reason}"
            decision.conviction = 0.0
            return decision

        # 2. World model bias check
        decision.world_model_bias = self._wm_bias
        if self._wm_bias != "flat" and self._wm_confidence > 0.7:
            # Strong WM bias against our trade direction
            if strategy_type == "drift":
                fade_dir = "down" if context.spike_size_pct > 0 else "up"
                if self._wm_bias != fade_dir:
                    decision.conviction *= 0.6  # Reduce conviction
                    decision.reason += f"[WM] Bias {self._wm_bias} against fade; "

        # 3. Compute conviction
        decision.conviction = context.conviction

        # 4. Adjust TP/SL based on regime and conviction
        decision.adjusted_tp, decision.adjusted_sl = self._adjust_exits(
            context, strategy_type, decision.conviction
        )

        # 5. Adjust risk based on health and conviction
        decision.adjusted_risk_pct = self._adjust_risk(decision.conviction)

        # 6. Health check
        status = self.monitor.get_status()
        decision.health_score = status.health_score
        decision.regime = self._current_regime

        if status.needs_retrain:
            decision.adjusted_risk_pct *= 0.5  # Halve risk when system is unhealthy
            decision.reason += "[HEALTH] System degraded, risk halved; "

        # 7. Final decision
        if decision.conviction < 0.2:
            decision.should_trade = False
            decision.reason += f"[LOW CONVICTION] {decision.conviction:.2f} < 0.2"
        elif not decision.reason:
            decision.reason = f"[OK] conv={decision.conviction:.2f} regime={self._current_regime}"

        # 8. Store adaptive params for dashboard display
        decision.adaptive_params = self.thinker.get_status()

        return decision

    # -------------------------------------------------------------------
    # Core: Analyze a completed trade
    # -------------------------------------------------------------------

    def analyze(self, outcome: TradeOutcome) -> TradeOutcome:
        """
        Analyze a completed trade and learn from it.
        
        Args:
            outcome: Trade outcome with context captured at entry
            
        Returns:
            Enriched outcome with diagnosis, category, and suggested fixes
        """
        # Run post-mortem
        enriched = self.thinker.record_trade(outcome)

        # Record in performance monitor
        self.monitor.record_trade(
            pnl=outcome.pnl_dollars,
            direction=outcome.direction,
            index=self.symbol,
            entry_price=outcome.entry_price,
            exit_price=outcome.exit_price,
            holding_bars=outcome.bars_held,
            regime=self._current_regime,
            confidence=outcome.context.conviction,
        )

        # Log for persistence
        self._trade_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trade_id": outcome.trade_id,
            "pnl": outcome.pnl_dollars,
            "pnl_pct": outcome.pnl_pct,
            "direction": "LONG" if outcome.direction == 1 else "SHORT",
            "entry": outcome.entry_price,
            "exit": outcome.exit_price,
            "bars_held": outcome.bars_held,
            "exit_reason": outcome.exit_reason,
            "category": outcome.category,
            "diagnosis": outcome.diagnosis,
            "fixable": outcome.fixable,
            "suggested_fix": outcome.suggested_fix,
            "conviction": outcome.context.conviction,
            "regime": self._current_regime,
        })

        # Save periodically
        if len(self._trade_log) % 10 == 0:
            self._save_state()

        # Maybe adapt parameters
        self._maybe_adapt()

        return enriched

    # -------------------------------------------------------------------
    # Core: Update world model prediction
    # -------------------------------------------------------------------

    def update_world_model(self, bias: str, confidence: float, snr: float = 0.0):
        """Update world model prediction (called from live data stream)."""
        self._wm_bias = bias
        self._wm_confidence = confidence
        self._wm_snr = snr

    # -------------------------------------------------------------------
    # Core: Update regime
    # -------------------------------------------------------------------

    def update_regime(self, regime: str):
        """Update current market regime."""
        if regime != self._current_regime:
            self._regime_history.append(regime)
            self._current_regime = regime

    # -------------------------------------------------------------------
    # Internal: Adjust exit levels
    # -------------------------------------------------------------------

    def _adjust_exits(self, context: MarketContext, strategy_type: str,
                      conviction: float) -> tuple[float, float]:
        """
        Adjust TP/SL based on context.
        
        Returns (adjusted_tp, adjusted_sl) in points.
        0 means use default.
        """
        if strategy_type == "smc":
            # SMC uses percentage-based exits, no adjustment needed
            return 0.0, 0.0

        # Drift Fade adjustments
        base_tp = 3.0
        base_sl = 1.0

        # High conviction → wider TP to capture more
        if conviction > 0.7:
            tp = base_tp * 1.2  # 3.6
            sl = base_sl        # Keep tight
        elif conviction < 0.35:
            tp = base_tp * 0.8  # 2.4
            sl = base_sl * 0.8  # 0.8 (tighter stop)
        else:
            tp, sl = base_tp, base_sl

        # Volatility adjustment
        if context.vol_regime == "high":
            tp *= 1.15  # Wider TP in high vol
            sl *= 1.1
        elif context.vol_regime == "low":
            tp *= 0.85  # Tighter TP in low vol
            sl *= 0.9

        # Algorithmic spike → wider SL (these can extend further)
        if context.is_algorithmic_spike:
            sl *= 1.5

        return round(tp, 2), round(sl, 2)

    # -------------------------------------------------------------------
    # Internal: Adjust risk sizing
    # -------------------------------------------------------------------

    def _adjust_risk(self, conviction: float) -> float:
        """
        Adjust risk percentage based on conviction and health.
        Returns fraction (e.g., 0.02 for 2%).
        0 means use default.
        """
        base_risk = 0.02  # 2% default

        # Scale risk with conviction
        risk = base_risk * conviction

        # Floor at 0.5% to avoid micro-trades
        risk = max(0.005, risk)

        return round(risk, 4)

    # -------------------------------------------------------------------
    # Internal: Adapt parameters based on patterns
    # -------------------------------------------------------------------

    def _maybe_adapt(self):
        """Check if we should adapt parameters."""
        n_trades = len(self.thinker.analyzer.history)
        if n_trades - self._last_adapt_trade < self._adapt_interval:
            return
        self._last_adapt_trade = n_trades

        # Run adaptive learner
        self.thinker.maybe_adjust()
        self._total_adjustments += 1

        # Log adjustment
        status = self.thinker.get_status()
        print(f"  [BRAIN-{self.symbol}] Adapted: conv={status['min_conviction']:.2f} "
              f"drift={status['drift_filter']:.2f} spike<{status['spike_size_filter']:.0f}x "
              f"WR={status['recent_wr']:.0%} losses={status['consecutive_losses']}")

    # -------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------

    def _save_state(self):
        """Save brain state to disk."""
        state = {
            "symbol": self.symbol,
            "adaptive_params": self.thinker.get_status(),
            "trade_log": self._trade_log[-100:],  # Keep last 100
            "total_adjustments": self._total_adjustments,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            self._log_file.write_text(json.dumps(state, indent=2, default=str))
        except Exception as e:
            print(f"  [BRAIN] Save error: {e}")

    def _load_state(self):
        """Load previous brain state from disk."""
        if self._log_file.exists():
            try:
                state = json.loads(self._log_file.read_text())
                self._trade_log = state.get("trade_log", [])
                self._total_adjustments = state.get("total_adjustments", 0)
                print(f"  [BRAIN-{self.symbol}] Loaded {len(self._trade_log)} past trades, "
                      f"{self._total_adjustments} adjustments")
            except Exception as e:
                print(f"  [BRAIN-{self.symbol}] Load error: {e}")

    # -------------------------------------------------------------------
    # Dashboard data
    # -------------------------------------------------------------------

    def get_dashboard_data(self) -> dict:
        """Get all data needed for the dashboard."""
        perf = self.monitor.get_status()
        adaptive = self.thinker.get_status()
        postmortem = self.thinker.analyzer.get_category_report()

        return {
            "symbol": self.symbol,
            "health_score": perf.health_score,
            "health_status": "HEALTHY" if perf.health_score > 0.7 else
                            "WARNING" if perf.health_score > 0.4 else "CRITICAL",
            "recommendations": perf.recommendations,
            # Performance
            "total_trades": perf.total_trades,
            "win_rate": perf.win_rate,
            "total_pnl": perf.total_pnl,
            "profit_factor": perf.profit_factor,
            "max_drawdown": perf.max_drawdown,
            "current_drawdown": perf.current_drawdown,
            "rolling_sharpe_10": perf.rolling_sharpe_10,
            "rolling_sharpe_50": perf.rolling_sharpe_50,
            # Regime
            "regime": self._current_regime,
            "regime_win_rates": perf.regime_win_rates,
            # World model
            "wm_bias": self._wm_bias,
            "wm_confidence": self._wm_confidence,
            "wm_snr": self._wm_snr,
            # Adaptive params
            "min_conviction": adaptive["min_conviction"],
            "drift_filter": adaptive["drift_filter"],
            "spike_size_filter": adaptive["spike_size_filter"],
            "consecutive_losses": adaptive["consecutive_losses"],
            "adjustments_made": adaptive["adjustments_made"],
            # Post-mortem
            "category_report": postmortem,
            # Equity curve (last 200 points)
            "equity_curve": self.monitor.equity_curve[-200:],
            # Recent trades (last 20)
            "recent_trades": self._trade_log[-20:],
        }

    # -------------------------------------------------------------------
    # Print summary
    # -------------------------------------------------------------------

    def print_summary(self):
        """Print brain summary to console."""
        data = self.get_dashboard_data()
        print(f"\n{'='*72}")
        print(f"  STRATEGY BRAIN — {self.symbol}")
        print(f"{'='*72}")
        print(f"  Health: {data['health_score']:.2f} ({data['health_status']})")
        print(f"  Regime: {data['regime']}")
        print(f"  WM Bias: {data['wm_bias']} (conf={data['wm_confidence']:.2f})")
        print(f"  Trades: {data['total_trades']}  WR: {data['win_rate']:.0%}  "
              f"PF: {data['profit_factor']:.2f}")
        print(f"  PnL: ${data['total_pnl']:+.2f}  MaxDD: {data['max_drawdown']:.1%}")
        print(f"  Conviction floor: {data['min_conviction']:.2f}")
        print(f"  Adjustments made: {data['adjustments_made']}")
        if data['recommendations']:
            print(f"  Recommendations:")
            for r in data['recommendations']:
                print(f"    → {r}")
        # Post-mortem summary
        report = self.thinker.analyzer.get_category_report()
        if report:
            print(f"  --- Diagnosis by Category ---")
            for cat, stats in sorted(report.items(), key=lambda x: -x[1]["trades"]):
                print(f"    {cat:30s}: {stats['trades']:3d} trades, "
                      f"WR={stats['win_rate']:.0f}%, "
                      f"avg PnL={stats['avg_pnl']*100:+.3f}%")
        # Recent diagnostics
        history = self.thinker.analyzer.history
        if history:
            print(f"  --- Last 3 Trade Diagnostics ---")
            for t in history[-3:]:
                emoji = "+" if t.is_win() else "-"
                print(f"    #{t.trade_id} {emoji} PnL={t.pnl_pct*100:+.3f}% [{t.category}]")
                print(f"      {t.diagnosis}")
        print(f"{'='*72}")
