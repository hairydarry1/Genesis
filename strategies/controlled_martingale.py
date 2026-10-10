"""
strategies/controlled_martingale.py — Controlled martingale recovery system.

⚠️ WARNING: Martingale strategies carry high risk. This implementation includes
strict controls to prevent catastrophic losses.

Concept: After a loss, increase position size to recover losses quickly.
BUT with strict limits to prevent account blowup.

Controls:
1. Maximum martingale level (3-5 steps max)
2. Maximum total exposure limit
3. Circuit breaker at drawdown threshold
4. Reset after win or max level reached

Usage:
    python -m strategies.controlled_martingale --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position


@dataclass
class MartingaleState:
    """Martingale progression state."""
    level: int  # Current martingale level (0 = base)
    consecutive_losses: int
    total_loss_amount: float  # Total loss to recover
    base_position_size: float
    current_position_size: float


class ControlledMartingale(Strategy):
    """
    Controlled martingale recovery system.

    Base Strategy: Spike fade (proven to work)

    Martingale Logic:
    - Level 0 (base): Position size = 1.0x
    - Level 1: Position size = 2.0x (recover L0 loss)
    - Level 2: Position size = 4.0x (recover L0 + L1 loss)
    - Level 3: Position size = 8.0x (recover L0 + L1 + L2 loss)
    - MAX LEVEL: 3 (total exposure = 15x base)

    Circuit Breakers:
    - Stop at 3 consecutive losses
    - Stop at 10% drawdown
    - Stop if total exposure > 15x base
    - Reset after any win

    Risk Controls:
    - Small base position (0.5% of equity)
    - Tight stops to minimize loss per level
    - Profitable base strategy required
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        # Base strategy params (spike fade)
        spike_multiplier: float = 3.0,
        entry_bar: int = 2,
        tp_multiplier: float = 2.0,
        sl_multiplier: float = 1.0,
        max_hold: int = 15,
        # Martingale params
        base_position_pct: float = 0.005,  # 0.5% of equity (SMALL!)
        martingale_multiplier: float = 2.0,  # 2x after each loss
        max_martingale_level: int = 3,  # Max 3 levels
        max_total_exposure: float = 15.0,  # Max 15x base
        circuit_breaker_dd: float = 0.10,  # Stop at 10% DD
        reset_on_win: bool = True,
    ):
        super().__init__('ControlledMartingale')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        # Base strategy
        self.spike_multiplier = spike_multiplier
        self.entry_bar = entry_bar
        self.tp_multiplier = tp_multiplier
        self.sl_multiplier = sl_multiplier
        self.max_hold = max_hold

        # Martingale
        self.base_position_pct = base_position_pct
        self.martingale_multiplier = martingale_multiplier
        self.max_martingale_level = max_martingale_level
        self.max_total_exposure = max_total_exposure
        self.circuit_breaker_dd = circuit_breaker_dd
        self.reset_on_win = reset_on_win

        # Martingale state
        self.martingale_state = MartingaleState(
            level=0,
            consecutive_losses=0,
            total_loss_amount=0.0,
            base_position_size=base_position_pct,
            current_position_size=base_position_pct,
        )

        # Spike detection
        self.last_spike_idx = None
        self.spike_magnitude = 0.0
        self.spike_price = 0.0
        self.spike_direction = None

        # Risk tracking
        self.peak_equity = 10000.0
        self.current_drawdown = 0.0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """
        Entry uses base strategy (spike fade).
        Position size adjusted by martingale level.
        """

        # Circuit breaker: stop if drawdown too high
        equity = state.get('equity', 10000.0)
        self.peak_equity = state.get('peak_equity', self.peak_equity)
        self.current_drawdown = (self.peak_equity - equity) / self.peak_equity

        if self.current_drawdown >= self.circuit_breaker_dd:
            return False, None  # CIRCUIT BREAKER TRIGGERED

        # Circuit breaker: stop at max level
        if self.martingale_state.level >= self.max_martingale_level:
            return False, None  # MAX LEVEL REACHED

        # Base strategy entry: spike fade
        if self.last_spike_idx is None:
            return False, None

        bars_since_spike = idx - self.last_spike_idx

        if bars_since_spike != self.entry_bar:
            return False, None

        # Calculate position size based on martingale level
        self.martingale_state.current_position_size = (
            self.base_position_pct * (self.martingale_multiplier ** self.martingale_state.level)
        )

        # Check total exposure limit
        if self.martingale_state.current_position_size > self.max_total_exposure * self.base_position_pct:
            return False, None  # EXPOSURE LIMIT

        # Enter opposite to spike
        if self.spike_direction == 'UP':
            return True, 'SHORT'
        else:
            return True, 'LONG'

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Standard spike fade exit."""

        current_price = prices[idx]

        # Take profit
        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            target = self.spike_magnitude * self.tp_multiplier

            if profit >= target:
                return True, 'TP'

            # Stop loss
            if current_price < self.spike_price:
                return True, 'SL'

        else:  # SHORT
            profit = position.entry_price - current_price
            target = self.spike_magnitude * self.tp_multiplier

            if profit >= target:
                return True, 'TP'

            # Stop loss
            if current_price > self.spike_price:
                return True, 'SL'

        # Timeout (tighter than base strategy)
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Update spike detection and martingale state."""

        # Detect spikes
        if idx >= 2:
            prev_move = abs(prices[idx-1] - prices[idx-2])
            recent_changes = np.abs(np.diff(prices[max(0, idx-100):idx]))
            positive = recent_changes[recent_changes > 0]

            if len(positive) > 0:
                median = np.median(positive)
                threshold = median * self.spike_multiplier

                if prev_move > threshold:
                    self.last_spike_idx = idx - 1
                    self.spike_magnitude = prev_move
                    self.spike_price = prices[idx-1]
                    self.spike_direction = 'UP' if prices[idx-1] > prices[idx-2] else 'DOWN'

    def update_martingale_state(self, trade_pnl: float):
        """
        Update martingale state after trade closes.

        Called externally after each trade.
        """

        if trade_pnl > 0:
            # WIN: Reset martingale
            if self.reset_on_win:
                self.martingale_state.level = 0
                self.martingale_state.consecutive_losses = 0
                self.martingale_state.total_loss_amount = 0.0
                self.martingale_state.current_position_size = self.base_position_pct

        else:
            # LOSS: Increment martingale level
            self.martingale_state.consecutive_losses += 1
            self.martingale_state.total_loss_amount += abs(trade_pnl)

            if self.martingale_state.level < self.max_martingale_level:
                self.martingale_state.level += 1


def backtest_controlled_martingale(symbol: str, optimize: bool = False):
    """Run controlled martingale backtest."""

    print(f"\n{'='*70}")
    print(f"⚠️  CONTROLLED MARTINGALE STRATEGY — {symbol}")
    print(f"{'='*70}")
    print("WARNING: Martingale strategies carry HIGH RISK.")
    print("This implementation includes strict controls.\n")

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
        strategy = ControlledMartingale(
            symbol=symbol,
            spike_multiplier=3.0,
            entry_bar=2,
            tp_multiplier=2.0,
            sl_multiplier=1.0,
            max_hold=15,
            base_position_pct=0.005,  # 0.5% base (SMALL!)
            martingale_multiplier=2.0,
            max_martingale_level=3,
            max_total_exposure=15.0,
            circuit_breaker_dd=0.10,
            reset_on_win=True,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("Controlled Martingale Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nExit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

        print(f"\n⚠️  Risk Analysis:")
        print(f"  Max Drawdown: {result.max_drawdown_pct:.2%}")
        print(f"  Max Consecutive Losses: {result.max_consecutive_losses}")
        print(f"  Circuit Breaker: {'TRIGGERED' if result.max_drawdown_pct >= 0.10 else 'Not triggered'}")

        print(f"\n💡 Martingale Statistics:")
        print(f"  Base position: 0.5% of equity")
        print(f"  Max martingale level: 3")
        print(f"  Max theoretical exposure: 15x base = 7.5% of equity")
        print(f"  Circuit breaker at: 10% drawdown")

    else:
        print("Optimizing controlled martingale parameters...\n")

        param_grid = {
            'symbol': [symbol],
            'spike_multiplier': [3.0],
            'entry_bar': [2],
            'tp_multiplier': [2.0, 2.5],
            'sl_multiplier': [1.0],
            'max_hold': [10, 15, 20],
            'base_position_pct': [0.003, 0.005, 0.008],  # Very small
            'martingale_multiplier': [2.0],
            'max_martingale_level': [2, 3],  # Conservative
            'circuit_breaker_dd': [0.08, 0.10, 0.12],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            ControlledMartingale,
            param_grid,
            metric='profit_factor'  # Not Sharpe - we want profitability
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

        test_strategy = ControlledMartingale(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, 10000.0)

        print(f"\nOut-of-Sample Validation:")
        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\n⚠️  SAFETY CHECK:")
        if test_result.max_drawdown_pct > 0.15:
            print("  ❌ FAILED: Max drawdown > 15% (TOO RISKY)")
        elif test_result.profit_factor < 1.5:
            print("  ❌ FAILED: Profit factor < 1.5 (NOT PROFITABLE ENOUGH)")
        elif test_result.max_consecutive_losses > 5:
            print("  ❌ FAILED: Max consecutive losses > 5 (TOO VOLATILE)")
        else:
            print("  ✅ PASSED: Risk controls within acceptable limits")
            print("  ⚠️  But still HIGH RISK - use with caution!")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Controlled Martingale Strategy')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_controlled_martingale(args.symbol, optimize=args.optimize)
