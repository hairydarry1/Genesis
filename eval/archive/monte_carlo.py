"""
eval/monte_carlo.py — Monte Carlo simulation for strategy robustness.

Tests:
  1. Randomized trade order — does edge survive different sequences?
  2. Bootstrap confidence intervals — what's the true range of returns?
  3. Worst-case scenarios — what's the maximum possible drawdown?
  4. Ruin probability — what's the chance of losing everything?

Usage:
    python -m eval.monte_carlo --trades checkpoints_vol10/paper_trades.json
    python -m eval.monte_carlo --backtest  # Run on synthetic trades
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np


class MonteCarloSimulator:
    """
    Monte Carlo simulation for trading strategy robustness.

    Method:
      1. Take historical trade PnLs
      2. Randomly shuffle order (1000+ times)
      3. Compute equity curve for each shuffle
      4. Analyze distribution of outcomes
    """

    def __init__(self, n_simulations: int = 1000, initial_equity: float = 10000.0):
        self.n_simulations = n_simulations
        self.initial_equity = initial_equity

    def load_trades(self, path: str) -> np.ndarray:
        """Load trade PnLs from JSON log."""
        with open(path) as f:
            trades = json.load(f)
        pnls = [t["pnl"] for t in trades if "pnl" in t]
        return np.array(pnls)

    def simulate(self, trade_pnls: np.ndarray) -> dict:
        """
        Run Monte Carlo simulation.
        Returns distribution of outcomes.
        """
        n_trades = len(trade_pnls)
        if n_trades < 10:
            return {"error": "Need at least 10 trades"}

        # Observed statistics
        win_rate = np.mean(trade_pnls > 0)
        avg_win = np.mean(trade_pnls[trade_pnls > 0]) if np.any(trade_pnls > 0) else 0
        avg_loss = np.mean(trade_pnls[trade_pnls <= 0]) if np.any(trade_pnls <= 0) else 0
        total_pnl = np.sum(trade_pnls)
        profit_factor = abs(np.sum(trade_pnls[trade_pnls > 0]) / max(abs(np.sum(trade_pnls[trade_pnls <= 0])), 1e-10))

        # Run simulations
        final_equities = []
        max_drawdowns = []
        ruin_count = 0
        positive_count = 0

        for _ in range(self.n_simulations):
            # Random shuffle of trade order
            shuffled = np.random.permutation(trade_pnls)

            equity = self.initial_equity
            peak = equity
            max_dd = 0
            ruined = False

            for pnl in shuffled:
                equity += pnl
                if equity <= 0:
                    ruined = True
                    break
                peak = max(peak, equity)
                dd = (peak - equity) / peak
                max_dd = max(max_dd, dd)

            final_equities.append(equity)
            max_drawdowns.append(max_dd * 100)
            if ruined:
                ruin_count += 1
            if equity > self.initial_equity:
                positive_count += 1

        final_equities = np.array(final_equities)
        max_drawdowns = np.array(max_drawdowns)

        # Confidence intervals
        ci_5 = np.percentile(final_equities, 5)
        ci_25 = np.percentile(final_equities, 25)
        ci_50 = np.percentile(final_equities, 50)
        ci_75 = np.percentile(final_equities, 75)
        ci_95 = np.percentile(final_equities, 95)

        return {
            "n_trades": n_trades,
            "n_simulations": self.n_simulations,
            "win_rate": win_rate,
            "avg_win": float(avg_win),
            "avg_loss": float(avg_loss),
            "profit_factor": profit_factor,
            "total_pnl_observed": float(total_pnl),

            # Distribution of final equity
            "final_equity_mean": float(np.mean(final_equities)),
            "final_equity_median": float(ci_50),
            "final_equity_std": float(np.std(final_equities)),
            "ci_5": float(ci_5),
            "ci_25": float(ci_25),
            "ci_50": float(ci_50),
            "ci_75": float(ci_75),
            "ci_95": float(ci_95),

            # Drawdown distribution
            "max_dd_mean": float(np.mean(max_drawdowns)),
            "max_dd_median": float(np.median(max_drawdowns)),
            "max_dd_95": float(np.percentile(max_drawdowns, 95)),

            # Risk metrics
            "ruin_probability": ruin_count / self.n_simulations,
            "probability_of_profit": positive_count / self.n_simulations,

            # Return distribution
            "return_mean": float(np.mean(final_equities / self.initial_equity - 1) * 100),
            "return_median": float(np.median(final_equities / self.initial_equity - 1) * 100),
        }

    def print_report(self, results: dict):
        """Print Monte Carlo report."""
        if "error" in results:
            print(f"  Error: {results['error']}")
            return

        print(f"\n{'='*65}")
        print(f"  MONTE CARLO SIMULATION ({results['n_simulations']} runs, {results['n_trades']} trades)")
        print(f"{'='*65}")

        print(f"\n  OBSERVED TRADE STATISTICS:")
        print(f"    Win rate:         {results['win_rate']:.1%}")
        print(f"    Avg win:          ${results['avg_win']:+.2f}")
        print(f"    Avg loss:         ${results['avg_loss']:+.2f}")
        print(f"    Profit factor:    {results['profit_factor']:.2f}")

        print(f"\n  EQUITY DISTRIBUTION (after shuffling trade order):")
        print(f"    Worst case (5%):  ${results['ci_5']:,.2f}  ({(results['ci_5']/10000-1)*100:+.1f}%)")
        print(f"    25th percentile:  ${results['ci_25']:,.2f}  ({(results['ci_25']/10000-1)*100:+.1f}%)")
        print(f"    Median (50%):     ${results['ci_50']:,.2f}  ({(results['ci_50']/10000-1)*100:+.1f}%)")
        print(f"    75th percentile:  ${results['ci_75']:,.2f}  ({(results['ci_75']/10000-1)*100:+.1f}%)")
        print(f"    Best case (95%):  ${results['ci_95']:,.2f}  ({(results['ci_95']/10000-1)*100:+.1f}%)")

        print(f"\n  RISK METRICS:")
        print(f"    Ruin probability: {results['ruin_probability']:.1%}")
        print(f"    Prob of profit:   {results['probability_of_profit']:.1%}")
        print(f"    Mean max DD:      {results['max_dd_mean']:.1f}%")
        print(f"    95th %ile DD:     {results['max_dd_95']:.1f}%")

        # Verdict
        print(f"\n  {'='*65}")
        if results["ruin_probability"] < 0.01 and results["probability_of_profit"] > 0.6:
            print(f"  VERDICT: ROBUST — low ruin risk, high probability of profit")
        elif results["ruin_probability"] < 0.05 and results["probability_of_profit"] > 0.5:
            print(f"  VERDICT: ACCEPTABLE — moderate risk, likely profitable")
        elif results["probability_of_profit"] > 0.4:
            print(f"  VERDICT: MARGINAL — edge is thin, needs more trades to confirm")
        else:
            print(f"  VERDICT: WEAK — high risk of loss, strategy needs improvement")
        print(f"{'='*65}\n")


def run_synthetic_backtest():
    """Run Monte Carlo on synthetic VOL10 trades from backtest."""
    from model.train_tiny import TINY_FEATURES, build_model
    import torch

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

    # Load ensemble
    from model.ensemble import VOL10Ensemble
    ens = VOL10Ensemble()
    ens.load()

    # Batch predict
    BATCH = 4096
    all_m, all_s = [], []
    for s in range(0, len(test), BATCH):
        x = torch.tensor(test[s:s+BATCH], dtype=torch.float32)
        m, snr = ens.predict(test[s:s+BATCH])
        all_m.append(m)
        all_s.append(snr)

    # Actually, predict individually and average
    def batch_pred(model, T, test_data):
        all_m, all_v = [], []
        with torch.no_grad():
            for s in range(0, len(test_data), BATCH):
                x = torch.tensor(test_data[s:s+BATCH], dtype=torch.float32)
                m, lv = model(x)
                all_m.append(m.numpy())
                all_v.append((torch.exp(lv) * T).numpy())
        return np.concatenate(all_m), np.concatenate(all_v)

    tiny_m, tiny_v = batch_pred(ens.tiny_model, ens.tiny_temperature, test)
    wide_m, wide_v = batch_pred(ens.wide_model, ens.wide_temperature, test)
    ens_m = 0.5 * tiny_m + 0.5 * wide_m
    ens_v = 0.5 * tiny_v + 0.5 * wide_v
    tm = np.sum(ens_m, 1)
    ts_ = np.sqrt(np.sum(ens_v, 1))
    snrs = np.abs(tm) / (ts_ + 1e-10)

    prices = 10000 * np.exp(np.cumsum(test_tgts[:, 0]))

    # Generate trades
    equity = 10000.0
    pos = 0
    ep = 0.0
    es = 0
    trade_pnls = []

    for i in range(len(prices) - 1):
        m, snr = float(tm[i]), float(snrs[i])
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
                trade_pnls.append(pnl)
                equity += pnl
                pos = 0

        if pos == 0 and snr > 0.30:
            d = 1 if m > 0 else (-1 if m < 0 else 0)
            if d != 0:
                pos = d
                ep = cp
                es = i

    return np.array(trade_pnls)


def main():
    parser = argparse.ArgumentParser(description="Monte Carlo simulation")
    parser.add_argument("--trades", default="checkpoints_vol10/paper_trades.json")
    parser.add_argument("--backtest", action="store_true", help="Use synthetic backtest trades")
    parser.add_argument("--sims", type=int, default=1000, help="Number of simulations")
    args = parser.parse_args()

    sim = MonteCarloSimulator(n_simulations=args.sims)

    if args.backtest:
        print("Generating synthetic trades from VOL10 ensemble backtest...")
        trade_pnls = run_synthetic_backtest()
        print(f"  Generated {len(trade_pnls)} trades")
    else:
        if not Path(args.trades).exists():
            print(f"No trades file found at {args.trades}")
            print("Run with --backtest to use synthetic trades")
            return
        trade_pnls = sim.load_trades(args.trades)
        print(f"  Loaded {len(trade_pnls)} trades from {args.trades}")

    results = sim.simulate(trade_pnls)
    sim.print_report(results)

    # Save results
    output = Path("checkpoints/monte_carlo_results.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  Results saved to {output}")


if __name__ == "__main__":
    main()
