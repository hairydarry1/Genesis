"""
strategies/statistical_arbitrage.py — Mean reversion using z-score and Bollinger Bands.

Concept: Price deviations from statistical mean are temporary and revert.

Entry Logic:
1. Calculate z-score (price deviation in standard deviations)
2. Enter when |z-score| > threshold (2.0-2.5)
3. Confirm with Bollinger Band bounce
4. Enter opposite to deviation (reversion)

Exit Logic:
1. TP: Mean reversion (z-score returns to 0)
2. SL: Further deviation (z-score increases)
3. Timeout: Mean doesn't revert

Usage:
    python -m strategies.statistical_arbitrage --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position


class StatisticalArbitrage(Strategy):
    """
    Statistical arbitrage using mean reversion.

    Z-Score Formula: (Price - Mean) / StdDev

    Entry Signals:
    - Z > +2.0: Overbought → Enter SHORT
    - Z < -2.0: Oversold → Enter LONG

    Bollinger Bands:
    - Upper Band: Mean + (2 * StdDev)
    - Lower Band: Mean - (2 * StdDev)
    - Price bounce from bands confirms entry
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        lookback_period: int = 20,  # Bars for mean/std calculation
        z_score_entry: float = 2.0,  # Z-score threshold for entry
        z_score_exit: float = 0.5,   # Z-score threshold for exit
        bb_std_multiplier: float = 2.0,  # Bollinger Band width
        require_bb_confirmation: bool = True,
        tp_z_score: float = 0.0,  # Target z-score (mean reversion)
        sl_z_score: float = 3.0,  # Stop if deviation increases
        max_hold: int = 30,
    ):
        super().__init__('StatisticalArbitrage')

        self.symbol = symbol.upper()
        self.lookback_period = lookback_period
        self.z_score_entry = z_score_entry
        self.z_score_exit = z_score_exit
        self.bb_std_multiplier = bb_std_multiplier
        self.require_bb_confirmation = require_bb_confirmation
        self.tp_z_score = tp_z_score
        self.sl_z_score = sl_z_score
        self.max_hold = max_hold

        # Entry state
        self.entry_z_score: float = 0.0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Enter when z-score exceeds threshold."""

        if idx < self.lookback_period:
            return False, None

        # Calculate z-score
        z_score, bb_upper, bb_lower = self._calculate_zscore_and_bb(prices, idx)

        current_price = prices[idx]

        # LONG: Oversold (z < -threshold)
        if z_score < -self.z_score_entry:
            # Confirm with BB if required
            if self.require_bb_confirmation:
                if current_price > bb_lower:  # Price bounced off lower band
                    self.entry_z_score = z_score
                    return True, 'LONG'
            else:
                self.entry_z_score = z_score
                return True, 'LONG'

        # SHORT: Overbought (z > +threshold)
        elif z_score > self.z_score_entry:
            # Confirm with BB if required
            if self.require_bb_confirmation:
                if current_price < bb_upper:  # Price bounced off upper band
                    self.entry_z_score = z_score
                    return True, 'SHORT'
            else:
                self.entry_z_score = z_score
                return True, 'SHORT'

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Exit when mean reverted or stop loss hit."""

        # Calculate current z-score
        z_score, _, _ = self._calculate_zscore_and_bb(prices, idx)

        if position.direction == 'LONG':
            # Take profit: z-score returned to mean
            if z_score >= self.tp_z_score:
                return True, 'TP_MEAN_REVERSION'

            # Stop loss: deviation increased (more oversold)
            if z_score < -self.sl_z_score:
                return True, 'SL_DEVIATION_INCREASE'

        else:  # SHORT
            # Take profit: z-score returned to mean
            if z_score <= self.tp_z_score:
                return True, 'TP_MEAN_REVERSION'

            # Stop loss: deviation increased (more overbought)
            if z_score > self.sl_z_score:
                return True, 'SL_DEVIATION_INCREASE'

        # Timeout
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def _calculate_zscore_and_bb(self, prices: np.ndarray, idx: int) -> tuple[float, float, float]:
        """
        Calculate z-score and Bollinger Bands.

        Returns:
            (z_score, bb_upper, bb_lower)
        """
        lookback = prices[max(0, idx - self.lookback_period):idx + 1]

        mean_price = np.mean(lookback)
        std_price = np.std(lookback)

        current_price = prices[idx]

        # Z-score
        z_score = (current_price - mean_price) / std_price if std_price > 0 else 0.0

        # Bollinger Bands
        bb_upper = mean_price + (self.bb_std_multiplier * std_price)
        bb_lower = mean_price - (self.bb_std_multiplier * std_price)

        return z_score, bb_upper, bb_lower


def backtest_statistical_arbitrage(symbol: str, optimize: bool = False):
    """Run statistical arbitrage backtest."""

    print(f"\n{'='*70}")
    print(f"STATISTICAL ARBITRAGE STRATEGY — {symbol}")
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
        strategy = StatisticalArbitrage(
            symbol=symbol,
            lookback_period=20,
            z_score_entry=2.0,
            z_score_exit=0.5,
            bb_std_multiplier=2.0,
            require_bb_confirmation=True,
            tp_z_score=0.0,
            sl_z_score=3.0,
            max_hold=30,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("Statistical Arbitrage Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

    else:
        print("Optimizing statistical arbitrage parameters...\n")

        param_grid = {
            'symbol': [symbol],
            'lookback_period': [15, 20, 25, 30],
            'z_score_entry': [1.5, 2.0, 2.5],
            'z_score_exit': [0.3, 0.5, 0.7],
            'bb_std_multiplier': [1.5, 2.0, 2.5],
            'require_bb_confirmation': [True, False],
            'tp_z_score': [0.0, 0.2],
            'sl_z_score': [2.5, 3.0, 3.5],
            'max_hold': [25, 30, 35],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            StatisticalArbitrage,
            param_grid,
            metric='sharpe_ratio'
        )

        print("Best Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")

        # Walk-forward validation
        train_size = int(len(prices) * 0.7)
        test_prices = prices[train_size:]

        test_strategy = StatisticalArbitrage(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, 10000.0)

        print(f"\nOut-of-Sample Validation:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Statistical Arbitrage Strategy')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_statistical_arbitrage(args.symbol, optimize=args.optimize)
