"""
strategies/correlation_arbitrage.py — Trade BOOM/CRASH correlation divergence.

Concept: BOOM and CRASH are inversely correlated synthetic indices.
When correlation breaks down, it creates arbitrage opportunities.

Strategy:
1. Monitor correlation between BOOM and CRASH
2. When correlation weakens (divergence), expect convergence
3. Go LONG underperformer, SHORT overperformer
4. Exit when correlation normalizes

Usage:
    python -m strategies.correlation_arbitrage --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position


@dataclass
class CorrelationState:
    """Correlation state between BOOM and CRASH."""
    correlation: float  # -1 to +1
    boom_zscore: float
    crash_zscore: float
    spread_zscore: float  # Z-score of price spread


class CorrelationArbitrage(Strategy):
    """
    Pairs trading strategy for BOOM/CRASH indices.

    Normal State: BOOM and CRASH are inversely correlated (r ≈ -0.7 to -0.9)

    Arbitrage Signal:
    - Correlation weakens (r > -0.5)
    - Price spread diverges (|spread_z| > threshold)
    - Trade for convergence

    Entry:
    - LONG underperforming index
    - SHORT outperforming index

    Exit:
    - Spread returns to mean
    - Correlation normalizes
    - Timeout
    """

    def __init__(
        self,
        boom_symbol: str = 'BOOM500',
        crash_symbol: str = 'CRASH500',
        correlation_period: int = 50,  # Bars for correlation
        spread_period: int = 30,  # Bars for spread calculation
        correlation_threshold: float = -0.5,  # Weak correlation trigger
        spread_zscore_entry: float = 2.0,  # Spread divergence entry
        spread_zscore_exit: float = 0.5,  # Spread convergence exit
        max_hold: int = 50,
    ):
        super().__init__('CorrelationArbitrage')

        self.boom_symbol = boom_symbol.upper()
        self.crash_symbol = crash_symbol.upper()
        self.correlation_period = correlation_period
        self.spread_period = spread_period
        self.correlation_threshold = correlation_threshold
        self.spread_zscore_entry = spread_zscore_entry
        self.spread_zscore_exit = spread_zscore_exit
        self.max_hold = max_hold

        # Data
        self.boom_prices: np.ndarray | None = None
        self.crash_prices: np.ndarray | None = None

        # Entry state
        self.entry_spread_zscore: float = 0.0
        self.boom_position: str | None = None  # 'LONG' or 'SHORT'
        self.crash_position: str | None = None

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """
        Enter when correlation breaks and spread diverges.

        Note: This strategy needs both BOOM and CRASH data.
        For backtesting, we'll use the single symbol's data
        and simulate the correlation logic.
        """

        if idx < max(self.correlation_period, self.spread_period):
            return False, None

        # In production, load both BOOM and CRASH data
        # For this demo, we'll detect divergences using price momentum

        # Calculate momentum divergence (simplified correlation proxy)
        recent = prices[max(0, idx - self.spread_period):idx + 1]

        # Z-score of current price vs recent mean
        mean_price = np.mean(recent)
        std_price = np.std(recent)

        current_price = prices[idx]
        z_score = (current_price - mean_price) / std_price if std_price > 0 else 0

        # Entry: Strong deviation (simulates correlation breakdown)
        if abs(z_score) > self.spread_zscore_entry:
            self.entry_spread_zscore = z_score

            # If price is too high → SHORT (expect reversion)
            if z_score > 0:
                return True, 'SHORT'
            # If price is too low → LONG (expect reversion)
            else:
                return True, 'LONG'

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Exit when spread converges."""

        recent = prices[max(0, idx - self.spread_period):idx + 1]
        mean_price = np.mean(recent)
        std_price = np.std(recent)

        current_price = prices[idx]
        current_z_score = (current_price - mean_price) / std_price if std_price > 0 else 0

        # Exit when z-score returns to near zero (convergence)
        if abs(current_z_score) <= self.spread_zscore_exit:
            return True, 'CONVERGENCE'

        # Stop loss: divergence increases (trade went wrong)
        if position.direction == 'LONG':
            if current_z_score < self.entry_spread_zscore - 1.0:
                return True, 'SL_DIVERGENCE_INCREASE'
        else:  # SHORT
            if current_z_score > self.entry_spread_zscore + 1.0:
                return True, 'SL_DIVERGENCE_INCREASE'

        # Timeout
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''


def load_paired_data(boom_symbol: str, crash_symbol: str) -> tuple[np.ndarray, np.ndarray] | None:
    """Load both BOOM and CRASH data for pairs trading."""

    boom_file = Path(f'data/candles_{boom_symbol.lower()}_1min.parquet')
    crash_file = Path(f'data/candles_{crash_symbol.lower()}_1min.parquet')

    if not boom_file.exists() or not crash_file.exists():
        return None

    boom_df = pd.read_parquet(boom_file)
    crash_df = pd.read_parquet(crash_file)

    # Align time series (in production, handle properly)
    min_len = min(len(boom_df), len(crash_df))

    boom_prices = boom_df['close'].values[:min_len]
    crash_prices = crash_df['close'].values[:min_len]

    return boom_prices, crash_prices


def backtest_correlation_arbitrage(optimize: bool = False):
    """Run correlation arbitrage backtest."""

    print(f"\n{'='*70}")
    print(f"BOOM/CRASH CORRELATION ARBITRAGE STRATEGY")
    print(f"{'='*70}\n")

    # For demo, we'll test on single symbol (simplified)
    # In production, use both BOOM and CRASH simultaneously
    symbol = 'CRASH500'
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')

    if not file_path.exists():
        print(f"❌ Data file not found: {file_path}")
        return

    df = pd.read_parquet(file_path)
    prices = df['close'].values

    print(f"Loaded {len(df)} bars")
    print("Note: Using simplified single-symbol demo.")
    print("Production version would trade BOOM and CRASH simultaneously.\n")

    if not optimize:
        strategy = CorrelationArbitrage(
            boom_symbol='BOOM500',
            crash_symbol='CRASH500',
            correlation_period=50,
            spread_period=30,
            correlation_threshold=-0.5,
            spread_zscore_entry=2.0,
            spread_zscore_exit=0.5,
            max_hold=50,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("Correlation Arbitrage Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

    else:
        print("Optimizing correlation arbitrage parameters...\n")

        param_grid = {
            'correlation_period': [40, 50, 60],
            'spread_period': [25, 30, 35],
            'spread_zscore_entry': [1.5, 2.0, 2.5],
            'spread_zscore_exit': [0.3, 0.5, 0.7],
            'max_hold': [40, 50, 60],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            CorrelationArbitrage,
            param_grid,
            metric='sharpe_ratio'
        )

        print("Best Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Correlation Arbitrage Strategy')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_correlation_arbitrage(optimize=args.optimize)
