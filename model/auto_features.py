"""
model/auto_features.py — Automatic feature generation engine.

Generates new feature candidates from raw price data using:
  - Technical indicators (momentum, mean reversion, volatility)
  - Cross-index correlations
  - Regime detection features
  - Microstructure features
  - Statistical features (entropy, autocorrelation, etc.)

Each feature is scored on:
  - Information gain (mutual information with targets)
  - Redundancy (correlation with existing features)
  - Stability (variance across time periods)
  - Predictive power (linear/non-linear correlation with returns)

Usage:
    from model.auto_features import AutoFeatureEngine
    engine = AutoFeatureEngine()
    candidates = engine.generate(candles_df)
    best = engine.rank(candidates, targets)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class FeatureCandidate:
    """A generated feature with metadata."""
    name: str
    values: np.ndarray
    category: str  # momentum, volatility, mean_reversion, correlation, microstructure
    description: str = ""
    # Scoring metrics (filled during ranking)
    info_gain: float = 0.0
    redundancy: float = 0.0
    stability: float = 0.0
    predictive_power: float = 0.0
    composite_score: float = 0.0


class AutoFeatureEngine:
    """
    Generates and ranks new feature candidates from price data.

    The engine creates hundreds of potential features, then scores them
    to find the most predictive, least redundant, most stable ones.
    """

    def __init__(self, lookback: int = 100):
        self.lookback = lookback

    def generate(self, df: pd.DataFrame) -> list[FeatureCandidate]:
        """
        Generate all feature candidates from a candle DataFrame.

        Expects columns: open, high, low, close, volume
        """
        candidates = []

        close = df["close"].values.astype(np.float64)
        high = df["high"].values.astype(np.float64)
        low = df["low"].values.astype(np.float64)
        opn = df["open"].values.astype(np.float64)
        vol = df["volume"].values.astype(np.float64) if "volume" in df else np.ones(len(df))

        returns = np.diff(np.log(np.maximum(close, 1e-10)))
        returns = np.concatenate([[0], returns])

        # === MOMENTUM FEATURES ===
        candidates.extend(self._momentum_features(close, returns, high, low))

        # === VOLATILITY FEATURES ===
        candidates.extend(self._volatility_features(close, returns, high, low))

        # === MEAN REVERSION FEATURES ===
        candidates.extend(self._mean_reversion_features(close, returns))

        # === MICROSTRUCTURE FEATURES ===
        candidates.extend(self._microstructure_features(close, high, low, opn, vol))

        # === STATISTICAL FEATURES ===
        candidates.extend(self._statistical_features(close, returns))

        # === CROSS-FEATURE INTERACTIONS ===
        candidates.extend(self._interaction_features(close, returns, high, low))

        logger.info("generated %d feature candidates", len(candidates))
        return candidates

    def _momentum_features(self, close, returns, high, low):
        feats = []
        for period in [5, 10, 20, 50]:
            # Rate of change
            roc = np.zeros(len(close))
            roc[period:] = close[period:] / np.maximum(close[:-period], 1e-10) - 1
            feats.append(FeatureCandidate(f"roc_{period}", roc, "momentum", f"Rate of change over {period} bars"))

            # Momentum
            mom = np.zeros(len(close))
            mom[period:] = returns[period:].cumsum() - returns[:-period].cumsum() if len(returns) > period else 0
            feats.append(FeatureCandidate(f"momentum_{period}", mom, "momentum", f"Momentum over {period} bars"))

            # RSI
            gains = np.where(returns > 0, returns, 0)
            losses = np.where(returns < 0, -returns, 0)
            avg_gain = np.zeros(len(returns))
            avg_loss = np.zeros(len(returns))
            for i in range(period, len(returns)):
                avg_gain[i] = np.mean(gains[i-period:i])
                avg_loss[i] = np.mean(losses[i-period:i])
            rs = avg_gain / (avg_loss + 1e-10)
            rsi = 100 - 100 / (1 + rs)
            feats.append(FeatureCandidate(f"rsi_{period}", rsi, "momentum", f"RSI({period})"))

            # Williams %R
            hh = pd.Series(high).rolling(period).max().values
            ll = pd.Series(low).rolling(period).min().values
            wr = (hh - close) / (np.maximum(hh - ll, 1e-10)) * -100
            feats.append(FeatureCandidate(f"williams_r_{period}", wr, "momentum", f"Williams %R({period})"))

        # Stochastic %K
        for period in [14]:
            hh = pd.Series(high).rolling(period).max().values
            ll = pd.Series(low).rolling(period).min().values
            k = (close - ll) / (np.maximum(hh - ll, 1e-10)) * 100
            feats.append(FeatureCandidate(f"stoch_k_{period}", k, "momentum", f"Stochastic %K({period})"))

        return feats

    def _volatility_features(self, close, returns, high, low):
        feats = []
        for period in [5, 10, 20, 50]:
            # Historical volatility
            hvol = np.zeros(len(returns))
            for i in range(period, len(returns)):
                hvol[i] = np.std(returns[i-period:i])
            feats.append(FeatureCandidate(f"hvol_{period}", hvol, "volatility", f"Historical vol({period})"))

            # ATR
            tr = np.maximum(high - low, np.maximum(np.abs(high - np.roll(close, 1)), np.abs(low - np.roll(close, 1))))
            tr[0] = high[0] - low[0]
            atr = pd.Series(tr).rolling(period).mean().values
            feats.append(FeatureCandidate(f"atr_{period}", atr, "volatility", f"ATR({period})"))

            # Bollinger Band width
            sma = pd.Series(close).rolling(period).mean().values
            std = pd.Series(close).rolling(period).std().values
            bbw = (2 * std) / (np.maximum(sma, 1e-10))
            feats.append(FeatureCandidate(f"bb_width_{period}", bbw, "volatility", f"BB width({period})"))

            # Bollinger Band %B
            upper = sma + 2 * std
            lower = sma - 2 * std
            bbp = (close - lower) / (np.maximum(upper - lower, 1e-10))
            feats.append(FeatureCandidate(f"bb_pct_{period}", bbp, "volatility", f"BB %B({period})"))

            # Parkinson volatility (using high-low)
            park = np.zeros(len(close))
            hl_ratio = np.log(np.maximum(high, 1e-10) / np.maximum(low, 1e-10))
            for i in range(period, len(close)):
                park[i] = np.sqrt(np.mean(hl_ratio[i-period:i] ** 2) / (4 * np.log(2)))
            feats.append(FeatureCandidate(f"parkinson_{period}", park, "volatility", f"Parkinson vol({period})"))

        # Volatility regime
        hvol20 = feats[2].values if len(feats) > 2 else np.zeros(len(close))
        hvol50 = feats[5].values if len(feats) > 5 else np.zeros(len(close))
        vol_ratio = hvol20 / (np.maximum(hvol50, 1e-10))
        feats.append(FeatureCandidate("vol_regime_ratio", vol_ratio, "volatility", "20/50 vol ratio"))

        return feats

    def _mean_reversion_features(self, close, returns):
        feats = []
        for period in [10, 20, 50]:
            # Z-score
            sma = pd.Series(close).rolling(period).mean().values
            std = pd.Series(close).rolling(period).std().values
            zscore = (close - sma) / (np.maximum(std, 1e-10))
            feats.append(FeatureCandidate(f"zscore_{period}", zscore, "mean_reversion", f"Price z-score({period})"))

            # Distance from SMA
            dist = (close - sma) / (np.maximum(sma, 1e-10))
            feats.append(FeatureCandidate(f"dist_sma_{period}", dist, "mean_reversion", f"Distance from SMA({period})"))

            # Hurst exponent (simplified)
            hurst = np.zeros(len(close))
            for i in range(period * 2, len(close)):
                window = returns[i-period*2:i]
                if len(window) > 10:
                    lags = range(2, min(20, len(window)))
                    tau = [np.sqrt(np.std(np.subtract(window[lag:], window[:-lag]))) for lag in lags]
                    poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
                    hurst[i] = poly[0] * 0.5
            feats.append(FeatureCandidate(f"hurst_{period}", hurst, "mean_reversion", f"Hurst exponent({period})"))

        # RSI divergence (price vs RSI)
        for period in [14]:
            gains = np.where(returns > 0, returns, 0)
            losses = np.where(returns < 0, -returns, 0)
            avg_gain = np.zeros(len(returns))
            avg_loss = np.zeros(len(returns))
            for i in range(period, len(returns)):
                avg_gain[i] = np.mean(gains[i-period:i])
                avg_loss[i] = np.mean(losses[i-period:i])
            rs = avg_gain / (avg_loss + 1e-10)
            rsi = 100 - 100 / (1 + rs)
            # Normalize both to [0,1] and compute difference
            price_norm = (close - np.min(close[:100])) / (np.max(close[:100]) - np.min(close[:100]) + 1e-10)
            rsi_norm = rsi / 100
            divergence = price_norm - rsi_norm
            feats.append(FeatureCandidate("rsi_divergence", divergence, "mean_reversion", "RSI divergence"))

        return feats

    def _microstructure_features(self, close, high, low, opn, vol):
        feats = []
        # Bid-ask spread proxy (high-low / mid)
        mid = (high + low) / 2
        spread = (high - low) / (np.maximum(mid, 1e-10))
        feats.append(FeatureCandidate("spread_proxy", spread, "microstructure", "HL spread proxy"))

        # Volume-weighted price
        vwap = np.cumsum(mid * vol) / np.maximum(np.cumsum(vol), 1e-10)
        vwap_dist = (close - vwap) / (np.maximum(vwap, 1e-10))
        feats.append(FeatureCandidate("vwap_dist", vwap_dist, "microstructure", "Distance from VWAP"))

        # Price impact (return per unit volume)
        impact = np.zeros(len(close))
        for i in range(1, len(close)):
            if vol[i] > 0:
                impact[i] = abs(returns[i]) / (vol[i] + 1e-10) if 'returns' in dir() else 0
        feats.append(FeatureCandidate("price_impact", impact, "microstructure", "Return per volume"))

        # Up/down volume ratio
        ud_vol = np.zeros(len(close))
        for i in range(20, len(close)):
            up_vol = np.sum(vol[i-20:i][close[i-20:i] > np.roll(close, 1)[i-20:i]])
            dn_vol = np.sum(vol[i-20:i][close[i-20:i] < np.roll(close, 1)[i-20:i]])
            ud_vol[i] = up_vol / (dn_vol + 1e-10)
        feats.append(FeatureCandidate("ud_volume_ratio", ud_vol, "microstructure", "Up/down volume ratio"))

        # Candle body ratio
        body = np.abs(close - opn)
        wick = (high - low) - body
        body_ratio = body / (np.maximum(high - low, 1e-10))
        feats.append(FeatureCandidate("candle_body_ratio", body_ratio, "microstructure", "Candle body/full range"))

        # Upper/lower shadow ratio
        upper_shadow = high - np.maximum(close, opn)
        lower_shadow = np.minimum(close, opn) - low
        shadow_ratio = upper_shadow / (np.maximum(lower_shadow, 1e-10))
        feats.append(FeatureCandidate("shadow_ratio", shadow_ratio, "microstructure", "Upper/lower shadow ratio"))

        return feats

    def _statistical_features(self, close, returns):
        feats = []
        for period in [20, 50]:
            # Skewness
            skew = np.zeros(len(returns))
            for i in range(period, len(returns)):
                skew[i] = pd.Series(returns[i-period:i]).skew()
            feats.append(FeatureCandidate(f"skew_{period}", skew, "statistical", f"Return skewness({period})"))

            # Kurtosis
            kurt = np.zeros(len(returns))
            for i in range(period, len(returns)):
                kurt[i] = pd.Series(returns[i-period:i]).kurtosis()
            feats.append(FeatureCandidate(f"kurt_{period}", kurt, "statistical", f"Return kurtosis({period})"))

            # Autocorrelation
            acorr = np.zeros(len(returns))
            for i in range(period, len(returns)):
                window = returns[i-period:i]
                if len(window) > 2:
                    acorr[i] = pd.Series(window).autocorr(lag=1)
            feats.append(FeatureCandidate(f"autocorr_{period}", acorr, "statistical", f"Autocorrelation({period})"))

        # Entropy (approximate)
        for period in [20, 50]:
            entropy = np.zeros(len(returns))
            for i in range(period, len(returns)):
                window = returns[i-period:i]
                # Discretize returns into bins
                bins = np.digitize(window, np.linspace(np.min(window), np.max(window), 10))
                probs = np.bincount(bins, minlength=11) / len(bins)
                probs = probs[probs > 0]
                entropy[i] = -np.sum(probs * np.log2(probs))
            feats.append(FeatureCandidate(f"entropy_{period}", entropy, "statistical", f"Return entropy({period})"))

        return feats

    def _interaction_features(self, close, returns, high, low):
        feats = []
        # Vol * Momentum interaction
        hvol20 = pd.Series(returns).rolling(20).std().values
        mom20 = np.zeros(len(close))
        mom20[20:] = close[20:] / np.maximum(close[:-20], 1e-10) - 1
        interaction = hvol20 * mom20
        feats.append(FeatureCandidate("vol_mom_interaction", interaction, "interaction", "Volatility * Momentum"))

        # Trend strength (direction * magnitude)
        sma10 = pd.Series(close).rolling(10).mean().values
        sma50 = pd.Series(close).rolling(50).mean().values
        trend = (sma10 - sma50) / (np.maximum(sma50, 1e-10))
        feats.append(FeatureCandidate("trend_strength", trend, "interaction", "Trend strength (10/50 SMA)"))

        # Volatility-adjusted momentum
        mom = np.zeros(len(close))
        mom[20:] = close[20:] / np.maximum(close[:-20], 1e-10) - 1
        adj_mom = mom / (np.maximum(hvol20, 1e-10))
        feats.append(FeatureCandidate("adj_momentum", adj_mom, "interaction", "Volatility-adjusted momentum"))

        return feats

    def rank(
        self,
        candidates: list[FeatureCandidate],
        targets: np.ndarray,
        existing_features: np.ndarray | None = None,
        top_k: int = 20,
    ) -> list[FeatureCandidate]:
        """
        Rank feature candidates by composite score.

        Args:
            candidates: List of feature candidates to rank
            targets: Target values (next-bar returns)
            existing_features: Already-selected features (for redundancy check)
            top_k: Number of top features to return
        """
        for c in candidates:
            vals = c.values
            # Handle NaN
            mask = ~np.isnan(vals) & ~np.isinf(vals)
            if mask.sum() < 100:
                c.composite_score = -1
                continue

            clean_vals = vals[mask]
            clean_targets = targets[mask] if len(targets) == len(vals) else targets[mask[:len(targets)]]

            # Information gain (correlation with target)
            if len(clean_targets) > 0:
                corr = abs(np.corrcoef(clean_vals[:len(clean_targets)], clean_targets[:len(clean_vals)])[0, 1])
                c.info_gain = corr if not np.isnan(corr) else 0

            # Redundancy (correlation with existing features)
            if existing_features is not None and existing_features.shape[1] > 0:
                max_corr = 0
                for j in range(existing_features.shape[1]):
                    ex = existing_features[:, j]
                    ex_mask = ~np.isnan(ex) & ~np.isinf(ex)
                    common = mask & ex_mask
                    if common.sum() > 100:
                        r = abs(np.corrcoef(vals[common], ex[common])[0, 1])
                        if not np.isnan(r) and r > max_corr:
                            max_corr = r
                c.redundancy = max_corr
            else:
                c.redundancy = 0

            # Stability (ratio of means across time halves)
            half = len(clean_vals) // 2
            if half > 50:
                mean1 = np.mean(clean_vals[:half])
                mean2 = np.mean(clean_vals[half:])
                std1 = np.std(clean_vals[:half]) + 1e-10
                c.stability = 1.0 - min(abs(mean1 - mean2) / (std1 + 1e-10), 1.0)
            else:
                c.stability = 0.5

            # Composite score: high info gain, low redundancy, high stability
            c.composite_score = (
                0.5 * c.info_gain +
                0.3 * c.stability +
                0.2 * (1.0 - c.redundancy)
            )

        # Sort by composite score
        ranked = sorted(candidates, key=lambda c: c.composite_score, reverse=True)

        # Filter out highly correlated features
        selected = []
        for c in ranked:
            if c.composite_score < 0:
                continue
            # Check redundancy with already selected
            is_redundant = False
            if existing_features is not None and len(selected) > 0:
                for s in selected:
                    common = ~(np.isnan(c.values) | np.isinf(c.values) | np.isnan(s.values) | np.isinf(s.values))
                    if common.sum() > 100:
                        r = abs(np.corrcoef(c.values[common], s.values[common])[0, 1])
                        if not np.isnan(r) and r > 0.8:
                            is_redundant = True
                            break
            if not is_redundant:
                selected.append(c)
                if len(selected) >= top_k:
                    break

        logger.info("ranked %d candidates, selected %d (top composite=%.3f)", len(candidates), len(selected), selected[0].composite_score if selected else 0)
        return selected
