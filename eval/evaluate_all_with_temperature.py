"""
eval/evaluate_all_with_temperature.py

Batch walk-forward evaluation of ALL model checkpoints with temperature
scaling applied from the saved checkpoint value.

Evaluates:
  1. checkpoints_reg_A/world_model_best.pt   (53 features, drop=0.2, wd=1e-3)
  2. checkpoints_reg_B/world_model_best.pt   (53 features, drop=0.15, wd=5e-4)
  3. checkpoints_reduced/world_model_best.pt (53 features, original regularization)
  4. checkpoints/world_model_best.pt         (89 features, full model)

Each gets the same treatment: temperature T is loaded from the checkpoint,
applied as var_cal = T * var_raw during walk-forward evaluation.

Usage:
    python -m eval.evaluate_all_with_temperature
    python -m eval.evaluate_all_with_temperature --val-days 30 --train-days 60
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import torch

from eval.validate import (
    load_precomputed_dataset,
    load_trained_model,
    predict_batch,
    predict_batch_with_temperature,
    predict_baselines,
    compute_sample_metrics,
    compute_calibration_detail,
    fit_temperature,
    classify_volatility_regimes,
    aggregate_folds,
    aggregate_regime_summary,
    determine_pass_fail,
    format_summary,
    REGIME_NAMES,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Checkpoints to evaluate
# ---------------------------------------------------------------------------
CHECKPOINTS = [
    {
        "name": "v7 (53 feat, sep_var, grad_bal)",
        "path": "checkpoints_v7/world_model_best.pt",
        "output_dir": "checkpoints_v7/validation_temperature",
    },
    {
        "name": "reg_A (53 feat, drop=0.2, wd=1e-3)",
        "path": "checkpoints_reg_A/world_model_best.pt",
        "output_dir": "checkpoints_reg_A/validation_temperature",
    },
    {
        "name": "reg_B (53 feat, drop=0.15, wd=5e-4)",
        "path": "checkpoints_reg_B/world_model_best.pt",
        "output_dir": "checkpoints_reg_B/validation_temperature",
    },
    {
        "name": "reduced (53 feat, original reg)",
        "path": "checkpoints_reduced/world_model_best.pt",
        "output_dir": "checkpoints_reduced/validation_temperature",
    },
    {
        "name": "full (89 feat)",
        "path": "checkpoints/world_model_best.pt",
        "output_dir": "checkpoints/validation_temperature",
    },
]


def evaluate_one_checkpoint(
    ckpt_info: dict,
    dataset_path: str,
    train_days: int,
    val_days: int,
    step_days: int,
    device: torch.device,
) -> dict:
    """Run walk-forward evaluation for a single checkpoint with temperature."""
    name = ckpt_info["name"]
    ckpt_path = ckpt_info["path"]
    output_dir = ckpt_info["output_dir"]

    if not Path(ckpt_path).exists():
        logger.warning("checkpoint.not_found path=%s", ckpt_path)
        return {"name": name, "status": "MISSING"}

    logger.info("evaluating name=%s path=%s", name, ckpt_path)

    # Load dataset
    ds = load_precomputed_dataset(dataset_path)
    states = ds["states"]
    targets = ds["targets"]
    timestamps = ds["timestamps"]
    feature_names = ds["feature_names"]
    horizon = targets.shape[1]

    # Load model (temperature defaults to 1.0 if not in checkpoint)
    model, saved_temperature = load_trained_model(ckpt_path, device)
    logger.info("loaded name=%s saved_temperature=%.4f", name, saved_temperature)

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

        train_mask = (timestamps >= np.datetime64(train_start)) & (timestamps < np.datetime64(train_end))
        val_mask = (timestamps >= np.datetime64(val_start)) & (timestamps < np.datetime64(val_end))
        train_indices = np.where(train_mask)[0]
        val_indices = np.where(val_mask)[0]

        if len(val_indices) > 0:
            folds.append({
                "fold": fold_idx,
                "val_start": val_start,
                "val_end": val_end,
                "train_indices": train_indices,
                "val_indices": val_indices,
                "n_val": len(val_indices),
            })
            fold_idx += 1

        offset_days += step_days

    if not folds:
        logger.error("no_folds name=%s", name)
        return {"name": name, "status": "NO_FOLDS"}

    # Evaluate each fold: fit temperature on TRAINING data, apply to VALIDATION data
    fold_results = []
    all_fold_temps = []
    for f in folds:
        t0 = time.time()
        train_states_f = states[f["train_indices"]]
        train_targets_f = targets[f["train_indices"]]
        val_states_f = states[f["val_indices"]]
        val_targets_f = targets[f["val_indices"]]

        # World model predictions on training set (to fit temperature)
        wm_means_train, wm_vars_train = predict_batch(model, train_states_f, device)

        # Fit temperature on training data: T = E[(y-mu)^2] / E[sigma^2]
        # This is the correct approach: fit T on training, apply to validation
        fold_temperature = fit_temperature(train_targets_f, wm_means_train, wm_vars_train)
        all_fold_temps.append(fold_temperature)

        # World model predictions on validation set (raw)
        wm_means_raw, wm_vars_raw = predict_batch(model, val_states_f, device)

        # Apply temperature scaling: var_cal = T * var_raw
        wm_vars_cal = wm_vars_raw * fold_temperature

        # Compute metrics with temperature-scaled predictions
        wm_metrics = compute_sample_metrics(val_targets_f, wm_means_raw, wm_vars_cal)
        wm_calibration = compute_calibration_detail(
            val_targets_f, wm_means_raw, np.sqrt(np.maximum(wm_vars_cal, 1e-12))
        )
        wm_calibration["temperature"] = fold_temperature
        wm_calibration["saved_temperature"] = saved_temperature

        # Baselines
        baseline_preds = predict_baselines(val_targets_f, horizon)
        baseline_metrics = {}
        for bname, (b_means, b_vars) in baseline_preds.items():
            baseline_metrics[bname] = compute_sample_metrics(val_targets_f, b_means, b_vars)

        # Per-regime breakdown
        regimes = classify_volatility_regimes(val_targets_f, None)
        regime_metrics = {}
        for regime_id in sorted(set(regimes)):
            regime_name = REGIME_NAMES.get(regime_id, f"regime_{regime_id}")
            mask = regimes == regime_id
            if mask.sum() < 5:
                continue
            wm_r = compute_sample_metrics(val_targets_f[mask], wm_means_raw[mask], wm_vars_cal[mask])
            bl_r = {}
            for bname, (b_means, b_vars) in baseline_preds.items():
                bl_r[bname] = compute_sample_metrics(val_targets_f[mask], b_means[mask], b_vars[mask])
            regime_metrics[regime_name] = {
                "world_model": wm_r,
                "baselines": bl_r,
                "n_samples": int(mask.sum()),
            }

        elapsed = time.time() - t0

        wm_nll = wm_metrics.get("nll", 0)
        rw_nll = baseline_metrics.get("random_walk", {}).get("nll", 0)
        beat = wm_nll < rw_nll

        logger.info(
            "fold fold=%d [%s -> %s] n=%d WM_NLL=%.4f RW_NLL=%.4f T=%.4f beat_RW=%s 1sig=%.1f%% eval=%.1fs",
            f["fold"], f["val_start"].date(), f["val_end"].date(), f["n_val"],
            wm_nll, rw_nll, fold_temperature, "YES" if beat else "NO",
            wm_metrics.get("coverage_1sigma", 0) * 100, elapsed,
        )

        # Per-regime in this fold
        for rname, rm in regime_metrics.items():
            wm_r = rm["world_model"]
            bl_rw = rm["baselines"].get("random_walk", {})
            logger.info(
                "  [%s] n=%d WM_NLL=%.4f RW_NLL=%.4f",
                rname, rm["n_samples"],
                wm_r.get("nll", 0), bl_rw.get("nll", 0),
            )

        fold_results.append({
            "fold": f["fold"],
            "val_start": str(f["val_start"].date()),
            "val_end": str(f["val_end"].date()),
            "n_val": f["n_val"],
            "world_model": wm_metrics,
            "baselines": baseline_metrics,
            "calibration": wm_calibration,
            "regime_metrics": regime_metrics,
            "eval_time_s": elapsed,
        })

    # Aggregate
    total_val = sum(f["n_val"] for f in fold_results)
    agg = {"n_folds": len(fold_results), "total_val_samples": total_val}

    for model_name in ["world_model", "persistence", "random_walk", "mean_reversion"]:
        weighted = {}
        for key in ["nll", "mae", "rmse", "coverage_1sigma", "coverage_2sigma",
                      "mean_predicted_std", "actual_std"]:
            vals = []
            weights = []
            for f in fold_results:
                m = f["world_model"] if model_name == "world_model" else f["baselines"].get(model_name, {})
                if key in m and f["n_val"] > 0:
                    vals.append(m[key])
                    weights.append(f["n_val"])
            if vals:
                weighted[key] = float(np.average(vals, weights=weights))
        agg[model_name] = weighted

    # Calibration gap
    gaps = [f["calibration"].get("calibration_gap", 0) for f in fold_results]
    agg["mean_calibration_gap"] = float(np.mean(gaps)) if gaps else 0

    # Regime summary
    regime_data = {}
    for f in fold_results:
        for rname, rm in f["regime_metrics"].items():
            if rname not in regime_data:
                regime_data[rname] = []
            regime_data[rname].append({
                "n_samples": rm["n_samples"],
                "wm_nll": rm["world_model"]["nll"],
                "wm_mae": rm["world_model"]["mae"],
                "wm_coverage_1sigma": rm["world_model"]["coverage_1sigma"],
                "wm_coverage_2sigma": rm["world_model"]["coverage_2sigma"],
            })

    regime_summary = {}
    for rname, entries in regime_data.items():
        regime_summary[rname] = {
            "n_samples": sum(e["n_samples"] for e in entries),
            "avg_nll": float(np.mean([e["wm_nll"] for e in entries])),
            "avg_mae": float(np.mean([e["wm_mae"] for e in entries])),
            "avg_coverage_1sigma": float(np.mean([e["wm_coverage_1sigma"] for e in entries])),
            "avg_coverage_2sigma": float(np.mean([e["wm_coverage_2sigma"] for e in entries])),
        }

    # Pass/fail
    pass_fail = determine_pass_fail(agg, regime_summary)

    # Check per-fold catastrophic failures
    for f in fold_results:
        if f["world_model"].get("nll", 0) > 0:
            pass_fail["criteria"]["4_no_catastrophic_failure"]["passed"] = False
            pass_fail["overall"] = False
            pass_fail["verdict"] = "FAIL"

    # Save report
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    avg_temperature = float(np.mean(all_fold_temps)) if all_fold_temps else saved_temperature
    report = {
        "config": {
            "checkpoint": ckpt_path,
            "saved_temperature": saved_temperature,
            "avg_temperature": avg_temperature,
            "fold_temperatures": all_fold_temps,
            "n_folds": len(fold_results),
            "train_days": train_days,
            "val_days": val_days,
            "step_days": step_days,
        },
        "aggregate": agg,
        "folds": fold_results,
        "regime_summary": regime_summary,
        "pass_fail": pass_fail,
    }

    report_path = Path(output_dir) / "validation_report.json"
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    report["name"] = name
    return report


def print_comparison_table(results: list[dict]) -> None:
    """Print a comparison table of all checkpoints."""
    print("\n" + "=" * 100)
    print("TEMPERATURE-CALIBRATED WALK-FORWARD EVALUATION COMPARISON")
    print("=" * 100)
    print(f"{'Checkpoint':<40s} {'T':>6s} {'WM NLL':>8s} {'RW NLL':>8s} {'Beat RW?':>8s} {'1sig':>6s} {'2sig':>6s} {'CalGap':>7s} {'Verdict':>10s}")
    print("-" * 100)

    for r in results:
        name = r.get("name", r.get("config", {}).get("checkpoint", "unknown"))
        if "status" in r and r["status"] not in ("OK",):
            print(f"{name:<40s} {'---':>6s} {'---':>8s} {'---':>8s} {'---':>8s} {'---':>6s} {'---':>6s} {'---':>7s} {r.get('status', '?'):>10s}")
            continue

        agg = r.get("aggregate", {})
        pf = r.get("pass_fail", {})
        cfg = r.get("config", {})

        T = cfg.get("avg_temperature", cfg.get("temperature", 1.0))
        wm = agg.get("world_model", {})
        rw = agg.get("random_walk", {})

        wm_nll = wm.get("nll", 0)
        rw_nll = rw.get("nll", 0)
        beat = "YES" if wm_nll < rw_nll else "NO"
        cal_gap = agg.get("mean_calibration_gap", 0)
        verdict = pf.get("verdict", "?")

        print(
            f"{name:<40s} {T:6.3f} {wm_nll:8.4f} {rw_nll:8.4f} {beat:>8s} "
            f"{wm.get('coverage_1sigma', 0):5.1%} {wm.get('coverage_2sigma', 0):5.1%} "
            f"{cal_gap:7.4f} {verdict:>10s}"
        )

    print("-" * 100)
    print(f"{'Full model (89 feat)':<40s} {'T':>6s} {'WM NLL':>8s} {'RW NLL':>8s} {'Beat RW?':>8s} {'1sig':>6s} {'2sig':>6s} {'CalGap':>7s} {'Verdict':>10s}")
    print("=" * 100)


def main() -> None:
    parser = argparse.ArgumentParser(description="Batch walk-forward evaluation with temperature scaling")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--val-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=30)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    device = torch.device(
        args.device if args.device else ("cuda" if torch.cuda.is_available() else "cpu")
    )
    logger.info("device=%s", device)

    results = []
    for ckpt_info in CHECKPOINTS:
        try:
            report = evaluate_one_checkpoint(
                ckpt_info,
                dataset_path=args.dataset,
                train_days=args.train_days,
                val_days=args.val_days,
                step_days=args.step_days,
                device=device,
            )
            results.append(report)
        except Exception as e:
            logger.error("eval_failed name=%s error=%s", ckpt_info["name"], str(e))
            results.append({"name": ckpt_info["name"], "status": f"ERROR: {e}"})

    # Print comparison table
    print_comparison_table(results)

    # Print detailed per-fold for each
    for r in results:
        if "status" in r and r["status"] != "OK":
            continue
        agg = r.get("aggregate", {})
        cfg = r.get("config", {})
        wm = agg.get("world_model", {})
        rw = agg.get("random_walk", {})
        pf = r.get("pass_fail", {})
        regime = r.get("regime_summary", {})

        print(f"\n{'=' * 80}")
        print(f"DETAILED: {r['name']}")
        print(f"{'=' * 80}")
        print(f"Temperature (fitted on train data): {cfg.get('avg_temperature', 1.0):.4f}")
        print(f"\n  Aggregate:")
        print(f"    WM:  NLL={wm.get('nll', 0):.4f}  MAE={wm.get('mae', 0):.6f}  1sig={wm.get('coverage_1sigma', 0):.1%}  2sig={wm.get('coverage_2sigma', 0):.1%}")
        print(f"    RW:  NLL={rw.get('nll', 0):.4f}  MAE={rw.get('mae', 0):.6f}  1sig={rw.get('coverage_1sigma', 0):.1%}  2sig={rw.get('coverage_2sigma', 0):.1%}")

        print(f"\n  Per-fold (WM NLL vs RW NLL):")
        for f in r.get("folds", []):
            wm_nll = f["world_model"].get("nll", 0)
            rw_nll = f["baselines"].get("random_walk", {}).get("nll", 0)
            fold_T = f["calibration"].get("temperature", 0)
            beat = "YES" if wm_nll < rw_nll else "NO"
            print(f"    Fold {f['fold']}: [{f['val_start']} -> {f['val_end']}]  WM={wm_nll:.4f}  RW={rw_nll:.4f}  {beat}  T={fold_T:.4f}")

        print(f"\n  Per-regime:")
        for rname, rs in sorted(regime.items(), key=lambda x: x[1]["n_samples"], reverse=True):
            print(f"    {rname:8s} (n={rs['n_samples']:6d}):  NLL={rs['avg_nll']:.4f}  1sig={rs['avg_coverage_1sigma']:.1%}  2sig={rs['avg_coverage_2sigma']:.1%}")

        print(f"\n  Verdict: {pf.get('verdict', 'UNKNOWN')}")


if __name__ == "__main__":
    main()
