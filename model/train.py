"""
model/train.py — Training loop for the world model.

Trains with Gaussian NLL loss, validates with calibration metrics,
and compares against baselines.

Usage:
    python -m model.train --data data/candles_R75_1min.parquet
    python -m model.train --data data/candles_R75_1min.parquet --horizon 10 --epochs 50
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
from torch.utils.data import DataLoader, Subset

from model.dataset import WorldModelDataset, build_dataset
from model.world_model import (
    WorldModel,
    WorldModelConfig,
    build_model_from_features,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Training utilities# ---------------------------------------------------------------------------
def train_epoch(
    model: nn.Module,
    dataloader: DataLoader,
    optimizer: torch.optim.Optimizer,
    device: torch.device,
    var_reg_weight: float = 0.1,
    var_grad_scale: float = 100.0,
) -> float:
    """
    Train for one epoch. Returns average loss.

    Parameters
    ----------
    var_reg_weight : float
        Weight for variance regularization (penalize deviation from empirical var).
    var_grad_scale : float
        Scale factor for variance head gradients to balance with mean head.
        Diagnostic showed mean/var gradient ratio = 400x, so we scale up
        variance gradients to compensate.
    """
    model.train()
    total_loss = 0.0
    n_batches = 0

    for states, targets in dataloader:
        states = states.to(device)
        targets = targets.to(device)

        optimizer.zero_grad()
        pred_mean, pred_log_var = model(states)
        loss = gaussian_nll_loss(
            pred_mean, pred_log_var, targets,
            var_reg_weight=var_reg_weight,
        )
        loss.backward()

        # Gradient balancing: scale up variance head gradients
        # This compensates for the 400x gradient imbalance discovered in diagnostics.
        if var_grad_scale != 1.0:
            for name, param in model.named_parameters():
                if param.grad is not None and "log_var" in name:
                    param.grad.data.mul_(var_grad_scale)

        # Gradient clipping
        torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)

        optimizer.step()
        total_loss += loss.item()
        n_batches += 1

    return total_loss / max(n_batches, 1)


@torch.no_grad()
def validate(
    model: nn.Module,
    dataloader: DataLoader,
    device: torch.device,
) -> dict[str, float]:
    """Validate and compute metrics. Returns dict of metric values."""
    model.eval()
    all_means = []
    all_vars = []
    all_targets = []

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

    # Compute metrics
    metrics = {}

    # NLL
    eps = 1e-8
    nll = 0.5 * (np.log(2 * np.pi * variances) + (targets - means) ** 2 / (variances + eps))
    metrics["val_nll"] = float(np.mean(nll))

    # MAE
    metrics["val_mae"] = float(np.mean(np.abs(targets - means)))

    # MSE
    metrics["val_mse"] = float(np.mean((targets - means) ** 2))

    # Calibration: what fraction of targets fall within 1σ, 2σ intervals?
    stds = np.sqrt(variances)
    within_1sigma = np.mean(np.abs(targets - means) <= stds)
    within_2sigma = np.mean(np.abs(targets - means) <= 2 * stds)
    metrics["coverage_1sigma"] = float(within_1sigma)  # target: ~68%
    metrics["coverage_2sigma"] = float(within_2sigma)  # target: ~95%

    # RMSE
    metrics["val_rmse"] = float(np.sqrt(metrics["val_mse"]))

    # Mean predicted uncertainty
    metrics["mean_predicted_std"] = float(np.mean(stds))
    metrics["actual_std"] = float(np.std(targets))

    return metrics


def compute_baseline_metrics(
    means: np.ndarray,
    variances: np.ndarray,
    targets: np.ndarray,
) -> dict[str, float]:
    """Compute the same metrics for baseline predictions for comparison."""
    eps = 1e-8
    nll = 0.5 * (np.log(2 * np.pi * variances + eps) + (targets - means) ** 2 / (variances + eps))
    stds = np.sqrt(variances + eps)

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


# ---------------------------------------------------------------------------
# Main training pipeline
# ---------------------------------------------------------------------------

def train_world_model(
    data_path: str,
    horizon: int = 10,
    epochs: int = 50,
    batch_size: int = 256,
    lr: float = 1e-3,
    hidden_dim: int = 32,
    dropout: float = 0.1,
    val_split: float = 0.15,
    save_dir: str = "checkpoints",
    device_str: str | None = None,
    var_reg_weight: float = 0.01,
) -> dict[str, Any]:
    """
    Full training pipeline.

    1. Load data
    2. Build dataset (states + targets)
    3. Train/val split
    4. Train with NLL loss
    5. Evaluate and compare to baselines
    6. Save best model

    Returns
    -------
    dict with training results, metrics, and model path
    """
    device = torch.device(
        device_str
        if device_str
        else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    logger.info("train.device %s", device)

    # ---- 1. Load precomputed dataset or build from scratch ----
    precomputed_path = Path(data_path).parent / f"dataset_R75_h{horizon}.npz"
    
    if precomputed_path.exists():
        logger.info("train.loading_precomputed path=%s", precomputed_path)
        data = np.load(str(precomputed_path), allow_pickle=True)
        states = data["states"]
        targets = data["targets"]
        timestamps = data["timestamps"]
        feature_names = list(data["feature_names"])
        logger.info(
            "train.dataset_loaded n_samples=%d n_features=%d",
            len(states), states.shape[1],
        )
    else:
        logger.info("train.loading_data path=%s", data_path)
        candles = pd.read_parquet(data_path, engine="pyarrow")
        candles["timestamp"] = pd.to_datetime(candles["timestamp"], utc=True).dt.tz_localize(None)
        candles = candles.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        logger.info("train.data_loaded n_bars=%d", len(candles))

        # Build state builder
        from features.state import StateBuilder
        structs_path = str(Path(data_path).parent / "structures_R75.parquet")
        logger.info("train.building_state_builder structs=%s", structs_path)
        t0 = time.time()
        sb = StateBuilder(candles, structures_path=structs_path)
        logger.info("train.state_builder_ready elapsed=%.1fs", time.time() - t0)

        logger.info("train.building_dataset horizon=%d", horizon)
        t0 = time.time()
        states, targets, timestamps = build_dataset(
            candles, sb, horizon=horizon, min_lookback=100
        )
        feature_names = sorted(
            {k for k in sb.build_state(candles.timestamp.iloc[100]).keys()}
        )
        logger.info(
            "train.dataset_built n_samples=%d n_features=%d elapsed=%.1fs",
            len(states), states.shape[1], time.time() - t0,
        )

    # ---- 4. Train/val split (temporal) ----
    n_val = int(len(states) * val_split)
    n_train = len(states) - n_val

    # Sort by timestamp to ensure temporal split
    sort_idx = np.argsort(timestamps)
    states = states[sort_idx]
    targets = targets[sort_idx]
    timestamps = timestamps[sort_idx]

    train_states = states[:n_train]
    train_targets = targets[:n_train]
    val_states = states[n_train:]
    val_targets = targets[n_train:]

    logger.info(
        "train.split train=%d val=%d", n_train, n_val
    )

    train_dataset = WorldModelDataset(train_states, train_targets, timestamps[:n_train])
    val_dataset = WorldModelDataset(val_states, val_targets, timestamps[n_train:])

    train_loader = DataLoader(
        train_dataset, batch_size=batch_size, shuffle=True,
        num_workers=0, pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_dataset, batch_size=batch_size, shuffle=False,
        num_workers=0, pin_memory=(device.type == "cuda"),
    )

    # ---- 5. Build model (with variance initialization from data) ----
    model = build_model_from_features(
        feature_names, horizon=horizon, dropout=dropout, hidden_dim=hidden_dim,
        train_targets=train_targets,
    ).to(device)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    # ---- 6. Training loop ----
    best_val_nll = float("inf")
    best_epoch = 0
    history: list[dict[str, Any]] = []

    Path(save_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(save_dir) / "world_model_best.pt"

    logger.info("train.starting_training epochs=%d", epochs)

    for epoch in range(epochs):
        t0 = time.time()

        # Train
        train_loss = train_epoch(model, train_loader, optimizer, device, var_reg_weight=var_reg_weight)
        scheduler.step()

        # Validate
        val_metrics = validate(model, val_loader, device)

        elapsed = time.time() - t0

        record = {
            "epoch": epoch,
            "train_loss": train_loss,
            "val_nll": val_metrics["val_nll"],
            "val_mae": val_metrics["val_mae"],
            "val_rmse": val_metrics["val_rmse"],
            "coverage_1sigma": val_metrics["coverage_1sigma"],
            "coverage_2sigma": val_metrics["coverage_2sigma"],
            "lr": optimizer.param_groups[0]["lr"],
            "elapsed": elapsed,
        }
        history.append(record)

        # Log
        if epoch % 5 == 0 or epoch == epochs - 1:
            logger.info(
                "train.epoch %d/%d loss=%.4f val_nll=%.4f val_mae=%.6f "
                "cov1σ=%.2f%% cov2σ=%.2f%% lr=%.2e time=%.1fs",
                epoch + 1, epochs, train_loss,
                val_metrics["val_nll"], val_metrics["val_mae"],
                val_metrics["coverage_1sigma"] * 100,
                val_metrics["coverage_2sigma"] * 100,
                optimizer.param_groups[0]["lr"],
                elapsed,
            )

        # Save best
        if val_metrics["val_nll"] < best_val_nll:
            best_val_nll = val_metrics["val_nll"]
            best_epoch = epoch
            torch.save(
                {
                    "model_state_dict": model.state_dict(),
                    "config": model.config,
                    "feature_names": feature_names,
                    "epoch": epoch,
                    "val_metrics": val_metrics,
                    "horizon": horizon,
                },
                save_path,
            )

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
    # T = E[(y-mu)^2] / E[sigma^2] — closed-form optimal for Gaussian NLL
    squared_errors = (val_targets - val_means_np) ** 2
    temperature = float(np.mean(squared_errors) / np.mean(val_vars_np))
    temperature = max(temperature, 0.01)  # clamp degenerate cases
    logger.info("train.temperature T=%.4f (model was %sconfident)",
                temperature,
                "over" if temperature > 1.0 else "under")

    # Re-save checkpoint with temperature included
    torch.save(
        {
            "model_state_dict": model.state_dict(),
            "config": model.config,
            "feature_names": feature_names,
            "epoch": best_epoch,
            "val_metrics": val_metrics,
            "horizon": horizon,
            "temperature": temperature,
        },
        save_path,
    )

    logger.info(
        "train.complete best_epoch=%d best_val_nll=%.4f saved=%s",
        best_epoch, best_val_nll, save_path,
    )

    # ---- 7. Compare against baselines ----
    logger.info("train.computing_baselines")
    baseline_comparison = _compare_baselines(
        val_states, val_targets, val_loader, model, device
    )

    results = {
        "best_epoch": best_epoch,
        "best_val_nll": best_val_nll,
        "history": history,
        "val_metrics": val_metrics,
        "baselines": baseline_comparison,
        "model_path": str(save_path),
        "n_train": n_train,
        "n_val": n_val,
        "horizon": horizon,
    }

    # Save results
    results_path = Path(save_dir) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    logger.info("train.results_saved path=%s", results_path)

    return results


def _compare_baselines(
    val_states: np.ndarray,
    val_targets: np.ndarray,
    val_loader: DataLoader,
    model: nn.Module,
    device: torch.device,
) -> dict[str, dict[str, float]]:
    """Compare world model against naive baselines on the validation set."""
    from eval.baselines import (
        PersistenceBaseline,
        MeanReversionBaseline,
        RandomWalkBaseline,
    )

    # Get world model predictions
    model.eval()
    with torch.no_grad():
        all_pred_mean = []
        all_pred_var = []
        for states, _ in val_loader:
            states = states.to(device)
            mean, log_var = model(states)
            all_pred_mean.append(mean.cpu().numpy())
            all_pred_var.append(torch.exp(log_var).cpu().numpy())

    wm_means = np.concatenate(all_pred_mean, axis=0)
    wm_vars = np.concatenate(all_pred_var, axis=0)

    wm_metrics = compute_baseline_metrics(wm_means, wm_vars, val_targets)
    wm_metrics["name"] = "world_model"

    # Persistence baseline: predict 0 return at all horizons
    persistence_means = np.zeros_like(val_targets)
    persistence_vars = np.ones_like(val_targets) * 0.001  # tiny variance
    persistence_metrics = compute_baseline_metrics(
        persistence_means, persistence_vars, val_targets
    )
    persistence_metrics["name"] = "persistence"

    # Random walk: predict 0 mean, variance grows with horizon
    horizon = val_targets.shape[1]
    steps = np.arange(1, horizon + 1, dtype=np.float32)
    # Estimate volatility from training data
    train_std = float(np.std(val_targets[:, 0])) if len(val_targets) > 0 else 0.001
    rw_means = np.zeros_like(val_targets)
    rw_vars = np.tile(train_std**2 * steps, (len(val_targets), 1))
    rw_metrics = compute_baseline_metrics(rw_means, rw_vars, val_targets)
    rw_metrics["name"] = "random_walk"

    # Mean reversion baseline
    # Use expanding window mean from the targets themselves (approximation)
    mr_means = np.zeros_like(val_targets)
    mr_vars = np.ones_like(val_targets) * train_std**2
    # Simple mean reversion: each step reverts by theta toward 0
    theta = 0.1
    for h in range(1, horizon + 1):
        mr_means[:, h - 1] = (1 - (1 - theta) ** h) * 0  # reverts to 0
        mr_vars[:, h - 1] = train_std**2 * (1 - (1 - 2 * theta) ** h) / (2 * theta)
    mr_metrics = compute_baseline_metrics(mr_means, mr_vars, val_targets)
    mr_metrics["name"] = "mean_reversion"

    return {
        "world_model": wm_metrics,
        "persistence": persistence_metrics,
        "random_walk": rw_metrics,
        "mean_reversion": mr_metrics,
    }


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Train the world model")
    parser.add_argument("--data", default="data/candles_R75_1min.parquet")
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden-dim", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument("--save-dir", default="checkpoints")
    parser.add_argument("--var-reg-weight", type=float, default=0.01,
                        help="Variance regularization weight (0 to disable)")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    results = train_world_model(
        data_path=args.data,
        horizon=args.horizon,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        var_reg_weight=args.var_reg_weight,
        hidden_dim=args.hidden_dim,
        dropout=args.dropout,
        val_split=args.val_split,
        save_dir=args.save_dir,
        device_str=args.device,
    )

    # Print summary
    print("\n" + "=" * 60)
    print("TRAINING COMPLETE")
    print("=" * 60)
    print(f"Best epoch: {results['best_epoch']}")
    print(f"Best val NLL: {results['best_val_nll']:.4f}")
    print(f"Model saved: {results['model_path']}")
    print()
    print("Baseline comparison (val NLL — lower is better):")
    for name, metrics in results["baselines"].items():
        print(f"  {name:20s}: NLL={metrics['val_nll']:.4f}  MAE={metrics['val_mae']:.6f}  "
              f"1sigma cov={metrics['coverage_1sigma']:.1%}  2sigma cov={metrics['coverage_2sigma']:.1%}")


if __name__ == "__main__":
    main()
