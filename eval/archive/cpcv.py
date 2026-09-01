"""
eval/cpcv.py — Combinatorial Purged Cross-Validation (CPCV) for the tiny world model.

Plain walk-forward validation tests against a single sequence of market
conditions per split, which can give false confidence from one lucky window.
CPCV instead enumerates all C(N, k) combinations of k test groups from N
contiguous time blocks, training on the remainder with:

  - Purge: training samples whose forward target window [i+1, i+H] overlaps
    the test block are removed (target leakage).
  - Embargo: samples within `embargo` bars after each test block are removed
    (serial correlation in features such as rolling volatility windows).

Usage:
    python -m eval.cpcv --dataset data/dataset_R75_h10.npz \
        --world-model checkpoints_tiny/world_model_best.pt
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import DataLoader, TensorDataset

from model.train_tiny import TinyWorldModel, gaussian_nll_loss, load_tiny_dataset


# ---------------------------------------------------------------------------
# Split generation
# ---------------------------------------------------------------------------

@dataclass
class CPCVSplit:
    combo: tuple[int, ...]
    test_idx: np.ndarray
    train_idx: np.ndarray


def cpcv_splits(
    n_samples: int,
    n_groups: int = 6,
    k: int = 2,
    horizon: int = 10,
    embargo: int = 10,
) -> list[CPCVSplit]:
    """
    Generate all C(N, k) combinatorial splits with purge + embargo.

    Group g covers contiguous indices [g*S, min((g+1)*S, n)).
    A training sample i is dropped when:
      - its forward window [i+1, i+horizon] overlaps any test index (purge), or
      - it falls within `embargo` bars after a test block ends (embargo).
    """
    if n_groups < 2 or k < 1 or k >= n_groups:
        raise ValueError(f"invalid n_groups={n_groups}, k={k}")
    S = n_samples // n_groups
    if S == 0:
        raise ValueError(f"n_samples={n_samples} too small for {n_groups} groups")

    group_bounds = [
        (g * S, min((g + 1) * S, n_samples)) for g in range(n_groups)
    ]

    def _contiguous_blocks(mask: np.ndarray) -> list[tuple[int, int]]:
        """Maximal runs of True as [start, end) intervals."""
        d = np.diff(mask.astype(np.int8))
        starts = list(np.where(d == 1)[0] + 1)
        ends = list(np.where(d == -1)[0] + 1)
        if mask[0]:
            starts = [0] + starts
        if mask[-1]:
            ends = ends + [len(mask)]
        return [(s, e) for s, e in zip(starts, ends)]

    splits = []
    for combo in combinations(range(n_groups), k):
        test_mask = np.zeros(n_samples, dtype=bool)
        for g in combo:
            lo, hi = group_bounds[g]
            test_mask[lo:hi] = True

        # Purge/embargo applied at EACH test-block boundary independently,
        # so interior groups remain usable for training.
        bad = test_mask.copy()
        for a, b in _contiguous_blocks(test_mask):
            # Purge: training samples whose forward window [i+1, i+horizon]
            # crosses into this block start.
            purge_lo = max(a - horizon, 0)
            bad[purge_lo:a] = True
            # Embargo: serial-correlation buffer after the block ends.
            embargo_hi = min(b + embargo, n_samples)
            bad[b:embargo_hi] = True

        train_idx = np.where(~bad)[0]
        test_idx = np.where(test_mask)[0]
        splits.append(CPCVSplit(combo=combo, test_idx=test_idx, train_idx=train_idx))

    return splits


# ---------------------------------------------------------------------------
# Per-combination training + evaluation
# ---------------------------------------------------------------------------

def _rw_baseline_nll(train_targets: np.ndarray, test_targets: np.ndarray) -> float:
    """Random-walk baseline: zero mean, per-horizon-step variance from train."""
    eps = 1e-8
    var = np.var(train_targets, axis=0) + eps
    nll = 0.5 * (np.log(2 * np.pi * var)[None, :] + (test_targets - 0.0) ** 2 / var[None, :])
    return float(np.mean(nll))


def _train_model(
    train_states: np.ndarray,
    train_targets: np.ndarray,
    n_features: int,
    horizon: int,
    epochs: int,
    batch_size: int,
    lr: float,
    device: torch.device,
    hidden_dim: int = 16,
    dropout: float = 0.1,
) -> TinyWorldModel:
    model = TinyWorldModel(
        n_features=n_features, horizon=horizon,
        hidden=hidden_dim, dropout=dropout,
    ).to(device)

    # Initialize variance head from empirical target statistics.
    # Without this, log_var starts at 0 (variance=1.0) which is
    # orders of magnitude off from the actual variance (~1e-6 for
    # synthetic indices), causing the model to massively overestimate
    # uncertainty (T ≈ 0.02 instead of ~0.5).
    emp_var = torch.tensor(train_targets, dtype=torch.float32).var(dim=0).clamp(min=1e-12)
    emp_log_var = torch.log(emp_var)
    with torch.no_grad():
        model.log_var_head.bias.copy_(emp_log_var)
        nn.init.normal_(model.log_var_head.weight, std=0.01)

    loader = DataLoader(
        TensorDataset(
            torch.tensor(train_states, dtype=torch.float32),
            torch.tensor(train_targets, dtype=torch.float32),
        ),
        batch_size=batch_size, shuffle=True, num_workers=0,
    )
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=epochs, eta_min=1e-5)

    for _ in range(epochs):
        model.train()
        for states_b, targets_b in loader:
            states_b, targets_b = states_b.to(device), targets_b.to(device)
            optimizer.zero_grad()
            mean, log_var = model(states_b)
            loss = gaussian_nll_loss(mean, log_var, targets_b, var_reg_weight=0.05)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
            optimizer.step()
        scheduler.step()
    return model


def run_cpcv(
    dataset_path: str,
    output_path: str | None = None,
    n_groups: int = 6,
    k: int = 2,
    horizon: int | None = None,
    embargo: int = 10,
    epochs: int = 30,
    batch_size: int = 4096,
    lr: float = 1e-3,
    device_str: str | None = None,
    temp_fit: str = "train",
    temp_holdout_frac: float = 0.2,
) -> dict:
    """Run full CPCV. Normalization stats are computed per-combination on the
    purged training subset only (no leakage into test).

    temp_fit="train": temperature T fitted on training-set predictions
        (original behavior; slightly optimistic because the model has seen
        those samples).
    temp_fit="holdout": the last `temp_holdout_frac` of each combination's
        purged training data is withheld from model fitting and used ONLY for
        temperature fitting — an unbiased T at the cost of some training data.
    """
    if temp_fit not in ("train", "holdout"):
        raise ValueError(f"invalid temp_fit={temp_fit!r}")
    device = torch.device(device_str or ("cuda" if torch.cuda.is_available() else "cpu"))

    ds = load_tiny_dataset(dataset_path)
    # Re-derive RAW (un-normalized) tiny states so we can re-normalize per split
    import numpy as _np
    raw = _np.load(dataset_path, allow_pickle=True)
    name_to_idx = {n: i for i, n in enumerate(list(raw["feature_names"]))}
    tiny_cols = [name_to_idx[f] for f in ds["feature_names"]]
    raw_states = raw["states"][:, tiny_cols].astype(_np.float64)

    timestamps = raw["timestamps"]
    order = _np.argsort(timestamps)
    raw_states = raw_states[order]
    targets = ds["targets"][order]
    H = horizon or targets.shape[1]

    splits = cpcv_splits(len(targets), n_groups=n_groups, k=k, horizon=H, embargo=embargo)
    print(f"CPCV: N={n_groups} groups, k={k} -> {len(splits)} combinations "
          f"(purge={H}, embargo={embargo}, temp_fit={temp_fit})")

    results = []
    for si, sp in enumerate(splits):
        t0 = time.time()

        # Chronological fit/calib split within the purged training set.
        # calib is only used for temperature fitting (holdout mode) — never
        # trained on — so T reflects out-of-sample prediction quality.
        if temp_fit == "holdout":
            cut = int(len(sp.train_idx) * (1 - temp_holdout_frac))
            fit_idx, calib_idx = sp.train_idx[:cut], sp.train_idx[cut:]
        else:
            fit_idx = calib_idx = sp.train_idx

        tr_states = raw_states[fit_idx]
        tr_targets = targets[fit_idx]
        te_states = raw_states[sp.test_idx]
        te_targets = targets[sp.test_idx]

        mean = tr_states.mean(axis=0)
        std = tr_states.std(axis=0) + 1e-8
        tr_states_n = ((tr_states - mean) / std).astype(np.float32)
        te_states_n = ((te_states - mean) / std).astype(np.float32)
        cal_states_n = ((raw_states[calib_idx] - mean) / std).astype(np.float32)
        cal_targets = targets[calib_idx]

        model = _train_model(tr_states_n, tr_targets, len(ds["feature_names"]),
                             H, epochs, batch_size, lr, device)

        model.eval()
        with torch.no_grad():
            preds_mean, preds_var = [], []
            for b in range(0, len(te_states_n), 8192):
                xb = torch.tensor(te_states_n[b:b + 8192], dtype=torch.float32, device=device)
                m, lv = model(xb)
                preds_mean.append(m.cpu().numpy())
                preds_var.append(torch.exp(lv).cpu().numpy())
        pred_mean = _np.concatenate(preds_mean)
        pred_var = _np.concatenate(preds_var)

        # Temperature fitted on calibration predictions (train or held-out
        # slice of train — never test; no leakage)
        with torch.no_grad():
            tm, tv = [], []
            for b in range(0, len(cal_states_n), 8192):
                xb = torch.tensor(cal_states_n[b:b + 8192], dtype=torch.float32, device=device)
                m, lv = model(xb)
                tm.append(m.cpu().numpy())
                tv.append(torch.exp(lv).cpu().numpy())
        T = max(float(_np.mean((_np.concatenate(tm) - cal_targets) ** 2) /
                      _np.mean(_np.concatenate(tv))), 0.01)

        cal_var = pred_var * T
        eps = 1e-8
        wm_nll = float(_np.mean(0.5 * (_np.log(2 * _np.pi * cal_var + eps) +
                                       (te_targets - pred_mean) ** 2 / (cal_var + eps))))
        rw_nll = _rw_baseline_nll(tr_targets, te_targets)

        rec = {
            "combo": list(sp.combo),
            "wm_nll": wm_nll,
            "rw_nll": rw_nll,
            "beat_rw": wm_nll < rw_nll,
            "temperature": T,
            "temp_fit": temp_fit,
            "n_train": len(fit_idx),
            "n_calib": len(calib_idx) if temp_fit == "holdout" else 0,
            "n_test": len(sp.test_idx),
            "time_s": round(time.time() - t0, 1),
        }
        results.append(rec)
        print(f"  [{si + 1:2d}/{len(splits)}] combo={sp.combo} "
              f"WM={wm_nll:.4f} RW={rw_nll:.4f} {'BEAT' if rec['beat_rw'] else 'LOSE'} "
              f"T={T:.3f} ({rec['time_s']}s)")

    wm_all = [r["wm_nll"] for r in results]
    rw_all = [r["rw_nll"] for r in results]
    n_beat = sum(r["beat_rw"] for r in results)
    summary = {
        "config": {
            "dataset": dataset_path, "n_groups": n_groups, "k": k,
            "embargo": embargo, "epochs": epochs, "n_combinations": len(splits),
            "temp_fit": temp_fit, "temp_holdout_frac": temp_holdout_frac,
        },
        "per_combination": results,
        "aggregate": {
            "wm_nll_mean": float(_np.mean(wm_all)),
            "wm_nll_std": float(_np.std(wm_all)),
            "rw_nll_mean": float(_np.mean(rw_all)),
            "rw_nll_std": float(_np.std(rw_all)),
            "combos_beating_rw": n_beat,
            "total_combinations": len(splits),
            "beat_fraction": n_beat / len(splits),
            "worst_combo_wm_nll": float(_np.max(wm_all)),
            "worst_combo_vs_rw": float(_np.max(_np.array(wm_all) - _np.array(rw_all))),
        },
    }

    # Gate: beat RW in >=70% of combinations and never regress badly
    agg = summary["aggregate"]
    passed = agg["beat_fraction"] >= 0.7 and agg["worst_combo_vs_rw"] < 0.5
    summary["verdict"] = "PASS" if passed else "FAIL"

    print("\n" + "=" * 60)
    print("CPCV SUMMARY")
    print("=" * 60)
    print(f"WM NLL: {agg['wm_nll_mean']:.4f} +/- {agg['wm_nll_std']:.4f}")
    print(f"RW NLL: {agg['rw_nll_mean']:.4f} +/- {agg['rw_nll_std']:.4f}")
    print(f"Beats RW: {agg['combos_beating_rw']}/{agg['total_combinations']} "
          f"({agg['beat_fraction']:.0%})")
    print(f"Worst combo vs RW: {agg['worst_combo_vs_rw']:+.4f}")
    print(f"Verdict: {summary['verdict']}")

    if output_path:
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Report saved to: {output_path}")

    return summary


def main():
    parser = argparse.ArgumentParser(description="Combinatorial Purged CV for tiny world model")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--n-groups", type=int, default=6)
    parser.add_argument("--k", type=int, default=2)
    parser.add_argument("--horizon", type=int, default=None)
    parser.add_argument("--embargo", type=int, default=10)
    parser.add_argument("--temp-fit", choices=("train", "holdout"), default="train",
                        help="fit temperature T on all train predictions (train) or "
                             "on a held-out slice of train never used for fitting (holdout)")
    parser.add_argument("--temp-holdout-frac", type=float, default=0.2)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--batch-size", type=int, default=4096)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    if args.output:
        out = args.output
    else:
        name = Path(args.dataset).stem.replace("dataset_", "").replace("_h10", "")
        out = f"checkpoints_cpcv/cpcv_{name}.json"
    run_cpcv(
        dataset_path=args.dataset,
        output_path=out,
        n_groups=args.n_groups,
        k=args.k,
        horizon=args.horizon,
        embargo=args.embargo,
        epochs=args.epochs,
        batch_size=args.batch_size,
        device_str=args.device,
        temp_fit=args.temp_fit,
        temp_holdout_frac=args.temp_holdout_frac,
    )


if __name__ == "__main__":
    main()
