"""
eval/analyze_predictions.py — Analyze prediction accuracy from logged predictions.

Reads JSONL prediction logs and computes:
  - Direction accuracy (step 1, step 5, step 10)
  - Mean absolute error at each horizon
  - Calibration (actual vs predicted coverage at multiple levels)
  - Rolling accuracy over time
  - Calibration plot data

Usage:
    python -m eval.analyze_predictions
    python -m eval.analyze_predictions --log checkpoints/predictions/predictions_2026-08-21.jsonl
    python -m eval.analyze_predictions --log-dir checkpoints/predictions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def load_predictions(log_path: str) -> list[dict]:
    """Load predictions from a JSONL log file."""
    records = []
    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return records


def analyze(records: list[dict]) -> dict:
    """Compute detailed accuracy metrics from prediction records."""
    # Filter to records with actual outcomes
    matched = [r for r in records if r.get("actual_returns") is not None]
    total = len(records)

    if not matched:
        return {"total": total, "matched": 0, "message": "No predictions with outcomes yet"}

    n = len(matched)
    horizon = matched[0]["horizon"]

    # Extract arrays
    means = np.array([r["means"] for r in matched])      # (n, horizon)
    variances = np.array([r["variances"] for r in matched])
    stds = np.sqrt(variances)
    actual = np.array([r["actual_returns"] for r in matched])  # (n, horizon)
    prices = np.array([r["price"] for r in matched])
    errors = actual - means

    results = {
        "total_predictions": total,
        "matched_with_outcomes": n,
        "horizon": horizon,
    }

    # --- Direction accuracy per step ---
    direction_acc = {}
    for h in range(horizon):
        # Count non-zero actual returns
        nonzero = actual[:, h] != 0
        if nonzero.sum() > 0:
            correct = np.sign(means[nonzero, h]) == np.sign(actual[nonzero, h])
            direction_acc[h + 1] = float(correct.mean())
        else:
            direction_acc[h + 1] = 0.5
    results["direction_accuracy_per_step"] = direction_acc
    results["direction_accuracy_overall"] = float(
        np.mean([v for v in direction_acc.values()])
    )

    # --- Error metrics per step ---
    mae_per_step = {}
    rmse_per_step = {}
    bias_per_step = {}
    for h in range(horizon):
        mae_per_step[h + 1] = float(np.mean(np.abs(errors[:, h])))
        rmse_per_step[h + 1] = float(np.sqrt(np.mean(errors[:, h] ** 2)))
        bias_per_step[h + 1] = float(np.mean(errors[:, h]))
    results["mae_per_step"] = mae_per_step
    results["rmse_per_step"] = rmse_per_step
    results["bias_per_step"] = bias_per_step

    # --- Calibration at multiple confidence levels ---
    levels = [0.1, 0.25, 0.5, 0.75, 0.9, 0.95]
    from scipy.stats import norm
    calibration = {}
    for level in levels:
        z = norm.ppf((1 + level) / 2)
        within = np.mean(np.abs(errors) <= z * stds, axis=0)
        avg_within = float(np.mean(within))
        calibration[f"{level:.0%}"] = {
            "predicted": level,
            "actual_mean": avg_within,
            "gap": abs(level - avg_within),
        }
    results["calibration"] = calibration

    # --- Aggregate metrics ---
    results["aggregate"] = {
        "direction_accuracy": float(
            np.mean([np.sign(means[i, 0]) == np.sign(actual[i, 0])
                     for i in range(n) if actual[i, 0] != 0])
        ),
        "mae_step1": float(np.mean(np.abs(errors[:, 0]))),
        "mae_step5": float(np.mean(np.abs(errors[:, min(4, horizon-1)]))) if horizon > 1 else None,
        "mae_step10": float(np.mean(np.abs(errors[:, min(9, horizon-1)]))) if horizon > 1 else None,
        "rmse_step1": float(np.sqrt(np.mean(errors[:, 0] ** 2))),
        "nll": float(np.mean(
            0.5 * (np.log(2 * np.pi * variances) + errors ** 2 / (variances + 1e-8))
        )),
        "coverage_1sigma": float(np.mean(np.abs(errors) <= stds)),
        "coverage_2sigma": float(np.mean(np.abs(errors) <= 2 * stds)),
    }

    # --- Rolling direction accuracy (last N predictions) ---
    for window in [10, 20, 50]:
        if n >= window:
            recent = matched[-window:]
            correct = sum(
                1 for r in recent
                if r.get("direction_correct") is True
            )
            results[f"direction_accuracy_last_{window}"] = correct / window

    # --- Per-hour breakdown (if enough data) ---
    from collections import defaultdict
    hour_results = defaultdict(list)
    for r in matched:
        hour = r["timestamp"][:13]  # "2026-08-21T23"
        hour_results[hour].append(r)

    if len(hour_results) > 1:
        hourly = {}
        for hour, recs in sorted(hour_results.items()):
            n_r = len(recs)
            correct = sum(1 for r in recs if r.get("direction_correct") is True)
            hourly[hour] = {
                "n": n_r,
                "direction_accuracy": correct / n_r if n_r > 0 else 0,
                "mean_mae": float(np.mean([r["error_mae"] for r in recs])),
            }
        results["hourly_breakdown"] = hourly

    return results


def format_report(results: dict) -> str:
    """Format analysis results as a human-readable report."""
    lines = []
    lines.append("=" * 70)
    lines.append("PREDICTION ACCURACY REPORT")
    lines.append("=" * 70)
    lines.append(f"Total predictions: {results['total_predictions']}")
    lines.append(f"Matched with outcomes: {results['matched_with_outcomes']}")
    lines.append(f"Horizon: {results.get('horizon', '?')} steps")

    if results.get("matched_with_outcomes", 0) == 0:
        lines.append("\nNo predictions with outcomes yet. Run the live inference pipeline first.")
        return "\n".join(lines)

    agg = results["aggregate"]
    lines.append(f"\n{'Aggregate Metrics':}")
    lines.append(f"  Direction accuracy (step 1):  {agg['direction_accuracy']:.1%}")
    lines.append(f"  MAE step 1:  {agg['mae_step1']:.6f}")
    if agg.get("mae_step5"):
        lines.append(f"  MAE step 5:  {agg['mae_step5']:.6f}")
    if agg.get("mae_step10"):
        lines.append(f"  MAE step 10: {agg['mae_step10']:.6f}")
    lines.append(f"  RMSE step 1: {agg['rmse_step1']:.6f}")
    lines.append(f"  NLL:         {agg['nll']:.4f}")
    lines.append(f"  Coverage 1sigma: {agg['coverage_1sigma']:.1%}")
    lines.append(f"  Coverage 2sigma: {agg['coverage_2sigma']:.1%}")

    # Direction accuracy per step
    da = results["direction_accuracy_per_step"]
    lines.append(f"\n{'Direction Accuracy per Step':}")
    lines.append(f"  {'Step':>4s}  {'Accuracy':>8s}  {'Bar':>20s}")
    for step, acc in da.items():
        bar = "#" * int(acc * 40)
        lines.append(f"  {step:4d}  {acc:7.1%}  {bar}")

    # Calibration
    cal = results["calibration"]
    lines.append(f"\n{'Calibration':}")
    lines.append(f"  {'Level':>8s}  {'Predicted':>10s}  {'Actual':>10s}  {'Gap':>8s}")
    for level_str, vals in cal.items():
        lines.append(
            f"  {level_str:>8s}  {vals['predicted']:9.1%}  "
            f"{vals['actual_mean']:9.1%}  {vals['gap']:7.1%}"
        )

    # Rolling accuracy
    lines.append(f"\n{'Rolling Direction Accuracy':}")
    for window in [10, 20, 50]:
        key = f"direction_accuracy_last_{window}"
        if key in results:
            lines.append(f"  Last {window:3d} predictions: {results[key]:.1%}")

    # Hourly breakdown
    if "hourly_breakdown" in results:
        lines.append(f"\n{'Hourly Breakdown':}")
        lines.append(f"  {'Hour':>20s}  {'N':>4s}  {'Dir Acc':>8s}  {'MAE':>10s}")
        for hour, vals in results["hourly_breakdown"].items():
            lines.append(
                f"  {hour:>20s}  {vals['n']:4d}  "
                f"{vals['direction_accuracy']:7.1%}  {vals['mean_mae']:10.6f}"
            )

    lines.append("=" * 70)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze prediction accuracy")
    parser.add_argument("--log", default=None, help="Path to a single JSONL log file")
    parser.add_argument("--log-dir", default="checkpoints/predictions", help="Directory with prediction logs")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    # Load records
    records = []
    if args.log:
        records.extend(load_predictions(args.log))
    else:
        log_dir = Path(args.log_dir)
        if log_dir.exists():
            for f in sorted(log_dir.glob("predictions_*.jsonl")):
                records.extend(load_predictions(str(f)))

    if not records:
        print("No prediction logs found.")
        print(f"  Searched: {args.log or args.log_dir}")
        sys.exit(1)

    # Analyze
    results = analyze(records)

    if args.json:
        print(json.dumps(results, indent=2, default=str))
    else:
        print(format_report(results))


if __name__ == "__main__":
    main()
