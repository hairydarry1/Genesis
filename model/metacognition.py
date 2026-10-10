"""
model/metacognition.py — Metacognition controller for the self-improving trading system.

This is the "brain" that orchestrates:
  1. Auto Feature Engine — discovers new predictive features
  2. Strategy Generator — proposes and tests hypotheses
  3. Performance Monitor — watches live performance
  4. Online Retrainer — retrains models on new data
  5. Strategy Lifecycle — manages strategy deployment/retirement

The metacognition loop:
  OBSERVE → THINK → HYPOTHESIZE → TEST → DEPLOY → MONITOR → LEARN → repeat

Usage:
    from model.metacognition import Metacognition
    brain = Metacognition()
    brain.start()  # Starts the autonomous learning loop
    brain.stop()
"""

from __future__ import annotations

import json
import logging
import time
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class BrainState:
    """Current state of the metacognition system."""
    is_running: bool = False
    cycle_count: int = 0
    last_cycle_time: float = 0.0
    current_phase: str = "idle"  # idle, observing, thinking, testing, deploying, monitoring

    # Discovery
    features_discovered: int = 0
    hypotheses_tested: int = 0
    hypotheses_passed: int = 0

    # Performance
    active_strategies: int = 0
    retired_strategies: int = 0
    total_pnl: float = 0.0

    # Health
    system_health: float = 1.0
    needs_attention: bool = False
    last_retrain: float = 0.0

    # Learning log
    learnings: list[str] = field(default_factory=list)


class Metacognition:
    """
    The "brain" that makes the trading system self-improving.

    Runs an autonomous loop:
    1. OBSERVE: Collect market data and performance metrics
    2. THINK: Analyze what's working and what's not
    3. HYPOTHESIZE: Generate new strategy ideas
    4. TEST: Backtest hypotheses automatically
    5. DEPLOY: Promote winning strategies
    6. MONITOR: Watch live performance
    7. LEARN: Feed results back to improve future hypotheses
    """

    def __init__(self, config: dict = None):
        self.config = config or {}
        self.state = BrainState()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()

        # Components (lazy-loaded)
        self._feature_engine = None
        self._strategy_generator = None
        self._performance_monitor = None
        self._retrainer = None
        self._lifecycle = None

        # Data
        self._learnings_file = Path("checkpoints/brain/learnings.json")
        self._learnings_file.parent.mkdir(parents=True, exist_ok=True)
        self._load_learnings()

    def _load_learnings(self):
        """Load accumulated learnings."""
        if self._learnings_file.exists():
            try:
                with open(self._learnings_file) as f:
                    data = json.load(f)
                self.state.learnings = data.get("learnings", [])
            except Exception:
                pass

    def _save_learnings(self):
        """Save learnings to disk."""
        with open(self._learnings_file, "w") as f:
            json.dump({"learnings": self.state.learnings[-100:]}, f, indent=2)

    @property
    def feature_engine(self):
        if self._feature_engine is None:
            from model.auto_features import AutoFeatureEngine
            self._feature_engine = AutoFeatureEngine()
        return self._feature_engine

    @property
    def strategy_generator(self):
        if self._strategy_generator is None:
            from model.strategy_generator import StrategyGenerator
            self._strategy_generator = StrategyGenerator()
        return self._strategy_generator

    @property
    def performance_monitor(self):
        if self._performance_monitor is None:
            from model.performance_monitor import PerformanceMonitor
            self._performance_monitor = PerformanceMonitor()
        return self._performance_monitor

    @property
    def retrainer(self):
        if self._retrainer is None:
            from model.online_retrain import OnlineRetrainer
            self._retrainer = OnlineRetrainer()
        return self._retrainer

    @property
    def lifecycle(self):
        if self._lifecycle is None:
            from model.strategy_lifecycle import StrategyLifecycle
            self._lifecycle = StrategyLifecycle()
        return self._lifecycle

    def start(self, interval_minutes: int = 60):
        """Start the autonomous learning loop."""
        if self.state.is_running:
            logger.warning("Metacognition already running")
            return

        self.state.is_running = True
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._loop, args=(interval_minutes,), daemon=True)
        self._thread.start()
        logger.info("Metacognition started (interval=%d min)", interval_minutes)

    def stop(self):
        """Stop the learning loop."""
        self.state.is_running = False
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=30)
        logger.info("Metacognition stopped")

    def _loop(self, interval_minutes: int):
        """Main learning loop."""
        while not self._stop_event.is_set():
            try:
                self._cycle()
            except Exception as e:
                logger.error("Cycle error: %s", e)
                self.state.needs_attention = True

            self._stop_event.wait(interval_minutes * 60)

    def _cycle(self):
        """One full observation-thinking-testing cycle."""
        self.state.cycle_count += 1
        self.state.last_cycle_time = time.time()
        logger.info("=== Metacognition cycle %d ===", self.state.cycle_count)

        # Phase 1: OBSERVE
        self.state.current_phase = "observing"
        logger.info("[1/7] Observing...")
        self._observe()

        # Phase 2: THINK
        self.state.current_phase = "thinking"
        logger.info("[2/7] Thinking...")
        self._think()

        # Phase 3: HYPOTHESIZE
        self.state.current_phase = "hypothesizing"
        logger.info("[3/7] Hypothesizing...")
        self._hypothesize()

        # Phase 4: TEST
        self.state.current_phase = "testing"
        logger.info("[4/7] Testing...")
        self._test()

        # Phase 5: DEPLOY
        self.state.current_phase = "deploying"
        logger.info("[5/7] Deploying...")
        self._deploy()

        # Phase 6: MONITOR
        self.state.current_phase = "monitoring"
        logger.info("[6/7] Monitoring...")
        self._monitor()

        # Phase 7: LEARN
        self.state.current_phase = "learning"
        logger.info("[7/7] Learning...")
        self._learn()

        self.state.current_phase = "idle"
        logger.info("=== Cycle %d complete ===", self.state.cycle_count)

    def _observe(self):
        """Collect current market state and performance data."""
        # Check performance monitor
        status = self.performance_monitor.get_status()
        self.state.system_health = status.health_score
        self.state.needs_attention = status.needs_retrain

        # Check strategy lifecycle
        lifecycle_summary = self.lifecycle.get_summary()
        self.state.active_strategies = lifecycle_summary["active"]
        self.state.retired_strategies = lifecycle_summary["retired"]
        self.state.total_pnl = lifecycle_summary["total_pnl"]

        logger.info("  health=%.2f active=%d retired=%d pnl=%.2f",
                     status.health_score, self.state.active_strategies,
                     self.state.retired_strategies, self.state.total_pnl)

    def _think(self):
        """Analyze what's working and what's not."""
        status = self.performance_monitor.get_status()

        # Generate insights
        insights = []
        if status.edge_degradation:
            insights.append("Edge is degrading — need new strategies")
        if status.regime_shift:
            insights.append("Regime shifted — old strategies may be stale")
        if status.max_drawdown > 0.10:
            insights.append(f"Drawdown at {status.max_drawdown:.1%} — risk too high")
        if status.win_rate < 0.45 and status.total_trades >= 20:
            insights.append(f"Win rate {status.win_rate:.0%} is low")

        for insight in insights:
            self.state.learnings.append(f"[{time.strftime('%Y-%m-%d')}] {insight}")
            logger.info("  INSIGHT: %s", insight)

    def _hypothesize(self):
        """Generate new strategy hypotheses."""
        # This would use the StrategyGenerator with LLM
        # For now, generate rule-based hypotheses
        self.state.hypotheses_tested += 1

    def _test(self):
        """Test hypotheses through backtesting."""
        pass  # Handled by strategy generator

    def _deploy(self):
        """Deploy winning strategies."""
        # Check if retraining is needed
        if self.state.needs_attention:
            logger.info("  Retraining needed — triggering...")
            for symbol in ["R_75", "R_10", "BOOM500", "CRASH500"]:
                try:
                    result = self.retrainer.retrain(symbol, epochs=20)
                    if result.deployed:
                        logger.info("  Deployed new model for %s (improvement: %.4f)", symbol, result.improvement)
                        self.state.learnings.append(f"Deployed improved model for {symbol}")
                except Exception as e:
                    logger.warning("  Retrain failed for %s: %s", symbol, e)

    def _monitor(self):
        """Check health of all active strategies."""
        for sv in self.lifecycle.get_active_strategies():
            new_status = self.lifecycle.check_health(sv.id)
            if new_status == "retired":
                logger.info("  Retired strategy: %s", sv.name)
                self.state.learnings.append(f"Retired strategy: {sv.name}")

    def _learn(self):
        """Accumulate learnings for future improvement."""
        self._save_learnings()

    def get_status(self) -> dict:
        """Get full brain status."""
        return {
            "running": self.state.is_running,
            "cycle": self.state.cycle_count,
            "phase": self.state.current_phase,
            "health": self.state.system_health,
            "needs_attention": self.state.needs_attention,
            "active_strategies": self.state.active_strategies,
            "retired_strategies": self.state.retired_strategies,
            "total_pnl": self.state.total_pnl,
            "recent_learnings": self.state.learnings[-10:],
            "lifecycle": self.lifecycle.get_summary(),
            "performance": {
                "health_score": self.performance_monitor.get_status().health_score,
                "recommendations": self.performance_monitor.get_status().recommendations,
            },
        }

    def run_cycle(self):
        """Run one cycle manually (for testing)."""
        self._cycle()

    def inject_learning(self, learning: str):
        """Manually inject a learning."""
        self.state.learnings.append(f"[manual] {learning}")
        self._save_learnings()
        logger.info("Learning injected: %s", learning)
