"""
model/train_all.py — Train world models for all 4 indices and compare.

Trains both tiny and wide architectures on each index, then produces
a comparison table showing which model/index combinations perform best.

Usage:
    python -m model.train_all
    python -m model.train_all --epochs 50 --indices R75 CRASH500
    python -m model.train_all --model wide --eval-only
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# Index configurations
INDEX_CONFIGS = {
    "R75": {
        "symbol": "R_75",
        "dataset": "data/dataset_R75_h10.npz",
        "output_tiny": "checkpoints_tiny",
        "output_wide": "checkpoints_wide_r75",
    },
    "VOL10": {
        "symbol": "R_10",
        "dataset": "data/dataset_volatility_10_h10.npz",
        "output_tiny": "checkpoints_vol10",
        "output_wide": "checkpoints_wide_vol10",
    },
    "BOOM500": {
        "symbol": "BOOM500",
        "dataset": "data/dataset_boom_500_h10.npz",
        "output_tiny": "checkpoints_boom_500",
        "output_wide": "checkpoints_wide_boom",
    },
    "CRASH500": {
        "symbol": "CRASH500",
        "dataset": "data/dataset_crash_500_h10.npz",
        "output_tiny": "checkpoints_crash_500_clean",
        "output_wide": "checkpoints_wide_crash",
    },
}


def train_index(index_name: str, config: dict, model_type: str, epochs: int = 50, **kwargs) -> dict:
    """Train one model on one index."""
    from model.train_tiny import train_tiny

    output_dir = config[f"output_{model_type}"]
    dataset = config["dataset"]

    if not Path(dataset).exists():
        logger.warning("dataset_not_found index=%s path=%s — skipping", index_name, dataset)
        return {"error": "dataset not found"}

    logger.info("=" * 60)
    logger.info("TRAINING %s / %s", index_name, model_type)
    logger.info("=" * 60)

    t0 = time.time()
    results = train_tiny(
        dataset_path=dataset,
        output_dir=output_dir,
        epochs=epochs,
        model_type=model_type,
        **kwargs,
    )
    elapsed = time.time() - t0
    results["elapsed"] = elapsed
    results["index"] = index_name
    results["model_type"] = model_type
    logger.info("done %s/%s in %.0fs", index_name, model_type, elapsed)
    return results


def evaluate_index(index_name: str, config: dict, model_type: str) -> dict:
    """Run walk-forward evaluation for one model."""
    from model.train_tiny import evaluate_walk_forward

    output_dir = config[f"output_{model_type}"]
    ckpt_path = f"{output_dir}/world_model_best.pt"

    if not Path(ckpt_path).exists():
        logger.warning("checkpoint_not_found index=%s model=%s", index_name, model_type)
        return {"error": "checkpoint not found"}

    logger.info("evaluating %s / %s", index_name, model_type)
    report = evaluate_walk_forward(
        checkpoint_path=ckpt_path,
        dataset_path=config["dataset"],
    )
    return report


def build_comparison(reports: dict) -> str:
    """Build a comparison table from all reports."""
    lines = []
    lines.append("")
    lines.append("=" * 100)
    lines.append("MODEL COMPARISON: ALL INDICES")
    lines.append("=" * 100)
    lines.append("")

    # Header
    lines.append(f"{'Index':<12} {'Model':<8} {'WM NLL':>8} {'RW NLL':>8} {'Delta':>8} {'Beat':>5} {'T':>6} {'1sig':>6} {'Verdict':<12}")
    lines.append("-" * 100)

    for key, report in sorted(reports.items()):
        if "error" in report:
            lines.append(f"{key:<20} ERROR: {report['error']}")
            continue

        agg = report.get("aggregate", {})
        config = report.get("config", {})
        index_name = key.split("/")[0]
        model_type = key.split("/")[1]

        wm_nll = agg.get("wm_nll", 0)
        rw_nll = agg.get("rw_nll", 0)
        delta = wm_nll - rw_nll
        beat = "YES" if agg.get("beat_rw") else "NO"
        T = config.get("avg_fold_temperature", 0)
        sig1 = agg.get("wm_1sig", 0)
        verdict = report.get("verdict", "?")

        lines.append(
            f"{index_name:<12} {model_type:<8} {wm_nll:>8.4f} {rw_nll:>8.4f} {delta:>+8.4f} {beat:>5} {T:>6.3f} {sig1:>5.1%} {verdict:<12}"
        )

    lines.append("-" * 100)
    lines.append("")

    # Best model per index
    lines.append("BEST MODEL PER INDEX:")
    seen_indices = set()
    for key, report in sorted(reports.items()):
        if "error" in report:
            continue
        index_name = key.split("/")[0]
        if index_name in seen_indices:
            continue
        seen_indices.add(index_name)

        # Find best model for this index
        best_key = None
        best_nll = float("inf")
        for k, r in reports.items():
            if k.startswith(index_name + "/") and "error" not in r:
                nll = r.get("aggregate", {}).get("wm_nll", 0)
                if nll < best_nll:
                    best_nll = nll
                    best_key = k

        if best_key:
            model_type = best_key.split("/")[1]
            report = reports[best_key]
            agg = report.get("aggregate", {})
            lines.append(f"  {index_name:<12} -> {model_type:<8} (WM NLL={agg.get('wm_nll', 0):.4f}, beats RW: {agg.get('beat_rw', False)})")

    lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Train and compare world models for all indices")
    parser.add_argument("--indices", nargs="+", default=list(INDEX_CONFIGS.keys()),
                        help="Indices to train (default: all)")
    parser.add_argument("--model", nargs="+", default=["tiny", "wide"],
                        choices=["tiny", "wide"],
                        help="Model architectures to train")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--eval-only", action="store_true", help="Skip training, just evaluate")
    parser.add_argument("--output", default="checkpoints/model_comparison.json",
                        help="Output path for comparison JSON")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    reports = {}

    # Train or evaluate each index × model
    for index_name in args.indices:
        if index_name not in INDEX_CONFIGS:
            logger.warning("unknown_index=%s skipping", index_name)
            continue
        config = INDEX_CONFIGS[index_name]

        for model_type in args.model:
            key = f"{index_name}/{model_type}"

            if not args.eval_only:
                train_index(index_name, config, model_type, epochs=args.epochs)

            report = evaluate_index(index_name, config, model_type)
            reports[key] = report

    # Build comparison
    comparison = build_comparison(reports)
    print(comparison)

    # Save
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(reports, f, indent=2, default=str)
    logger.info("comparison saved to %s", output_path)


if __name__ == "__main__":
    main()
