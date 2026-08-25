"""
model/sweep_ultra.py — Ultra-fast hyperparameter sweep.

Uses subsampled train+val data and fewer epochs to get results in ~1 hour on CPU.

Key optimizations:
  - Subsample both train (1/8) and val (1/4) data
  - Only 30 epochs per config
  - 8 configs total

Usage:
    python -m model.sweep_ultra
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import torch
from torch.utils.data import DataLoader, TensorDataset

from model.world_model import (
    WorldModel, WorldModelConfig, FeatureGroup,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)

CONFIGS = [
    {"name": "A1_w48_d2_f2",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A2_w48_d3_f2",  "hidden_dim": 48, "branch_depth": 3, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A3_w64_d2_f2",  "hidden_dim": 64, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A4_w48_d2_f3",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 3, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B1_w48_lr5e4",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 5e-4,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B2_w48_drop05", "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.05, "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B3_w48_novreg", "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.0,  "weight_decay": 1e-4},
    {"name": "B4_w48_vreg05", "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.05, "weight_decay": 1e-4},
]

BASELINE_NLL = -4.8647
BASELINE_MAE = 0.001567


def build_model(cfg, feature_names, horizon, train_targets_np):
    groups = defaultdict(list)
    for i, name in enumerate(sorted(feature_names)):
        parts = name.split("_")
        if parts[0] in ("1min", "15min", "1h"):
            prefix = parts[0]
        else:
            prefix = "structure"
        groups[prefix].append((name, i))

    feature_groups = []
    for prefix in sorted(groups.keys()):
        items = groups[prefix]
        indices = [idx for _, idx in items]
        feature_groups.append(FeatureGroup(name=prefix, indices=indices, hidden_dim=cfg["hidden_dim"]))

    model_cfg = WorldModelConfig(
        n_features=len(feature_names),
        horizon=horizon,
        feature_groups=feature_groups,
        fusion_hidden=cfg["hidden_dim"] * 2,
        dropout=cfg["dropout"],
        head_hidden=cfg.get("head_hidden", cfg["hidden_dim"]),
        branch_depth=cfg["branch_depth"],
        fusion_depth=cfg["fusion_depth"],
    )
    model = WorldModel(model_cfg)
    model.init_variance_from_data(train_targets_np)
    return model


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/dataset_R75_h10.npz")
    parser.add_argument("--save-dir", default="checkpoints/sweep")
    parser.add_argument("--epochs", type=int, default=30)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("sweep.device=%s", device)

    # Load data
    data = np.load(args.data, allow_pickle=True)
    states = data["states"]
    targets = data["targets"]
    timestamps = data["timestamps"]
    feature_names_full = list(data["feature_names"])
    horizon = targets.shape[1]
    logger.info("sweep.dataset n=%d features=%d", len(states), states.shape[1])

    # Filter to original 89 features for fair comparison with baseline
    import torch as _torch
    baseline_ckpt = _torch.load("checkpoints/world_model_best.pt", map_location="cpu", weights_only=False)
    original_features = [str(f) for f in baseline_ckpt["feature_names"]]
    feat_to_idx = {name: i for i, name in enumerate(feature_names_full)}
    selected_idx = [feat_to_idx[f] for f in original_features if f in feat_to_idx]
    feature_names = [feature_names_full[i] for i in selected_idx]
    states = states[:, selected_idx]
    logger.info("sweep.filtered to %d original features (from %d)", len(feature_names), len(feature_names_full))

    # Temporal split
    sort_idx = np.argsort(timestamps)
    states, targets, timestamps = states[sort_idx], targets[sort_idx], timestamps[sort_idx]
    n_val = int(len(states) * 0.15)
    n_train = len(states) - n_val

    # Subsample BOTH train and val for speed
    TRAIN_SUB = 8  # 110K → ~14K
    VAL_SUB = 2    # 19K → ~10K
    train_s = states[:n_train:TRAIN_SUB]
    train_t = targets[:n_train:TRAIN_SUB]
    val_s = states[n_train::VAL_SUB]
    val_t = targets[n_train::VAL_SUB]
    logger.info("sweep.split train=%d val=%d", len(train_s), len(val_s))

    # Convert to tensors once
    train_s_t = torch.tensor(train_s, dtype=torch.float32)
    train_t_t = torch.tensor(train_t, dtype=torch.float32)
    val_s_t = torch.tensor(val_s, dtype=torch.float32)
    val_t_t = torch.tensor(val_t, dtype=torch.float32)

    train_ds = TensorDataset(train_s_t, train_t_t)
    val_ds = TensorDataset(val_s_t, val_t_t)

    results = []

    for i, cfg in enumerate(CONFIGS):
        name = cfg["name"]
        logger.info("sweep.run %d/%d: %s", i + 1, len(CONFIGS), name)
        t0 = time.time()

        model = build_model(cfg, feature_names, horizon, train_t).to(device)
        n_params = sum(p.numel() for p in model.parameters())

        train_loader = DataLoader(train_ds, batch_size=256, shuffle=True, num_workers=0)
        val_loader = DataLoader(val_ds, batch_size=512, shuffle=False, num_workers=0)

        optimizer = torch.optim.AdamW(model.parameters(), lr=cfg["lr"], weight_decay=cfg["weight_decay"])
        scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, eta_min=1e-6)

        best_nll = float("inf")
        best_epoch = 0
        best_mae = 0.0
        best_cov1 = 0.0
        best_cov2 = 0.0

        for epoch in range(args.epochs):
            # Train
            model.train()
            for sb, tb in train_loader:
                sb, tb = sb.to(device), tb.to(device)
                optimizer.zero_grad()
                mean, log_var = model(sb)
                loss = gaussian_nll_loss(mean, log_var, tb, var_reg_weight=cfg["var_reg"])
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                optimizer.step()
            scheduler.step()

            # Validate
            model.eval()
            with torch.no_grad():
                all_m, all_v, all_t = [], [], []
                for sb, tb in val_loader:
                    sb, tb = sb.to(device), tb.to(device)
                    mean, log_var = model(sb)
                    all_m.append(mean.cpu().numpy())
                    all_v.append(torch.exp(log_var).cpu().numpy())
                    all_t.append(tb.cpu().numpy())

            means_np = np.concatenate(all_m)
            vars_np = np.concatenate(all_v)
            targets_np = np.concatenate(all_t)

            eps = 1e-8
            nll = float(np.mean(0.5 * (np.log(2 * np.pi * vars_np) + (targets_np - means_np) ** 2 / (vars_np + eps))))
            mae = float(np.mean(np.abs(targets_np - means_np)))
            stds = np.sqrt(vars_np)
            cov1 = float(np.mean(np.abs(targets_np - means_np) <= stds))
            cov2 = float(np.mean(np.abs(targets_np - means_np) <= 2 * stds))

            if nll < best_nll:
                best_nll = nll
                best_epoch = epoch
                best_mae = mae
                best_cov1 = cov1
                best_cov2 = cov2

            if epoch % 10 == 0 or epoch == args.epochs - 1:
                elapsed = time.time() - t0
                logger.info("  [%s] ep %2d/%d NLL=%.4f MAE=%.6f 1s=%.1f%% 2s=%.1f%% (%.0fs)",
                            name, epoch + 1, args.epochs, nll, mae, cov1*100, cov2*100, elapsed)

        elapsed = time.time() - t0
        results.append({
            "name": name,
            "config": cfg,
            "n_params": n_params,
            "best_epoch": best_epoch,
            "best_nll": best_nll,
            "best_mae": best_mae,
            "best_cov1": best_cov1,
            "best_cov2": best_cov2,
            "elapsed_s": elapsed,
        })
        logger.info("sweep.done %s NLL=%.4f (Δ=%.4f) time=%.0fs",
                     name, best_nll, best_nll - BASELINE_NLL, elapsed)

    # Sort and report
    results.sort(key=lambda x: x["best_nll"])

    logger.info("\n" + "=" * 70)
    logger.info("SWEEP RESULTS (sorted by val NLL)")
    logger.info("=" * 70)
    logger.info(f"{'Name':<25s} {'NLL':>8s} {'Delta':>8s} {'MAE':>10s} {'1sig':>6s} {'2sig':>6s} {'Params':>7s}")
    logger.info("-" * 72)
    logger.info(f"{'BASELINE_ref':<25s} {BASELINE_NLL:8.4f} {'---':>8s} {BASELINE_MAE:10.6f} {'69.2%':>6s} {'96.0%':>6s} {'23188':>7s}")

    for r in results:
        delta = r["best_nll"] - BASELINE_NLL
        marker = " <<< BETTER" if delta < -0.001 else ""
        logger.info(
            f"{r['name']:<25s} {r['best_nll']:8.4f} {delta:+8.4f} {r['best_mae']:10.6f} "
            f"{r['best_cov1']:5.1%} {r['best_cov2']:5.1%} {r['n_params']:7d}{marker}"
        )

    best = results[0]
    delta = best["best_nll"] - BASELINE_NLL
    logger.info(f"\nBest: {best['name']} NLL={best['best_nll']:.4f} (delta={delta:+.4f})")
    logger.info(f"Config: {json.dumps(best['config'], indent=2)}")

    # Save
    Path(args.save_dir).mkdir(parents=True, exist_ok=True)
    with open(Path(args.save_dir) / "sweep_final.json", "w") as f:
        json.dump({"baseline_nll": BASELINE_NLL, "results": results, "best": best}, f, indent=2, default=str)
    logger.info("Saved to %s/sweep_final.json", args.save_dir)


if __name__ == "__main__":
    main()
