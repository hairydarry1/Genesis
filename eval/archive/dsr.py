"""
eval/dsr.py — Deflated Sharpe Ratio (Bailey & López de Prado, 2014).

The DSR corrects a measured Sharpe ratio for multiple-testing bias: if you
try enough strategy variants, some will look good by pure chance. It converts
an observed SR into the probability that the *true* SR exceeds zero after
accounting for:

  - n_trials: how many strategy variants were tested (this project's v2-v8
    sweeps, regularization variants, and per-index runs all count)
  - the variance of SRs across those trials
  - skewness and kurtosis of the returns (non-Normality inflates SR noise)

Usage:
    from eval.dsr import deflated_sharpe, sharpe_ratio
    dsr = deflated_sharpe(returns, n_trials=16, trial_sharpes=[...])

CLI:
    python -m eval.dsr --returns-file path/to/returns.npy --n-trials 16
    python -m eval.dsr --policy-report checkpoints_tiny/policy_validation.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

EULER_GAMMA = 0.5772156649015329


def sharpe_ratio(returns: np.ndarray) -> float:
    """Per-period Sharpe ratio (not annualized): mean / std."""
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 2:
        return 0.0
    std = np.std(r, ddof=1)
    if std < 1e-12:
        return 0.0
    return float(np.mean(r) / std)


def expected_max_sharpe(n_trials: int, sr_std: float) -> float:
    """
    Expected maximum Sharpe under the null (all true SR = 0), approximated via
    extreme value theory for N >= ~2:
        E[max] ≈ sr_std * ((1-γ)*Z[1-1/N] + γ*Z[1-1/(N*e)])
    where γ is the Euler-Mascheroni constant.
    """
    if n_trials < 2 or sr_std <= 0:
        return 0.0
    z1 = stats.norm.ppf(1.0 - 1.0 / n_trials)
    z2 = stats.norm.ppf(1.0 - 1.0 / (n_trials * math.e))
    return float(sr_std * ((1 - EULER_GAMMA) * z1 + EULER_GAMMA * z2))


def deflated_sharpe(
    returns: np.ndarray,
    n_trials: int,
    trial_sharpes: list[float] | None = None,
    default_sr_std_n: int | None = None,
) -> dict:
    """
    Compute the Deflated Sharpe Ratio.

    Parameters
    ----------
    returns : array-like
        Per-step strategy returns (e.g. reward per bar, or equity deltas).
    n_trials : int
        Number of independent strategy configurations tested.
    trial_sharpes : list[float], optional
        Sharpe ratios of the other trials; used to estimate the cross-trial
        variance of SR. If omitted, falls back to the theoretical single-series
        approximation sr_var ≈ 1/T scaled by default_sr_std_n trials at T.
    """
    r = np.asarray(returns, dtype=np.float64)
    T = len(r)
    if T < 3:
        raise ValueError(f"need >= 3 returns, got {T}")

    sr_hat = sharpe_ratio(r)
    skew = float(stats.skew(r))
    # Pearson kurtosis (normal == 3)
    kurt = float(stats.kurtosis(r, fisher=False))

    # Cross-trial SR variance
    if trial_sharpes and len(trial_sharpes) >= 2:
        all_srs = list(trial_sharpes) + [sr_hat]
        sr_var = float(np.var(all_srs, ddof=1))
    else:
        # Theoretical variance of SR estimator under iid normal returns
        sr_var = (1.0 / (T - 1)) * (
            1 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2
        )
        # Scale up as an approximation of spread across `default_sr_std_n` trials
        if default_sr_std_n:
            sr_var *= max(default_sr_std_n, 1)

    sr_std = math.sqrt(max(sr_var, 1e-12))
    sr0 = expected_max_sharpe(n_trials, sr_std)

    denom = math.sqrt(max(
        1.0 - skew * sr_hat + (kurt - 1) / 4 * sr_hat ** 2, 1e-12
    ))
    z = (sr_hat - sr0) * math.sqrt(T - 1) / denom
    dsr = float(stats.norm.cdf(z))

    return {
        "sr_hat": sr_hat,
        "sr0_expected_max_under_null": sr0,
        "sr_std_across_trials": sr_std,
        "n_returns": T,
        "skew": skew,
        "kurtosis": kurt,
        "n_trials": n_trials,
        "dsr": dsr,
        "significant": dsr >= 0.95,
    }


# ---------------------------------------------------------------------------
# Helpers to extract returns from project artifacts
# ---------------------------------------------------------------------------

def returns_from_policy_report(report_path: str) -> tuple[list[float], list[list[float]]]:
    """
    Extract aggregate and per-fold episode returns from a policy_validation.json.
    Returns (all_returns, folds_of_returns).
    """
    with open(report_path) as f:
        report = json.load(f)
    folds = []
    for fold in report.get("folds", []):
        er = fold.get("episode_returns") or fold.get("eval", {}).get("episode_returns")
        if er:
            folds.append(list(er))
    flat = [x for f in folds for x in f]
    return flat, folds


def main():
    parser = argparse.ArgumentParser(description="Deflated Sharpe Ratio")
    parser.add_argument("--returns-file", default=None,
                        help=".npy/.txt file with one return per line/bar")
    parser.add_argument("--policy-report", default=None,
                        help="policy_validation.json with episode_returns")
    parser.add_argument("--n-trials", type=int, default=16,
                        help="number of strategy variants tested across the project")
    args = parser.parse_args()

    if args.returns_file:
        p = Path(args.returns_file)
        if p.suffix == ".npy":
            rets = np.load(p)
        else:
            rets = np.array([float(line) for line in p.read_text().splitlines() if line.strip()])
        result = deflated_sharpe(rets, args.n_trials)
        print(json.dumps(result, indent=2))
        print("DSR significant (>=0.95):", result["significant"])
    elif args.policy_report:
        flat, folds = returns_from_policy_report(args.policy_report)
        if not flat:
            print("No episode_returns found in report — rerun policy_validation "
                  "(post Phase-1 version saves them).")
            return
        result = deflated_sharpe(np.array(flat), args.n_trials)
        print(json.dumps(result, indent=2))
    else:
        parser.error("provide --returns-file or --policy-report")


if __name__ == "__main__":
    main()
