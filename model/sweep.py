"""
model/sweep.py — Hyperparameter sweep for the world model.

Systematically varies architecture and training hyperparameters to find
configurations that improve beyond the current best val NLL of -4.8647.

Configs run sequentially, results saved to sweep_results.json.

Usage:
    python -m model.sweep
    python -m model.sweep --data data/dataset_R75_h10.npz
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from itertools import product
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader

from model.dataset import WorldModelDataset
from model.world_model import (
    build_model_from_features,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sweep configs — each is a dict of hyperparameters
# ---------------------------------------------------------------------------
# Current baseline: hidden=32, branch_depth=2, fusion_depth=2, dropout=0.1, lr=1e-3, var_reg=0.01 → NLL=-4.8647

SWEEP_CONFIGS = [
    # === Architecture: vary hidden_dim × branch_depth (8 configs) ===
    {"name": "A1_w48_d2_f2",     "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A2_w48_d3_f2",     "hidden_dim": 48, "branch_depth": 3, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A3_w64_d2_f2",     "hidden_dim": 64, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A4_w48_d2_f3",     "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 3, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},

    # === Training: best arch variants × lr/dropout (4 configs) ===
    {"name": "B1_w48_lr5e4",      "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 5e-4,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B2_w48_drop05",     "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.05, "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B3_w48_novreg",     "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.0,  "weight_decay": 1e-4},
    {"name": "B4_w48_vreg05",     "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.05, "weight_decay": 1e-4},
]


# ---------------------------------------------------------------------------
# Training function (self-contained)
# ---------------------------------------------------------------------------
def train_config(
    config: dict[str, Any],
    train_states: np.ndarray,
    train_targets: np.ndarray,
    val_states: np.ndarray,
    val_targets: np.ndarray,
    feature_names: list[str],
    horizon: int,
    device: torch.device,
    epochs: int = 100,
    batch_size: int = 256,
) -> dict[str, Any]:
    """Train a single config and return metrics."""
    name = config["name"]
    hidden_dim = config["hidden_dim"]
    branch_depth = config["branch_depth"]
    fusion_depth = config["fusion_depth"]
    dropout = config["dropout"]
    lr = config["lr"]
    var_reg = config["var_reg"]
    weight_decay = config.get("weight_decay", 1e-4)
    head_hidden = config.get("head_hidden", hidden_dim)

    t0 = time.time()

    # Build model
    from model.world_model import WorldModelConfig, FeatureGroup, WorldModel
    from collections import defaultdict

    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, feat_name in enumerate(sorted(feature_names)):
        parts = feat_name.split("_")
        if parts[0] in ("1min", "15min", "1h"):
            prefix = parts[0]
        elif parts[0] in ("n", "nearest", "premium", "structure"):
            prefix = "structure"
        else:
            prefix = "other"
        groups[prefix].append((feat_name, i))

    feature_groups = []
    for prefix in sorted(groups.keys()):
        items = groups[prefix]
        indices = [idx for _, idx in items]
        feature_groups.append(FeatureGroup(name=prefix, indices=indices, hidden_dim=hidden_dim))

    config_obj = WorldModelConfig(
        n_features=len(feature_names),
        horizon=horizon,
        feature_groups=feature_groups,
        fusion_hidden=hidden_dim * 2,
        dropout=dropout,
        head_hidden=head_hidden,
        branch_depth=branch_depth,
        fusion_depth=fusion_depth,
    )
    model = WorldModel(config_obj).to(device)
    model.init_variance_from_data(train_targets)

    n_params = sum(p.numel() for p in model.parameters())

    # Data loaders
    # Create dummy timestamps (not used in training, but required by WorldModelDataset)
    train_ts = np.zeros(len(train_states), dtype='datetime64[ns]')
    val_ts = np.zeros(len(val_states), dtype='datetime64[ns]')
    train_ds = WorldModelDataset(train_states, train_targets, train_ts)
    val_ds = WorldModelDataset(val_states, val_targets, val_ts)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    # Optimizer + scheduler
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)

    # Training loop
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
            loss = gaussian_nll_loss(mean, log_var, targets_b, var_reg_weight=var_reg)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1
        scheduler.step()
        train_loss = total_loss / max(n_batches, 1)

        # Validate
        model.eval()
        all_means, all_vars, all_targets = [], [], []
        with torch.no_grad():
            for states_b, targets_b in val_loader:
                states_b = states_b.to(device)
                targets_b = targets_b.to(device)
                mean, log_var = model(states_b)
                all_means.append(mean.cpu().numpy())
                all_vars.append(torch.exp(log_var).cpu().numpy())
                all_targets.append(targets_b.cpu().numpy())

        means_np = np.concatenate(all_means, axis=0)
        vars_np = np.concatenate(all_vars, axis=0)
        targets_np = np.concatenate(all_targets, axis=0)

        eps = 1e-8
        nll_vals = 0.5 * (np.log(2 * np.pi * vars_np) + (targets_np - means_np) ** 2 / (vars_np + eps))
        val_nll = float(np.mean(nll_vals))
        val_mae = float(np.mean(np.abs(targets_np - means_np)))
        stds = np.sqrt(vars_np)
        cov1 = float(np.mean(np.abs(targets_np - means_np) <= stds))
        cov2 = float(np.mean(np.abs(targets_np - means_np) <= 2 * stds))

        history.append({
            "epoch": epoch, "train_loss": train_loss,
            "val_nll": val_nll, "val_mae": val_mae,
            "cov1": cov1, "cov2": cov2,
        })

        if val_nll < best_val_nll:
            best_val_nll = val_nll
            best_epoch = epoch
            best_state = {k: v.clone() for k, v in model.state_dict().items()}

        if epoch % 20 == 0 or epoch == epochs - 1:
            elapsed = time.time() - t0
            logger.info(
                "  [%s] epoch %3d/%d NLL=%.4f MAE=%.6f cov1=%.1f%% time=%.0fs",
                name, epoch + 1, epochs, val_nll, val_mae, cov1 * 100, elapsed,
            )

    elapsed = time.time() - t0

    # Final metrics at best epoch
    best_entry = history[best_epoch]

    return {
        "name": name,
        "hidden_dim": hidden_dim,
        "branch_depth": branch_depth,
        "fusion_depth": fusion_depth,
        "dropout": dropout,
        "lr": lr,
        "var_reg": var_reg,
        "weight_decay": weight_decay,
        "head_hidden": head_hidden,
        "n_params": n_params,
        "best_epoch": best_epoch,
        "best_val_nll": best_val_nll,
        "val_mae": best_entry["val_mae"],
        "cov1": best_entry["cov1"],
        "cov2": best_entry["cov2"],
        "elapsed_s": elapsed,
    }


# ---------------------------------------------------------------------------
# Main sweep
# ---------------------------------------------------------------------------
def run_sweep(
    dataset_path: str = "data/dataset_R75_h10.npz",
    save_dir: str = "checkpoints/sweep",
    epochs: int = 100,
    batch_size: int = 256,
    val_split: float = 0.15,
    device_str: str | None = None,
) -> list[dict]:
    """Run the full hyperparameter sweep."""
    device = torch.device(
        device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    logger.info("sweep.device=%s", device)

    # Load dataset
    data = np.load(dataset_path, allow_pickle=True)
    states = data["states"]
    targets = data["targets"]
    timestamps = data["timestamps"]
    feature_names = list(data["feature_names"])
    horizon = targets.shape[1]

    logger.info("sweep.dataset n=%d features=%d horizon=%d", len(states), states.shape[1], horizon)

    # Temporal split
    sort_idx = np.argsort(timestamps)
    states = states[sort_idx]
    targets = targets[sort_idx]
    timestamps = timestamps[sort_idx]

    n_val = int(len(states) * val_split)
    n_train = len(states) - n_val
    train_states = states[:n_train]
    train_targets = targets[:n_train]
    val_states = states[n_train:]
    val_targets = targets[n_train:]

    logger.info("sweep.split train=%d val=%d", n_train, n_val)

    # Add baseline (current best) for reference
    baseline = {
        "name": "BASELINE_current",
        "hidden_dim": 32, "branch_depth": 2, "fusion_depth": 2,
        "dropout": 0.1, "lr": 1e-3, "var_reg": 0.01, "weight_decay": 1e-4,
        "head_hidden": 32, "n_params": 23188,
        "best_epoch": -1, "best_val_nll": -4.8647,
        "val_mae": 0.001567, "cov1": 0.692, "cov2": 0.960,
        "elapsed_s": 0,
    }

    results = [baseline]

    # Run each config
    for i, cfg in enumerate(SWEEP_CONFIGS):
        logger.info("sweep.running %d/%d: %s", i + 1, len(SWEEP_CONFIGS), cfg["name"])
        t0 = time.time()

        try:
            result = train_config(
                config=cfg,
                train_states=train_states,
                train_targets=train_targets,
                val_states=val_states,
                val_targets=val_targets,
                feature_names=feature_names,
                horizon=horizon,
                device=device,
                epochs=epochs,
                batch_size=batch_size,
            )
            results.append(result)

            elapsed = time.time() - t0
            improvement = result["best_val_nll"] - baseline["best_val_nll"]
            logger.info(
                "sweep.done %s NLL=%.4f (Δ=%.4f) MAE=%.6f params=%d time=%.0fs",
                cfg["name"], result["best_val_nll"], improvement,
                result["val_mae"], result["n_params"], elapsed,
            )
        except Exception as e:
            logger.error("sweep.failed %s error=%s", cfg["name"], str(e))
            results.append({"name": cfg["name"], "error": str(e)})

        # Save intermediate results
        Path(save_dir).mkdir(parents=True, exist_ok=True)
        results_path = Path(save_dir) / "sweep_results.json"
        with open(results_path, "w") as f:
            json.dump(results, f, indent=2, default=str)

    # Sort by NLL
    valid_results = [r for r in results if "error" not in r and "BASELINE" not in r["name"]]
    valid_results.sort(key=lambda x: x["best_val_nll"])

    logger.info("\n" + "=" * 70)
    logger.info("SWEEP RESULTS (sorted by val NLL)")
    logger.info("=" * 70)
    logger.info(f"{'Name':<25s} {'NLL':>8s} {'Δ':>8s} {'MAE':>10s} {'1sig':>6s} {'2sig':>6s} {'Params':>8s}")
    logger.info("-" * 70)

    # Baseline first
    b = baseline
    logger.info(f"{'BASELINE':<25s} {b['best_val_nll']:8.4f} {'---':>8s} {b['val_mae']:10.6f} {b['cov1']:5.1%} {b['cov2']:5.1%} {b['n_params']:8d}")

    for r in valid_results:
        delta = r["best_val_nll"] - baseline["best_val_nll"]
        marker = " <-- BETTER" if delta < -0.001 else ""
        logger.info(
            f"{r['name']:<25s} {r['best_val_nll']:8.4f} {delta:+8.4f} {r['val_mae']:10.6f} {r['cov1']:5.1%} {r['cov2']:5.1%} {r['n_params']:8d}{marker}"
        )

    if valid_results:
        best = valid_results[0]
        delta = best["best_val_nll"] - baseline["best_val_nll"]
        logger.info(f"\nBest config: {best['name']}")
        logger.info(f"  NLL: {best['best_val_nll']:.4f} (Δ={delta:+.4f} vs baseline)")
        logger.info(f"  hidden_dim={best['hidden_dim']}, branch_depth={best['branch_depth']}, "
                     f"fusion_depth={best['fusion_depth']}, dropout={best['dropout']}")
        logger.info(f"  lr={best['lr']}, var_reg={best['var_reg']}, wd={best['weight_decay']}")

    # Save final sorted results
    final_path = Path(save_dir) / "sweep_final.json"
    with open(final_path, "w") as f:
        json.dump({"baseline": baseline, "results": valid_results}, f, indent=2, default=str)

    return results


def main():
    parser = argparse.ArgumentParser(description="Hyperparameter sweep for world model")
    parser.add_argument("--data", default="data/dataset_R75_h10.npz")
    parser.add_argument("--save-dir", default="checkpoints/sweep")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=256)
    parser.add_argument("--val-split", type=float, default=0.15)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_sweep(
        dataset_path=args.data,
        save_dir=args.save_dir,
        epochs=args.epochs,
        batch_size=args.batch_size,
        val_split=args.val_split,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
