"""
model/exit_engine.py — Advanced exit and sizing logic.

Components:
  1. TrailingStop — moves stop in profit direction
  2. AdaptiveTPSL — adjusts TP/SL based on ATR
  3. DrawdownSizer — reduces size during drawdowns
  4. StreakDetector — pauses after consecutive losses

Usage:
    from model.exit_engine import ExitEngine

    engine = ExitEngine()
    engine.update_atr(closes)  # Call each bar
    should_exit, reason = engine.check_exit(entry_price, current_price, position, bar)
    size = engine.compute_size(kelly_base, current_equity, peak_equity)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Trailing Stop
# ---------------------------------------------------------------------------
class TrailingStop:
    """
    Moves stop loss in the direction of profit.

    Parameters:
      activation_pct: Start trailing after this % profit (default: 0.15%)
      trail_distance: Distance behind best price (default: 0.10%)
      min_profit: Minimum profit to activate trailing (default: 0.10%)
    """

    def __init__(
        self,
        activation_pct: float = 0.0015,  # Activate after 0.15% profit
        trail_distance: float = 0.0010,   # Trail 0.10% behind best
    ):
        self.activation_pct = activation_pct
        self.trail_distance = trail_distance
        self.best_price = 0.0
        self.activated = False

    def reset(self):
        self.best_price = 0.0
        self.activated = False

    def update(self, current_price: float, position: int) -> float:
        """
        Update trailing stop. Returns the stop price.
        """
        if position == 1:  # Long
            if current_price > self.best_price:
                self.best_price = current_price
            profit = (self.best_price - self.entry_price) / self.entry_price if hasattr(self, 'entry_price') else 0
            if profit >= self.activation_pct:
                self.activated = True
            if self.activated:
                return self.best_price * (1 - self.trail_distance)
            return 0  # No stop yet

        elif position == -1:  # Short
            if self.best_price == 0 or current_price < self.best_price:
                self.best_price = current_price
            profit = (self.entry_price - self.best_price) / self.entry_price if hasattr(self, 'entry_price') else 0
            if profit >= self.activation_pct:
                self.activated = True
            if self.activated:
                return self.best_price * (1 + self.trail_distance)
            return float('inf')  # No stop yet

        return 0

    def check(self, current_price: float, position: int, entry_price: float) -> tuple[bool, str]:
        """Check if trailing stop is hit."""
        self.entry_price = entry_price

        if position == 1:
            # Update best price
            if not hasattr(self, '_best') or current_price > self._best:
                self._best = current_price

            profit_pct = (self._best - entry_price) / entry_price

            # Activate after minimum profit
            if profit_pct >= self.activation_pct:
                self.activated = True

            if self.activated:
                stop_price = self._best * (1 - self.trail_distance)
                if current_price <= stop_price:
                    return True, f"trailing_stop (best={self._best:.2f}, stop={stop_price:.2f})"

        elif position == -1:
            if not hasattr(self, '_best') or current_price < self._best:
                self._best = current_price

            profit_pct = (entry_price - self._best) / entry_price

            if profit_pct >= self.activation_pct:
                self.activated = True

            if self.activated:
                stop_price = self._best * (1 + self.trail_distance)
                if current_price >= stop_price:
                    return True, f"trailing_stop (best={self._best:.2f}, stop={stop_price:.2f})"

        return False, ""

    def start(self, entry_price: float):
        """Initialize for new trade."""
        self.entry_price = entry_price
        self._best = entry_price
        self.activated = False


# ---------------------------------------------------------------------------
# Adaptive TP/SL (ATR-based)
# ---------------------------------------------------------------------------
class AdaptiveTPSL:
    """
    Adjusts take profit and stop loss based on current ATR.

    When ATR is high (volatile), use wider TP/SL.
    When ATR is low (calm), use tighter TP/SL.

    Parameters:
      tp_atr_mult: Take profit = ATR × this multiplier
      sl_atr_mult: Stop loss = ATR × this multiplier
      min_tp: Minimum take profit (floor)
      max_tp: Maximum take profit (ceiling)
      min_sl: Minimum stop loss (floor)
      max_sl: Maximum stop loss (ceiling)
    """

    def __init__(
        self,
        tp_atr_mult: float = 3.0,
        sl_atr_mult: float = 2.0,
        min_tp: float = 0.0020,   # 0.20% minimum (walk-forward optimized)
        max_tp: float = 0.0060,   # 0.60% maximum
        min_sl: float = 0.0010,   # 0.10% minimum
        max_sl: float = 0.0030,   # 0.30% maximum
        atr_period: int = 20,
    ):
        self.tp_atr_mult = tp_atr_mult
        self.sl_atr_mult = sl_atr_mult
        self.min_tp = min_tp
        self.max_tp = max_tp
        self.min_sl = min_sl
        self.max_sl = max_sl
        self.atr_period = atr_period
        self.current_atr = 0.0
        self.current_tp = 0.0030
        self.current_sl = 0.0010

    def update_atr(self, closes: np.ndarray):
        """Update ATR from recent price data."""
        if len(closes) < self.atr_period + 1:
            return

        # True range approximation using close-to-close
        returns = np.abs(np.diff(np.log(np.maximum(closes[-self.atr_period - 1:], 1e-10))))
        self.current_atr = float(np.mean(returns))

        # Compute adaptive TP/SL
        self.current_tp = np.clip(
            self.current_atr * self.tp_atr_mult,
            self.min_tp, self.max_tp,
        )
        self.current_sl = np.clip(
            self.current_atr * self.sl_atr_mult,
            self.min_sl, self.max_sl,
        )

    def get_tp(self) -> float:
        return self.current_tp

    def get_sl(self) -> float:
        return self.current_sl

    def check_exit(self, entry_price: float, current_price: float, position: int) -> tuple[bool, str]:
        """Check if adaptive TP/SL is hit."""
        pnl_pct = (current_price - entry_price) / entry_price * position

        if pnl_pct >= self.current_tp:
            return True, f"adaptive_tp ({pnl_pct*100:+.3f}% >= {self.current_tp*100:.3f}%)"
        if pnl_pct <= -self.current_sl:
            return True, f"adaptive_sl ({pnl_pct*100:+.3f}% <= -{self.current_sl*100:.3f}%)"

        return False, ""


# ---------------------------------------------------------------------------
# Drawdown-Based Sizing
# ---------------------------------------------------------------------------
class DrawdownSizer:
    """
    Reduces position size during drawdowns.

    Formula: size = base_size × (1 - DD/max_DD)
    At max_DD, size = 0 (stop trading).

    Parameters:
      max_dd: Maximum drawdown before stopping (default: 15%)
      scale_start: DD level where scaling begins (default: 3%)
    """

    def __init__(
        self,
        max_dd: float = 0.15,       # Stop at 15% drawdown
        scale_start: float = 0.03,   # Start scaling at 3% DD
    ):
        self.max_dd = max_dd
        self.scale_start = scale_start

    def compute_multiplier(self, current_equity: float, peak_equity: float) -> float:
        """
        Returns a multiplier between 0 and 1.
        1.0 = full size, 0.0 = no trading.
        """
        if peak_equity <= 0:
            return 0.0

        dd = (peak_equity - current_equity) / peak_equity

        if dd <= self.scale_start:
            return 1.0  # No reduction

        if dd >= self.max_dd:
            return 0.0  # Stop trading

        # Linear scale between scale_start and max_dd
        reduction = (dd - self.scale_start) / (self.max_dd - self.scale_start)
        return max(0.0, 1.0 - reduction)

    def should_trade(self, current_equity: float, peak_equity: float) -> tuple[bool, str]:
        """Check if we should trade at all."""
        mult = self.compute_multiplier(current_equity, peak_equity)
        if mult <= 0:
            dd = (peak_equity - current_equity) / peak_equity * 100
            return False, f"max_dd_reached ({dd:.1f}%)"
        return True, f"dd_ok ({mult:.0%} size)"


# ---------------------------------------------------------------------------
# Win/Loss Streak Detector
# ---------------------------------------------------------------------------
class StreakDetector:
    """
    Detects winning and losing streaks.
    Pauses trading after too many consecutive losses.

    Parameters:
      max_consec_losses: Pause after this many consecutive losses (default: 5)
      cooldown_bars: Wait this many bars after pause (default: 30)
      reduce_after: Reduce size after this many losses (default: 3)
    """

    def __init__(
        self,
        max_consec_losses: int = 5,
        cooldown_bars: int = 30,
        reduce_after: int = 3,
    ):
        self.max_consec_losses = max_consec_losses
        self.cooldown_bars = cooldown_bars
        self.reduce_after = reduce_after
        self.consec_losses = 0
        self.consec_wins = 0
        self.cooldown_remaining = 0
        self.history: list[bool] = []  # True = win, False = loss

    def record_trade(self, win: bool):
        """Record a completed trade."""
        self.history.append(win)
        if len(self.history) > 100:
            self.history = self.history[-100:]

        if win:
            self.consec_wins += 1
            self.consec_losses = 0
        else:
            self.consec_losses += 1
            self.consec_wins = 0

            # Start cooldown after max consecutive losses
            if self.consec_losses >= self.max_consec_losses:
                self.cooldown_remaining = self.cooldown_bars

    def tick(self):
        """Call each bar to decrement cooldown."""
        if self.cooldown_remaining > 0:
            self.cooldown_remaining -= 1

    def can_trade(self) -> tuple[bool, str]:
        """Check if we're allowed to trade."""
        if self.cooldown_remaining > 0:
            return False, f"streak_cooldown ({self.cooldown_remaining} bars left)"

        if self.consec_losses >= self.reduce_after:
            return True, f"loss_streak ({self.consec_losses} losses, reduced size)"

        return True, "ok"

    def get_size_multiplier(self) -> float:
        """Returns size multiplier based on streak."""
        if self.cooldown_remaining > 0:
            return 0.0

        if self.consec_losses >= self.reduce_after:
            # Reduce by 25% per additional loss beyond reduce_after
            excess = self.consec_losses - self.reduce_after
            return max(0.25, 1.0 - excess * 0.25)

        return 1.0

    def get_status(self) -> dict:
        return {
            "consec_losses": self.consec_losses,
            "consec_wins": self.consec_wins,
            "cooldown": self.cooldown_remaining,
            "size_mult": self.get_size_multiplier(),
            "recent_wr": sum(self.history[-20:]) / max(len(self.history[-20:]), 1) * 100,
        }


# ---------------------------------------------------------------------------
# Combined Exit Engine
# ---------------------------------------------------------------------------
@dataclass
class ExitDecision:
    should_exit: bool
    reason: str
    exit_type: str  # "tp", "sl", "trailing", "adaptive", "streak", "max_hold", "none"


class ExitEngine:
    """
    Combines all exit logic into one unified engine.

    Priority order:
      1. Hard stop loss (always respected)
      2. Trailing stop (once activated)
      3. Adaptive TP (ATR-based)
      4. Max hold period
      5. Signal reversal
    """

    def __init__(self):
        self.trailing = TrailingStop()
        self.adaptive = AdaptiveTPSL()
        self.dd_sizer = DrawdownSizer()
        self.streak = StreakDetector()
        self._entry_price = 0.0
        self._entry_bar = 0

    def start_trade(self, entry_price: float, entry_bar: int):
        """Initialize for new trade."""
        self._entry_price = entry_price
        self._entry_bar = entry_bar
        self.trailing.start(entry_price)

    def update(self, closes: np.ndarray, current_bar: int):
        """Update ATR and streak cooldown each bar."""
        self.adaptive.update_atr(closes)
        self.streak.tick()

    def check_exit(
        self,
        current_price: float,
        position: int,
        current_bar: int,
        max_hold: int = 60,
        signal_mean: float = 0.0,
    ) -> ExitDecision:
        """
        Check all exit conditions. Returns the first one that triggers.
        """
        bars_held = current_bar - self._entry_bar

        # 1. Hard stop loss (always check first)
        pnl_pct = (current_price - self._entry_price) / self._entry_price * position
        if pnl_pct <= -self.adaptive.get_sl():
            return ExitDecision(True, f"hard_sl ({pnl_pct*100:+.3f}%)", "sl")

        # 2. Trailing stop
        trailing_hit, trailing_reason = self.trailing.check(current_price, position, self._entry_price)
        if trailing_hit:
            return ExitDecision(True, trailing_reason, "trailing")

        # 3. Adaptive TP
        tp_hit, tp_reason = self.adaptive.check_exit(self._entry_price, current_price, position)
        if tp_hit:
            return ExitDecision(True, tp_reason, "tp")

        # 4. Max hold
        if bars_held >= max_hold:
            return ExitDecision(True, f"max_hold ({bars_held} bars)", "max_hold")

        # 5. Signal reversal
        if position == 1 and signal_mean < -0.001:
            return ExitDecision(True, f"signal_reverse (mean={signal_mean:+.6f})", "signal_reverse")
        if position == -1 and signal_mean > 0.001:
            return ExitDecision(True, f"signal_reverse (mean={signal_mean:+.6f})", "signal_reverse")

        return ExitDecision(False, "", "none")

    def get_position_size(self, kelly_base: float, current_equity: float, peak_equity: float) -> float:
        """
        Compute final position size combining:
          1. Kelly base size
          2. Drawdown reduction
          3. Streak reduction
        """
        # DD multiplier
        dd_mult = self.dd_sizer.compute_multiplier(current_equity, peak_equity)

        # Streak multiplier
        streak_mult = self.streak.get_size_multiplier()

        # Combined
        final_size = kelly_base * dd_mult * streak_mult
        return max(0.0, min(0.50, final_size))  # Cap at 50%

    def record_trade(self, win: bool):
        """Record completed trade for streak tracking."""
        self.streak.record_trade(win)
