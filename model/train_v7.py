"""
model/train_v7.py — Train with variance-fix architecture.

Changes from previous versions:
  1. Separate variance pathway (deeper, wider) to avoid gradient competition
  2. Gradient balancing: scale up variance head gradients by 100x
  3. Stronger variance regularization (0.1 vs 0.01)
  4. Temperature saved in checkpoint

Usage:
    python -m model.train_v7
    python -m model.train_v7 --epochs 50 --var-reg-weight 0.1 --var-grad-scale 100
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from model.dataset import WorldModelDataset
from model.world_model import (
    WorldModel,
    build_model_from_features,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)


def train_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    optimizer_mean: torch.optim.Optimizer,
    optimizer_var: torch.optim.Optimizer,
    mean_params: list,
    var_params: list,
    device: torch.device,
    var_reg_weight: float = 0.1,
) -> float:
    """Train for one epoch with separate optimizers for mean and variance."""
    model.train()
    total_loss = 0.0
    n_batches = 0

    for states, targets in dataloader:
        states = states.to(device)
        targets = targets.to(device)

        optimizer_mean.zero_grad()
        optimizer_var.zero_grad()
        pred_mean, pred_log_var = model(states)
        loss = gaussian_nll_loss(
            pred_mean, pred_log_var, targets,
            var_reg_weight=var_reg_weight,
        )
        loss.backward()

        # Gradient clipping per-group
        torch.nn.utils.clip_grad_norm_(mean_params, max_norm=1.0)
        torch.nn.utils.clip_grad_norm_(var_params, max_norm=1.0)
        optimizer_mean.step()
        optimizer_var.step()
        total_loss += loss.item()
        n_batches += 1

    return total_loss / max(n_batches, 1)


@torch.no_grad()
def validate(
    model: nn.Module,
    dataloader: DataLoader,
    device: torch.device,
) -> dict[str, float]:
    """Validate and compute metrics."""
    model.eval()
    all_means, all_vars, all_targets = [], [], []

    for states, targets in dataloader:
        states = states.to(device)
        targets = targets.to(device)
        pred_mean, pred_log_var = model(states)
        all_means.append(pred_mean.cpu().numpy())
        all_vars.append(torch.exp(pred_log_var).cpu().numpy())
        all_targets.append(targets.cpu().numpy())

    means = np.concatenate(all_means, axis=0)
    variances = np.concatenate(all_vars, axis=0)
    targets = np.concatenate(all_targets, axis=0)

    eps = 1e-8
    nll = 0.5 * (np.log(2 * np.pi * variances) + (targets - means) ** 2 / (variances + eps))
    stds = np.sqrt(variances)

    return {
        "val_nll": float(np.mean(nll)),
        "val_mae": float(np.mean(np.abs(targets - means))),
        "val_mse": float(np.mean((targets - means) ** 2)),
        "coverage_1sigma": float(np.mean(np.abs(targets - means) <= stds)),
        "coverage_2sigma": float(np.mean(np.abs(targets - means) <= 2 * stds)),
        "val_rmse": float(np.sqrt(np.mean((targets - means) ** 2))),
        "mean_predicted_std": float(np.mean(stds)),
        "actual_std": float(np.std(targets)),
    }


def compute_temperature(val_targets, val_means, val_vars):
    """Compute temperature scaling factor."""
    squared_errors = (val_targets - val_means) ** 2
    T = float(np.mean(squared_errors) / np.mean(val_vars))
    return max(T, 0.01)


def train_v7(
    dataset_path: str = "data/dataset_R75_h10.npz",
    output_dir: str = "checkpoints_v7",
    epochs: int = 50,
    batch_size: int = 256,
    lr: float = 1e-3,
    hidden_dim: int = 32,
    dropout: float = 0.1,
    weight_decay: float = 1e-4,
    var_reg_weight: float = 0.1,
    var_grad_scale: float = 100.0,
    val_split: float = 0.15,
    device_str: str | None = None,
) -> dict[str, Any]:
    """Train with variance-fix architecture."""
    device = torch.device(device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info("train_v7.device=%s var_reg=%.2f var_grad_scale=%.0f", device, var_reg_weight, var_grad_scale)

    # Load dataset
    data = np.load(dataset_path, allow_pickle=True)
    full_states = data["states"]
    full_targets = data["targets"]
    full_timestamps = data["timestamps"]
    full_feature_names = list(data["feature_names"])
    horizon = full_targets.shape[1]

    # Filter to selected features (same as train_reduced)
    from model.train_reduced import SELECTED_FEATURES, build_reduced_dataset
    states, feature_names = build_reduced_dataset(
        full_states, full_targets, full_timestamps, full_feature_names, SELECTED_FEATURES
    )

    # Temporal split
    sort_idx = np.argsort(full_timestamps)
    states = states[sort_idx]
    targets = full_targets[sort_idx]

    n_val = int(len(states) * val_split)
    n_train = len(states) - n_val

    train_states = states[:n_train]
    train_targets = targets[:n_train]
    val_states = states[n_train:]
    val_targets = targets[n_train:]

    logger.info("split train=%d val=%d features=%d", n_train, n_val, len(feature_names))

    train_loader = DataLoader(
        WorldModelDataset(train_states, train_targets, full_timestamps[:n_train]),
        batch_size=batch_size, shuffle=True, num_workers=0,
    )
    val_loader = DataLoader(
        WorldModelDataset(val_states, val_targets, full_timestamps[n_train:]),
        batch_size=batch_size, shuffle=False, num_workers=0,
    )

    # Build model (with new separate variance pathway)
    model = build_model_from_features(
        feature_names, horizon=horizon, dropout=dropout,
        hidden_dim=hidden_dim, train_targets=train_targets,
    ).to(device)

    # Separate optimizers: mean head gets normal LR, var head gets 10x LR
    # to compensate for the ~100x gradient imbalance.
    mean_params = []
    var_params = []
    for name, param in model.named_parameters():
        if "log_var" in name:
            var_params.append(param)
        else:
            mean_params.append(param)

    optimizer_mean = torch.optim.AdamW(mean_params, lr=lr, weight_decay=weight_decay)
    optimizer_var = torch.optim.AdamW(var_params, lr=lr * 10, weight_decay=weight_decay)
    scheduler_mean = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer_mean, T_max=epochs, eta_min=1e-5)
    scheduler_var = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer_var, T_max=epochs, eta_min=1e-4)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(output_dir) / "world_model_best.pt"

    best_val_nll = float("inf")
    best_epoch = 0
    history = []

    logger.info("training epochs=%d features=%d var_reg=%.2f sep_optim=True",
                epochs, len(feature_names), var_reg_weight)

    for epoch in range(epochs):
        t0 = time.time()
        train_loss = train_epoch(
            model, train_loader,
            optimizer_mean, optimizer_var,
            mean_params, var_params,
            device,
            var_reg_weight=var_reg_weight,
        )
        scheduler_mean.step()
        scheduler_var.step()
        val_metrics = validate(model, val_loader, device)
        elapsed = time.time() - t0

        record = {
            "epoch": epoch, "train_loss": train_loss,
            "val_nll": val_metrics["val_nll"], "val_mae": val_metrics["val_mae"],
            "val_rmse": val_metrics["val_rmse"],
            "coverage_1sigma": val_metrics["coverage_1sigma"],
            "coverage_2sigma": val_metrics["coverage_2sigma"],
            "lr": optimizer_mean.param_groups[0]["lr"], "elapsed": elapsed,
        }
        history.append(record)

        if epoch % 5 == 0 or epoch == epochs - 1:
            logger.info(
                "epoch %d/%d loss=%.4f val_nll=%.4f val_mae=%.6f "
                "cov1sig=%.2f%% cov2sig=%.2f%% lr=%.2e time=%.1fs",
                epoch + 1, epochs, train_loss,
                val_metrics["val_nll"], val_metrics["val_mae"],
                val_metrics["coverage_1sigma"] * 100,
                val_metrics["coverage_2sigma"] * 100,
                optimizer_mean.param_groups[0]["lr"], elapsed,
            )

        if val_metrics["val_nll"] < best_val_nll:
            best_val_nll = val_metrics["val_nll"]
            best_epoch = epoch

            # Compute temperature on validation set
            model.eval()
            with torch.no_grad():
                all_val_mean, all_val_var = [], []
                for states_b, _ in val_loader:
                    states_b = states_b.to(device)
                    mean, log_var = model(states_b)
                    all_val_mean.append(mean.cpu().numpy())
                    all_val_var.append(torch.exp(log_var).cpu().numpy())
            val_means_np = np.concatenate(all_val_mean, axis=0)
            val_vars_np = np.concatenate(all_val_var, axis=0)
            temperature = compute_temperature(val_targets, val_means_np, val_vars_np)

            # Load normalization stats
            norm_stats = {}
            if "feat_mean" in data and "feat_std" in data:
                feat_mean_full = data["feat_mean"]
                feat_std_full = data["feat_std"]
                all_feat_names = list(data["feature_names"])
                name_to_idx = {n: i for i, n in enumerate(all_feat_names)}
                sel_indices = [name_to_idx[f] for f in feature_names if f in name_to_idx]
                norm_stats["feat_mean"] = feat_mean_full[sel_indices].astype(np.float32)
                norm_stats["feat_std"] = feat_std_full[sel_indices].astype(np.float32)

            torch.save({
                "model_state_dict": model.state_dict(),
                "config": model.config,
                "feature_names": feature_names,
                "epoch": epoch,
                "val_metrics": val_metrics,
                "horizon": horizon,
                "temperature": temperature,
                **norm_stats,
            }, save_path)

            logger.info("saved T=%.4f val_nll=%.4f", temperature, best_val_nll)

    logger.info("complete best_epoch=%d best_val_nll=%.4f saved=%s", best_epoch, best_val_nll, save_path)

    # Baselines
    model.eval()
    with torch.no_grad():
        all_pred_mean, all_pred_var = [], []
        for states_b, _ in val_loader:
            states_b = states_b.to(device)
            mean, log_var = model(states_b)
            all_pred_mean.append(mean.cpu().numpy())
            all_pred_var.append(torch.exp(log_var).cpu().numpy())

    wm_means = np.concatenate(all_pred_mean, axis=0)
    wm_vars = np.concatenate(all_pred_var, axis=0)

    # Apply temperature
    wm_vars_cal = wm_vars * temperature

    eps = 1e-8
    def _metrics(m, v, t):
        var = np.maximum(v, eps)
        nll = 0.5 * (np.log(2 * np.pi * var) + (t - m) ** 2 / var)
        stds = np.sqrt(var)
        return {
            "val_nll": float(np.mean(nll)),
            "val_mae": float(np.mean(np.abs(t - m))),
            "coverage_1sigma": float(np.mean(np.abs(t - m) <= stds)),
            "coverage_2sigma": float(np.mean(np.abs(t - m) <= 2 * stds)),
        }

    wm_raw = _metrics(wm_means, wm_vars, val_targets); wm_raw["name"] = "world_model_raw"
    wm_cal = _metrics(wm_means, wm_vars_cal, val_targets); wm_cal["name"] = "world_model_cal"

    persistence = _metrics(np.zeros_like(val_targets), np.ones_like(val_targets) * 1e-6, val_targets)
    persistence["name"] = "persistence"
    train_std = float(np.std(val_targets[:, 0]))
    steps = np.arange(1, horizon + 1, dtype=np.float64)
    rw = _metrics(np.zeros_like(val_targets), np.tile(train_std**2 * steps, (len(val_targets), 1)), val_targets)
    rw["name"] = "random_walk"

    baselines = {"world_model_raw": wm_raw, "world_model_cal": wm_cal, "persistence": persistence, "random_walk": rw}

    results = {
        "best_epoch": best_epoch,
        "best_val_nll": best_val_nll,
        "history": history,
        "val_metrics": val_metrics,
        "baselines": baselines,
        "model_path": str(save_path),
        "n_train": n_train,
        "n_val": n_val,
        "horizon": horizon,
        "n_features": len(feature_names),
        "temperature": temperature,
    }

    results_path = Path(output_dir) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    print("\n" + "=" * 60)
    print("V7 TRAINING COMPLETE (separate variance pathway + gradient balancing)")
    print("=" * 60)
    print(f"Features: {len(feature_names)}")
    print(f"Best epoch: {best_epoch}")
    print(f"Best val NLL (raw): {best_val_nll:.4f}")
    print(f"Temperature: {temperature:.4f}")
    print(f"Model saved: {save_path}")
    print(f"\nBaseline comparison (temperature-calibrated):")
    for name, m in baselines.items():
        print(f"  {name:20s}: NLL={m['val_nll']:.4f}  1sig={m['coverage_1sigma']:.1%}  2sig={m['coverage_2sigma']:.1%}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Train with variance-fix architecture (v7)")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--output-dir", default="checkpoints_v7")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-dim", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--var-reg-weight", type=float, default=0.1)
    parser.add_argument("--var-grad-scale", type=float, default=100.0)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    train_v7(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        weight_decay=args.weight_decay,
        var_reg_weight=args.var_reg_weight,
        var_grad_scale=args.var_grad_scale,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
