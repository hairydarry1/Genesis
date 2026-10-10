"""
model/pattern_recognition.py — Advanced pattern recognition for Boom/Crash indices.

Identifies:
1. Pre-spike velocity signatures
2. Tick count exhaustion patterns
3. Multi-tick buildup patterns
4. Spike cluster patterns
5. Retracement patterns

Usage:
    from model.pattern_recognition import PatternRecognizer

    recognizer = PatternRecognizer(symbol='CRASH500')
    patterns = recognizer.analyze(tick_data)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import numpy as np
import pandas as pd
from collections import deque


@dataclass
class Pattern:
    """Detected pattern with confidence score."""
    pattern_type: str
    confidence: float  # 0.0 to 1.0
    location: int      # tick index
    direction: Literal['UP', 'DOWN', 'NEUTRAL']
    strength: float    # magnitude/significance
    metadata: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"{self.pattern_type}@{self.location} ({self.direction}, conf={self.confidence:.2f})"


@dataclass
class SpikePrediction:
    """Spike prediction with timing and direction."""
    probability: float      # 0.0 to 1.0
    expected_direction: Literal['UP', 'DOWN']
    expected_ticks: int     # ticks until spike
    expected_magnitude: float
    reasoning: str


class PatternRecognizer:
    """
    Advanced pattern recognition for Boom/Crash indices.

    Parameters
    ----------
    symbol : str
        'BOOM300', 'BOOM500', 'BOOM1000', 'CRASH300', 'CRASH500', 'CRASH1000'
    velocity_threshold : int
        Consecutive same-direction ticks to trigger velocity alert
    tick_count_threshold : int
        Ticks since last spike to trigger imminent spike alert
    """

    def __init__(
        self,
        symbol: str,
        velocity_threshold: int = 12,
        tick_count_threshold: int | None = None,
    ):
        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        # Extract index number (300/500/1000)
        if '300' in self.symbol:
            self.index_number = 300
        elif '500' in self.symbol:
            self.index_number = 500
        elif '1000' in self.symbol:
            self.index_number = 1000
        else:
            self.index_number = 500  # default

        self.velocity_threshold = velocity_threshold
        self.tick_count_threshold = tick_count_threshold or int(self.index_number * 1.4)

        # Pattern detection state
        self._last_spike_idx: int = 0
        self._spike_history: deque = deque(maxlen=100)
        self._velocity_history: deque = deque(maxlen=50)

    def detect_spikes(self, prices: np.ndarray, multiplier: float = 3.0) -> np.ndarray:
        """
        Detect spike indices in price data.

        Parameters
        ----------
        prices : np.ndarray
            Tick prices
        multiplier : float
            Spike threshold as multiple of median move

        Returns
        -------
        np.ndarray
            Boolean array marking spike locations
        """
        changes = np.abs(np.diff(prices))
        positive_changes = changes[changes > 0]

        if len(positive_changes) == 0:
            return np.zeros(len(prices), dtype=bool)

        median_move = np.median(positive_changes)
        threshold = median_move * multiplier

        # Pad to match original length
        spike_mask = np.zeros(len(prices), dtype=bool)
        spike_mask[1:] = changes > threshold

        return spike_mask

    def analyze_velocity_buildup(
        self,
        prices: np.ndarray,
        lookback: int = 20
    ) -> tuple[int, str]:
        """
        Analyze velocity buildup in recent ticks.

        Parameters
        ----------
        prices : np.ndarray
            Recent tick prices (last 20-50 ticks)
        lookback : int
            Number of ticks to analyze

        Returns
        -------
        tuple[int, str]
            (consecutive_count, direction)
            - consecutive_count: number of consecutive same-direction ticks
            - direction: 'UP', 'DOWN', or 'NEUTRAL'
        """
        if len(prices) < 2:
            return 0, 'NEUTRAL'

        changes = np.diff(prices[-lookback:])

        # Count consecutive upticks or downticks from the end
        if len(changes) == 0:
            return 0, 'NEUTRAL'

        # Start from most recent
        current_direction = 'UP' if changes[-1] > 0 else 'DOWN' if changes[-1] < 0 else 'NEUTRAL'

        if current_direction == 'NEUTRAL':
            return 0, 'NEUTRAL'

        count = 0
        for i in range(len(changes) - 1, -1, -1):
            if current_direction == 'UP' and changes[i] > 0:
                count += 1
            elif current_direction == 'DOWN' and changes[i] < 0:
                count += 1
            else:
                break

        return count, current_direction

    def predict_spike(
        self,
        prices: np.ndarray,
        current_idx: int
    ) -> SpikePrediction:
        """
        Predict imminent spike based on multiple signals.

        Parameters
        ----------
        prices : np.ndarray
            Full tick price history
        current_idx : int
            Current tick index

        Returns
        -------
        SpikePrediction
            Spike prediction with probability and timing
        """
        # Signal 1: Tick count exhaustion
        ticks_since_spike = current_idx - self._last_spike_idx
        tick_exhaustion_prob = self._compute_tick_exhaustion_prob(ticks_since_spike)

        # Signal 2: Velocity buildup
        recent_prices = prices[max(0, current_idx - 50):current_idx + 1]
        velocity_count, velocity_dir = self.analyze_velocity_buildup(recent_prices)
        velocity_prob = self._compute_velocity_prob(velocity_count, velocity_dir)

        # Signal 3: Historical spike intervals
        avg_interval = self._compute_average_spike_interval()
        interval_prob = self._compute_interval_prob(ticks_since_spike, avg_interval)

        # Combine signals
        combined_prob = self._combine_probabilities([
            (tick_exhaustion_prob, 0.4),
            (velocity_prob, 0.4),
            (interval_prob, 0.2),
        ])

        # Determine expected direction
        if self.is_boom:
            expected_dir = 'UP'
        elif self.is_crash:
            expected_dir = 'DOWN'
        else:
            expected_dir = 'NEUTRAL'

        # Estimate timing and magnitude
        expected_ticks = self._estimate_ticks_to_spike(ticks_since_spike, avg_interval)
        expected_magnitude = self._estimate_spike_magnitude(prices)

        # Build reasoning
        reasoning_parts = []
        if tick_exhaustion_prob > 0.5:
            reasoning_parts.append(f"{ticks_since_spike} ticks since last spike (threshold {self.tick_count_threshold})")
        if velocity_prob > 0.5:
            reasoning_parts.append(f"{velocity_count} consecutive {velocity_dir} ticks (threshold {self.velocity_threshold})")
        if interval_prob > 0.3:
            reasoning_parts.append(f"Approaching average interval of {avg_interval:.0f} ticks")

        reasoning = "; ".join(reasoning_parts) if reasoning_parts else "Low confidence prediction"

        return SpikePrediction(
            probability=combined_prob,
            expected_direction=expected_dir,
            expected_ticks=expected_ticks,
            expected_magnitude=expected_magnitude,
            reasoning=reasoning,
        )

    def detect_patterns(
        self,
        prices: np.ndarray,
        window_size: int = 100
    ) -> list[Pattern]:
        """
        Detect all patterns in the given price window.

        Parameters
        ----------
        prices : np.ndarray
            Tick prices
        window_size : int
            Number of recent ticks to analyze

        Returns
        -------
        list[Pattern]
            Detected patterns sorted by confidence (descending)
        """
        patterns = []
        current_idx = len(prices) - 1

        # Pattern 1: Velocity exhaustion
        velocity_count, velocity_dir = self.analyze_velocity_buildup(prices, lookback=30)
        if velocity_count >= self.velocity_threshold:
            patterns.append(Pattern(
                pattern_type='VELOCITY_EXHAUSTION',
                confidence=min(velocity_count / 25.0, 1.0),
                location=current_idx,
                direction=velocity_dir,
                strength=velocity_count,
                metadata={'velocity_count': velocity_count}
            ))

        # Pattern 2: Tick count exhaustion
        ticks_since_spike = current_idx - self._last_spike_idx
        if ticks_since_spike >= self.tick_count_threshold:
            patterns.append(Pattern(
                pattern_type='TICK_EXHAUSTION',
                confidence=self._compute_tick_exhaustion_prob(ticks_since_spike),
                location=current_idx,
                direction='UP' if self.is_boom else 'DOWN',
                strength=ticks_since_spike,
                metadata={'ticks_since_spike': ticks_since_spike}
            ))

        # Pattern 3: Spike clusters
        spike_mask = self.detect_spikes(prices[-window_size:])
        recent_spikes = np.where(spike_mask)[0]
        if len(recent_spikes) >= 2:
            # Check for clusters (spikes within 5 bars)
            for i in range(len(recent_spikes) - 1):
                gap = recent_spikes[i + 1] - recent_spikes[i]
                if gap <= 5:
                    patterns.append(Pattern(
                        pattern_type='SPIKE_CLUSTER',
                        confidence=0.7,
                        location=window_size - len(prices) + recent_spikes[i],
                        direction='NEUTRAL',
                        strength=gap,
                        metadata={'gap': gap, 'cluster_size': 2}
                    ))

        # Pattern 4: Retracement after spike
        if len(recent_spikes) > 0:
            last_spike_idx = recent_spikes[-1]
            bars_since = len(spike_mask) - last_spike_idx - 1
            if 2 <= bars_since <= 10:
                # Calculate retracement percentage
                spike_price = prices[-window_size + last_spike_idx]
                current_price = prices[-1]
                pre_spike_price = prices[-window_size + last_spike_idx - 1] if last_spike_idx > 0 else spike_price

                spike_move = spike_price - pre_spike_price
                retracement = current_price - spike_price

                if abs(spike_move) > 0:
                    retracement_pct = abs(retracement / spike_move)

                    patterns.append(Pattern(
                        pattern_type='RETRACEMENT',
                        confidence=min(retracement_pct, 1.0),
                        location=current_idx,
                        direction='UP' if retracement > 0 else 'DOWN',
                        strength=retracement_pct,
                        metadata={
                            'retracement_pct': retracement_pct,
                            'bars_since_spike': bars_since
                        }
                    ))

        # Sort by confidence
        patterns.sort(key=lambda p: p.confidence, reverse=True)

        return patterns

    def update(self, prices: np.ndarray) -> None:
        """
        Update internal state with new price data.

        Parameters
        ----------
        prices : np.ndarray
            Full tick price history
        """
        # Detect spikes and update last spike index
        spike_mask = self.detect_spikes(prices)
        spike_indices = np.where(spike_mask)[0]

        if len(spike_indices) > 0:
            self._last_spike_idx = spike_indices[-1]

            # Update spike history
            for idx in spike_indices:
                if idx not in [s['idx'] for s in self._spike_history]:
                    self._spike_history.append({
                        'idx': idx,
                        'price': prices[idx],
                        'magnitude': abs(prices[idx] - prices[idx - 1]) if idx > 0 else 0
                    })

        # Update velocity history
        if len(prices) >= 2:
            velocity_count, velocity_dir = self.analyze_velocity_buildup(prices, lookback=20)
            self._velocity_history.append({
                'count': velocity_count,
                'direction': velocity_dir
            })

    def _compute_tick_exhaustion_prob(self, ticks_since_spike: int) -> float:
        """Compute probability of imminent spike based on tick count."""
        if ticks_since_spike < self.tick_count_threshold * 0.8:
            return 0.0
        elif ticks_since_spike >= self.tick_count_threshold * 1.5:
            return 0.99
        else:
            # Linear interpolation
            ratio = (ticks_since_spike - self.tick_count_threshold * 0.8) / (self.tick_count_threshold * 0.7)
            return min(ratio, 1.0)

    def _compute_velocity_prob(self, count: int, direction: str) -> float:
        """Compute probability based on velocity buildup."""
        # For CRASH: UP velocity predicts DOWN spike
        # For BOOM: DOWN velocity predicts UP spike
        is_correct_direction = (
            (self.is_crash and direction == 'UP') or
            (self.is_boom and direction == 'DOWN')
        )

        if not is_correct_direction:
            return 0.0

        if count < self.velocity_threshold:
            return 0.0
        elif count >= 20:
            return 0.95
        else:
            # Linear from threshold to 20
            ratio = (count - self.velocity_threshold) / (20 - self.velocity_threshold)
            return 0.5 + 0.45 * ratio

    def _compute_interval_prob(self, current_interval: int, avg_interval: float) -> float:
        """Compute probability based on historical spike intervals."""
        if avg_interval == 0:
            return 0.0

        ratio = current_interval / avg_interval

        if ratio < 0.8:
            return 0.0
        elif ratio >= 1.5:
            return 0.8
        else:
            return (ratio - 0.8) / 0.7

    def _compute_average_spike_interval(self) -> float:
        """Compute average interval between spikes from history."""
        if len(self._spike_history) < 2:
            return float(self.index_number)  # Use theoretical average

        intervals = []
        for i in range(1, len(self._spike_history)):
            interval = self._spike_history[i]['idx'] - self._spike_history[i - 1]['idx']
            intervals.append(interval)

        return np.mean(intervals) if intervals else float(self.index_number)

    def _estimate_ticks_to_spike(self, ticks_since_spike: int, avg_interval: float) -> int:
        """Estimate how many ticks until next spike."""
        if ticks_since_spike >= self.tick_count_threshold:
            # Imminent
            return np.random.randint(1, 50)
        else:
            remaining = int(avg_interval - ticks_since_spike)
            return max(remaining, 10)

    def _estimate_spike_magnitude(self, prices: np.ndarray) -> float:
        """Estimate expected spike magnitude based on history."""
        if len(self._spike_history) < 3:
            # Use typical values based on index
            typical_magnitudes = {
                300: 3.0,
                500: 5.0,
                1000: 8.0,
            }
            return typical_magnitudes.get(self.index_number, 5.0)

        recent_magnitudes = [s['magnitude'] for s in list(self._spike_history)[-10:]]
        return np.median(recent_magnitudes)

    def _combine_probabilities(self, weighted_probs: list[tuple[float, float]]) -> float:
        """
        Combine multiple probability signals with weights.

        Parameters
        ----------
        weighted_probs : list[tuple[float, float]]
            List of (probability, weight) tuples

        Returns
        -------
        float
            Combined probability (0.0 to 1.0)
        """
        total_weight = sum(w for _, w in weighted_probs)
        if total_weight == 0:
            return 0.0

        weighted_sum = sum(p * w for p, w in weighted_probs)
        return min(weighted_sum / total_weight, 1.0)


if __name__ == "__main__":
    # Quick test
    print("Pattern Recognition System for Boom/Crash Indices")
    print("=" * 60)

    # Simulate some tick data
    np.random.seed(42)
    base_prices = np.cumsum(np.random.randn(1000) * 0.01) + 100.0

    # Inject spikes every ~500 ticks
    spike_indices = [200, 450, 750]
    for idx in spike_indices:
        base_prices[idx] += 5.0  # UP spike

    # Test BOOM500 recognizer
    recognizer = PatternRecognizer(symbol='BOOM500')
    recognizer.update(base_prices)

    patterns = recognizer.detect_patterns(base_prices, window_size=200)

    print(f"\nDetected {len(patterns)} patterns:")
    for p in patterns[:5]:
        print(f"  {p}")

    # Test spike prediction
    prediction = recognizer.predict_spike(base_prices, len(base_prices) - 1)
    print(f"\nSpike Prediction:")
    print(f"  Probability: {prediction.probability:.1%}")
    print(f"  Direction: {prediction.expected_direction}")
    print(f"  Expected in: {prediction.expected_ticks} ticks")
    print(f"  Expected magnitude: {prediction.expected_magnitude:.2f}")
    print(f"  Reasoning: {prediction.reasoning}")

    print("\n[OK] Pattern recognition test complete!")
