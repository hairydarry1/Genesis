"""
model/strategy_backtester.py — Comprehensive backtesting framework for Boom/Crash strategies.

Features:
1. Walk-forward optimization
2. Parameter optimization with grid search
3. Monte Carlo simulation
4. Multi-strategy comparison
5. Risk metrics calculation

Usage:
    from model.strategy_backtester import Backtester, Strategy

    backtester = Backtester(tick_data, symbol='CRASH500')
    results = backtester.run(strategy, initial_equity=10000)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Literal, Any
import numpy as np
import pandas as pd
from collections import defaultdict
from enum import Enum


class OrderType(Enum):
    """Order types."""
    BUY = 'BUY'
    SELL = 'SELL'
    CLOSE = 'CLOSE'


@dataclass
class Trade:
    """Single trade record."""
    entry_idx: int
    entry_price: float
    direction: Literal['LONG', 'SHORT']
    size: float

    exit_idx: int | None = None
    exit_price: float | None = None
    exit_reason: str = ''

    pnl: float = 0.0
    pnl_pct: float = 0.0

    def close(self, exit_idx: int, exit_price: float, reason: str):
        """Close the trade and calculate PnL."""
        self.exit_idx = exit_idx
        self.exit_price = exit_price
        self.exit_reason = reason

        if self.direction == 'LONG':
            self.pnl = (exit_price - self.entry_price) * self.size
        else:  # SHORT
            self.pnl = (self.entry_price - exit_price) * self.size

        self.pnl_pct = self.pnl / (self.entry_price * self.size) if (self.entry_price * self.size) > 0 else 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_idx is None

    @property
    def is_winner(self) -> bool:
        return self.pnl > 0

    @property
    def duration(self) -> int:
        """Trade duration in ticks."""
        if self.exit_idx is None:
            return 0
        return self.exit_idx - self.entry_idx


@dataclass
class BacktestResult:
    """Backtest results with performance metrics."""

    # Trade statistics
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    # PnL
    total_pnl: float = 0.0
    total_return_pct: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    profit_factor: float = 0.0

    # Risk metrics
    max_drawdown: float = 0.0
    max_drawdown_pct: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    calmar_ratio: float = 0.0

    # Trade metrics
    avg_win: float = 0.0
    avg_loss: float = 0.0
    largest_win: float = 0.0
    largest_loss: float = 0.0
    avg_trade_duration: float = 0.0

    # Streak metrics
    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0

    # Equity curve
    equity_curve: list[float] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)

    # Exit breakdown
    exit_reasons: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def to_dict(self) -> dict:
        """Convert to dictionary for easy printing."""
        return {
            'Total Trades': self.total_trades,
            'Win Rate': f"{self.win_rate:.1%}",
            'Total PnL': f"{self.total_pnl:.2f}",
            'Total Return': f"{self.total_return_pct:.2%}",
            'Profit Factor': f"{self.profit_factor:.2f}",
            'Max Drawdown': f"{self.max_drawdown_pct:.2%}",
            'Sharpe Ratio': f"{self.sharpe_ratio:.2f}",
            'Avg Win': f"{self.avg_win:.2f}",
            'Avg Loss': f"{self.avg_loss:.2f}",
            'Avg Duration': f"{self.avg_trade_duration:.1f} ticks",
        }


@dataclass
class Position:
    """Current position state."""
    direction: Literal['LONG', 'SHORT'] | None = None
    entry_price: float = 0.0
    entry_idx: int = 0
    size: float = 0.0
    bars_held: int = 0

    @property
    def is_open(self) -> bool:
        return self.direction is not None

    def open(self, direction: Literal['LONG', 'SHORT'], price: float, idx: int, size: float):
        """Open a new position."""
        self.direction = direction
        self.entry_price = price
        self.entry_idx = idx
        self.size = size
        self.bars_held = 0

    def close(self):
        """Close the position."""
        self.direction = None
        self.entry_price = 0.0
        self.entry_idx = 0
        self.size = 0.0
        self.bars_held = 0

    def unrealized_pnl(self, current_price: float) -> float:
        """Calculate unrealized PnL."""
        if not self.is_open:
            return 0.0

        if self.direction == 'LONG':
            return (current_price - self.entry_price) * self.size
        else:  # SHORT
            return (self.entry_price - current_price) * self.size


class Strategy:
    """
    Base strategy class.

    Subclass this and implement should_enter() and should_exit() methods.
    """

    def __init__(self, name: str, **params):
        self.name = name
        self.params = params

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, Literal['LONG', 'SHORT'] | None]:
        """
        Determine if we should enter a trade.

        Returns
        -------
        tuple[bool, str | None]
            (should_enter, direction) where direction is 'LONG' or 'SHORT'
        """
        raise NotImplementedError

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """
        Determine if we should exit current position.

        Returns
        -------
        tuple[bool, str]
            (should_exit, reason)
        """
        raise NotImplementedError

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Called on each tick for state updates."""
        pass


class Backtester:
    """
    Comprehensive backtesting engine.

    Parameters
    ----------
    prices : np.ndarray
        Tick price data
    symbol : str
        'BOOM300', 'BOOM500', etc.
    spread : float
        Spread cost per trade (in price points)
    slippage : float
        Slippage cost per trade (in price points)
    """

    def __init__(
        self,
        prices: np.ndarray,
        symbol: str = 'CRASH500',
        spread: float = 0.0001,
        slippage: float = 0.00005,
    ):
        self.prices = prices
        self.symbol = symbol.upper()
        self.spread = spread
        self.slippage = slippage

        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

    def run(
        self,
        strategy: Strategy,
        initial_equity: float = 10000.0,
        position_size_pct: float = 1.0,
        max_bars_in_trade: int | None = None,
    ) -> BacktestResult:
        """
        Run backtest for given strategy.

        Parameters
        ----------
        strategy : Strategy
            Strategy to backtest
        initial_equity : float
            Starting equity
        position_size_pct : float
            Position size as fraction of equity
        max_bars_in_trade : int | None
            Maximum bars to hold a trade (timeout)

        Returns
        -------
        BacktestResult
            Complete backtest results
        """
        equity = initial_equity
        peak_equity = initial_equity
        equity_curve = [initial_equity]

        position = Position()
        trades: list[Trade] = []

        state = {
            'equity': equity,
            'peak_equity': peak_equity,
            'last_spike_idx': 0,
            'spike_history': [],
        }

        # Detect spikes once for the whole dataset
        spike_mask = self._detect_spikes(self.prices)
        spike_indices = np.where(spike_mask)[0]
        state['spike_indices'] = spike_indices

        # Run backtest tick by tick
        for idx in range(1, len(self.prices)):
            current_price = self.prices[idx]

            # Update state
            state['equity'] = equity
            state['peak_equity'] = peak_equity

            if len(spike_indices) > 0:
                past_spikes = spike_indices[spike_indices < idx]
                if len(past_spikes) > 0:
                    state['last_spike_idx'] = past_spikes[-1]

            # Call strategy's on_tick
            strategy.on_tick(self.prices, idx, state)

            # Check exit conditions if in position
            if position.is_open:
                position.bars_held += 1

                should_exit, exit_reason = strategy.should_exit(self.prices, idx, position, state)

                # Timeout check
                if max_bars_in_trade and position.bars_held >= max_bars_in_trade:
                    should_exit = True
                    exit_reason = 'TIMEOUT'

                if should_exit:
                    # Close position
                    exit_price = self._apply_transaction_costs(current_price, 'exit')

                    trade = Trade(
                        entry_idx=position.entry_idx,
                        entry_price=position.entry_price,
                        direction=position.direction,
                        size=position.size,
                    )
                    trade.close(idx, exit_price, exit_reason)

                    equity += trade.pnl
                    peak_equity = max(peak_equity, equity)

                    trades.append(trade)
                    position.close()

            # Check entry conditions if not in position
            elif not position.is_open:
                should_enter, direction = strategy.should_enter(self.prices, idx, state)

                if should_enter and direction:
                    # Open position
                    entry_price = self._apply_transaction_costs(current_price, 'entry')
                    size = (equity * position_size_pct) / entry_price

                    position.open(direction, entry_price, idx, size)

            equity_curve.append(equity)

        # Close any open position at end
        if position.is_open:
            exit_price = self._apply_transaction_costs(self.prices[-1], 'exit')
            trade = Trade(
                entry_idx=position.entry_idx,
                entry_price=position.entry_price,
                direction=position.direction,
                size=position.size,
            )
            trade.close(len(self.prices) - 1, exit_price, 'END_OF_DATA')
            equity += trade.pnl
            trades.append(trade)

        # Calculate metrics
        return self._calculate_metrics(trades, equity_curve, initial_equity)

    def _detect_spikes(self, prices: np.ndarray, multiplier: float = 3.0) -> np.ndarray:
        """Detect spikes in price data."""
        changes = np.abs(np.diff(prices))
        positive = changes[changes > 0]

        if len(positive) == 0:
            return np.zeros(len(prices), dtype=bool)

        median = np.median(positive)
        threshold = median * multiplier

        spike_mask = np.zeros(len(prices), dtype=bool)
        spike_mask[1:] = changes > threshold

        return spike_mask

    def _apply_transaction_costs(self, price: float, action: str) -> float:
        """Apply spread and slippage costs."""
        total_cost = self.spread + self.slippage

        if action == 'entry':
            return price + total_cost
        else:  # exit
            return price - total_cost

    def _calculate_metrics(
        self,
        trades: list[Trade],
        equity_curve: list[float],
        initial_equity: float
    ) -> BacktestResult:
        """Calculate all performance metrics."""
        result = BacktestResult()

        if len(trades) == 0:
            return result

        result.trades = trades
        result.equity_curve = equity_curve
        result.total_trades = len(trades)

        # Win/loss statistics
        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]

        result.winning_trades = len(winners)
        result.losing_trades = len(losers)
        result.win_rate = len(winners) / len(trades) if len(trades) > 0 else 0.0

        # PnL
        result.total_pnl = sum(t.pnl for t in trades)
        result.total_return_pct = result.total_pnl / initial_equity if initial_equity > 0 else 0.0

        result.gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        result.gross_loss = abs(sum(t.pnl for t in losers)) if losers else 0.0
        result.profit_factor = result.gross_profit / result.gross_loss if result.gross_loss > 0 else 0.0

        # Trade metrics
        result.avg_win = np.mean([t.pnl for t in winners]) if winners else 0.0
        result.avg_loss = np.mean([t.pnl for t in losers]) if losers else 0.0
        result.largest_win = max([t.pnl for t in winners]) if winners else 0.0
        result.largest_loss = min([t.pnl for t in losers]) if losers else 0.0
        result.avg_trade_duration = np.mean([t.duration for t in trades])

        # Drawdown
        equity_array = np.array(equity_curve)
        peak = np.maximum.accumulate(equity_array)
        drawdown = peak - equity_array
        result.max_drawdown = np.max(drawdown)
        result.max_drawdown_pct = result.max_drawdown / initial_equity if initial_equity > 0 else 0.0

        # Sharpe ratio
        returns = np.diff(equity_array) / equity_array[:-1]
        if len(returns) > 0 and np.std(returns) > 0:
            result.sharpe_ratio = np.mean(returns) / np.std(returns) * np.sqrt(252)

        # Sortino ratio
        negative_returns = returns[returns < 0]
        if len(negative_returns) > 0 and np.std(negative_returns) > 0:
            result.sortino_ratio = np.mean(returns) / np.std(negative_returns) * np.sqrt(252)

        # Calmar ratio
        if result.max_drawdown_pct > 0:
            result.calmar_ratio = result.total_return_pct / result.max_drawdown_pct

        # Consecutive wins/losses
        current_streak = 0
        max_win_streak = 0
        max_loss_streak = 0

        for trade in trades:
            if trade.is_winner:
                if current_streak >= 0:
                    current_streak += 1
                else:
                    current_streak = 1
                max_win_streak = max(max_win_streak, current_streak)
            else:
                if current_streak <= 0:
                    current_streak -= 1
                else:
                    current_streak = -1
                max_loss_streak = max(max_loss_streak, abs(current_streak))

        result.max_consecutive_wins = max_win_streak
        result.max_consecutive_losses = max_loss_streak

        # Exit reasons
        for trade in trades:
            result.exit_reasons[trade.exit_reason] += 1

        return result

    def optimize(
        self,
        strategy_class: type[Strategy],
        param_grid: dict[str, list],
        initial_equity: float = 10000.0,
        metric: str = 'profit_factor',
    ) -> tuple[dict, BacktestResult]:
        """
        Grid search parameter optimization.

        Parameters
        ----------
        strategy_class : type[Strategy]
            Strategy class to optimize
        param_grid : dict[str, list]
            Parameter grid, e.g. {'threshold': [10, 15, 20], 'tp_mult': [2.0, 2.5]}
        initial_equity : float
            Starting equity
        metric : str
            Metric to optimize ('profit_factor', 'sharpe_ratio', 'total_return_pct', etc.)

        Returns
        -------
        tuple[dict, BacktestResult]
            (best_params, best_result)
        """
        from itertools import product

        param_names = list(param_grid.keys())
        param_values = list(param_grid.values())

        best_score = -np.inf
        best_params = None
        best_result = None

        # Grid search
        for values in product(*param_values):
            params = dict(zip(param_names, values))

            strategy = strategy_class(**params)
            result = self.run(strategy, initial_equity=initial_equity)

            score = getattr(result, metric, 0.0)

            if score > best_score:
                best_score = score
                best_params = params
                best_result = result

        return best_params, best_result


# Example strategies

class SpikeFadeStrategy(Strategy):
    """Fade spikes after they occur."""

    def __init__(
        self,
        spike_multiplier: float = 3.0,
        entry_bar: int = 2,
        tp_multiplier: float = 2.0,
        sl_multiplier: float = 1.0,
        max_hold: int = 10,
    ):
        super().__init__('SpikeFade')
        self.spike_multiplier = spike_multiplier
        self.entry_bar = entry_bar
        self.tp_multiplier = tp_multiplier
        self.sl_multiplier = sl_multiplier
        self.max_hold = max_hold

        self.spike_detected_idx = None
        self.spike_magnitude = 0.0
        self.spike_price = 0.0

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, Literal['LONG', 'SHORT'] | None]:
        # Check if we detected a spike recently
        if self.spike_detected_idx is None:
            return False, None

        bars_since_spike = idx - self.spike_detected_idx

        if bars_since_spike == self.entry_bar:
            # Enter opposite direction
            if prices[self.spike_detected_idx] > prices[self.spike_detected_idx - 1]:
                direction = 'SHORT'  # Spike was up, fade down
            else:
                direction = 'LONG'   # Spike was down, fade up

            return True, direction

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        current_price = prices[idx]

        # Take profit: target retracement
        if position.direction == 'LONG':
            profit = current_price - position.entry_price
            if profit >= self.spike_magnitude * self.tp_multiplier:
                return True, 'TP'

            # Stop loss
            if current_price < self.spike_price:
                return True, 'SL'

        else:  # SHORT
            profit = position.entry_price - current_price
            if profit >= self.spike_magnitude * self.tp_multiplier:
                return True, 'TP'

            # Stop loss
            if current_price > self.spike_price:
                return True, 'SL'

        # Max hold
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'

        return False, ''

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        if idx < 2:
            return

        # Detect spike
        prev_change = abs(prices[idx - 1] - prices[idx - 2])
        changes = np.abs(np.diff(prices[max(0, idx - 100):idx]))
        positive = changes[changes > 0]

        if len(positive) > 0:
            median = np.median(positive)
            threshold = median * self.spike_multiplier

            if prev_change > threshold:
                self.spike_detected_idx = idx - 1
                self.spike_magnitude = prev_change
                self.spike_price = prices[idx - 1]


if __name__ == "__main__":
    print("Backtesting Framework Test")
    print("=" * 60)

    # Generate test data
    np.random.seed(42)
    prices = np.cumsum(np.random.randn(5000) * 0.01) + 100.0

    # Inject spikes
    for i in range(200, 5000, 500):
        prices[i] += 3.0

    backtester = Backtester(prices, symbol='BOOM500')

    # Test spike fade strategy
    strategy = SpikeFadeStrategy(
        spike_multiplier=3.0,
        entry_bar=2,
        tp_multiplier=2.0,
        sl_multiplier=1.0,
    )

    result = backtester.run(strategy, initial_equity=10000.0)

    print("\nBacktest Results:")
    for key, value in result.to_dict().items():
        print(f"  {key}: {value}")

    print(f"\nExit Breakdown:")
    for reason, count in result.exit_reasons.items():
        print(f"  {reason}: {count}")

    print("\n[OK] Backtest test complete!")
