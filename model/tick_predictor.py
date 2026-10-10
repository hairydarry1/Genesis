"""
model/tick_predictor.py — Tick-level prediction model for Boom/Crash indices.

Predicts next 10-50 tick movements using:
1. Velocity patterns
2. Buildup signatures
3. Tick count statistics
4. Historical spike patterns

Usage:
    from model.tick_predictor import TickPredictor

    predictor = TickPredictor(symbol='CRASH500')
    predictor.train(historical_ticks)

    prediction = predictor.predict_next_ticks(current_state, n_ticks=10)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import numpy as np
import pandas as pd
from collections import deque


@dataclass
class TickPrediction:
    """Prediction for next N ticks."""

    predicted_prices: np.ndarray  # Next N tick prices
    predicted_directions: np.ndarray  # +1 (up), -1 (down), 0 (neutral)

    spike_probability: float  # Probability of spike in next N ticks
    spike_expected_tick: int | None  # Which tick the spike is expected
    spike_expected_direction: Literal['UP', 'DOWN'] | None

    confidence: float  # Overall confidence (0.0 to 1.0)
    reasoning: list[str] = field(default_factory=list)

    # Statistical bounds
    upper_bound: np.ndarray = field(default_factory=lambda: np.array([]))
    lower_bound: np.ndarray = field(default_factory=lambda: np.array([]))


@dataclass
class TickState:
    """Current state of the tick stream."""

    recent_prices: np.ndarray  # Last 100+ ticks
    ticks_since_spike: int
    current_velocity: int  # Consecutive same-direction ticks
    velocity_direction: Literal['UP', 'DOWN', 'NEUTRAL']

    # Statistical features
    recent_volatility: float
    tick_interval_mean: float
    tick_interval_std: float

    # Pattern indicators
    is_building_up: bool
    buildup_strength: float


class TickPredictor:
    """
    Predict next 10-50 tick movements for Boom/Crash indices.

    Uses statistical patterns, velocity analysis, and spike timing models.

    Parameters
    ----------
    symbol : str
        'BOOM300', 'BOOM500', 'BOOM1000', 'CRASH300', 'CRASH500', 'CRASH1000'
    """

    def __init__(self, symbol: str):
        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        # Extract index number
        if '300' in self.symbol:
            self.index_number = 300
        elif '500' in self.symbol:
            self.index_number = 500
        elif '1000' in self.symbol:
            self.index_number = 1000
        else:
            self.index_number = 500

        # Learned patterns from training
        self.spike_intervals: list[int] = []
        self.spike_magnitudes: list[float] = []
        self.velocity_before_spike: list[int] = []
        self.typical_tick_change: float = 0.0
        self.typical_volatility: float = 0.0

        # State tracking
        self.last_spike_idx: int = 0
        self.spike_history: deque = deque(maxlen=100)

    def train(self, historical_prices: np.ndarray, verbose: bool = True):
        """
        Train predictor on historical tick data.

        Parameters
        ----------
        historical_prices : np.ndarray
            Historical tick prices
        verbose : bool
            Print training statistics
        """
        if len(historical_prices) < 100:
            raise ValueError("Need at least 100 ticks for training")

        # Detect spikes
        spike_mask = self._detect_spikes(historical_prices)
        spike_indices = np.where(spike_mask)[0]

        if len(spike_indices) < 2:
            raise ValueError("Need at least 2 spikes in training data")

        # Learn spike intervals
        self.spike_intervals = []
        for i in range(1, len(spike_indices)):
            interval = spike_indices[i] - spike_indices[i-1]
            self.spike_intervals.append(interval)

        # Learn spike magnitudes
        self.spike_magnitudes = []
        for idx in spike_indices:
            if idx > 0:
                magnitude = abs(historical_prices[idx] - historical_prices[idx-1])
                self.spike_magnitudes.append(magnitude)

        # Learn velocity before spikes
        self.velocity_before_spike = []
        for idx in spike_indices:
            if idx >= 30:
                velocity = self._count_velocity(historical_prices[idx-30:idx])
                self.velocity_before_spike.append(velocity)

        # Learn typical tick behavior
        changes = np.diff(historical_prices)
        non_spike_changes = changes[~spike_mask[1:]]
        self.typical_tick_change = np.median(np.abs(non_spike_changes[non_spike_changes != 0]))
        self.typical_volatility = np.std(non_spike_changes)

        if verbose:
            print(f"Training on {len(historical_prices)} ticks")
            print(f"  Spikes detected: {len(spike_indices)}")
            print(f"  Avg spike interval: {np.mean(self.spike_intervals):.0f} ticks")
            print(f"  Median spike interval: {np.median(self.spike_intervals):.0f} ticks")
            print(f"  Avg spike magnitude: {np.mean(self.spike_magnitudes):.4f}")
            print(f"  Avg velocity before spike: {np.mean(self.velocity_before_spike):.1f} ticks")
            print(f"  Typical tick change: {self.typical_tick_change:.5f}")
            print(f"  Typical volatility: {self.typical_volatility:.5f}")

    def predict_next_ticks(
        self,
        current_prices: np.ndarray,
        n_ticks: int = 10,
        include_spike_scenarios: bool = True
    ) -> TickPrediction:
        """
        Predict next N tick movements.

        Parameters
        ----------
        current_prices : np.ndarray
            Recent tick prices (last 100+ ticks)
        n_ticks : int
            Number of ticks to predict
        include_spike_scenarios : bool
            Whether to model potential spike scenarios

        Returns
        -------
        TickPrediction
            Complete prediction with confidence bounds
        """
        if len(current_prices) < 20:
            raise ValueError("Need at least 20 recent ticks for prediction")

        # Analyze current state
        state = self._analyze_current_state(current_prices)

        # Base prediction: random walk with drift
        predicted_prices = self._predict_base_trajectory(
            current_prices[-1],
            n_ticks,
            state
        )

        # Predict spike probability and timing
        spike_prob, spike_tick, spike_dir = self._predict_spike(state, n_ticks)

        # Adjust prediction if spike expected
        if include_spike_scenarios and spike_prob > 0.5 and spike_tick is not None:
            predicted_prices = self._inject_spike_scenario(
                predicted_prices,
                spike_tick,
                spike_dir,
                state
            )

        # Calculate directions
        predicted_directions = np.sign(np.diff(predicted_prices))
        predicted_directions = np.concatenate([[0], predicted_directions])  # First tick has no direction

        # Calculate confidence bounds
        upper_bound = predicted_prices + 2 * self.typical_volatility * np.sqrt(np.arange(1, n_ticks + 1))
        lower_bound = predicted_prices - 2 * self.typical_volatility * np.sqrt(np.arange(1, n_ticks + 1))

        # Calculate overall confidence
        confidence = self._calculate_prediction_confidence(state, spike_prob, n_ticks)

        # Build reasoning
        reasoning = self._build_reasoning(state, spike_prob, spike_tick, spike_dir)

        return TickPrediction(
            predicted_prices=predicted_prices,
            predicted_directions=predicted_directions,
            spike_probability=spike_prob,
            spike_expected_tick=spike_tick,
            spike_expected_direction=spike_dir,
            confidence=confidence,
            reasoning=reasoning,
            upper_bound=upper_bound,
            lower_bound=lower_bound,
        )

    def _analyze_current_state(self, prices: np.ndarray) -> TickState:
        """Analyze current tick stream state."""
        # Calculate velocity
        velocity, vel_dir = self._analyze_velocity(prices)

        # Calculate ticks since last spike
        spike_mask = self._detect_spikes(prices)
        spike_indices = np.where(spike_mask)[0]

        if len(spike_indices) > 0:
            ticks_since_spike = len(prices) - spike_indices[-1] - 1
        else:
            ticks_since_spike = len(prices)

        # Calculate volatility
        changes = np.diff(prices[-50:])
        recent_volatility = np.std(changes)

        # Calculate typical interval statistics
        if len(spike_indices) >= 2:
            intervals = np.diff(spike_indices)
            tick_interval_mean = np.mean(intervals)
            tick_interval_std = np.std(intervals)
        else:
            tick_interval_mean = float(self.index_number)
            tick_interval_std = float(self.index_number) * 0.2

        # Check for buildup
        is_building_up = False
        buildup_strength = 0.0

        if self.is_crash and vel_dir == 'UP' and velocity >= 8:
            is_building_up = True
            buildup_strength = min(velocity / 20.0, 1.0)
        elif self.is_boom and vel_dir == 'DOWN' and velocity >= 8:
            is_building_up = True
            buildup_strength = min(velocity / 20.0, 1.0)

        return TickState(
            recent_prices=prices,
            ticks_since_spike=ticks_since_spike,
            current_velocity=velocity,
            velocity_direction=vel_dir,
            recent_volatility=recent_volatility,
            tick_interval_mean=tick_interval_mean,
            tick_interval_std=tick_interval_std,
            is_building_up=is_building_up,
            buildup_strength=buildup_strength,
        )

    def _predict_base_trajectory(
        self,
        current_price: float,
        n_ticks: int,
        state: TickState
    ) -> np.ndarray:
        """Predict base trajectory assuming no spikes."""
        # Random walk with slight mean reversion
        prices = np.zeros(n_ticks)
        prices[0] = current_price

        # Estimate drift based on recent trend
        recent_trend = state.recent_prices[-10:] - state.recent_prices[-11:-1]
        avg_drift = np.mean(recent_trend)

        # Add noise
        noise = np.random.randn(n_ticks - 1) * self.typical_tick_change

        for i in range(1, n_ticks):
            # Mean reversion component
            mean_reversion = -0.1 * (prices[i-1] - current_price)

            # Next price
            prices[i] = prices[i-1] + avg_drift * 0.5 + mean_reversion + noise[i-1]

        return prices

    def _predict_spike(
        self,
        state: TickState,
        n_ticks: int
    ) -> tuple[float, int | None, Literal['UP', 'DOWN'] | None]:
        """
        Predict spike probability and timing.

        Returns
        -------
        tuple[float, int | None, str | None]
            (probability, expected_tick_index, direction)
        """
        # Signal 1: Tick count exhaustion
        threshold = self.index_number * 1.4
        if state.ticks_since_spike >= threshold * 1.2:
            tick_exhaustion_prob = 0.95
        elif state.ticks_since_spike >= threshold:
            tick_exhaustion_prob = 0.7
        elif state.ticks_since_spike >= threshold * 0.8:
            tick_exhaustion_prob = 0.3
        else:
            tick_exhaustion_prob = 0.0

        # Signal 2: Velocity buildup
        velocity_prob = 0.0
        if state.is_building_up:
            velocity_prob = state.buildup_strength * 0.8

        # Combine probabilities
        spike_prob = max(tick_exhaustion_prob, velocity_prob)
        spike_prob = min(spike_prob + 0.1 * min(tick_exhaustion_prob, velocity_prob), 1.0)

        # Estimate timing
        spike_tick = None
        if spike_prob > 0.5:
            if velocity_prob > tick_exhaustion_prob:
                # Velocity-driven: imminent (within 5 ticks)
                spike_tick = np.random.randint(1, min(6, n_ticks))
            else:
                # Tick count driven: random in window
                remaining_ticks = int(state.tick_interval_mean - state.ticks_since_spike)
                spike_tick = min(max(remaining_ticks, 1), n_ticks - 1)

        # Direction
        spike_dir = 'UP' if self.is_boom else 'DOWN' if self.is_crash else None

        return spike_prob, spike_tick, spike_dir

    def _inject_spike_scenario(
        self,
        base_prediction: np.ndarray,
        spike_tick: int,
        spike_dir: Literal['UP', 'DOWN'],
        state: TickState
    ) -> np.ndarray:
        """Inject spike into base prediction."""
        prediction = base_prediction.copy()

        # Spike magnitude
        if len(self.spike_magnitudes) > 0:
            magnitude = np.median(self.spike_magnitudes)
        else:
            magnitude = self.typical_tick_change * 10

        # Apply spike
        if spike_dir == 'UP':
            prediction[spike_tick:] += magnitude
        else:
            prediction[spike_tick:] -= magnitude

        # Add retracement after spike
        if spike_tick < len(prediction) - 1:
            retracement = magnitude * 0.5  # 50% retracement
            decay = np.linspace(0, retracement, len(prediction) - spike_tick - 1)

            if spike_dir == 'UP':
                prediction[spike_tick + 1:] -= decay
            else:
                prediction[spike_tick + 1:] += decay

        return prediction

    def _calculate_prediction_confidence(
        self,
        state: TickState,
        spike_prob: float,
        n_ticks: int
    ) -> float:
        """Calculate overall prediction confidence."""
        # Base confidence decreases with prediction horizon
        horizon_decay = np.exp(-n_ticks / 20.0)

        # Confidence higher when spike is imminent (more predictable)
        spike_confidence_boost = spike_prob * 0.3

        # Confidence lower when high volatility
        volatility_penalty = min(state.recent_volatility / self.typical_volatility, 1.0) * 0.2

        confidence = 0.5 * horizon_decay + spike_confidence_boost - volatility_penalty

        return np.clip(confidence, 0.0, 1.0)

    def _build_reasoning(
        self,
        state: TickState,
        spike_prob: float,
        spike_tick: int | None,
        spike_dir: Literal['UP', 'DOWN'] | None
    ) -> list[str]:
        """Build human-readable reasoning."""
        reasoning = []

        # Tick count
        reasoning.append(f"{state.ticks_since_spike} ticks since last spike (avg: {state.tick_interval_mean:.0f})")

        # Velocity
        if state.current_velocity >= 12:
            reasoning.append(f"High velocity: {state.current_velocity} consecutive {state.velocity_direction} ticks")

        # Buildup
        if state.is_building_up:
            reasoning.append(f"Buildup detected (strength: {state.buildup_strength:.1%})")

        # Spike prediction
        if spike_prob > 0.5:
            reasoning.append(f"Spike {spike_dir} expected in ~{spike_tick} ticks (prob: {spike_prob:.1%})")
        elif spike_prob > 0.3:
            reasoning.append(f"Elevated spike risk (prob: {spike_prob:.1%})")
        else:
            reasoning.append("Low spike probability - normal random walk expected")

        return reasoning

    def _detect_spikes(self, prices: np.ndarray, multiplier: float = 3.0) -> np.ndarray:
        """Detect spike indices."""
        changes = np.abs(np.diff(prices))
        positive = changes[changes > 0]

        if len(positive) == 0:
            return np.zeros(len(prices), dtype=bool)

        median = np.median(positive)
        threshold = median * multiplier

        spike_mask = np.zeros(len(prices), dtype=bool)
        spike_mask[1:] = changes > threshold

        return spike_mask

    def _analyze_velocity(self, prices: np.ndarray, lookback: int = 20) -> tuple[int, Literal['UP', 'DOWN', 'NEUTRAL']]:
        """Analyze velocity (consecutive same-direction ticks)."""
        if len(prices) < 2:
            return 0, 'NEUTRAL'

        changes = np.diff(prices[-lookback:])

        if len(changes) == 0:
            return 0, 'NEUTRAL'

        # Current direction
        if changes[-1] > 0:
            current_dir = 'UP'
        elif changes[-1] < 0:
            current_dir = 'DOWN'
        else:
            return 0, 'NEUTRAL'

        # Count consecutive
        count = 0
        for i in range(len(changes) - 1, -1, -1):
            if (current_dir == 'UP' and changes[i] > 0) or (current_dir == 'DOWN' and changes[i] < 0):
                count += 1
            else:
                break

        return count, current_dir

    def _count_velocity(self, prices: np.ndarray) -> int:
        """Count velocity before a spike."""
        velocity, _ = self._analyze_velocity(prices, lookback=len(prices))
        return velocity


if __name__ == "__main__":
    print("Tick Prediction Model Test")
    print("=" * 60)

    # Generate test data
    np.random.seed(42)
    n_ticks = 5000
    historical_prices = np.cumsum(np.random.randn(n_ticks) * 0.01) + 100.0

    # Inject spikes every ~500 ticks
    for i in range(500, n_ticks, 500):
        historical_prices[i:] -= 5.0  # DOWN spikes for CRASH

    # Train predictor
    predictor = TickPredictor(symbol='CRASH500')
    predictor.train(historical_prices)

    # Predict next 20 ticks
    current_state = historical_prices[-100:]
    prediction = predictor.predict_next_ticks(current_state, n_ticks=20)

    print(f"\nPrediction Results:")
    print(f"  Spike probability: {prediction.spike_probability:.1%}")
    if prediction.spike_expected_tick:
        print(f"  Expected spike at tick: {prediction.spike_expected_tick} ({prediction.spike_expected_direction})")
    print(f"  Confidence: {prediction.confidence:.1%}")

    print(f"\nReasoning:")
    for reason in prediction.reasoning:
        print(f"  - {reason}")

    print(f"\nPredicted prices (next 20 ticks):")
    for i, (price, direction) in enumerate(zip(prediction.predicted_prices[:10], prediction.predicted_directions[:10])):
        dir_symbol = '↑' if direction > 0 else '↓' if direction < 0 else '→'
        print(f"  Tick {i+1}: {price:.2f} {dir_symbol}")

    print("\n[OK] Tick prediction test complete!")
