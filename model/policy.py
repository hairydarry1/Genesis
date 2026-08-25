"""
model/policy.py — Simple policy network for the trading agent.

Architecture (first version — keep simple):
    Input: observation (features + position + equity + vol)
    Encoder: 2-layer MLP (64 -> 64)
    Policy head: action logits (3: hold, buy, sell)
    Value head: expected return (scalar)
    Confidence head: calibration score (scalar, sigmoid-activated)

Training: PPO (Proximal Policy Optimization) — simple, stable, widely used.

Usage:
    policy = PolicyNetwork(obs_dim=11, n_actions=3)
    logits, value, confidence = policy(obs_tensor)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


@dataclass
class PolicyConfig:
    """Configuration for the policy network."""
    obs_dim: int = 11       # 8 features + position + equity_norm + rolling_vol
    n_actions: int = 3      # hold, buy, sell
    hidden_dim: int = 64
    n_layers: int = 2
    dropout: float = 0.1
    # PPO hyperparameters
    gamma: float = 0.99       # discount factor
    lam: float = 0.95         # GAE lambda
    clip_epsilon: float = 0.2 # PPO clip range
    value_loss_weight: float = 0.5
    confidence_loss_weight: float = 0.5
    entropy_weight: float = 0.02


class PolicyNetwork(nn.Module):
    """
    Actor-critic policy network with confidence head.

    Outputs:
        - action_logits: (batch, n_actions) — unnormalized log-probs
        - value: (batch, 1) — estimated state value
        - confidence: (batch, 1) — calibration score [0, 1]
    """

    def __init__(self, config: PolicyConfig | None = None):
        super().__init__()
        self.cfg = config or PolicyConfig()

        # Shared encoder
        layers = []
        in_dim = self.cfg.obs_dim
        for _ in range(self.cfg.n_layers):
            layers.extend([
                nn.Linear(in_dim, self.cfg.hidden_dim),
                nn.LayerNorm(self.cfg.hidden_dim),
                nn.ReLU(),
                nn.Dropout(self.cfg.dropout),
            ])
            in_dim = self.cfg.hidden_dim
        self.encoder = nn.Sequential(*layers)

        # Policy head (actor)
        self.policy_head = nn.Linear(self.cfg.hidden_dim, self.cfg.n_actions)

        # Value head (critic)
        self.value_head = nn.Linear(self.cfg.hidden_dim, 1)

        # Confidence head (calibration)
        self.confidence_head = nn.Sequential(
            nn.Linear(self.cfg.hidden_dim, 1),
            nn.Sigmoid(),
        )

    def forward(self, obs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor]:
        """
        Parameters
        ----------
        obs : (batch, obs_dim)

        Returns
        -------
        logits : (batch, n_actions)
        value : (batch, 1)
        confidence : (batch, 1) in [0, 1]
        """
        h = self.encoder(obs)
        logits = self.policy_head(h)
        value = self.value_head(h)
        confidence = self.confidence_head(h)
        return logits, value, confidence

    def get_action(self, obs: torch.Tensor, deterministic: bool = False) -> tuple[int, float, float, float]:
        """
        Get an action from the policy.

        Returns
        -------
        action : int
        log_prob : float
        value : float
        confidence : float
        """
        with torch.no_grad():
            obs = obs.unsqueeze(0) if obs.dim() == 1 else obs
            logits, value, confidence = self.forward(obs)
            probs = F.softmax(logits, dim=-1)

            if deterministic:
                action = torch.argmax(probs, dim=-1).item()
            else:
                dist = torch.distributions.Categorical(probs)
                action = dist.sample().item()

            log_prob = torch.log(probs[0, action]).item()
            value = value.item()
            confidence = confidence.item()

        return action, log_prob, value, confidence

    @property
    def param_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ---------------------------------------------------------------------------
# PPO Training
# ---------------------------------------------------------------------------
def compute_gae(
    rewards: list[float],
    values: list[float],
    dones: list[bool],
    gamma: float = 0.99,
    lam: float = 0.95,
) -> tuple[list[float], list[float]]:
    """Compute Generalized Advantage Estimation (GAE)."""
    n = len(rewards)
    advantages = [0.0] * n
    returns = [0.0] * n

    last_gae = 0.0
    for t in reversed(range(n)):
        if t == n - 1:
            next_value = 0.0
        else:
            next_value = values[t + 1]

        delta = rewards[t] + gamma * next_value * (1 - dones[t]) - values[t]
        last_gae = delta + gamma * lam * (1 - dones[t]) * last_gae
        advantages[t] = last_gae
        returns[t] = advantages[t] + values[t]

    return advantages, returns


def ppo_update(
    policy: PolicyNetwork,
    optimizer: torch.optim.Optimizer,
    obs: torch.Tensor,
    actions: torch.Tensor,
    old_log_probs: torch.Tensor,
    advantages: torch.Tensor,
    returns: torch.Tensor,
    confidences: torch.Tensor,
    corrects: torch.Tensor,
    cfg: PolicyConfig,
) -> dict[str, float]:
    """One PPO update step."""
    policy.train()

    logits, values, pred_conf = policy(obs)
    probs = F.softmax(logits, dim=-1)
    dist = torch.distributions.Categorical(probs)
    new_log_probs = dist.log_prob(actions)
    entropy = dist.entropy().mean()

    # PPO clip
    ratio = torch.exp(new_log_probs - old_log_probs)
    clipped = torch.clamp(ratio, 1 - cfg.clip_epsilon, 1 + cfg.clip_epsilon)
    policy_loss = -torch.min(ratio * advantages, clipped * advantages).mean()

    # Value loss
    value_loss = F.mse_loss(values.squeeze(), returns)

    # Confidence/calibration loss — per-sample BCE between confidence
    # and actual correctness. Teaches the confidence head to output
    # high values when the action is correct and low when wrong.
    confidence_loss = torch.tensor(0.0)
    if len(corrects) > 0:
        n = len(corrects)
        pc = pred_conf.squeeze(-1)[:n].clamp(0.05, 0.95)
        target = corrects.float()[:n]
        confidence_loss = F.binary_cross_entropy(pc, target)

    # Total loss
    loss = (
        policy_loss
        + cfg.value_loss_weight * value_loss
        + cfg.confidence_loss_weight * confidence_loss
        - cfg.entropy_weight * entropy
    )

    optimizer.zero_grad()
    loss.backward()
    torch.nn.utils.clip_grad_norm_(policy.parameters(), max_norm=0.5)
    optimizer.step()

    return {
        "policy_loss": policy_loss.item(),
        "value_loss": value_loss.item(),
        "confidence_loss": confidence_loss.item(),
        "entropy": entropy.item(),
        "total_loss": loss.item(),
    }


# ---------------------------------------------------------------------------
# Rollout collection
# ---------------------------------------------------------------------------
@torch.no_grad()
def collect_rollout(
    policy: PolicyNetwork,
    env,
    max_steps: int = 200,
    start_idx: int | None = None,
) -> dict[str, list]:
    """Collect a rollout from the environment."""
    policy.eval()

    obs_list = []
    actions_list = []
    rewards_list = []
    values_list = []
    log_probs_list = []
    dones_list = []
    confidences_list = []
    corrects_list = []

    obs = env.reset(start_idx=start_idx)
    total_reward = 0.0

    for _ in range(max_steps):
        obs_tensor = torch.tensor(obs, dtype=torch.float32)
        action, log_prob, value, confidence = policy.get_action(obs_tensor)

        next_obs, reward, done, info = env.step(action, confidence=confidence)

        obs_list.append(obs)
        actions_list.append(action)
        rewards_list.append(reward)
        values_list.append(value)
        log_probs_list.append(log_prob)
        dones_list.append(done)
        confidences_list.append(confidence)

        # Track direction correctness
        correct = info.get("correct")
        corrects_list.append(correct if correct is not None else False)

        total_reward += reward
        obs = next_obs

        if done:
            break

    return {
        "obs": torch.from_numpy(np.array(obs_list, dtype=np.float32)),
        "actions": torch.tensor(actions_list, dtype=torch.long),
        "rewards": rewards_list,
        "values": values_list,
        "log_probs": torch.tensor(log_probs_list, dtype=torch.float32),
        "dones": dones_list,
        "confidences": torch.tensor(confidences_list, dtype=torch.float32),
        "corrects": torch.tensor(corrects_list, dtype=torch.bool),
        "total_reward": total_reward,
        "equity": info.get("equity", env.cfg.initial_equity),
        "trade_count": info.get("trade_count", 0),
    }


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------
def train_policy(
    env,
    n_iterations: int = 100,
    rollout_steps: int = 200,
    n_epochs: int = 4,
    batch_size: int = 64,
    lr: float = 3e-4,
    device: str | None = None,
    start_idx: int | None = None,
) -> dict:
    """Train the policy using PPO."""
    device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))

    cfg = PolicyConfig(obs_dim=env.obs_dim, n_actions=env.cfg.n_actions)
    policy = PolicyNetwork(cfg).to(device)
    optimizer = torch.optim.Adam(policy.parameters(), lr=lr)

    print(f"Policy params: {policy.param_count}")
    print(f"Obs dim: {cfg.obs_dim}, Actions: {cfg.n_actions}")
    print(f"Training for {n_iterations} iterations, {rollout_steps} steps each")

    history = []

    for iteration in range(n_iterations):
        # Collect rollout
        rollout = collect_rollout(policy, env, max_steps=rollout_steps, start_idx=start_idx)

        # Compute GAE
        advantages, returns = compute_gae(
            rollout["rewards"],
            rollout["values"],
            rollout["dones"],
            gamma=cfg.gamma,
            lam=cfg.lam,
        )

        advantages = torch.tensor(advantages, dtype=torch.float32, device=device)
        returns = torch.tensor(returns, dtype=torch.float32, device=device)

        # Normalize advantages
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        # PPO update
        obs = rollout["obs"].to(device)
        actions = rollout["actions"].to(device)
        old_log_probs = rollout["log_probs"].to(device)
        confidences = rollout["confidences"].to(device)
        corrects = rollout["corrects"].to(device)

        epoch_losses = []
        for _ in range(n_epochs):
            # Mini-batch updates
            n_samples = len(obs)
            indices = torch.randperm(n_samples)
            for start in range(0, n_samples, batch_size):
                end = min(start + batch_size, n_samples)
                mb_idx = indices[start:end]

                loss_info = ppo_update(
                    policy, optimizer,
                    obs[mb_idx], actions[mb_idx], old_log_probs[mb_idx],
                    advantages[mb_idx], returns[mb_idx],
                    confidences[mb_idx], corrects[mb_idx],
                    cfg,
                )
                epoch_losses.append(loss_info)

        # Average losses
        avg_losses = {}
        for key in epoch_losses[0]:
            avg_losses[key] = np.mean([l[key] for l in epoch_losses])

        # Record
        record = {
            "iteration": iteration,
            "total_reward": rollout["total_reward"],
            "equity": rollout["equity"],
            "trade_count": rollout["trade_count"],
            **avg_losses,
        }
        history.append(record)

        if iteration % 10 == 0 or iteration == n_iterations - 1:
            print(
                f"  iter {iteration:3d}/{n_iterations} "
                f"reward={rollout['total_reward']:+.2f} "
                f"equity={rollout['equity']:.2f} "
                f"trades={rollout['trade_count']} "
                f"policy_loss={avg_losses['policy_loss']:.4f} "
                f"value_loss={avg_losses['value_loss']:.4f}"
            )

    return {
        "policy": policy,
        "config": cfg,
        "history": history,
    }


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from env.trading_env import TradingEnv, TradingEnvConfig

    dataset = sys.argv[1] if len(sys.argv) > 1 else "data/dataset_R75_h10.npz"

    print("Loading environment...")
    env = TradingEnv(
        dataset_path=dataset,
        config=TradingEnvConfig(max_steps=200),
    )

    print("\nTraining policy (10 iterations)...")
    result = train_policy(env, n_iterations=10, rollout_steps=200)

    print("\n[OK] Policy training test complete!")
    print(f"  Final equity: {result['history'][-1]['equity']:.2f}")
    print(f"  Final reward: {result['history'][-1]['total_reward']:.4f}")
