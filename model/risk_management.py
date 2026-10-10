"""
model/risk_management.py — Dynamic risk management for the trading system.

Features:
1. Dynamic position sizing based on world model confidence (SNR)
2. Drawdown-based position scaling
3. Portfolio-level position management
4. Correlation-aware hedging
5. Crash500-specific drift detection

Usage:
    risk_mgr = RiskManager(config)
    position_size = risk_mgr.compute_position_size(
        confidence=0.8,
        current_drawdown=0.02,
        volatility=0.001,
        index_type='boom_500'
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class RiskConfig:
    """Configuration for risk management."""
    
    # --- Position sizing ---
    # Base position size (fraction of equity)
    base_position_size: float = 1.0
    
    # Confidence scaling: position_size = base * (min_confidence + confidence * scale_factor)
    min_confidence: float = 0.3
    confidence_scale: float = 0.7
    
    # --- Drawdown protection ---
    # Maximum drawdown before reducing position size
    max_drawdown_warning: float = 0.05   # 5%: start reducing
    max_drawdown_critical: float = 0.10  # 10%: reduce significantly
    max_drawdown_stop: float = 0.15      # 15%: stop trading
    
    # Position reduction multipliers
    dd_warning_multiplier: float = 0.5
    dd_critical_multiplier: float = 0.2
    
    # --- Volatility scaling ---
    # Scale position inversely with volatility
    vol_target: float = 0.001  # Target volatility
    vol_scale_enabled: bool = True
    
    # --- Portfolio management ---
    # Maximum total exposure across all indices
    max_portfolio_exposure: float = 3.0  # 3x leverage max
    max_single_index_exposure: float = 1.5  # 1.5x per index
    
    # --- Crash500-specific ---
    # Drift detection parameters
    drift_detection_enabled: bool = True
    drift_lookback: int = 1000  # bars to look back for drift
    drift_threshold: float = 0.0001  # minimum drift to detect
    drift_confidence_boost: float = 0.3  # boost confidence when drift detected
    
    # --- Transaction cost awareness ---
    # Minimum expected profit to justify a trade
    min_expected_profit: float = 0.001
    
    def __post_init__(self):
        pass


class RiskManager:
    """
    Dynamic risk management for multi-index trading.
    
    Computes position sizes based on:
    1. World model confidence (SNR)
    2. Current drawdown level
    3. Volatility regime
    4. Portfolio-level exposure limits
    5. Index-specific characteristics (e.g., Crash500 drift)
    """
    
    def __init__(self, config: RiskConfig | None = None):
        self.cfg = config or RiskConfig()
        
        # Portfolio state
        self._index_positions: dict[str, float] = {}  # index -> current position size
        self._index_equity: dict[str, float] = {}     # index -> current equity
        self._peak_equity: dict[str, float] = {}      # index -> peak equity
        self._total_equity: float = 0.0
        
        # Drift detection state
        self._recent_returns: dict[str, list[float]] = {}
    
    def reset(self):
        """Reset all state."""
        self._index_positions.clear()
        self._index_equity.clear()
        self._peak_equity.clear()
        self._total_equity = 0.0
        self._recent_returns.clear()
    
    def update_state(
        self,
        index: str,
        position: float,
        equity: float,
        peak_equity: float,
    ):
        """Update state for a specific index."""
        self._index_positions[index] = position
        self._index_equity[index] = equity
        self._peak_equity[index] = peak_equity
        self._total_equity = sum(self._index_equity.values())
    
    def compute_position_size(
        self,
        confidence: float,
        current_drawdown: float,
        volatility: float,
        index_type: str = 'generic',
        recent_returns: list[float] | None = None,
    ) -> float:
        """
        Compute optimal position size based on multiple factors.
        
        Parameters
        ----------
        confidence : float
            World model signal-to-noise ratio (0 to inf, typically 0 to 2)
        current_drawdown : float
            Current drawdown from peak (0 to 1)
        volatility : float
            Current rolling volatility
        index_type : str
            Index type ('r75', 'vol10', 'boom_500', 'crash_500', 'generic')
        recent_returns : list[float], optional
            Recent returns for drift detection
        
        Returns
        -------
        float
            Position size multiplier (0 to max_single_index_exposure)
        """
        # Start with base position size
        size = self.cfg.base_position_size
        
        # 1. Confidence scaling
        conf_factor = self._confidence_factor(confidence)
        size *= conf_factor
        
        # 2. Drawdown protection
        dd_factor = self._drawdown_factor(current_drawdown)
        size *= dd_factor
        
        # 3. Volatility scaling
        if self.cfg.vol_scale_enabled and volatility > 0:
            vol_factor = self._volatility_factor(volatility)
            size *= vol_factor
        
        # 4. Index-specific adjustments
        if index_type == 'crash_500' and self.cfg.drift_detection_enabled:
            drift_factor = self._drift_factor(recent_returns or [])
            size *= drift_factor
        
        # 5. Portfolio-level limits
        size = self._apply_portfolio_limits(size, index_type)
        
        # Clamp to valid range
        size = max(0.0, min(size, self.cfg.max_single_index_exposure))
        
        return size
    
    def _confidence_factor(self, confidence: float) -> float:
        """Scale position by world model confidence."""
        # confidence is SNR: |predicted_mean| / sqrt(predicted_variance)
        # Higher SNR = stronger directional signal = larger position
        normalized_conf = min(confidence / 2.0, 1.0)  # normalize to [0, 1]
        factor = self.cfg.min_confidence + normalized_conf * self.cfg.confidence_scale
        return factor
    
    def _drawdown_factor(self, current_drawdown: float) -> float:
        """Scale position by current drawdown."""
        if current_drawdown >= self.cfg.max_drawdown_stop:
            return 0.0  # Stop trading
        elif current_drawdown >= self.cfg.max_drawdown_critical:
            return self.cfg.dd_critical_multiplier
        elif current_drawdown >= self.cfg.max_drawdown_warning:
            # Linear interpolation between warning and critical
            range_dd = self.cfg.max_drawdown_critical - self.cfg.max_drawdown_warning
            progress = (current_drawdown - self.cfg.max_drawdown_warning) / range_dd
            factor = 1.0 - progress * (1.0 - self.cfg.dd_critical_multiplier)
            return factor
        else:
            return 1.0  # No reduction
    
    def _volatility_factor(self, volatility: float) -> float:
        """Scale position inversely with volatility (vol targeting)."""
        if volatility <= 0:
            return 1.0
        # Position size ∝ target_vol / actual_vol
        ratio = self.cfg.vol_target / volatility
        # Clamp to reasonable range
        return max(0.3, min(ratio, 2.0))
    
    def _drift_factor(self, recent_returns: list[float]) -> float:
        """
        Detect drift in Crash500-like indices.
        
        When strong upward drift is detected, boost position size
        (the 'always long' strategy is near-optimal).
        """
        if len(recent_returns) < self.cfg.drift_lookback:
            return 1.0
        
        # Use recent returns for drift detection
        returns = np.array(recent_returns[-self.cfg.drift_lookback:])
        
        # Compute drift: mean return * number of bars
        drift = np.mean(returns)
        
        # Positive drift = upward trend = boost position
        if drift > self.cfg.drift_threshold:
            # Boost based on drift strength
            boost = 1.0 + self.cfg.drift_confidence_boost * min(drift / self.cfg.drift_threshold, 2.0)
            return min(boost, 1.5)  # Cap at 1.5x
        elif drift < -self.cfg.drift_threshold:
            # Negative drift = reduce position
            return 0.5
        else:
            return 1.0
    
    def _apply_portfolio_limits(self, proposed_size: float, index_type: str) -> float:
        """Apply portfolio-level exposure limits.

        NOTE: the computed size REPLACES this index's current position
        (target sizing), so this index's own current exposure does not count
        against its own headroom.
        """
        others_total = sum(
            abs(p)
            for name, p in self._index_positions.items()
            if name != index_type
        )
        
        # Check total portfolio exposure (excluding this index's old position,
        # which the proposed size replaces)
        remaining_total = self.cfg.max_portfolio_exposure - others_total
        if remaining_total <= 0:
            return 0.0
        
        # Take the minimum (single-index cap applied directly)
        return min(proposed_size, remaining_total, self.cfg.max_single_index_exposure)
    
    def should_trade(
        self,
        expected_return: float,
        confidence: float,
        current_drawdown: float,
        volatility: float,
    ) -> bool:
        """
        Decide whether a trade is worth taking.
        
        Returns False if:
        - Expected return is too small to justify transaction costs
        - Drawdown is too high
        - Confidence is too low
        """
        # Check drawdown stop
        if current_drawdown >= self.cfg.max_drawdown_stop:
            return False
        
        # Check minimum expected profit
        if expected_return < self.cfg.min_expected_profit:
            return False
        
        # Check minimum confidence
        if confidence < 0.1:
            return False
        
        return True
    
    def get_portfolio_summary(self) -> dict:
        """Get current portfolio state summary."""
        total_equity = sum(self._index_equity.values())
        total_position = sum(abs(p) for p in self._index_positions.values())
        
        # Compute portfolio-level drawdown
        total_peak = sum(self._peak_equity.values())
        portfolio_dd = (total_peak - total_equity) / total_peak if total_peak > 0 else 0.0
        
        return {
            'total_equity': total_equity,
            'total_position': total_position,
            'portfolio_drawdown': portfolio_dd,
            'index_positions': dict(self._index_positions),
            'index_equity': dict(self._index_equity),
            'exposure_utilization': total_position / self.cfg.max_portfolio_exposure,
        }
