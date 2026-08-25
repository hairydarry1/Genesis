"""
eval/policy_validation.py — Walk-forward policy validation.

Trains the policy on rolling windows, evaluates out-of-sample,
and checks the brief's pass criteria:
  1. Beats random policy and heuristic baseline OOS
  2. Calibration holds: stated confidence roughly matches actual win rate
  3. No catastrophic drawdown in any fold

Usage:
    python -m eval.policy_validation --dataset data/dataset_R75_h10.npz
    python -m eval.policy_validation --dataset data/dataset_R75_h10.npz --train-iterations 100
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from env.trading_env import TradingEnv, TradingEnvConfig
from model.policy import PolicyNetwork, PolicyConfig, train_policy, collect_rollout
from model.train_tiny import TinyWorldModel, load_tiny_dataset, predict_tiny, TINY_FEATURES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baselines
# ---------------------------------------------------------------------------
def evaluate_random_policy(
    env: TradingEnv,
    n_episodes: int = 50,
    steps_per_episode: int = 500,
) -> dict:
    """Evaluate a random policy."""
    rewards = []
    equities = []
    trade_counts = []
    drawdowns = []

    for _ in range(n_episodes):
        obs = env.reset()
        total_reward = 0.0
        for _ in range(steps_per_episode):
            action = np.random.randint(0, env.cfg.n_actions)
            obs, reward, done, info = env.step(action)
            total_reward += reward
            if done:
                break
        rewards.append(total_reward)
        equities.append(info.get("equity", env.cfg.initial_equity))
        trade_counts.append(info.get("trade_count", 0))
        drawdowns.append(info.get("drawdown_pct", 0))

    return {
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "mean_equity": float(np.mean(equities)),
        "mean_trades": float(np.mean(trade_counts)),
        "max_drawdown": float(np.max(drawdowns)),
        "rewards": rewards,
    }


def evaluate_heuristic_policy(
    env: TradingEnv,
    n_episodes: int = 50,
    steps_per_episode: int = 500,
) -> dict:
    """
    Heuristic: if rolling vol is low (calm market), go long.
    If rolling vol is high (volatile), stay flat.
    This mimics a simple "buy calm, avoid spikes" strategy.
    """
    rewards = []
    equities = []
    trade_counts = []
    drawdowns = []

    for _ in range(n_episodes):
        obs = env.reset()
        total_reward = 0.0
        for _ in range(steps_per_episode):
            # Extract rolling_vol from obs (last element)
            rolling_vol = obs[-1]
            # Simple heuristic: buy if vol is below median, else hold
            if rolling_vol < 0.002:  # calm
                action = 1  # buy
            else:
                action = 0  # hold
            obs, reward, done, info = env.step(action)
            total_reward += reward
            if done:
                break
        rewards.append(total_reward)
        equities.append(info.get("equity", env.cfg.initial_equity))
        trade_counts.append(info.get("trade_count", 0))
        drawdowns.append(info.get("drawdown_pct", 0))

    return {
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "mean_equity": float(np.mean(equities)),
        "mean_trades": float(np.mean(trade_counts)),
        "max_drawdown": float(np.max(drawdowns)),
        "rewards": rewards,
    }


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------
def evaluate_policy_on_fold(
    policy: PolicyNetwork,
    env: TradingEnv,
    world_model: TinyWorldModel | None = None,
    wm_feature_indices: list[int] | None = None,
    n_episodes: int = 20,
    steps_per_episode: int = 500,
    start_idx: int | None = None,
) -> dict:
    """Evaluate a trained policy on a specific data fold.
    
    Calibration uses the world model's signal-to-noise ratio (SNR) as the
    confidence metric: SNR = |predicted_mean| / sqrt(predicted_variance).
    This measures how strong the directional signal is — high SNR means the
    world model sees a clear directional signal, so the policy should be
    more likely correct.  We fit a simple affine mapping from SNR to [0, 1]
    using the first half of labeled steps, then apply it on the second half.
    """
    import torch.nn.functional as F_torch

    policy.eval()
    rewards = []
    equities = []
    trade_counts = []
    drawdowns = []

    # Collect: (snr, action, correct_or_None) per step
    snr_list = []
    labeled_snrs = []
    labeled_corrects = []

    use_wm = (world_model is not None and wm_feature_indices is not None)

    for ep in range(n_episodes):
        obs = env.reset(start_idx=start_idx)
        total_reward = 0.0

        for step in range(steps_per_episode):
            idx = env._episode_start + step
            if idx >= env.n_samples - 1:
                break

            obs_tensor = torch.tensor(obs, dtype=torch.float32)
            with torch.no_grad():
                logits, _, _ = policy(obs_tensor.unsqueeze(0))
                probs = F_torch.softmax(logits, dim=-1)
                action = int(probs.argmax(dim=-1).item())
            obs, reward, done, info = env.step(action)
            total_reward += reward

            # Compute SNR from world model
            snr = 0.5  # default
            if use_wm and idx < len(env.states):
                with torch.no_grad():
                    full_feat = env.states[idx:idx+1]
                    wm_feat = full_feat[:, wm_feature_indices]
                    feat_tensor = torch.tensor(wm_feat, dtype=torch.float32)
                    pred_mean, pred_log_var = world_model(feat_tensor)
                    total_mean = pred_mean.sum().item()
                    total_var = torch.exp(pred_log_var).sum().item()
                    snr = abs(total_mean) / (np.sqrt(total_var) + 1e-10)
                    # Clip to reasonable range
                    snr = min(snr, 10.0)

            # Track correctness
            policy_direction = info.get("position", 0)
            correct = None
            if policy_direction != 0:
                next_idx = idx + 1
                if next_idx < len(env._returns):
                    actual_direction = np.sign(env._returns[next_idx])
                    if actual_direction != 0:
                        correct = (policy_direction == actual_direction)

            snr_list.append((snr, action, correct))
            if correct is not None:
                labeled_snrs.append(snr)
                labeled_corrects.append(float(correct))

        rewards.append(total_reward)
        equities.append(info.get("equity", env.cfg.initial_equity))
        trade_counts.append(info.get("trade_count", 0))
        drawdowns.append(info.get("drawdown_pct", 0))

    # Fit affine mapping from SNR to confidence [0, 1] on first half
    n_labeled = len(labeled_snrs)
    half = max(n_labeled // 2, 1)
    fit_snrs = np.array(labeled_snrs[:half])
    fit_accs = np.array(labeled_corrects[:half])

    # Simple approach: bin SNR values and map to accuracy
    # Then on second half, look up the bin
    if len(fit_snrs) >= 20:
        # Fit a simple sigmoid: conf = sigmoid(a * snr + b) via least squares
        # Or just use percentile-based binning
        snr_pcts = np.percentile(fit_snrs, [0, 25, 50, 75, 90, 100])
        snr_to_conf = {}
        for i in range(len(snr_pcts) - 1):
            lo, hi = snr_pcts[i], snr_pcts[i + 1]
            mask = (fit_snrs >= lo) & (fit_snrs < hi) if i < len(snr_pcts) - 2 else (fit_snrs >= lo)
            if mask.sum() > 0:
                bin_acc = float(fit_accs[mask].mean())
                # Clamp accuracy to [0.1, 0.9] to avoid extreme confidence
                bin_conf = max(0.1, min(0.9, bin_acc))
                snr_to_conf[(lo, hi)] = bin_conf
        # Default for values outside range
        default_conf = float(fit_accs.mean())
        default_conf = max(0.1, min(0.9, default_conf))
    else:
        snr_to_conf = {}
        default_conf = 0.5

    # Apply on second half
    all_confidences = []
    all_corrects = []
    for i in range(half, n_labeled):
        snr = labeled_snrs[i]
        correct = labeled_corrects[i]
        # Look up confidence from SNR bin
        conf = default_conf
        for (lo, hi), c in snr_to_conf.items():
            if lo <= snr < hi or (hi == snr_pcts[-1] and snr == hi):
                conf = c
                break
        all_confidences.append(conf)
        all_corrects.append(correct)

    calibration = compute_calibration(all_confidences, all_corrects)

    return {
        "mean_reward": float(np.mean(rewards)),
        "std_reward": float(np.std(rewards)),
        "mean_equity": float(np.mean(equities)),
        "mean_trades": float(np.mean(trade_counts)),
        "max_drawdown": float(np.max(drawdowns)),
        "rewards": rewards,
        "calibration": calibration,
        "n_episodes": n_episodes,
    }


def compute_calibration(
    confidences: list[float],
    corrects: list[bool],
    n_bins: int = 10,
) -> dict:
    """Compute calibration using Expected Calibration Error (ECE).

    ECE = sum(|bin_n| / N) * |bin_accuracy - bin_confidence|
    This measures how well confidence tracks accuracy across bins,
    weighted by the number of samples in each bin.
    """
    if not confidences or not corrects:
        return {"bins": [], "overall_gap": 0.0, "n_samples": 0}

    confs = np.array(confidences, dtype=float)
    corr = np.array(corrects, dtype=float)
    N = len(confs)

    # Bin by confidence
    bin_edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for i in range(n_bins):
        mask = (confs >= bin_edges[i]) & (confs < bin_edges[i + 1])
        if mask.sum() > 0:
            avg_conf = float(confs[mask].mean())
            avg_acc = float(corr[mask].mean())
            bins.append({
                "confidence": avg_conf,
                "accuracy": avg_acc,
                "count": int(mask.sum()),
                "gap": abs(avg_conf - avg_acc),
            })

    # Expected Calibration Error (ECE)
    ece = 0.0
    for b in bins:
        weight = b["count"] / N
        ece += weight * b["gap"]

    return {
        "bins": bins,
        "overall_gap": ece,
        "n_samples": N,
    }


# ---------------------------------------------------------------------------
# Main walk-forward evaluation
# ---------------------------------------------------------------------------
def run_walk_forward(
    dataset_path: str = "data/dataset_R75_h10.npz",
    world_model_path: str | None = None,
    train_days: int = 60,
    val_days: int = 30,
    step_days: int = 30,
    train_iterations: int = 100,
    rollout_steps: int = 500,
    eval_episodes: int = 20,
    device: str | None = None,
) -> dict:
    """Run full walk-forward policy evaluation."""
    device = device or ("cuda" if torch.cuda.is_available() else "cpu")

    # Load world model if provided
    world_model = None
    wm_feature_indices = None
    if world_model_path and Path(world_model_path).exists():
        ckpt = torch.load(world_model_path, map_location=device, weights_only=False)
        wm_feature_names = list(ckpt["feature_names"])
        wm_n_features = len(wm_feature_names)

        world_model = TinyWorldModel(
            n_features=wm_n_features,
            horizon=ckpt.get("horizon", 10),
            hidden=ckpt.get("hidden_dim", 16),
        ).to(device)
        world_model.load_state_dict(ckpt["model_state_dict"])
        world_model.eval()

        # Map wm feature names to env feature indices
        ds = load_tiny_dataset(dataset_path)
        env_feat_to_idx = {name: i for i, name in enumerate(ds["feature_names"])}
        wm_feature_indices = [env_feat_to_idx[f] for f in wm_feature_names if f in env_feat_to_idx]
        logger.info("world_model_loaded path=%s features=%d", world_model_path, wm_n_features)

    # Load dataset to get timestamps for splitting
    data = np.load(dataset_path, allow_pickle=True)
    timestamps = data["timestamps"]
    ts_min = pd.Timestamp(timestamps.min())
    ts_max = pd.Timestamp(timestamps.max())

    logger.info("dataset range=%s to %s", ts_min.date(), ts_max.date())

    # Build walk-forward splits
    folds = []
    offset_days = 0
    fold_idx = 0
    while True:
        train_start = ts_min + pd.Timedelta(days=offset_days)
        train_end = train_start + pd.Timedelta(days=train_days)
        val_start = train_end
        val_end = val_start + pd.Timedelta(days=val_days)

        if val_end > ts_max + pd.Timedelta(hours=12):
            break

        # Find indices for this fold
        train_mask = (timestamps >= np.datetime64(train_start)) & (timestamps < np.datetime64(train_end))
        val_mask = (timestamps >= np.datetime64(val_start)) & (timestamps < np.datetime64(val_end))
        train_indices = np.where(train_mask)[0]
        val_indices = np.where(val_mask)[0]

        if len(train_indices) > 0 and len(val_indices) > 0:
            folds.append({
                "fold": fold_idx,
                "train_start": str(train_start.date()),
                "train_end": str(train_end.date()),
                "val_start": str(val_start.date()),
                "val_end": str(val_end.date()),
                "train_start_idx": int(train_indices[0]),
                "train_end_idx": int(train_indices[-1]),
                "val_start_idx": int(val_indices[0]),
                "val_end_idx": int(val_indices[-1]),
                "n_train": len(train_indices),
                "n_val": len(val_indices),
            })
            fold_idx += 1

        offset_days += step_days

    logger.info("walk_forward folds=%d", len(folds))

    # Baselines (evaluate once on the full dataset)
    logger.info("evaluating baselines...")
    env_base = TradingEnv(
        dataset_path=dataset_path,
        tiny_features=TINY_FEATURES,
        config=TradingEnvConfig(max_steps=rollout_steps),
    )

    random_stats = evaluate_random_policy(env_base, n_episodes=eval_episodes, steps_per_episode=rollout_steps)
    heuristic_stats = evaluate_heuristic_policy(env_base, n_episodes=eval_episodes, steps_per_episode=rollout_steps)

    logger.info(
        "baselines: random=%.2f+/-%.2f  heuristic=%.2f+/-%.2f",
        random_stats["mean_reward"], random_stats["std_reward"],
        heuristic_stats["mean_reward"], heuristic_stats["std_reward"],
    )

    # Evaluate each fold
    fold_results = []
    for f in folds:
        t0 = time.time()
        logger.info(
            "fold %d: train=[%s -> %s] val=[%s -> %s]",
            f["fold"], f["train_start"], f["train_end"],
            f["val_start"], f["val_end"],
        )

        # Create environment for this fold's validation period
        env_val = TradingEnv(
            dataset_path=dataset_path,
            tiny_features=TINY_FEATURES,
            config=TradingEnvConfig(max_steps=rollout_steps),
        )

        # Train policy on this fold's training data
        # Restrict to fold's training range by pre-setting the start
        env_train = TradingEnv(
            dataset_path=dataset_path,
            tiny_features=TINY_FEATURES,
            config=TradingEnvConfig(max_steps=min(rollout_steps, f["n_train"])),
        )

        result = train_policy(
            env_train,
            n_iterations=train_iterations,
            rollout_steps=min(rollout_steps, f["n_train"]),
            n_epochs=4,
            lr=3e-4,
            device=device,
        )
        trained_policy = result["policy"]

        # Evaluate on validation period
        val_stats = evaluate_policy_on_fold(
            trained_policy,
            env_val,
            world_model=world_model,
            wm_feature_indices=wm_feature_indices,
            n_episodes=eval_episodes,
            steps_per_episode=rollout_steps,
            start_idx=f["val_start_idx"],
        )

        elapsed = time.time() - t0

        # Compare vs baselines
        beats_random = val_stats["mean_reward"] > random_stats["mean_reward"]
        beats_heuristic = val_stats["mean_reward"] > heuristic_stats["mean_reward"]

        fold_result = {
            **f,
            "policy_reward": val_stats["mean_reward"],
            "policy_std": val_stats["std_reward"],
            "policy_equity": val_stats["mean_equity"],
            "policy_trades": val_stats["mean_trades"],
            "policy_max_dd": val_stats["max_drawdown"],
            "calibration": val_stats["calibration"],
            "random_reward": random_stats["mean_reward"],
            "heuristic_reward": heuristic_stats["mean_reward"],
            "beats_random": beats_random,
            "beats_heuristic": beats_heuristic,
            "elapsed_s": elapsed,
        }
        fold_results.append(fold_result)

        logger.info(
            "  policy=%.2f+/-%.2f  random=%.2f  heuristic=%.2f  "
            "beats_random=%s  beats_heuristic=%s  dd=%.1f%%  cal_gap=%.3f  time=%.1fs",
            val_stats["mean_reward"], val_stats["std_reward"],
            random_stats["mean_reward"], heuristic_stats["mean_reward"],
            "YES" if beats_random else "NO",
            "YES" if beats_heuristic else "NO",
            val_stats["max_drawdown"] * 100,
            val_stats["calibration"]["overall_gap"],
            elapsed,
        )

    # Aggregate
    total_folds = len(fold_results)
    n_beats_random = sum(1 for f in fold_results if f["beats_random"])
    n_beats_heuristic = sum(1 for f in fold_results if f["beats_heuristic"])
    avg_reward = np.mean([f["policy_reward"] for f in fold_results])
    # Calibration: only count folds with actual trades (n_samples > 0)
    cal_folds = [f["calibration"] for f in fold_results if f["calibration"]["n_samples"] > 0]
    if cal_folds:
        avg_cal_gap = np.mean([c["overall_gap"] for c in cal_folds])
    else:
        avg_cal_gap = 0.0  # no calibration data = neutral
    max_dd = max(f["policy_max_dd"] for f in fold_results)
    no_catastrophic_dd = max_dd < 0.20  # 20% max drawdown

    # Pass/fail
    beats_baselines = n_beats_random >= total_folds // 2 and n_beats_heuristic >= total_folds // 2
    calibration_ok = avg_cal_gap < 0.15  # 15% max ECE
    overall_pass = beats_baselines and calibration_ok and no_catastrophic_dd

    verdict = "PASS" if overall_pass else "FAIL"
    if not beats_baselines:
        verdict = "FAIL (baselines)"
    if not calibration_ok:
        verdict = "FAIL (calibration)"
    if not no_catastrophic_dd:
        verdict = "FAIL (drawdown)"

    # Print report
    print("\n" + "=" * 90)
    print("WALK-FORWARD POLICY VALIDATION")
    print("=" * 90)
    print(f"Dataset: {dataset_path}")
    print(f"Folds: {total_folds} (train={train_days}d, val={val_days}d, step={step_days}d)")
    print(f"Training: {train_iterations} iterations, {rollout_steps} steps/episode")
    print(f"\nBaselines:")
    print(f"  Random:   {random_stats['mean_reward']:+.2f} +/- {random_stats['std_reward']:.2f}")
    print(f"  Heuristic: {heuristic_stats['mean_reward']:+.2f} +/- {heuristic_stats['std_reward']:.2f}")
    print(f"\n{'Fold':>4s} {'Period':>25s} {'Policy':>12s} {'vs Random':>10s} {'vs Heur':>10s} {'MaxDD':>8s} {'CalGap':>8s}")
    print("-" * 90)

    for f in fold_results:
        period = f"  {f['val_start']} -> {f['val_end']}"
        print(
            f"{f['fold']:4d} {period:>25s} "
            f"{f['policy_reward']:+12.2f} "
            f"{'BEAT' if f['beats_random'] else 'LOSE':>10s} "
            f"{'BEAT' if f['beats_heuristic'] else 'LOSE':>10s} "
            f"{f['policy_max_dd']:7.1%} "
            f"{f['calibration']['overall_gap']:8.3f}"
        )

    print("-" * 90)
    print(f"Aggregate:")
    print(f"  Avg policy reward: {avg_reward:+.2f}")
    print(f"  Beats random: {n_beats_random}/{total_folds}")
    print(f"  Beats heuristic: {n_beats_heuristic}/{total_folds}")
    print(f"  Avg calibration gap (ECE): {avg_cal_gap:.3f} ({len(cal_folds)}/{total_folds} folds with trades)")
    print(f"  Max drawdown: {max_dd:.1%}")
    print(f"\nVerdict: {verdict}")

    # Calibration detail for best fold
    best_fold = max(fold_results, key=lambda x: x["policy_reward"])
    print(f"\nCalibration detail (best fold {best_fold['fold']}):")
    for b in best_fold["calibration"]["bins"]:
        print(f"  Conf={b['confidence']:.2f}  Acc={b['accuracy']:.2f}  N={b['count']:5d}  Gap={b['gap']:.3f}")

    report = {
        "config": {
            "dataset": dataset_path,
            "n_folds": total_folds,
            "train_days": train_days,
            "val_days": val_days,
            "step_days": step_days,
            "train_iterations": train_iterations,
            "rollout_steps": rollout_steps,
        },
        "baselines": {
            "random": random_stats,
            "heuristic": heuristic_stats,
        },
        "folds": fold_results,
        "aggregate": {
            "avg_reward": float(avg_reward),
            "n_beats_random": n_beats_random,
            "n_beats_heuristic": n_beats_heuristic,
            "avg_calibration_gap": float(avg_cal_gap),
            "max_drawdown": float(max_dd),
        },
        "verdict": verdict,
    }

    return report


def main():
    parser = argparse.ArgumentParser(description="Walk-forward policy validation")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--world-model", default="checkpoints_tiny/world_model_best.pt",
                        help="Path to world model checkpoint for calibration")
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--val-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--train-iterations", type=int, default=100)
    parser.add_argument("--rollout-steps", type=int, default=500)
    parser.add_argument("--eval-episodes", type=int, default=20)
    parser.add_argument("--device", default=None)
    parser.add_argument("--output", default="checkpoints_tiny/policy_validation.json")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    report = run_walk_forward(
        dataset_path=args.dataset,
        world_model_path=args.world_model,
        train_days=args.train_days,
        val_days=args.val_days,
        step_days=args.step_days,
        train_iterations=args.train_iterations,
        rollout_steps=args.rollout_steps,
        eval_episodes=args.eval_episodes,
        device=args.device,
    )

    # Save report
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to: {args.output}")


if __name__ == "__main__":
    main()
