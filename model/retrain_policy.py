"""
model/retrain_policy.py — Retrain PPO policies on clean data.

Retrains the trading policy for all 4 indices using:
  - Clean datasets (no lookahead bias)
  - Proper variance initialization
  - Walk-forward validation
  - Transaction cost simulation

Usage:
    python -m model.retrain_policy
    python -m model.retrain_policy --indices R_75 CRASH500
    python -m model.retrain_policy --iterations 200
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)

# Index configurations
INDEX_CONFIGS = {
    "R_75": {
        "dataset": "data/dataset_R75_h10.npz",
        "output_dir": "checkpoints_tiny_clean",
        "symbol": "R_75",
    },
    "R_10": {
        "dataset": "data/dataset_volatility_10_h10.npz",
        "output_dir": "checkpoints_vol10_clean",
        "symbol": "R_10",
    },
    "BOOM500": {
        "dataset": "data/dataset_boom_500_h10.npz",
        "output_dir": "checkpoints_boom_500_clean",
        "symbol": "BOOM500",
    },
    "CRASH500": {
        "dataset": "data/dataset_crash_500_h10.npz",
        "output_dir": "checkpoints_crash_500_clean",
        "symbol": "CRASH500",
    },
}


def train_single_index(
    index_name: str,
    config: dict,
    n_iterations: int = 150,
    rollout_steps: int = 300,
    n_folds: int = 4,
) -> dict:
    """Train policy for a single index with walk-forward validation."""
    from env.trading_env import TradingEnv, TradingEnvConfig
    from model.policy import train_policy, PolicyConfig
    from model.train_tiny import TINY_FEATURES

    dataset_path = config["dataset"]
    output_dir = Path(config["output_dir"])

    if not Path(dataset_path).exists():
        logger.error("[%s] Dataset not found: %s", index_name, dataset_path)
        return {"error": "dataset not found"}

    logger.info("=" * 60)
    logger.info("TRAINING POLICY: %s", index_name)
    logger.info("=" * 60)

    # Create environment
    env = TradingEnv(
        dataset_path,
        tiny_features=TINY_FEATURES,
        config=TradingEnvConfig(
            max_steps=500,
            initial_equity=10000,
            spread_cost=0.0001,
            slippage_cost=0.00005,
        ),
    )

    logger.info("Dataset: %s", dataset_path)
    logger.info("Samples: %d", env.n_samples)
    logger.info("Features: %d (+ 3 obs)", env.obs_dim - 3)
    logger.info("Actions: %d (hold/buy/sell)", env.cfg.n_actions)

    # Walk-forward training
    fold_size = env.n_samples // n_folds
    fold_results = []

    for fold in range(n_folds):
        logger.info("\n--- Fold %d/%d ---", fold + 1, n_folds)

        # Train on fold
        start_idx = fold * fold_size
        result = train_policy(
            env,
            n_iterations=n_iterations,
            rollout_steps=rollout_steps,
            n_epochs=4,
            batch_size=64,
            lr=3e-4,
            start_idx=start_idx,
        )

        # Evaluate on validation set (next fold)
        val_start = (fold + 1) * fold_size if fold + 1 < n_folds else 0
        val_end = val_start + fold_size if fold + 1 < n_folds else env.n_samples

        policy = result["policy"]
        policy.eval()

        # Run validation
        obs = env.reset(start_idx=val_start)
        total_reward = 0
        trades = 0
        equity_curve = [env.cfg.initial_equity]
        peak = env.cfg.initial_equity

        for step in range(min(500, val_end - val_start)):
            import torch
            import torch.nn.functional as F
            obs_tensor = torch.tensor(obs, dtype=torch.float32).unsqueeze(0)
            with torch.no_grad():
                logits, _, _ = policy(obs_tensor)
                action = int(F.softmax(logits, dim=-1).argmax().item())
            obs, reward, done, info = env.step(action)
            total_reward += reward
            eq = info.get("equity", env.cfg.initial_equity)
            equity_curve.append(eq)
            peak = max(peak, eq)
            if info.get("trade_count", 0) > trades:
                trades = info.get("trade_count", 0)
            if done:
                break

        final_eq = equity_curve[-1]
        total_return = (final_eq / env.cfg.initial_equity - 1) * 100
        max_dd = (peak - min(equity_curve)) / peak if peak > 0 else 0
        returns = np.diff(equity_curve) / np.array(equity_curve[:-1])
        sharpe = float(np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252 * 24 * 60)) if len(returns) > 10 else 0

        fold_results.append({
            "fold": fold,
            "total_reward": total_reward,
            "total_return": total_return,
            "max_drawdown": max_dd,
            "sharpe": sharpe,
            "trades": trades,
        })

        logger.info(
            "  Fold %d: return=%.1f%% sharpe=%.2f maxdd=%.1f%% trades=%d",
            fold, total_return, sharpe, max_dd * 100, trades,
        )

    # Train final model on all data
    logger.info("\n--- Final Training (all data) ---")
    final_result = train_policy(
        env,
        n_iterations=n_iterations,
        rollout_steps=rollout_steps,
        n_epochs=4,
        batch_size=64,
        lr=3e-4,
    )

    # Save
    output_dir.mkdir(parents=True, exist_ok=True)
    save_path = output_dir / "policy_best.pt"

    torch.save({
        "policy_state_dict": final_result["policy"].state_dict(),
        "config": {
            "obs_dim": env.obs_dim,
            "n_actions": env.cfg.n_actions,
        },
        "fold_results": fold_results,
        "n_iterations": n_iterations,
        "rollout_steps": rollout_steps,
    }, save_path)

    logger.info("Saved policy to %s", save_path)

    # Summary
    avg_return = np.mean([f["total_return"] for f in fold_results])
    avg_sharpe = np.mean([f["sharpe"] for f in fold_results])
    avg_dd = np.mean([f["max_drawdown"] for f in fold_results])
    positive_folds = sum(1 for f in fold_results if f["total_return"] > 0)

    summary = {
        "index": index_name,
        "dataset": dataset_path,
        "n_folds": n_folds,
        "avg_return": avg_return,
        "avg_sharpe": avg_sharpe,
        "avg_max_dd": avg_dd,
        "positive_folds": positive_folds,
        "fold_results": fold_results,
        "checkpoint": str(save_path),
    }

    logger.info("\n%s SUMMARY", index_name)
    logger.info("  Avg Return: %.1f%%", avg_return)
    logger.info("  Avg Sharpe: %.2f", avg_sharpe)
    logger.info("  Avg Max DD: %.1f%%", avg_dd * 100)
    logger.info("  Positive folds: %d/%d", positive_folds, n_folds)

    return summary


def main():
    parser = argparse.ArgumentParser(description="Retrain PPO policies on clean data")
    parser.add_argument("--indices", nargs="+", default=list(INDEX_CONFIGS.keys()),
                        help="Indices to train")
    parser.add_argument("--iterations", type=int, default=150,
                        help="PPO iterations per fold")
    parser.add_argument("--rollout", type=int, default=300,
                        help="Rollout steps per iteration")
    parser.add_argument("--folds", type=int, default=4,
                        help="Walk-forward folds")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    all_results = {}
    for index_name in args.indices:
        if index_name not in INDEX_CONFIGS:
            logger.warning("Unknown index: %s", index_name)
            continue

        config = INDEX_CONFIGS[index_name]
        result = train_single_index(
            index_name,
            config,
            n_iterations=args.iterations,
            rollout_steps=args.rollout,
            n_folds=args.folds,
        )
        all_results[index_name] = result

    # Save summary
    summary_path = Path("checkpoints/policy_retrain_summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with open(summary_path, "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    # Print comparison
    print("\n" + "=" * 80)
    print("POLICY RETRAIN RESULTS")
    print("=" * 80)
    print(f"{'Index':<12} {'Return':>8} {'Sharpe':>8} {'MaxDD':>8} {'Folds':>6}")
    print("-" * 80)
    for name, result in all_results.items():
        if "error" in result:
            print(f"{name:<12} ERROR: {result['error']}")
        else:
            print(f"{name:<12} {result['avg_return']:>+7.1f}% {result['avg_sharpe']:>8.2f} {result['avg_max_dd']*100:>7.1f}% {result['positive_folds']}/{result['n_folds']}")
    print("=" * 80)


if __name__ == "__main__":
    main()
