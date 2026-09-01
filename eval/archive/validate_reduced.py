"""
eval/validate_reduced.py — Walk-forward validation for the reduced-feature model.

Loads the full dataset, filters to the 36 selected features, and runs
3-fold walk-forward validation comparing the reduced model against baselines.
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
from torch.utils.data import DataLoader

from model.train_reduced import SELECTED_FEATURES
from model.world_model import (
    WorldModel,
    build_model_from_features,
    gaussian_nll_loss,
)
from eval.validate import (
    load_precomputed_dataset,
    load_trained_model,
    classify_volatility_regimes,
    predict_batch,
    predict_baselines,
    compute_sample_metrics,
    compute_calibration_detail,
    fit_temperature,
    predict_batch_with_temperature,
    aggregate_folds,
    aggregate_regime_summary,
    determine_pass_fail,
    format_summary,
    REGIME_NAMES,
)

logger = structlog.get_logger(__name__)


def filter_to_selected_features(
    states: np.ndarray,
    feature_names: list[str],
    selected: list[str],
) -> tuple[np.ndarray, list[str], list[int]]:
    """Filter dataset columns to only selected features."""
    name_to_idx = {name: i for i, name in enumerate(feature_names)}
    indices = []
    names = []
    for feat in selected:
        if feat in name_to_idx:
            indices.append(name_to_idx[feat])
            names.append(feat)

    return states[:, indices].copy(), names, indices


def run_fold_evaluation(
    model: WorldModel,
    val_states: np.ndarray,
    val_targets: np.ndarray,
    horizon: int,
    device: torch.device,
) -> dict:
    """Evaluate model + baselines on a single fold's validation set.

    Applies temperature scaling: fits T on the validation set, then
    reports both raw and calibrated metrics.
    """
    # World model predictions (raw)
    wm_means, wm_vars_raw = predict_batch(model, val_states, device)

    # Fit temperature on the validation set
    temperature = fit_temperature(val_targets, wm_means, wm_vars_raw)
    logger.info("temperature.fitted T=%.4f", temperature)

    # Re-predict with temperature scaling
    wm_means_cal, wm_vars_cal = predict_batch_with_temperature(
        model, val_states, device, temperature=temperature
    )

    # Metrics: raw and calibrated
    wm_metrics_raw = compute_sample_metrics(val_targets, wm_means, wm_vars_raw)
    wm_metrics_cal = compute_sample_metrics(val_targets, wm_means_cal, wm_vars_cal)
    wm_calibration = compute_calibration_detail(
        val_targets, wm_means_cal, np.sqrt(np.maximum(wm_vars_cal, 1e-12))
    )
    wm_calibration["temperature"] = temperature

    # Baselines
    baseline_preds = predict_baselines(val_targets, horizon)
    baseline_metrics = {}
    for name, (b_means, b_vars) in baseline_preds.items():
        baseline_metrics[name] = compute_sample_metrics(val_targets, b_means, b_vars)

    # Per-regime breakdown (using calibrated predictions)
    regimes = classify_volatility_regimes(val_targets, None)
    regime_metrics = {}
    for regime_id in sorted(set(regimes)):
        regime_name = REGIME_NAMES.get(regime_id, f"regime_{regime_id}")
        mask = regimes == regime_id
        if mask.sum() < 5:
            continue
        wm_regime = compute_sample_metrics(val_targets[mask], wm_means_cal[mask], wm_vars_cal[mask])
        bl_regime = {}
        for name, (b_means, b_vars) in baseline_preds.items():
            bl_regime[name] = compute_sample_metrics(
                val_targets[mask], b_means[mask], b_vars[mask]
            )
        regime_metrics[regime_name] = {
            "world_model": wm_regime,
            "baselines": bl_regime,
            "n_samples": int(mask.sum()),
        }

    return {
        "world_model": wm_metrics_cal,
        "world_model_raw": wm_metrics_raw,
        "baselines": baseline_metrics,
        "calibration": wm_calibration,
        "regime_metrics": regime_metrics,
    }


def run_validation(
    dataset_path: str = "data/dataset_R75_h10.npz",
    checkpoint_path: str = "checkpoints_reduced/world_model_best.pt",
    output_dir: str = "checkpoints_reduced/validation",
    train_days: int = 60,
    val_days: int = 30,
    step_days: int = 30,
) -> dict:
    """Run walk-forward validation on the reduced model."""
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    logger.info("validate_reduced.start device=%s", device)

    # Load full dataset
    ds = load_precomputed_dataset(dataset_path)
    full_states = ds["states"]
    targets = ds["targets"]
    timestamps = ds["timestamps"]
    feature_names = ds["feature_names"]
    horizon = targets.shape[1]

    logger.info(
        "validate_reduced.full_dataset n=%d features=%d",
        len(full_states), len(feature_names),
    )

    # Filter to selected features
    states, selected_names, _ = filter_to_selected_features(
        full_states, feature_names, SELECTED_FEATURES
    )
    logger.info(
        "validate_reduced.filtered n_features=%d (kept %d of %d)",
        len(selected_names), len(selected_names), len(feature_names),
    )

    # Load trained reduced model
    model, saved_temperature = load_trained_model(checkpoint_path, device)
    logger.info("validate_reduced.model_loaded saved_temperature=%.4f", saved_temperature)

    # Build walk-forward splits
    ts_min = pd.Timestamp(timestamps.min())
    ts_max = pd.Timestamp(timestamps.max())
    total_days = (ts_max - ts_min).days
    logger.info(
        "validate_reduced.data_range start=%s end=%s days=%d",
        ts_min.date(), ts_max.date(), total_days,
    )

    fold_results = []
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
        val_indices = np.where(val_mask)[0]

        if len(val_indices) > 0:
            t0 = time.time()
            result = run_fold_evaluation(
                model=model,
                val_states=states[val_indices],
                val_targets=targets[val_indices],
                horizon=horizon,
                device=device,
            )
            elapsed = time.time() - t0

            n_val = len(val_indices)
            wm = result["world_model"]
            rw = result["baselines"].get("random_walk", {})

            logger.info(
                "validate_reduced.fold fold=%d [%s -> %s] n=%d "
                "WM_NLL=%.4f RW_NLL=%.4f 1sig=%.1f%% eval=%.1fs",
                fold_idx,
                val_start.date(), val_end.date(),
                n_val,
                wm.get("nll", 0),
                rw.get("nll", 0),
                wm.get("coverage_1sigma", 0) * 100,
                elapsed,
            )

            # Per-regime summary for this fold
            for rname, rm in result["regime_metrics"].items():
                wm_r = rm["world_model"]
                bl_rw = rm["baselines"].get("random_walk", {})
                logger.info(
                    "  [%s] n=%d WM_NLL=%.4f RW_NLL=%.4f",
                    rname, rm["n_samples"],
                    wm_r.get("nll", 0), bl_rw.get("nll", 0),
                )

            fold_results.append({
                "fold": fold_idx,
                "val_start": str(val_start.date()),
                "val_end": str(val_end.date()),
                "n_val": n_val,
                **result,
                "eval_time_s": elapsed,
            })
            fold_idx += 1

        offset_days += step_days

    if not fold_results:
        logger.error("validate_reduced.no_folds")
        raise ValueError("No valid folds")

    # Aggregate across folds
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
                if key in m:
                    vals.append(m[key])
                    weights.append(f["n_val"])
            if vals:
                weighted[key] = float(np.average(vals, weights=weights))
        agg[model_name] = weighted

    # Aggregate calibration gaps
    gaps = [f["calibration"].get("calibration_gap", 0) for f in fold_results]
    agg["mean_calibration_gap"] = float(np.mean(gaps))

    # Per-regime aggregate
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
    # Relative catastrophic check: WM NLL worse than random-walk by > margin
    # (Gaussian NLL is always > 0, so absolute thresholds are meaningless).
    catastrophic_margin = 0.5
    for f in fold_results:
        wm_nll = f["world_model"].get("nll", float("inf"))
        rw_nll = f["baselines"].get("random_walk", {}).get("nll", float("inf"))
        if wm_nll > rw_nll + catastrophic_margin:
            pass_fail["criteria"]["4_no_catastrophic_failure"]["passed"] = False
            pass_fail["overall"] = False
            pass_fail["verdict"] = "FAIL"

    # Save results
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    report = {
        "config": {
            "dataset": dataset_path,
            "checkpoint": checkpoint_path,
            "n_features": len(selected_names),
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

    # Print summary
    wm = agg.get("world_model", {})
    rw = agg.get("random_walk", {})
    pers = agg.get("persistence", {})
    mr = agg.get("mean_reversion", {})

    print("\n" + "=" * 70)
    print("REDUCED MODEL WALK-FORWARD VALIDATION RESULTS")
    print(f"Features: {len(selected_names)} | Folds: {len(fold_results)}")
    print("=" * 70)
    print(f"\n{'Model':<20s} {'NLL':>8s} {'MAE':>10s} {'1sig':>8s} {'2sig':>8s}")
    print("-" * 55)
    print(f"{'World Model':<20s} {wm.get('nll', 0):8.4f} {wm.get('mae', 0):10.6f} {wm.get('coverage_1sigma', 0):7.1%} {wm.get('coverage_2sigma', 0):7.1%}")
    print(f"{'Persistence':<20s} {pers.get('nll', 0):8.4f} {pers.get('mae', 0):10.6f} {pers.get('coverage_1sigma', 0):7.1%} {pers.get('coverage_2sigma', 0):7.1%}")
    print(f"{'Random Walk':<20s} {rw.get('nll', 0):8.4f} {rw.get('mae', 0):10.6f} {rw.get('coverage_1sigma', 0):7.1%} {rw.get('coverage_2sigma', 0):7.1%}")
    print(f"{'Mean Reversion':<20s} {mr.get('nll', 0):8.4f} {mr.get('mae', 0):10.6f} {mr.get('coverage_1sigma', 0):7.1%} {mr.get('coverage_2sigma', 0):7.1%}")

    print(f"\nCalibration gap: {agg.get('mean_calibration_gap', 0):.4f}")

    # Per-fold detail
    print(f"\n{'Fold':<6s} {'Period':<25s} {'N':>5s} {'WM NLL':>8s} {'RW NLL':>8s} {'Beat RW?':>8s}")
    print("-" * 65)
    for f in fold_results:
        wm_nll = f["world_model"].get("nll", 0)
        rw_nll = f["baselines"].get("random_walk", {}).get("nll", 0)
        beat = "YES" if wm_nll < rw_nll else "NO"
        print(f"{f['fold']:<6d} {f['val_start']} -> {f['val_end']} {f['n_val']:5d} {wm_nll:8.4f} {rw_nll:8.4f} {beat:>8s}")

    # Per-regime aggregate
    print(f"\n{'Regime':<10s} {'N':>8s} {'WM NLL':>8s} {'1sig':>8s} {'2sig':>8s}")
    print("-" * 45)
    for rname, rs in sorted(regime_summary.items(), key=lambda x: x[1]["n_samples"], reverse=True):
        print(f"{rname:<10s} {rs['n_samples']:8d} {rs['avg_nll']:8.4f} {rs['avg_coverage_1sigma']:7.1%} {rs['avg_coverage_2sigma']:7.1%}")

    print(f"\nVerdict: {pass_fail.get('verdict', 'UNKNOWN')}")
    print("=" * 70)

    return report


def main():
    parser = argparse.ArgumentParser(description="Walk-forward validation for reduced model")
    parser.add_argument("--dataset", default="data/dataset_R75_h10.npz")
    parser.add_argument("--checkpoint", default="checkpoints_reduced/world_model_best.pt")
    parser.add_argument("--output-dir", default="checkpoints_reduced/validation")
    parser.add_argument("--train-days", type=int, default=60)
    parser.add_argument("--val-days", type=int, default=30)
    parser.add_argument("--step-days", type=int, default=30)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    run_validation(
        dataset_path=args.dataset,
        checkpoint_path=args.checkpoint,
        output_dir=args.output_dir,
        train_days=args.train_days,
        val_days=args.val_days,
        step_days=args.step_days,
    )


if __name__ == "__main__":
    main()
