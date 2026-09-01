"""
eval/feature_audit.py — Comprehensive feature audit for the world model.

Answers the fundamental question: do the features carry predictive signal,
and which ones?

Methods:
  1. Pearson correlation with each horizon step
  2. Mutual information (non-linear dependency)
  3. Permutation importance (shuffle each feature, measure NLL change)
  4. Feature redundancy matrix (identify correlated groups)
  5. Linear regression baseline (R² with top-K features)
  6. Information coefficient (Spearman rank correlation)

Usage:
    python -m eval.feature_audit
    python -m eval.feature_audit --top-k 20
"""

from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch
from scipy import stats as sp_stats

from model.world_model import (
    WorldModel,
    build_model_from_features,
    gaussian_nll_loss,
)
from model.train_reduced import SELECTED_FEATURES, build_reduced_dataset

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. Pearson correlation
# ---------------------------------------------------------------------------
def pearson_correlations(
    states: np.ndarray, targets: np.ndarray, feature_names: list[str]
) -> pd.DataFrame:
    """Compute Pearson correlation between each feature and each horizon step."""
    rows = []
    for i, name in enumerate(feature_names):
        feat = states[:, i]
        if np.std(feat) < 1e-12:
            continue
        for step in range(targets.shape[1]):
            t = targets[:, step]
            r, p = sp_stats.pearsonr(feat, t)
            rows.append({
                "feature": name,
                "step": step,
                "pearson_r": r,
                "r_squared": r ** 2,
                "p_value": p,
                "abs_r": abs(r),
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 2. Mutual information (discretized)
# ---------------------------------------------------------------------------
def mutual_information(
    states: np.ndarray, targets: np.ndarray, feature_names: list[str], n_bins: int = 20
) -> pd.DataFrame:
    """Compute mutual information between each feature and each horizon step."""
    rows = []
    for i, name in enumerate(feature_names):
        feat = states[:, i]
        if np.std(feat) < 1e-12:
            continue
        for step in range(targets.shape[1]):
            t = targets[:, step]
            # Discretize both into bins
            feat_d = np.digitize(feat, np.percentile(feat, np.linspace(0, 100, n_bins + 1)[1:-1]))
            t_d = np.digitize(t, np.percentile(t, np.linspace(0, 100, n_bins + 1)[1:-1]))

            # Mutual information via contingency table
            contingency = np.histogram2d(feat_d, t_d, bins=n_bins)[0]
            contingency = contingency / contingency.sum()
            p_feat = contingency.sum(axis=1)
            p_target = contingency.sum(axis=0)

            # MI = sum p(x,y) log(p(x,y) / (p(x) p(y)))
            nonzero = contingency > 0
            p_feat_t = p_feat[:, None] * p_target[None, :]
            mi = np.sum(contingency[nonzero] * np.log(contingency[nonzero] / p_feat_t[nonzero]))
            rows.append({
                "feature": name,
                "step": step,
                "mi": mi,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 3. Permutation importance (NLL-based)
# ---------------------------------------------------------------------------
def permutation_importance(
    model: WorldModel,
    states: np.ndarray,
    targets: np.ndarray,
    feature_names: list[str],
    device: torch.device,
    n_repeats: int = 5,
) -> pd.DataFrame:
    """Shuffle each feature, measure NLL degradation on validation set."""
    model.eval()

    # Baseline NLL
    with torch.no_grad():
        batch_size = 4096
        all_means, all_vars = [], []
        for start in range(0, len(states), batch_size):
            batch = torch.tensor(states[start:start + batch_size], dtype=torch.float32).to(device)
            mean, log_var = model(batch)
            all_means.append(mean.cpu().numpy())
            all_vars.append(torch.exp(log_var).cpu().numpy())
        base_means = np.concatenate(all_means, axis=0)
        base_vars = np.concatenate(all_vars, axis=0)

    eps = 1e-8
    base_nll = np.mean(0.5 * (np.log(2 * np.pi * base_vars) + (targets - base_means) ** 2 / (base_vars + eps)))

    rows = []
    for i, name in enumerate(feature_names):
        nll_increases = []
        for _ in range(n_repeats):
            permuted = states.copy()
            permuted[:, i] = np.random.permutation(permuted[:, i])

            with torch.no_grad():
                all_m, all_v = [], []
                for start in range(0, len(permuted), batch_size):
                    batch = torch.tensor(permuted[start:start + batch_size], dtype=torch.float32).to(device)
                    mean, log_var = model(batch)
                    all_m.append(mean.cpu().numpy())
                    all_v.append(torch.exp(log_var).cpu().numpy())
                p_means = np.concatenate(all_m, axis=0)
                p_vars = np.concatenate(all_v, axis=0)

            nll = np.mean(0.5 * (np.log(2 * np.pi * p_vars) + (targets - p_means) ** 2 / (p_vars + eps)))
            nll_increases.append(nll - base_nll)

        mean_increase = np.mean(nll_increases)
        std_increase = np.std(nll_increases)
        rows.append({
            "feature": name,
            "nll_increase_mean": mean_increase,
            "nll_increase_std": std_increase,
            "importance": mean_increase / max(std_increase, 1e-10),
        })

    df = pd.DataFrame(rows)
    df["rank"] = df["nll_increase_mean"].rank(ascending=False).astype(int)
    return df.sort_values("nll_increase_mean", ascending=False).reset_index(drop=True)


# ---------------------------------------------------------------------------
# 4. Feature redundancy (correlation matrix)
# ---------------------------------------------------------------------------
def feature_redundancy(
    states: np.ndarray, feature_names: list[str], threshold: float = 0.7
) -> list[tuple[str, str, float]]:
    """Find highly correlated feature pairs."""
    n = states.shape[1]
    corr = np.corrcoef(states.T)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            if abs(corr[i, j]) > threshold:
                pairs.append((feature_names[i], feature_names[j], corr[i, j]))
    pairs.sort(key=lambda x: abs(x[2]), reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# 5. Linear regression baseline
# ---------------------------------------------------------------------------
def linear_regression_baseline(
    states: np.ndarray,
    targets: np.ndarray,
    feature_names: list[str],
    top_k: int = 10,
) -> dict:
    """Fit OLS on top-K features (by Pearson r with step-0), report R²."""
    # Rank features by |corr| with step-0
    corrs = []
    for i, name in enumerate(feature_names):
        if np.std(states[:, i]) < 1e-12:
            continue
        r, _ = sp_stats.pearsonr(states[:, i], targets[:, 0])
        corrs.append((abs(r), name, i))
    corrs.sort(reverse=True)

    top_features = [c[1] for c in corrs[:top_k]]
    top_indices = [c[2] for c in corrs[:top_k]]

    # Fit OLS
    X = states[:, top_indices]
    # Add intercept
    X_aug = np.column_stack([np.ones(len(X)), X])

    results = {}
    for step in range(min(3, targets.shape[1])):  # steps 0, 1, 2
        y = targets[:, step]
        try:
            # OLS: beta = (X'X)^-1 X'y
            beta = np.linalg.lstsq(X_aug, y, rcond=None)[0]
            y_pred = X_aug @ beta
            ss_res = np.sum((y - y_pred) ** 2)
            ss_tot = np.sum((y - np.mean(y)) ** 2)
            r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
            results[f"step_{step}"] = {
                "r_squared": r_squared,
                "top_features": top_features,
            }
        except Exception as e:
            results[f"step_{step}"] = {"error": str(e)}

    return results


# ---------------------------------------------------------------------------
# 6. Information coefficient (Spearman rank correlation)
# ---------------------------------------------------------------------------
def information_coefficient(
    states: np.ndarray, targets: np.ndarray, feature_names: list[str]
) -> pd.DataFrame:
    """Spearman rank correlation between features and targets."""
    rows = []
    for i, name in enumerate(feature_names):
        feat = states[:, i]
        if np.std(feat) < 1e-12:
            continue
        for step in range(targets.shape[1]):
            t = targets[:, step]
            rho, p = sp_stats.spearmanr(feat, t)
            rows.append({
                "feature": name,
                "step": step,
                "ic": rho,
                "abs_ic": abs(rho),
                "p_value": p,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def run_feature_audit(
    dataset_path: str = "data/dataset_R75_h10.npz",
    checkpoint_path: str = "checkpoints_v7/world_model_best.pt",
    output_dir: str = "checkpoints_v7/feature_audit",
    top_k: int = 20,
    device_str: str | None = None,
) -> str:
    """Run full feature audit."""
    device = torch.device(
        device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    logger.info("device=%s", device)

    # Load dataset
    data = np.load(dataset_path, allow_pickle=True)
    full_states = data["states"]
    full_targets = data["targets"]
    full_timestamps = data["timestamps"]
    full_feature_names = list(data["feature_names"])
    horizon = full_targets.shape[1]

    # Filter to selected features
    states, feature_names = build_reduced_dataset(
        full_states, full_targets, full_timestamps, full_feature_names, SELECTED_FEATURES
    )

    # Temporal split (last 15% as validation)
    sort_idx = np.argsort(full_timestamps)
    states = states[sort_idx]
    targets = full_targets[sort_idx]
    n_val = int(len(states) * 0.15)
    val_states = states[-n_val:]
    val_targets = targets[-n_val:]
    train_states = states[:-n_val]
    train_targets = targets[:-n_val]

    logger.info(
        "data loaded: features=%d train=%d val=%d",
        len(feature_names), len(train_states), len(val_states),
    )

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    report_lines = []

    def log_and_record(msg: str):
        logger.info(msg)
        report_lines.append(msg)

    # --- 1. Pearson Correlation ---
    log_and_record("\n" + "=" * 80)
    log_and_record("1. PEARSON CORRELATION WITH TARGETS")
    log_and_record("=" * 80)

    pearson_df = pearson_correlations(train_states, train_targets, feature_names)
    # Show top features for step 0
    top_pearson = pearson_df[pearson_df["step"] == 0].nlargest(top_k, "abs_r")
    log_and_record(f"\nTop {top_k} features by |r| with step-0 return:")
    for _, row in top_pearson.iterrows():
        log_and_record(f"  {row['feature']:45s} r={row['pearson_r']:+.4f}  R²={row['r_squared']:.4f}")

    # Also show step-9
    top_pearson_9 = pearson_df[pearson_df["step"] == 9].nlargest(top_k, "abs_r")
    log_and_record(f"\nTop {top_k} features by |r| with step-9 return:")
    for _, row in top_pearson_9.iterrows():
        log_and_record(f"  {row['feature']:45s} r={row['pearson_r']:+.4f}  R²={row['r_squared']:.4f}")

    # Count strong features per step
    log_and_record("\nFeatures with |r| > 0.05 per horizon step:")
    for step in range(horizon):
        step_df = pearson_df[pearson_df["step"] == step]
        strong = step_df[step_df["abs_r"] > 0.05]
        log_and_record(f"  Step {step:2d}: {len(strong):3d} features")

    pearson_df.to_csv(f"{output_dir}/pearson_correlation.csv", index=False)

    # --- 2. Mutual Information ---
    log_and_record("\n" + "=" * 80)
    log_and_record("2. MUTUAL INFORMATION WITH TARGETS")
    log_and_record("=" * 80)

    mi_df = mutual_information(train_states, train_targets, feature_names)
    top_mi = mi_df[mi_df["step"] == 0].nlargest(top_k, "mi")
    log_and_record(f"\nTop {top_k} features by MI with step-0 return:")
    for _, row in top_mi.iterrows():
        log_and_record(f"  {row['feature']:45s} MI={row['mi']:.4f}")

    mi_df.to_csv(f"{output_dir}/mutual_information.csv", index=False)

    # --- 3. Permutation Importance ---
    log_and_record("\n" + "=" * 80)
    log_and_record("3. PERMUTATION IMPORTANCE (NLL degradation)")
    log_and_record("=" * 80)

    try:
        model, saved_T = load_model_for_audit(checkpoint_path, feature_names, device)
        perm_df = permutation_importance(
            model, val_states[:5000], val_targets[:5000], feature_names, device, n_repeats=3
        )
        log_and_record("\nTop features by NLL increase when permuted:")
        for _, row in perm_df.head(top_k).iterrows():
            log_and_record(
                f"  {row['feature']:45s} "
                f"ΔNLL={row['nll_increase_mean']:+.4f} ± {row['nll_increase_std']:.4f}"
            )

        # Bottom features (least important)
        log_and_record(f"\nBottom 10 least important features:")
        for _, row in perm_df.tail(10).iterrows():
            log_and_record(
                f"  {row['feature']:45s} "
                f"ΔNLL={row['nll_increase_mean']:+.4f} ± {row['nll_increase_std']:.4f}"
            )

        perm_df.to_csv(f"{output_dir}/permutation_importance.csv", index=False)
    except Exception as e:
        log_and_record(f"  ERROR: {e}")

    # --- 4. Feature Redundancy ---
    log_and_record("\n" + "=" * 80)
    log_and_record("4. FEATURE REDUNDANCY (|corr| > 0.7)")
    log_and_record("=" * 80)

    pairs = feature_redundancy(train_states, feature_names, threshold=0.7)
    log_and_record(f"\nHighly correlated pairs: {len(pairs)}")
    for name_i, name_j, corr in pairs[:30]:
        log_and_record(f"  {name_i:35s} <-> {name_j:35s}  r={corr:+.3f}")

    # --- 5. Linear Regression Baseline ---
    log_and_record("\n" + "=" * 80)
    log_and_record("5. LINEAR REGRESSION BASELINE (top-K features)")
    log_and_record("=" * 80)

    for k in [5, 10, 15, len(feature_names)]:
        results = linear_regression_baseline(train_states, train_targets, feature_names, top_k=k)
        log_and_record(f"\nTop-{k} features linear model:")
        for step_key, step_result in results.items():
            if "r_squared" in step_result:
                log_and_record(
                    f"  {step_key}: R²={step_result['r_squared']:.6f}  "
                    f"features={step_result['top_features'][:5]}"
                )

    # --- 6. Information Coefficient ---
    log_and_record("\n" + "=" * 80)
    log_and_record("6. INFORMATION COEFFICIENT (Spearman rank correlation)")
    log_and_record("=" * 80)

    ic_df = information_coefficient(train_states, train_targets, feature_names)
    top_ic = ic_df[ic_df["step"] == 0].nlargest(top_k, "abs_ic")
    log_and_record(f"\nTop {top_k} features by |IC| with step-0 return:")
    for _, row in top_ic.iterrows():
        log_and_record(
            f"  {row['feature']:45s} IC={row['ic']:+.4f}  p={row['p_value']:.2e}"
        )

    ic_df.to_csv(f"{output_dir}/information_coefficient.csv", index=False)

    # --- Summary ---
    log_and_record("\n" + "=" * 80)
    log_and_record("SUMMARY: RECOMMENDED FEATURE SET")
    log_and_record("=" * 80)

    # Vote across methods: features that appear in top-10 of multiple methods
    votes = {}
    methods_top10 = {}

    # Pearson step-0
    for _, row in top_pearson.iterrows():
        name = row["feature"]
        votes[name] = votes.get(name, 0) + 1
    methods_top10["pearson_step0"] = set(top_pearson["feature"].tolist())

    # Pearson step-9
    for _, row in top_pearson_9.iterrows():
        name = row["feature"]
        votes[name] = votes.get(name, 0) + 1
    methods_top10["pearson_step9"] = set(top_pearson_9["feature"].tolist())

    # MI step-0
    for _, row in top_mi.iterrows():
        name = row["feature"]
        votes[name] = votes.get(name, 0) + 1
    methods_top10["mi_step0"] = set(top_mi["feature"].tolist())

    # IC step-0
    for _, row in top_ic.iterrows():
        name = row["feature"]
        votes[name] = votes.get(name, 0) + 1
    methods_top10["ic_step0"] = set(top_ic["feature"].tolist())

    # Permutation importance
    if "perm_df" in dir():
        for _, row in perm_df.head(top_k).iterrows():
            name = row["feature"]
            if row["nll_increase_mean"] > 0:
                votes[name] = votes.get(name, 0) + 1
        methods_top10["perm_importance"] = set(perm_df.head(top_k)["feature"].tolist())

    log_and_record("\nFeature voting (appears in top-10 of each method):")
    voted = sorted(votes.items(), key=lambda x: -x[1])
    for name, count in voted:
        log_and_record(f"  {name:45s} votes={count}/{len(methods_top10)}")

    # Final recommendation
    recommended = [name for name, count in voted if count >= 3]
    log_and_record(f"\nRECOMMENDED features (≥3 votes): {len(recommended)}")
    for name in recommended:
        log_and_record(f"  - {name}")

    # Save report
    report_path = f"{output_dir}/audit_report.txt"
    with open(report_path, "w") as f:
        f.write("\n".join(report_lines))
    log_and_record(f"\nReport saved to: {report_path}")

    return "\n".join(report_lines)


def load_model_for_audit(
    checkpoint_path: str, feature_names: list[str], device: torch.device
) -> tuple[WorldModel, float]:
    """Load model, handling old vs new architecture."""
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    saved_config = ckpt["config"]

    # Detect legacy
    has_legacy_attr = hasattr(saved_config, "legacy")
    is_legacy = getattr(saved_config, "legacy", False) if has_legacy_attr else True

    model = build_model_from_features(
        feature_names,
        horizon=getattr(saved_config, "horizon", 10),
        dropout=getattr(saved_config, "dropout", 0.1),
        hidden_dim=getattr(saved_config, "head_hidden", getattr(saved_config, "fusion_hidden", 32)),
        legacy=is_legacy,
        train_targets=None,
    ).to(device)

    state_dict = ckpt["model_state_dict"]
    remapped = {}
    for k, v in state_dict.items():
        if is_legacy and "log_var_shared." in k:
            new_k = k.replace("log_var_shared.", "log_var_encoder.")
            if new_k in {n for n, _ in model.named_parameters()}:
                remapped[new_k] = v
                continue
        remapped[k] = v

    model.load_state_dict(remapped, strict=False)
    model.eval()
    temperature = ckpt.get("temperature", 1.0)
    return model, temperature


def main():
    parser = argparse.ArgumentParser(description="Comprehensive feature audit")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--checkpoint", default="checkpoints_v7/world_model_best.pt")
    parser.add_argument("--output-dir", default="checkpoints_v7/feature_audit")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_feature_audit(
        dataset_path=args.dataset,
        checkpoint_path=args.checkpoint,
        output_dir=args.output_dir,
        top_k=args.top_k,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
