"""
strategies/tick_count_catcher.py — Tick count exhaustion spike catcher strategy.

Strategy: Enter when tick count since last spike exceeds threshold.
The algorithm MUST inject a spike, so this catches the inevitable.

Usage:
    python -m strategies.tick_count_catcher --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position
from model.pattern_recognition import PatternRecognizer


class TickCountCatcher(Strategy):
    """
    Tick count exhaustion spike catcher.

    Entry Rules:
    1. Ticks since last spike >= tick_threshold
    2. Enter in spike direction:
       - BOOM: LONG (spike goes UP)
       - CRASH: SHORT (spike goes DOWN)

    Exit Rules:
    1. TP: Catch the spike (tp_points profit)
    2. SL: Tight stop (sl_points loss)
    3. Timeout: max_hold ticks if no spike
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        tick_threshold: int = 700,
        tp_points: float = 200.0,
        sl_points: float = 25.0,
        max_hold: int = 200,
        use_velocity_boost: bool = True,
        velocity_threshold: int = 12,
    ):
        super().__init__('TickCountCatcher')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        self.tick_threshold = tick_threshold
        self.tp_points = tp_points
        self.sl_points = sl_points
        self.max_hold = max_hold
        self.use_velocity_boost = use_velocity_boost
        self.velocity_threshold = velocity_threshold

        # Pattern recognizer
        self.recognizer = PatternRecognizer(
            symbol=symbol,
            velocity_threshold=velocity_threshold,
            tick_count_threshold=tick_threshold
        )

        # State
        self.last_spike_idx = 0
        self.ticks_since_spike = 0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Check if we should enter spike catcher trade."""

        # Update recognizer
        self.recognizer.update(prices[:idx+1])

        # Calculate ticks since last spike
        spike_mask = self.recognizer.detect_spikes(prices[:idx+1])
        spike_indices = np.where(spike_mask)[0]

        if len(spike_indices) > 0:
            self.last_spike_idx = spike_indices[-1]
            self.ticks_since_spike = idx - self.last_spike_idx
        else:
            self.ticks_since_spike = idx

        # Base condition: tick count exhaustion
        tick_signal = self.ticks_since_spike >= self.tick_threshold

        # Velocity boost (optional)
        velocity_signal = False
        if self.use_velocity_boost:
            velocity, vel_dir = self.recognizer.analyze_velocity_buildup(prices[max(0, idx-30):idx+1])

            # For CRASH: UP velocity predicts DOWN spike
            # For BOOM: DOWN velocity predicts UP spike
            if self.is_crash and vel_dir == 'UP' and velocity >= self.velocity_threshold:
                velocity_signal = True
            elif self.is_boom and vel_dir == 'DOWN' and velocity >= self.velocity_threshold:
                velocity_signal = True

        # Enter if either condition met (but tick count is stronger signal)
        if tick_signal or (velocity_signal and self.ticks_since_spike >= self.tick_threshold * 0.8):
            if self.is_boom:
                return True, 'LONG'  # Boom spike goes UP
            elif self.is_crash:
                return True, 'SHORT'  # Crash spike goes DOWN

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Check if we should exit current position."""

        current_price = prices[idx]

        # Take profit: catch the spike
        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            if profit >= self.tp_points:
                return True, 'TP'

            # Stop loss
            loss = position.entry_price - current_price
            if loss >= self.sl_points:
                return True, 'SL'

        else:  # SHORT
            profit = position.entry_price - current_price
            if profit >= self.tp_points:
                return True, 'TP'

            # Stop loss
            loss = current_price - position.entry_price
            if loss >= self.sl_points:
                return True, 'SL'

        # Timeout: no spike came
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''


def load_data(symbol: str) -> pd.DataFrame:
    """Load 1-minute candle data for symbol."""
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')

    if not file_path.exists():
        raise FileNotFoundError(f"Data file not found: {file_path}")

    df = pd.read_parquet(file_path)
    return df


def backtest_strategy(symbol: str, optimize: bool = False):
    """Run backtest for tick count catcher strategy."""

    print(f"\n{'='*70}")
    print(f"TICK COUNT CATCHER STRATEGY BACKTEST — {symbol}")
    print(f"{'='*70}\n")

    # Load data
    print("Loading data...")
    df = load_data(symbol)
    print(f"  Loaded {len(df)} bars")

    # Use close prices
    prices = df['close'].values

    if not optimize:
        # Single backtest with default params
        # Adjust threshold based on index number
        if '300' in symbol:
            default_threshold = 500
        elif '500' in symbol:
            default_threshold = 700
        elif '1000' in symbol:
            default_threshold = 900
        else:
            default_threshold = 700

        strategy = TickCountCatcher(
            symbol=symbol,
            tick_threshold=default_threshold,
            tp_points=200.0,
            sl_points=25.0,
            max_hold=200,
            use_velocity_boost=True,
            velocity_threshold=12,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0, position_size_pct=1.0)

        print("\nBacktest Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

        # Trade analysis
        if result.total_trades > 0:
            print(f"\nTrade Analysis:")
            winners = [t for t in result.trades if t.is_winner]
            losers = [t for t in result.trades if not t.is_winner]

            if winners:
                avg_winner_duration = np.mean([t.duration for t in winners])
                print(f"  Avg winner duration: {avg_winner_duration:.1f} bars")

            if losers:
                avg_loser_duration = np.mean([t.duration for t in losers])
                print(f"  Avg loser duration: {avg_loser_duration:.1f} bars")

    else:
        # Optimize parameters
        print("\nOptimizing parameters...")

        # Set threshold range based on index
        if '300' in symbol:
            tick_range = [400, 500, 600]
        elif '500' in symbol:
            tick_range = [600, 700, 800, 900]
        elif '1000' in symbol:
            tick_range = [800, 900, 1000, 1100]
        else:
            tick_range = [600, 700, 800]

        param_grid = {
            'symbol': [symbol],
            'tick_threshold': tick_range,
            'tp_points': [150.0, 200.0, 250.0],
            'sl_points': [20.0, 25.0, 30.0],
            'max_hold': [150, 200, 250],
            'use_velocity_boost': [True, False],
            'velocity_threshold': [12],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            TickCountCatcher,
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

        test_strategy = TickCountCatcher(**best_params)
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

        if test_result.profit_factor < 2.0:
            passed = False
            reasons.append(f"Profit factor {test_result.profit_factor:.2f} < 2.0")

        if test_result.win_rate < 0.70:
            passed = False
            reasons.append(f"Win rate {test_result.win_rate:.1%} < 70%")

        if test_result.max_drawdown_pct > 0.15:
            passed = False
            reasons.append(f"Max drawdown {test_result.max_drawdown_pct:.1%} > 15%")

        if test_result.total_trades < 10:
            passed = False
            reasons.append(f"Only {test_result.total_trades} trades (need >= 10 for statistical significance)")

        if passed:
            print("✅ PASSED - Strategy ready for paper trading")
            print(f"\nExpected Performance:")
            print(f"  Win Rate: {test_result.win_rate:.1%}")
            print(f"  Profit Factor: {test_result.profit_factor:.2f}")
            print(f"  Risk/Reward: 1:{test_result.tp_points/test_result.sl_points:.1f}")
        else:
            print("❌ FAILED - Strategy needs improvement")
            print("\nReasons:")
            for reason in reasons:
                print(f"  - {reason}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Tick Count Catcher Strategy Backtest')
    parser.add_argument('--symbol', default='CRASH500',
                       choices=['BOOM300', 'BOOM500', 'BOOM1000',
                               'CRASH300', 'CRASH500', 'CRASH1000'])
    parser.add_argument('--optimize', action='store_true',
                       help='Run parameter optimization')

    args = parser.parse_args()

    backtest_strategy(args.symbol, optimize=args.optimize)
