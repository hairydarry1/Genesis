"""
model/context_builder.py — Builds MarketContext from raw features + WM predictions.

Converts the feature vector and world model output into a structured
MarketContext that the LLM strategy agent can reason about.

Usage:
    builder = ContextBuilder()
    ctx = builder.build(
        features=feature_dict,
        wm_pred_mean=mean_array,
        wm_pred_std=std_array,
        current_price=35000.0,
        timestamp="2026-08-26 14:30",
    )
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np

from model.strategy_agent import MarketContext

logger = logging.getLogger(__name__)


class ContextBuilder:
    """Builds MarketContext from raw features and predictions."""

    def build(
        self,
        features: dict[str, float],
        wm_pred_mean: np.ndarray | None = None,
        wm_pred_std: np.ndarray | None = None,
        current_price: float = 0.0,
        timestamp: str = "",
        equity: float = 10000.0,
        peak_equity: float = 10000.0,
        position: float = 0.0,
        recent_returns: list[float] | None = None,
    ) -> MarketContext:
        """
        Build a MarketContext from features and predictions.

        Parameters
        ----------
        features : dict
            Feature vector from StateBuilder (keys like "15min_return_1", etc.)
        wm_pred_mean : np.ndarray or None
            World model predicted means (horizon steps)
        wm_pred_std : np.ndarray or None
            World model predicted stds (horizon steps)
        current_price : float
            Current instrument price
        timestamp : str
            Current timestamp
        equity, peak_equity, position : float
            Portfolio state
        recent_returns : list[float] or None
            Recent trade returns for win rate calculation
        """
        ctx = MarketContext(
            current_price=current_price,
            timestamp=timestamp,
        )

        # --- World model predictions ---
        if wm_pred_mean is not None and wm_pred_std is not None:
            ctx.wm_mean_returns = wm_pred_mean.tolist()
            ctx.wm_std_returns = wm_pred_std.tolist()

            # SNR: |mean| / std of cumulative prediction
            cum_mean = float(np.sum(wm_pred_mean))
            cum_var = float(np.sum(wm_pred_std ** 2))
            ctx.wm_snr = abs(cum_mean) / max(np.sqrt(cum_var), 1e-8) * np.sign(cum_mean)

        # --- Volatility features ---
        ctx.realized_vol = self._get(features, "15min_realized_vol", 0.0)
        ctx.vol_regime = self._get(features, "15min_vol_regime", 1.0)

        # Estimate vol percentile from vol regime (rough heuristic)
        if ctx.vol_regime > 1.5:
            ctx.vol_percentile = 85.0
        elif ctx.vol_regime > 1.2:
            ctx.vol_percentile = 70.0
        elif ctx.vol_regime < 0.8:
            ctx.vol_percentile = 25.0
        else:
            ctx.vol_percentile = 50.0

        # --- Structure features ---
        ctx.nearest_structure_distance = self._get(features, "nearest_structure_distance", 0.0)
        ctx.n_structures_200 = int(self._get(features, "n_structures", 0.0))

        # Infer structure type from counts
        n_fvg = self._get(features, "n_fvg", 0.0)
        n_ob = self._get(features, "n_ob", 0.0)
        n_bos = self._get(features, "n_bos", 0.0)
        if n_bos > 0:
            ctx.nearest_structure_type = "BOS"
        elif n_ob > 0:
            ctx.nearest_structure_type = "order_block"
        elif n_fvg > 0:
            ctx.nearest_structure_type = "FVG"
        else:
            ctx.nearest_structure_type = "none"

        # --- Trend/momentum ---
        ret_15m = self._get(features, "15min_return_1", 0.0)
        ret_1h = self._get(features, "1h_return_1", 0.0)

        if ret_15m > 0.001:
            ctx.trend_15min = "up"
        elif ret_15m < -0.001:
            ctx.trend_15min = "down"
        else:
            ctx.trend_15min = "flat"

        if ret_1h > 0.001:
            ctx.trend_1h = "up"
        elif ret_1h < -0.001:
            ctx.trend_1h = "down"
        else:
            ctx.trend_1h = "flat"

        ctx.streak = int(self._get(features, "15min_up_streak", 0.0)
                         - self._get(features, "15min_down_streak", 0.0))

        # --- Price position ---
        ctx.price_z_score = self._get(features, "15min_price_z_score", 0.0)
        ctx.price_position_in_range = self._get(features, "15min_close_position_in_range", 0.5)

        # --- Risk state ---
        ctx.current_drawdown = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0

        if position > 0.01:
            ctx.current_position = "long"
            ctx.position_size = position
        elif position < -0.01:
            ctx.current_position = "short"
            ctx.position_size = abs(position)
        else:
            ctx.current_position = "flat"
            ctx.position_size = 0.0

        # --- Recent performance ---
        if recent_returns and len(recent_returns) > 0:
            ctx.recent_win_rate = sum(1 for r in recent_returns if r > 0) / len(recent_returns)
            ctx.recent_avg_return = float(np.mean(recent_returns))
            ctx.daily_pnl_pct = float(np.sum(recent_returns[-50:]))  # approx daily

        return ctx

    @staticmethod
    def _get(features: dict, key: str, default: float = 0.0) -> float:
        """Safe feature extraction."""
        val = features.get(key, default)
        if val is None or np.isnan(val) or np.isinf(val):
            return default
        return float(val)
