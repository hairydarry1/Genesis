"""
eval/test_predictability.py — Test whether R_75 is predictable at 1-min resolution.

Runs simple models (linear, logistic) against random walk to establish
the theoretical ceiling for any model on this data.

If even simple models can't beat random walk, the market is efficient
at this timescale and no amount of model complexity will help.
"""

from __future__ import annotations

import sys
import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import TimeSeriesSplit


def load_data(path: str = "data/dataset_R75_h10.npz") -> dict:
    """Load the precomputed dataset."""
    data = np.load(path, allow_pickle=True)
    return {
        "states": data["states"],
        "targets": data["targets"],
        "timestamps": data["timestamps"],
        "feature_names": list(data["feature_names"]),
        "feat_mean": data["feat_mean"],
        "feat_std": data["feat_std"],
    }


def compute_information_coefficients(states: np.ndarray, targets: np.ndarray, feature_names: list[str]) -> pd.DataFrame:
    """
    Compute Spearman rank correlation between each feature and future returns.
    IC > 0.03 is considered meaningful in finance.
    """
    from scipy.stats import spearmanr

    results = []
    for i, name in enumerate(feature_names):
        feature = states[:, i]
        # Skip constant features
        if np.std(feature) < 1e-10:
            continue

        # IC for each horizon step
        ics = []
        for h in range(targets.shape[1]):
            valid = np.isfinite(feature) & np.isfinite(targets[:, h])
            if valid.sum() > 100:
                ic, _ = spearmanr(feature[valid], targets[valid, h])
                ics.append(ic if np.isfinite(ic) else 0.0)
            else:
                ics.append(0.0)

        results.append({
            "feature": name,
            "IC_step1": ics[0],
            "IC_step5": ics[4] if len(ics) > 4 else 0.0,
            "IC_step10": ics[9] if len(ics) > 9 else 0.0,
            "IC_abs_mean": np.mean(np.abs(ics)),
        })

    df = pd.DataFrame(results).sort_values("IC_abs_mean", ascending=False)
    return df


def test_linear_regression(states: np.ndarray, targets: np.ndarray) -> dict:
    """
    Test if linear regression on raw features can beat random walk.
    This is the simplest possible model — if it fails, nothing will work.
    """
    n = len(states)
    horizon = targets.shape[1]

    # Time series split (5 folds)
    tscv = TimeSeriesSplit(n_splits=5)

    wm_nlls = []
    rw_nlls = []

    for train_idx, test_idx in tscv.split(states):
        X_train, X_test = states[train_idx], states[test_idx]
        y_train, y_test = targets[train_idx], targets[test_idx]

        # Standardize
        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        # Fit linear regression for each horizon step
        pred_means = np.zeros((len(X_test), horizon))
        pred_vars = np.zeros((len(X_test), horizon))

        for h in range(horizon):
            lr = LinearRegression()
            lr.fit(X_train_s, y_train[:, h])
            pred_means[:, h] = lr.predict(X_test_s)
            # Estimate variance from training residuals
            train_pred = lr.predict(X_train_s)
            residuals = y_train[:, h] - train_pred
            pred_vars[:, h] = np.var(residuals)

        # NLL
        eps = 1e-12
        var = np.maximum(pred_vars, eps)
        nll = 0.5 * (np.log(2 * np.pi * var) + (y_test - pred_means) ** 2 / var)
        wm_nlls.append(float(np.mean(nll)))

        # Random walk baseline
        train_std = np.std(y_train[:, 0])
        steps = np.arange(1, horizon + 1, dtype=np.float64)
        rw_var = np.tile(train_std**2 * steps, (len(X_test), 1))
        rw_nll = 0.5 * (np.log(2 * np.pi * rw_var) + (y_test - 0) ** 2 / rw_var)
        rw_nlls.append(float(np.mean(rw_nll)))

    return {
        "linear_regression": {
            "mean_nll": float(np.mean(wm_nlls)),
            "std_nll": float(np.std(wm_nlls)),
        },
        "random_walk": {
            "mean_nll": float(np.mean(rw_nlls)),
            "std_nll": float(np.std(rw_nlls)),
        },
        "gap": float(np.mean(rw_nlls) - np.mean(wm_nlls)),
    }


def test_direction_prediction(states: np.ndarray, targets: np.ndarray) -> dict:
    """
    Test if logistic regression can predict direction (up/down) better than 50%.
    """
    from sklearn.metrics import accuracy_score

    tscv = TimeSeriesSplit(n_splits=5)
    accs_step1 = []
    accs_step5 = []

    for train_idx, test_idx in tscv.split(states):
        X_train, X_test = states[train_idx], states[test_idx]
        y_train, y_test = targets[train_idx], targets[test_idx]

        scaler = StandardScaler()
        X_train_s = scaler.fit_transform(X_train)
        X_test_s = scaler.transform(X_test)

        for h, accs in [(0, accs_step1), (4, accs_step5)]:
            # Binary: up (1) or down (0)
            y_train_dir = (y_train[:, h] > 0).astype(int)
            y_test_dir = (y_test[:, h] > 0).astype(int)

            # Filter out ties
            valid = y_test[:, h] != 0
            if valid.sum() < 50:
                continue

            clf = LogisticRegression(max_iter=1000, C=0.1)
            clf.fit(X_train_s, y_train_dir)
            y_pred = clf.predict(X_test_s)
            acc = accuracy_score(y_test_dir[valid], y_pred[valid])
            accs.append(acc)

    return {
        "direction_accuracy_step1": float(np.mean(accs_step1)) if accs_step1 else 0.5,
        "direction_accuracy_step5": float(np.mean(accs_step5)) if accs_step5 else 0.5,
        "n_folds": max(len(accs_step1), len(accs_step5)),
    }


def test_random_split_baseline(targets: np.ndarray) -> dict:
    """Establish the random walk baseline NLL."""
    horizon = targets.shape[1]
    n = len(targets)

    train_std = np.std(targets[:n//2, 0])
    steps = np.arange(1, horizon + 1, dtype=np.float64)
    rw_var = np.tile(train_std**2 * steps, (n, 1))
    rw_nll = 0.5 * (np.log(2 * np.pi * rw_var) + targets**2 / rw_var)

    return {
        "random_walk_nll": float(np.mean(rw_nll)),
        "train_std": float(train_std),
    }


def main():
    print("=" * 70)
    print("R_75 PREDICTABILITY TEST")
    print("=" * 70)

    # Load data
    print("\nLoading data...")
    data = load_data()
    states = data["states"]
    targets = data["targets"]
    feature_names = data["feature_names"]
    n, p = states.shape
    horizon = targets.shape[1]

    print(f"  Samples: {n:,}")
    print(f"  Features: {p}")
    print(f"  Horizon: {horizon} steps")
    print(f"  Date range: {data['timestamps'][0]} to {data['timestamps'][-1]}")

    # 1. Information Coefficients
    print("\n" + "-" * 70)
    print("1. INFORMATION COEFFICIENTS (feature -> future return correlation)")
    print("-" * 70)
    ic_df = compute_information_coefficients(states, targets, feature_names)
    print(f"\nTop 15 features by |IC|:")
    print(f"{'Feature':<40s} {'IC_step1':>10s} {'IC_step5':>10s} {'IC_step10':>10s} {'|IC|_mean':>10s}")
    print("-" * 80)
    for _, row in ic_df.head(15).iterrows():
        print(f"{row['feature']:<40s} {row['IC_step1']:+10.4f} {row['IC_step5']:+10.4f} {row['IC_step10']:+10.4f} {row['IC_abs_mean']:10.4f}")

    # IC summary
    print(f"\nIC Summary:")
    print(f"  Max |IC|: {ic_df['IC_abs_mean'].max():.4f}")
    print(f"  Mean |IC|: {ic_df['IC_abs_mean'].mean():.4f}")
    print(f"  Features with |IC| > 0.03: {(ic_df['IC_abs_mean'] > 0.03).sum()}/{len(ic_df)}")
    print(f"  Features with |IC| > 0.05: {(ic_df['IC_abs_mean'] > 0.05).sum()}/{len(ic_df)}")

    # 2. Linear Regression
    print("\n" + "-" * 70)
    print("2. LINEAR REGRESSION (can a linear model beat random walk?)")
    print("-" * 70)
    linear_results = test_linear_regression(states, targets)
    print(f"\n  Linear Regression NLL: {linear_results['linear_regression']['mean_nll']:.4f} (+/- {linear_results['linear_regression']['std_nll']:.4f})")
    print(f"  Random Walk NLL:       {linear_results['random_walk']['mean_nll']:.4f} (+/- {linear_results['random_walk']['std_nll']:.4f})")
    print(f"  Gap (positive = LR beats RW): {linear_results['gap']:+.4f}")
    if linear_results['gap'] > 0:
        print(f"  Verdict: LINEAR MODEL BEATS RANDOM WALK")
    else:
        print(f"  Verdict: LINEAR MODEL DOES NOT BEAT RANDOM WALK")

    # 3. Direction Prediction
    print("\n" + "-" * 70)
    print("3. DIRECTION PREDICTION (logistic regression)")
    print("-" * 70)
    dir_results = test_direction_prediction(states, targets)
    print(f"\n  Direction accuracy step 1: {dir_results['direction_accuracy_step1']:.1%}")
    print(f"  Direction accuracy step 5: {dir_results['direction_accuracy_step5']:.1%}")
    print(f"  Random baseline: 50.0%")
    if dir_results['direction_accuracy_step1'] > 0.52:
        print(f"  Verdict: DIRECTION IS PREDICTABLE (above 52%)")
    else:
        print(f"  Verdict: DIRECTION IS NOT PREDICTABLE (near 50%)")

    # 4. Summary
    print("\n" + "=" * 70)
    print("OVERALL VERDICT")
    print("=" * 70)

    max_ic = ic_df['IC_abs_mean'].max()
    lr_beats_rw = linear_results['gap'] > 0
    dir_predictable = dir_results['direction_accuracy_step1'] > 0.52

    if max_ic < 0.03 and not lr_beats_rw and not dir_predictable:
        print("\n  R_75 IS NOT PREDICTABLE at 1-min resolution.")
        print("  No amount of model complexity will help.")
        print("  The market is efficient at this timescale.")
    elif max_ic > 0.05 or lr_beats_rw or dir_predictable:
        print("\n  R_75 SHOWS SOME PREDICTABILITY at 1-min resolution.")
        print("  There may be signal in the features.")
    else:
        print("\n  R_75 MARGINALLY PREDICTABLE at 1-min resolution.")
        print("  Weak signal exists but may not be exploitable after costs.")


if __name__ == "__main__":
    main()
