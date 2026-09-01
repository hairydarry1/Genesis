"""
eval/diagnose_variance.py — Diagnose why variance is systematically underestimated.

Runs a trained model and examines:
  1. Raw predicted std vs actual std (per horizon step)
  2. Gradient magnitude comparison (mean head vs log_var head)
  3. Variance initialization vs final trained values
  4. Whether the variance head is undertrained (capacity analysis)
"""

from __future__ import annotations

import torch
import numpy as np
from model.world_model import build_model_from_features, gaussian_nll_loss
from eval.validate import load_precomputed_dataset, load_trained_model, predict_batch, predict_baselines
from torch.utils.data import DataLoader, TensorDataset
import logging

logging.basicConfig(level=logging.INFO, format="%(message)s")


def diagnose_checkpoint(ckpt_path: str, dataset_path: str = "data/dataset_R75_h10.npz"):
    """Deep diagnosis of a single checkpoint."""
    device = torch.device("cpu")
    ds = load_precomputed_dataset(dataset_path)
    states, targets, timestamps = ds["states"], ds["targets"], ds["timestamps"]

    sort_idx = np.argsort(timestamps)
    states, targets = states[sort_idx], targets[sort_idx]

    n_val = int(len(states) * 0.15)
    val_states, val_targets = states[-n_val:], targets[-n_val:]

    model, temperature = load_trained_model(ckpt_path, device)
    horizon = targets.shape[1]

    print(f"\n{'=' * 70}")
    print(f"VARIANCE DIAGNOSIS: {ckpt_path}")
    print(f"{'=' * 70}")
    print(f"Temperature: {temperature:.4f}")

    # --- 1. Per-horizon analysis ---
    means, vars_raw = predict_batch(model, val_states, device)
    vars_cal = vars_raw * temperature

    stds_raw = np.sqrt(vars_raw)
    stds_cal = np.sqrt(vars_cal)
    actual_std_per_step = np.std(val_targets, axis=0)
    mean_pred_std_raw = np.mean(stds_raw, axis=0)
    mean_pred_std_cal = np.mean(stds_cal, axis=0)

    print(f"\n--- Per-horizon step analysis ---")
    print(f"{'Step':>4s}  {'ActStd':>8s}  {'PredStd(raw)':>12s}  {'PredStd(cal)':>12s}  {'Ratio(raw)':>10s}  {'Ratio(cal)':>10s}")
    for h in range(horizon):
        r_raw = mean_pred_std_raw[h] / actual_std_per_step[h] if actual_std_per_step[h] > 0 else 0
        r_cal = mean_pred_std_cal[h] / actual_std_per_step[h] if actual_std_per_step[h] > 0 else 0
        print(f"  {h+1:2d}   {actual_std_per_step[h]:8.6f}  {mean_pred_std_raw[h]:12.6f}  {mean_pred_std_cal[h]:12.6f}  {r_raw:10.4f}  {r_cal:10.4f}")

    # --- 2. Variance head weight inspection ---
    print(f"\n--- Variance head weight statistics ---")
    log_var_head = model.dist_head.log_var_head
    mean_head = model.dist_head.mean_head

    print(f"  log_var_head.weight: shape={log_var_head.weight.shape}, "
          f"mean={log_var_head.weight.data.mean():.6f}, "
          f"std={log_var_head.weight.data.std():.6f}, "
          f"abs_mean={log_var_head.weight.data.abs().mean():.6f}")
    print(f"  log_var_head.bias:   shape={log_var_head.bias.shape}, "
          f"values={log_var_head.bias.data.tolist()}")
    print(f"  mean_head.weight:    shape={mean_head.weight.shape}, "
          f"mean={mean_head.weight.data.mean():.6f}, "
          f"std={mean_head.weight.data.std():.6f}, "
          f"abs_mean={mean_head.weight.data.abs().mean():.6f}")
    print(f"  mean_head.bias:      shape={mean_head.bias.shape}, "
          f"std={mean_head.bias.data.std():.6f}")

    # --- 3. Gradient magnitude analysis ---
    print(f"\n--- Gradient magnitude analysis (1 batch) ---")
    batch_x = torch.tensor(val_states[:256], dtype=torch.float32)
    batch_y = torch.tensor(val_targets[:256], dtype=torch.float32)

    model.train()  # enable grad
    model.zero_grad()
    pred_mean, pred_log_var = model(batch_x)
    loss = gaussian_nll_loss(pred_mean, pred_log_var, batch_y, var_reg_weight=0.01)
    loss.backward()

    # Collect gradient norms per component
    grad_norms = {}
    for name, param in model.named_parameters():
        if param.grad is not None:
            grad_norms[name] = param.grad.data.norm().item()

    # Print variance-related gradients
    var_grads = {k: v for k, v in grad_norms.items() if "log_var" in k or "var" in k.lower()}
    mean_grads = {k: v for k, v in grad_norms.items() if "mean" in k.lower() and "log_var" not in k}
    fusion_grads = {k: v for k, v in grad_norms.items() if "fusion" in k}

    total_var_grad = sum(v for v in var_grads.values())
    total_mean_grad = sum(v for v in mean_grads.values())
    total_fusion_grad = sum(v for v in fusion_grads.values())

    print(f"  Total log_var head grad norm:  {total_var_grad:.6f}")
    print(f"  Total mean head grad norm:     {total_mean_grad:.6f}")
    print(f"  Total fusion layer grad norm:  {total_fusion_grad:.6f}")
    if total_mean_grad > 0:
        print(f"  Mean/Var gradient ratio:       {total_mean_grad / max(total_var_grad, 1e-10):.2f}x")

    for name in sorted(var_grads.keys()):
        print(f"    {name:50s}: grad_norm={var_grads[name]:.6f}")

    # --- 4. Check shared layer bottleneck ---
    print(f"\n--- Shared layer bottleneck analysis ---")
    shared = model.dist_head.shared
    total_params_shared = sum(p.numel() for p in shared.parameters())
    total_params_mean = sum(p.numel() for p in mean_head.parameters())
    total_params_var = sum(p.numel() for p in log_var_head.parameters())
    print(f"  Shared layer params:  {total_params_shared:,}")
    print(f"  Mean head params:     {total_params_mean:,}")
    print(f"  LogVar head params:   {total_params_var:,}")
    print(f"  Total dist head:      {total_params_shared + total_params_mean + total_params_var:,}")
    print(f"  Var/Mean param ratio: {total_params_var / max(total_params_mean, 1):.2f}x")

    # --- 5. Coverage analysis (calibrated) ---
    print(f"\n--- Coverage analysis (calibrated) ---")
    for sigmas in [1.0, 1.5, 2.0, 2.5, 3.0]:
        within = np.mean(np.abs(val_targets - means) <= sigmas * stds_cal)
        expected = {1.0: 0.6827, 1.5: 0.8664, 2.0: 0.9545, 2.5: 0.9876, 3.0: 0.9973}
        exp_cov = expected.get(sigmas, 0)
        print(f"  {sigmas:.1f}sigma: actual={within:.1%}  expected={exp_cov:.1%}  gap={abs(within - exp_cov):.1%}")

    # --- 6. NLL decomposition ---
    print(f"\n--- NLL decomposition ---")
    nll_raw_vals = 0.5 * (np.log(2 * np.pi * vars_raw) + (val_targets - means) ** 2 / (vars_raw + 1e-12))
    nll_cal_vals = 0.5 * (np.log(2 * np.pi * vars_cal) + (val_targets - means) ** 2 / (vars_cal + 1e-12))

    # Split into bias term and fit term
    bias_raw = 0.5 * np.log(2 * np.pi * vars_raw)
    fit_raw = 0.5 * (val_targets - means) ** 2 / (vars_raw + 1e-12)
    bias_cal = 0.5 * np.log(2 * np.pi * vars_cal)
    fit_cal = 0.5 * (val_targets - means) ** 2 / (vars_cal + 1e-12)

    print(f"  NLL (raw):     {np.mean(nll_raw_vals):.4f}  (bias={np.mean(bias_raw):.4f}, fit={np.mean(fit_raw):.4f})")
    print(f"  NLL (cal):     {np.mean(nll_cal_vals):.4f}  (bias={np.mean(bias_cal):.4f}, fit={np.mean(fit_cal):.4f})")
    print(f"  MSE:           {np.mean((val_targets - means) ** 2):.10f}")
    print(f"  Avg pred var(raw): {np.mean(vars_raw):.10f}")
    print(f"  Avg pred var(cal): {np.mean(vars_cal):.10f}")
    print(f"  Actual var:        {np.var(val_targets):.10f}")
    print(f"  Var ratio (raw):   {np.mean(vars_raw) / np.var(val_targets):.4f}")
    print(f"  Var ratio (cal):   {np.mean(vars_cal) / np.var(val_targets):.4f}")

    model.eval()
    return temperature


def diagnose_gradient_flow(ckpt_path: str, dataset_path: str = "data/dataset_R75_h10.npz"):
    """Analyze gradient flow during training to see if variance head is undertrained."""
    device = torch.device("cpu")
    ds = load_precomputed_dataset(dataset_path)
    states, targets, timestamps = ds["states"], ds["targets"], ds["timestamps"]

    sort_idx = np.argsort(timestamps)
    states, targets = states[sort_idx], targets[sort_idx]

    n_val = int(len(states) * 0.15)
    train_states, val_states = states[:-n_val], states[-n_val:]
    train_targets, val_targets = targets[:-n_val], targets[-n_val:]

    model, _ = load_trained_model(ckpt_path, device)
    model.train()

    # Check initial variance vs trained variance
    # Re-initialize to check
    print(f"\n--- Gradient flow during fine-tuning (10 steps) ---")

    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-4)
    dataset = TensorDataset(
        torch.tensor(train_states[:5000], dtype=torch.float32),
        torch.tensor(train_targets[:5000], dtype=torch.float32),
    )
    loader = DataLoader(dataset, batch_size=256, shuffle=True)

    for step in range(10):
        for states_b, targets_b in loader:
            optimizer.zero_grad()
            pred_mean, pred_log_var = model(states_b)
            loss = gaussian_nll_loss(pred_mean, pred_log_var, targets_b, var_reg_weight=0.01)
            loss.backward()

            # Collect gradient norms
            var_grad = 0
            mean_grad = 0
            for name, param in model.named_parameters():
                if param.grad is not None:
                    g = param.grad.data.norm().item()
                    if "log_var" in name:
                        var_grad += g
                    elif "mean" in name:
                        mean_grad += g

            ratio = mean_grad / max(var_grad, 1e-10)
            print(f"  Step {step}: loss={loss.item():.4f}  mean_grad={mean_grad:.6f}  var_grad={var_grad:.6f}  ratio={ratio:.2f}x")
            optimizer.step()
            break  # one batch per step

    # After fine-tuning, check predicted variance
    model.eval()
    means_f, vars_f = predict_batch(model, val_states, device)
    print(f"\n  After 10 steps fine-tuning:")
    print(f"    Avg predicted std: {np.mean(np.sqrt(vars_f)):.6f}")
    print(f"    Actual std:        {np.std(val_targets):.6f}")
    print(f"    Var ratio:         {np.mean(vars_f) / np.var(val_targets):.4f}")


if __name__ == "__main__":
    import sys

    checkpoints = [
        ("checkpoints_reg_A/world_model_best.pt", "reg_A"),
        ("checkpoints_reduced/world_model_best.pt", "reduced"),
        ("checkpoints/world_model_best.pt", "full"),
    ]

    for path, name in checkpoints:
        print(f"\n{'#' * 70}")
        print(f"# {name}")
        print(f"{'#' * 70}")
        try:
            T = diagnose_checkpoint(path)
            diagnose_gradient_flow(path)
        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
