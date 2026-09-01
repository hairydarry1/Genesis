"""
eval/feature_importance.py — Feature importance analysis for the world model.

Methods:
  1. Gradient-based: gradient magnitudes at input ( Integrated Gradients-style)
  2. Permutation importance: shuffle each feature, measure NLL degradation
  3. Branch ablation: remove entire feature groups (timeframes, structure)
  4. Weight magnitude: first-layer weight norms as proxy for feature importance

Usage:
    python -m eval.feature_importance
    python -m eval.feature_importance --n-permutations 50 --top-k 20
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
import torch.nn.functional as F

from model.world_model import WorldModel, build_model_from_features, gaussian_nll_loss
from model.train import validate
from torch.utils.data import DataLoader

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_dataset_and_model(
    dataset_path: str,
    checkpoint_path: str,
    device: torch.device,
) -> tuple[np.ndarray, np.ndarray, list[str], int, WorldModel]:
    """Load precomputed dataset and trained model."""
    data = np.load(dataset_path, allow_pickle=True)
    states = data["states"]
    targets = data["targets"]
    feature_names = list(data["feature_names"])
    horizon = targets.shape[1]

    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    model = build_model_from_features(ckpt["feature_names"], horizon=ckpt["horizon"])
    model.load_state_dict(ckpt["model_state_dict"])
    model = model.to(device)
    model.eval()

    logger.info(
        "loaded dataset=%d samples, features=%d, model_epoch=%d",
        len(states), len(feature_names), ckpt.get("epoch", -1),
    )
    return states, targets, feature_names, horizon, model


# ---------------------------------------------------------------------------
# Method 1: Gradient-based importance
# ---------------------------------------------------------------------------

def gradient_importance(
    model: WorldModel,
    states: np.ndarray,
    targets: np.ndarray,
    device: torch.device,
    batch_size: int = 512,
) -> dict[str, float]:
    """
    Compute feature importance via gradient magnitudes.

    For each input feature, compute the gradient of the NLL loss w.r.t. the input.
    The absolute gradient magnitude indicates how sensitive the model is to that feature.
    """
    model.eval()
    n_features = states.shape[1]
    grad_accum = np.zeros(n_features, dtype=np.float64)
    n_batches = 0

    for i in range(0, len(states), batch_size):
        batch_x = torch.tensor(
            states[i:i+batch_size], dtype=torch.float32, device=device, requires_grad=True
        )
        batch_y = torch.tensor(
            targets[i:i+batch_size], dtype=torch.float32, device=device
        )

        pred_mean, pred_log_var = model(batch_x)
        loss = gaussian_nll_loss(pred_mean, pred_log_var, batch_y, var_reg_weight=0.0)
        loss.backward()

        # Accumulate mean absolute gradient per feature
        grads = batch_x.grad.abs().mean(dim=0).cpu().numpy()
        grad_accum += grads
        n_batches += 1

        model.zero_grad()

    # Average across batches
    grad_importance = grad_accum / max(n_batches, 1)

    # Normalize to sum to 1
    total = grad_importance.sum()
    if total > 0:
        grad_importance = grad_importance / total

    return grad_importance


# ---------------------------------------------------------------------------
# Method 2: Permutation importance
# ---------------------------------------------------------------------------

@torch.no_grad()
def permutation_importance(
    model: WorldModel,
    states: np.ndarray,
    targets: np.ndarray,
    feature_names: list[str],
    device: torch.device,
    n_permutations: int = 10,
    batch_size: int = 512,
) -> dict[str, dict[str, float]]:
    """
    Permutation importance: for each feature, shuffle it and measure NLL increase.

    Higher NLL increase = more important feature.
    """
    model.eval()
    n_features = states.shape[1]

    # Baseline NLL
    base_nll = _compute_nll(model, states, targets, device, batch_size)
    logger.info("permutation baseline_nll=%.4f", base_nll)

    importance = {}
    for feat_idx in range(n_features):
        nll_drops = []
        for _ in range(n_permutations):
            permuted = states.copy()
            permuted[:, feat_idx] = np.random.permutation(permuted[:, feat_idx])
            perm_nll = _compute_nll(model, permuted, targets, device, batch_size)
            nll_drops.append(perm_nll - base_nll)

        mean_drop = float(np.mean(nll_drops))
        std_drop = float(np.std(nll_drops))
        importance[feature_names[feat_idx]] = {
            "mean_nll_increase": mean_drop,
            "std": std_drop,
            "importance_score": mean_drop / max(abs(base_nll), 1e-8),
        }

    return importance


@torch.no_grad()
def _compute_nll(
    model: WorldModel,
    states: np.ndarray,
    targets: np.ndarray,
    device: torch.device,
    batch_size: int = 512,
) -> float:
    """Compute average NLL over a dataset."""
    model.eval()
    all_nll = []
    for i in range(0, len(states), batch_size):
        batch_x = torch.tensor(states[i:i+batch_size], dtype=torch.float32).to(device)
        batch_y = torch.tensor(targets[i:i+batch_size], dtype=torch.float32).to(device)
        mean, log_var = model(batch_x)
        variance = torch.exp(log_var)
        nll = 0.5 * (torch.log(2 * np.pi * variance + 1e-12) +
                      (batch_y - mean) ** 2 / (variance + 1e-8))
        all_nll.append(nll.mean().item())
    return float(np.mean(all_nll))


# ---------------------------------------------------------------------------
# Method 3: Branch (group) ablation
# ---------------------------------------------------------------------------

def branch_ablation(
    model: WorldModel,
    states: np.ndarray,
    targets: np.ndarray,
    feature_names: list[str],
    device: torch.device,
    batch_size: int = 512,
) -> dict[str, dict[str, float]]:
    """
    Ablate entire feature branches and measure NLL degradation.

    For each feature group (1min, 15min, 1h, structure), zero out all features
    in that group and measure the NLL increase.
    """
    # Baseline NLL
    base_nll = _compute_nll(model, states, targets, device, batch_size)

    # Get feature groups from model
    group_importance = {}
    for group in model.feature_groups:
        indices = group.indices
        # Zero out this group's features
        ablated = states.copy()
        ablated[:, indices] = 0.0

        ablated_nll = _compute_nll(model, ablated, targets, device, batch_size)
        nll_increase = ablated_nll - base_nll

        group_importance[group.name] = {
            "n_features": len(indices),
            "baseline_nll": base_nll,
            "ablated_nll": ablated_nll,
            "nll_increase": nll_increase,
            "importance_score": nll_increase / max(abs(base_nll), 1e-8),
            "feature_names": [feature_names[i] for i in indices],
        }

    return group_importance


# ---------------------------------------------------------------------------
# Method 4: First-layer weight magnitude
# ---------------------------------------------------------------------------

def weight_magnitude_importance(
    model: WorldModel,
    feature_names: list[str],
) -> dict[str, float]:
    """
    Feature importance from first-layer weight magnitudes.

    For each branch encoder, the first linear layer's weights indicate
    how much each input feature contributes to the branch's output.
    """
    importance = {name: 0.0 for name in feature_names}

    for group in model.feature_groups:
        branch = model.branches[group.name]
        # Get first linear layer weights: shape (hidden_dim, input_dim)
        first_layer = None
        for module in branch.net:
            if isinstance(module, torch.nn.Linear):
                first_layer = module
                break

        if first_layer is None:
            continue

        with torch.no_grad():
            # Weight L2 norm per input feature
            weight = first_layer.weight.data  # (hidden, input_dim)
            feat_importance = weight.norm(dim=0).cpu().numpy()  # (input_dim,)

            # Map to global feature indices
            for local_idx, global_idx in enumerate(group.indices):
                importance[feature_names[global_idx]] = float(feat_importance[local_idx])

    # Normalize
    total = sum(importance.values())
    if total > 0:
        importance = {k: v / total for k, v in importance.items()}

    return importance


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_importance_report(
    grad_imp: np.ndarray,
    perm_imp: dict[str, dict[str, float]],
    branch_imp: dict[str, dict[str, float]],
    weight_imp: dict[str, float],
    feature_names: list[str],
    output_path: str,
) -> str:
    """Generate comprehensive feature importance report."""
    lines = []
    lines.append("=" * 80)
    lines.append("FEATURE IMPORTANCE ANALYSIS")
    lines.append("=" * 80)

    # --- Gradient-based top features ---
    lines.append("\n" + "-" * 80)
    lines.append("METHOD 1: GRADIENT-BASED IMPORTANCE (gradient magnitude)")
    lines.append("-" * 80)
    grad_sorted = sorted(
        zip(feature_names, grad_imp), key=lambda x: x[1], reverse=True
    )
    lines.append(f"\n{'Rank':<6}{'Feature':<40}{'Importance':>12}{'Cumulative':>12}")
    lines.append("-" * 70)
    cum = 0.0
    for rank, (name, imp) in enumerate(grad_sorted[:25], 1):
        cum += imp
        lines.append(f"{rank:<6}{name:<40}{imp:>12.6f}{cum:>12.4f}")

    # --- Permutation importance top features ---
    lines.append("\n" + "-" * 80)
    lines.append("METHOD 2: PERMUTATION IMPORTANCE (NLL increase when shuffled)")
    lines.append("-" * 80)
    perm_sorted = sorted(
        perm_imp.items(),
        key=lambda x: x[1]["mean_nll_increase"],
        reverse=True,
    )
    lines.append(f"\n{'Rank':<6}{'Feature':<40}{'NLL Increase':>14}{'Std':>10}{'Score':>10}")
    lines.append("-" * 80)
    for rank, (name, vals) in enumerate(perm_sorted[:25], 1):
        lines.append(
            f"{rank:<6}{name:<40}"
            f"{vals['mean_nll_increase']:>14.6f}"
            f"{vals['std']:>10.6f}"
            f"{vals['importance_score']:>10.4f}"
        )

    # --- Branch ablation ---
    lines.append("\n" + "-" * 80)
    lines.append("METHOD 3: BRANCH ABLATION (zero out entire feature groups)")
    lines.append("-" * 80)
    branch_sorted = sorted(
        branch_imp.items(),
        key=lambda x: x[1]["nll_increase"],
        reverse=True,
    )
    lines.append(f"\n{'Branch':<20}{'N Features':>10}{'NLL Increase':>14}{'Score':>10}")
    lines.append("-" * 54)
    for name, vals in branch_sorted:
        lines.append(
            f"{name:<20}{vals['n_features']:>10}"
            f"{vals['nll_increase']:>14.6f}"
            f"{vals['importance_score']:>10.4f}"
        )

    # --- Weight magnitude ---
    lines.append("\n" + "-" * 80)
    lines.append("METHOD 4: FIRST-LAYER WEIGHT MAGNITUDE")
    lines.append("-" * 80)
    weight_sorted = sorted(weight_imp.items(), key=lambda x: x[1], reverse=True)
    lines.append(f"\n{'Rank':<6}{'Feature':<40}{'Weight Norm':>12}")
    lines.append("-" * 58)
    for rank, (name, imp) in enumerate(weight_sorted[:25], 1):
        lines.append(f"{rank:<6}{name:<40}{imp:>12.6f}")

    # --- Consensus ranking ---
    lines.append("\n" + "=" * 80)
    lines.append("CONSENSUS RANKING (averaged across methods)")
    lines.append("=" * 80)

    # Combine all methods into a consensus score
    consensus: dict[str, float] = {name: 0.0 for name in feature_names}

    # Gradient: already normalized
    for i, name in enumerate(feature_names):
        consensus[name] += float(grad_imp[i]) * 100  # weight: 100

    # Permutation: normalize scores
    perm_scores = [v["mean_nll_increase"] for v in perm_imp.values()]
    perm_max = max(perm_scores) if perm_scores else 1.0
    for name, vals in perm_imp.items():
        consensus[name] += (vals["mean_nll_increase"] / max(perm_max, 1e-8)) * 100

    # Branch: distribute branch importance to its features
    for group_name, vals in branch_imp.items():
        if vals["n_features"] > 0:
            per_feat = vals["importance_score"] * 100 / vals["n_features"]
            for fname in vals["feature_names"]:
                if fname in consensus:
                    consensus[fname] += per_feat

    # Weight: already normalized
    for name, imp in weight_imp.items():
        consensus[name] += imp * 100

    consensus_sorted = sorted(consensus.items(), key=lambda x: x[1], reverse=True)
    lines.append(f"\n{'Rank':<6}{'Feature':<40}{'Consensus Score':>16}")
    lines.append("-" * 62)
    for rank, (name, score) in enumerate(consensus_sorted[:30], 1):
        lines.append(f"{rank:<6}{name:<40}{score:>16.4f}")

    # --- Feature group summary ---
    lines.append("\n" + "=" * 80)
    lines.append("FEATURE GROUP SUMMARY")
    lines.append("=" * 80)

    group_prefixes = {
        "1min": "1-minute timeframe",
        "15min": "15-minute timeframe",
        "1h": "1-hour timeframe",
        "structure": "Structure features",
    }
    for prefix, label in group_prefixes.items():
        group_features = [n for n in feature_names if n.startswith(prefix)]
        group_scores = [consensus.get(n, 0) for n in group_features]
        if group_scores:
            lines.append(
                f"\n  {label:30s}: {len(group_features):3d} features, "
                f"avg_score={np.mean(group_scores):8.4f}, "
                f"max={np.max(group_scores):8.4f}"
            )
            # Top 3 in group
            top3 = sorted(zip(group_features, group_scores), key=lambda x: x[1], reverse=True)[:3]
            for name, score in top3:
                lines.append(f"    - {name:38s} {score:8.4f}")

    # --- Feature category breakdown ---
    lines.append("\n" + "=" * 80)
    lines.append("FEATURE CATEGORY BREAKDOWN (within each timeframe)")
    lines.append("=" * 80)

    categories = {
        "return": "Returns",
        "vol": "Volatility",
        "streak": "Streak counters",
        "autocorr": "Autocorrelation",
        "spike": "Spike detection",
        "mean_rev": "Mean reversion",
        "range": "Price range",
        "body": "Candle body",
        "ATR": "ATR",
        "close": "Close price",
        "high": "High price",
        "low": "Low price",
        "open": "Open price",
        "volume": "Volume",
    }

    for cat_key, cat_label in categories.items():
        cat_features = [n for n in feature_names if cat_key in n.lower()]
        if cat_features:
            cat_scores = [consensus.get(n, 0) for n in cat_features]
            lines.append(
                f"\n  {cat_label:25s}: {len(cat_features):2d} features, "
                f"avg={np.mean(cat_scores):8.4f}, max={np.max(cat_scores):8.4f}"
            )

    report_text = "\n".join(lines)

    # Save
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Also save JSON
    json_path = output_path.replace(".txt", ".json")
    json_data = {
        "gradient_importance": {name: float(grad_imp[i]) for i, name in enumerate(feature_names)},
        "permutation_importance": perm_imp,
        "branch_ablation": {
            k: {kk: vv for kk, vv in v.items() if kk != "feature_names"}
            for k, v in branch_imp.items()
        },
        "weight_magnitude": weight_imp,
        "consensus": {name: float(score) for name, score in consensus_sorted},
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(json_data, f, indent=2, default=str)

    logger.info("report_saved path=%s json=%s", output_path, json_path)
    return report_text


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run_feature_importance(
    dataset_path: str = "data/dataset_R75_h10.npz",
    checkpoint_path: str = "checkpoints/world_model_best.pt",
    output_path: str = "checkpoints/feature_importance.txt",
    n_permutations: int = 10,
    device_str: str | None = None,
) -> str:
    """Run full feature importance analysis."""
    device = torch.device(
        device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
    )

    # Load
    states, targets, feature_names, horizon, model = load_dataset_and_model(
        dataset_path, checkpoint_path, device
    )

    # Use validation split (last 15%)
    n_val = int(len(states) * 0.15)
    val_states = states[-n_val:]
    val_targets = targets[-n_val:]

    logger.info("using val_samples=%d for importance analysis", n_val)

    t0 = time.time()

    # Method 1: Gradient importance
    logger.info("running gradient importance...")
    t1 = time.time()
    grad_imp = gradient_importance(model, val_states, val_targets, device)
    logger.info("gradient importance done in %.1fs", time.time() - t1)

    # Method 2: Permutation importance (reduced set for speed)
    logger.info("running permutation importance (n_permutations=%d)...", n_permutations)
    t1 = time.time()
    # Use a subsample for permutation importance (it's slow)
    perm_sample_size = min(5000, len(val_states))
    perm_indices = np.random.choice(len(val_states), perm_sample_size, replace=False)
    perm_states = val_states[perm_indices]
    perm_targets = val_targets[perm_indices]
    perm_imp = permutation_importance(
        model, perm_states, perm_targets, feature_names, device,
        n_permutations=n_permutations,
    )
    logger.info("permutation importance done in %.1fs", time.time() - t1)

    # Method 3: Branch ablation
    logger.info("running branch ablation...")
    t1 = time.time()
    branch_imp = branch_ablation(model, val_states, val_targets, feature_names, device)
    logger.info("branch ablation done in %.1fs", time.time() - t1)

    # Method 4: Weight magnitude
    logger.info("running weight magnitude analysis...")
    t1 = time.time()
    weight_imp = weight_magnitude_importance(model, feature_names)
    logger.info("weight magnitude done in %.1fs", time.time() - t1)

    elapsed = time.time() - t0
    logger.info("total analysis time: %.1fs", elapsed)

    # Generate report
    report_text = generate_importance_report(
        grad_imp, perm_imp, branch_imp, weight_imp, feature_names, output_path
    )

    print(report_text)
    return report_text


def main() -> None:
    parser = argparse.ArgumentParser(description="Feature importance analysis")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--checkpoint", default="checkpoints/world_model_best.pt")
    parser.add_argument("--output", default="checkpoints/feature_importance.txt")
    parser.add_argument("--n-permutations", type=int, default=10)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_feature_importance(
        dataset_path=args.dataset,
        checkpoint_path=args.checkpoint,
        output_path=args.output,
        n_permutations=args.n_permutations,
        device_str=args.device,
    )


if __name__ == "__main__":
    main()
