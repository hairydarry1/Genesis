"""
backtest/lookahead_audit.py — Lookahead bias audit (mandatory per brief).

Every feature — especially structural detectors (swing points, FVG, order
blocks, ...) — must only ever use data available at or before the decision
timestamp. This module verifies that empirically:

  1. Compute feature vectors normally over the FULL history ("what training
     saw").
  2. Recompute them with ALL data beyond each decision bar blacked out
     ("what a live trader could have seen").
  3. Compare. Any divergence means the feature pipeline leaks future
     information — this must be fixed, not worked around, since it silently
     inflates every result upstream of it.

Convention note: the training pipeline makes decisions at BAR CLOSE (state at
bar i may use everything through bar i's close, targets start at bar i+1).
The audit therefore truncates to bars [0..i] inclusive and probes whether the
full-history computation agrees exactly with the truncated recomputation.

Usage:
    python -m backtest.lookahead_audit --candles data/candles_R75_1min.parquet
    python -m backtest.lookahead_audit --candles ... --n-probes 30 --output lookahead_report.json
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from features.state import StateBuilder


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_candles(path: str | Path) -> pd.DataFrame:
    """Load 1-min candles the same way model/precompute_dataset.py does."""
    p = Path(path)
    if p.suffix == ".parquet":
        df = pd.read_parquet(p, engine="pyarrow")
    elif p.suffix == ".csv":
        df = pd.read_csv(p)
    else:
        raise ValueError(f"unsupported candle format: {p.suffix}")
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_localize(None)
    df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Comparison core
# ---------------------------------------------------------------------------

def compare_states(
    full: dict[str, float],
    trunc: dict[str, float],
    tolerance: float,
) -> list[dict]:
    """Compare two feature dicts; return list of mismatches."""
    mismatches: list[dict] = []

    missing_full = sorted(set(full) - set(trunc))
    missing_trunc = sorted(set(trunc) - set(full))
    for k in missing_full:
        mismatches.append({"feature": k, "kind": "missing_in_truncated",
                           "full": full[k], "truncated": None, "abs_diff": None})
    for k in missing_trunc:
        mismatches.append({"feature": k, "kind": "missing_in_full",
                           "full": None, "truncated": trunc[k], "abs_diff": None})

    for k in sorted(set(full) & set(trunc)):
        a, b = float(full[k]), float(trunc[k])
        if not (np.isfinite(a) and np.isfinite(b)):
            if a != b:  # one inf/nan, other finite (NaNs were zeroed upstream)
                mismatches.append({"feature": k, "kind": "nonfinite",
                                   "full": a, "truncated": b, "abs_diff": None})
            continue
        # Scale-aware tolerance: absolute for small values, relative otherwise
        diff = abs(a - b)
        if diff > tolerance * max(1.0, abs(a), abs(b)):
            mismatches.append({"feature": k, "kind": "value_mismatch",
                               "full": a, "truncated": b, "abs_diff": diff})
    return mismatches


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

@dataclass
class AuditReport:
    config: dict = field(default_factory=dict)
    probes: list[dict] = field(default_factory=list)
    feature_offenders: dict[str, dict] = field(default_factory=dict)  # name -> count/max_diff/example
    verdict: str = "UNKNOWN"

    def summarize(self) -> dict:
        n_leaky = sum(1 for p in self.probes if not p["ok"])
        return {
            "config": self.config,
            "verdict": self.verdict,
            "n_probes": len(self.probes),
            "n_clean_probes": len(self.probes) - n_leaky,
            "n_leaky_probes": n_leaky,
            "features_flagged": sorted(self.feature_offenders),
            "feature_detail": self.feature_offenders,
            "probes": self.probes,
        }


def audit_candles(
    candles: pd.DataFrame,
    builder_kwargs: dict | None = None,
    n_probes: int = 20,
    warmup_bars: int = 1500,
    tolerance: float = 1e-9,
    seed: int = 0,
    verbose: bool = True,
) -> AuditReport:
    """
    Run the lookahead audit on a candle series.

    Probes `n_probes` evenly-spaced decision bars (after `warmup_bars`).
    For each probe bar i:
      - full state  : StateBuilder over ALL candles, state at bar i's timestamp
      - trunc state : StateBuilder over candles[:i+1] ONLY, same timestamp
    Any difference is a lookahead leak.
    """
    builder_kwargs = dict(builder_kwargs or {})
    n = len(candles)
    if n < warmup_bars + 50:
        raise ValueError(
            f"need >= {warmup_bars + 50} bars, got {n} — lower --warmup or provide more data"
        )

    rng = np.random.default_rng(seed)
    probe_indices = np.linspace(warmup_bars, n - 1, n_probes).astype(int)
    probe_indices = sorted(set(probe_indices.tolist()))

    report = AuditReport(config={
        "n_bars": n,
        "start": str(candles["timestamp"].iloc[0]),
        "end": str(candles["timestamp"].iloc[-1]),
        "n_probes": len(probe_indices),
        "warmup_bars": warmup_bars,
        "tolerance": tolerance,
        "builder_kwargs": {k: v for k, v in builder_kwargs.items()},
        "seed": seed,
    })

    def _log(msg: str) -> None:
        if verbose:
            print(msg, flush=True)

    _log(f"Building FULL-history StateBuilder ({n} bars)...")
    t0 = time.time()
    full_builder = StateBuilder(candles, **builder_kwargs)
    _log(f"  done in {time.time() - t0:.1f}s")

    for pi, bar_i in enumerate(probe_indices):
        ts = pd.Timestamp(candles["timestamp"].iloc[bar_i])
        t0 = time.time()

        state_full = full_builder.build_state(ts)

        # Black out everything after the decision bar
        candles_trunc = candles.iloc[:bar_i + 1].reset_index(drop=True)
        trunc_builder = StateBuilder(candles_trunc, **builder_kwargs)
        state_trunc = trunc_builder.build_state(ts)

        mismatches = compare_states(state_full, state_trunc, tolerance)
        max_diff = max([m["abs_diff"] or 0.0 for m in mismatches], default=0.0)
        probe_rec = {
            "probe": pi,
            "bar_idx": int(bar_i),
            "timestamp": str(ts),
            "n_features": len(state_full),
            "n_mismatches": len(mismatches),
            "max_abs_diff": max_diff,
            "ok": len(mismatches) == 0,
            "mismatches": mismatches,
            "elapsed_s": round(time.time() - t0, 1),
        }
        report.probes.append(probe_rec)

        status = "CLEAN" if probe_rec["ok"] else f"LEAK ({len(mismatches)} features)"
        _log(f"  [{pi + 1}/{len(probe_indices)}] bar {bar_i} @ {ts} -> {status} "
             f"({probe_rec['elapsed_s']}s)")

        for m in mismatches:
            rec = report.feature_offenders.setdefault(
                m["feature"], {"count": 0, "max_abs_diff": 0.0, "example": m})
            rec["count"] += 1
            if m["abs_diff"] and m["abs_diff"] > rec["max_abs_diff"]:
                rec["max_abs_diff"] = m["abs_diff"]

        del trunc_builder, candles_trunc

    n_leaky = sum(1 for p in report.probes if not p["ok"])
    report.verdict = "PASS" if n_leaky == 0 else "FAIL"
    return report


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Lookahead bias audit")
    parser.add_argument("--candles", required=True, help="parquet/csv of 1-min candles")
    parser.add_argument("--n-probes", type=int, default=20)
    parser.add_argument("--warmup", type=int, default=1500,
                        help="bars required before the first probe (feature warm-up)")
    parser.add_argument("--tolerance", type=float, default=1e-9)
    parser.add_argument("--atr-period", type=int, default=None,
                        help="override StateBuilder atr_period")
    parser.add_argument("--swing-lookback", type=int, default=None,
                        help="override StateBuilder swing_lookback")
    parser.add_argument("--structures", default=None,
                        help="precomputed structures parquet (as used in training)")
    parser.add_argument("--output", default=None, help="write JSON report here")
    args = parser.parse_args()

    candles = load_candles(args.candles)
    print(f"Candles: {len(candles)} bars, {candles.timestamp.min()} to {candles.timestamp.max()}")

    builder_kwargs: dict = {}
    if args.atr_period is not None:
        builder_kwargs["atr_period"] = args.atr_period
    if args.swing_lookback is not None:
        builder_kwargs["swing_lookback"] = args.swing_lookback
    if args.structures:
        builder_kwargs["structures_path"] = args.structures

    report = audit_candles(
        candles,
        builder_kwargs=builder_kwargs,
        n_probes=args.n_probes,
        warmup_bars=args.warmup,
        tolerance=args.tolerance,
    )

    summary = report.summarize()
    print("\n" + "=" * 60)
    print("LOOKAHEAD AUDIT SUMMARY")
    print("=" * 60)
    print(f"Probes clean: {summary['n_clean_probes']}/{summary['n_probes']}")
    if summary["features_flagged"]:
        print("Features flagged:")
        for name in summary["features_flagged"]:
            d = summary["feature_detail"][name]
            print(f"  {name}: {d['count']}x max_diff={d['max_abs_diff']:.6g}")
    print(f"Verdict: {report.verdict}")

    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        print(f"Report saved to: {args.output}")

    raise SystemExit(0 if report.verdict == "PASS" else 1)


if __name__ == "__main__":
    main()
