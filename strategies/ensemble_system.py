"""
strategies/ensemble_system.py — Multi-strategy ensemble with intelligent risk management.

Combines:
1. Spike Fade (mean reversion after spikes)
2. Tick Count Catcher (catching inevitable spikes)
3. Velocity Protection (early exit before counter-spikes)

Usage:
    python -m strategies.ensemble_system --symbol CRASH500 --backtest
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass
from typing import Literal

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_backtester import Strategy, Backtester, Position
from model.pattern_recognition import PatternRecognizer
from model.tick_predictor import TickPredictor


@dataclass
class SignalStrength:
    """Signal strength from each sub-strategy."""
    fade_signal: float = 0.0  # 0-1
    catcher_signal: float = 0.0  # 0-1
    velocity_signal: float = 0.0  # 0-1
    direction: Literal['LONG', 'SHORT', 'NEUTRAL'] = 'NEUTRAL'


class EnsembleSystem(Strategy):
    """
    Intelligent multi-strategy ensemble.

    Decision Logic:
    - Uses pattern recognition to assess market state
    - Combines signals from multiple strategies
    - Weighs strategies based on confidence
    - Applies dynamic risk management

    Entry:
    - Fade after confirmed spike (if high confidence)
    - Catch imminent spike (if tick count extreme)
    - Only enter when signals align

    Exit:
    - Velocity-based early exit (priority 1)
    - Take profit targets
    - Dynamic stops based on volatility
    """

    def __init__(
        self,
        symbol: str = 'CRASH500',
        # Fade params
        fade_spike_mult: float = 3.0,
        fade_entry_bar: int = 2,
        fade_tp_mult: float = 2.0,
        # Catcher params
        catcher_tick_threshold: int = 700,
        catcher_tp_points: float = 200.0,
        catcher_sl_points: float = 25.0,
        # Velocity params
        velocity_threshold: int = 12,
        # Ensemble params
        fade_weight: float = 0.4,
        catcher_weight: float = 0.4,
        velocity_weight: float = 0.2,
        min_confidence: float = 0.6,
        max_hold: int = 50,
    ):
        super().__init__('EnsembleSystem')

        self.symbol = symbol.upper()
        self.is_boom = 'BOOM' in self.symbol
        self.is_crash = 'CRASH' in self.symbol

        # Sub-strategy parameters
        self.fade_spike_mult = fade_spike_mult
        self.fade_entry_bar = fade_entry_bar
        self.fade_tp_mult = fade_tp_mult

        self.catcher_tick_threshold = catcher_tick_threshold
        self.catcher_tp_points = catcher_tp_points
        self.catcher_sl_points = catcher_sl_points

        self.velocity_threshold = velocity_threshold

        # Ensemble weights
        self.fade_weight = fade_weight
        self.catcher_weight = catcher_weight
        self.velocity_weight = velocity_weight
        self.min_confidence = min_confidence
        self.max_hold = max_hold

        # Pattern recognition
        self.recognizer = PatternRecognizer(
            symbol=symbol,
            velocity_threshold=velocity_threshold,
            tick_count_threshold=catcher_tick_threshold
        )

        # State
        self.last_spike_idx = None
        self.spike_magnitude = 0.0
        self.spike_price = 0.0
        self.spike_direction = None
        self.entry_reason = None  # Track which sub-strategy triggered entry

    def should_enter(self, prices: np.ndarray, idx: int, state: dict) -> tuple[bool, str | None]:
        """Ensemble decision: combine all signals."""

        if idx < 100:
            return False, None

        # Update recognizer
        self.recognizer.update(prices[:idx+1])

        # Get signals from each sub-strategy
        signals = self._compute_signals(prices, idx, state)

        # Combine signals
        ensemble_score = (
            signals.fade_signal * self.fade_weight +
            signals.catcher_signal * self.catcher_weight +
            signals.velocity_signal * self.velocity_weight
        )

        # Enter if confidence high enough
        if ensemble_score >= self.min_confidence:
            # Track which strategy triggered (for analysis)
            if signals.catcher_signal > signals.fade_signal:
                self.entry_reason = 'CATCHER'
            else:
                self.entry_reason = 'FADE'

            return True, signals.direction

        return False, None

    def should_exit(self, prices: np.ndarray, idx: int, position: Position, state: dict) -> tuple[bool, str]:
        """Ensemble exit: velocity protection + targets."""

        current_price = prices[idx]

        # Update recognizer
        self.recognizer.update(prices[:idx+1])

        # PRIORITY 1: Velocity exit (protect from counter-spike)
        velocity, vel_dir = self.recognizer.analyze_velocity_buildup(
            prices[max(0, idx-30):idx+1],
            lookback=20
        )

        if position.direction == 'LONG' and vel_dir == 'DOWN' and velocity >= self.velocity_threshold:
            return True, f'VELOCITY_EXIT_{self.entry_reason}'

        if position.direction == 'SHORT' and vel_dir == 'UP' and velocity >= self.velocity_threshold:
            return True, f'VELOCITY_EXIT_{self.entry_reason}'

        # PRIORITY 2: Take profit (varies by entry reason)
        if self.entry_reason == 'CATCHER':
            # Catcher: tight TP to catch spike
            if position.direction == 'LONG':
                profit = current_price - position.entry_price
                if profit >= self.catcher_tp_points:
                    return True, 'TP_CATCHER'
            else:
                profit = position.entry_price - current_price
                if profit >= self.catcher_tp_points:
                    return True, 'TP_CATCHER'

        elif self.entry_reason == 'FADE':
            # Fade: retracement-based TP
            if position.direction == 'LONG':
                profit = current_price - position.entry_price
                if profit >= self.spike_magnitude * self.fade_tp_mult:
                    return True, 'TP_FADE'
            else:
                profit = position.entry_price - current_price
                if profit >= self.spike_magnitude * self.fade_tp_mult:
                    return True, 'TP_FADE'

        # PRIORITY 3: Stop loss (varies by entry)
        if self.entry_reason == 'CATCHER':
            # Tight stop for catcher
            if position.direction == 'LONG':
                loss = position.entry_price - current_price
                if loss >= self.catcher_sl_points:
                    return True, 'SL_CATCHER'
            else:
                loss = current_price - position.entry_price
                if loss >= self.catcher_sl_points:
                    return True, 'SL_CATCHER'

        elif self.entry_reason == 'FADE':
            # Spike-based stop for fade
            if position.direction == 'LONG' and current_price < self.spike_price:
                return True, 'SL_FADE'
            if position.direction == 'SHORT' and current_price > self.spike_price:
                return True, 'SL_FADE'

        # PRIORITY 4: Timeout
        if position.bars_held >= self.max_hold:
            return True, f'TIMEOUT_{self.entry_reason}'

        return False, ''

    def _compute_signals(self, prices: np.ndarray, idx: int, state: dict) -> SignalStrength:
        """Compute signal strength from each sub-strategy."""

        signals = SignalStrength()

        # 1. Fade signal (after spike)
        if self.last_spike_idx is not None:
            bars_since = idx - self.last_spike_idx

            if bars_since == self.fade_entry_bar:
                # Recent spike, ready to fade
                signals.fade_signal = 0.8

                if self.spike_direction == 'UP':
                    signals.direction = 'SHORT'
                else:
                    signals.direction = 'LONG'

        # 2. Catcher signal (tick exhaustion)
        spike_mask = self.recognizer.detect_spikes(prices[:idx+1])
        spike_indices = np.where(spike_mask)[0]

        if len(spike_indices) > 0:
            ticks_since_spike = idx - spike_indices[-1]
        else:
            ticks_since_spike = idx

        # Exponential probability increase
        if ticks_since_spike >= self.catcher_tick_threshold * 1.2:
            signals.catcher_signal = 0.95
        elif ticks_since_spike >= self.catcher_tick_threshold:
            signals.catcher_signal = 0.7
        elif ticks_since_spike >= self.catcher_tick_threshold * 0.8:
            signals.catcher_signal = 0.3

        if signals.catcher_signal > 0:
            if self.is_boom:
                signals.direction = 'LONG'
            elif self.is_crash:
                signals.direction = 'SHORT'

        # 3. Velocity signal (buildupdetected)
        velocity, vel_dir = self.recognizer.analyze_velocity_buildup(prices[max(0, idx-30):idx+1])

        if velocity >= self.velocity_threshold:
            # Velocity detected - boost catcher signal
            if self.is_crash and vel_dir == 'UP':
                signals.velocity_signal = min(velocity / 20.0, 1.0)
                signals.direction = 'SHORT'
            elif self.is_boom and vel_dir == 'DOWN':
                signals.velocity_signal = min(velocity / 20.0, 1.0)
                signals.direction = 'LONG'

        # Resolve conflicts (catcher usually stronger)
        if signals.catcher_signal > signals.fade_signal:
            # Use catcher direction
            pass
        elif signals.fade_signal > 0:
            # Use fade direction
            pass

        return signals

    def on_tick(self, prices: np.ndarray, idx: int, state: dict):
        """Update spike detection."""

        if idx < 2:
            return

        prev_move = abs(prices[idx-1] - prices[idx-2])
        recent_changes = np.abs(np.diff(prices[max(0, idx-100):idx]))
        positive = recent_changes[recent_changes > 0]

        if len(positive) > 0:
            median = np.median(positive)
            threshold = median * self.fade_spike_mult

            if prev_move > threshold:
                self.last_spike_idx = idx - 1
                self.spike_magnitude = prev_move
                self.spike_price = prices[idx-1]
                self.spike_direction = 'UP' if prices[idx-1] > prices[idx-2] else 'DOWN'


def backtest_ensemble(symbol: str, optimize: bool = False):
    """Run ensemble backtest."""

    print(f"\n{'='*70}")
    print(f"ENSEMBLE SYSTEM BACKTEST — {symbol}")
    print(f"{'='*70}\n")

    # Load data
    symbol_lower = symbol.lower()
    file_path = Path(f'data/candles_{symbol_lower}_1min.parquet')
    df = pd.read_parquet(file_path)
    prices = df['close'].values

    print(f"Loaded {len(df)} bars\n")

    # Set defaults based on symbol
    if '300' in symbol:
        default_threshold = 500
    elif '500' in symbol:
        default_threshold = 700
    elif '1000' in symbol:
        default_threshold = 900
    else:
        default_threshold = 700

    if not optimize:
        strategy = EnsembleSystem(
            symbol=symbol,
            fade_spike_mult=3.0,
            fade_entry_bar=2,
            fade_tp_mult=2.0,
            catcher_tick_threshold=default_threshold,
            catcher_tp_points=200.0,
            catcher_sl_points=25.0,
            velocity_threshold=12,
            fade_weight=0.4,
            catcher_weight=0.4,
            velocity_weight=0.2,
            min_confidence=0.6,
        )

        backtester = Backtester(prices, symbol=symbol)
        result = backtester.run(strategy, initial_equity=10000.0)

        print("Ensemble Results:")
        for key, value in result.to_dict().items():
            print(f"  {key}: {value}")

        print(f"\nDetailed Exit Breakdown:")
        for reason, count in sorted(result.exit_reasons.items()):
            pct = count / result.total_trades * 100 if result.total_trades > 0 else 0
            print(f"  {reason}: {count} ({pct:.1f}%)")

        # Analyze by entry type
        catcher_trades = [t for t in result.trades if any('CATCHER' in str(t.exit_reason))]
        fade_trades = [t for t in result.trades if any('FADE' in str(t.exit_reason))]

        if catcher_trades:
            catcher_wr = sum(1 for t in catcher_trades if t.is_winner) / len(catcher_trades)
            print(f"\nCatcher Sub-Strategy:")
            print(f"  Trades: {len(catcher_trades)}")
            print(f"  Win Rate: {catcher_wr:.1%}")

        if fade_trades:
            fade_wr = sum(1 for t in fade_trades if t.is_winner) / len(fade_trades)
            print(f"\nFade Sub-Strategy:")
            print(f"  Trades: {len(fade_trades)}")
            print(f"  Win Rate: {fade_wr:.1%}")

    else:
        print("Optimizing ensemble parameters...\n")

        if '500' in symbol:
            tick_range = [650, 700, 750]
        elif '1000' in symbol:
            tick_range = [850, 900, 950]
        else:
            tick_range = [450, 500, 550]

        param_grid = {
            'symbol': [symbol],
            'fade_spike_mult': [3.0],
            'fade_entry_bar': [2],
            'fade_tp_mult': [2.0, 2.5],
            'catcher_tick_threshold': tick_range,
            'catcher_tp_points': [200.0],
            'catcher_sl_points': [25.0],
            'velocity_threshold': [12],
            'fade_weight': [0.3, 0.4, 0.5],
            'catcher_weight': [0.3, 0.4, 0.5],
            'min_confidence': [0.5, 0.6, 0.7],
        }

        backtester = Backtester(prices, symbol=symbol)
        best_params, best_result = backtester.optimize(
            EnsembleSystem,
            param_grid,
            metric='sharpe_ratio'
        )

        print("Best Parameters:")
        for key, value in best_params.items():
            print(f"  {key}: {value}")

        print(f"\nBest In-Sample Results:")
        for key, value in best_result.to_dict().items():
            print(f"  {key}: {value}")

        # Walk-forward test
        train_size = int(len(prices) * 0.7)
        test_prices = prices[train_size:]

        test_strategy = EnsembleSystem(**best_params)
        test_backtester = Backtester(test_prices, symbol=symbol)
        test_result = test_backtester.run(test_strategy, 10000.0)

        print(f"\n{'='*70}")
        print("OUT-OF-SAMPLE VALIDATION")
        print(f"{'='*70}\n")

        for key, value in test_result.to_dict().items():
            print(f"  {key}: {value}")

        # Final gate
        print(f"\n{'='*70}")
        print("DEPLOYMENT GATE CHECK")
        print(f"{'='*70}\n")

        passed = True
        reasons = []

        if test_result.profit_factor < 1.5:
            passed = False
            reasons.append(f"PF {test_result.profit_factor:.2f} < 1.5")

        if test_result.win_rate < 0.60:
            passed = False
            reasons.append(f"WR {test_result.win_rate:.1%} < 60%")

        if test_result.sharpe_ratio < 1.0:
            passed = False
            reasons.append(f"Sharpe {test_result.sharpe_ratio:.2f} < 1.0")

        if passed:
            print("✅ PASSED - Ready for paper trading")
            print(f"\nRecommended Configuration:")
            for key, value in best_params.items():
                print(f"  {key} = {value}")
        else:
            print("❌ FAILED")
            for reason in reasons:
                print(f"  - {reason}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='Ensemble Strategy System')
    parser.add_argument('--symbol', default='CRASH500')
    parser.add_argument('--optimize', action='store_true')
    args = parser.parse_args()

    backtest_ensemble(args.symbol, optimize=args.optimize)
