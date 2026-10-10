"""
strategies/regime_adaptive.py — Adaptive strategy that changes behavior based on market regime.

Regimes:
1. TRENDING_UP - Strong upward momentum
2. TRENDING_DOWN - Strong downward momentum
3. RANGING - Sideways, mean-reverting
4. HIGH_VOLATILITY - Spike clusters, dangerous
5. LOW_VOLATILITY - Calm, predictable

Strategy adapts entry/exit logic based on current regime.

Usage:
    python -m strategies.regime_adaptive --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import Literal
from enum import Enum

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position


class MarketRegime(Enum):
    """Market regime types."""
    TRENDING_UP = 'TRENDING_UP'
    TRENDING_DOWN = 'TRENDING_DOWN'
    RANGING = 'RANGING'
    HIGH_VOLATILITY = 'HIGH_VOLATILITY'
    LOW_VOLATILITY = 'LOW_VOLATILITY'


@dataclass
class RegimeState:
    """Current regime state."""
    regime: MarketRegime
    confidence: float  # 0.0 to 1.0
    duration: int  # Bars in current regime

    # Regime characteristics
    trend_strength: float
    volatility: float
    spike_frequency: float


class RegimeAdaptive(Strategy):
    """
    Adaptive strategy that changes behavior based on market regime.

    Regime Detection:
    - Trend: EMA slope and ADX
    - Volatility: ATR and standard deviation
    - Spike frequency: Recent spike count

    Adaptive Behavior:
    - TRENDING: Follow trend, wider stops
    - RANGING: Mean reversion, tight targets
    - HIGH_VOL: Reduce size, tighter stops
    - LOW_VOL: Normal operation
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        # Regime detection params
        trend_ema_period: int = 20,
        volatility_period: int = 14,
        regime_lookback: int = 50,
        # Ranging regime params
        ranging_tp_mult: float = 0.5,
        ranging_sl_mult: float = 1.0,
        # Trending regime params
        trending_tp_mult: float = 3.0,
        trending_sl_mult: float = 2.0,
        # Volatility adjustments
        high_vol_size_mult: float = 0.5,
        high_vol_max_hold: int = 10,
        normal_max_hold: int = 30,
    ):
        super().__init__('RegimeAdaptive')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        self.trend_ema_period = trend_ema_period
        self.volatility_period = volatility_period
        self.regime_lookback = regime_lookback

        self.ranging_tp_mult = ranging_tp_mult
        self.ranging_sl_mult = ranging_sl_mult
        self.trending_tp_mult = trending_tp_mult
        self.trending_sl_mult = trending_sl_mult

        self.high_vol_size_mult = high_vol_size_mult
        self.high_vol_max_hold = high_vol_max_hold
        self.normal_max_hold = normal_max_hold

        # State
        self.current_regime: RegimeState | None = None
        self.entry_regime: MarketRegime | None = None
        self.avg_range: float = 0.0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Entry logic adapts to current regime."""

        if idx < self.regime_lookback:
            return False, None

        # Detect current regime
        self.current_regime = self._detect_regime(prices, idx)

        current_price = prices[idx]

        # Don't enter in HIGH_VOLATILITY regime
        if self.current_regime.regime == MarketRegime.HIGH_VOLATILITY:
            return False, None

        # RANGING: Mean reversion entries
        if self.current_regime.regime == MarketRegime.RANGING:
            return self._ranging_entry(prices, idx, current_price)

        # TRENDING: Trend following entries
        elif self.current_regime.regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]:
            return self._trending_entry(prices, idx, current_price)

        # LOW_VOLATILITY: Standard spike fade
        elif self.current_regime.regime == MarketRegime.LOW_VOLATILITY:
            return self._low_vol_entry(prices, idx, current_price)

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Exit logic adapts to entry regime."""

        if self.entry_regime is None:
            return False, ''

        current_price = prices[idx]

        # Update current regime
        current_regime = self._detect_regime(prices, idx)

        # Exit if regime changed dramatically
        if current_regime.regime == MarketRegime.HIGH_VOLATILITY:
            return True, 'REGIME_CHANGE_HIGH_VOL'

        # Regime-specific exit logic
        if self.entry_regime == MarketRegime.RANGING:
            return self._ranging_exit(prices, idx, position, current_price)

        elif self.entry_regime in [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN]:
            return self._trending_exit(prices, idx, position, current_price)

        elif self.entry_regime == MarketRegime.LOW_VOLATILITY:
            return self._low_vol_exit(prices, idx, position, current_price)

        # Default timeout
        max_hold = self.high_vol_max_hold if current_regime.volatility > self.avg_range * 2 else self.normal_max_hold
        if position.bars_held >= max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def _detect_regime(self, prices: np.ndarray, idx: int) -> RegimeState:
        """Detect current market regime."""

        recent = prices[max(0, idx - self.regime_lookback):idx + 1]

        # Calculate trend strength (EMA slope)
        ema = self._calculate_ema(recent, self.trend_ema_period)
        ema_slope = (ema[-1] - ema[-10]) / ema[-10] if len(ema) >= 10 else 0
        trend_strength = abs(ema_slope)

        # Calculate volatility
        returns = np.diff(recent) / recent[:-1]
        volatility = np.std(returns)
        self.avg_range = volatility

        # Calculate spike frequency
        spike_threshold = np.median(np.abs(np.diff(recent))) * 3
        spike_count = np.sum(np.abs(np.diff(recent)) > spike_threshold)
        spike_frequency = spike_count / len(recent)

        # Determine regime
        regime = None
        confidence = 0.0

        # HIGH VOLATILITY (priority - most dangerous)
        if spike_frequency > 0.05 or volatility > np.percentile(returns, 95):
            regime = MarketRegime.HIGH_VOLATILITY
            confidence = min(spike_frequency * 20, 1.0)

        # TRENDING UP
        elif ema_slope > 0.002 and trend_strength > 0.001:
            regime = MarketRegime.TRENDING_UP
            confidence = min(trend_strength * 100, 1.0)

        # TRENDING DOWN
        elif ema_slope < -0.002 and trend_strength > 0.001:
            regime = MarketRegime.TRENDING_DOWN
            confidence = min(trend_strength * 100, 1.0)

        # LOW VOLATILITY
        elif volatility < np.percentile(returns, 25):
            regime = MarketRegime.LOW_VOLATILITY
            confidence = 0.7

        # RANGING (default)
        else:
            regime = MarketRegime.RANGING
            confidence = 0.5

        return RegimeState(
            regime=regime,
            confidence=confidence,
            duration=0,  # Simplified - would track in production
            trend_strength=trend_strength,
            volatility=volatility,
            spike_frequency=spike_frequency,
        )

    def _ranging_entry(self, prices: np.ndarray, idx: int, current_price: float) -> tuple[bool, str | None]:
        """Mean reversion entry in ranging market."""

        # Enter when price deviates from mean
        recent = prices[max(0, idx - 20):idx + 1]
        mean_price = np.mean(recent)
        std_price = np.std(recent)

        z_score = (current_price - mean_price) / std_price if std_price > 0 else 0

        # Enter LONG when oversold (z < -1.5)
        if z_score < -1.5:
            return True, 'LONG'

        # Enter SHORT when overbought (z > 1.5)
        if z_score > 1.5:
            return True, 'SHORT'

        return False, None

    def _trending_entry(self, prices: np.ndarray, idx: int, current_price: float) -> tuple[bool, str | None]:
        """Trend following entry."""

        # Enter on pullbacks in trend direction
        ema_short = self._calculate_ema(prices[max(0, idx - 20):idx + 1], 10)
        ema_long = self._calculate_ema(prices[max(0, idx - 50):idx + 1], 20)

        if len(ema_short) < 2 or len(ema_long) < 2:
            return False, None

        # LONG: Short-term pullback in uptrend
        if ema_short[-1] > ema_long[-1]:  # Uptrend
            if current_price < ema_short[-1] * 0.999:  # Pullback
                return True, 'LONG'

        # SHORT: Short-term rally in downtrend
        elif ema_short[-1] < ema_long[-1]:  # Downtrend
            if current_price > ema_short[-1] * 1.001:  # Rally
                return True, 'SHORT'

        return False, None

    def _low_vol_entry(self, prices: np.ndarray, idx: int, current_price: float) -> tuple[bool, str | None]:
        """Standard spike fade entry in low volatility."""

        # Detect recent spike
        if idx < 5:
            return False, None

        recent_changes = np.abs(np.diff(prices[idx - 5:idx + 1]))
        median_change = np.median(recent_changes)

        # If there was a spike 2-3 bars ago, fade it
        for lookback in [2, 3]:
            if idx >= lookback:
                spike_move = abs(prices[idx - lookback] - prices[idx - lookback - 1])
                if spike_move > median_change * 3:
                    # Fade: enter opposite
                    if prices[idx - lookback] > prices[idx - lookback - 1]:
                        return True, 'SHORT'
                    else:
                        return True, 'LONG'

        return False, None

    def _ranging_exit(self, prices: np.ndarray, idx: int, position: Position, current_price: float) -> tuple[bool, str]:
        """Mean reversion exit - tight targets."""

        recent = prices[max(0, idx - 20):idx + 1]
        std_price = np.std(recent)

        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            if profit >= std_price * self.ranging_tp_mult:
                return True, 'TP_RANGING'
            if profit <= -std_price * self.ranging_sl_mult:
                return True, 'SL_RANGING'

        else:  # SHORT
            profit = position.entry_price - current_price
            if profit >= std_price * self.ranging_tp_mult:
                return True, 'TP_RANGING'
            if profit <= -std_price * self.ranging_sl_mult:
                return True, 'SL_RANGING'

        return False, ''

    def _trending_exit(self, prices: np.ndarray, idx: int, position: Position, current_price: float) -> tuple[bool, str]:
        """Trend following exit - wide targets, trailing stop."""

        recent = prices[max(0, idx - 20):idx + 1]
        atr = np.mean(np.abs(np.diff(recent)))

        if position.direction == 'LONG':
            profit = current_price - position.entry_price

            # Wide TP
            if profit >= atr * self.trending_tp_mult:
                return True, 'TP_TRENDING'

            # Trailing stop (trend reversal)
            ema = self._calculate_ema(recent, 10)
            if len(ema) >= 2 and current_price < ema[-1]:
                return True, 'TRAIL_TREND_REVERSAL'

            # Wide SL
            if profit <= -atr * self.trending_sl_mult:
                return True, 'SL_TRENDING'

        else:  # SHORT
            profit = position.entry_price - current_price

            if profit >= atr * self.trending_tp_mult:
                return True, 'TP_TRENDING'

            ema = self._calculate_ema(recent, 10)
            if len(ema) >= 2 and current_price > ema[-1]:
                return True, 'TRAIL_TREND_REVERSAL'

            if profit <= -atr * self.trending_sl_mult:
                return True, 'SL_TRENDING'

        return False, ''

    def _low_vol_exit(self, prices: np.ndarray, idx: int, position: Position, current_price: float) -> tuple[bool, str]:
        """Standard fade exit in low volatility."""

        recent = prices[max(0, idx - 20):idx + 1]
        atr = np.mean(np.abs(np.diff(recent)))

        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            if profit >= atr * 2.0:
                return True, 'TP_LOW_VOL'
            if profit <= -atr * 1.5:
                return True, 'SL_LOW_VOL'
        else:
            profit = position.entry_price - current_price
            if profit >= atr * 2.0:
                return True, 'TP_LOW_VOL'
            if profit <= -atr * 1.5:
                return True, 'SL_LOW_VOL'

        return False, ''

    def _calculate_ema(self, prices: np.ndarray, period: int) -> np.ndarray:
        """Calculate exponential moving average."""
        if len(prices) < period:
            return np.array([])

        ema = np.zeros(len(prices))
        ema[0] = prices[0]
        multiplier = 2 / (period + 1)

        for i in range(1, len(prices)):
            ema[i] = (prices[i] - ema[i-1]) * multiplier + ema[i-1]

        return ema

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Track regime on each tick."""
        if idx >= self.regime_lookback:
            self.current_regime = self._detect_regime(prices, idx)


def backtest_regime_adaptive(symbol: str, optimize: bool = False):
    """Run regime adaptive strategy backtest."""

    print(f"\n{'='*70}")
    print(f"REGIME ADAPTIVE STRATEGY — {symbol}")
    print(f"{'='*70}\n")

    # Load data
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')

    if not file_path.exists():
        print(f"❌ Data file not found: {file_path}")
        return

    df = pd.read_parquet(file_path)
    prices = df['close'].values

    print(f"Loaded {len(df)} bars\n")

    if not optimize:
        strategy = RegimeAdaptive(
            symbol=symbol,
            trend_ema_period=20,
            volatility_period=14,
            regime_lookback=50,
            ranging_tp_mult=0.5,
            ranging_sl_mult=1.0,
            trending_tp_mult=3.0,
            trending_sl_mult=2.0,
            high_vol_size_mult=0.5,
            high_vol_max_hold=10,
            normal_max_hold=30,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("Regime Adaptive Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown by Regime:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

    else:
        print("Optimizing regime adaptive parameters...\n")

        param_grid = {
            'symbol': [symbol],
            'trend_ema_period': [15, 20, 25],
            'regime_lookback': [40, 50, 60],
            'ranging_tp_mult': [0.4, 0.5, 0.6],
            'ranging_sl_mult': [0.8, 1.0, 1.2],
            'trending_tp_mult': [2.5, 3.0, 3.5],
            'trending_sl_mult': [1.5, 2.0, 2.5],
            'normal_max_hold': [25, 30, 35],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            RegimeAdaptive,
            param_grid,
            metric='sharpe_ratio'
        )

        print("Best Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")

        # Test out-of-sample
        train_size = int(len(prices) * 0.7)
        test_prices = prices[train_size:]

        test_strategy = RegimeAdaptive(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, 10000.0)

        print(f"\nOut-of-Sample Validation:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Regime Adaptive Strategy')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_regime_adaptive(args.symbol, optimize=args.optimize)
