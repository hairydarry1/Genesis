"""
eval/diagnose_predictions.py — Diagnose why the model outputs constant predictions.

Examines:
  1. Feature distributions in training data (mean, std, zeros, NaNs)
  2. Model weight magnitudes per branch (are some branches dead?)
  3. Gradient flow through the model
  4. Sensitivity test: does the model respond to feature changes?
  5. Live vs training feature comparison
"""

from __future__ import annotations

import sys
import numpy as np
import torch

from model.world_model import build_model_from_features, WorldModel


def load_model_and_data(
    checkpoint_path: str = "checkpoints_reduced/world_model_best.pt",
    dataset_path: str = "data/dataset_R75_h10.npz",
) -> tuple[WorldModel, dict, torch.device]:
    """Load trained model and dataset."""
    device = torch.device("cpu")

    # Load checkpoint
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    feature_names = [str(f) for f in ckpt["feature_names"]]

    model = build_model_from_features(
        feature_names, horizon=ckpt["horizon"],
        hidden_dim=32, branch_depth=2, fusion_depth=2,
    )
    model.load_state_dict(ckpt["model_state_dict"])
    model = model.to(device)
    model.eval()

    # Load dataset
    data = np.load(dataset_path, allow_pickle=True)
    full_states = data["states"]
    full_targets = data["targets"]
    full_feature_names = list(data["feature_names"])

    # Filter to model's features
    name_to_idx = {name: i for i, name in enumerate(full_feature_names)}
    selected_indices = [name_to_idx[f] for f in feature_names if f in name_to_idx]
    states = full_states[:, selected_indices]

    return model, {
        "states": states,
        "targets": full_targets,
        "feature_names": feature_names,
    }, device


def check_feature_distributions(data: dict) -> None:
    """Analyze feature distributions."""
    print("\n" + "=" * 70)
    print("FEATURE DISTRIBUTION ANALYSIS")
    print("=" * 70)

    states = data["states"]
    names = data["feature_names"]

    print(f"\nDataset shape: {states.shape} (samples x features)")
    print(f"\n{'Feature':<30s} {'Mean':>10s} {'Std':>10s} {'Min':>10s} {'Max':>10s} {'%Zero':>8s} {'%NaN':>8s}")
    print("-" * 86)

    zero_counts = np.sum(states == 0, axis=0)
    nan_counts = np.sum(np.isnan(states), axis=0)
    total = len(states)

    for i, name in enumerate(names):
        col = states[:, i]
        valid = col[~np.isnan(col)]
        if len(valid) == 0:
            print(f"{name:<30s} {'ALL NaN':>10s}")
            continue
        pct_zero = zero_counts[i] / total * 100
        pct_nan = nan_counts[i] / total * 100
        print(f"{name:<30s} {np.mean(valid):10.6f} {np.std(valid):10.6f} {np.min(valid):10.6f} {np.max(valid):10.6f} {pct_zero:7.1f}% {pct_nan:7.1f}%")

    # Check correlation between features
    print("\n\nFeature correlation matrix (top correlations):")
    valid_mask = ~np.any(np.isnan(states), axis=1)
    valid_states = states[valid_mask]
    if valid_states.shape[0] > 100:
        corr = np.corrcoef(valid_states.T)
        # Find high correlations
        high_corr = []
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if abs(corr[i, j]) > 0.8:
                    high_corr.append((names[i], names[j], corr[i, j]))
        if high_corr:
            high_corr.sort(key=lambda x: abs(x[2]), reverse=True)
            for n1, n2, c in high_corr[:10]:
                print(f"  {n1:<25s} <-> {n2:<25s}: {c:.3f}")
        else:
            print("  No features with correlation > 0.8")


def check_weight_magnitudes(model: WorldModel) -> None:
    """Analyze weight magnitudes per branch to find dead branches."""
    print("\n" + "=" * 70)
    print("WEIGHT MAGNITUDE ANALYSIS (per branch)")
    print("=" * 70)

    for name, branch in model.branches.items():
        weights = []
        biases = []
        for param in branch.parameters():
            if param.dim() >= 2:
                weights.append(param.detach().cpu().numpy().flatten())
            else:
                biases.append(param.detach().cpu().numpy().flatten())

        all_w = np.concatenate(weights) if weights else np.array([0])
        all_b = np.concatenate(biases) if biases else np.array([0])

        print(f"\n  Branch: {name}")
        print(f"    Weight mean={np.mean(np.abs(all_w)):.6f}  std={np.std(all_w):.6f}  "
              f"max={np.max(np.abs(all_w)):.6f}  min={np.min(all_w):.6f}")
        print(f"    Bias   mean={np.mean(np.abs(all_b)):.6f}  std={np.std(all_b):.6f}  "
              f"max={np.max(np.abs(all_b)):.6f}  min={np.min(all_b):.6f}")

        # Check for dead neurons (all outputs near zero)
        if len(weights) > 0:
            # First layer weights
            first_w = weights[0]
            # Reshape to (hidden_dim, input_dim) for the first layer
            n_params = len(first_w)
            if n_params > 32:
                w_matrix = first_w.reshape(32, -1) if n_params % 32 == 0 else None
                if w_matrix is not None:
                    row_norms = np.linalg.norm(w_matrix, axis=1)
                    dead_rows = np.sum(row_norms < 1e-6)
                    print(f"    Dead neurons (first layer): {dead_rows}/32")

    # Check fusion layer
    print(f"\n  Fusion layer:")
    for name, param in model.fusion.named_parameters():
        if param.dim() >= 2:
            w = param.detach().cpu().numpy()
            print(f"    {name}: mean_abs={np.mean(np.abs(w)):.6f}  std={np.std(w):.6f}")

    # Check distribution head
    print(f"\n  Distribution head:")
    for name, param in model.dist_head.named_parameters():
        w = param.detach().cpu().numpy()
        print(f"    {name}: shape={w.shape}  mean={np.mean(w):.6f}  std={np.std(w):.6f}")


def check_gradient_flow(model: WorldModel, data: dict, device: torch.device) -> None:
    """Check if gradients flow through the model."""
    print("\n" + "=" * 70)
    print("GRADIENT FLOW ANALYSIS")
    print("=" * 70)

    model.train()  # Enable gradients

    # Get a batch of data
    states = torch.tensor(data["states"][:256], dtype=torch.float32).to(device)
    targets = torch.tensor(data["targets"][:256], dtype=torch.float32).to(device)

    # Forward pass
    pred_mean, pred_log_var = model(states)

    # Compute loss
    from model.world_model import gaussian_nll_loss
    loss = gaussian_nll_loss(pred_mean, pred_log_var, targets, var_reg_weight=0.01)

    # Backward pass
    loss.backward()

    print(f"\n  Loss: {loss.item():.4f}")
    print(f"  Pred mean range: [{pred_mean.min():.6f}, {pred_mean.max():.6f}]")
    print(f"  Pred mean std: {pred_mean.std():.6f}")
    print(f"  Target mean range: [{targets.min():.6f}, {targets.max():.6f}]")
    print(f"  Target std: {targets.std():.6f}")

    # Check gradients per branch
    print(f"\n  Gradient norms per branch:")
    for name, branch in model.branches.items():
        total_norm = 0
        n_params = 0
        for param in branch.parameters():
            if param.grad is not None:
                total_norm += param.grad.data.norm(2).item() ** 2
                n_params += param.grad.numel()
        if n_params > 0:
            avg_grad = (total_norm / n_params) ** 0.5
            print(f"    {name}: avg_grad_norm={avg_grad:.8f}")
        else:
            print(f"    {name}: NO GRADIENTS")

    # Fusion gradients
    total_norm = 0
    n_params = 0
    for param in model.fusion.parameters():
        if param.grad is not None:
            total_norm += param.grad.data.norm(2).item() ** 2
            n_params += param.grad.numel()
    if n_params > 0:
        avg_grad = (total_norm / n_params) ** 0.5
        print(f"    fusion: avg_grad_norm={avg_grad:.8f}")

    # Distribution head gradients
    total_norm = 0
    n_params = 0
    for param in model.dist_head.parameters():
        if param.grad is not None:
            total_norm += param.grad.data.norm(2).item() ** 2
            n_params += param.grad.numel()
    if n_params > 0:
        avg_grad = (total_norm / n_params) ** 0.5
        print(f"    dist_head: avg_grad_norm={avg_grad:.8f}")

    model.eval()


def sensitivity_test(model: WorldModel, data: dict, device: torch.device) -> None:
    """Test if model responds to controlled changes in input features."""
    print("\n" + "=" * 70)
    print("SENSITIVITY TEST: Does the model respond to feature changes?")
    print("=" * 70)

    # Get a baseline input
    baseline = torch.tensor(data["states"][1000:1001], dtype=torch.float32).to(device)

    with torch.no_grad():
        mean_base, logvar_base = model(baseline)
        mean_base = mean_base.cpu().numpy().flatten()
        logvar_base = logvar_base.cpu().numpy().flatten()

    print(f"\n  Baseline prediction (step 1-5): {mean_base[:5]}")
    print(f"  Baseline log_var (step 1-5): {logvar_base[:5]}")

    # Test each feature by perturbing it
    print(f"\n  {'Feature':<30s} {'Perturbation':>12s} {'Mean D step1':>12s} {'Mean D all':>12s}")
    print("  " + "-" * 66)

    for feat_idx in range(min(10, len(data["feature_names"]))):
        feat_name = data["feature_names"][feat_idx]

        # Perturb by +1 std
        perturbed = baseline.clone()
        perturbed[0, feat_idx] += 1.0  # +1 std (features are standardized)

        with torch.no_grad():
            mean_pert, logvar_pert = model(perturbed)
            mean_pert = mean_pert.cpu().numpy().flatten()
            logvar_pert = logvar_pert.cpu().numpy().flatten()

        delta_mean = mean_pert - mean_base
        print(f"  {feat_name:<30s} {'+1.0':>12s} {delta_mean[0]:+12.8f} {np.mean(delta_mean):+12.8f}")

    # Test with zero input
    zero_input = torch.zeros_like(baseline)
    with torch.no_grad():
        mean_zero, _ = model(zero_input)
        mean_zero = mean_zero.cpu().numpy().flatten()
    print(f"\n  Zero input prediction (step 1-5): {mean_zero[:5]}")
    print(f"  Baseline prediction  (step 1-5): {mean_base[:5]}")
    print(f"  Difference:                       {mean_zero[:5] - mean_base[:5]}")


def compare_live_features(data: dict) -> None:
    """Compare training data feature stats with what we see in live predictions."""
    print("\n" + "=" * 70)
    print("LIVE vs TRAINING FEATURE COMPARISON")
    print("=" * 70)

    states = data["states"]
    names = data["feature_names"]

    # Training stats
    print(f"\n  Training data stats (last 1000 samples):")
    recent = states[-1000:]
    print(f"  {'Feature':<30s} {'Train Mean':>10s} {'Train Std':>10s}")
    for i, name in enumerate(names):
        col = recent[:, i]
        valid = col[~np.isnan(col)]
        if len(valid) > 0:
            print(f"  {name:<30s} {np.mean(valid):10.6f} {np.std(valid):10.6f}")

    # Check if any features have near-zero variance
    print(f"\n  Features with near-zero variance (std < 0.001):")
    for i, name in enumerate(names):
        col = states[:, i]
        valid = col[~np.isnan(col)]
        if len(valid) > 0 and np.std(valid) < 0.001:
            print(f"    {name}: std={np.std(valid):.8f}")


def main():
    print("Loading model and data...")
    model, data, device = load_model_and_data()

    # Run all diagnostics
    check_feature_distributions(data)
    check_weight_magnitudes(model)
    check_gradient_flow(model, data, device)
    sensitivity_test(model, data, device)
    compare_live_features(data)

    print("\n" + "=" * 70)
    print("DIAGNOSIS COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
