"""
model/compare_models.py — Quick comparison of all trained models.

Reads walk_forward_report.json from each checkpoint directory and
prints a clean comparison table.

Usage:
    python -m model.compare_models
"""

from __future__ import annotations

import json
from pathlib import Path


# Checkpoint directories to check
CHECKPOINT_DIRS = {
    "R75/tiny": "checkpoints_tiny_clean",
    "R75/wide": "checkpoints_wide_r75",
    "VOL10/tiny": "checkpoints_vol10",
    "VOL10/wide": "checkpoints_wide_vol10",
    "BOOM/tiny": "checkpoints_boom_500_clean",
    "BOOM/wide": "checkpoints_wide_boom",
    "CRASH/tiny": "checkpoints_crash_500_clean",
    "CRASH/wide": "checkpoints_wide_crash",
}


def load_report(name: str, path: str) -> dict | None:
    report_path = Path(path) / "walk_forward_report.json"
    if not report_path.exists():
        return None
    try:
        with open(report_path) as f:
            return json.load(f)
    except Exception:
        return None


def main():
    print()
    print("=" * 105)
    print("GENESIS WORLD MODEL COMPARISON")
    print("=" * 105)
    print()
    print(f"{'Index/Model':<20} {'WM NLL':>9} {'RW NLL':>9} {'Delta':>9} {'Beat':>5} {'T':>7} {'1sig':>7} {'Verdict':<12}")
    print("-" * 105)

    results = {}
    for name, path in sorted(CHECKPOINT_DIRS.items()):
        report = load_report(name, path)
        if report is None:
            print(f"{name:<20} {'NOT TRAINED':>9}")
            continue

        agg = report.get("aggregate", {})
        config = report.get("config", {})

        wm = agg.get("wm_nll", 0)
        rw = agg.get("rw_nll", 0)
        delta = wm - rw
        beat = "YES" if agg.get("beat_rw") else "NO"
        T = config.get("avg_fold_temperature", 0)
        sig1 = agg.get("wm_1sig", 0)
        verdict = report.get("verdict", "?")
        params = config.get("param_count", 0)

        print(f"{name:<20} {wm:>9.4f} {rw:>9.4f} {delta:>+9.4f} {beat:>5} {T:>7.3f} {sig1:>6.1%} {verdict:<12}")
        results[name] = {"wm": wm, "rw": rw, "delta": delta, "verdict": verdict, "params": params}

    print("-" * 105)

    # Find best per index
    print()
    print("BEST MODEL PER INDEX:")
    seen = set()
    for name in sorted(results.keys()):
        index = name.split("/")[0]
        if index in seen:
            continue
        seen.add(index)
        # Find best
        candidates = [(n, r) for n, r in results.items() if n.startswith(index + "/")]
        if candidates:
            best_name, best = min(candidates, key=lambda x: x[1]["wm"])
            print(f"  {index:<12} -> {best_name.split('/')[1]:<6} (WM NLL={best['wm']:.4f}, beats RW: {'YES' if best['delta'] < 0 else 'NO'}, params={best['params']})")

    print()

    # Per-index verdicts
    print("VERDICTS:")
    for name, r in sorted(results.items()):
        status = "PASS" if r["delta"] < 0 else "FAIL"
        icon = "+" if r["delta"] < 0 else "-"
        print(f"  [{icon}] {name}: WM={r['wm']:.4f} vs RW={r['rw']:.4f} (delta={r['delta']:+.4f}) -> {r['verdict']}")

    print()


if __name__ == "__main__":
    main()
