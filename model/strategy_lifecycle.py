"""
model/strategy_lifecycle.py — Strategy lifecycle management.

Manages the lifecycle of trading strategies:
  1. Generate → New strategy proposed
  2. Validate → Backtested and paper-traded
  3. Deploy → Live with small size
  4. Monitor → Watch performance
  5. Scale → Increase size if profitable
  6. Retire → Kill if underperforming

Each strategy has a version, history, and status.

Usage:
    from model.strategy_lifecycle import StrategyLifecycle
    lifecycle = StrategyLifecycle()
    lifecycle.deploy("v1_momentum_rsi", features=[...], config={...})
    status = lifecycle.check_health("v1_momentum_rsi")
    if status == "retire":
        lifecycle.retire("v1_momentum_rsi")
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class StrategyVersion:
    """A versioned trading strategy."""
    id: str
    name: str
    description: str
    features: list[str]
    config: dict = field(default_factory=dict)
    status: str = "generated"  # generated, validating, paper, live, scaling, retired
    created_at: float = 0.0
    deployed_at: float = 0.0
    retired_at: float = 0.0

    # Performance tracking
    total_pnl: float = 0.0
    trade_count: int = 0
    win_count: int = 0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0
    current_equity: float = 0.0
    sharpe_ratio: float = 0.0

    # Size management
    position_size: float = 1.0  # Multiplier (0-2)
    max_position_size: float = 2.0

    # Health
    health_score: float = 1.0
    last_health_check: float = 0.0
    consecutive_losses: int = 0
    days_since_deploy: int = 0


class StrategyLifecycle:
    """
    Manages the full lifecycle of trading strategies.

    Strategies progress through stages:
    generated → validating → paper → live → scaling → retired

    The lifecycle manager:
    - Auto-deploys strategies that pass validation
    - Monitors live performance
    - Scales up profitable strategies
    - Retires underperforming ones
    """

    # Thresholds
    MIN_TRADES_FOR_VALIDATION = 50
    MIN_TRADES_FOR_DEPLOY = 100
    MIN_SHARPE_FOR_DEPLOY = 1.0
    MIN_WIN_RATE_FOR_DEPLOY = 0.50
    MAX_DRAWDOWN_FOR_RETIRE = 0.20
    MIN_SHARPE_TO_RETIRE = 0.3
    SCALE_UP_THRESHOLD = 1.5  # Sharpe threshold to increase size
    SCALE_DOWN_THRESHOLD = 0.5  # Sharpe threshold to decrease size

    def __init__(self, data_dir: str = "checkpoints/strategies"):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.strategies: dict[str, StrategyVersion] = {}
        self._load_strategies()

    def _load_strategies(self):
        """Load saved strategies from disk."""
        for path in self.data_dir.glob("*.json"):
            try:
                with open(path) as f:
                    data = json.load(f)
                sv = StrategyVersion(**data)
                self.strategies[sv.id] = sv
            except Exception as e:
                logger.warning("Failed to load strategy %s: %s", path, e)

    def _save_strategy(self, sv: StrategyVersion):
        """Save a strategy to disk."""
        path = self.data_dir / f"{sv.id}.json"
        with open(path, "w") as f:
            json.dump({
                "id": sv.id, "name": sv.name, "description": sv.description,
                "features": sv.features, "config": sv.config, "status": sv.status,
                "created_at": sv.created_at, "deployed_at": sv.deployed_at,
                "retired_at": sv.retired_at, "total_pnl": sv.total_pnl,
                "trade_count": sv.trade_count, "win_count": sv.win_count,
                "max_drawdown": sv.max_drawdown, "peak_equity": sv.peak_equity,
                "current_equity": sv.current_equity, "sharpe_ratio": sv.sharpe_ratio,
                "position_size": sv.position_size, "max_position_size": sv.max_position_size,
                "health_score": sv.health_score, "last_health_check": sv.last_health_check,
                "consecutive_losses": sv.consecutive_losses,
            }, f, indent=2)

    def create(self, name: str, description: str, features: list[str], config: dict = None) -> StrategyVersion:
        """Create a new strategy."""
        sv = StrategyVersion(
            id=f"{name}_{int(time.time())}",
            name=name,
            description=description,
            features=features,
            config=config or {},
            status="generated",
            created_at=time.time(),
            current_equity=10000,
        )
        self.strategies[sv.id] = sv
        self._save_strategy(sv)
        logger.info("strategy_created id=%s name=%s", sv.id, sv.name)
        return sv

    def advance(self, strategy_id: str) -> str:
        """Advance a strategy to the next stage."""
        sv = self.strategies.get(strategy_id)
        if not sv:
            return "not_found"

        transitions = {
            "generated": "validating",
            "validating": "paper",
            "paper": "live",
            "live": "scaling",
        }

        new_status = transitions.get(sv.status)
        if new_status:
            sv.status = new_status
            if new_status == "live":
                sv.deployed_at = time.time()
            self._save_strategy(sv)
            logger.info("strategy_advanced id=%s status=%s", sv.id, sv.status)

        return sv.status

    def record_trade(self, strategy_id: str, pnl: float):
        """Record a trade for a strategy."""
        sv = self.strategies.get(strategy_id)
        if not sv:
            return

        sv.total_pnl += pnl
        sv.trade_count += 1
        if pnl > 0:
            sv.win_count += 1
            sv.consecutive_losses = 0
        else:
            sv.consecutive_losses += 1

        sv.current_equity += pnl
        sv.peak_equity = max(sv.peak_equity, sv.current_equity)
        dd = (sv.peak_equity - sv.current_equity) / max(sv.peak_equity, 1)
        sv.max_drawdown = max(sv.max_drawdown, dd)

        # Update Sharpe (rolling 50 trades)
        sv.health_score = self._compute_health(sv)
        self._save_strategy(sv)

    def check_health(self, strategy_id: str) -> str:
        """Check strategy health and take action if needed."""
        sv = self.strategies.get(strategy_id)
        if not sv or sv.status == "retired":
            return "unknown"

        sv.last_health_check = time.time()
        sv.days_since_deploy = int((time.time() - sv.deployed_at) / 86400) if sv.deployed_at else 0

        # Auto-retire if too many losses
        if sv.consecutive_losses >= 10:
            self.retire(strategy_id, "10 consecutive losses")
            return "retired"

        # Auto-retire if drawdown too high
        if sv.max_drawdown > self.MAX_DRAWDOWN_FOR_RETIRE and sv.trade_count >= self.MIN_TRADES_FOR_VALIDATION:
            self.retire(strategy_id, f"Max drawdown {sv.max_drawdown:.1%}")
            return "retired"

        # Auto-retire if Sharpe too low after enough trades
        if sv.sharpe_ratio < self.MIN_SHARPE_TO_RETIRE and sv.trade_count >= self.MIN_TRADES_FOR_VALIDATION:
            self.retire(strategy_id, f"Sharpe {sv.sharpe_ratio:.2f} below threshold")
            return "retired"

        # Scale up if performing well
        if sv.status == "live" and sv.sharpe_ratio > self.SCALE_UP_THRESHOLD:
            sv.position_size = min(sv.position_size * 1.2, sv.max_position_size)
            sv.status = "scaling"
            self._save_strategy(sv)
            return "scaling"

        # Scale down if performing poorly
        if sv.status == "scaling" and sv.sharpe_ratio < self.SCALE_DOWN_THRESHOLD:
            sv.position_size = max(sv.position_size * 0.8, 0.5)
            sv.status = "live"
            self._save_strategy(sv)
            return "live"

        return sv.status

    def retire(self, strategy_id: str, reason: str = ""):
        """Retire a strategy."""
        sv = self.strategies.get(strategy_id)
        if sv:
            sv.status = "retired"
            sv.retired_at = time.time()
            self._save_strategy(sv)
            logger.info("strategy_retired id=%s reason=%s", sv.id, reason)

    def _compute_health(self, sv: StrategyVersion) -> float:
        """Compute strategy health score."""
        if sv.trade_count < 10:
            return 0.5

        score = 1.0
        win_rate = sv.win_count / sv.trade_count if sv.trade_count > 0 else 0
        if win_rate < 0.45:
            score -= 0.3
        if sv.max_drawdown > 0.10:
            score -= 0.2
        if sv.consecutive_losses > 5:
            score -= 0.2
        if sv.total_pnl < 0:
            score -= 0.3

        return max(0.0, min(1.0, score))

    def get_active_strategies(self) -> list[StrategyVersion]:
        """Get all non-retired strategies."""
        return [sv for sv in self.strategies.values() if sv.status != "retired"]

    def get_summary(self) -> dict:
        """Get summary of all strategies."""
        active = self.get_active_strategies()
        retired = [sv for sv in self.strategies.values() if sv.status == "retired"]

        return {
            "total": len(self.strategies),
            "active": len(active),
            "retired": len(retired),
            "by_status": {
                status: sum(1 for sv in self.strategies.values() if sv.status == status)
                for status in ["generated", "validating", "paper", "live", "scaling", "retired"]
            },
            "total_pnl": sum(sv.total_pnl for sv in active),
            "avg_sharpe": np.mean([sv.sharpe_ratio for sv in active]) if active else 0,
            "strategies": [
                {
                    "id": sv.id, "name": sv.name, "status": sv.status,
                    "pnl": sv.total_pnl, "trades": sv.trade_count,
                    "win_rate": sv.win_count / sv.trade_count if sv.trade_count > 0 else 0,
                    "sharpe": sv.sharpe_ratio, "size": sv.position_size,
                    "health": sv.health_score,
                }
                for sv in active
            ],
        }
