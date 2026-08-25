"""
model/experiment.py — Architecture search for the world model.

Trains multiple configurations (hidden dim, depth) and compares:
  - Validation NLL, MAE, RMSE
  - Calibration (1σ, 2σ coverage)
  - Parameter count
  - Training time

Usage:
    python -m model.experiment --epochs 40
    python -m model.experiment --epochs 60 --configs wide,deep,widedepth
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from model.world_model import (
    WorldModel,
    build_model_from_features,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Experiment configurations
# ---------------------------------------------------------------------------

@dataclass
class ExperimentConfig:
    """A single architecture experiment."""
    name: str
    hidden_dim: int = 32
    branch_depth: int = 2
    fusion_depth: int = 2
    dropout: float = 0.1
    lr: float = 1e-3
    var_reg_weight: float = 0.01


# Pre-defined configs to sweep
CONFIGS: dict[str, ExperimentConfig] = {
    "baseline": ExperimentConfig(
        name="baseline",
        hidden_dim=32, branch_depth=2, fusion_depth=2,
    ),
    "wide": ExperimentConfig(
        name="wide",
        hidden_dim=64, branch_depth=2, fusion_depth=2,
    ),
    "deep": ExperimentConfig(
        name="deep",
        hidden_dim=32, branch_depth=3, fusion_depth=3,
    ),
    "widedepth": ExperimentConfig(
        name="widedepth",
        hidden_dim=64, branch_depth=3, fusion_depth=2,
    ),
    "widehead": ExperimentConfig(
        name="widehead",
        hidden_dim=64, branch_depth=2, fusion_depth=2, dropout=0.15,
    ),
    "xl": ExperimentConfig(
        name="xl",
        hidden_dim=96, branch_depth=3, fusion_depth=3, dropout=0.15,
    ),
}


# ---------------------------------------------------------------------------
# Training loop
# ---------------------------------------------------------------------------

@dataclass
class ExperimentResult:
    """Result of a single experiment."""
    name: str
    config: ExperimentConfig
    n_params: int
    best_epoch: int
    best_val_nll: float
    final_val_mae: float
    final_val_rmse: float
    coverage_1sigma: float
    coverage_2sigma: float
    calibration_gap: float
    mean_predicted_std: float
    actual_std: float
    training_time_s: float
    epochs_per_sec: float
    history: list[dict[str, Any]] = field(default_factory=list)


def train_experiment(
    config: ExperimentConfig,
    feature_names: list[str],
    train_states: np.ndarray,
    train_targets: np.ndarray,
    val_states: np.ndarray,
    val_targets: np.ndarray,
    horizon: int,
    epochs: int = 40,
    batch_size: int = 256,
    device: torch.device = None,
) -> ExperimentResult:
    """Train and evaluate one experiment configuration."""
    if device is None:
        device = torch.device("cpu")

    t0 = time.time()

    # Build model
    model = build_model_from_features(
        feature_names, horizon=horizon,
        hidden_dim=config.hidden_dim,
        dropout=config.dropout,
        branch_depth=config.branch_depth,
        fusion_depth=config.fusion_depth,
        train_targets=train_targets,
    ).to(device)

    n_params = sum(p.numel() for p in model.parameters())

    # Data loaders
    train_ds = TensorDataset(
        torch.tensor(train_states, dtype=torch.float32),
        torch.tensor(train_targets, dtype=torch.float32),
    )
    val_ds = TensorDataset(
        torch.tensor(val_states, dtype=torch.float32),
        torch.tensor(val_targets, dtype=torch.float32),
    )
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    # Optimizer
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    best_val_nll = float("inf")
    best_epoch = 0
    best_state = None
    history = []

    for epoch in range(epochs):
        # Train
        model.train()
        total_loss = 0.0
        n_batches = 0
        for states_b, targets_b in train_loader:
            states_b = states_b.to(device)
            targets_b = targets_b.to(device)
            optimizer.zero_grad()
            mean, log_var = model(states_b)
            loss = gaussian_nll_loss(mean, log_var, targets_b, var_reg_weight=config.var_reg_weight)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1
        scheduler.step()
        train_loss = total_loss / max(n_batches, 1)

        # Validate
        model.eval()
        with torch.no_grad():
            all_means, all_vars, all_targets = [], [], []
            for states_b, targets_b in val_loader:
                states_b = states_b.to(device)
                targets_b = targets_b.to(device)
                mean, log_var = model(states_b)
                all_means.append(mean.cpu().numpy())
                all_vars.append(torch.exp(log_var).cpu().numpy())
                all_targets.append(targets_b.cpu().numpy())

            means = np.concatenate(all_means)
            variances = np.concatenate(all_vars)
            targets_np = np.concatenate(all_targets)

        eps = 1e-12
        var = np.maximum(variances, eps)
        stds = np.sqrt(var)
        nll_vals = 0.5 * (np.log(2 * np.pi * var) + (targets_np - means) ** 2 / var)
        val_nll = float(np.mean(nll_vals))
        val_mae = float(np.mean(np.abs(targets_np - means)))
        val_rmse = float(np.sqrt(np.mean((targets_np - means) ** 2)))
        cov_1s = float(np.mean(np.abs(targets_np - means) <= stds))
        cov_2s = float(np.mean(np.abs(targets_np - means) <= 2 * stds))

        record = {
            "epoch": epoch,
            "train_loss": train_loss,
            "val_nll": val_nll,
            "val_mae": val_mae,
            "cov_1sigma": cov_1s,
            "cov_2sigma": cov_2s,
        }
        history.append(record)

        if val_nll < best_val_nll:
            best_val_nll = val_nll
            best_epoch = epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if epoch % 10 == 0 or epoch == epochs - 1:
            logger.info(
                "  [%s] epoch %d/%d  loss=%.4f  val_nll=%.4f  1s=%.1f%%  2s=%.1f%%",
                config.name, epoch + 1, epochs, train_loss, val_nll,
                cov_1s * 100, cov_2s * 100,
            )

    # Restore best model
    if best_state is not None:
        model.load_state_dict(best_state)

    # Final calibration with best model
    model.eval()
    with torch.no_grad():
        all_means, all_vars, all_targets = [], [], []
        for states_b, targets_b in val_loader:
            states_b = states_b.to(device)
            targets_b = targets_b.to(device)
            mean, log_var = model(states_b)
            all_means.append(mean.cpu().numpy())
            all_vars.append(torch.exp(log_var).cpu().numpy())
            all_targets.append(targets_b.cpu().numpy())

        means = np.concatenate(all_means)
        variances = np.concatenate(all_vars)
        targets_np = np.concatenate(all_targets)

    var = np.maximum(variances, eps)
    stds = np.sqrt(var)

    # Calibration gap
    from scipy.stats import norm
    levels = np.linspace(0.05, 0.95, 20)
    predicted_cov, actual_cov = [], []
    for level in levels:
        z = norm.ppf((1 + level) / 2)
        lower = means - z * stds
        upper = means + z * stds
        predicted_cov.append(level)
        actual_cov.append(float(np.mean((targets_np >= lower) & (targets_np <= upper))))
    cal_gap = float(np.mean(np.abs(np.array(predicted_cov) - np.array(actual_cov))))

    elapsed = time.time() - t0

    return ExperimentResult(
        name=config.name,
        config=config,
        n_params=n_params,
        best_epoch=best_epoch,
        best_val_nll=best_val_nll,
        final_val_mae=float(np.mean(np.abs(targets_np - means))),
        final_val_rmse=float(np.sqrt(np.mean((targets_np - means) ** 2))),
        coverage_1sigma=cov_1s,
        coverage_2sigma=cov_2s,
        calibration_gap=cal_gap,
        mean_predicted_std=float(np.mean(stds)),
        actual_std=float(np.std(targets_np)),
        training_time_s=elapsed,
        epochs_per_sec=epochs / elapsed if elapsed > 0 else 0,
        history=history,
    )


# ---------------------------------------------------------------------------
# Main experiment runner
# ---------------------------------------------------------------------------

def run_experiments(
    dataset_path: str = "data/dataset_R75_h10.npz",
    configs: list[str] | None = None,
    epochs: int = 40,
    batch_size: int = 256,
    output_dir: str = "checkpoints/experiments",
    device_str: str | None = None,
) -> list[ExperimentResult]:
    """Run architecture search experiments."""
    device = torch.device(
        device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    )

    # Load dataset
    ds = np.load(dataset_path, allow_pickle=True)
    states = ds["states"]
    targets = ds["targets"]
    timestamps = ds["timestamps"]
    feature_names = list(ds["feature_names"])
    horizon = targets.shape[1]

    # Temporal split: 85% train, 15% val
    n_val = int(len(states) * 0.15)
    n_train = len(states) - n_val
    sort_idx = np.argsort(timestamps)
    states = states[sort_idx]
    targets = targets[sort_idx]

    train_states = states[:n_train]
    train_targets = targets[:n_train]
    val_states = states[n_train:]
    val_targets = targets[n_train:]

    logger.info(
        "experiments.dataset train=%d val=%d features=%d horizon=%d",
        n_train, n_val, states.shape[1], horizon,
    )

    # Select configs
    if configs is None:
        configs = ["baseline", "wide", "deep", "widedepth"]

    results = []
    for name in configs:
        if name not in CONFIGS:
            logger.warning("experiments.unknown_config name=%s", name)
            continue

        config = CONFIGS[name]
        logger.info(
            "experiments.running name=%s hidden=%d branch_depth=%d fusion_depth=%d",
            name, config.hidden_dim, config.branch_depth, config.fusion_depth,
        )

        result = train_experiment(
            config=config,
            feature_names=feature_names,
            train_states=train_states,
            train_targets=train_targets,
            val_states=val_states,
            val_targets=val_targets,
            horizon=horizon,
            epochs=epochs,
            batch_size=batch_size,
            device=device,
        )
        results.append(result)

    # Print comparison table
    print_comparison(results)

    # Save results
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(output_dir) / "experiment_results.json"
    save_data = []
    for r in results:
        save_data.append({
            "name": r.name,
            "hidden_dim": r.config.hidden_dim,
            "branch_depth": r.config.branch_depth,
            "fusion_depth": r.config.fusion_depth,
            "n_params": r.n_params,
            "best_epoch": r.best_epoch,
            "best_val_nll": r.best_val_nll,
            "val_mae": r.final_val_mae,
            "val_rmse": r.final_val_rmse,
            "coverage_1sigma": r.coverage_1sigma,
            "coverage_2sigma": r.coverage_2sigma,
            "calibration_gap": r.calibration_gap,
            "training_time_s": r.training_time_s,
        })
    with open(save_path, "w") as f:
        json.dump(save_data, f, indent=2)
    logger.info("experiments.saved path=%s", save_path)

    return results


def print_comparison(results: list[ExperimentResult]) -> None:
    """Print a formatted comparison table."""
    print("\n" + "=" * 100)
    print("ARCHITECTURE EXPERIMENT RESULTS")
    print("=" * 100)
    print(f"{'Name':12s} {'Params':>8s} {'Hidden':>6s} {'BrD':>3s} {'FuD':>3s} "
          f"{'NLL':>8s} {'MAE':>10s} {'1sig':>6s} {'2sig':>6s} {'CalGap':>7s} {'Time':>6s}")
    print("-" * 100)

    for r in sorted(results, key=lambda x: x.best_val_nll):
        print(
            f"{r.name:12s} {r.n_params:>8,d} {r.config.hidden_dim:>6d} "
            f"{r.config.branch_depth:>3d} {r.config.fusion_depth:>3d} "
            f"{r.best_val_nll:>8.4f} {r.final_val_mae:>10.6f} "
            f"{r.coverage_1sigma:>5.1%} {r.coverage_2sigma:>5.1%} "
            f"{r.calibration_gap:>7.4f} {r.training_time_s:>5.0f}s"
        )

    print("=" * 100)
    print("Lower NLL = better | 1sig target ~68% | 2sig target ~95% | CalGap target <0.05")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Architecture search experiments")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--configs", nargs="+", default=None,
                        help=f"Configs to run. Available: {list(CONFIGS.keys())}")
    parser.add_argument("--epochs", type=int, default=40)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--output-dir", default="checkpoints/experiments")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_experiments(
        dataset_path=args.dataset,
        configs=args.configs,
        epochs=args.epochs,
        batch_size=args.batch_size,
        output_dir=args.output_dir,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
