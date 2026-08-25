"""
features/advanced_features.py — Advanced feature engineering v2.

New features beyond the original 8:
  1. Market microstructure (bid-ask imbalance, trade intensity)
  2. Order flow (volume-weighted momentum, delta)
  3. Cross-index momentum (spillover effects)
  4. Volatility regime transitions
  5. Price acceleration (second derivative)
  6. Range expansion/contraction
  7. Tick distribution (skewness, kurtosis)
  8. Time-of-day patterns

Usage:
    extractor = AdvancedFeatureExtractor()
    features = extractor.extract(candles, cross_index_data)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np
import pandas as pd


@dataclass
class AdvancedFeatureConfig:
    """Configuration for advanced features."""
    
    # Microstructure
    trade_intensity_window: int = 20
    imbalance_window: int = 20
    
    # Order flow
    volume_momentum_window: int = 20
    delta_window: int = 20
    
    # Cross-index
    momentum_spillover_window: int = 50
    correlation_window: int = 100
    
    # Volatility
    vol_regime_window: int = 100
    vol_transition_window: int = 20
    
    # Price dynamics
    acceleration_window: int = 10
    range_window: int = 20
    
    # Distribution
    distribution_window: int = 50
    
    # Time patterns
    session_hours: list = None  # Trading hours
    
    def __post_init__(self):
        if self.session_hours is None:
            self.session_hours = list(range(24))


class MicrostructureFeatures:
    """
    Market microstructure features.
    
    These capture the quality of price movements and
    the balance between buyers and sellers.
    """
    
    def __init__(self, config: AdvancedFeatureConfig):
        self.cfg = config
    
    def trade_intensity(self, volumes: np.ndarray) -> float:
        """
        Trade intensity: volume per unit time.
        High intensity = active market.
        """
        if len(volumes) < self.cfg.trade_intensity_window:
            return 0.0
        
        recent = volumes[-self.cfg.trade_intensity_window:]
        return float(np.mean(recent) / (np.std(recent) + 1e-10))
    
    def bid_ask_imbalance(self, opens: np.ndarray, closes: np.ndarray, highs: np.ndarray, lows: np.ndarray) -> float:
        """
        Bid-ask imbalance proxy using candle structure.
        
        If close is near high → buyers in control
        If close is near low → sellers in control
        """
        if len(opens) < self.cfg.imbalance_window:
            return 0.0
        
        recent_o = opens[-self.cfg.imbalance_window:]
        recent_c = closes[-self.cfg.imbalance_window:]
        recent_h = highs[-self.cfg.imbalance_window:]
        recent_l = lows[-self.cfg.imbalance_window:]
        
        # Candle body position within range
        range_size = recent_h - recent_l
        range_size = np.maximum(range_size, 1e-10)
        body_position = (recent_c - recent_l) / range_size
        
        # Imbalance: positive = buyers, negative = sellers
        imbalance = 2 * np.mean(body_position) - 1
        
        return float(np.clip(imbalance, -1, 1))
    
    def price_efficiency(self, closes: np.ndarray) -> float:
        """
        Price efficiency: ratio of net price change to total path length.
        
        High efficiency = price moves directly (trending)
        Low efficiency = price oscillates (mean-reverting)
        """
        if len(closes) < self.cfg.imbalance_window:
            return 0.0
        
        recent = closes[-self.cfg.imbalance_window:]
        
        # Net change
        net_change = abs(recent[-1] - recent[0])
        
        # Total path length
        path_length = np.sum(np.abs(np.diff(recent)))
        
        if path_length < 1e-10:
            return 0.0
        
        return float(net_change / path_length)


class OrderFlowFeatures:
    """
    Order flow features.
    
    These capture the direction and strength of
    institutional order flow.
    """
    
    def __init__(self, config: AdvancedFeatureConfig):
        self.cfg = config
    
    def volume_weighted_momentum(self, returns: np.ndarray, volumes: np.ndarray) -> float:
        """
        Volume-weighted momentum.
        
        Momentum weighted by volume captures conviction.
        """
        if len(returns) < self.cfg.volume_momentum_window:
            return 0.0
        
        recent_ret = returns[-self.cfg.volume_momentum_window:]
        recent_vol = volumes[-self.cfg.volume_momentum_window:]
        
        # Volume-weighted average return
        total_vol = np.sum(recent_vol) + 1e-10
        vwm = np.sum(recent_ret * recent_vol) / total_vol
        
        return float(vwm)
    
    def volume_delta(self, opens: np.ndarray, closes: np.ndarray, volumes: np.ndarray) -> float:
        """
        Volume delta: estimate of buying vs selling volume.
        
        Up-close candles contribute to buy volume
        Down-close candles contribute to sell volume
        """
        if len(opens) < self.cfg.delta_window:
            return 0.0
        
        recent_o = opens[-self.cfg.delta_window:]
        recent_c = closes[-self.cfg.delta_window:]
        recent_v = volumes[-self.cfg.delta_window:]
        
        # Classify candles
        up_mask = recent_c > recent_o
        down_mask = recent_c < recent_o
        
        buy_volume = np.sum(recent_v[up_mask]) if np.any(up_mask) else 0
        sell_volume = np.sum(recent_v[down_mask]) if np.any(down_mask) else 0
        
        total = buy_volume + sell_volume + 1e-10
        delta = (buy_volume - sell_volume) / total
        
        return float(np.clip(delta, -1, 1))
    
    def volume_trend(self, volumes: np.ndarray) -> float:
        """
        Volume trend: increasing or decreasing volume.
        
        Rising volume + rising price = bullish
        Rising volume + falling price = bearish
        """
        if len(volumes) < self.cfg.volume_momentum_window:
            return 0.0
        
        recent = volumes[-self.cfg.volume_momentum_window:]
        x = np.arange(len(recent))
        
        # Linear regression slope
        slope = np.polyfit(x, recent, 1)[0]
        
        # Normalize by mean volume
        mean_vol = np.mean(recent) + 1e-10
        
        return float(slope / mean_vol)


class CrossIndexMomentum:
    """
    Cross-index momentum features.
    
    Captures momentum spillover between correlated indices.
    """
    
    def __init__(self, config: AdvancedFeatureConfig):
        self.cfg = config
        self._index_returns: dict[str, list[float]] = {}
    
    def update(self, index: str, ret: float):
        """Update returns for an index."""
        if index not in self._index_returns:
            self._index_returns[index] = []
        self._index_returns[index].append(ret)
        
        max_len = max(self.cfg.momentum_spillover_window, self.cfg.correlation_window) * 2
        if len(self._index_returns[index]) > max_len:
            self._index_returns[index] = self._index_returns[index][-max_len:]
    
    def momentum_spillover(self, source: str, target: str) -> float:
        """
        Momentum spillover from source to target.
        
        If source has strong momentum, does it predict
        target's next move?
        """
        if source not in self._index_returns or target not in self._index_returns:
            return 0.0
        
        src_ret = self._index_returns[source]
        tgt_ret = self._index_returns[target]
        
        min_len = min(len(src_ret), len(tgt_ret), self.cfg.momentum_spillover_window)
        if min_len < 10:
            return 0.0
        
        src = np.array(src_ret[-min_len:])
        tgt = np.array(tgt_ret[-min_len:])
        
        # Cross-correlation at lag 1
        if len(src) > 1:
            corr = np.corrcoef(src[:-1], tgt[1:])[0, 1]
            return float(corr) if not np.isnan(corr) else 0.0
        
        return 0.0
    
    def relative_strength(self, index: str) -> float:
        """
        Relative strength vs all other indices.
        
        Positive = outperforming
        Negative = underperforming
        """
        if index not in self._index_returns:
            return 0.0
        
        src_ret = self._index_returns[index]
        if len(src_ret) < self.cfg.momentum_spillover_window:
            return 0.0
        
        src_momentum = np.sum(src_ret[-self.cfg.momentum_spillover_window:])
        
        # Compare to average of other indices
        other_momenta = []
        for other_idx, other_ret in self._index_returns.items():
            if other_idx == index:
                continue
            if len(other_ret) >= self.cfg.momentum_spillover_window:
                other_momenta.append(np.sum(other_ret[-self.cfg.momentum_spillover_window:]))
        
        if not other_momenta:
            return 0.0
        
        avg_other = np.mean(other_momenta)
        
        return float(src_momentum - avg_other)
    
    def correlation_regime(self, idx1: str, idx2: str) -> float:
        """
        Correlation regime: is correlation increasing or decreasing?
        
        Rising correlation = indices moving together (risk-on/off)
        Falling correlation = indices diverging
        """
        if idx1 not in self._index_returns or idx2 not in self._index_returns:
            return 0.0
        
        r1 = self._index_returns[idx1]
        r2 = self._index_returns[idx2]
        
        min_len = min(len(r1), len(r2))
        if min_len < self.cfg.correlation_window:
            return 0.0
        
        r1 = np.array(r1[-min_len:])
        r2 = np.array(r2[-min_len:])
        
        # Split into two halves
        half = min_len // 2
        corr_early = np.corrcoef(r1[:half], r2[:half])[0, 1]
        corr_late = np.corrcoef(r1[half:], r2[half:])[0, 1]
        
        if np.isnan(corr_early) or np.isnan(corr_late):
            return 0.0
        
        return float(corr_late - corr_early)


class VolatilityRegimeFeatures:
    """
    Volatility regime transition features.
    """
    
    def __init__(self, config: AdvancedFeatureConfig):
        self.cfg = config
    
    def vol_regime(self, returns: np.ndarray) -> float:
        """
        Current volatility regime.
        
        Returns normalized volatility:
        - Low: < -0.5 (calm)
        - Normal: -0.5 to 0.5
        - High: > 0.5 (volatile)
        """
        if len(returns) < self.cfg.vol_regime_window:
            return 0.0
        
        recent = returns[-self.cfg.vol_regime_window:]
        
        current_vol = np.std(recent[-self.cfg.vol_transition_window:])
        long_term_vol = np.std(recent)
        
        if long_term_vol < 1e-10:
            return 0.0
        
        ratio = current_vol / long_term_vol
        
        # Map to -1 to 1 scale
        return float(np.clip((ratio - 1) * 2, -1, 1))
    
    def vol_transition(self, returns: np.ndarray) -> float:
        """
        Volatility transition signal.
        
        Detects when volatility is changing regime.
        """
        if len(returns) < self.cfg.vol_regime_window:
            return 0.0
        
        recent = returns[-self.cfg.vol_regime_window:]
        
        # Short-term vs medium-term vol
        short_vol = np.std(recent[-self.cfg.vol_transition_window:])
        med_vol = np.std(recent[-self.cfg.vol_transition_window * 2:-self.cfg.vol_transition_window])
        
        if med_vol < 1e-10:
            return 0.0
        
        change = (short_vol - med_vol) / med_vol
        
        return float(np.clip(change, -1, 1))
    
    def vol_mean_reversion(self, returns: np.ndarray) -> float:
        """
        Volatility mean reversion signal.
        
        High vol → expect vol to decrease (and vice versa)
        """
        if len(returns) < self.cfg.vol_regime_window:
            return 0.0
        
        recent = returns[-self.cfg.vol_regime_window:]
        current_vol = np.std(recent[-self.cfg.vol_transition_window:])
        mean_vol = np.std(recent)
        
        if mean_vol < 1e-10:
            return 0.0
        
        # Z-score of current vol
        z_score = (current_vol - mean_vol) / (np.std(recent) + 1e-10)
        
        # Mean reversion signal: if vol is high, expect it to decrease
        return float(np.clip(-z_score, -1, 1))


class PriceDynamicsFeatures:
    """
    Price dynamics features (acceleration, range, distribution).
    """
    
    def __init__(self, config: AdvancedFeatureConfig):
        self.cfg = config
    
    def price_acceleration(self, closes: np.ndarray) -> float:
        """
        Price acceleration (second derivative).
        
        Positive = accelerating upward
        Negative = accelerating downward
        """
        if len(closes) < self.cfg.acceleration_window + 2:
            return 0.0
        
        recent = closes[-self.cfg.acceleration_window - 2:]
        
        # First derivative (velocity)
        velocity = np.diff(recent)
        
        # Second derivative (acceleration)
        acceleration = np.diff(velocity)
        
        return float(np.mean(acceleration) / (np.std(closes[-self.cfg.acceleration_window:]) + 1e-10))
    
    def range_expansion(self, highs: np.ndarray, lows: np.ndarray) -> float:
        """
        Range expansion/contraction.
        
        Expanding range = trending
        Contracting range = consolidating
        """
        if len(highs) < self.cfg.range_window * 2:
            return 0.0
        
        recent_range = highs[-self.cfg.range_window:] - lows[-self.cfg.range_window:]
        prev_range = highs[-self.cfg.range_window * 2:-self.cfg.range_window] - lows[-self.cfg.range_window * 2:-self.cfg.range_window]
        
        mean_recent = np.mean(recent_range)
        mean_prev = np.mean(prev_range)
        
        if mean_prev < 1e-10:
            return 0.0
        
        expansion = (mean_recent - mean_prev) / mean_prev
        
        return float(np.clip(expansion, -1, 1))
    
    def tick_distribution(self, returns: np.ndarray) -> tuple[float, float]:
        """
        Return distribution (skewness, kurtosis).
        
        Skewness: negative = crash risk
        Kurtosis: high = fat tails
        """
        if len(returns) < self.cfg.distribution_window:
            return 0.0, 0.0
        
        recent = returns[-self.cfg.distribution_window:]
        
        skew = float(np.mean(((recent - np.mean(recent)) / (np.std(recent) + 1e-10)) ** 3))
        kurt = float(np.mean(((recent - np.mean(recent)) / (np.std(recent) + 1e-10)) ** 4) - 3)
        
        return skew, kurt
    
    def price_position_in_range(self, close: float, high: float, low: float) -> float:
        """
        Price position in recent range.
        
        0 = at low, 1 = at high, 0.5 = middle
        """
        range_size = high - low
        if range_size < 1e-10:
            return 0.5
        
        return float((close - low) / range_size)


class AdvancedFeatureExtractor:
    """
    Combined advanced feature extractor.
    
    Produces a feature vector from candle data.
    """
    
    def __init__(self, config: AdvancedFeatureConfig | None = None):
        self.cfg = config or AdvancedFeatureConfig()
        
        self.microstructure = MicrostructureFeatures(self.cfg)
        self.order_flow = OrderFlowFeatures(self.cfg)
        self.cross_momentum = CrossIndexMomentum(self.cfg)
        self.vol_regime = VolatilityRegimeFeatures(self.cfg)
        self.price_dynamics = PriceDynamicsFeatures(self.cfg)
    
    def extract(
        self,
        candles: pd.DataFrame,
        cross_index_returns: dict[str, float] | None = None,
    ) -> dict[str, float]:
        """
        Extract all advanced features from candle data.
        
        Parameters
        ----------
        candles : DataFrame with columns: open, high, low, close, volume
        cross_index_returns : dict, optional
            Recent returns from other indices for cross-momentum
        
        Returns
        -------
        features : dict
            Feature name -> value
        """
        if len(candles) < 50:
            return self._default_features()
        
        opens = candles['open'].values
        highs = candles['high'].values
        lows = candles['low'].values
        closes = candles['close'].values
        volumes = candles['volume'].values
        
        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        
        # Update cross-index momentum
        if cross_index_returns:
            for idx, ret in cross_index_returns.items():
                self.cross_momentum.update(idx, ret)
        
        features = {}
        
        # Microstructure
        features['adv_trade_intensity'] = self.microstructure.trade_intensity(volumes)
        features['adv_imbalance'] = self.microstructure.bid_ask_imbalance(opens, closes, highs, lows)
        features['adv_price_efficiency'] = self.microstructure.price_efficiency(closes)
        
        # Order flow
        features['adv_vwap_momentum'] = self.order_flow.volume_weighted_momentum(returns, volumes)
        features['adv_volume_delta'] = self.order_flow.volume_delta(opens, closes, volumes)
        features['adv_volume_trend'] = self.order_flow.volume_trend(volumes)
        
        # Cross-index momentum
        if cross_index_returns:
            for idx in cross_index_returns:
                features[f'adv_momentum_spillover_{idx}'] = self.cross_momentum.momentum_spillover(idx, 'self')
            features['adv_relative_strength'] = self.cross_momentum.relative_strength('self')
        else:
            features['adv_momentum_spillover'] = 0.0
            features['adv_relative_strength'] = 0.0
        
        # Volatility regime
        features['adv_vol_regime'] = self.vol_regime.vol_regime(returns)
        features['adv_vol_transition'] = self.vol_regime.vol_transition(returns)
        features['adv_vol_mean_reversion'] = self.vol_regime.vol_mean_reversion(returns)
        
        # Price dynamics
        features['adv_acceleration'] = self.price_dynamics.price_acceleration(closes)
        features['adv_range_expansion'] = self.price_dynamics.range_expansion(highs, lows)
        skew, kurt = self.price_dynamics.tick_distribution(returns)
        features['adv_skewness'] = skew
        features['adv_kurtosis'] = kurt
        features['adv_price_position'] = self.price_dynamics.price_position_in_range(
            closes[-1], np.max(highs[-20:]), np.min(lows[-20:])
        )
        
        return features
    
    def _default_features(self) -> dict[str, float]:
        """Default features when insufficient data."""
        return {
            'adv_trade_intensity': 0.0,
            'adv_imbalance': 0.0,
            'adv_price_efficiency': 0.0,
            'adv_vwap_momentum': 0.0,
            'adv_volume_delta': 0.0,
            'adv_volume_trend': 0.0,
            'adv_momentum_spillover': 0.0,
            'adv_relative_strength': 0.0,
            'adv_vol_regime': 0.0,
            'adv_vol_transition': 0.0,
            'adv_vol_mean_reversion': 0.0,
            'adv_acceleration': 0.0,
            'adv_range_expansion': 0.0,
            'adv_skewness': 0.0,
            'adv_kurtosis': 0.0,
            'adv_price_position': 0.0,
        }
    
    def get_feature_names(self) -> list[str]:
        """Get list of all feature names."""
        return list(self._default_features().keys())
    
    def get_feature_count(self) -> int:
        """Get number of features."""
        return len(self.get_feature_names())
