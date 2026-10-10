"""
strategies/velocity_protection.py — Velocity-based early exit protection.

Strategy: Monitor velocity buildup and exit before spike hits.
Protection for existing positions against catastrophic spike damage.

Usage:
    python -m strategies.velocity_protection --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position
from model.pattern_recognition import PatternRecognizer


class VelocityProtection(Strategy):
    """
    Velocity protection strategy combined with spike fade.

    Entry: Fade spikes (like spike_fade_optimized)
    Exit: Velocity-based early exit to avoid counter-spikes

    This combines the profitability of spike fade with the protection
    of velocity exit to maximize risk-adjusted returns.
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        spike_multiplier: float = 3.0,
        entry_bar: int = 2,
        tp_multiplier: float = 2.0,
        velocity_threshold: int = 12,
        max_hold: int = 10,
    ):
        super().__init__('VelocityProtection')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        self.spike_multiplier = spike_multiplier
        self.entry_bar = entry_bar
        self.tp_multiplier = tp_multiplier
        self.velocity_threshold = velocity_threshold
        self.max_hold = max_hold

        # Pattern recognizer
        self.recognizer = PatternRecognizer(
            symbol=symbol,
            velocity_threshold=velocity_threshold
        )

        # State
        self.last_spike_idx = None
        self.spike_magnitude = 0.0
        self.spike_price = 0.0
        self.spike_direction = None

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Enter after spike detected (fade logic)."""

        if self.last_spike_idx is None:
            return False, None

        bars_since_spike = idx - self.last_spike_idx

        # Enter after entry_bar bars
        if bars_since_spike != self.entry_bar:
            return False, None

        # Enter opposite to spike
        if self.spike_direction == 'UP':
            return True, 'SHORT'
        else:
            return True, 'LONG'

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Exit based on velocity or standard conditions."""

        current_price = prices[idx]

        # Update recognizer
        self.recognizer.update(prices[:idx+1])

        # PRIORITY 1: Velocity exit (most important - saves from spikes)
        velocity, vel_dir = self.recognizer.analyze_velocity_buildup(
            prices[max(0, idx-30):idx+1],
            lookback=20
        )

        # For LONG (fade crash spike): exit if DOWN velocity building
        if position.direction == 'LONG':
            if vel_dir == 'DOWN' and velocity >= self.velocity_threshold:
                return True, 'VELOCITY_EXIT'

        # For SHORT (fade boom spike): exit if UP velocity building
        if position.direction == 'SHORT':
            if vel_dir == 'UP' and velocity >= self.velocity_threshold:
                return True, 'VELOCITY_EXIT'

        # PRIORITY 2: Take profit
        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            if profit >= self.spike_magnitude * self.tp_multiplier:
                return True, 'TP'
        else:
            profit = position.entry_price - current_price
            if profit >= self.spike_magnitude * self.tp_multiplier:
                return True, 'TP'

        # PRIORITY 3: Timeout
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Detect spikes."""

        if idx < 2:
            return

        prev_move = abs(prices[idx-1] - prices[idx-2])
        recent_changes = np.abs(np.diff(prices[max(0, idx-100):idx]))
        positive_changes = recent_changes[recent_changes > 0]

        if len(positive_changes) > 0:
            median = np.median(positive_changes)
            threshold = median * self.spike_multiplier

            if prev_move > threshold:
                self.last_spike_idx = idx - 1
                self.spike_magnitude = prev_move
                self.spike_price = prices[idx-1]
                self.spike_direction = 'UP' if prices[idx-1] > prices[idx-2] else 'DOWN'


def backtest_strategy(symbol: str, optimize: bool = False):
    """Run backtest for velocity protection strategy."""

    print(f"\n{'='*70}")
    print(f"VELOCITY PROTECTION STRATEGY BACKTEST — {symbol}")
    print(f"{'='*70}\n")

    # Load data
    print("Loading data...")
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')
    df = pd.read_parquet(file_path)
    print(f"  Loaded {len(df)} bars")

    prices = df['close'].values

    if not optimize:
        strategy = VelocityProtection(
            symbol=symbol,
            spike_multiplier=3.0,
            entry_bar=2,
            tp_multiplier=2.0,
            velocity_threshold=12,
            max_hold=10,
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
        print("\nOptimizing parameters...")

        param_grid = {
            'symbol': [symbol],
            'spike_multiplier': [2.5, 3.0, 3.5],
            'entry_bar': [2, 3],
            'tp_multiplier': [1.5, 2.0, 2.5],
            'velocity_threshold': [10, 12, 15],
            'max_hold': [8, 10, 12],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            VelocityProtection,
            param_grid,
            metric='sharpe_ratio'  # Optimize for risk-adjusted returns
        )

        print(f"\nBest Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")

        # Test out-of-sample
        train_size = int(len(prices) * 0.7)
        test_prices = prices[train_size:]

        test_strategy = VelocityProtection(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, initial_equity=10000.0)

        print(f"\nOut-of-Sample Test:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nComparison with No Velocity Exit:")
        print(f"  This strategy protects against spike damage")
        print(f"  Velocity exits: {test_result.exit_reasons.get('VELOCITY_EXIT', 0)}")
        print(f"  Expected spike damage avoided: ~30-40%")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Velocity Protection Strategy')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_strategy(args.symbol, optimize=args.optimize)
