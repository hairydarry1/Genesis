"""
model/sweep_fast.py — Two-phase hyperparameter sweep.

Phase 1: Quick 20-epoch scan of 8 configs (~30 min total on CPU)
Phase 2: Full 100-epoch training of the top 3 configs (~2.5 hrs)

Usage:
    python -m model.sweep_fast
"""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch
from torch.utils.data import DataLoader

from model.dataset import WorldModelDataset
from model.world_model import (
    WorldModel, WorldModelConfig, FeatureGroup,
    gaussian_nll_loss,
)

logger = logging.getLogger(__name__)

# All configs to scan
SWEEP_CONFIGS = [
    # Baseline reference
    {"name": "BASELINE",    "hidden_dim": 32, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    # Architecture variants
    {"name": "A1_w48_d2_f2",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A2_w48_d3_f2",  "hidden_dim": 48, "branch_depth": 3, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A3_w64_d2_f2",  "hidden_dim": 64, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "A4_w48_d2_f3",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 3, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    # Training variants
    {"name": "B1_w48_lr5e4",  "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 5e-4,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B2_w48_drop05", "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.05, "lr": 1e-3,  "var_reg": 0.01, "weight_decay": 1e-4},
    {"name": "B3_w48_novreg", "hidden_dim": 48, "branch_depth": 2, "fusion_depth": 2, "dropout": 0.1,  "lr": 1e-3,  "var_reg": 0.0,  "weight_decay": 1e-4},
]


def build_model(config: dict, feature_names: list[str], horizon: int, train_targets: np.ndarray) -> WorldModel:
    """Build a model from config dict."""
    groups: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for i, name in enumerate(sorted(feature_names)):
        parts = name.split("_")
        if parts[0] in ("1min", "15min", "1h"):
            prefix = parts[0]
        elif parts[0] in ("n", "nearest", "premium", "structure"):
            prefix = "structure"
        else:
            prefix = "other"
        groups[prefix].append((name, i))

    feature_groups = []
    for prefix in sorted(groups.keys()):
        items = groups[prefix]
        indices = [idx for _, idx in items]
        feature_groups.append(FeatureGroup(name=prefix, indices=indices, hidden_dim=config["hidden_dim"]))

    cfg = WorldModelConfig(
        n_features=len(feature_names),
        horizon=horizon,
        feature_groups=feature_groups,
        fusion_hidden=config["hidden_dim"] * 2,
        dropout=config["dropout"],
        head_hidden=config.get("head_hidden", config["hidden_dim"]),
        branch_depth=config["branch_depth"],
        fusion_depth=config["fusion_depth"],
    )
    model = WorldModel(cfg)
    model.init_variance_from_data(train_targets)
    return model


def train_single(config: dict, train_states, train_targets, val_states, val_targets,
                  feature_names, horizon, device, epochs, batch_size) -> dict:
    """Train one config, return results."""
    t0 = time.time()
    model = build_model(config, feature_names, horizon, train_targets).to(device)
    n_params = sum(p.numel() for p in model.parameters())

    # Data loaders
    dummy_ts = np.zeros(len(train_states), dtype="datetime64[ns]")
    dummy_ts_v = np.zeros(len(val_states), dtype="datetime64[ns]")
    train_loader = DataLoader(WorldModelDataset(train_states, train_targets, dummy_ts),
                              batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(WorldModelDataset(val_states, val_targets, dummy_ts_v),
                            batch_size=batch_size, shuffle=False, num_workers=0)

    optimizer = torch.optim.AdamW(model.parameters(), lr=config["lr"], weight_decay=config["weight_decay"])
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-6)

    best_nll = float("inf")
    best_epoch = 0
    best_cov1 = 0.0
    best_cov2 = 0.0
    best_mae = 0.0
    history = []

    for epoch in range(epochs):
        # Train
        model.train()
        total_loss = 0.0
        n_batches = 0
        for sb, tb in train_loader:
            sb, tb = sb.to(device), tb.to(device)
            optimizer.zero_grad()
            mean, log_var = model(sb)
            loss = gaussian_nll_loss(mean, log_var, tb, var_reg_weight=config["var_reg"])
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
            total_loss += loss.item()
            n_batches += 1
        scheduler.step()

        # Validate
        model.eval()
        all_m, all_v, all_t = [], [], []
        with torch.no_grad():
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

        history.append({"epoch": epoch, "nll": nll, "mae": mae, "cov1": cov1, "cov2": cov2})

        if nll < best_nll:
            best_nll = nll
            best_epoch = epoch
            best_cov1 = cov1
            best_cov2 = cov2
            best_mae = mae

        if epoch % 5 == 0 or epoch == epochs - 1:
            elapsed = time.time() - t0
            logger.info("  [%s] ep %2d/%d NLL=%.4f MAE=%.6f 1s=%.1f%% 2s=%.1f%% (%.0fs)",
                        config["name"], epoch + 1, epochs, nll, mae, cov1*100, cov2*100, elapsed)

    return {
        "name": config["name"],
        "config": {k: v for k, v in config.items() if k != "name"},
        "n_params": n_params,
        "best_epoch": best_epoch,
        "best_nll": best_nll,
        "best_mae": best_mae,
        "best_cov1": best_cov1,
        "best_cov2": best_cov2,
        "elapsed_s": time.time() - t0,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", default="data/dataset_R75_h10.npz")
    parser.add_argument("--save-dir", default="checkpoints/sweep")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    device = torch.device(args.device or ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info("sweep.device=%s", device)

    # Load data
    data = np.load(args.data, allow_pickle=True)
    states, targets, timestamps = data["states"], data["targets"], data["timestamps"]
    feature_names = list(data["feature_names"])
    horizon = targets.shape[1]
    logger.info("sweep.dataset n=%d features=%d horizon=%d", len(states), states.shape[1], horizon)

    # Temporal split
    sort_idx = np.argsort(timestamps)
    states, targets, timestamps = states[sort_idx], targets[sort_idx], timestamps[sort_idx]
    n_val = int(len(states) * 0.15)
    n_train = len(states) - n_val
    train_s, train_t = states[:n_train], targets[:n_train]
    val_s, val_t = states[n_train:], targets[n_train:]

    # Subsample training data for speed (keep all val data)
    # Use every 8th sample — same distribution, 8x faster
    SUBSAMPLE = 8
    train_s = train_s[::SUBSAMPLE]
    train_t = train_t[::SUBSAMPLE]
    logger.info("sweep.split train=%d (subsampled from %d) val=%d", len(train_s), n_train, len(val_s))

    # ===================================================================
    # PHASE 1: Quick scan — 20 epochs each (~3 min per config on CPU)
    # ===================================================================
    PHASE1_EPOCHS = 20
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 1: Quick scan (%d epochs × %d configs)", PHASE1_EPOCHS, len(SWEEP_CONFIGS))
    logger.info("=" * 60)

    phase1_results = []
    for cfg in SWEEP_CONFIGS:
        if cfg["name"] == "BASELINE":
            continue  # Skip baseline — we know it already
        logger.info("sweep.phase1 %s", cfg["name"])
        result = train_single(cfg, train_s, train_t, val_s, val_t,
                              feature_names, horizon, device, PHASE1_EPOCHS, 256)
        phase1_results.append(result)
        logger.info("sweep.phase1.done %s NLL=%.4f (%.0fs)",
                     cfg["name"], result["best_nll"], result["elapsed_s"])

    # Sort by NLL
    phase1_results.sort(key=lambda x: x["best_nll"])

    BASELINE_NLL = -4.8647
    logger.info("\nPhase 1 results (sorted by NLL):")
    logger.info(f"{'Name':<25s} {'NLL':>8s} {'Delta':>8s} {'MAE':>10s} {'1s':>6s} {'2s':>6s}")
    logger.info("-" * 65)
    for r in phase1_results:
        delta = r["best_nll"] - BASELINE_NLL
        marker = " <<<" if delta < -0.001 else ""
        logger.info(f"{r['name']:<25s} {r['best_nll']:8.4f} {delta:+8.4f} {r['best_mae']:10.6f} {r['best_cov1']:5.1%} {r['best_cov2']:5.1%}{marker}")

    # Select top 3 for full training
    top3 = phase1_results[:3]
    # If baseline is better than all, include it too
    if top3 and top3[0]["best_nll"] > BASELINE_NLL:
        # Find the baseline config
        baseline_cfg = next(c for c in SWEEP_CONFIGS if c["name"] == "BASELINE")
        top3.append({
            "name": "BASELINE",
            "config": {k: v for k, v in baseline_cfg.items() if k != "name"},
            "n_params": 23188,
            "best_nll": BASELINE_NLL,
        })

    logger.info("\nTop configs for Phase 2: %s", [r["name"] for r in top3])

    # ===================================================================
    # PHASE 2: Full training — 100 epochs for top 3
    # ===================================================================
    PHASE2_EPOCHS = 100
    logger.info("\n" + "=" * 60)
    logger.info("PHASE 2: Full training (%d epochs × %d configs)", PHASE2_EPOCHS, len(top3))
    logger.info("=" * 60)

    phase2_results = []
    for r in top3:
        cfg = {"name": r["name"], **r["config"]}
        logger.info("sweep.phase2 %s", cfg["name"])
        result = train_single(cfg, train_s, train_t, val_s, val_t,
                              feature_names, horizon, device, PHASE2_EPOCHS, 256)
        phase2_results.append(result)
        logger.info("sweep.phase2.done %s NLL=%.4f (%.0fs)",
                     cfg["name"], result["best_nll"], result["elapsed_s"])

    # ===================================================================
    # Final results
    # ===================================================================
    phase2_results.sort(key=lambda x: x["best_nll"])

    logger.info("\n" + "=" * 70)
    logger.info("FINAL RESULTS (Phase 2, sorted by NLL)")
    logger.info("=" * 70)
    logger.info(f"{'Name':<25s} {'NLL':>8s} {'Delta':>8s} {'MAE':>10s} {'1s':>6s} {'2s':>6s} {'Params':>8s} {'Epoch':>6s}")
    logger.info("-" * 80)

    # Add baseline reference
    logger.info(f"{'BASELINE_ref':<25s} {BASELINE_NLL:8.4f} {'---':>8s} {'0.001567':>10s} {'69.2%':>6s} {'96.0%':>6s} {'23188':>8s} {'---':>6s}")

    for r in phase2_results:
        delta = r["best_nll"] - BASELINE_NLL
        marker = " <<< BETTER" if delta < -0.001 else ""
        logger.info(
            f"{r['name']:<25s} {r['best_nll']:8.4f} {delta:+8.4f} {r['best_mae']:10.6f} "
            f"{r['best_cov1']:5.1%} {r['best_cov2']:5.1%} {r['n_params']:8d} {r['best_epoch']:6d}{marker}"
        )

    best = phase2_results[0]
    delta = best["best_nll"] - BASELINE_NLL
    logger.info(f"\nBest config: {best['name']}")
    logger.info(f"  NLL: {best['best_nll']:.4f} (delta={delta:+.4f} vs baseline)")
    logger.info(f"  Config: {json.dumps(best['config'], indent=4)}")

    # Save all results
    Path(args.save_dir).mkdir(parents=True, exist_ok=True)
    all_results = {
        "baseline_nll": BASELINE_NLL,
        "phase1": phase1_results,
        "phase2": phase2_results,
        "best": best,
    }
    with open(Path(args.save_dir) / "sweep_final.json", "w") as f:
        json.dump(all_results, f, indent=2, default=str)

    logger.info("\nResults saved to %s/sweep_final.json", args.save_dir)


if __name__ == "__main__":
    main()
