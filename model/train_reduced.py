"""
model/train_reduced.py — Train with a reduced feature set.

Based on feature importance analysis, we drop:
  - All 1min features (near-zero importance)
  - All structure features (near-zero importance)
  - Weak 15min/1h features (volume, vol_of_vol, time_since_*, etc.)

Keeps only the top ~35 features that actually drive predictions.

Usage:
    python -m model.train_reduced
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import DataLoader

from model.dataset import WorldModelDataset
from model.world_model import (
    WorldModel,
    WorldModelConfig,
    FeatureGroup,
    build_model_from_features,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature selection: top features from importance analysis
# ---------------------------------------------------------------------------

# Keep: 15min core + 1h core + enhanced features
# Removed: OHLC prices (correlated), realized_vol (near-zero variance)
SELECTED_FEATURES = [
    # --- 15min: Relative features (normalized, predictive) ---
    "15min_range",
    "15min_body",
    "15min_range_ATR",
    "15min_body_ATR",
    "15min_return_1",
    "15min_return_5",
    "15min_return_20",
    "15min_return_1_ATR",
    "15min_return_5_ATR",
    "15min_return_20_ATR",
    # --- 15min: Streak & momentum ---
    "15min_up_streak",
    "15min_down_streak",
    "15min_autocorrelation",
    "15min_mean_reversion_ratio",
    # --- 15min: Volatility ---
    "15min_vol_regime",
    "15min_ATR",
    # --- 15min: Enhanced / engineered features ---
    "15min_momentum_score",
    "15min_momentum_acceleration",
    "15min_return_x_streak",
    "15min_return_x_vol_regime",
    "15min_close_position_in_range",
    "15min_price_z_score",
    "15min_vol_short_long_ratio",
    "15min_vol_breakout",
    "15min_time_since_spike",
    "15min_time_since_large_move",
    # --- 1h: Relative features ---
    "1h_range",
    "1h_body",
    "1h_return_1",
    "1h_return_5",
    "1h_return_20",
    "1h_return_1_ATR",
    "1h_return_5_ATR",
    "1h_return_20_ATR",
    # --- 1h: Streak & momentum ---
    "1h_up_streak",
    "1h_down_streak",
    "1h_autocorrelation",
    "1h_mean_reversion_ratio",
    # --- 1h: Volatility ---
    "1h_vol_regime",
    "1h_ATR",
    # --- 1h: Enhanced / engineered features ---
    "1h_momentum_score",
    "1h_momentum_acceleration",
    "1h_return_x_streak",
    "1h_return_x_vol_regime",
    "1h_close_position_in_range",
    "1h_price_z_score",
    "1h_vol_short_long_ratio",
    "1h_vol_breakout",
    "1h_time_since_spike",
    "1h_time_since_large_move",
    # --- Structural features (state-based, not cumulative counts) ---
    "nearest_structure_distance",
    "nearest_structure_size",
    "premium_discount_position",
]


def build_reduced_dataset(
    full_states: np.ndarray,
    full_targets: np.ndarray,
    full_timestamps: np.ndarray,
    full_feature_names: list[str],
    selected_features: list[str],
) -> tuple[np.ndarray, list[str]]:
    """
    Filter dataset to only selected features.
    """
    # Find indices of selected features
    name_to_idx = {name: i for i, name in enumerate(full_feature_names)}
    selected_indices = []
    missing = []
    for feat in selected_features:
        if feat in name_to_idx:
            selected_indices.append(name_to_idx[feat])
        else:
            missing.append(feat)

    if missing:
        logger.warning("missing_features %s", missing)

    selected_indices = sorted(selected_indices)
    selected_names = [full_feature_names[i] for i in selected_indices]

    reduced_states = full_states[:, selected_indices].copy()

    logger.info(
        "reduced_dataset features=%d -> %d (kept %d, dropped %d)",
        len(full_feature_names), len(selected_names),
        len(selected_names), len(full_feature_names) - len(selected_names),
    )

    return reduced_states, selected_names


# ---------------------------------------------------------------------------
# Training (same as model/train.py but with selected features)
# ---------------------------------------------------------------------------

def train_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    var_reg_weight: float = 0.1,
    var_grad_scale: float = 100.0,
) -> float:
    """
    Train for one epoch with gradient balancing for variance head.

    var_grad_scale compensates for the ~400x gradient imbalance between
    mean and variance heads discovered in diagnostics.
    """
    model.train()
    total_loss = 0.0
    n_batches = 0
    for states, targets in dataloader:
        states = states.to(device)
        targets = targets.to(device)
        optimizer.zero_grad()
        pred_mean, pred_log_var = model(states)
        loss = gaussian_nll_loss(pred_mean, pred_log_var, targets, var_reg_weight=var_reg_weight)
        loss.backward()

        # Gradient balancing: scale up variance head gradients
        if var_grad_scale != 1.0:
            for name, param in model.named_parameters():
                if param.grad is not None and "log_var" in name:
                    param.grad.data.mul_(var_grad_scale)

        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
        optimizer.step()
        total_loss += loss.item()
        n_batches += 1
    return total_loss / max(n_batches, 1)


@torch.no_grad()
def validate(model: nn.Module, dataloader: DataLoader, device: torch.device) -> dict[str, float]:
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


@torch.no_grad()
def compute_baseline_comparison(
    val_targets: np.ndarray,
    horizon: int,
    val_loader: DataLoader,
    model: nn.Module,
    device: torch.device,
) -> dict[str, dict[str, float]]:
    """Compare world model against baselines."""
    model.eval()
    all_pred_mean, all_pred_var = [], []
    for states, _ in val_loader:
        states = states.to(device)
        mean, log_var = model(states)
        all_pred_mean.append(mean.cpu().numpy())
        all_pred_var.append(torch.exp(log_var).cpu().numpy())

    wm_means = np.concatenate(all_pred_mean, axis=0)
    wm_vars = np.concatenate(all_pred_var, axis=0)

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
            "name": "",
        }

    wm = _metrics(wm_means, wm_vars, val_targets); wm["name"] = "world_model"

    persistence = _metrics(np.zeros_like(val_targets), np.ones_like(val_targets) * 1e-6, val_targets); persistence["name"] = "persistence"
    train_std = float(np.std(val_targets[:, 0]))
    steps = np.arange(1, horizon + 1, dtype=np.float64)
    rw = _metrics(np.zeros_like(val_targets), np.tile(train_std**2 * steps, (len(val_targets), 1)), val_targets); rw["name"] = "random_walk"

    theta = 0.1
    mr_vars = np.zeros_like(val_targets)
    for h in range(1, horizon + 1):
        decay = (1 - theta) ** h
        mr_vars[:, h - 1] = train_std**2 * (1 - decay**2) / (2 * theta)
    mr = _metrics(np.zeros_like(val_targets), mr_vars, val_targets); mr["name"] = "mean_reversion"

    return {"world_model": wm, "persistence": persistence, "random_walk": rw, "mean_reversion": mr}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def train_reduced_model(
    dataset_path: str = "data/dataset_R75_h10.npz",
    output_dir: str = "checkpoints_reduced",
    epochs: int = 100,
    batch_size: int = 256,
    lr: float = 1e-3,
    hidden_dim: int = 32,
    dropout: float = 0.1,
    weight_decay: float = 1e-4,
    val_split: float = 0.15,
    device_str: str | None = None,
) -> dict[str, Any]:
    """Train with reduced feature set."""
    device = torch.device(device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info("train_reduced.device=%s", device)

    # Load full dataset
    data = np.load(dataset_path, allow_pickle=True)
    full_states = data["states"]
    full_targets = data["targets"]
    full_timestamps = data["timestamps"]
    full_feature_names = list(data["feature_names"])
    horizon = full_targets.shape[1]

    logger.info("loaded full_dataset n_samples=%d n_features=%d", len(full_states), len(full_feature_names))

    # Build reduced dataset
    states, feature_names = build_reduced_dataset(
        full_states, full_targets, full_timestamps, full_feature_names, SELECTED_FEATURES
    )
    logger.info("reduced_features=%d: %s", len(feature_names), feature_names[:10])

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

    logger.info("split train=%d val=%d", n_train, n_val)

    train_loader = DataLoader(
        WorldModelDataset(train_states, train_targets, full_timestamps[:n_train]),
        batch_size=batch_size, shuffle=True, num_workers=0,
    )
    val_loader = DataLoader(
        WorldModelDataset(val_states, val_targets, full_timestamps[n_train:]),
        batch_size=batch_size, shuffle=False, num_workers=0,
    )

    # Build model
    model = build_model_from_features(
        feature_names, horizon=horizon, dropout=dropout,
        hidden_dim=hidden_dim, train_targets=train_targets,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(output_dir) / "world_model_best.pt"

    best_val_nll = float("inf")
    best_epoch = 0
    history = []

    logger.info("training epochs=%d features=%d", epochs, len(feature_names))

    for epoch in range(epochs):
        t0 = time.time()
        train_loss = train_epoch(model, train_loader, optimizer, device)
        scheduler.step()
        val_metrics = validate(model, val_loader, device)
        elapsed = time.time() - t0

        record = {
            "epoch": epoch, "train_loss": train_loss,
            "val_nll": val_metrics["val_nll"],
            "val_mae": val_metrics["val_mae"],
            "val_rmse": val_metrics["val_rmse"],
            "coverage_1sigma": val_metrics["coverage_1sigma"],
            "coverage_2sigma": val_metrics["coverage_2sigma"],
            "lr": optimizer.param_groups[0]["lr"],
            "elapsed": elapsed,
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
                optimizer.param_groups[0]["lr"], elapsed,
            )

        if val_metrics["val_nll"] < best_val_nll:
            best_val_nll = val_metrics["val_nll"]
            best_epoch = epoch
            # Load normalization stats from dataset (if present)
            norm_stats = {}
            if "feat_mean" in data and "feat_std" in data:
                # Map selected features to their normalized stats
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
                **norm_stats,
            }, save_path)

    # ---- Post-training: fit temperature on the full validation set ----
    # This single scalar T is saved in the checkpoint and applied at inference
    # to correct systematic over/under-confidence.
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
    squared_errors = (val_targets - val_means_np) ** 2
    temperature = float(np.mean(squared_errors) / np.mean(val_vars_np))
    temperature = max(temperature, 0.01)
    logger.info("temperature T=%.4f (model was %sconfident)",
                temperature,
                "over" if temperature > 1.0 else "under")

    # Re-save checkpoint with temperature + norm_stats
    torch.save({
        "model_state_dict": model.state_dict(),
        "config": model.config,
        "feature_names": feature_names,
        "epoch": best_epoch,
        "val_metrics": val_metrics,
        "horizon": horizon,
        "temperature": temperature,
        **norm_stats,
    }, save_path)

    logger.info("complete best_epoch=%d best_val_nll=%.4f saved=%s", best_epoch, best_val_nll, save_path)

    # Baselines
    baselines = compute_baseline_comparison(val_targets, horizon, val_loader, model, device)

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
        "features": feature_names,
    }

    results_path = Path(output_dir) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Print summary
    print("\n" + "=" * 60)
    print("REDUCED FEATURE MODEL TRAINING COMPLETE")
    print("=" * 60)
    print(f"Features: {len(feature_names)}")
    print(f"Best epoch: {best_epoch}")
    print(f"Best val NLL: {best_val_nll:.4f}")
    print(f"Model saved: {save_path}")
    print()
    print("Baseline comparison:")
    for name, m in baselines.items():
        print(f"  {name:20s}: NLL={m['val_nll']:.4f}  1sig={m['coverage_1sigma']:.1%}  2sig={m['coverage_2sigma']:.1%}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Train with reduced feature set")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--output-dir", default="checkpoints_reduced")
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-dim", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    train_reduced_model(
        dataset_path=args.dataset,
        output_dir=args.output_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        weight_decay=args.weight_decay,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
