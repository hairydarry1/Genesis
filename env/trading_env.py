"""
env/trading_env.py — Training environment for the trading policy.

Gym-style interface wrapping historical data + world model.
Applies realistic constraints: spread, slippage, position limits.

Usage:
    env = TradingEnv(dataset_path="data/dataset_R75_h10.npz")
    obs = env.reset()
    for _ in range(1000):
        action = policy(obs)  # action: 0=hold, 1=buy, 2=sell
        obs, reward, done, info = env.step(action)
        if done:
            break
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from model.reward import RewardConfig, RewardFunction


@dataclass
class TradingEnvConfig:
    """Configuration for the trading environment."""

    # --- Action space ---
    # 0 = hold/flat, 1 = buy (long), 2 = sell (short)
    n_actions: int = 3

    # --- Position limits ---
    max_position: int = 1  # max contracts/shares
    allow_short: bool = True

    # --- Transaction costs ---
    spread_cost: float = 0.0001   # fraction of price
    slippage_cost: float = 0.00005

    # --- Starting conditions ---
    initial_equity: float = 10000.0
    position_size: float = 1.0  # fixed position size (fraction of equity)

    # --- Episode length ---
    max_steps: int = 1000  # per episode (subsample from full dataset)

    # --- World model context ---
    # How many past observations to include in the state (for the policy)
    history_len: int = 10  # last N observations

    # --- Reward ---
    reward_config: RewardConfig | None = None


class TradingEnv:
    """
    Simple trading environment for policy training.

    State: [features (8), position (1), equity_norm (1), rolling_vol (1)]
    Action: 0=hold, 1=buy, 2=sell
    Reward: from RewardFunction (risk-adjusted, drawdown-penalized)
    """

    def __init__(
        self,
        dataset_path: str,
        tiny_features: list[str] | None = None,
        config: TradingEnvConfig | None = None,
    ):
        self.cfg = config or TradingEnvConfig()

        # Load dataset
        data = np.load(dataset_path, allow_pickle=True)
        self.states = data["states"]  # (N, n_features)
        self.targets = data["targets"]  # (N, horizon)
        self.timestamps = data["timestamps"]
        self.feature_names = list(data["feature_names"])

        self.n_samples = len(self.states)
        self.n_features = self.states.shape[1]
        self.horizon = self.targets.shape[1]

        # Filter to tiny features if specified
        if tiny_features is not None:
            name_to_idx = {name: i for i, name in enumerate(self.feature_names)}
            self.tiny_indices = [name_to_idx[f] for f in tiny_features if f in name_to_idx]
            self.tiny_feature_names = [f for f in tiny_features if f in name_to_idx]
        else:
            self.tiny_indices = list(range(self.n_features))
            self.tiny_feature_names = self.feature_names

        self.tiny_n_features = len(self.tiny_indices)

        # Reward function
        self.reward_fn = RewardFunction(self.cfg.reward_config or RewardConfig())

        # State dimensions
        # obs = [features, position (1), equity_norm (1), rolling_vol (1)]
        self.obs_dim = self.tiny_n_features + 3

        # Episode state
        self._episode_start = 0
        self._step = 0
        self._position = 0  # +1 long, -1 short, 0 flat
        self._entry_price = 0.0
        self._equity = self.cfg.initial_equity
        self._peak_equity = self.cfg.initial_equity
        self._total_pnl = 0.0
        self._trade_returns: list[float] = []

        # Precompute returns for PnL calculation
        self._returns = np.zeros(self.n_samples, dtype=np.float64)
        for i in range(1, self.n_samples):
            # Use first-step target as return proxy
            self._returns[i] = self.targets[i, 0] if i < self.n_samples else 0.0

        # Rolling volatility (simple: std of last 100 returns)
        self._rolling_vol = np.full(self.n_samples, 0.002, dtype=np.float64)
        window = 100
        for i in range(window, self.n_samples):
            self._rolling_vol[i] = np.std(self._returns[i - window:i]) + 1e-10

        # Direction accuracy tracking
        self._direction_correct: list[bool] = []
        self._confidences: list[float] = []

    def reset(
        self,
        start_idx: int | None = None,
        seed: int | None = None,
    ) -> np.ndarray:
        """Reset the environment to a random starting point."""
        if seed is not None:
            np.random.seed(seed)

        # Random start position (leave room for max_steps)
        max_start = self.n_samples - self.cfg.max_steps - self.horizon - 1
        if max_start <= 0:
            max_start = 0

        if start_idx is not None:
            self._episode_start = min(start_idx, max_start)
        else:
            self._episode_start = np.random.randint(0, max(1, max_start))

        self._step = 0
        self._position = 0
        self._entry_price = 0.0
        self._equity = self.cfg.initial_equity
        self._peak_equity = self.cfg.initial_equity
        self._total_pnl = 0.0
        self._trade_returns = []

        self.reward_fn.reset()

        return self._get_obs()

    def step(self, action: int) -> tuple[np.ndarray, float, bool, dict[str, Any]]:
        """
        Take one step in the environment.

        Parameters
        ----------
        action : int
            0 = hold/flat, 1 = buy (long), 2 = sell (short)

        Returns
        -------
        obs : np.ndarray
            Next observation.
        reward : float
            Reward for this step.
        done : bool
            Whether the episode is over.
        info : dict
            Additional information.
        """
        idx = self._episode_start + self._step
        if idx >= self.n_samples - 1:
            return self._get_obs(), 0.0, True, {"reason": "end_of_data"}

        current_price = 1.0  # normalized price (we use returns, not absolute price)
        next_return = self._returns[idx + 1]  # return in next bar
        rolling_vol = self._rolling_vol[idx]

        # Map action to position
        new_position = self._action_to_position(action)

        # Check position limits
        if not self.cfg.allow_short and new_position < 0:
            new_position = 0

        # --- Transaction costs ---
        trade_cost = 0.0
        if new_position != self._position:
            trade_cost = (self.cfg.spread_cost + self.cfg.slippage_cost) * abs(new_position - self._position)

        # --- PnL calculation ---
        realized_pnl = 0.0
        unrealized_pnl = 0.0

        if self._position != 0 and new_position != self._position:
            # Closing or flipping position
            trade_return = next_return * self._position
            realized_pnl = trade_return * self.cfg.position_size * self._equity
            self._trade_returns.append(trade_return)
        elif new_position != 0:
            # Holding or opening position
            unrealized_pnl = next_return * new_position * self.cfg.position_size * self._equity

        # Update equity
        self._equity += realized_pnl + unrealized_pnl - trade_cost * self._equity
        self._peak_equity = max(self._peak_equity, self._equity)
        self._total_pnl += realized_pnl

        # Direction prediction (use first-step target sign)
        correct = None
        if self._position != 0 or new_position != 0:
            predicted_direction = new_position if new_position != 0 else self._position
            actual_direction = np.sign(next_return)
            correct = (predicted_direction == actual_direction) if actual_direction != 0 else None

        # Confidence (use inverse of rolling vol as proxy — low vol = high confidence)
        vol_ratio = rolling_vol / 0.002  # normalize to typical vol
        confidence = max(0.1, min(0.9, 1.0 / vol_ratio))

        # --- Reward ---
        reward, reward_details = self.reward_fn.step(
            position=new_position,
            prev_position=self._position,
            entry_price=self._entry_price,
            current_price=current_price,
            confidence=confidence,
            correct=correct,
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            peak_equity=self._peak_equity,
            current_equity=self._equity,
            rolling_vol=rolling_vol,
            step=self._step,
        )

        # Update position
        if new_position != self._position:
            self._entry_price = current_price
        self._position = new_position

        # Advance step
        self._step += 1
        done = self._step >= self.cfg.max_steps

        # Info
        info = {
            "equity": self._equity,
            "peak_equity": self._peak_equity,
            "position": self._position,
            "total_pnl": self._total_pnl,
            "trade_count": self.reward_fn._trade_count,
            "drawdown_pct": (self._peak_equity - self._equity) / self._peak_equity if self._peak_equity > 0 else 0,
            "rolling_vol": rolling_vol,
            **reward_details,
        }

        return self._get_obs(), reward, done, info

    def _get_obs(self) -> np.ndarray:
        """Build the observation vector."""
        idx = self._episode_start + self._step
        idx = min(idx, self.n_samples - 1)

        # Features (tiny subset)
        features = self.states[idx, self.tiny_indices]

        # Position encoding
        pos = np.array([self._position], dtype=np.float32)

        # Equity normalized
        eq_norm = np.array([self._equity / self.cfg.initial_equity], dtype=np.float32)

        # Rolling vol
        vol = np.array([self._rolling_vol[idx]], dtype=np.float32)

        obs = np.concatenate([features, pos, eq_norm, vol]).astype(np.float32)
        return obs

    def _action_to_position(self, action: int) -> int:
        """Map action index to position."""
        if action == 0:
            return 0  # hold flat
        elif action == 1:
            return 1  # buy/long
        elif action == 2:
            return -1 if self.cfg.allow_short else 0  # sell/short
        else:
            return 0  # default to flat

    def get_random_baseline_stats(self, n_episodes: int = 100) -> dict:
        """Evaluate a random policy for baseline comparison."""
        returns = []
        for _ in range(n_episodes):
            obs = self.reset()
            episode_reward = 0.0
            done = False
            while not done:
                action = np.random.randint(0, self.cfg.n_actions)
                obs, reward, done, info = self.step(action)
                episode_reward += reward
            returns.append(episode_reward)

        return {
            "mean_reward": float(np.mean(returns)),
            "std_reward": float(np.std(returns)),
            "min_reward": float(np.min(returns)),
            "max_reward": float(np.max(returns)),
        }

    def get_buy_and_hold_stats(self, n_episodes: int = 100) -> dict:
        """Evaluate a buy-and-hold policy for baseline comparison."""
        returns = []
        for _ in range(n_episodes):
            obs = self.reset()
            episode_reward = 0.0
            done = False
            step = 0
            while not done:
                action = 1 if step == 0 else 0  # buy at start, hold
                obs, reward, done, info = self.step(action)
                episode_reward += reward
                step += 1
            returns.append(episode_reward)

        return {
            "mean_reward": float(np.mean(returns)),
            "std_reward": float(np.std(returns)),
            "min_reward": float(np.min(returns)),
            "max_reward": float(np.max(returns)),
        }

    def close(self):
        """Clean up."""
        pass


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys

    dataset = sys.argv[1] if len(sys.argv) > 1 else "data/dataset_R75_h10.npz"

    print(f"Loading {dataset}...")
    env = TradingEnv(dataset_path=dataset)
    print(f"  Samples: {env.n_samples}")
    print(f"  Features: {env.n_features} (tiny: {env.tiny_n_features})")
    print(f"  Obs dim: {env.obs_dim}")
    print(f"  Action space: {env.cfg.n_actions}")

    # Test reset + step
    obs = env.reset(seed=42)
    print(f"\n  Obs shape: {obs.shape}")
    print(f"  Obs sample: {obs[:5]}...")

    total_reward = 0.0
    for i in range(100):
        action = np.random.randint(0, env.cfg.n_actions)
        obs, reward, done, info = env.step(action)
        total_reward += reward
        if done:
            break

    print(f"\n  100 random steps: total_reward={total_reward:.4f}")
    print(f"  Final equity: {info['equity']:.2f}")
    print(f"  Position: {info['position']}")
    print(f"  Trades: {info['trade_count']}")

    # Baselines
    print("\n  Computing baselines (10 episodes each)...")
    random_stats = env.get_random_baseline_stats(n_episodes=10)
    bh_stats = env.get_buy_and_hold_stats(n_episodes=10)

    print(f"  Random policy:  mean_reward={random_stats['mean_reward']:.4f} +/- {random_stats['std_reward']:.4f}")
    print(f"  Buy-and-hold:   mean_reward={bh_stats['mean_reward']:.4f} +/- {bh_stats['std_reward']:.4f}")

    env.close()
    print("\n[OK] Environment test complete!")
