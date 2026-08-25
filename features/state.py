"""
features/state.py — Feature / state representation for the world model.

Single source of truth for "what the model sees."

build_state(timestamp) -> feature_vector

Features are grouped into:
  1. Multi-timeframe encoding (1m, 15m, 1h)
  2. Volatility features (realized vol, vol-of-vol, time-since-last-large-move)
  3. Statistical fingerprint features (streak counters, mean-reversion indicators)
  4. Structural context features (from features/structures.py)

Usage:
    from features.state import StateBuilder

    builder = StateBuilder(ticks_df)
    vec = builder.build_state(timestamp)
    # vec is a dict of named float features — ready for the world model
"""

from __future__ import annotations

from typing import Any

import bisect

import numpy as np
import pandas as pd
import structlog

from data.resample import resample_ticks, resample_bars
from features.structures import (
    StructureInstance,
    detect_structures,
    compute_atr,
    compute_premium_discount,
    detect_swing_points,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

# Default multi-timeframe resolutions
DEFAULT_TIMEFRAMES = ["1min", "15min", "1h"]

# Volatility feature parameters
VOL_LOOKBACK = 100         # bars for rolling realized vol
VOV_LOOKBACK = 50          # bars for vol-of-vol
LARGE_MOVE_ATR = 2.0       # move > this * ATR = "large move"
STREAK_MAX = 50            # cap streak counter

# Fingerprint feature parameters
SPIKE_ATR_MULT = 3.0       # move > this * ATR = spike (Boom/Crash signature)
MEAN_REV_LOOKBACK = 50     # bars for mean-reversion computation
PERSISTENCE_LOOKBACK = 50  # bars for persistence (autocorrelation) computation


# ---------------------------------------------------------------------------
# StateBuilder
# ---------------------------------------------------------------------------

class StateBuilder:
    """
    Builds the feature vector for the world model from raw tick data.

    Parameters
    ----------
    ticks : pd.DataFrame
        Raw tick data with columns: ['timestamp', 'price'].
    timeframes : list[str], optional
        Multi-timeframe resolutions to build.
    atr_period : int
        ATR period for structure detection.
    swing_lookback : int
        Swing-point detection lookback.
    instrument_cfg : dict, optional
        Instrument-specific quality thresholds for structure detection.
    """

    def __init__(
        self,
        ticks: pd.DataFrame,
        timeframes: list[str] | None = None,
        atr_period: int = 14,
        swing_lookback: int = 5,
        instrument_cfg: dict[str, Any] | None = None,
        structures_path: str | None = None,
    ) -> None:
        self.ticks = ticks.copy()
        self.timeframes = timeframes or DEFAULT_TIMEFRAMES
        self.atr_period = atr_period
        self.swing_lookback = swing_lookback
        self.instrument_cfg = instrument_cfg or {}

        # Build bar series at each timeframe
        self._bars: dict[str, pd.DataFrame] = {}
        self._atr: dict[str, pd.Series] = {}
        self._precompute_bars()

        # Structure detection: load from cache or detect lazily
        self._structures: list[StructureInstance] = []
        self._structure_cache: dict[int, list[StructureInstance]] = {}
        self._structure_window = 5000  # bars per structure detection window
        if structures_path:
            self._load_structures(structures_path)

        # Build index for fast timestamp lookup
        self._build_timestamp_index()

        logger.info(
            "state_builder.init",
            n_ticks=len(ticks),
            timeframes=self.timeframes,
            bars={k: len(v) for k, v in self._bars.items()},
            note="structures detected lazily",
        )

    def _precompute_bars(self) -> None:
        """Resample input data into all timeframes.
        
        Handles both raw ticks (with 'price' column) and pre-formed
        candle data (with 'open'/'close' columns).
        """
        is_candles = "open" in self.ticks.columns and "close" in self.ticks.columns
        
        if is_candles:
            # Input is already OHLC bars — assign the finest timeframe directly,
            # resample coarser timeframes from it
            self._bars[self.timeframes[0]] = self.ticks.copy()
            self._atr[self.timeframes[0]] = compute_atr(self.ticks, period=self.atr_period)
            for tf in self.timeframes[1:]:
                self._bars[tf] = resample_bars(self.ticks, freq=tf)
                self._atr[tf] = compute_atr(self._bars[tf], period=self.atr_period)
        else:
            # Input is raw ticks — resample into all timeframes
            for tf in self.timeframes:
                self._bars[tf] = resample_ticks(self.ticks, freq=tf)
                self._atr[tf] = compute_atr(self._bars[tf], period=self.atr_period)

    def _load_structures(self, path: str) -> None:
        """Load pre-computed structures from a Parquet file.
        
        Uses batch conversion for speed on large datasets.
        """
        import time as _time
        t0 = _time.time()
        df = pd.read_parquet(path, engine="pyarrow")
        
        # Convert columns to numpy arrays once (avoids repeated indexing)
        stypes = df["stype"].values
        zone_highs = df["zone_high"].values.astype(float)
        zone_lows = df["zone_low"].values.astype(float)
        indices = df["index"].values.astype(int)
        timestamps = pd.to_datetime(df["timestamp"]).values
        directions = df["direction"].values
        size_atrs = df["size_ATR"].values.astype(float)
        age_bars = df["age_bars"].values.astype(int)
        
        self._structures = [
            StructureInstance(
                stype=stypes[i],
                zone_high=zone_highs[i],
                zone_low=zone_lows[i],
                index=indices[i],
                timestamp=pd.Timestamp(timestamps[i]),
                direction=directions[i],
                size_ATR=size_atrs[i],
                age_bars=age_bars[i],
            )
            for i in range(len(df))
        ]
        elapsed = _time.time() - t0
        logger.info(
            "state_builder.structures_loaded",
            path=path,
            n_structures=len(self._structures),
            elapsed_s=round(elapsed, 2),
        )

    def _get_structures_up_to(self, bar_idx: int) -> list[StructureInstance]:
        """
        Get structures up to the given bar index.
        
        If pre-loaded structures exist (from Parquet), use those.
        Otherwise, detect lazily in rolling windows.
        """
        # Fast path: pre-loaded structures from Parquet
        if self._structures:
            # Binary search for efficiency on sorted list
            indices = [s.index for s in self._structures]
            pos = bisect.bisect_right(indices, bar_idx)
            return self._structures[:pos]
        
        # Slow path: detect lazily in rolling windows
        if not self.timeframes:
            return []
        
        finest = self.timeframes[0]
        bars = self._bars[finest]
        n_bars = len(bars)
        
        if bar_idx >= n_bars:
            bar_idx = n_bars - 1
        if bar_idx < 0:
            return []
        
        # Determine which window(s) we need
        window_start = max(0, bar_idx - self._structure_window + 1)
        window_end = min(bar_idx + 1, n_bars)
        
        # Check if we already have structures for this range
        cache_key = window_start // self._structure_window
        if cache_key not in self._structure_cache:
            # Detect structures on this window slice
            window_bars = bars.iloc[window_start:window_end].copy()
            window_bars = window_bars.reset_index(drop=True)
            
            structures = detect_structures(
                window_bars,
                atr_period=self.atr_period,
                swing_lookback=self.swing_lookback,
                instrument_cfg=self.instrument_cfg,
            )
            
            # Adjust indices back to global bar indices
            for s in structures:
                s.index += window_start
            
            self._structure_cache[cache_key] = structures
        
        # Collect all structures up to bar_idx from cached windows
        all_structures = []
        for key in sorted(self._structure_cache.keys()):
            for s in self._structure_cache[key]:
                if s.index <= bar_idx:
                    all_structures.append(s)
        
        return all_structures

    @staticmethod
    def _normalize_ts(ts: pd.Timestamp) -> pd.Timestamp:
        """Strip timezone info for consistent comparison."""
        if ts.tzinfo is not None:
            return ts.tz_localize(None)
        return ts

    def _build_timestamp_index(self) -> None:
        """Build a lookup from timestamp → bar index for each timeframe."""
        self._ts_index: dict[str, dict] = {}
        for tf in self.timeframes:
            # Normalize to tz-naive for consistent comparison
            ts = self._bars[tf]["timestamp"].apply(self._normalize_ts)
            self._ts_index[tf] = {
                "timestamps": ts.values,
                "indices": np.arange(len(ts)),
            }

    def _find_bar_index(self, tf: str, timestamp: pd.Timestamp) -> int | None:
        """Find the bar index for a given timestamp in a timeframe."""
        ts_array = self._ts_index[tf]["timestamps"]
        idx_array = self._ts_index[tf]["indices"]

        # Normalize incoming timestamp for comparison
        norm_ts = self._normalize_ts(timestamp)

        # Find the last bar whose timestamp <= the target
        mask = ts_array <= norm_ts
        if not mask.any():
            return None
        return int(idx_array[mask][-1])

    # ------------------------------------------------------------------
    # Enhanced feature extraction (engineered interactions)
    # ------------------------------------------------------------------

    ENHANCED_LOOKBACKS = [20, 50, 100]  # for multi-scale vol and z-scores

    def _extract_enhanced_features(
        self, tf: str, bar_idx: int
    ) -> dict[str, float]:
        """
        Engineered features based on importance analysis insights.

        Categories:
        1. Interaction features (return × streak, return × vol)
        2. Multi-scale volatility ratios
        3. Price position features (position in range, z-score)
        4. Momentum features (weighted returns, acceleration)
        """
        bars = self._bars[tf]
        atr = self._atr[tf]
        f: dict[str, float] = {}

        # Defaults
        f[f"{tf}_return_x_streak"] = 0.0
        f[f"{tf}_return_x_vol_regime"] = 0.0
        f[f"{tf}_range_x_streak"] = 0.0
        f[f"{tf}_vol_short_long_ratio"] = 1.0
        f[f"{tf}_vol_breakout"] = 0.0
        f[f"{tf}_close_position_in_range"] = 0.5
        f[f"{tf}_price_z_score"] = 0.0
        f[f"{tf}_momentum_score"] = 0.0
        f[f"{tf}_momentum_acceleration"] = 0.0

        if bar_idx < 20:
            return f

        close_arr = bars["close"].values[:bar_idx + 1].astype(np.float64)
        high_arr = bars["high"].values[:bar_idx + 1].astype(np.float64)
        low_arr = bars["low"].values[:bar_idx + 1].astype(np.float64)
        n = len(close_arr)

        # Log returns
        log_ret = np.diff(np.log(np.maximum(close_arr, 1e-10)))
        current_ret = log_ret[-1] if len(log_ret) > 0 else 0.0

        # --- Streak (reuse existing logic) ---
        up_streak = 0
        down_streak = 0
        for i in range(n - 1, 0, -1):
            if close_arr[i] > close_arr[i - 1]:
                up_streak += 1
                if down_streak > 0:
                    break
            elif close_arr[i] < close_arr[i - 1]:
                down_streak += 1
                if up_streak > 0:
                    break
            else:
                break
        streak_direction = 1.0 if up_streak > 0 else (-1.0 if down_streak > 0 else 0.0)
        streak_strength = min(max(up_streak, down_streak), STREAK_MAX)

        # --- Vol regime (short/long) ---
        if len(log_ret) >= VOL_LOOKBACK:
            long_vol = float(np.std(log_ret[-VOL_LOOKBACK:]))
            short_vol = float(np.std(log_ret[-20:])) if len(log_ret) >= 20 else long_vol
            vol_regime = short_vol / long_vol if long_vol > 0 else 1.0
        else:
            vol_regime = 1.0

        # 1) Interaction: return × streak
        f[f"{tf}_return_x_streak"] = current_ret * streak_direction * min(streak_strength, 10.0)

        # 2) Interaction: return × vol regime
        f[f"{tf}_return_x_vol_regime"] = current_ret * vol_regime

        # 3) Interaction: range × streak
        atr_val = float(atr.iloc[bar_idx]) if bar_idx < len(atr) else 1.0
        current_range = float(high_arr[-1] - low_arr[-1])
        f[f"{tf}_range_x_streak"] = (current_range / atr_val if atr_val > 0 else 0.0) * streak_direction

        # 4) Multi-scale vol ratio (20-bar / 100-bar)
        if len(log_ret) >= 100:
            vol_100 = float(np.std(log_ret[-100:]))
            vol_20 = float(np.std(log_ret[-20:])) if len(log_ret) >= 20 else vol_100
            f[f"{tf}_vol_short_long_ratio"] = vol_20 / vol_100 if vol_100 > 0 else 1.0
        elif len(log_ret) >= 20:
            vol_long = float(np.std(log_ret))
            vol_short = float(np.std(log_ret[-20:]))
            f[f"{tf}_vol_short_long_ratio"] = vol_short / vol_long if vol_long > 0 else 1.0

        # 5) Vol breakout: current bar's |return| vs recent vol
        if len(log_ret) >= 20:
            recent_vol = float(np.std(log_ret[-20:]))
            f[f"{tf}_vol_breakout"] = abs(current_ret) / recent_vol if recent_vol > 0 else 0.0

        # 6) Price position in recent range (0 = at low, 1 = at high)
        lookback = min(50, n)
        recent_high = float(np.max(high_arr[-lookback:]))
        recent_low = float(np.min(low_arr[-lookback:]))
        if recent_high > recent_low:
            f[f"{tf}_close_position_in_range"] = (
                (close_arr[-1] - recent_low) / (recent_high - recent_low)
            )

        # 7) Price z-score (how far from mean in std units)
        if n >= 50:
            mean_price = float(np.mean(close_arr[-50:]))
            std_price = float(np.std(close_arr[-50:]))
            f[f"{tf}_price_z_score"] = (
                (close_arr[-1] - mean_price) / std_price if std_price > 0 else 0.0
            )

        # 8) Momentum score (weighted sum of recent returns)
        if len(log_ret) >= 20:
            # Exponentially weighted: recent returns matter more
            weights = np.exp(-np.linspace(0, 2, min(20, len(log_ret))))
            weights = weights / weights.sum()
            recent = log_ret[-len(weights):]
            f[f"{tf}_momentum_score"] = float(np.dot(recent, weights))

        # 9) Momentum acceleration (change in momentum)
        if len(log_ret) >= 40:
            weights = np.exp(-np.linspace(0, 2, 20))
            weights = weights / weights.sum()
            recent_now = log_ret[-20:]
            recent_prev = log_ret[-40:-20]
            momentum_now = np.dot(recent_now, weights)
            momentum_prev = np.dot(recent_prev, weights)
            f[f"{tf}_momentum_acceleration"] = float(momentum_now - momentum_prev)

        return f

    def _extract_ohlcv_features(
        self, tf: str, bar_idx: int, lookbacks: list[int] = [1, 5, 20]
    ) -> dict[str, float]:
        """Extract OHLCV-derived features at a specific bar."""
        bars = self._bars[tf]
        features: dict[str, float] = {}

        if bar_idx < 0 or bar_idx >= len(bars):
            return features

        row = bars.iloc[bar_idx]
        atr_val = self._atr[tf].iloc[bar_idx] if bar_idx < len(self._atr[tf]) else 1.0

        # Current bar features
        features[f"{tf}_close"] = float(row["close"])
        features[f"{tf}_high"] = float(row["high"])
        features[f"{tf}_low"] = float(row["low"])
        features[f"{tf}_open"] = float(row["open"])
        features[f"{tf}_range"] = float(row["high"] - row["low"])
        features[f"{tf}_range_ATR"] = float((row["high"] - row["low"]) / atr_val) if atr_val > 0 else 0.0
        features[f"{tf}_body"] = float(abs(row["close"] - row["open"]))
        features[f"{tf}_body_ATR"] = float(abs(row["close"] - row["open"]) / atr_val) if atr_val > 0 else 0.0
        features[f"{tf}_ATR"] = float(atr_val)
        features[f"{tf}_volume"] = float(row["volume"]) if "volume" in row.index else 0.0

        # Returns at different lookbacks
        for lb in lookbacks:
            if bar_idx >= lb:
                prev_close = bars.iloc[bar_idx - lb]["close"]
                features[f"{tf}_return_{lb}"] = float(
                    (row["close"] - prev_close) / prev_close
                ) if prev_close != 0 else 0.0
                features[f"{tf}_return_{lb}_ATR"] = float(
                    (row["close"] - prev_close) / atr_val
                ) if atr_val > 0 else 0.0
            else:
                features[f"{tf}_return_{lb}"] = 0.0
                features[f"{tf}_return_{lb}_ATR"] = 0.0

        return features

    def _extract_volatility_features(self, tf: str, bar_idx: int) -> dict[str, float]:
        """Extract volatility-specific features."""
        bars = self._bars[tf]
        atr = self._atr[tf]
        features: dict[str, float] = {}

        # Always include all keys (zero if insufficient data)
        features[f"{tf}_realized_vol"] = 0.0
        features[f"{tf}_vol_of_vol"] = 0.0
        features[f"{tf}_time_since_large_move"] = 0.0
        features[f"{tf}_vol_regime"] = 1.0

        if bar_idx < 5:
            return features

        # Rolling realized volatility (log returns)
        close = bars["close"].values[:bar_idx + 1]
        if len(close) < 3:
            return features

        log_returns = np.diff(np.log(np.maximum(close, 1e-10)))

        # Realized vol (annualized, but we keep it scale-free relative to the series)
        if len(log_returns) >= VOL_LOOKBACK:
            recent_returns = log_returns[-VOL_LOOKBACK:]
        else:
            recent_returns = log_returns

        realized_vol = float(np.std(recent_returns)) if len(recent_returns) > 1 else 0.0
        features[f"{tf}_realized_vol"] = realized_vol

        # Volatility of volatility
        if len(log_returns) >= VOV_LOOKBACK:
            # Compute rolling vol in windows
            window_size = max(10, len(log_returns) // 10)
            vols = []
            for i in range(0, len(log_returns) - window_size + 1, window_size):
                vols.append(float(np.std(log_returns[i:i + window_size])))
            vol_of_vol = float(np.std(vols)) if len(vols) > 1 else 0.0
            features[f"{tf}_vol_of_vol"] = vol_of_vol
        else:
            features[f"{tf}_vol_of_vol"] = 0.0

        # Time since last large move
        atr_val = atr.iloc[bar_idx] if bar_idx < len(atr) else 0
        time_since_large = 0
        if atr_val > 0:
            for i in range(len(log_returns) - 1, -1, -1):
                if abs(log_returns[i]) > LARGE_MOVE_ATR * (atr_val / close[i]) if close[i] > 0 else 0:
                    time_since_large = len(log_returns) - 1 - i
                    break
        features[f"{tf}_time_since_large_move"] = float(time_since_large)

        # Current volatility regime indicator (high/low relative to recent)
        if len(log_returns) >= VOL_LOOKBACK:
            long_vol = float(np.std(log_returns))
            short_vol = float(np.std(log_returns[-20:])) if len(log_returns) >= 20 else long_vol
            features[f"{tf}_vol_regime"] = float(short_vol / long_vol) if long_vol > 0 else 1.0
        else:
            features[f"{tf}_vol_regime"] = 1.0

        return features

    def _extract_fingerprint_features(self, tf: str, bar_idx: int) -> dict[str, float]:
        """
        Statistical fingerprint features — synthetic-specific indicators.
        Time-since-last-spike, streak counters, mean-reversion vs persistence.
        """
        bars = self._bars[tf]
        atr = self._atr[tf]
        features: dict[str, float] = {}

        # Always include all keys (zero if insufficient data)
        features[f"{tf}_time_since_spike"] = 0.0
        features[f"{tf}_up_streak"] = 0.0
        features[f"{tf}_down_streak"] = 0.0
        features[f"{tf}_mean_reversion_ratio"] = 0.0
        features[f"{tf}_autocorrelation"] = 0.0

        if bar_idx < 5:
            return features

        close = bars["close"].values[:bar_idx + 1]
        if len(close) < 3:
            return features

        log_returns = np.diff(np.log(np.maximum(close, 1e-10)))
        atr_val = atr.iloc[bar_idx] if bar_idx < len(atr) else 0

        # Time since last spike (Boom/Crash signature)
        time_since_spike = 0
        if atr_val > 0:
            for i in range(len(log_returns) - 1, -1, -1):
                spike_threshold = SPIKE_ATR_MULT * (atr_val / close[i]) if close[i] > 0 else 0
                if abs(log_returns[i]) > spike_threshold:
                    time_since_spike = len(log_returns) - 1 - i
                    break
        features[f"{tf}_time_since_spike"] = float(time_since_spike)

        # Streak counter (consecutive up/down bars)
        up_streak = 0
        down_streak = 0
        for i in range(len(close) - 1, 0, -1):
            if close[i] > close[i - 1]:
                up_streak += 1
                if down_streak > 0:
                    break
            elif close[i] < close[i - 1]:
                down_streak += 1
                if up_streak > 0:
                    break
            else:
                break
        features[f"{tf}_up_streak"] = float(min(up_streak, STREAK_MAX))
        features[f"{tf}_down_streak"] = float(min(down_streak, STREAK_MAX))

        # Mean-reversion indicator: ratio of short-term mean to long-term mean
        if len(close) >= MEAN_REV_LOOKBACK:
            short_mean = float(np.mean(close[-20:]))
            long_mean = float(np.mean(close[-MEAN_REV_LOOKBACK:]))
            features[f"{tf}_mean_reversion_ratio"] = float(
                (short_mean - long_mean) / long_mean
            ) if long_mean != 0 else 0.0
        else:
            features[f"{tf}_mean_reversion_ratio"] = 0.0

        # Persistence indicator: autocorrelation of returns
        if len(log_returns) >= PERSISTENCE_LOOKBACK:
            recent = log_returns[-PERSISTENCE_LOOKBACK:]
            if np.std(recent) > 0:
                # Lag-1 autocorrelation
                autocorr = float(np.corrcoef(recent[:-1], recent[1:])[0, 1])
                features[f"{tf}_autocorrelation"] = autocorr if not np.isnan(autocorr) else 0.0
            else:
                features[f"{tf}_autocorrelation"] = 0.0
        else:
            features[f"{tf}_autocorrelation"] = 0.0

        return features

    def _extract_structure_features(
        self, bar_idx: int, tf: str, current_price: float
    ) -> dict[str, float]:
        """
        Extract structural context features from detected structures.
        Uses ROLLING window (last 200 bars) for counts, matching batch precomputation.
        """
        ROLLING_LOOKBACK = 200
        features: dict[str, float] = {}

        # Get structures up to current bar, then filter to rolling window
        all_structs = self._get_structures_up_to(bar_idx)
        relevant = [s for s in all_structs if s.index >= bar_idx - ROLLING_LOOKBACK]

        if not relevant:
            features["n_structures"] = 0.0
            features["n_fvg"] = 0.0
            features["n_ifvg"] = 0.0
            features["n_ob"] = 0.0
            features["n_liquidity_sweep"] = 0.0
            features["n_bos"] = 0.0
            features["n_choch"] = 0.0
            features["n_eqh"] = 0.0
            features["n_eql"] = 0.0
            features["n_mitigation"] = 0.0
            features["nearest_structure_distance"] = 0.0
            features["nearest_structure_size"] = 0.0
            features["premium_discount_position"] = 0.5
            return features

        # Count by type (rolling window)
        type_counts: dict[str, int] = {}
        for s in relevant:
            type_counts[s.stype] = type_counts.get(s.stype, 0) + 1

        features["n_structures"] = float(len(relevant))
        features["n_fvg"] = float(type_counts.get("FVG", 0))
        features["n_ifvg"] = float(type_counts.get("iFVG", 0))
        features["n_ob"] = float(type_counts.get("OB", 0))
        features["n_liquidity_sweep"] = float(type_counts.get("liquidity_sweep", 0))
        features["n_bos"] = float(type_counts.get("BOS", 0))
        features["n_choch"] = float(type_counts.get("CHoCH", 0))
        features["n_eqh"] = float(type_counts.get("EQH", 0))
        features["n_eql"] = float(type_counts.get("EQL", 0))
        features["n_mitigation"] = float(type_counts.get("mitigation_block", 0))

        # Nearest structure distance and size (within rolling window)
        min_distance = float("inf")
        nearest_size = 0.0
        for s in relevant:
            zone_mid = (s.zone_high + s.zone_low) / 2.0
            dist = abs(current_price - zone_mid)
            if dist < min_distance:
                min_distance = dist
                nearest_size = s.size_ATR

        features["nearest_structure_distance"] = float(min_distance / current_price) if current_price > 0 else 0.0
        features["nearest_structure_size"] = nearest_size

        # Premium/Discount zone position -- only use recent swing points
        recent_bars = self._bars[tf].iloc[max(0, bar_idx - 500):bar_idx + 1]
        pd_zone = compute_premium_discount(
            detect_swing_points(recent_bars, lookback=self.swing_lookback),
            current_price,
        )
        features["premium_discount_position"] = pd_zone["position"]

        features["structure_confluence"] = 0.0

        return features

    # ------------------------------------------------------------------
    # Main public API
    # ------------------------------------------------------------------

    def build_state(self, timestamp: pd.Timestamp) -> dict[str, float]:
        """
        Build the complete feature vector for a given timestamp.

        Parameters
        ----------
        timestamp : pd.Timestamp
            The point in time to build the state for.

        Returns
        -------
        dict[str, float]
            Named feature values — no NaNs, consistent shape.
        """
        features: dict[str, float] = {}

        # Multi-timeframe features
        for tf in self.timeframes:
            bar_idx = self._find_bar_index(tf, timestamp)
            if bar_idx is None:
                # Fill with zeros if no bar found for this timeframe
                features.update(self._empty_ohlcv_features(tf))
                features.update(self._empty_vol_features(tf))
                features.update(self._empty_fingerprint_features(tf))
                features.update(self._empty_enhanced_features(tf))
                continue

            features.update(self._extract_ohlcv_features(tf, bar_idx))
            features.update(self._extract_volatility_features(tf, bar_idx))
            features.update(self._extract_fingerprint_features(tf, bar_idx))
            features.update(self._extract_enhanced_features(tf, bar_idx))

        # Structural features (use finest timeframe)
        if self.timeframes:
            finest = self.timeframes[0]
            bar_idx = self._find_bar_index(finest, timestamp)
            if bar_idx is not None and bar_idx < len(self._bars[finest]):
                current_price = float(self._bars[finest].iloc[bar_idx]["close"])
            else:
                # Fallback: try 'price' column (ticks) or 'close' column (candles)
                if "close" in self.ticks.columns:
                    current_price = float(self.ticks["close"].iloc[-1])
                elif "price" in self.ticks.columns:
                    current_price = float(self.ticks["price"].iloc[-1])
                else:
                    current_price = 0.0
            features.update(self._extract_structure_features(bar_idx, finest, current_price))

        # Ensure no NaNs
        features = {k: float(v) if not np.isnan(v) and not np.isinf(v) else 0.0 for k, v in features.items()}

        return features

    def build_state_vector(self, timestamp: pd.Timestamp) -> np.ndarray:
        """
        Build state as a numpy array (ordered consistently).
        
        Returns
        -------
        np.ndarray of shape (n_features,)
        """
        d = self.build_state(timestamp)
        # Sort by key for consistent ordering
        keys = sorted(d.keys())
        return np.array([d[k] for k in keys], dtype=np.float32)

    # ------------------------------------------------------------------
    # Batch operations for fast dataset precomputation
    # ------------------------------------------------------------------

    def _precompute_auxiliary(self, tf: str) -> dict[str, np.ndarray]:
        """Precompute log returns and ATR arrays for a timeframe once."""
        close = self._bars[tf]["close"].values.astype(np.float64)
        log_returns = np.diff(np.log(np.maximum(close, 1e-10)))
        atr = self._atr[tf].values.astype(np.float64)
        return {"close": close, "log_returns": log_returns, "atr": atr}

    def _build_ohlcv_features_fast(
        self, tf: str, bar_idx: int, aux: dict[str, np.ndarray]
    ) -> dict[str, float]:
        """Fast OHLCV feature extraction using precomputed arrays."""
        f: dict[str, float] = {}
        bars = self._bars[tf]
        if bar_idx < 0 or bar_idx >= len(bars):
            return f

        close_arr = aux["close"]
        atr_arr = aux["atr"]
        log_returns = aux["log_returns"]

        row = bars.iloc[bar_idx]
        atr_val = float(atr_arr[bar_idx]) if bar_idx < len(atr_arr) else 1.0
        c = float(close_arr[bar_idx])

        f[f"{tf}_close"] = c
        f[f"{tf}_high"] = float(row["high"])
        f[f"{tf}_low"] = float(row["low"])
        f[f"{tf}_open"] = float(row["open"])
        f[f"{tf}_range"] = float(row["high"] - row["low"])
        f[f"{tf}_range_ATR"] = float((row["high"] - row["low"]) / atr_val) if atr_val > 0 else 0.0
        f[f"{tf}_body"] = float(abs(c - float(row["open"])))
        f[f"{tf}_body_ATR"] = float(abs(c - float(row["open"])) / atr_val) if atr_val > 0 else 0.0
        f[f"{tf}_ATR"] = atr_val
        f[f"{tf}_volume"] = float(row["volume"]) if "volume" in row.index else 0.0

        for lb in [1, 5, 20]:
            if bar_idx >= lb:
                prev_close = float(close_arr[bar_idx - lb])
                f[f"{tf}_return_{lb}"] = float((c - prev_close) / prev_close) if prev_close != 0 else 0.0
                f[f"{tf}_return_{lb}_ATR"] = float((c - prev_close) / atr_val) if atr_val > 0 else 0.0
            else:
                f[f"{tf}_return_{lb}"] = 0.0
                f[f"{tf}_return_{lb}_ATR"] = 0.0

        return f

    def _build_vol_features_fast(
        self, tf: str, bar_idx: int, aux: dict[str, np.ndarray]
    ) -> dict[str, float]:
        """Fast volatility feature extraction."""
        f: dict[str, float] = {}
        f[f"{tf}_realized_vol"] = 0.0
        f[f"{tf}_vol_of_vol"] = 0.0
        f[f"{tf}_time_since_large_move"] = 0.0
        f[f"{tf}_vol_regime"] = 1.0

        if bar_idx < 5:
            return f

        close_arr = aux["close"]
        log_returns = aux["log_returns"]
        atr_arr = aux["atr"]
        n_lr = len(log_returns)
        if n_lr < 3 or bar_idx >= n_lr + 1:
            return f

        # Slice up to bar_idx-1 (log_returns[bar_idx-1] corresponds to bar_idx)
        lr_slice = log_returns[:bar_idx] if bar_idx <= n_lr else log_returns

        # Realized vol
        if len(lr_slice) >= VOL_LOOKBACK:
            recent = lr_slice[-VOL_LOOKBACK:]
        else:
            recent = lr_slice
        f[f"{tf}_realized_vol"] = float(np.std(recent)) if len(recent) > 1 else 0.0

        # Vol of vol
        if len(lr_slice) >= VOV_LOOKBACK:
            ws = max(10, len(lr_slice) // 10)
            vols = [float(np.std(lr_slice[i:i+ws])) for i in range(0, len(lr_slice) - ws + 1, ws)]
            f[f"{tf}_vol_of_vol"] = float(np.std(vols)) if len(vols) > 1 else 0.0

        # Time since large move
        atr_val = float(atr_arr[bar_idx]) if bar_idx < len(atr_arr) else 0
        time_since = 0
        if atr_val > 0:
            for i in range(len(lr_slice) - 1, -1, -1):
                c_i = float(close_arr[i + 1]) if i + 1 < len(close_arr) else 1.0
                thresh = LARGE_MOVE_ATR * (atr_val / c_i) if c_i > 0 else 0
                if abs(lr_slice[i]) > thresh:
                    time_since = len(lr_slice) - 1 - i
                    break
        f[f"{tf}_time_since_large_move"] = float(time_since)

        # Vol regime
        if len(lr_slice) >= VOL_LOOKBACK:
            long_vol = float(np.std(lr_slice))
            short_vol = float(np.std(lr_slice[-20:])) if len(lr_slice) >= 20 else long_vol
            f[f"{tf}_vol_regime"] = float(short_vol / long_vol) if long_vol > 0 else 1.0

        return f

    def _build_fingerprint_features_fast(
        self, tf: str, bar_idx: int, aux: dict[str, np.ndarray]
    ) -> dict[str, float]:
        """Fast fingerprint feature extraction."""
        f: dict[str, float] = {}
        f[f"{tf}_time_since_spike"] = 0.0
        f[f"{tf}_up_streak"] = 0.0
        f[f"{tf}_down_streak"] = 0.0
        f[f"{tf}_mean_reversion_ratio"] = 0.0
        f[f"{tf}_autocorrelation"] = 0.0

        if bar_idx < 5:
            return f

        close_arr = aux["close"]
        log_returns = aux["log_returns"]
        atr_arr = aux["atr"]
        n_lr = len(log_returns)
        if n_lr < 3 or bar_idx >= n_lr + 1:
            return f

        lr_slice = log_returns[:bar_idx] if bar_idx <= n_lr else log_returns

        # Spike detection
        atr_val = float(atr_arr[bar_idx]) if bar_idx < len(atr_arr) else 0
        time_since_spike = 0
        if atr_val > 0:
            for i in range(len(lr_slice) - 1, -1, -1):
                c_i = float(close_arr[i + 1]) if i + 1 < len(close_arr) else 1.0
                thresh = SPIKE_ATR_MULT * (atr_val / c_i) if c_i > 0 else 0
                if abs(lr_slice[i]) > thresh:
                    time_since_spike = len(lr_slice) - 1 - i
                    break
        f[f"{tf}_time_since_spike"] = float(time_since_spike)

        # Streak
        close_slice = close_arr[:bar_idx + 1]
        up_streak = 0
        down_streak = 0
        for i in range(len(close_slice) - 1, 0, -1):
            if close_slice[i] > close_slice[i - 1]:
                up_streak += 1
                if down_streak > 0:
                    break
            elif close_slice[i] < close_slice[i - 1]:
                down_streak += 1
                if up_streak > 0:
                    break
            else:
                break
        f[f"{tf}_up_streak"] = float(min(up_streak, STREAK_MAX))
        f[f"{tf}_down_streak"] = float(min(down_streak, STREAK_MAX))

        # Mean reversion
        close_slice = close_arr[:bar_idx + 1]
        if len(close_slice) >= MEAN_REV_LOOKBACK:
            short_mean = float(np.mean(close_slice[-20:]))
            long_mean = float(np.mean(close_slice[-MEAN_REV_LOOKBACK:]))
            f[f"{tf}_mean_reversion_ratio"] = float((short_mean - long_mean) / long_mean) if long_mean != 0 else 0.0

        # Autocorrelation
        if len(lr_slice) >= PERSISTENCE_LOOKBACK:
            recent = lr_slice[-PERSISTENCE_LOOKBACK:]
            if np.std(recent) > 0:
                ac = float(np.corrcoef(recent[:-1], recent[1:])[0, 1])
                f[f"{tf}_autocorrelation"] = ac if not np.isnan(ac) else 0.0

        return f

    def _build_structure_features_fast(
        self, bar_idx: int, tf: str, current_price: float, aux: dict[str, np.ndarray]
    ) -> dict[str, float]:
        """Fast structure feature extraction."""
        f: dict[str, float] = {}

        # Use binary search for structures
        if self._structures:
            struct_indices = [s.index for s in self._structures]
            pos = bisect.bisect_right(struct_indices, bar_idx)
            relevant = self._structures[:pos]
        else:
            relevant = []

        if not relevant:
            f["n_structures"] = 0.0
            f["n_fvg"] = 0.0
            f["n_ifvg"] = 0.0
            f["n_ob"] = 0.0
            f["n_liquidity_sweep"] = 0.0
            f["n_bos"] = 0.0
            f["n_choch"] = 0.0
            f["n_eqh"] = 0.0
            f["n_eql"] = 0.0
            f["n_mitigation"] = 0.0
            f["nearest_structure_distance"] = 0.0
            f["nearest_structure_size"] = 0.0
            f["premium_discount_position"] = 0.5
            f["structure_confluence"] = 0.0
            return f

        # Count by type (only consider structures from finest timeframe)
        finest = self.timeframes[0] if self.timeframes else "1min"
        bars = self._bars[finest]
        n_fine = len(bars)

        type_counts: dict[str, int] = {}
        min_distance = float("inf")
        nearest_size = 0.0
        for s in relevant:
            type_counts[s.stype] = type_counts.get(s.stype, 0) + 1
            zone_mid = (s.zone_high + s.zone_low) / 2.0
            dist = abs(current_price - zone_mid)
            if dist < min_distance:
                min_distance = dist
                nearest_size = s.size_ATR

        f["n_structures"] = float(len(relevant))
        f["n_fvg"] = float(type_counts.get("FVG", 0))
        f["n_ifvg"] = float(type_counts.get("iFVG", 0))
        f["n_ob"] = float(type_counts.get("OB", 0))
        f["n_liquidity_sweep"] = float(type_counts.get("liquidity_sweep", 0))
        f["n_bos"] = float(type_counts.get("BOS", 0))
        f["n_choch"] = float(type_counts.get("CHoCH", 0))
        f["n_eqh"] = float(type_counts.get("EQH", 0))
        f["n_eql"] = float(type_counts.get("EQL", 0))
        f["n_mitigation"] = float(type_counts.get("mitigation_block", 0))
        f["nearest_structure_distance"] = float(min_distance / current_price) if current_price > 0 else 0.0
        f["nearest_structure_size"] = nearest_size

        # Premium/Discount (use recent swing points only)
        recent_start = max(0, bar_idx - 500)
        recent_bars = bars.iloc[recent_start:bar_idx + 1].copy().reset_index(drop=True)
        sw = detect_swing_points(recent_bars, lookback=self.swing_lookback)
        pd_zone = compute_premium_discount(sw, current_price)
        f["premium_discount_position"] = pd_zone["position"]
        f["structure_confluence"] = 0.0

        return f

    def _precompute_premium_discount(self, finest: str) -> np.ndarray:
        """Precompute premium/discount position for every bar in the finest timeframe."""
        import time as _time
        t0 = _time.time()
        bars = self._bars[finest]
        n = len(bars)
        positions = np.full(n, 0.5, dtype=np.float64)
        lookback = self.swing_lookback
        window = 500
        step = max(1, window // 4)
        cached_swings: list = []
        cached_end = -1

        for i in range(lookback, n):
            win_start = max(0, i - window)
            if i == lookback or (i - cached_end) >= step:
                window_slice = bars.iloc[win_start:i + 1].copy().reset_index(drop=True)
                cached_swings = detect_swing_points(window_slice, lookback=lookback)
                cached_end = i

            zone_highs = [sw.price for sw in cached_swings if sw.kind == "high" and sw.price > 0]
            zone_lows = [sw.price for sw in cached_swings if sw.kind == "low" and sw.price > 0]
            price = float(bars.iloc[i]["close"])

            if zone_highs and price > 0:
                swing_high = max(zone_highs)
                swing_low = min(zone_lows)
                if swing_high != swing_low:
                    positions[i] = max(0.0, min(1.0, (price - swing_low) / (swing_high - swing_low)))

        elapsed = _time.time() - t0
        logger.info("precompute.premium_discount", elapsed_s=round(elapsed, 1), n_bars=n)
        return positions

    def _precompute_all_structure_counts(self) -> dict[str, np.ndarray]:
        """Precompute ROLLING structure counts for every bar index.

        Uses a rolling window of ROLLING_STRUCT_LOOKBACK bars so features
        reflect recent market structure rather than cumulative history.
        """
        import time as _time
        t0 = _time.time()

        ROLLING_STRUCT_LOOKBACK = 200  # bars to look back for counts

        if not self._structures or not self.timeframes:
            return {}

        finest = self.timeframes[0]
        n_fine = len(self._bars[finest])
        close_arr = self._bars[finest]["close"].values.astype(np.float64)

        type_names = ["FVG", "iFVG", "OB", "liquidity_sweep", "BOS", "CHoCH", "EQH", "EQL", "mitigation_block"]
        keys_map = {}
        for t in type_names:
            k = f"n_{t}".lower()
            if t == "mitigation_block":
                k = "n_mitigation"
            keys_map[t] = k
        counts = {v: np.zeros(n_fine, dtype=np.float32) for v in keys_map.values()}
        counts["n_structures"] = np.zeros(n_fine, dtype=np.float32)
        nearest_dist = np.zeros(n_fine, dtype=np.float64)
        nearest_size = np.zeros(n_fine, dtype=np.float64)

        # Build per-bar structure lists for efficient sliding window
        bar_structs: dict[int, list] = {}
        for s in self._structures:
            if s.index not in bar_structs:
                bar_structs[s.index] = []
            bar_structs[s.index].append(s)

        # Sliding window counts
        window_counts = {k: 0 for k in counts}
        window_min_dist = 1e18
        window_nearest_size = 0.0
        # We'll track which structures are in the window using an index list
        window_struct_indices: list[int] = []  # bar indices of structures in window

        struct_idx = 0
        n_structs = len(self._structures)
        # Pre-sort structure bar indices
        struct_bar_indices = [s.index for s in self._structures]

        for bar_i in range(n_fine):
            # Remove structures that fell out of the window (more than LOOKBACK bars ago)
            window_start = bar_i - ROLLING_STRUCT_LOOKBACK
            while window_struct_indices and window_struct_indices[0] < window_start:
                old_idx = window_struct_indices.pop(0)
                # Decrease counts for the structure at old_idx
                for s in bar_structs.get(old_idx, []):
                    window_counts["n_structures"] -= 1
                    sk = keys_map.get(s.stype)
                    if sk and sk in window_counts:
                        window_counts[sk] -= 1

            # Add new structures at this bar
            if bar_i in bar_structs:
                for s in bar_structs[bar_i]:
                    window_counts["n_structures"] += 1
                    sk = keys_map.get(s.stype)
                    if sk and sk in window_counts:
                        window_counts[sk] += 1
                    window_struct_indices.append(bar_i)

            # Compute nearest structure within window
            c = close_arr[bar_i]
            min_dist = 0.0
            min_size = 0.0
            if c > 0:
                best_dist = float('inf')
                for si in range(len(window_struct_indices) - 1, -1, -1):
                    bi = window_struct_indices[si]
                    for s in bar_structs.get(bi, []):
                        zone_mid = (s.zone_high + s.zone_low) / 2.0
                        dist = abs(c - zone_mid)
                        if dist < best_dist:
                            best_dist = dist
                            min_size = s.size_ATR
                if best_dist < float('inf'):
                    min_dist = best_dist

            for k in counts:
                counts[k][bar_i] = window_counts[k]
            nearest_dist[bar_i] = min_dist
            nearest_size[bar_i] = min_size

        counts["nearest_structure_distance"] = nearest_dist
        counts["nearest_structure_size"] = nearest_size

        elapsed = _time.time() - t0
        logger.info("precompute.structure_counts", elapsed_s=round(elapsed, 1), n_structs=n_structs, n_bars=n_fine, lookback=ROLLING_STRUCT_LOOKBACK)
        return counts

    def _vectorize_ohlcv_features(self, tf: str) -> dict[str, np.ndarray]:
        """Precompute ALL OHLCV features for every bar in a timeframe as arrays."""
        bars = self._bars[tf]
        n = len(bars)
        atr = self._atr[tf].values.astype(np.float64)
        open_ = bars["open"].values.astype(np.float64)
        high = bars["high"].values.astype(np.float64)
        low = bars["low"].values.astype(np.float64)
        close = bars["close"].values.astype(np.float64)
        volume = bars["volume"].values.astype(np.float64) if "volume" in bars.columns else np.zeros(n)

        f: dict[str, np.ndarray] = {}
        f[f"{tf}_close"] = close
        f[f"{tf}_high"] = high
        f[f"{tf}_low"] = low
        f[f"{tf}_open"] = open_
        f[f"{tf}_range"] = high - low
        f[f"{tf}_range_ATR"] = np.where(atr > 0, (high - low) / atr, 0.0)
        f[f"{tf}_body"] = np.abs(close - open_)
        f[f"{tf}_body_ATR"] = np.where(atr > 0, np.abs(close - open_) / atr, 0.0)
        f[f"{tf}_ATR"] = atr
        f[f"{tf}_volume"] = volume

        for lb in [1, 5, 20]:
            prev = np.roll(close, lb)
            prev[:lb] = close[:lb]  # fill with current for boundary
            f[f"{tf}_return_{lb}"] = np.where(prev != 0, (close - prev) / prev, 0.0)
            f[f"{tf}_return_{lb}_ATR"] = np.where(atr > 0, (close - prev) / atr, 0.0)

        return f

    def _vectorize_vol_features(self, tf: str) -> dict[str, np.ndarray]:
        """Precompute ALL volatility features as arrays."""
        bars = self._bars[tf]
        n = len(bars)
        close = bars["close"].values.astype(np.float64)
        atr = self._atr[tf].values.astype(np.float64)
        log_returns = np.diff(np.log(np.maximum(close, 1e-10)))
        # Pad to length n: log_returns[i] = return from bar i to bar i+1
        lr = np.zeros(n)
        lr[1:] = log_returns

        f: dict[str, np.ndarray] = {}
        f[f"{tf}_realized_vol"] = np.zeros(n)
        f[f"{tf}_vol_of_vol"] = np.zeros(n)
        f[f"{tf}_time_since_large_move"] = np.zeros(n)
        f[f"{tf}_vol_regime"] = np.ones(n)

        # Use pandas for rolling std (vectorized)
        lr_series = pd.Series(lr)
        rolling_vol = lr_series.rolling(VOL_LOOKBACK, min_periods=2).std().values
        f[f"{tf}_realized_vol"] = np.nan_to_num(rolling_vol, nan=0.0)

        # Vol of vol: rolling std of rolling vol
        rv_series = pd.Series(np.nan_to_num(rolling_vol, nan=0.0))
        vov = rv_series.rolling(VOV_LOOKBACK, min_periods=2).std().values
        f[f"{tf}_vol_of_vol"] = np.nan_to_num(vov, nan=0.0)

        # Time since large move (vectorized backwards scan)
        large_thresh = LARGE_MOVE_ATR * (atr / np.maximum(close, 1e-10))
        is_large = np.abs(lr) > large_thresh
        time_since = np.zeros(n)
        last_large = -1
        for i in range(n - 1, -1, -1):
            if is_large[i]:
                last_large = i
            if last_large >= 0:
                time_since[i] = n - 1 - last_large
        f[f"{tf}_time_since_large_move"] = time_since

        # Vol regime: short-term / long-term vol ratio
        short_vol = lr_series.rolling(20, min_periods=2).std().values
        long_vol = lr_series.rolling(VOL_LOOKBACK, min_periods=2).std().values
        f[f"{tf}_vol_regime"] = np.where(
            long_vol > 0, np.nan_to_num(short_vol / long_vol, nan=1.0), 1.0
        )

        return f

    def _vectorize_fingerprint_features(self, tf: str) -> dict[str, np.ndarray]:
        """Precompute ALL fingerprint features as arrays."""
        bars = self._bars[tf]
        n = len(bars)
        close = bars["close"].values.astype(np.float64)
        atr = self._atr[tf].values.astype(np.float64)
        log_returns = np.zeros(n)
        log_returns[1:] = np.diff(np.log(np.maximum(close, 1e-10)))

        f: dict[str, np.ndarray] = {}

        # Time since spike
        spike_thresh = SPIKE_ATR_MULT * (atr / np.maximum(close, 1e-10))
        is_spike = np.abs(log_returns) > spike_thresh
        time_since_spike = np.zeros(n)
        last_spike = -1
        for i in range(n - 1, -1, -1):
            if is_spike[i]:
                last_spike = i
            if last_spike >= 0:
                time_since_spike[i] = n - 1 - last_spike
        f[f"{tf}_time_since_spike"] = time_since_spike

        # Streak counter (vectorized)
        up_streak = np.zeros(n)
        down_streak = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                up_streak[i] = up_streak[i - 1] + 1
                down_streak[i] = 0
            elif close[i] < close[i - 1]:
                down_streak[i] = down_streak[i - 1] + 1
                up_streak[i] = 0
            else:
                up_streak[i] = 0
                down_streak[i] = 0
        f[f"{tf}_up_streak"] = np.minimum(up_streak, STREAK_MAX)
        f[f"{tf}_down_streak"] = np.minimum(down_streak, STREAK_MAX)

        # Mean reversion ratio
        close_series = pd.Series(close)
        short_mean = close_series.rolling(20, min_periods=2).mean().values
        long_mean = close_series.rolling(MEAN_REV_LOOKBACK, min_periods=2).mean().values
        f[f"{tf}_mean_reversion_ratio"] = np.where(
            long_mean != 0, np.nan_to_num((short_mean - long_mean) / long_mean, nan=0.0), 0.0
        )

        # Autocorrelation (lag-1)
        lr_series = pd.Series(log_returns)
        # Rolling correlation via pandas rolling apply is slow; use a simpler approach
        autocorr = np.zeros(n)
        for i in range(PERSISTENCE_LOOKBACK + 1, n):
            window = log_returns[i - PERSISTENCE_LOOKBACK:i + 1]
            if np.std(window) > 0:
                c = np.corrcoef(window[:-1], window[1:])[0, 1]
                autocorr[i] = c if not np.isnan(c) else 0.0
        f[f"{tf}_autocorrelation"] = autocorr

        return f

    def build_all_states(
        self, timestamps: np.ndarray, bar_idx_map: dict[pd.Timestamp, int] | None = None
    ) -> tuple[np.ndarray, list[str]]:
        """
        Build state vectors for many timestamps efficiently.
        
        Precomputes ALL features as numpy arrays per timeframe,
        premium/discount, and structure counts — then just indexes.
        """
        import time as _time
        t0 = _time.time()

        finest = self.timeframes[0] if self.timeframes else "1min"
        n_fine = len(self._bars[finest])

        # 1) Vectorize all per-timeframe features as arrays
        tf_features: dict[str, dict[str, np.ndarray]] = {}
        for tf in self.timeframes:
            tf_features[tf] = {}
            tf_features[tf].update(self._vectorize_ohlcv_features(tf))
            tf_features[tf].update(self._vectorize_vol_features(tf))
            tf_features[tf].update(self._vectorize_fingerprint_features(tf))
            tf_features[tf].update(self._vectorize_enhanced_features(tf))

        t_vec = _time.time()
        logger.info("build_all_states.vectorize_done", elapsed_s=round(t_vec - t0, 1))

        # 2) Precompute premium/discount and structure counts
        precomputed_pd = self._precompute_premium_discount(finest)
        precomputed_structs = self._precompute_all_structure_counts()

        # 3) Map timestamps → bar index per timeframe
        bar_idx_matrix = np.full((len(timestamps), len(self.timeframes)), -1, dtype=np.intp)
        for tf_i, tf in enumerate(self.timeframes):
            ts_arr = self._ts_index[tf]["timestamps"]
            resolved = np.searchsorted(ts_arr, timestamps, side='right') - 1
            for i in range(len(timestamps)):
                r = int(resolved[i])
                if 0 <= r < len(ts_arr) and ts_arr[r] <= timestamps[i]:
                    bar_idx_matrix[i, tf_i] = r

        t_idx = _time.time()
        logger.info("build_all_states.index_done", elapsed_s=round(t_idx - t_vec, 1))

        # 4) Build feature names from a sample
        sample_state = self.build_state(pd.Timestamp(timestamps[len(timestamps) // 2]))
        feature_names = sorted(sample_state.keys())
        n_features = len(feature_names)
        key_to_idx = {k: i for i, k in enumerate(feature_names)}

        n_samples = len(timestamps)
        states = np.zeros((n_samples, n_features), dtype=np.float32)

        # 5) Fill states by indexing precomputed arrays
        # For each timeframe, map bar_idx_matrix → feature values
        for tf_i, tf in enumerate(self.timeframes):
            bar_indices = bar_idx_matrix[:, tf_i]
            valid = bar_indices >= 0
            feat_dict = tf_features[tf]
            for feat_key, arr in feat_dict.items():
                if feat_key in key_to_idx:
                    col_idx = key_to_idx[feat_key]
                    # Default to 0 for invalid indices
                    states[valid, col_idx] = arr[bar_indices[valid]].astype(np.float32)

        # Structure features
        struct_count_keys = [
            "n_structures", "n_fvg", "n_ifvg", "n_ob", "n_liquidity_sweep",
            "n_bos", "n_choch", "n_eqh", "n_eql", "n_mitigation",
            "nearest_structure_distance", "nearest_structure_size",
        ]
        valid_fine = bar_idx_matrix[:, 0] >= 0
        fine_idx = bar_idx_matrix[valid_fine, 0].astype(np.intp)

        for k in struct_count_keys:
            if k in key_to_idx and k in precomputed_structs:
                col_idx = key_to_idx[k]
                vals = precomputed_structs[k][fine_idx].astype(np.float32)
                vals = np.nan_to_num(vals, nan=0.0, posinf=0.0, neginf=0.0)
                states[valid_fine, col_idx] = vals

        if "premium_discount_position" in key_to_idx:
            col_idx = key_to_idx["premium_discount_position"]
            states[valid_fine, col_idx] = precomputed_pd[fine_idx].astype(np.float32)

        if "structure_confluence" in key_to_idx:
            states[:, key_to_idx["structure_confluence"]] = 0.0

        # Sanitize NaN/Inf
        np.nan_to_num(states, copy=False, nan=0.0, posinf=0.0, neginf=0.0)

        elapsed = _time.time() - t0
        logger.info(
            "build_all_states.done", n_samples=n_samples, n_features=n_features,
            elapsed_s=round(elapsed, 1), rate=f"{n_samples/elapsed:.0f}/s"
        )
        return states, feature_names

    @property
    def feature_names(self) -> list[str]:
        """Ordered list of feature names (matching build_state_vector order)."""
        sample = self.build_state(self.ticks["timestamp"].iloc[len(self.ticks) // 2])
        return sorted(sample.keys())

    @property
    def n_features(self) -> int:
        """Number of features in the state vector."""
        return len(self.feature_names)

    # ------------------------------------------------------------------
    # Empty feature placeholders
    # ------------------------------------------------------------------

    def _empty_ohlcv_features(self, tf: str) -> dict[str, float]:
        """Return zero-valued OHLCV features for a missing timeframe bar."""
        return {
            f"{tf}_close": 0.0,
            f"{tf}_high": 0.0,
            f"{tf}_low": 0.0,
            f"{tf}_open": 0.0,
            f"{tf}_range": 0.0,
            f"{tf}_range_ATR": 0.0,
            f"{tf}_body": 0.0,
            f"{tf}_body_ATR": 0.0,
            f"{tf}_ATR": 0.0,
            f"{tf}_volume": 0.0,
            f"{tf}_return_1": 0.0,
            f"{tf}_return_1_ATR": 0.0,
            f"{tf}_return_5": 0.0,
            f"{tf}_return_5_ATR": 0.0,
            f"{tf}_return_20": 0.0,
            f"{tf}_return_20_ATR": 0.0,
        }

    def _empty_vol_features(self, tf: str) -> dict[str, float]:
        """Return zero-valued volatility features."""
        return {
            f"{tf}_realized_vol": 0.0,
            f"{tf}_vol_of_vol": 0.0,
            f"{tf}_time_since_large_move": 0.0,
            f"{tf}_vol_regime": 0.0,
        }

    def _empty_fingerprint_features(self, tf: str) -> dict[str, float]:
        """Return zero-valued fingerprint features."""
        return {
            f"{tf}_time_since_spike": 0.0,
            f"{tf}_up_streak": 0.0,
            f"{tf}_down_streak": 0.0,
            f"{tf}_mean_reversion_ratio": 0.0,
            f"{tf}_autocorrelation": 0.0,
        }

    def _empty_enhanced_features(self, tf: str) -> dict[str, float]:
        """Return zero-valued enhanced (engineered) features."""
        return {
            f"{tf}_return_x_streak": 0.0,
            f"{tf}_return_x_vol_regime": 0.0,
            f"{tf}_range_x_streak": 0.0,
            f"{tf}_vol_short_long_ratio": 1.0,
            f"{tf}_vol_breakout": 0.0,
            f"{tf}_close_position_in_range": 0.5,
            f"{tf}_price_z_score": 0.0,
            f"{tf}_momentum_score": 0.0,
            f"{tf}_momentum_acceleration": 0.0,
        }

    # ------------------------------------------------------------------
    # Vectorized enhanced features for batch precomputation
    # ------------------------------------------------------------------

    def _vectorize_enhanced_features(self, tf: str) -> dict[str, np.ndarray]:
        """Precompute ALL enhanced (engineered) features as arrays."""
        bars = self._bars[tf]
        n = len(bars)
        close = bars["close"].values.astype(np.float64)
        high = bars["high"].values.astype(np.float64)
        low = bars["low"].values.astype(np.float64)
        atr = self._atr[tf].values.astype(np.float64)

        log_ret = np.zeros(n)
        log_ret[1:] = np.diff(np.log(np.maximum(close, 1e-10)))

        f: dict[str, np.ndarray] = {}

        # Precompute streak (vectorized)
        up_streak = np.zeros(n)
        down_streak = np.zeros(n)
        for i in range(1, n):
            if close[i] > close[i - 1]:
                up_streak[i] = up_streak[i - 1] + 1
                down_streak[i] = 0
            elif close[i] < close[i - 1]:
                down_streak[i] = down_streak[i - 1] + 1
                up_streak[i] = 0
            else:
                up_streak[i] = 0
                down_streak[i] = 0
        up_streak = np.minimum(up_streak, STREAK_MAX)
        down_streak = np.minimum(down_streak, STREAK_MAX)
        streak_dir = np.where(up_streak > 0, 1.0, np.where(down_streak > 0, -1.0, 0.0))
        streak_str = np.maximum(up_streak, down_streak)

        # Precompute vol regime
        lr_series = pd.Series(log_ret)
        short_vol = lr_series.rolling(20, min_periods=2).std().values
        long_vol = lr_series.rolling(VOL_LOOKBACK, min_periods=2).std().values
        vol_regime = np.where(long_vol > 0, np.nan_to_num(short_vol / long_vol, nan=1.0), 1.0)

        # 1) return_x_streak
        f[f"{tf}_return_x_streak"] = log_ret * streak_dir * np.minimum(streak_str, 10.0)

        # 2) return_x_vol_regime
        f[f"{tf}_return_x_vol_regime"] = log_ret * vol_regime

        # 3) range_x_streak
        ranges = high - low
        f[f"{tf}_range_x_streak"] = np.where(
            atr > 0, (ranges / atr) * streak_dir, 0.0
        )

        # 4) vol_short_long_ratio
        vol_100 = lr_series.rolling(100, min_periods=20).std().values
        vol_20 = lr_series.rolling(20, min_periods=5).std().values
        f[f"{tf}_vol_short_long_ratio"] = np.where(
            vol_100 > 0, np.nan_to_num(vol_20 / vol_100, nan=1.0), 1.0
        )

        # 5) vol_breakout: |return| / recent_vol
        f[f"{tf}_vol_breakout"] = np.where(
            vol_20 > 0, np.nan_to_num(np.abs(log_ret) / vol_20, nan=0.0), 0.0
        )

        # 6) close_position_in_range (rolling 50-bar)
        close_series = pd.Series(close)
        rolling_high = pd.Series(high).rolling(50, min_periods=5).max().values
        rolling_low = pd.Series(low).rolling(50, min_periods=5).min().values
        range_span = rolling_high - rolling_low
        f[f"{tf}_close_position_in_range"] = np.where(
            range_span > 0, np.nan_to_num((close - rolling_low) / range_span, nan=0.5), 0.5
        )

        # 7) price_z_score (50-bar)
        mean_50 = close_series.rolling(50, min_periods=10).mean().values
        std_50 = close_series.rolling(50, min_periods=10).std().values
        f[f"{tf}_price_z_score"] = np.where(
            std_50 > 0, np.nan_to_num((close - mean_50) / std_50, nan=0.0), 0.0
        )

        # 8) momentum_score (exponentially weighted recent returns)
        weights = np.exp(-np.linspace(0, 2, 20))
        weights = weights / weights.sum()
        momentum = np.zeros(n)
        for i in range(19, n):
            momentum[i] = np.dot(log_ret[i - 19:i + 1], weights)
        f[f"{tf}_momentum_score"] = momentum

        # 9) momentum_acceleration
        acceleration = np.zeros(n)
        for i in range(39, n):
            m_now = np.dot(log_ret[i - 19:i + 1], weights)
            m_prev = np.dot(log_ret[i - 39:i - 19], weights)
            acceleration[i] = m_now - m_prev
        f[f"{tf}_momentum_acceleration"] = acceleration

        return f


# ---------------------------------------------------------------------------
# Convenience: build from a Parquet file
# ---------------------------------------------------------------------------

def build_state_from_parquet(
    parquet_path: str,
    timestamp: pd.Timestamp,
    timeframes: list[str] | None = None,
) -> dict[str, float]:
    """Load ticks from a Parquet file and build state at a timestamp."""
    ticks = pd.read_parquet(parquet_path, engine="pyarrow")
    builder = StateBuilder(ticks, timeframes=timeframes)
    return builder.build_state(timestamp)


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    """CLI: show feature vector at a given timestamp."""
    import argparse

    parser = argparse.ArgumentParser(description="Build feature state at a timestamp")
    parser.add_argument("input", help="Input tick Parquet file")
    parser.add_argument("--timestamp", help="ISO timestamp to build state at")
    parser.add_argument("--timeframes", nargs="+", default=DEFAULT_TIMEFRAMES)
    args = parser.parse_args()

    ticks = pd.read_parquet(args.input, engine="pyarrow")

    if args.timestamp:
        ts = pd.Timestamp(args.timestamp, tz="UTC")
    else:
        # Use the middle of the data
        ts = ticks["timestamp"].iloc[len(ticks) // 2]

    builder = StateBuilder(ticks, timeframes=args.timeframes)
    state = builder.build_state(ts)

    print(f"\nState at {ts}:")
    print(f"  Total features: {len(state)}")
    print(f"  Feature names: {sorted(state.keys())[:10]}...")
    print(f"\n  Sample values:")
    for k in sorted(state.keys())[:20]:
        print(f"    {k}: {state[k]:.6f}")

    vec = builder.build_state_vector(ts)
    print(f"\n  Vector shape: {vec.shape}")
    print(f"  Any NaN: {np.any(np.isnan(vec))}")
    print(f"  Any Inf: {np.any(np.isinf(vec))}")


if __name__ == "__main__":
    main()
