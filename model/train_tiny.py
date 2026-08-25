"""
model/train_tiny.py — Minimal 8-feature world model.

Deduplicated feature set from the feature audit:
  1. 15min_return_1          — best linear predictor (r=0.24)
  2. 15min_autocorrelation   — mean-reversion signal
  3. 15min_vol_regime        — volatility context
  4. 15min_price_z_score     — position in range (deduplicated from premium_discount)
  5. 15min_range_ATR         — volatility scale (non-redundant with perm importance)
  6. 1h_return_1             — higher-TF direction
  7. nearest_structure_distance — structural context
  8. 15min_up_streak         — momentum direction

Architecture: 1 hidden layer, ~500 params total.
Training: Gaussian NLL with temperature scaling.
Evaluation: Walk-forward with 4 monthly folds + per-regime breakdown.

Usage:
    python -m model.train_tiny
    python -m model.train_tiny --epochs 50
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
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, TensorDataset

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# The 8 deduplicated features
# ---------------------------------------------------------------------------
TINY_FEATURES = [
    "15min_return_1",
    "15min_autocorrelation",
    "15min_vol_regime",
    "15min_price_z_score",
    "15min_range_ATR",
    "1h_return_1",
    "nearest_structure_distance",
    "15min_up_streak",
]


# ---------------------------------------------------------------------------
# Tiny model: 8 -> 16 -> (mean, log_var) for horizon steps
# ---------------------------------------------------------------------------
class TinyWorldModel(nn.Module):
    """
    Minimal distributional world model.

    Architecture:
        Linear(8, 16) -> ReLU -> Dropout
        mean_head: Linear(16, horizon)
        log_var_head: Linear(16, horizon)

    Total params: ~500 (for horizon=10)
    """

    def __init__(self, n_features: int = 8, horizon: int = 10, hidden: int = 16, dropout: float = 0.1):
        super().__init__()
        self.horizon = horizon
        self.n_features = n_features

        self.encoder = nn.Sequential(
            nn.Linear(n_features, hidden),
            nn.LayerNorm(hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.mean_head = nn.Linear(hidden, horizon)
        self.log_var_head = nn.Linear(hidden, horizon)

        # Initialize log_var head to zeros (starts at variance=1)
        nn.init.zeros_(self.log_var_head.weight)
        nn.init.zeros_(self.log_var_head.bias)

    def forward(self, x: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        h = self.encoder(x)
        mean = self.mean_head(h)
        log_var = self.log_var_head(h)
        log_var = torch.clamp(log_var, min=-20.0, max=5.0)
        return mean, log_var

    @property
    def param_count(self) -> int:
        return sum(p.numel() for p in self.parameters())


# ---------------------------------------------------------------------------
# Gaussian NLL loss
# ---------------------------------------------------------------------------
def gaussian_nll_loss(
    pred_mean: torch.Tensor,
    pred_log_var: torch.Tensor,
    targets: torch.Tensor,
    var_reg_weight: float = 0.0,
) -> torch.Tensor:
    """Gaussian negative log-likelihood loss."""
    eps = 1e-8
    var = torch.exp(pred_log_var) + eps
    nll = 0.5 * (torch.log(2 * np.pi * var) + (targets - pred_mean) ** 2 / var)
    loss = nll.mean()

    # Optional variance regularization: penalize variance away from sample variance
    if var_reg_weight > 0:
        sample_var = (targets - pred_mean).detach() ** 2
        var_penalty = ((var - sample_var) ** 2).mean()
        loss = loss + var_reg_weight * var_penalty

    return loss


# ---------------------------------------------------------------------------
# Temperature scaling
# ---------------------------------------------------------------------------
def fit_temperature(
    targets: np.ndarray, means: np.ndarray, vars: np.ndarray
) -> float:
    """Fit temperature: T = E[(y-mu)^2] / E[sigma^2]."""
    squared_errors = (targets - means) ** 2
    T = float(np.mean(squared_errors) / np.mean(vars))
    return max(T, 0.01)


# ---------------------------------------------------------------------------
# Data loading and preprocessing
# ---------------------------------------------------------------------------
def load_tiny_dataset(
    dataset_path: str = "data/dataset_R75_h10.npz",
) -> dict:
    """Load dataset and extract only the 8 tiny features."""
    data = np.load(dataset_path, allow_pickle=True)
    full_states = data["states"]
    full_targets = data["targets"]
    full_timestamps = data["timestamps"]
    full_feature_names = list(data["feature_names"])

    # Map feature names to indices
    name_to_idx = {name: i for i, name in enumerate(full_feature_names)}
    tiny_indices = []
    found = []
    for feat in TINY_FEATURES:
        if feat in name_to_idx:
            tiny_indices.append(name_to_idx[feat])
            found.append(feat)
        else:
            logger.warning("feature_not_found name=%s", feat)

    logger.info(
        "tiny_dataset features=%d (kept %d of %d) found=%s",
        len(found), len(found), len(full_feature_names), found,
    )

    tiny_states = full_states[:, tiny_indices].copy()
    norm_mean = tiny_states.mean(axis=0)
    norm_std = tiny_states.std(axis=0) + 1e-8
    tiny_states = (tiny_states - norm_mean) / norm_std

    return {
        "states": tiny_states,
        "targets": full_targets,
        "timestamps": full_timestamps,
        "feature_names": found,
        "norm_mean": norm_mean,
        "norm_std": norm_std,
    }


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------
def train_tiny(
    dataset_path: str = "data/dataset_R75_h10.npz",
    output_dir: str = "checkpoints_tiny",
    epochs: int = 50,
    batch_size: int = 512,
    lr: float = 1e-3,
    hidden_dim: int = 16,
    dropout: float = 0.1,
    weight_decay: float = 1e-4,
    var_reg_weight: float = 0.05,
    val_split: float = 0.15,
    device_str: str | None = None,
) -> dict:
    """Train the tiny 8-feature model."""
    device = torch.device(device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu"))
    logger.info("device=%s", device)

    # Load data
    ds = load_tiny_dataset(dataset_path)
    states = ds["states"]
    targets = ds["targets"]
    timestamps = ds["timestamps"]
    feature_names = ds["feature_names"]
    horizon = targets.shape[1]

    # Temporal split
    sort_idx = np.argsort(timestamps)
    states = states[sort_idx]
    targets = targets[sort_idx]

    n_val = int(len(states) * val_split)
    n_train = len(states) - n_val
    train_states = states[:n_train]
    train_targets = targets[:n_train]
    val_states = states[n_train:]
    val_targets = targets[n_train:]

    logger.info("split train=%d val=%d features=%d", n_train, n_val, len(feature_names))

    train_loader = DataLoader(
        TensorDataset(torch.tensor(train_states, dtype=torch.float32),
                       torch.tensor(train_targets, dtype=torch.float32)),
        batch_size=batch_size, shuffle=True, num_workers=0,
    )
    val_loader = DataLoader(
        TensorDataset(torch.tensor(val_states, dtype=torch.float32),
                       torch.tensor(val_targets, dtype=torch.float32)),
        batch_size=batch_size, shuffle=False, num_workers=0,
    )

    # Build model
    model = TinyWorldModel(
        n_features=len(feature_names), horizon=horizon,
        hidden=hidden_dim, dropout=dropout,
    ).to(device)
    logger.info("model params=%d", model.param_count)

    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    save_path = Path(output_dir) / "world_model_best.pt"

    best_val_nll = float("inf")
    best_epoch = 0
    history = []

    for epoch in range(epochs):
        t0 = time.time()

        # Train
        model.train()
        total_loss = 0.0
        n_batches = 0
        for states_b, targets_b in train_loader:
            states_b = states_b.to(device)
            targets_b = targets_b.to(device)
            optimizer.zero_grad()
            pred_mean, pred_log_var = model(states_b)
            loss = gaussian_nll_loss(pred_mean, pred_log_var, targets_b, var_reg_weight)
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
                mean, log_var = model(states_b)
                all_means.append(mean.cpu().numpy())
                all_vars.append(torch.exp(log_var).cpu().numpy())
                all_targets.append(targets_b.cpu().numpy())

        val_means = np.concatenate(all_means, axis=0)
        val_vars = np.concatenate(all_vars, axis=0)
        val_tgts = np.concatenate(all_targets, axis=0)

        eps = 1e-8
        nll_arr = 0.5 * (np.log(2 * np.pi * val_vars) + (val_tgts - val_means) ** 2 / (val_vars + eps))
        val_nll = float(np.mean(nll_arr))
        val_mae = float(np.mean(np.abs(val_tgts - val_means)))
        stds = np.sqrt(val_vars)
        cov_1sig = float(np.mean(np.abs(val_tgts - val_means) <= stds))
        cov_2sig = float(np.mean(np.abs(val_tgts - val_means) <= 2 * stds))

        elapsed = time.time() - t0

        record = {
            "epoch": epoch, "train_loss": train_loss, "val_nll": val_nll,
            "val_mae": val_mae, "cov_1sig": cov_1sig, "cov_2sig": cov_2sig,
            "lr": optimizer.param_groups[0]["lr"], "elapsed": elapsed,
        }
        history.append(record)

        if epoch % 5 == 0 or epoch == epochs - 1:
            logger.info(
                "epoch %d/%d loss=%.4f val_nll=%.4f val_mae=%.6f "
                "cov1sig=%.2f%% cov2sig=%.2f%% lr=%.2e time=%.1fs",
                epoch + 1, epochs, train_loss, val_nll, val_mae,
                cov_1sig * 100, cov_2sig * 100,
                optimizer.param_groups[0]["lr"], elapsed,
            )

        if val_nll < best_val_nll:
            best_val_nll = val_nll
            best_epoch = epoch

            # Compute temperature on validation
            temperature = fit_temperature(val_tgts, val_means, val_vars)

            torch.save({
                "model_state_dict": model.state_dict(),
                "feature_names": feature_names,
                "epoch": epoch,
                "val_nll": val_nll,
                "val_mae": val_mae,
                "coverage_1sigma": cov_1sig,
                "coverage_2sigma": cov_2sig,
                "temperature": temperature,
                "horizon": horizon,
                "hidden_dim": hidden_dim,
                "dropout": dropout,
                "norm_mean": ds["norm_mean"].astype(np.float32),
                "norm_std": ds["norm_std"].astype(np.float32),
            }, save_path)

            logger.info("saved T=%.4f val_nll=%.4f", temperature, best_val_nll)

    logger.info("complete best_epoch=%d best_val_nll=%.4f", best_epoch, best_val_nll)

    # Baselines comparison on validation set
    eps = 1e-8
    def _nll(m, v, t):
        v = np.maximum(v, eps)
        return float(np.mean(0.5 * (np.log(2 * np.pi * v) + (t - m) ** 2 / v)))

    wm_raw_nll = _nll(val_means, val_vars, val_tgts)
    wm_cal_nll = _nll(val_means, val_vars * temperature, val_tgts)

    train_std = float(np.std(train_targets[:, 0]))
    steps = np.arange(1, horizon + 1, dtype=np.float64)
    rw_nll = _nll(np.zeros_like(val_tgts), np.tile(train_std ** 2 * steps, (len(val_tgts), 1)), val_tgts)
    pers_nll = _nll(np.zeros_like(val_tgts), np.ones_like(val_tgts) * 1e-6, val_tgts)

    print("\n" + "=" * 60)
    print("TINY MODEL TRAINING COMPLETE")
    print("=" * 60)
    print(f"Features: {len(feature_names)}")
    print(f"Params: {model.param_count}")
    print(f"Best epoch: {best_epoch}")
    print(f"Best val NLL (raw): {wm_raw_nll:.4f}")
    print(f"Best val NLL (calibrated): {wm_cal_nll:.4f}")
    print(f"Temperature: {temperature:.4f}")
    print(f"Random Walk NLL: {rw_nll:.4f}")
    print(f"Persistence NLL: {pers_nll:.4f}")
    print(f"Beats RW (raw)? {'YES' if wm_raw_nll < rw_nll else 'NO'}")
    print(f"Beats RW (cal)? {'YES' if wm_cal_nll < rw_nll else 'NO'}")
    print(f"Model saved: {save_path}")

    results = {
        "best_epoch": best_epoch,
        "best_val_nll": best_val_nll,
        "temperature": temperature,
        "val_nll_raw": wm_raw_nll,
        "val_nll_cal": wm_cal_nll,
        "rw_nll": rw_nll,
        "pers_nll": pers_nll,
        "param_count": model.param_count,
        "n_features": len(feature_names),
        "feature_names": feature_names,
        "history": history,
    }
    results_path = Path(output_dir) / "training_results.json"
    with open(results_path, "w") as f:
        json.dump(results, f, indent=2, default=str)

    return results


# ---------------------------------------------------------------------------
# Walk-forward evaluation
# ---------------------------------------------------------------------------
def evaluate_walk_forward(
    checkpoint_path: str = "checkpoints_tiny/world_model_best.pt",
    dataset_path: str = "data/dataset_R75_h10.npz",
    train_days: int = 60,
    val_days: int = 30,
    step_days: int = 30,
    device_str: str | None = None,
) -> dict:
    """Walk-forward evaluation with per-fold temperature fitting."""
    device = torch.device(device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu"))

    # Load checkpoint
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    feature_names = ckpt["feature_names"]
    horizon = ckpt["horizon"]
    saved_T = ckpt.get("temperature", 1.0)

    # Build model
    model = TinyWorldModel(
        n_features=len(feature_names), horizon=horizon,
        hidden=ckpt.get("hidden_dim", 16),
        dropout=ckpt.get("dropout", 0.0),  # no dropout at eval time
    ).to(device)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()

    logger.info("loaded checkpoint params=%d T_saved=%.4f", model.param_count, saved_T)

    # Load and filter dataset
    ds = load_tiny_dataset(dataset_path)
    # Ensure we use the same features and normalization
    ckpt_feat_set = set(feature_names)
    ds_feat_set = set(ds["feature_names"])
    if ckpt_feat_set != ds_feat_set:
        logger.warning("feature_mismatch ckpt=%s ds=%s", ckpt_feat_set, ds_feat_set)

    states = ds["states"]
    targets = ds["targets"]
    timestamps = ds["timestamps"]

    # Build walk-forward splits
    ts_min = pd.Timestamp(timestamps.min())
    ts_max = pd.Timestamp(timestamps.max())

    folds = []
    fold_idx = 0
    offset_days = 0
    while True:
        train_start = ts_min + pd.Timedelta(days=offset_days)
        train_end = train_start + pd.Timedelta(days=train_days)
        val_start = train_end
        val_end = val_start + pd.Timedelta(days=val_days)
        if val_end > ts_max + pd.Timedelta(hours=12):
            break
        val_mask = (timestamps >= np.datetime64(val_start)) & (timestamps < np.datetime64(val_end))
        train_mask = (timestamps >= np.datetime64(train_start)) & (timestamps < np.datetime64(train_end))
        val_indices = np.where(val_mask)[0]
        train_indices = np.where(train_mask)[0]
        if len(val_indices) > 0 and len(train_indices) > 0:
            folds.append({
                "fold": fold_idx,
                "val_start": val_start, "val_end": val_end,
                "train_indices": train_indices, "val_indices": val_indices,
                "n_val": len(val_indices),
            })
            fold_idx += 1
        offset_days += step_days

    logger.info("walk_forward folds=%d", len(folds))

    # Evaluate each fold
    fold_results = []
    fold_temps = []

    for f in folds:
        t0 = time.time()
        train_s = states[f["train_indices"]]
        train_t = targets[f["train_indices"]]
        val_s = states[f["val_indices"]]
        val_t = targets[f["val_indices"]]

        # Predict on training set (to fit temperature)
        wm_means_train, wm_vars_train = predict_tiny(model, train_s, device)
        fold_T = fit_temperature(train_t, wm_means_train, wm_vars_train)
        fold_temps.append(fold_T)

        # Predict on validation set
        wm_means_val, wm_vars_val = predict_tiny(model, val_s, device)

        # Apply temperature
        wm_vars_cal = wm_vars_val * fold_T

        # Compute metrics
        eps = 1e-8
        wm_nll = float(np.mean(0.5 * (np.log(2 * np.pi * wm_vars_cal) + (val_t - wm_means_val) ** 2 / (wm_vars_cal + eps))))
        wm_mae = float(np.mean(np.abs(val_t - wm_means_val)))
        wm_stds = np.sqrt(wm_vars_cal)
        wm_1sig = float(np.mean(np.abs(val_t - wm_means_val) <= wm_stds))
        wm_2sig = float(np.mean(np.abs(val_t - wm_means_val) <= 2 * wm_stds))

        # Baselines
        train_std = float(np.std(train_t[:, 0]))
        steps_arr = np.arange(1, horizon + 1, dtype=np.float64)
        rw_vars = np.tile(train_std ** 2 * steps_arr, (len(val_t), 1))
        rw_nll = float(np.mean(0.5 * (np.log(2 * np.pi * rw_vars) + (val_t - 0) ** 2 / rw_vars)))
        rw_mae = float(np.mean(np.abs(val_t)))
        rw_stds = np.sqrt(rw_vars)
        rw_1sig = float(np.mean(np.abs(val_t) <= rw_stds))
        rw_2sig = float(np.mean(np.abs(val_t) <= 2 * rw_stds))

        pers_nll = float(np.mean(0.5 * (np.log(2 * np.pi * 1e-6) + val_t ** 2 / 1e-6)))

        # Regime breakdown
        all_returns = val_t[:, 0]
        realized_vol = np.std(all_returns)
        regime_masks = {
            "calm": np.abs(all_returns) < realized_vol * 0.5,
            "choppy": (np.abs(all_returns) >= realized_vol * 0.5) & (np.abs(all_returns) < realized_vol * 1.5),
            "spike": np.abs(all_returns) >= realized_vol * 1.5,
        }

        regime_results = {}
        for rname, mask in regime_masks.items():
            if mask.sum() < 5:
                continue
            r_nll = float(np.mean(0.5 * (np.log(2 * np.pi * wm_vars_cal[mask]) + (val_t[mask] - wm_means_val[mask]) ** 2 / (wm_vars_cal[mask] + eps))))
            r_rw = float(np.mean(0.5 * (np.log(2 * np.pi * rw_vars[mask]) + val_t[mask] ** 2 / rw_vars[mask])))
            regime_results[rname] = {
                "n": int(mask.sum()),
                "wm_nll": r_nll, "rw_nll": r_rw,
                "beat": r_nll < r_rw,
            }

        beat = wm_nll < rw_nll
        elapsed = time.time() - t0

        logger.info(
            "fold %d [%s -> %s] n=%d WM_NLL=%.4f RW_NLL=%.4f T=%.4f beat=%s 1sig=%.1f%% time=%.1fs",
            f["fold"], f["val_start"].date(), f["val_end"].date(), f["n_val"],
            wm_nll, rw_nll, fold_T, "YES" if beat else "NO", wm_1sig * 100, elapsed,
        )
        for rname, rr in regime_results.items():
            logger.info("  [%s] n=%d WM=%.4f RW=%.4f beat=%s", rname, rr["n"], rr["wm_nll"], rr["rw_nll"], "YES" if rr["beat"] else "NO")

        fold_results.append({
            "fold": f["fold"],
            "val_start": str(f["val_start"].date()),
            "val_end": str(f["val_end"].date()),
            "n_val": f["n_val"],
            "wm_nll": wm_nll, "wm_mae": wm_mae, "wm_1sig": wm_1sig, "wm_2sig": wm_2sig,
            "rw_nll": rw_nll, "rw_mae": rw_mae, "rw_1sig": rw_1sig, "rw_2sig": rw_2sig,
            "pers_nll": pers_nll,
            "temperature": fold_T,
            "beat_rw": beat,
            "regime_results": regime_results,
        })

    # Aggregate
    total_n = sum(f["n_val"] for f in fold_results)
    wm_nll_agg = sum(f["wm_nll"] * f["n_val"] for f in fold_results) / total_n
    rw_nll_agg = sum(f["rw_nll"] * f["n_val"] for f in fold_results) / total_n
    wm_1sig_agg = sum(f["wm_1sig"] * f["n_val"] for f in fold_results) / total_n
    wm_2sig_agg = sum(f["wm_2sig"] * f["n_val"] for f in fold_results) / total_n
    avg_T = np.mean(fold_temps)
    n_beat = sum(1 for f in fold_results if f["beat_rw"])

    # Regime aggregation
    regime_agg = {}
    for f in fold_results:
        for rname, rr in f["regime_results"].items():
            if rname not in regime_agg:
                regime_agg[rname] = {"wm_nlls": [], "rw_nlls": [], "ns": []}
            regime_agg[rname]["wm_nlls"].append(rr["wm_nll"])
            regime_agg[rname]["rw_nlls"].append(rr["rw_nll"])
            regime_agg[rname]["ns"].append(rr["n"])

    regime_summary = {}
    for rname, ra in regime_agg.items():
        wm_avg = np.mean(ra["wm_nlls"])
        rw_avg = np.mean(ra["rw_nlls"])
        regime_summary[rname] = {
            "avg_wm_nll": float(wm_avg),
            "avg_rw_nll": float(rw_avg),
            "beat": wm_avg < rw_avg,
            "total_n": sum(ra["ns"]),
        }

    # Pass/fail
    beats_all_folds = all(f["beat_rw"] for f in fold_results)
    beats_avg = wm_nll_agg < rw_nll_agg
    beats_2_regimes = sum(1 for r in regime_summary.values() if r["beat"]) >= 2
    no_catastrophic = all(f["wm_nll"] < 0 for f in fold_results)

    overall_pass = beats_avg and beats_2_regimes and no_catastrophic
    verdict = "PASS" if overall_pass else ("CONDITIONAL" if beats_avg else "FAIL")

    print("\n" + "=" * 80)
    print("TINY MODEL WALK-FORWARD EVALUATION")
    print("=" * 80)
    print(f"Features: {len(feature_names)}  Params: {model.param_count}")
    print(f"Saved T: {saved_T:.4f}  Avg fold T: {avg_T:.4f}")
    print(f"\nAggregate:")
    print(f"  WM NLL:  {wm_nll_agg:.4f}")
    print(f"  RW NLL:  {rw_nll_agg:.4f}")
    print(f"  Beat RW: {'YES' if beats_avg else 'NO'} (delta={wm_nll_agg - rw_nll_agg:+.4f})")
    print(f"  1sig: {wm_1sig_agg:.1%}  2sig: {wm_2sig_agg:.1%}")
    print(f"  Folds beating RW: {n_beat}/{len(fold_results)}")

    print(f"\nPer-fold:")
    for f in fold_results:
        print(f"  Fold {f['fold']}: [{f['val_start']} -> {f['val_end']}]  WM={f['wm_nll']:.4f}  RW={f['rw_nll']:.4f}  T={f['temperature']:.4f}  {'BEAT' if f['beat_rw'] else 'LOSE'}")

    print(f"\nPer-regime:")
    for rname, rs in sorted(regime_summary.items(), key=lambda x: x[1]["total_n"], reverse=True):
        print(f"  {rname:8s} (n={rs['total_n']:6d}):  WM={rs['avg_wm_nll']:.4f}  RW={rs['avg_rw_nll']:.4f}  {'BEAT' if rs['beat'] else 'LOSE'}")

    print(f"\nVerdict: {verdict}")
    print(f"  Beats RW on average: {'YES' if beats_avg else 'NO'}")
    print(f"  Beats RW in >=2 regimes: {'YES' if beats_2_regimes else 'NO'}")
    print(f"  No catastrophic fold: {'YES' if no_catastrophic else 'NO'}")

    report = {
        "config": {
            "checkpoint": checkpoint_path,
            "features": feature_names,
            "param_count": model.param_count,
            "saved_temperature": saved_T,
            "avg_fold_temperature": float(avg_T),
            "n_folds": len(fold_results),
            "train_days": train_days, "val_days": val_days, "step_days": step_days,
        },
        "aggregate": {
            "wm_nll": wm_nll_agg, "rw_nll": rw_nll_agg,
            "beat_rw": beats_avg,
            "wm_1sig": wm_1sig_agg, "wm_2sig": wm_2sig_agg,
            "n_beat": n_beat, "n_folds": len(fold_results),
        },
        "folds": fold_results,
        "regime_summary": regime_summary,
        "verdict": verdict,
    }

    report_path = Path(checkpoint_path).parent / "walk_forward_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)
    logger.info("report saved to %s", report_path)

    return report


def predict_tiny(
    model: TinyWorldModel, states: np.ndarray, device: torch.device, batch_size: int = 4096
) -> tuple[np.ndarray, np.ndarray]:
    """Batch prediction for numpy arrays."""
    model.eval()
    all_means, all_vars = [], []
    with torch.no_grad():
        for start in range(0, len(states), batch_size):
            batch = torch.tensor(states[start:start + batch_size], dtype=torch.float32).to(device)
            mean, log_var = model(batch)
            all_means.append(mean.cpu().numpy())
            all_vars.append(torch.exp(log_var).cpu().numpy())
    return np.concatenate(all_means, axis=0), np.concatenate(all_vars, axis=0)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Tiny 8-feature world model")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--output-dir", default="checkpoints_tiny")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--hidden", type=int, default=16)
    parser.add_argument("--dropout", type=float, default=0.1)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--var-reg", type=float, default=0.05)
    parser.add_argument("--device", default=None)
    parser.add_argument("--eval-only", action="store_true", help="Skip training, just run walk-forward eval")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if not args.eval_only:
        train_tiny(
            dataset_path=args.dataset,
            output_dir=args.output_dir,
            epochs=args.epochs,
            batch_size=args.batch_size,
            lr=args.lr,
            hidden_dim=args.hidden,
            dropout=args.dropout,
            weight_decay=args.weight_decay,
            var_reg_weight=args.var_reg,
            device_str=args.device,
        )

    # Walk-forward evaluation
    ckpt_path = f"{args.output_dir}/world_model_best.pt"
    if Path(ckpt_path).exists():
        evaluate_walk_forward(
            checkpoint_path=ckpt_path,
            dataset_path=args.dataset,
            device_str=args.device,
        )
    else:
        logger.error("checkpoint_not_found path=%s", ckpt_path)


if __name__ == "__main__":
    main()
