"""
backtest/engine.py — Full-pipeline backtest engine.

Replays historical data bar-by-bar through the ENTIRE decision stack in
sequence, reusing the exact components used in training/evaluation (no
reimplementation):

    1. World model predicts the next-10-bar distribution (mean/var per step)
    2. Policy produces action + confidence from the env observation
    3. Confidence gate routes: high -> auto-execute, medium -> advisory,
       low -> discard
    4. Governor (backtest/governor_sim.py) checks hard limits before a trade
       is recorded as valid

Output: a full simulated trade/decision log — every decision point, the raw
action, the executed action, confidence score, gate route, governor
interventions, and outcome.

Execution realism note: spread/slippage costs come from TradingEnvConfig —
the SAME cost model used during policy training, per the brief's requirement
of consistency.

Usage:
    python -m backtest.engine --dataset data/dataset_R75_h10.npz \
        --world-model checkpoints_tiny/world_model_best.pt \
        --policy checkpoints_tiny/policy_best.pt \
        --output-dir backtest_results/
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch

from backtest.governor_sim import Governor, GovernorConfig
from env.trading_env import TradingEnv, TradingEnvConfig
from model.policy import PolicyConfig, PolicyNetwork
from model.train_tiny import TINY_FEATURES, TinyWorldModel, load_tiny_dataset


# ---------------------------------------------------------------------------
# Confidence gate
# ---------------------------------------------------------------------------

class ConfidenceGate:
    """
    Routes decisions by stated confidence:
      - conf >= auto_threshold   -> "auto"     (would execute autonomously)
      - conf >= advisory_threshold -> "advisory" (flagged for human review;
                                    NOT executed in this simulation)
      - below                    -> "discard"
    """

    def __init__(self, auto_threshold: float = 0.6, advisory_threshold: float = 0.4):
        if not 0 < advisory_threshold <= auto_threshold <= 1:
            raise ValueError("require 0 < advisory_threshold <= auto_threshold <= 1")
        self.auto_threshold = auto_threshold
        self.advisory_threshold = advisory_threshold

    def route(self, confidence: float) -> str:
        if confidence >= self.auto_threshold:
            return "auto"
        if confidence >= self.advisory_threshold:
            return "advisory"
        return "discard"

    @property
    def summary(self) -> dict:
        return {
            "auto_threshold": self.auto_threshold,
            "advisory_threshold": self.advisory_threshold,
        }


# ---------------------------------------------------------------------------
# Component loading (exact training artifacts)
# ---------------------------------------------------------------------------

def load_world_model(path: str, device: torch.device):
    ckpt = torch.load(path, map_location=device, weights_only=False)
    wm = TinyWorldModel(
        n_features=len(ckpt["feature_names"]),
        horizon=int(ckpt.get("horizon", 10)),
        hidden=int(ckpt.get("hidden_dim", 16)),
        dropout=float(ckpt.get("dropout", 0.1)),
    ).to(device)
    wm.load_state_dict(ckpt["model_state_dict"])
    wm.eval()
    meta = {
        "feature_names": list(ckpt["feature_names"]),
        "norm_mean": np.asarray(ckpt["norm_mean"], dtype=np.float64),
        "norm_std": np.asarray(ckpt["norm_std"], dtype=np.float64),
        "temperature": float(ckpt.get("temperature", 1.0)),
    }
    return wm, meta


def load_policy(path: str, obs_dim: int, device: torch.device) -> PolicyNetwork:
    ckpt = torch.load(path, map_location=device, weights_only=False)
    saved_cfg = ckpt.get("config")
    cfg_kwargs = {}
    for key in ("hidden_dim", "n_layers", "dropout", "n_actions"):
        val = getattr(saved_cfg, key, None) if saved_cfg is not None else (
            saved_cfg.get(key) if isinstance(saved_cfg, dict) else None
        )
        if val is not None:
            cfg_kwargs[key] = val
    cfg = PolicyConfig(obs_dim=obs_dim, **cfg_kwargs)
    policy = PolicyNetwork(cfg).to(device)
    policy.load_state_dict(ckpt["policy_state_dict"])
    policy.eval()
    return policy


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    auto_confidence: float = 0.6
    advisory_confidence: float = 0.4
    governor: GovernorConfig = field(default_factory=GovernorConfig)


class BacktestEngine:
    def __init__(
        self,
        dataset_path: str,
        world_model_path: str | None = None,
        policy_path: str | None = None,
        config: BacktestConfig | None = None,
        env_config: TradingEnvConfig | None = None,
        device_str: str | None = None,
    ):
        self.cfg = config or BacktestConfig()
        self.device = torch.device(
            device_str or ("cuda" if torch.cuda.is_available() else "cpu")
        )

        # --- World model (optional but part of the full pipeline) ---
        self.world_model = None
        self.wm_meta = None
        if world_model_path and Path(world_model_path).exists():
            self.world_model, self.wm_meta = load_world_model(world_model_path, self.device)

        # --- Environment: same dataset, same cost model as training ---
        ds = load_tiny_dataset(dataset_path)
        n_samples = len(ds["targets"])

        # Full-history replay: one episode over all bars.
        env_cfg = env_config or TradingEnvConfig()
        env_cfg.max_steps = n_samples + 1  # loop terminates on end_of_data
        self.env = TradingEnv(dataset_path=dataset_path, tiny_features=TINY_FEATURES,
                              config=env_cfg)

        # --- Policy ---
        if policy_path and Path(policy_path).exists():
            self.policy = load_policy(policy_path, self.env.obs_dim, self.device)
        else:
            print("[engine] no policy checkpoint given — using UNTRAINED random policy")
            self.policy = PolicyNetwork(PolicyConfig(obs_dim=self.env.obs_dim)).to(self.device)

        self.gate = ConfidenceGate(self.cfg.auto_confidence, self.cfg.advisory_confidence)
        self.governor = Governor(self.cfg.governor)

    # -- pipeline pieces --------------------------------------------------

    @torch.no_grad()
    def _world_model_signal(self, raw_tiny_features: np.ndarray) -> tuple[float, float]:
        """SNR of the WM's next-bar prediction: |mean| / sqrt(calibrated var)."""
        if self.world_model is None or self.wm_meta is None:
            return float("nan"), float("nan")
        x = (raw_tiny_features - self.wm_meta["norm_mean"]) / self.wm_meta["norm_std"]
        xb = torch.tensor(x, dtype=torch.float32, device=self.device).unsqueeze(0)
        mean, log_var = self.world_model(xb)
        mean0 = float(mean[0, 0])
        var0 = float(torch.exp(log_var[0, 0])) * self.wm_meta["temperature"]
        snr = abs(mean0) / (var0 ** 0.5 + 1e-12)
        return snr, mean0

    @torch.no_grad()
    def _policy_action(self, obs: np.ndarray) -> tuple[int, float]:
        action, _, _, confidence = self.policy.get_action(
            torch.tensor(obs, dtype=torch.float32, device=self.device),
            deterministic=True,
        )
        return int(action), float(confidence)

    # -- main replay ------------------------------------------------------

    def run(self, verbose: bool = True) -> dict:
        t0 = time.time()
        env = self.env
        obs = env.reset(start_idx=0)
        decisions: list[dict] = []
        equity_curve: list[float] = [env._equity]

        done = False
        bar = 0
        while not done:
            idx = env._episode_start + env._step
            equity_before = env._equity

            # 1-2. World model + policy propose
            raw_feats = env.states[min(idx, env.n_samples - 1), env.tiny_indices]
            snr, wm_mean = self._world_model_signal(raw_feats.astype(np.float64))
            raw_action, confidence = self._policy_action(obs)

            # 3. Confidence gate
            route = self.gate.route(confidence)
            gated_action = raw_action if route == "auto" else 0  # advisory/discard -> flat

            # 4. Governor hard limits
            want_position = env._action_to_position(gated_action)
            final_position, vetoed = self.governor.check(idx, equity_before, want_position)
            final_action = {1: 1, -1: 2, 0: 0}[final_position]

            # Step with the governed action
            obs, reward, done, info = env.step(final_action, confidence=confidence)

            # End-of-data step carries no accounting fields
            if "equity" not in info:
                decisions.append({
                    "bar_idx": int(idx),
                    "timestamp": str(np.datetime_as_string(
                        env.timestamps[min(idx, env.n_samples - 1)], unit="s")),
                    "raw_action": int(raw_action),
                    "executed_action": int(final_action),
                    "gate_route": route,
                    "confidence": round(confidence, 4),
                    "wm_snr": round(snr, 4),
                    "wm_pred_mean_h1": round(wm_mean, 8),
                    "governor_veto": bool(vetoed),
                    "equity_before": round(equity_before, 4),
                    "equity_after": round(equity_before, 4),
                    "position": int(env._position),
                    "reward": 0.0,
                    "note": str(info.get("reason", "end_of_data")),
                })
                break

            # Feed realized PnL of closed trades to the loss-streak tracker
            if info.get("realized_pnl") is not None:
                self.governor.report_trade(idx, info["realized_pnl"])

            decisions.append({
                "bar_idx": int(idx),
                "timestamp": str(np.datetime_as_string(
                    env.timestamps[min(idx, env.n_samples - 1)], unit="s")),
                "raw_action": int(raw_action),
                "executed_action": int(final_action),
                "gate_route": route,
                "confidence": round(confidence, 4),
                "wm_snr": round(snr, 4),
                "wm_pred_mean_h1": round(wm_mean, 8),
                "governor_veto": bool(vetoed),
                "equity_before": round(equity_before, 4),
                "equity_after": round(info["equity"], 4),
                "position": int(info["position"]),
                "reward": round(float(reward), 6),
            })
            equity_curve.append(info["equity"])
            bar += 1
            if verbose and bar % 5000 == 0:
                print(f"[engine] {bar} bars, equity={info['equity']:.2f}")

        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        dd = (peak - eq) / peak
        trades_closed = len(env._trade_returns)
        wins = sum(1 for r in env._trade_returns if r > 0)

        result = {
            "config": {
                "dataset_bars": env.n_samples,
                "gate": self.gate.summary,
                "governor_config": vars(self.cfg.governor),
                "world_model_loaded": self.world_model is not None,
            },
            "metrics": {
                "n_decisions": len(decisions),
                "final_equity": float(eq[-1]),
                "total_return_pct": float((eq[-1] / eq[0] - 1) * 100),
                "max_drawdown_pct": float(dd.max() * 100),
                "trades_closed": trades_closed,
                "win_rate": wins / trades_closed if trades_closed else None,
                "routes": _count(decisions, "gate_route"),
                "governor_vetoes": sum(1 for d in decisions if d["governor_veto"]),
                "actions_raw": _count(decisions, "raw_action"),
                "actions_executed": _count(decisions, "executed_action"),
                "elapsed_s": round(time.time() - t0, 1),
            },
            "governor": self.governor.summary,
            "decisions": decisions,
        }
        return result


def _count(decisions: list[dict], key: str) -> dict:
    out: dict = {}
    for d in decisions:
        k = str(d[key])
        out[k] = out.get(k, 0) + 1
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Full-pipeline backtest engine")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--world-model", default=None)
    parser.add_argument("--policy", default=None)
    parser.add_argument("--output-dir", default="backtest_results")
    parser.add_argument("--auto-confidence", type=float, default=0.6)
    parser.add_argument("--advisory-confidence", type=float, default=0.4)
    parser.add_argument("--max-drawdown-pct", type=float, default=0.10)
    args = parser.parse_args()

    engine = BacktestEngine(
        dataset_path=args.dataset,
        world_model_path=args.world_model,
        policy_path=args.policy,
        config=BacktestConfig(
            auto_confidence=args.auto_confidence,
            advisory_confidence=args.advisory_confidence,
            governor=GovernorConfig(max_drawdown_pct=args.max_drawdown_pct),
        ),
    )
    result = engine.run()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    name = Path(args.dataset).stem.replace("dataset_", "").replace("_h10", "")

    log_path = out_dir / f"trade_log_{name}.json"
    with open(log_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    print("\n" + "=" * 60)
    print(f"BACKTEST SUMMARY — {name}")
    print("=" * 60)
    m = result["metrics"]
    print(f"Decisions:       {m['n_decisions']}")
    print(f"Total return:    {m['total_return_pct']:+.2f}%")
    print(f"Max drawdown:    {m['max_drawdown_pct']:.2f}%")
    print(f"Trades closed:   {m['trades_closed']}  win_rate={m['win_rate']}")
    print(f"Gate routes:     {m['routes']}")
    print(f"Governor vetoes: {m['governor_vetoes']}")
    print(f"Breaker fired:   {result['governor']['breaker_triggered']}")
    print(f"Trade log saved to: {log_path}")


if __name__ == "__main__":
    main()
