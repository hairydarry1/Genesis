"""
model/regime.py — Market regime detection + Kelly sizing + session filters.

Components:
  1. RegimeDetector — identifies calm/trending/spike regimes
  2. KellySizer — dynamic position sizing based on actual edge
  3. SessionFilter — adapts strategy to time of day
  4. VolatilityFilter — stops trading when vol > 2σ

Usage:
    from model.regime import RegimeDetector, KellySizer, SessionFilter, VolatilityFilter

    regime_det = RegimeDetector()
    kelly = KellySizer()
    session = SessionFilter()
    vol_filter = VolatilityFilter()

    regime = regime_det.detect(closes)
    size = kelly.compute_size(win_rate=0.52, avg_win=0.003, avg_loss=0.001)
    can_trade = session.can_trade(now)
    vol_ok = vol_filter.check(closes)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Regime Detection
# ---------------------------------------------------------------------------
class Regime(Enum):
    CALM = "calm"
    TRENDING = "trending"
    SPIKE = "spike"
    UNKNOWN = "unknown"


@dataclass
class RegimeState:
    regime: Regime
    confidence: float  # 0-1
    vol_percentile: float  # 0-100
    trend_strength: float  # -1 to +1
    should_trade: bool
    reason: str


class RegimeDetector:
    """
    Detects market regime using volatility clustering.

    Method:
      1. Compute rolling volatility (20-bar window)
      2. Compute volatility percentile (where are we relative to history?)
      3. Compute trend strength (EMA slope)
      4. Classify regime based on vol percentile + trend

    Thresholds:
      - Calm: vol_percentile < 30, |trend| < 0.3
      - Trending: vol_percentile 30-70, |trend| > 0.3
      - Spike: vol_percentile > 70 OR vol > 3σ
    """

    def __init__(
        self,
        vol_window: int = 20,
        lookback: int = 500,
        calm_threshold: float = 0.30,
        spike_threshold: float = 0.70,
        trend_threshold: float = 0.3,
    ):
        self.vol_window = vol_window
        self.lookback = lookback
        self.calm_threshold = calm_threshold
        self.spike_threshold = spike_threshold
        self.trend_threshold = trend_threshold
        self._vol_history: list[float] = []

    def detect(self, closes: np.ndarray) -> RegimeState:
        """Detect current regime from price series."""
        if len(closes) < self.vol_window + 10:
            return RegimeState(
                regime=Regime.UNKNOWN, confidence=0, vol_percentile=50,
                trend_strength=0, should_trade=True, reason="insufficient_data",
            )

        # Rolling volatility
        returns = np.diff(np.log(np.maximum(closes[-self.lookback:], 1e-10)))
        vol = float(np.std(returns[-self.vol_window:]))
        self._vol_history.append(vol)
        if len(self._vol_history) > 1000:
            self._vol_history = self._vol_history[-1000:]

        # Volatility percentile
        vol_arr = np.array(self._vol_history)
        vol_percentile = float(np.mean(vol_arr < vol) * 100)

        # Volatility ratio (current vs long-term)
        long_vol = float(np.mean(vol_arr[-200:])) if len(vol_arr) > 200 else float(np.mean(vol_arr))
        vol_ratio = vol / (long_vol + 1e-10)

        # Trend strength (EMA slope)
        if len(closes) >= 50:
            ema_fast = self._ema(closes, 20)
            ema_slow = self._ema(closes, 50)
            trend_strength = float((ema_fast[-1] - ema_slow[-1]) / (ema_slow[-1] + 1e-10))
            trend_strength = np.clip(trend_strength, -1, 1)
        else:
            trend_strength = 0.0

        # Classify regime
        if vol_percentile > self.spike_threshold * 100 or vol_ratio > 3.0:
            regime = Regime.SPIKE
            should_trade = False
            reason = f"vol_spike (pct={vol_percentile:.0f}, ratio={vol_ratio:.1f}x)"
            confidence = min(vol_percentile / 100, 1.0)
        elif vol_percentile < self.calm_threshold * 100 and abs(trend_strength) < self.trend_threshold:
            regime = Regime.CALM
            should_trade = True
            reason = f"calm (pct={vol_percentile:.0f}, trend={trend_strength:+.2f})"
            confidence = 1.0 - vol_percentile / 100
        elif abs(trend_strength) >= self.trend_threshold:
            regime = Regime.TRENDING
            should_trade = True
            reason = f"trending (pct={vol_percentile:.0f}, trend={trend_strength:+.2f})"
            confidence = abs(trend_strength)
        else:
            regime = Regime.CALM
            should_trade = True
            reason = f"normal (pct={vol_percentile:.0f}, ratio={vol_ratio:.1f}x)"
            confidence = 0.5

        return RegimeState(
            regime=regime,
            confidence=confidence,
            vol_percentile=vol_percentile,
            trend_strength=trend_strength,
            should_trade=should_trade,
            reason=reason,
        )

    @staticmethod
    def _ema(data: np.ndarray, period: int) -> np.ndarray:
        """Exponential moving average."""
        alpha = 2 / (period + 1)
        ema = np.zeros_like(data, dtype=float)
        ema[0] = data[0]
        for i in range(1, len(data)):
            ema[i] = alpha * data[i] + (1 - alpha) * ema[i - 1]
        return ema


# ---------------------------------------------------------------------------
# Kelly Criterion Position Sizing
# ---------------------------------------------------------------------------
class KellySizer:
    """
    Dynamic position sizing using Kelly Criterion.

    f* = (b * p - q) / b

    Where:
      b = avg_win / avg_loss (reward-to-risk ratio)
      p = win probability
      q = 1 - p

    We use fractional Kelly (default 25%) for safety.
    """

    def __init__(
        self,
        kelly_fraction: float = 0.25,  # Use 25% of Kelly (conservative)
        min_size: float = 0.05,        # Minimum 5% of equity
        max_size: float = 0.50,        # Maximum 50% of equity
        min_trades: int = 20,          # Need 20+ trades for reliable estimate
    ):
        self.kelly_fraction = kelly_fraction
        self.min_size = min_size
        self.max_size = max_size
        self.min_trades = min_trades

        # Running statistics
        self.total_trades = 0
        self.wins = 0
        self.total_win_pnl = 0.0
        self.total_loss_pnl = 0.0

    def record_trade(self, pnl: float):
        """Record a completed trade."""
        self.total_trades += 1
        if pnl > 0:
            self.wins += 1
            self.total_win_pnl += pnl
        else:
            self.total_loss_pnl += abs(pnl)

    @property
    def win_rate(self) -> float:
        return self.wins / max(self.total_trades, 1)

    @property
    def avg_win(self) -> float:
        return self.total_win_pnl / max(self.wins, 1)

    @property
    def avg_loss(self) -> float:
        losses = self.total_trades - self.wins
        return self.total_loss_pnl / max(losses, 1)

    @property
    def profit_factor(self) -> float:
        return self.total_win_pnl / max(self.total_loss_pnl, 1e-10)

    def compute_kelly(self) -> float:
        """Compute raw Kelly fraction."""
        b = self.avg_win / max(self.avg_loss, 1e-10)
        p = self.win_rate
        q = 1 - p
        kelly = (b * p - q) / b
        return max(kelly, 0)  # Never negative (that means don't trade)

    def compute_size(self, win_rate: float | None = None, avg_win: float | None = None,
                     avg_loss: float | None = None) -> float:
        """
        Compute position size as fraction of equity.
        Uses running statistics if available, otherwise uses provided values.
        """
        if self.total_trades >= self.min_trades:
            # Use running statistics
            kelly = self.compute_kelly()
        elif win_rate is not None and avg_win is not None and avg_loss is not None:
            # Use provided values (from backtest)
            b = avg_win / max(avg_loss, 1e-10)
            p = win_rate
            q = 1 - p
            kelly = max((b * p - q) / b, 0)
        else:
            # Default conservative size
            kelly = 0.10

        # Apply fractional Kelly
        size = kelly * self.kelly_fraction

        # Clamp
        size = max(self.min_size, min(self.max_size, size))

        return size

    def get_status(self) -> dict:
        """Get current Kelly sizing status."""
        return {
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "avg_win": self.avg_win,
            "avg_loss": self.avg_loss,
            "profit_factor": self.profit_factor,
            "kelly_raw": self.compute_kelly() if self.total_trades >= self.min_trades else 0,
            "kelly_fraction": self.kelly_fraction,
            "position_size": self.compute_size(),
        }


# ---------------------------------------------------------------------------
# Session Filter
# ---------------------------------------------------------------------------
class SessionFilter:
    """
    Adapts trading to time of day.

    Sessions (UTC):
      Asian:  00:00 - 08:00  (low vol, mean reversion)
      Europe: 08:00 - 16:00  (higher vol, trending)
      US:     13:00 - 21:00  (highest vol, spikes)
      Night:  21:00 - 00:00  (low vol, thin liquidity)

    For synthetic indices, volatility varies by session.
    """

    def __init__(
        self,
        trade_asian: bool = True,
        trade_europe: bool = True,
        trade_us: bool = True,
        trade_night: bool = False,  # Avoid thin liquidity
    ):
        self.sessions = {
            "asian": {"start": 0, "end": 8, "active": trade_asian},
            "europe": {"start": 8, "end": 16, "active": trade_europe},
            "us": {"start": 13, "end": 21, "active": trade_us},
            "night": {"start": 21, "end": 24, "active": trade_night},
        }

    def get_session(self, utc_hour: int) -> str:
        """Get current session name."""
        if 0 <= utc_hour < 8:
            return "asian"
        elif 8 <= utc_hour < 13:
            return "europe_early"
        elif 13 <= utc_hour < 16:
            return "overlap"  # Europe + US
        elif 16 <= utc_hour < 21:
            return "us"
        else:
            return "night"

    def can_trade(self, now: datetime | None = None) -> tuple[bool, str]:
        """Check if we should trade at current time."""
        if now is None:
            now = datetime.now(timezone.utc)
        hour = now.hour

        session = self.get_session(hour)

        # Check if session is active
        if session == "night":
            return False, "night_liquidity"
        elif session == "overlap":
            return True, "overlap_high_vol"
        elif session == "us":
            return self.sessions["us"]["active"], "us_session"
        elif session.startswith("europe"):
            return self.sessions["europe"]["active"], "europe_session"
        elif session == "asian":
            return self.sessions["asian"]["active"], "asian_session"

        return True, "unknown_session"

    def get_session_config(self, session: str) -> dict:
        """Get recommended trading config for session."""
        configs = {
            "asian": {"snr_mult": 0.8, "tp_mult": 0.8, "sl_mult": 1.2},  # Tighter TP, wider SL
            "europe_early": {"snr_mult": 1.0, "tp_mult": 1.0, "sl_mult": 1.0},
            "overlap": {"snr_mult": 1.2, "tp_mult": 1.2, "sl_mult": 0.8},  # Higher SNR, wider TP
            "us": {"snr_mult": 1.1, "tp_mult": 1.1, "sl_mult": 0.9},
            "night": {"snr_mult": 0.0, "tp_mult": 0.0, "sl_mult": 0.0},  # No trading
        }
        return configs.get(session, configs["europe_early"])


# ---------------------------------------------------------------------------
# Volatility Filter
# ---------------------------------------------------------------------------
class VolatilityFilter:
    """
    Stops trading when volatility exceeds threshold.

    Method:
      1. Compute rolling volatility
      2. Compare to long-term average
      3. Block trading if vol > threshold (default: 2σ)
    """

    def __init__(
        self,
        short_window: int = 20,
        long_window: int = 200,
        threshold: float = 2.0,  # Block if vol > 2x long-term avg
        cooldown_bars: int = 50,  # Wait 50 bars after vol spike
    ):
        self.short_window = short_window
        self.long_window = long_window
        self.threshold = threshold
        self.cooldown_bars = cooldown_bars
        self._last_spike_bar = -999
        self._bar_count = 0

    def check(self, closes: np.ndarray, current_bar: int = -1) -> tuple[bool, str]:
        """
        Check if volatility is acceptable.
        Returns (can_trade, reason).
        """
        if len(closes) < self.long_window:
            return True, "insufficient_data"

        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        short_vol = float(np.std(returns[-self.short_window:]))
        long_vol = float(np.std(returns[-self.long_window:]))

        vol_ratio = short_vol / (long_vol + 1e-10)

        # Check cooldown
        if current_bar - self._last_spike_bar < self.cooldown_bars:
            remaining = self.cooldown_bars - (current_bar - self._last_spike_bar)
            return False, f"cooldown ({remaining} bars remaining)"

        # Check threshold
        if vol_ratio > self.threshold:
            self._last_spike_bar = current_bar
            return False, f"vol_spike ({vol_ratio:.1f}x avg)"

        return True, f"vol_ok ({vol_ratio:.1f}x avg)"

    def get_vol_regime(self, closes: np.ndarray) -> str:
        """Get current volatility regime name."""
        if len(closes) < self.long_window:
            return "unknown"

        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        short_vol = float(np.std(returns[-self.short_window:]))
        long_vol = float(np.std(returns[-self.long_window:]))
        ratio = short_vol / (long_vol + 1e-10)

        if ratio < 0.7:
            return "low_vol"
        elif ratio < 1.3:
            return "normal"
        elif ratio < 2.0:
            return "elevated"
        else:
            return "extreme"
