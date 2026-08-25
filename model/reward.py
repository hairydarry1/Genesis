"""
model/reward.py — Layered reward function for the trading policy.

Components:
  1. Risk-adjusted return: PnL scaled by rolling realized volatility
  2. Drawdown penalty: scales with depth of current drawdown
  3. Calibration penalty: penalizes gap between stated confidence and empirical accuracy
  4. Transaction cost: spread + slippage at every trade

All weights are configurable hyperparameters.

Reward is computed per-timestep (dense), with realized PnL dominant at trade close.

Usage:
    from model.reward import RewardConfig, RewardFunction

    cfg = RewardConfig()
    reward_fn = RewardFunction(cfg)
    reward, details = reward_fn.step(
        position=1,           # +1 long, -1 short, 0 flat
        prev_position=0,      # previous position
        entry_price=100.0,    # price at trade entry (0 if flat)
        current_price=100.5,  # current mid price
        confidence=0.7,       # model's stated confidence [0, 1]
        correct=True,         # was the direction prediction correct?
        realized_pnl=0.5,     # realized PnL (only on trade close)
        unrealized_pnl=0.5,   # unrealized PnL (mark-to-market)
        peak_equity=10000.0,  # running peak equity
        current_equity=10050.0,# current equity
        rolling_vol=0.002,    # rolling realized volatility
        step=100,             # current timestep
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class RewardConfig:
    """Configuration for the layered reward function."""

    # --- Risk-adjusted return ---
    # PnL is divided by rolling_vol, then log-scaled to prevent extreme values
    # from dominating. log(1 + |PnL/vol|) * sign(PnL)
    risk_adjust_weight: float = 1.0

    # --- Drawdown penalty ---
    # Scales quadratically with drawdown depth: penalty = -dd_weight * (dd%)^2
    drawdown_weight: float = 50.0
    # Maximum drawdown before penalty becomes extreme
    max_drawdown_pct: float = 0.20  # 20%

    # --- Calibration penalty ---
    # Penalizes |confidence - empirical_accuracy| over a rolling window
    calibration_weight: float = 0.5
    calibration_window: int = 100  # timesteps for rolling accuracy estimate

    # --- Transaction costs ---
    # Applied at every trade (position change)
    spread_cost: float = 0.0001   # 1 pip spread (as fraction of price)
    slippage_cost: float = 0.00005  # 0.5 pip slippage
    # Total cost per trade = (spread + slippage) * position_size * price

    # --- Position holding cost ---
    # Small per-timestep cost for holding a position (encourages efficiency)
    holding_cost_per_step: float = 0.0  # disabled by default

    # --- Action penalties ---
    # Penalty for excessive trading (churning)
    churn_penalty: float = 0.0  # disabled by default

    def __post_init__(self):
        self.total_trade_cost = self.spread_cost + self.slippage_cost


class RewardFunction:
    """
    Computes the layered reward at each timestep.

    Call .step() at each timestep with the current state.
    Call .reset() at the start of each episode.
    """

    def __init__(self, config: RewardConfig | None = None):
        self.cfg = config or RewardConfig()
        self._reset_state()

    def _reset_state(self):
        """Reset internal state for a new episode."""
        self._peak_equity = 0.0
        self._trade_count = 0
        self._correct_predictions: list[bool] = []
        self._confidences: list[float] = []
        self._prev_position = 0
        self._step_count = 0

    def reset(self):
        """Reset for a new episode."""
        self._reset_state()

    def step(
        self,
        position: int,
        prev_position: int,
        entry_price: float,
        current_price: float,
        confidence: float,
        correct: bool | None,
        realized_pnl: float,
        unrealized_pnl: float,
        peak_equity: float,
        current_equity: float,
        rolling_vol: float,
        step: int,
    ) -> tuple[float, dict[str, float]]:
        """
        Compute the reward for one timestep.

        Parameters
        ----------
        position : int
            Current position: +1 (long), -1 (short), 0 (flat).
        prev_position : int
            Previous timestep's position.
        entry_price : float
            Price at which the current position was entered (0 if flat).
        current_price : float
            Current mid price.
        confidence : float
            Model's stated confidence in its prediction [0, 1].
        correct : bool or None
            Whether the direction prediction was correct (None if unknown).
        realized_pnl : float
            PnL realized from closing a trade (0 if no trade closed).
        unrealized_pnl : float
            Mark-to-market unrealized PnL.
        peak_equity : float
            Running peak equity for drawdown calculation.
        current_equity : float
            Current portfolio equity.
        rolling_vol : float
            Rolling realized volatility (annualized or per-step).
        step : int
            Current timestep in the episode.

        Returns
        -------
        reward : float
            Total layered reward.
        details : dict
            Breakdown of each reward component.
        """
        self._step_count = step
        details: dict[str, float] = {}

        # --- 1. Risk-adjusted return ---
        # Use realized PnL if a trade closed, otherwise unrealized
        effective_pnl = realized_pnl if abs(realized_pnl) > 1e-10 else unrealized_pnl

        if rolling_vol > 1e-10:
            raw_ratio = effective_pnl / rolling_vol
            # Log scale to compress extreme values and prevent domination
            # log(1 + |x|) * sign(x) keeps sign, compresses magnitude
            risk_adj_return = np.sign(raw_ratio) * np.log1p(abs(raw_ratio))
        else:
            risk_adj_return = 0.0

        reward_risk_adj = self.cfg.risk_adjust_weight * risk_adj_return
        details["risk_adjusted_return"] = reward_risk_adj

        # --- 2. Drawdown penalty ---
        if peak_equity > 0:
            drawdown_pct = max(0, (peak_equity - current_equity) / peak_equity)
        else:
            drawdown_pct = 0.0

        # Quadratic penalty that steepens as drawdown approaches max
        if drawdown_pct < self.cfg.max_drawdown_pct:
            dd_penalty = -self.cfg.drawdown_weight * (drawdown_pct ** 2)
        else:
            # Extreme penalty beyond max drawdown
            dd_penalty = -self.cfg.drawdown_weight * (self.cfg.max_drawdown_pct ** 2) * 10

        details["drawdown_penalty"] = dd_penalty
        details["drawdown_pct"] = drawdown_pct

        # --- 3. Calibration penalty ---
        calibration_penalty = 0.0
        if correct is not None:
            self._correct_predictions.append(correct)
            self._confidences.append(confidence)

            # Keep only the last N predictions
            window = self.cfg.calibration_window
            if len(self._correct_predictions) > window:
                self._correct_predictions = self._correct_predictions[-window:]
                self._confidences = self._confidences[-window:]

            if len(self._correct_predictions) >= 10:
                avg_confidence = np.mean(self._confidences)
                empirical_accuracy = np.mean(self._correct_predictions)
                calibration_gap = abs(avg_confidence - empirical_accuracy)
                calibration_penalty = -self.cfg.calibration_weight * calibration_gap

        details["calibration_penalty"] = calibration_penalty

        # --- 4. Transaction costs ---
        trade_cost = 0.0
        if position != prev_position:
            # Position changed = a trade occurred
            trade_cost = -self.cfg.total_trade_cost * abs(current_price) * abs(position - prev_position)
            self._trade_count += 1

        details["transaction_cost"] = trade_cost

        # --- 5. Holding cost (optional) ---
        holding_cost = 0.0
        if position != 0:
            holding_cost = -self.cfg.holding_cost_per_step

        details["holding_cost"] = holding_cost

        # --- 6. Churn penalty (optional) ---
        churn_penalty = 0.0
        if self._step_count > 0 and position != prev_position:
            churn_penalty = -self.cfg.churn_penalty

        details["churn_penalty"] = churn_penalty

        # --- Total reward ---
        total = (
            reward_risk_adj
            + dd_penalty
            + calibration_penalty
            + trade_cost
            + holding_cost
            + churn_penalty
        )

        details["total_reward"] = total
        details["peak_equity"] = peak_equity
        details["current_equity"] = current_equity
        details["trade_count"] = self._trade_count

        self._prev_position = position

        return total, details

    def rank_trades(self, trades: list[dict]) -> list[dict]:
        """
        Rank a list of completed trades by the reward function.

        Each trade dict should have:
            - pnl: float (realized PnL)
            - max_drawdown_pct: float (peak-to-trough during trade)
            - duration_steps: int
            - confidence: float
            - correct: bool
            - volatility: float (avg rolling vol during trade)

        Returns trades sorted by reward (best first).
        """
        scored = []
        for trade in trades:
            # Risk-adjusted return (log-scaled, matching step())
            vol = max(trade.get("volatility", 0.001), 1e-10)
            raw_ratio = trade["pnl"] / vol
            risk_adj = np.sign(raw_ratio) * np.log1p(abs(raw_ratio))

            # Drawdown penalty
            dd = trade.get("max_drawdown_pct", 0.0)
            dd_pen = -self.cfg.drawdown_weight * (dd ** 2)

            # Calibration
            cal_pen = 0.0
            if "confidence" in trade and "correct" in trade:
                cal_pen = -self.cfg.calibration_weight * abs(trade["confidence"] - float(trade["correct"]))

            # Transaction cost (entry + exit = 2 trades)
            tc = -2 * self.cfg.total_trade_cost

            total = risk_adj + dd_pen + cal_pen + tc
            scored.append({**trade, "reward_score": total})

        return sorted(scored, key=lambda x: x["reward_score"], reverse=True)


# ---------------------------------------------------------------------------
# Sanity check: hand-constructed trade sequences
# ---------------------------------------------------------------------------
def _sanity_check():
    """
    Verify the reward function ranks trades correctly.

    Expected ranking (best to worst):
      1. Clean winner: modest profit, low drawdown, high confidence
      2. Disciplined small loss: small loss, low drawdown, honest low confidence
      3. Big risky win: large profit but high drawdown, low confidence
      4. Slow bleed: small losses accumulating, moderate drawdown
      5. Overconfident loss: loss with high confidence (calibration penalty)
    """
    cfg = RewardConfig()
    rf = RewardFunction(cfg)

    trades = [
        {
            "name": "Clean winner",
            "pnl": 0.5,
            "max_drawdown_pct": 0.02,
            "duration_steps": 50,
            "confidence": 0.8,
            "correct": True,
            "volatility": 0.002,
        },
        {
            "name": "Disciplined small loss",
            "pnl": -0.1,
            "max_drawdown_pct": 0.01,
            "duration_steps": 20,
            "confidence": 0.3,
            "correct": False,
            "volatility": 0.002,
        },
        {
            "name": "Big risky win",
            "pnl": 1.0,
            "max_drawdown_pct": 0.15,
            "duration_steps": 100,
            "confidence": 0.6,
            "correct": True,
            "volatility": 0.002,
        },
        {
            "name": "Slow bleed",
            "pnl": -0.3,
            "max_drawdown_pct": 0.08,
            "duration_steps": 80,
            "confidence": 0.5,
            "correct": False,
            "volatility": 0.002,
        },
        {
            "name": "Overconfident loss",
            "pnl": -0.2,
            "max_drawdown_pct": 0.05,
            "duration_steps": 40,
            "confidence": 0.9,
            "correct": False,
            "volatility": 0.002,
        },
    ]

    ranked = rf.rank_trades(trades)

    print("\n" + "=" * 70)
    print("REWARD FUNCTION SANITY CHECK")
    print("=" * 70)
    print("\nExpected ranking (best -> worst):")
    print("  1. Clean winner (modest profit, low DD, high confidence)")
    print("  2. Disciplined small loss (honest about uncertainty)")
    print("  3. Big risky win (high profit but high DD)")
    print("  4. Slow bleed (accumulating losses)")
    print("  5. Overconfident loss (high confidence + wrong)")
    print()

    for i, trade in enumerate(ranked):
        print(f"  {i+1}. {trade['name']:25s}  score={trade['reward_score']:+.4f}  "
              f"pnl={trade['pnl']:+.2f}  dd={trade['max_drawdown_pct']:.1%}  "
              f"conf={trade['confidence']:.1f}")

    # Verify the clean winner is ranked first
    assert ranked[0]["name"] == "Clean winner", f"Expected 'Clean winner' first, got '{ranked[0]['name']}'"

    # Verify the overconfident loss is ranked last (or near last)
    bottom_names = [t["name"] for t in ranked[-2:]]
    assert "Overconfident loss" in bottom_names, f"Expected 'Overconfident loss' in bottom 2, got {bottom_names}"

    print("\n[PASS] Sanity check passed!")

    # Also test per-step reward
    print("\n" + "=" * 70)
    print("PER-STEP REWARD TEST")
    print("=" * 70)

    rf.reset()

    # Simulate a profitable long trade
    steps = [
        {"position": 1, "prev_position": 0, "entry_price": 100.0, "current_price": 100.1,
         "confidence": 0.7, "correct": True, "realized_pnl": 0.0, "unrealized_pnl": 0.1,
         "peak_equity": 10000.0, "current_equity": 10000.1, "rolling_vol": 0.002, "step": 1},
        {"position": 1, "prev_position": 1, "entry_price": 100.0, "current_price": 100.3,
         "confidence": 0.75, "correct": True, "realized_pnl": 0.0, "unrealized_pnl": 0.3,
         "peak_equity": 10000.3, "current_equity": 10000.3, "rolling_vol": 0.002, "step": 2},
        {"position": 0, "prev_position": 1, "entry_price": 100.0, "current_price": 100.5,
         "confidence": 0.8, "correct": True, "realized_pnl": 0.5, "unrealized_pnl": 0.0,
         "peak_equity": 10000.5, "current_equity": 10000.5, "rolling_vol": 0.002, "step": 3},
    ]

    total_reward = 0.0
    for i, s in enumerate(steps):
        r, d = rf.step(**s)
        total_reward += r
        print(f"  Step {i+1}: reward={r:+.4f}  "
              f"risk_adj={d['risk_adjusted_return']:+.4f}  "
              f"tx_cost={d['transaction_cost']:+.6f}  "
              f"equity={d['current_equity']:.2f}")

    print(f"\n  Total reward over 3 steps: {total_reward:+.4f}")
    print(f"  Trade count: {rf._trade_count}")

    print("\n[PASS] Per-step test complete!")


if __name__ == "__main__":
    _sanity_check()
