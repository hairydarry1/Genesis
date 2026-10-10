"""
model/ensemble.py — Model ensemble for VOL10 predictions.

Combines tiny (516 params) + wide (2132 params) models:
  - Averages their predicted means (reduces variance)
  - Averages their predicted variances (better uncertainty)
  - Uses combined SNR for trade decisions

This is a simple model averaging approach that:
  1. Runs both models on the same input
  2. Averages the predictions
  3. Produces more stable signals than either model alone

Usage:
    from model.ensemble import VOL10Ensemble
    ens = VOL10Ensemble()
    mean, snr = ens.predict(features)
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import torch


class VOL10Ensemble:
    """Ensemble of tiny + wide models for VOL10."""

    def __init__(
        self,
        tiny_path: str = "checkpoints_vol10/world_model_best.pt",
        wide_path: str = "checkpoints_wide_vol10/world_model_best.pt",
        weights: tuple[float, float] = (0.5, 0.5),  # (tiny_weight, wide_weight)
    ):
        self.tiny_path = Path(tiny_path)
        self.wide_path = Path(wide_path)
        self.weights = weights
        self.tiny_model = None
        self.wide_model = None
        self.tiny_temperature = 1.0
        self.wide_temperature = 1.0
        self.feature_names: list[str] = []

    def load(self):
        """Load both models."""
        from model.train_tiny import build_model

        # Tiny model
        if self.tiny_path.exists():
            ckpt = torch.load(self.tiny_path, map_location="cpu", weights_only=False)
            self.feature_names = list(ckpt["feature_names"])
            model_type = ckpt.get("model_type", "tiny")
            hidden = ckpt.get("hidden_dim", 16)
            self.tiny_model = build_model(
                model_type=model_type, n_features=len(self.feature_names),
                horizon=10, hidden=hidden, dropout=0.0,
            )
            self.tiny_model.load_state_dict(ckpt["model_state_dict"])
            self.tiny_model.eval()
            self.tiny_temperature = ckpt.get("temperature", 1.0)
            tiny_params = sum(p.numel() for p in self.tiny_model.parameters())
            print(f"  Tiny loaded: {tiny_params} params, T={self.tiny_temperature:.3f}")

        # Wide model
        if self.wide_path.exists():
            ckpt = torch.load(self.wide_path, map_location="cpu", weights_only=False)
            if not self.feature_names:
                self.feature_names = list(ckpt["feature_names"])
            model_type = ckpt.get("model_type", "wide")
            hidden = ckpt.get("hidden_dim", 32)
            self.wide_model = build_model(
                model_type=model_type, n_features=len(self.feature_names),
                horizon=10, hidden=hidden, dropout=0.0,
            )
            self.wide_model.load_state_dict(ckpt["model_state_dict"])
            self.wide_model.eval()
            self.wide_temperature = ckpt.get("temperature", 1.0)
            wide_params = sum(p.numel() for p in self.wide_model.parameters())
            print(f"  Wide loaded: {wide_params} params, T={self.wide_temperature:.3f}")

        n_models = (1 if self.tiny_model else 0) + (1 if self.wide_model else 0)
        print(f"  Ensemble: {n_models} models loaded")

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        """
        Run ensemble prediction.
        Returns (total_mean, snr).
        """
        x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)

        all_means = []
        all_vars = []
        total_weight = 0

        # Tiny model prediction
        if self.tiny_model is not None:
            with torch.no_grad():
                mean, log_var = self.tiny_model(x)
                pred_mean = mean.numpy().flatten()
                pred_var = torch.exp(log_var).numpy().flatten() * self.tiny_temperature
                all_means.append(pred_mean * self.weights[0])
                all_vars.append(pred_var * self.weights[0])
                total_weight += self.weights[0]

        # Wide model prediction
        if self.wide_model is not None:
            with torch.no_grad():
                mean, log_var = self.wide_model(x)
                pred_mean = mean.numpy().flatten()
                pred_var = torch.exp(log_var).numpy().flatten() * self.wide_temperature
                all_means.append(pred_mean * self.weights[1])
                all_vars.append(pred_var * self.weights[1])
                total_weight += self.weights[1]

        if not all_means:
            return 0.0, 0.0

        # Weighted average
        combined_mean = sum(all_means) / total_weight
        combined_var = sum(all_vars) / total_weight

        total_mean = float(np.sum(combined_mean))
        total_std = float(np.sqrt(np.sum(combined_var)))
        snr = abs(total_mean) / (total_std + 1e-10)

        return total_mean, min(snr, 10.0)

    def predict_individual(self, features: np.ndarray) -> dict:
        """Get individual model predictions for comparison."""
        x = torch.tensor(features, dtype=torch.float32).unsqueeze(0)
        result = {}

        if self.tiny_model is not None:
            with torch.no_grad():
                mean, log_var = self.tiny_model(x)
                pred_mean = mean.numpy().flatten()
                pred_var = torch.exp(log_var).numpy().flatten() * self.tiny_temperature
                tm = float(np.sum(pred_mean))
                ts = float(np.sqrt(np.sum(pred_var)))
                result["tiny"] = {"mean": tm, "snr": abs(tm)/(ts+1e-10), "direction": "UP" if tm > 0 else "DOWN"}

        if self.wide_model is not None:
            with torch.no_grad():
                mean, log_var = self.wide_model(x)
                pred_mean = mean.numpy().flatten()
                pred_var = torch.exp(log_var).numpy().flatten() * self.wide_temperature
                tm = float(np.sum(pred_mean))
                ts = float(np.sqrt(np.sum(pred_var)))
                result["wide"] = {"mean": tm, "snr": abs(tm)/(ts+1e-10), "direction": "UP" if tm > 0 else "DOWN"}

        return result


def backtest_ensemble():
    """Backtest ensemble vs individual models on VOL10 out-of-sample."""
    from model.train_tiny import TINY_FEATURES, build_model

    # Load data
    data = np.load("data/dataset_volatility_10_h10.npz", allow_pickle=True)
    states, targets, feature_names = data["states"], data["targets"], list(data["feature_names"])
    name_to_idx = {n: i for i, n in enumerate(feature_names)}
    tiny_idx = [name_to_idx[f] for f in TINY_FEATURES if f in name_to_idx]
    tiny = states[:, tiny_idx]

    split = int(len(tiny) * 0.90)
    norm_mean = tiny[:split].mean(0)
    norm_std = tiny[:split].std(0) + 1e-8
    test = (tiny[split:] - norm_mean) / norm_std
    test_tgts = targets[split:]

    # Load individual models
    def load_model(path, model_type):
        ckpt = torch.load(path, map_location="cpu", weights_only=False)
        hidden = ckpt.get("hidden_dim", 16 if model_type == "tiny" else 32)
        m = build_model(model_type=model_type, n_features=8, horizon=10, hidden=hidden, dropout=0.0)
        m.load_state_dict(ckpt["model_state_dict"])
        m.eval()
        return m, ckpt.get("temperature", 1.0)

    tiny_model, tiny_T = load_model("checkpoints_vol10/world_model_best.pt", "tiny")
    wide_model, wide_T = load_model("checkpoints_wide_vol10/world_model_best.pt", "wide")

    # Batch predict
    BATCH = 4096

    def batch_predict(model, T):
        all_m, all_v = [], []
        with torch.no_grad():
            for s in range(0, len(test), BATCH):
                x = torch.tensor(test[s:s+BATCH], dtype=torch.float32)
                m, lv = model(x)
                all_m.append(m.numpy())
                all_v.append((torch.exp(lv) * T).numpy())
        all_m = np.concatenate(all_m)
        all_v = np.concatenate(all_v)
        return np.sum(all_m, 1), np.sqrt(np.sum(all_v, 1))

    tiny_means, tiny_stds = batch_predict(tiny_model, tiny_T)
    wide_means, wide_stds = batch_predict(wide_model, wide_T)

    # Ensemble: weighted average
    ens_means = 0.5 * tiny_means + 0.5 * wide_means
    ens_stds = np.sqrt(0.5 * tiny_stds**2 + 0.5 * wide_stds**2)

    tiny_snrs = np.abs(tiny_means) / (tiny_stds + 1e-10)
    wide_snrs = np.abs(wide_means) / (wide_stds + 1e-10)
    ens_snrs = np.abs(ens_means) / (ens_stds + 1e-10)

    prices = 10000 * np.exp(np.cumsum(test_tgts[:, 0]))

    # Run backtest for each
    def run_bt(pred_means, pred_snrs, label):
        equity = 10000.0; pos = 0; ep = 0.0; es = 0; trades = []; wins = 0
        for i in range(len(prices) - 1):
            m, snr = float(pred_means[i]), float(pred_snrs[i])
            cp = prices[i]
            if pos != 0:
                pp = (cp - ep) / ep * pos
                ex = False
                if pp >= 0.003: ex = True
                elif pp <= -0.001: ex = True
                elif (i - es) >= 60: ex = True
                elif pos == 1 and m < -0.0005: ex = True
                elif pos == -1 and m > 0.0005: ex = True
                if ex:
                    cost = abs(pp) * 0.0003 * 2
                    pnl = equity * (pp - cost)
                    equity += pnl; trades.append(pnl > 0)
                    if pnl > 0: wins += 1
                    pos = 0
            if pos == 0 and snr > 0.30:
                d = 1 if m > 0 else (-1 if m < 0 else 0)
                if d != 0:
                    pos = d; ep = cp; es = i
        n = len(trades)
        wr = wins / max(n, 1) * 100
        ret = (equity / 10000 - 1) * 100
        print(f"  {label:<20} trades={n:>4}  win={wr:>5.1f}%  return={ret:>+7.2f}%")
        return {"label": label, "trades": n, "win_rate": wr, "return": ret}

    print("\n  VOL10 OUT-OF-SAMPLE COMPARISON (26K bars):")
    print("  " + "-" * 60)
    r1 = run_bt(tiny_means, tiny_snrs, "Tiny only (516p)")
    r2 = run_bt(wide_means, wide_snrs, "Wide only (2132p)")
    r3 = run_bt(ens_means, ens_snrs, "Ensemble (50/50)")
    print("  " + "-" * 60)

    # Agreement analysis
    tiny_dir = np.sign(tiny_means)
    wide_dir = np.sign(wide_means)
    agreement = np.mean(tiny_dir == wide_dir) * 100
    print(f"\n  Model agreement: {agreement:.1f}% of bars")
    print(f"  Both UP:   {np.mean((tiny_dir > 0) & (wide_dir > 0))*100:.1f}%")
    print(f"  Both DOWN: {np.mean((tiny_dir < 0) & (wide_dir < 0))*100:.1f}%")
    print(f"  Disagree:  {np.mean(tiny_dir != wide_dir)*100:.1f}%")

    # Best
    best = max([r1, r2, r3], key=lambda x: x["return"])
    print(f"\n  Best: {best['label']} ({best['return']:+.2f}%)")


if __name__ == "__main__":
    backtest_ensemble()
