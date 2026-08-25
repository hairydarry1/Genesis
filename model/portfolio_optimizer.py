"""
model/portfolio_optimizer.py — Portfolio optimization for multi-index trading.

Features:
  1. Kelly criterion for optimal position sizing
  2. Dynamic hedging between correlated indices
  3. Mean-variance optimization
  4. Risk parity allocation
  5. Cross-index momentum signals

Usage:
    optimizer = PortfolioOptimizer()
    weights = optimizer.compute_weights(
        indices=['R_75', 'R_10', 'BOOM500', 'CRASH500'],
        returns=recent_returns,
        volatilities=recent_vols,
        correlations=corr_matrix,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import numpy as np


@dataclass
class OptimizerConfig:
    """Configuration for portfolio optimizer."""
    
    # Kelly criterion
    kelly_fraction: float = 0.25  # Quarter-Kelly for safety
    kelly_lookback: int = 100     # Bars to estimate win rate
    
    # Risk constraints
    max_position_size: float = 1.5
    min_position_size: float = 0.0
    max_portfolio_risk: float = 0.02  # 2% daily VaR target
    
    # Hedging
    hedge_threshold: float = 0.7  # Correlation threshold for hedging
    hedge_ratio: float = 0.3      # Hedge 30% of correlated positions
    
    # Momentum
    momentum_lookback: int = 50
    momentum_weight: float = 0.3
    
    # Rebalancing
    rebalance_threshold: float = 0.1  # Rebalance if weights drift > 10%
    min_rebalance_interval: int = 60  # Minimum minutes between rebalances


class KellyCriterion:
    """
    Kelly criterion for optimal position sizing.
    
    Kelly fraction = (win_rate * avg_win - (1-win_rate) * avg_loss) / avg_win
    
    We use quarter-Kelly (25%) for safety.
    """
    
    def __init__(self, fraction: float = 0.25, lookback: int = 100):
        self.fraction = fraction
        self.lookback = lookback
        self._trade_history: list[float] = []
    
    def update(self, trade_pnl: float):
        """Record a trade PnL."""
        self._trade_history.append(trade_pnl)
        if len(self._trade_history) > self.lookback * 2:
            self._trade_history = self._trade_history[-self.lookback * 2:]
    
    def compute_kelly_fraction(self) -> float:
        """Compute optimal Kelly fraction."""
        if len(self._trade_history) < 20:
            return self.fraction  # Default
        
        recent = self._trade_history[-self.lookback:]
        
        wins = [t for t in recent if t > 0]
        losses = [t for t in recent if t < 0]
        
        if not wins or not losses:
            return self.fraction
        
        win_rate = len(wins) / len(recent)
        avg_win = np.mean(wins)
        avg_loss = abs(np.mean(losses))
        
        if avg_win <= 0:
            return 0.0
        
        # Kelly formula
        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        
        # Apply fraction (quarter-Kelly)
        return max(0.0, min(kelly * self.fraction, 1.0))
    
    def get_stats(self) -> dict:
        """Get Kelly statistics."""
        if len(self._trade_history) < 20:
            return {'kelly_fraction': self.fraction, 'n_trades': len(self._trade_history)}
        
        recent = self._trade_history[-self.lookback:]
        wins = [t for t in recent if t > 0]
        losses = [t for t in recent if t < 0]
        
        return {
            'kelly_fraction': self.compute_kelly_fraction(),
            'win_rate': len(wins) / len(recent) if recent else 0,
            'avg_win': np.mean(wins) if wins else 0,
            'avg_loss': abs(np.mean(losses)) if losses else 0,
            'n_trades': len(self._trade_history),
        }


class DynamicHedger:
    """
    Dynamic hedging between correlated indices.
    
    When two indices are highly correlated, we can hedge
    by taking opposite positions to reduce risk.
    """
    
    def __init__(self, threshold: float = 0.7, hedge_ratio: float = 0.3):
        self.threshold = threshold
        self.hedge_ratio = hedge_ratio
        self._correlations: dict[str, dict[str, float]] = {}
    
    def update_correlations(self, correlations: dict[str, dict[str, float]]):
        """Update correlation matrix."""
        self._correlations = correlations
    
    def compute_hedge_adjustment(
        self,
        index: str,
        proposed_position: float,
        all_positions: dict[str, float],
    ) -> float:
        """
        Compute position adjustment for hedging.
        
        If highly correlated with another index that has
        an opposite position, reduce the position size.
        """
        if index not in self._correlations:
            return proposed_position
        
        adjustment = proposed_position
        
        for other_idx, other_pos in all_positions.items():
            if other_idx == index or other_pos == 0:
                continue
            
            corr = self._correlations.get(index, {}).get(other_idx, 0)
            
            # If highly correlated and opposite direction, hedge
            if abs(corr) > self.threshold:
                if np.sign(proposed_position) != np.sign(other_pos):
                    # Reduce position by hedge ratio
                    adjustment *= (1 - self.hedge_ratio * abs(corr))
        
        return adjustment
    
    def get_hedge_signals(self, all_positions: dict[str, float]) -> list[dict]:
        """Get hedging recommendations."""
        signals = []
        
        for i1, pos1 in all_positions.items():
            if pos1 == 0:
                continue
            
            for i2, pos2 in all_positions.items():
                if i2 <= i1 or pos2 == 0:
                    continue
                
                corr = self._correlations.get(i1, {}).get(i2, 0)
                
                if abs(corr) > self.threshold:
                    signals.append({
                        'indices': [i1, i2],
                        'correlation': corr,
                        'same_direction': np.sign(pos1) == np.sign(pos2),
                        'hedge_needed': np.sign(pos1) != np.sign(pos2),
                        'recommendation': 'hedge' if np.sign(pos1) != np.sign(pos2) else 'diversify',
                    })
        
        return signals


class MeanVarianceOptimizer:
    """
    Mean-variance optimization for portfolio allocation.
    
    Maximizes Sharpe ratio subject to position constraints.
    """
    
    def __init__(self, max_position: float = 1.5):
        self.max_position = max_position
    
    def optimize(
        self,
        expected_returns: np.ndarray,
        cov_matrix: np.ndarray,
        risk_aversion: float = 1.0,
    ) -> np.ndarray:
        """
        Compute optimal weights using mean-variance optimization.
        
        Parameters
        ----------
        expected_returns : array (n_indices,)
        cov_matrix : array (n_indices, n_indices)
        risk_aversion : float
            Higher = more conservative
        
        Returns
        -------
        weights : array (n_indices,)
        """
        n = len(expected_returns)
        
        if n == 0:
            return np.array([])
        
        # Simple analytical solution for unconstrained case
        # w* = (1/risk_aversion) * Σ^{-1} * μ
        try:
            inv_cov = np.linalg.inv(cov_matrix + np.eye(n) * 1e-8)
            weights = (1 / risk_aversion) * inv_cov @ expected_returns
        except np.linalg.LinAlgError:
            # Fallback: equal weight
            weights = np.ones(n) / n
        
        # Apply constraints
        weights = np.clip(weights, -self.max_position, self.max_position)
        
        # Normalize to sum to 1 (if long-only)
        if np.all(weights >= 0):
            total = np.sum(weights)
            if total > 0:
                weights = weights / total
        
        return weights


class RiskParityAllocator:
    """
    Risk parity allocation.
    
    Allocates capital such that each index contributes
    equally to total portfolio risk.
    """
    
    def __init__(self, max_position: float = 1.5):
        self.max_position = max_position
    
    def allocate(
        self,
        volatilities: dict[str, float],
        target_risk: float = 0.01,
    ) -> dict[str, float]:
        """
        Compute risk parity weights.
        
        Parameters
        ----------
        volatilities : dict
            Index -> volatility
        target_risk : float
            Target risk contribution per index
        
        Returns
        -------
        weights : dict
            Index -> weight
        """
        indices = list(volatilities.keys())
        vols = np.array([volatilities[i] for i in indices])
        
        # Inverse volatility weighting
        inv_vols = 1.0 / (vols + 1e-10)
        total = np.sum(inv_vols)
        
        weights = {}
        for i, idx in enumerate(indices):
            w = inv_vols[i] / total * self.max_position
            weights[idx] = float(w)
        
        return weights


class MomentumSignal:
    """
    Cross-index momentum signal.
    
    Detects momentum spillover between indices.
    """
    
    def __init__(self, lookback: int = 50):
        self.lookback = lookback
        self._returns: dict[str, list[float]] = {}
    
    def update(self, index: str, ret: float):
        """Update with new return."""
        if index not in self._returns:
            self._returns[index] = []
        self._returns[index].append(ret)
        if len(self._returns[index]) > self.lookback * 2:
            self._returns[index] = self._returns[index][-self.lookback * 2:]
    
    def compute_signal(self, index: str) -> float:
        """
        Compute momentum signal for an index.
        
        Returns -1 to 1:
        - Positive = upward momentum
        - Negative = downward momentum
        """
        if index not in self._returns or len(self._returns[index]) < self.lookback:
            return 0.0
        
        returns = np.array(self._returns[index][-self.lookback:])
        
        # Simple momentum: sum of recent returns
        momentum = np.sum(returns)
        
        # Normalize by volatility
        vol = np.std(returns) + 1e-10
        signal = momentum / (vol * np.sqrt(self.lookback))
        
        return float(np.clip(signal, -1, 1))
    
    def get_cross_signals(self) -> dict[str, float]:
        """Get momentum signals for all indices."""
        return {idx: self.compute_signal(idx) for idx in self._returns}


class PortfolioOptimizer:
    """
    Combined portfolio optimizer using multiple methods.
    
    Integrates:
    1. Kelly criterion for position sizing
    2. Dynamic hedging for risk reduction
    3. Mean-variance for optimal allocation
    4. Risk parity for diversification
    5. Momentum signals for timing
    """
    
    def __init__(self, config: OptimizerConfig | None = None):
        self.cfg = config or OptimizerConfig()
        
        self.kelly = KellyCriterion(self.cfg.kelly_fraction, self.cfg.kelly_lookback)
        self.hedger = DynamicHedger(self.cfg.hedge_threshold, self.cfg.hedge_ratio)
        self.mv_optimizer = MeanVarianceOptimizer(self.cfg.max_position_size)
        self.risk_parity = RiskParityAllocator(self.cfg.max_position_size)
        self.momentum = MomentumSignal(self.cfg.momentum_lookback)
        
        self._last_rebalance = 0
    
    def compute_weights(
        self,
        indices: list[str],
        expected_returns: np.ndarray | None = None,
        volatilities: dict[str, float] | None = None,
        correlations: dict[str, dict[str, float]] | None = None,
        current_positions: dict[str, float] | None = None,
        method: str = "combined",
    ) -> dict[str, float]:
        """
        Compute optimal portfolio weights.
        
        Parameters
        ----------
        indices : list[str]
            List of index names
        expected_returns : array, optional
            Expected returns per index
        volatilities : dict, optional
            Current volatilities per index
        correlations : dict, optional
            Correlation matrix
        current_positions : dict, optional
            Current positions per index
        method : str
            Optimization method: 'kelly', 'mean_variance', 'risk_parity', 'combined'
        
        Returns
        -------
        weights : dict
            Index -> weight (-1 to 1)
        """
        n = len(indices)
        
        if expected_returns is None:
            expected_returns = np.zeros(n)
        
        if volatilities is None:
            volatilities = {idx: 0.001 for idx in indices}
        
        if correlations is None:
            correlations = {i1: {i2: 1.0 if i1 == i2 else 0.0 for i2 in indices} for i1 in indices}
        
        if current_positions is None:
            current_positions = {idx: 0.0 for idx in indices}
        
        # Update hedger with correlations
        self.hedger.update_correlations(correlations)
        
        # Compute weights based on method
        if method == "kelly":
            weights = self._kelly_weights(indices, expected_returns, volatilities)
        elif method == "mean_variance":
            cov = self._build_cov_matrix(indices, volatilities, correlations)
            weights_arr = self.mv_optimizer.optimize(expected_returns, cov)
            weights = {idx: float(weights_arr[i]) for i, idx in enumerate(indices)}
        elif method == "risk_parity":
            weights = self.risk_parity.allocate(volatilities)
        else:  # combined
            weights = self._combined_weights(indices, expected_returns, volatilities, correlations)
        
        # Apply hedging adjustments
        for idx in indices:
            weights[idx] = self.hedger.compute_hedge_adjustment(
                idx, weights[idx], current_positions
            )
        
        # Apply Kelly sizing
        kelly_frac = self.kelly.compute_kelly_fraction()
        for idx in indices:
            weights[idx] *= kelly_frac
        
        # Apply momentum signals
        momentum_signals = self.momentum.get_cross_signals()
        for idx in indices:
            if idx in momentum_signals:
                momentum_adj = momentum_signals[idx] * self.cfg.momentum_weight
                weights[idx] *= (1 + momentum_adj)
        
        # Final constraints
        for idx in indices:
            weights[idx] = max(-self.cfg.max_position_size, min(self.cfg.max_position_size, weights[idx]))
            if abs(weights[idx]) < self.cfg.min_position_size:
                weights[idx] = 0.0
        
        return weights
    
    def _kelly_weights(
        self,
        indices: list[str],
        expected_returns: np.ndarray,
        volatilities: dict[str, float],
    ) -> dict[str, float]:
        """Compute Kelly-based weights."""
        weights = {}
        for i, idx in enumerate(indices):
            vol = volatilities.get(idx, 0.001)
            ret = expected_returns[i] if i < len(expected_returns) else 0
            
            # Simplified Kelly: weight = expected_return / variance
            if vol > 0:
                kelly_weight = ret / (vol ** 2)
                weights[idx] = float(np.clip(kelly_weight, -1, 1))
            else:
                weights[idx] = 0.0
        
        return weights
    
    def _combined_weights(
        self,
        indices: list[str],
        expected_returns: np.ndarray,
        volatilities: dict[str, float],
        correlations: dict[str, dict[str, float]],
    ) -> dict[str, float]:
        """Compute combined weights from multiple methods."""
        # Get weights from each method
        kelly_w = self._kelly_weights(indices, expected_returns, volatilities)
        
        cov = self._build_cov_matrix(indices, volatilities, correlations)
        mv_w_arr = self.mv_optimizer.optimize(expected_returns, cov)
        mv_w = {idx: float(mv_w_arr[i]) for i, idx in enumerate(indices)}
        
        rp_w = self.risk_parity.allocate(volatilities)
        
        # Combine: 40% Kelly + 30% Mean-Variance + 30% Risk Parity
        combined = {}
        for idx in indices:
            combined[idx] = (
                0.4 * kelly_w.get(idx, 0) +
                0.3 * mv_w.get(idx, 0) +
                0.3 * rp_w.get(idx, 0)
            )
        
        return combined
    
    def _build_cov_matrix(
        self,
        indices: list[str],
        volatilities: dict[str, float],
        correlations: dict[str, dict[str, float]],
    ) -> np.ndarray:
        """Build covariance matrix from volatilities and correlations."""
        n = len(indices)
        cov = np.zeros((n, n))
        
        for i, i1 in enumerate(indices):
            for j, i2 in enumerate(indices):
                vol_i = volatilities.get(i1, 0.001)
                vol_j = volatilities.get(i2, 0.001)
                corr = correlations.get(i1, {}).get(i2, 1.0 if i1 == i2 else 0.0)
                cov[i, j] = vol_i * vol_j * corr
        
        return cov
    
    def record_trade(self, pnl: float):
        """Record a trade for Kelly updates."""
        self.kelly.update(pnl)
    
    def update_momentum(self, index: str, ret: float):
        """Update momentum signal."""
        self.momentum.update(index, ret)
    
    def get_status(self) -> dict:
        """Get optimizer status."""
        return {
            'kelly': self.kelly.get_stats(),
            'momentum': self.momentum.get_cross_signals(),
            'hedge_signals': self.hedger.get_hedge_signals({}),
        }
