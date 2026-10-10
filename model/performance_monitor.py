"""
model/performance_monitor.py — Live performance monitoring and edge detection.

Monitors:
  - Rolling Sharpe ratio (is the strategy still profitable?)
  - Win rate stability (is it degrading?)
  - Drawdown tracking (are we in a bad regime?)
  - Regime detection (has the market changed?)
  - Statistical significance (is the edge real or noise?)

Alerts when:
  - Rolling Sharpe drops below threshold
  - Win rate degrades significantly
  - Maximum drawdown breached
  - Market regime changed (model may be stale)

Usage:
    from model.performance_monitor import PerformanceMonitor
    monitor = PerformanceMonitor()
    monitor.record_trade(pnl=100, metadata={...})
    status = monitor.get_status()
    if status.should_retrain:
        # Trigger retraining
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class TradeRecord:
    """A recorded trade with metadata."""
    timestamp: float
    pnl: float
    direction: int  # +1 long, -1 short
    index: str
    entry_price: float = 0.0
    exit_price: float = 0.0
    holding_bars: int = 0
    regime: str = "unknown"
    confidence: float = 0.0
    snr: float = 0.0


@dataclass
class PerformanceStatus:
    """Current performance status."""
    # Core metrics
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    profit_factor: float = 0.0

    # Rolling metrics
    rolling_sharpe_10: float = 0.0  # Last 10 trades
    rolling_sharpe_50: float = 0.0  # Last 50 trades
    rolling_win_rate_10: float = 0.0
    rolling_win_rate_50: float = 0.0

    # Drawdown
    current_drawdown: float = 0.0
    max_drawdown: float = 0.0
    peak_equity: float = 0.0

    # Regime
    current_regime: str = "calm"
    regime_win_rates: dict = field(default_factory=dict)

    # Health signals
    edge_degradation: bool = False  # Is the edge fading?
    regime_shift: bool = False  # Has the market regime changed?
    needs_retrain: bool = False  # Should we retrain the model?
    health_score: float = 1.0  # 0-1, overall health

    # Recommendations
    recommendations: list[str] = field(default_factory=list)


class PerformanceMonitor:
    """
    Monitors live trading performance and detects edge degradation.

    Uses rolling windows, statistical tests, and regime detection
    to determine if the strategy needs retraining or adjustment.
    """

    def __init__(
        self,
        min_trades_for_signal: int = 20,
        sharpe_threshold: float = 0.5,
        win_rate_threshold: float = 0.45,
        max_drawdown_threshold: float = 0.15,
        retrain_sharpe_threshold: float = 0.3,
    ):
        self.min_trades = min_trades_for_signal
        self.sharpe_threshold = sharpe_threshold
        self.win_rate_threshold = win_rate_threshold
        self.max_dd_threshold = max_drawdown_threshold
        self.retrain_threshold = retrain_sharpe_threshold

        self.trades: list[TradeRecord] = []
        self.equity_curve: list[float] = [10000]
        self.peak_equity: float = 10000
        self._regime_changes: list[tuple[float, str]] = []

    def record_trade(self, pnl: float, **kwargs):
        """Record a completed trade."""
        trade = TradeRecord(
            timestamp=time.time(),
            pnl=pnl,
            direction=kwargs.get("direction", 0),
            index=kwargs.get("index", "unknown"),
            entry_price=kwargs.get("entry_price", 0),
            exit_price=kwargs.get("exit_price", 0),
            holding_bars=kwargs.get("holding_bars", 0),
            regime=kwargs.get("regime", "calm"),
            confidence=kwargs.get("confidence", 0),
            snr=kwargs.get("snr", 0),
        )
        self.trades.append(trade)

        # Update equity
        new_equity = self.equity_curve[-1] + pnl
        self.equity_curve.append(new_equity)
        self.peak_equity = max(self.peak_equity, new_equity)

        logger.info("trade_recorded pnl=%.2f equity=%.2f total=%d", pnl, new_equity, len(self.trades))

    def record_bar(self, equity: float, regime: str = "calm"):
        """Record a bar update (called every bar, not just on trades)."""
        self.equity_curve.append(equity)
        self.peak_equity = max(self.peak_equity, equity)

        # Detect regime changes
        if self._regime_changes and self._regime_changes[-1][1] != regime:
            self._regime_changes.append((time.time(), regime))
        elif not self._regime_changes:
            self._regime_changes.append((time.time(), regime))

    def get_status(self) -> PerformanceStatus:
        """Compute current performance status."""
        status = PerformanceStatus()
        n = len(self.trades)

        if n == 0:
            status.recommendations.append("No trades recorded yet — start paper trading")
            return status

        # Core metrics
        pnls = np.array([t.pnl for t in self.trades])
        status.total_trades = n
        status.total_pnl = float(np.sum(pnls))
        status.avg_pnl = float(np.mean(pnls))
        status.win_rate = float(np.mean(pnls > 0))

        wins = pnls[pnls > 0]
        losses = pnls[pnls < 0]
        gross_profit = float(np.sum(wins)) if len(wins) > 0 else 0
        gross_loss = float(np.abs(np.sum(losses))) if len(losses) > 0 else 1e-10
        status.profit_factor = gross_profit / gross_loss

        # Rolling metrics
        if n >= 10:
            recent_10 = pnls[-10:]
            status.rolling_win_rate_10 = float(np.mean(recent_10 > 0))
            status.rolling_sharpe_10 = self._compute_sharpe(recent_10)

        if n >= 50:
            recent_50 = pnls[-50:]
            status.rolling_win_rate_50 = float(np.mean(recent_50 > 0))
            status.rolling_sharpe_50 = self._compute_sharpe(recent_50)

        # Drawdown
        eq = np.array(self.equity_curve)
        running_max = np.maximum.accumulate(eq)
        dd = (running_max - eq) / np.maximum(running_max, 1e-10)
        status.current_drawdown = float(dd[-1]) if len(dd) > 0 else 0
        status.max_drawdown = float(np.max(dd)) if len(dd) > 0 else 0
        status.peak_equity = self.peak_equity

        # Regime analysis
        regime_trades = {}
        for t in self.trades:
            r = t.regime
            if r not in regime_trades:
                regime_trades[r] = []
            regime_trades[r].append(t.pnl)

        status.regime_win_rates = {}
        for r, pnls_r in regime_trades.items():
            status.regime_win_rates[r] = float(np.mean(np.array(pnls_r) > 0))

        if self._regime_changes:
            status.current_regime = self._regime_changes[-1][1]

        # Health assessment
        status.edge_degradation = self._detect_edge_degradation()
        status.regime_shift = self._detect_regime_shift()
        status.needs_retrain = self._should_retrain(status)
        status.health_score = self._compute_health(status)
        status.recommendations = self._generate_recommendations(status)

        return status

    def _compute_sharpe(self, pnls: np.ndarray, annualize: bool = True) -> float:
        """Compute Sharpe ratio from PnL series."""
        if len(pnls) < 2 or np.std(pnls) < 1e-10:
            return 0.0
        sharpe = np.mean(pnls) / np.std(pnls)
        if annualize:
            sharpe *= np.sqrt(252 * 24 * 60)  # Per-bar to annual
        return float(sharpe)

    def _detect_edge_degradation(self) -> bool:
        """Detect if the trading edge is fading."""
        if len(self.trades) < self.min_trades:
            return False

        # Compare recent Sharpe to historical
        recent_sharpe = self._compute_sharpe(np.array([t.pnl for t in self.trades[-20:]]))
        historical_sharpe = self._compute_sharpe(np.array([t.pnl for t in self.trades[:-20]])) if len(self.trades) > 40 else recent_sharpe

        # Edge is degrading if recent Sharpe is significantly worse
        return recent_sharpe < historical_sharpe * 0.5 and recent_sharpe < self.sharpe_threshold

    def _detect_regime_shift(self) -> bool:
        """Detect if the market regime has changed recently."""
        if len(self._regime_changes) < 2:
            return False

        # Count regime changes in last 100 trades
        recent_cutoff = time.time() - 3600  # Last hour
        recent_changes = sum(1 for t, _ in self._regime_changes if t > recent_cutoff)
        return recent_changes > 3

    def _should_retrain(self, status: PerformanceStatus) -> bool:
        """Determine if the model should be retrained."""
        # Retrain if:
        # 1. Rolling Sharpe is too low
        # 2. Max drawdown breached
        # 3. Edge is degrading
        # 4. Regime shifted

        if len(self.trades) < self.min_trades:
            return False

        reasons = []
        if status.rolling_sharpe_50 < self.retrain_threshold and len(self.trades) >= 50:
            reasons.append("low_sharpe")
        if status.max_drawdown > self.max_dd_threshold:
            reasons.append("max_drawdown")
        if status.edge_degradation:
            reasons.append("edge_degradation")
        if status.regime_shift:
            reasons.append("regime_shift")

        return len(reasons) >= 2  # Need at least 2 reasons

    def _compute_health(self, status: PerformanceStatus) -> float:
        """Compute overall health score 0-1."""
        if len(self.trades) < 10:
            return 0.5  # Unknown

        score = 1.0

        # Penalize for low Sharpe
        if status.rolling_sharpe_50 < 0:
            score -= 0.3
        elif status.rolling_sharpe_50 < 0.5:
            score -= 0.15

        # Penalize for high drawdown
        score -= min(status.max_drawdown * 2, 0.3)

        # Penalize for edge degradation
        if status.edge_degradation:
            score -= 0.2

        # Penalize for regime shift
        if status.regime_shift:
            score -= 0.1

        # Reward for consistency
        if status.win_rate > 0.55:
            score += 0.1
        if status.profit_factor > 1.5:
            score += 0.1

        return max(0.0, min(1.0, score))

    def _generate_recommendations(self, status: PerformanceStatus) -> list[str]:
        """Generate actionable recommendations."""
        recs = []

        if status.needs_retrain:
            recs.append("URGENT: Model needs retraining — multiple degradation signals")

        if status.edge_degradation:
            recs.append("Edge is fading — consider reducing position size or pausing trading")

        if status.max_drawdown > 0.10:
            recs.append(f"Drawdown at {status.max_drawdown:.1%} — consider reducing exposure")

        if status.regime_shift:
            recs.append("Regime shift detected — model may be stale, retrain recommended")

        if status.rolling_sharpe_10 < 0 and len(status.recommendations) == 0:
            recs.append("Recent trades unprofitable — review strategy logic")

        if status.win_rate < 0.45 and len(self.trades) >= 20:
            recs.append(f"Win rate {status.win_rate:.0%} below threshold — review entry/exit logic")

        # Regime-specific advice
        for regime, wr in status.regime_win_rates.items():
            if wr < 0.40 and regime == status.current_regime:
                recs.append(f"Low win rate in {regime} regime ({wr:.0%}) — avoid trading in this regime")

        if not recs:
            recs.append("System healthy — continue monitoring")

        return recs

    def get_trade_log(self, last_n: int = 50) -> list[dict]:
        """Get recent trade log as dicts."""
        return [
            {
                "timestamp": t.timestamp,
                "pnl": t.pnl,
                "direction": t.direction,
                "index": t.index,
                "regime": t.regime,
                "confidence": t.confidence,
            }
            for t in self.trades[-last_n:]
        ]
