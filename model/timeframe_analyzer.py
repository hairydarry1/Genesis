"""
model/timeframe_analyzer.py — Multi-timeframe analysis for Boom/Crash indices.

Analyzes multiple timeframes simultaneously to identify:
1. Trend alignment across timeframes
2. Support/resistance zones
3. Momentum divergences
4. Entry/exit confluence signals

Usage:
    from model.timeframe_analyzer import TimeframeAnalyzer

    analyzer = TimeframeAnalyzer(tick_data)
    analysis = analyzer.analyze_current_state()
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal
import numpy as np
import pandas as pd
from collections import deque


@dataclass
class TimeframeState:
    """State of a single timeframe."""
    timeframe: str
    trend: Literal['UP', 'DOWN', 'NEUTRAL']
    trend_strength: float  # 0.0 to 1.0
    momentum: float
    volatility: float
    current_price: float

    # Moving averages
    ema_fast: float = 0.0
    ema_slow: float = 0.0

    # Support/resistance
    nearest_support: float = 0.0
    nearest_resistance: float = 0.0
    distance_to_support: float = 0.0
    distance_to_resistance: float = 0.0

    # Patterns
    is_oversold: bool = False
    is_overbought: bool = False

    metadata: dict = field(default_factory=dict)


@dataclass
class MultiTimeframeAnalysis:
    """Complete multi-timeframe analysis result."""

    # Individual timeframe states
    tick: TimeframeState | None = None
    second: TimeframeState | None = None
    minute: TimeframeState | None = None
    five_minute: TimeframeState | None = None

    # Confluence signals
    trend_alignment: float = 0.0  # 0.0 to 1.0
    momentum_alignment: float = 0.0
    entry_signal_strength: float = 0.0

    # Overall assessment
    primary_trend: Literal['UP', 'DOWN', 'NEUTRAL'] = 'NEUTRAL'
    signal: Literal['BUY', 'SELL', 'HOLD'] = 'HOLD'
    confidence: float = 0.0

    # Risk zones
    stop_loss_level: float = 0.0
    take_profit_level: float = 0.0

    reasoning: list[str] = field(default_factory=list)


class TimeframeAnalyzer:
    """
    Multi-timeframe analysis engine.

    Analyzes tick, 1-second, 1-minute, and 5-minute timeframes simultaneously.

    Parameters
    ----------
    tick_prices : np.ndarray
        Tick-level price data
    tick_timestamps : np.ndarray | None
        Timestamps for each tick (optional, for precise aggregation)
    """

    def __init__(
        self,
        tick_prices: np.ndarray,
        tick_timestamps: np.ndarray | None = None,
    ):
        self.tick_prices = tick_prices
        self.tick_timestamps = tick_timestamps

        # Aggregate to different timeframes
        self.second_bars = self._aggregate_to_seconds()
        self.minute_bars = self._aggregate_to_minutes()
        self.five_minute_bars = self._aggregate_to_five_minutes()

    def _aggregate_to_seconds(self) -> pd.DataFrame:
        """Aggregate ticks to 1-second bars."""
        if self.tick_timestamps is not None:
            # Use actual timestamps
            df = pd.DataFrame({
                'timestamp': pd.to_datetime(self.tick_timestamps, unit='s'),
                'price': self.tick_prices
            })
            df['second'] = df['timestamp'].dt.floor('1s')

            bars = df.groupby('second').agg({
                'price': ['first', 'max', 'min', 'last', 'count']
            }).reset_index()

            bars.columns = ['timestamp', 'open', 'high', 'low', 'close', 'ticks']
        else:
            # Simulate 1-second bars (assume ~10 ticks per second)
            ticks_per_second = 10
            n_bars = len(self.tick_prices) // ticks_per_second

            bars_list = []
            for i in range(n_bars):
                start = i * ticks_per_second
                end = min(start + ticks_per_second, len(self.tick_prices))
                segment = self.tick_prices[start:end]

                if len(segment) > 0:
                    bars_list.append({
                        'open': segment[0],
                        'high': np.max(segment),
                        'low': np.min(segment),
                        'close': segment[-1],
                        'ticks': len(segment),
                    })

            bars = pd.DataFrame(bars_list)

        return bars

    def _aggregate_to_minutes(self) -> pd.DataFrame:
        """Aggregate seconds to 1-minute bars."""
        if len(self.second_bars) == 0:
            return pd.DataFrame()

        bars_per_minute = 60
        n_bars = len(self.second_bars) // bars_per_minute

        bars_list = []
        for i in range(n_bars):
            start = i * bars_per_minute
            end = min(start + bars_per_minute, len(self.second_bars))
            segment = self.second_bars.iloc[start:end]

            if len(segment) > 0:
                bars_list.append({
                    'open': segment['open'].iloc[0],
                    'high': segment['high'].max(),
                    'low': segment['low'].min(),
                    'close': segment['close'].iloc[-1],
                    'volume': segment['ticks'].sum(),
                })

        return pd.DataFrame(bars_list)

    def _aggregate_to_five_minutes(self) -> pd.DataFrame:
        """Aggregate minutes to 5-minute bars."""
        if len(self.minute_bars) == 0:
            return pd.DataFrame()

        bars_per_five_min = 5
        n_bars = len(self.minute_bars) // bars_per_five_min

        bars_list = []
        for i in range(n_bars):
            start = i * bars_per_five_min
            end = min(start + bars_per_five_min, len(self.minute_bars))
            segment = self.minute_bars.iloc[start:end]

            if len(segment) > 0:
                bars_list.append({
                    'open': segment['open'].iloc[0],
                    'high': segment['high'].max(),
                    'low': segment['low'].min(),
                    'close': segment['close'].iloc[-1],
                    'volume': segment['volume'].sum(),
                })

        return pd.DataFrame(bars_list)

    def analyze_timeframe(
        self,
        bars: pd.DataFrame,
        timeframe: str,
        lookback: int = 20
    ) -> TimeframeState:
        """
        Analyze a single timeframe.

        Parameters
        ----------
        bars : pd.DataFrame
            OHLC bars for this timeframe
        timeframe : str
            Name of timeframe ('tick', '1s', '1m', '5m')
        lookback : int
            Number of bars to analyze

        Returns
        -------
        TimeframeState
            Complete state for this timeframe
        """
        if len(bars) < 2:
            return TimeframeState(
                timeframe=timeframe,
                trend='NEUTRAL',
                trend_strength=0.0,
                momentum=0.0,
                volatility=0.0,
                current_price=0.0
            )

        recent = bars.tail(lookback)
        current_price = recent['close'].iloc[-1]

        # Calculate EMAs
        ema_fast = recent['close'].ewm(span=10, adjust=False).mean().iloc[-1]
        ema_slow = recent['close'].ewm(span=20, adjust=False).mean().iloc[-1]

        # Determine trend
        if ema_fast > ema_slow * 1.001:
            trend = 'UP'
            trend_strength = (ema_fast - ema_slow) / ema_slow
        elif ema_fast < ema_slow * 0.999:
            trend = 'DOWN'
            trend_strength = (ema_slow - ema_fast) / ema_fast
        else:
            trend = 'NEUTRAL'
            trend_strength = 0.0

        trend_strength = min(trend_strength, 1.0)

        # Calculate momentum
        if len(recent) >= 5:
            momentum = (recent['close'].iloc[-1] - recent['close'].iloc[-5]) / recent['close'].iloc[-5]
        else:
            momentum = 0.0

        # Calculate volatility
        if 'high' in recent.columns and 'low' in recent.columns:
            ranges = recent['high'] - recent['low']
            volatility = ranges.mean() / current_price
        else:
            volatility = recent['close'].pct_change().std()

        # Support and resistance
        recent_highs = recent['high'].values if 'high' in recent.columns else recent['close'].values
        recent_lows = recent['low'].values if 'low' in recent.columns else recent['close'].values

        resistance_levels = recent_highs[recent_highs > current_price]
        support_levels = recent_lows[recent_lows < current_price]

        nearest_resistance = np.min(resistance_levels) if len(resistance_levels) > 0 else current_price * 1.01
        nearest_support = np.max(support_levels) if len(support_levels) > 0 else current_price * 0.99

        distance_to_resistance = (nearest_resistance - current_price) / current_price
        distance_to_support = (current_price - nearest_support) / current_price

        # Overbought/oversold
        prices = recent['close'].values
        if len(prices) >= 14:
            rsi = self._calculate_rsi(prices, period=14)
            is_oversold = rsi < 30
            is_overbought = rsi > 70
        else:
            is_oversold = False
            is_overbought = False

        return TimeframeState(
            timeframe=timeframe,
            trend=trend,
            trend_strength=trend_strength,
            momentum=momentum,
            volatility=volatility,
            current_price=current_price,
            ema_fast=ema_fast,
            ema_slow=ema_slow,
            nearest_support=nearest_support,
            nearest_resistance=nearest_resistance,
            distance_to_support=distance_to_support,
            distance_to_resistance=distance_to_resistance,
            is_oversold=is_oversold,
            is_overbought=is_overbought,
        )

    def analyze_current_state(self) -> MultiTimeframeAnalysis:
        """
        Perform complete multi-timeframe analysis of current state.

        Returns
        -------
        MultiTimeframeAnalysis
            Complete analysis with confluence signals
        """
        analysis = MultiTimeframeAnalysis()

        # Analyze each timeframe
        if len(self.tick_prices) > 0:
            tick_bars = pd.DataFrame({'close': self.tick_prices[-100:]})
            analysis.tick = self.analyze_timeframe(tick_bars, 'tick', lookback=50)

        if len(self.second_bars) > 0:
            analysis.second = self.analyze_timeframe(self.second_bars, '1s', lookback=60)

        if len(self.minute_bars) > 0:
            analysis.minute = self.analyze_timeframe(self.minute_bars, '1m', lookback=20)

        if len(self.five_minute_bars) > 0:
            analysis.five_minute = self.analyze_timeframe(self.five_minute_bars, '5m', lookback=12)

        # Calculate confluence
        timeframes = [analysis.tick, analysis.second, analysis.minute, analysis.five_minute]
        timeframes = [tf for tf in timeframes if tf is not None]

        if len(timeframes) == 0:
            return analysis

        # Trend alignment
        up_count = sum(1 for tf in timeframes if tf.trend == 'UP')
        down_count = sum(1 for tf in timeframes if tf.trend == 'DOWN')

        analysis.trend_alignment = max(up_count, down_count) / len(timeframes)

        if up_count > down_count:
            analysis.primary_trend = 'UP'
        elif down_count > up_count:
            analysis.primary_trend = 'DOWN'
        else:
            analysis.primary_trend = 'NEUTRAL'

        # Momentum alignment
        avg_momentum = np.mean([tf.momentum for tf in timeframes])
        momentum_std = np.std([tf.momentum for tf in timeframes])
        analysis.momentum_alignment = 1.0 - min(momentum_std, 1.0)

        # Entry signal
        if analysis.trend_alignment >= 0.75 and analysis.momentum_alignment >= 0.6:
            if analysis.primary_trend == 'UP':
                analysis.signal = 'BUY'
                analysis.reasoning.append("Strong uptrend across timeframes")
            elif analysis.primary_trend == 'DOWN':
                analysis.signal = 'SELL'
                analysis.reasoning.append("Strong downtrend across timeframes")
        else:
            analysis.signal = 'HOLD'
            analysis.reasoning.append("Weak or conflicting signals")

        # Confidence
        analysis.confidence = (analysis.trend_alignment + analysis.momentum_alignment) / 2.0

        # Risk levels
        current_price = timeframes[0].current_price

        if analysis.signal == 'BUY':
            analysis.stop_loss_level = min(tf.nearest_support for tf in timeframes if tf.nearest_support > 0)
            analysis.take_profit_level = max(tf.nearest_resistance for tf in timeframes if tf.nearest_resistance > 0)
        elif analysis.signal == 'SELL':
            analysis.stop_loss_level = max(tf.nearest_resistance for tf in timeframes if tf.nearest_resistance > 0)
            analysis.take_profit_level = min(tf.nearest_support for tf in timeframes if tf.nearest_support > 0)
        else:
            analysis.stop_loss_level = current_price * 0.99
            analysis.take_profit_level = current_price * 1.01

        return analysis

    def _calculate_rsi(self, prices: np.ndarray, period: int = 14) -> float:
        """Calculate RSI indicator."""
        deltas = np.diff(prices)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)

        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])

        if avg_loss == 0:
            return 100.0

        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))

        return rsi

    def get_summary(self, analysis: MultiTimeframeAnalysis) -> str:
        """Get human-readable summary of analysis."""
        lines = []
        lines.append("=" * 60)
        lines.append("MULTI-TIMEFRAME ANALYSIS")
        lines.append("=" * 60)

        for tf_name, tf_state in [
            ('Tick', analysis.tick),
            ('1 Second', analysis.second),
            ('1 Minute', analysis.minute),
            ('5 Minute', analysis.five_minute),
        ]:
            if tf_state:
                lines.append(f"\n{tf_name}:")
                lines.append(f"  Trend: {tf_state.trend} (strength: {tf_state.trend_strength:.2f})")
                lines.append(f"  Momentum: {tf_state.momentum:+.4f}")
                lines.append(f"  Volatility: {tf_state.volatility:.4f}")
                lines.append(f"  Price: {tf_state.current_price:.2f}")
                lines.append(f"  Support: {tf_state.nearest_support:.2f} ({tf_state.distance_to_support:.2%} away)")
                lines.append(f"  Resistance: {tf_state.nearest_resistance:.2f} ({tf_state.distance_to_resistance:.2%} away)")

        lines.append(f"\n{'=' * 60}")
        lines.append("CONFLUENCE ANALYSIS")
        lines.append("=" * 60)
        lines.append(f"Primary Trend: {analysis.primary_trend}")
        lines.append(f"Trend Alignment: {analysis.trend_alignment:.1%}")
        lines.append(f"Momentum Alignment: {analysis.momentum_alignment:.1%}")
        lines.append(f"\nSignal: {analysis.signal}")
        lines.append(f"Confidence: {analysis.confidence:.1%}")

        if analysis.signal != 'HOLD':
            lines.append(f"\nStop Loss: {analysis.stop_loss_level:.2f}")
            lines.append(f"Take Profit: {analysis.take_profit_level:.2f}")

        if analysis.reasoning:
            lines.append(f"\nReasoning:")
            for reason in analysis.reasoning:
                lines.append(f"  - {reason}")

        return "\n".join(lines)


if __name__ == "__main__":
    print("Multi-Timeframe Analysis Test")
    print("=" * 60)

    # Generate test data
    np.random.seed(42)
    n_ticks = 10000
    tick_prices = np.cumsum(np.random.randn(n_ticks) * 0.01) + 100.0

    # Add trend
    trend = np.linspace(0, 2, n_ticks)
    tick_prices += trend

    # Create analyzer
    analyzer = TimeframeAnalyzer(tick_prices)

    # Analyze current state
    analysis = analyzer.analyze_current_state()

    # Print summary
    print(analyzer.get_summary(analysis))

    print("\n[OK] Multi-timeframe analysis test complete!")
