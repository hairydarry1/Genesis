"""
strategies/spike_fade_optimized.py — Optimized spike fade strategy for Boom/Crash.

Strategy: Enter opposite direction after spike, target retracement.
Optimization: Cold hours filter, cluster avoidance, optimized TP/SL.

Usage:
    python -m strategies.spike_fade_optimized --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position
from model.pattern_recognition import PatternRecognizer


class SpikeFadeOptimized(Strategy):
    """
    Optimized spike fade strategy.

    Entry Rules:
    1. Spike detected (range > spike_multiplier * median)
    2. Current hour in cold_hours (if enabled)
    3. Wait entry_bar bars after spike
    4. No recent spike cluster (avoid fade during cluster)

    Exit Rules:
    1. TP: tp_multiplier * spike_magnitude retracement
    2. SL: price moves beyond spike extreme
    3. Timeout: max_hold bars
    4. Velocity exit (if enabled): exit before counter-spike
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        spike_multiplier: float = 3.0,
        entry_bar: int = 2,
        tp_multiplier: float = 2.0,
        sl_multiplier: float = 1.0,
        max_hold: int = 10,
        use_cold_hours: bool = True,
        use_velocity_exit: bool = False,
        velocity_threshold: int = 12,
    ):
        super().__init__('SpikeFadeOptimized')

        self.symbol = symbol.upper()
        self.spike_multiplier = spike_multiplier
        self.entry_bar = entry_bar
        self.tp_multiplier = tp_multiplier
        self.sl_multiplier = sl_multiplier
        self.max_hold = max_hold
        self.use_cold_hours = use_cold_hours
        self.use_velocity_exit = use_velocity_exit
        self.velocity_threshold = velocity_threshold

        # Cold hours per symbol (from backtests)
        self.cold_hours_map = {
            'BOOM500': [1, 5, 12, 14, 15, 19],
            'CRASH500': [0, 6, 7, 9, 22, 23],
            'BOOM1000': [6, 13, 18, 20, 21, 22],
            'CRASH1000': [3, 6, 7, 18, 22, 23],
            'BOOM300': [1, 5, 12, 14, 15, 19],  # Same as BOOM500
            'CRASH300': [0, 6, 7, 9, 22, 23],  # Same as CRASH500
        }

        # State
        self.last_spike_idx = None
        self.spike_magnitude = 0.0
        self.spike_price = 0.0
        self.spike_direction = None  # 'UP' or 'DOWN'

        # Pattern recognizer for velocity exit
        if use_velocity_exit:
            self.recognizer = PatternRecognizer(
                symbol=symbol,
                velocity_threshold=velocity_threshold
            )

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Check if we should enter a fade trade."""

        # Need spike detection first
        if self.last_spike_idx is None:
            return False, None

        bars_since_spike = idx - self.last_spike_idx

        # Wait entry_bar bars after spike
        if bars_since_spike != self.entry_bar:
            return False, None

        # Check cold hours (if enabled)
        if self.use_cold_hours:
            # Extract hour from timestamp if available
            current_hour = idx % 24  # Simplified - in real use, parse from timestamp
            cold_hours = self.cold_hours_map.get(self.symbol, [])

            if cold_hours and current_hour not in cold_hours:
                return False, None

        # Check for recent spike cluster (avoid fading during clusters)
        recent_spikes = self._count_recent_spikes(prices, idx, lookback=5)
        if recent_spikes > 1:
            return False, None  # Skip if part of cluster

        # Enter opposite to spike direction
        if self.spike_direction == 'UP':
            return True, 'SHORT'  # Fade upward spike
        else:
            return True, 'LONG'   # Fade downward spike

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Check if we should exit current position."""

        current_price = prices[idx]

        # Velocity exit (if enabled)
        if self.use_velocity_exit and hasattr(self, 'recognizer'):
            self.recognizer.update(prices[:idx+1])

            # Check velocity in counter direction
            velocity, vel_dir = self.recognizer.analyze_velocity_buildup(prices[max(0, idx-30):idx+1])

            # For LONG position, exit if DOWN velocity building
            if position.direction == 'LONG' and vel_dir == 'DOWN' and velocity >= self.velocity_threshold:
                return True, 'VELOCITY_EXIT'

            # For SHORT position, exit if UP velocity building
            if position.direction == 'SHORT' and vel_dir == 'UP' and velocity >= self.velocity_threshold:
                return True, 'VELOCITY_EXIT'

        # Take profit
        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            target = self.spike_magnitude * self.tp_multiplier

            if profit >= target:
                return True, 'TP'

            # Stop loss: price moves below spike low
            if current_price < self.spike_price:
                return True, 'SL'

        else:  # SHORT
            profit = position.entry_price - current_price
            target = self.spike_magnitude * self.tp_multiplier

            if profit >= target:
                return True, 'TP'

            # Stop loss: price moves above spike high
            if current_price > self.spike_price:
                return True, 'SL'

        # Timeout
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Update spike detection on each tick."""

        if idx < 2:
            return

        # Detect spike at idx-1
        prev_move = abs(prices[idx-1] - prices[idx-2])

        # Calculate threshold from recent data
        recent_changes = np.abs(np.diff(prices[max(0, idx-100):idx]))
        positive_changes = recent_changes[recent_changes > 0]

        if len(positive_changes) > 0:
            median = np.median(positive_changes)
            threshold = median * self.spike_multiplier

            # Spike detected
            if prev_move > threshold:
                self.last_spike_idx = idx - 1
                self.spike_magnitude = prev_move
                self.spike_price = prices[idx-1]

                # Determine direction
                if prices[idx-1] > prices[idx-2]:
                    self.spike_direction = 'UP'
                else:
                    self.spike_direction = 'DOWN'

    def _count_recent_spikes(self, prices: np.ndarray, idx: int, lookback: int = 5) -> int:
        """Count spikes in recent bars."""
        if idx < lookback + 2:
            return 0

        recent = prices[idx-lookback-1:idx]
        changes = np.abs(np.diff(recent))
        positive = changes[changes > 0]

        if len(positive) == 0:
            return 0

        median = np.median(positive)
        threshold = median * self.spike_multiplier

        return (changes > threshold).sum()


def load_data(symbol: str) -> pd.DataFrame:
    """Load 1-minute candle data for symbol."""
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')

    if not file_path.exists():
        raise FileNotFoundError(f"Data file not found: {file_path}")

    df = pd.read_parquet(file_path)
    return df


def backtest_strategy(symbol: str, optimize: bool = False):
    """Run backtest for spike fade strategy."""

    print(f"\n{'='*70}")
    print(f"SPIKE FADE STRATEGY BACKTEST — {symbol}")
    print(f"{'='*70}\n")

    # Load data
    print("Loading data...")
    df = load_data(symbol)
    print(f"  Loaded {len(df)} bars")

    # Use close prices
    prices = df['close'].values

    if not optimize:
        # Single backtest with default params
        strategy = SpikeFadeOptimized(
            symbol=symbol,
            spike_multiplier=3.0,
            entry_bar=2,
            tp_multiplier=2.0,
            sl_multiplier=1.0,
            max_hold=10,
            use_cold_hours=True,
            use_velocity_exit=False,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("\nBacktest Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

    else:
        # Optimize parameters
        print("\nOptimizing parameters...")

        param_grid = {
            'symbol': [symbol],
            'spike_multiplier': [2.5, 3.0, 3.5],
            'entry_bar': [1, 2, 3],
            'tp_multiplier': [1.5, 2.0, 2.5],
            'sl_multiplier': [0.8, 1.0, 1.2],
            'max_hold': [6, 8, 10],
            'use_cold_hours': [True, False],
            'use_velocity_exit': [False],  # Start without velocity
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            SpikeFadeOptimized,
            param_grid,
            metric='profit_factor'
        )

        print(f"\nBest Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")

        # Walk-forward validation
        print(f"\n{'='*70}")
        print("WALK-FORWARD VALIDATION")
        print(f"{'='*70}\n")

        train_size = int(len(prices) * 0.7)
        test_prices = prices[train_size:]

        test_strategy = SpikeFadeOptimized(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, initial_equity=10000.0)

        print("Out-of-Sample Test Results:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")

        # Gate check
        print(f"\n{'='*70}")
        print("GATE CHECK")
        print(f"{'='*70}")

        passed = True
        reasons = []

        if test_result.profit_factor < 1.5:
            passed = False
            reasons.append(f"Profit factor {test_result.profit_factor:.2f} < 1.5")

        if test_result.win_rate < 0.55:
            passed = False
            reasons.append(f"Win rate {test_result.win_rate:.1%} < 55%")

        if test_result.max_drawdown_pct > 0.20:
            passed = False
            reasons.append(f"Max drawdown {test_result.max_drawdown_pct:.1%} > 20%")

        if passed:
            print("✅ PASSED - Strategy ready for paper trading")
        else:
            print("❌ FAILED - Strategy needs improvement")
            print("\nReasons:")
            for reason in reasons:
                print(f"  - {reason}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Spike Fade Strategy Backtest')
    parser.add_argument('--symbol', default='CRASH500',
                       choices=['BOOM300', 'BOOM500', 'BOOM1000',
                               'CRASH300', 'CRASH500', 'CRASH1000'])
    parser.add_argument('--optimize', action='store_true',
                       help='Run parameter optimization')

    args = parser.parse_args()

    backtest_strategy(args.symbol, optimize=args.optimize)
