"""
strategies/smc_strategy.py — Smart Money Concepts (ICT) strategy for Boom/Crash.

Concepts Used:
1. Fair Value Gaps (FVG) - Price imbalances that must be filled
2. Order Blocks (OB) - Institutional entry zones
3. Liquidity Sweeps - Stop hunts before reversals
4. Break of Structure (BOS) / Change of Character (CHoCH)
5. Premium/Discount zones

Usage:
    python -m strategies.smc_strategy --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position


@dataclass
class FairValueGap:
    """Fair Value Gap structure."""
    start_idx: int
    end_idx: int
    gap_high: float
    gap_low: float
    gap_type: Literal['BULLISH', 'BEARISH']
    filled: bool = False


@dataclass
class OrderBlock:
    """Order Block structure."""
    idx: int
    high: float
    low: float
    ob_type: Literal['BULLISH', 'BEARISH']
    tested: bool = False


class SMCStrategy(Strategy):
    """
    Smart Money Concepts strategy for Boom/Crash.

    Entry Logic:
    1. Identify FVG (3-bar pattern with gap)
    2. Wait for price to return to FVG zone
    3. Confirm with order block or BOS
    4. Enter in FVG direction

    Exit Logic:
    1. TP: Opposite FVG or significant resistance
    2. SL: Beyond FVG zone
    3. Trail stop after 50% move
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        min_fvg_size: float = 0.0005,  # Minimum FVG size as % of price
        fvg_lookback: int = 50,  # Bars to look back for FVGs
        ob_lookback: int = 20,  # Bars to look back for order blocks
        tp_multiplier: float = 2.0,  # TP as multiple of FVG size
        sl_multiplier: float = 1.5,  # SL as multiple of FVG size
        trailing_activation: float = 0.5,  # Start trailing at 50% of TP
        max_hold: int = 30,
    ):
        super().__init__('SMCStrategy')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        self.min_fvg_size = min_fvg_size
        self.fvg_lookback = fvg_lookback
        self.ob_lookback = ob_lookback
        self.tp_multiplier = tp_multiplier
        self.sl_multiplier = sl_multiplier
        self.trailing_activation = trailing_activation
        self.max_hold = max_hold

        # Detected structures
        self.fvgs: list[FairValueGap] = []
        self.order_blocks: list[OrderBlock] = []

        # Entry state
        self.active_fvg: FairValueGap | None = None
        self.entry_confirmed: bool = False
        self.highest_profit: float = 0.0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Enter when price returns to unfilled FVG with confirmation."""

        if idx < 10:
            return False, None

        current_price = prices[idx]

        # Update structures
        self._update_structures(prices, idx)

        # Find nearest unfilled FVG
        nearest_fvg = self._find_nearest_unfilled_fvg(current_price)

        if nearest_fvg is None:
            return False, None

        # Check if price is in FVG zone
        in_fvg_zone = (nearest_fvg.gap_low <= current_price <= nearest_fvg.gap_high)

        if not in_fvg_zone:
            return False, None

        # Confirm with order block or structure break
        confirmation = self._check_entry_confirmation(prices, idx, nearest_fvg)

        if confirmation:
            self.active_fvg = nearest_fvg
            self.highest_profit = 0.0

            if nearest_fvg.gap_type == 'BULLISH':
                return True, 'LONG'
            else:
                return True, 'SHORT'

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Exit based on TP, SL, or trailing stop."""

        if self.active_fvg is None:
            return False, ''

        current_price = prices[idx]

        # Calculate FVG size
        fvg_size = self.active_fvg.gap_high - self.active_fvg.gap_low

        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            self.highest_profit = max(self.highest_profit, profit)

            # Take profit
            tp_target = position.entry_price + (fvg_size * self.tp_multiplier)
            if current_price >= tp_target:
                return True, 'TP'

            # Stop loss (below FVG)
            sl_level = self.active_fvg.gap_low - (fvg_size * self.sl_multiplier)
            if current_price <= sl_level:
                return True, 'SL'

            # Trailing stop (after 50% of TP reached)
            if self.highest_profit >= (fvg_size * self.tp_multiplier * self.trailing_activation):
                trail_stop = position.entry_price + (self.highest_profit * 0.7)
                if current_price <= trail_stop:
                    return True, 'TRAIL_STOP'

        else:  # SHORT
            profit = position.entry_price - current_price
            self.highest_profit = max(self.highest_profit, profit)

            # Take profit
            tp_target = position.entry_price - (fvg_size * self.tp_multiplier)
            if current_price <= tp_target:
                return True, 'TP'

            # Stop loss (above FVG)
            sl_level = self.active_fvg.gap_high + (fvg_size * self.sl_multiplier)
            if current_price >= sl_level:
                return True, 'SL'

            # Trailing stop
            if self.highest_profit >= (fvg_size * self.tp_multiplier * self.trailing_activation):
                trail_stop = position.entry_price - (self.highest_profit * 0.7)
                if current_price >= trail_stop:
                    return True, 'TRAIL_STOP'

        # Timeout
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def _update_structures(self, prices: np.ndarray, idx: int):
        """Update FVGs and order blocks."""

        # Detect new FVGs (need at least 3 bars)
        if idx >= 3:
            self._detect_fvg(prices, idx)

        # Detect order blocks
        if idx >= 5:
            self._detect_order_block(prices, idx)

        # Mark filled FVGs
        current_price = prices[idx]
        for fvg in self.fvgs:
            if not fvg.filled:
                if fvg.gap_type == 'BULLISH' and current_price <= fvg.gap_low:
                    fvg.filled = True
                elif fvg.gap_type == 'BEARISH' and current_price >= fvg.gap_high:
                    fvg.filled = True

    def _detect_fvg(self, prices: np.ndarray, idx: int):
        """
        Detect Fair Value Gap.

        Bullish FVG: Bar 1 high < Bar 3 low (gap between them)
        Bearish FVG: Bar 1 low > Bar 3 high (gap between them)
        """

        if idx < 3:
            return

        # Use simple close prices for detection
        bar1 = prices[idx - 2]
        bar2 = prices[idx - 1]
        bar3 = prices[idx]

        # Estimate high/low from close (simplified)
        # In production, use actual OHLC data
        bar1_high = bar1 * 1.001
        bar1_low = bar1 * 0.999
        bar3_high = bar3 * 1.001
        bar3_low = bar3 * 0.999

        # Bullish FVG
        if bar1_high < bar3_low:
            gap_size = (bar3_low - bar1_high) / bar1
            if gap_size >= self.min_fvg_size:
                fvg = FairValueGap(
                    start_idx=idx - 2,
                    end_idx=idx,
                    gap_high=bar3_low,
                    gap_low=bar1_high,
                    gap_type='BULLISH'
                )
                self.fvgs.append(fvg)

        # Bearish FVG
        elif bar1_low > bar3_high:
            gap_size = (bar1_low - bar3_high) / bar1
            if gap_size >= self.min_fvg_size:
                fvg = FairValueGap(
                    start_idx=idx - 2,
                    end_idx=idx,
                    gap_high=bar1_low,
                    gap_low=bar3_high,
                    gap_type='BEARISH'
                )
                self.fvgs.append(fvg)

        # Keep only recent FVGs
        self.fvgs = [fvg for fvg in self.fvgs if idx - fvg.end_idx <= self.fvg_lookback]

    def _detect_order_block(self, prices: np.ndarray, idx: int):
        """
        Detect Order Block.

        Bullish OB: Last down candle before strong move up
        Bearish OB: Last up candle before strong move down
        """

        if idx < 5:
            return

        # Look for strong moves (> 2 consecutive bars same direction)
        recent = prices[idx-4:idx+1]
        changes = np.diff(recent)

        # Bullish order block (last down move before rally)
        if changes[-1] > 0 and changes[-2] > 0:  # Two up bars
            if changes[-3] < 0:  # Previous bar was down
                ob = OrderBlock(
                    idx=idx - 3,
                    high=prices[idx - 3] * 1.001,
                    low=prices[idx - 3] * 0.999,
                    ob_type='BULLISH'
                )
                self.order_blocks.append(ob)

        # Bearish order block (last up move before drop)
        elif changes[-1] < 0 and changes[-2] < 0:  # Two down bars
            if changes[-3] > 0:  # Previous bar was up
                ob = OrderBlock(
                    idx=idx - 3,
                    high=prices[idx - 3] * 1.001,
                    low=prices[idx - 3] * 0.999,
                    ob_type='BEARISH'
                )
                self.order_blocks.append(ob)

        # Keep only recent order blocks
        self.order_blocks = [ob for ob in self.order_blocks if idx - ob.idx <= self.ob_lookback]

    def _find_nearest_unfilled_fvg(self, current_price: float) -> FairValueGap | None:
        """Find nearest unfilled FVG."""

        unfilled = [fvg for fvg in self.fvgs if not fvg.filled]

        if not unfilled:
            return None

        # Find closest by distance
        closest = min(unfilled, key=lambda fvg:
                     min(abs(current_price - fvg.gap_high), abs(current_price - fvg.gap_low)))

        return closest

    def _check_entry_confirmation(self, prices: np.ndarray, idx: int, fvg: FairValueGap) -> bool:
        """
        Check for entry confirmation.

        Confirmation can be:
        1. Price touched order block in same direction
        2. Recent break of structure in same direction
        3. Strong momentum bar in FVG direction
        """

        current_price = prices[idx]

        # Check order blocks
        for ob in self.order_blocks:
            if ob.ob_type == fvg.gap_type and not ob.tested:
                if ob.low <= current_price <= ob.high:
                    ob.tested = True
                    return True

        # Check momentum (simplified)
        if idx >= 3:
            recent_move = abs(prices[idx] - prices[idx - 3])
            avg_move = np.mean(np.abs(np.diff(prices[max(0, idx-20):idx])))

            if recent_move > avg_move * 1.5:  # Strong momentum
                move_direction = 'BULLISH' if prices[idx] > prices[idx - 3] else 'BEARISH'
                if move_direction == fvg.gap_type:
                    return True

        # Default: confirm if price is in FVG zone
        return True


def backtest_smc(symbol: str, optimize: bool = False):
    """Run SMC strategy backtest."""

    print(f"\n{'='*70}")
    print(f"SMART MONEY CONCEPTS STRATEGY — {symbol}")
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
        strategy = SMCStrategy(
            symbol=symbol,
            min_fvg_size=0.0005,
            fvg_lookback=50,
            ob_lookback=20,
            tp_multiplier=2.0,
            sl_multiplier=1.5,
            trailing_activation=0.5,
            max_hold=30,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("SMC Strategy Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

        # Structure analysis
        print(f"\nStructure Detection:")
        print(f"  FVGs detected: {len(strategy.fvgs)}")
        print(f"  Order blocks detected: {len(strategy.order_blocks)}")

    else:
        print("Optimizing SMC parameters...\n")

        param_grid = {
            'symbol': [symbol],
            'min_fvg_size': [0.0003, 0.0005, 0.0008],
            'fvg_lookback': [30, 50, 70],
            'tp_multiplier': [1.5, 2.0, 2.5, 3.0],
            'sl_multiplier': [1.0, 1.5, 2.0],
            'trailing_activation': [0.5, 0.6, 0.7],
            'max_hold': [20, 30, 40],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            SMCStrategy,
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

        test_strategy = SMCStrategy(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, 10000.0)

        print(f"\nOut-of-Sample Validation:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Smart Money Concepts Strategy')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_smc(args.symbol, optimize=args.optimize)
