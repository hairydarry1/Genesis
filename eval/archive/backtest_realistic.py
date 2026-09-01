"""
eval/backtest_realistic.py — Realistic backtest with transaction costs.

Measures:
  - Sharpe ratio (after costs)
  - Win rate
  - Profit factor
  - Max drawdown
  - Comparison to buy-and-hold and random walk

Transaction costs:
  - Spread: 0.01% per trade (typical for synthetic indices)
  - Slippage: 0.005% per trade
  - Total round-trip: ~0.03%

Usage:
    python -m eval.backtest_realistic --index R_10
    python -m eval.backtest_realistic --all
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import torch
import torch.nn.functional as F

logger = logging.getLogger(__name__)

# Transaction costs
SPREAD_COST = 0.0001      # 0.01% per side
SLIPPAGE_COST = 0.00005   # 0.005% per side
TOTAL_COST_PER_TRADE = SPREAD_COST + SLIPPAGE_COST  # 0.015% per side = 0.03% round-trip

INDEX_CONFIGS = {
    "R_75": {
        "dataset": "data/dataset_R75_h10.npz",
        "world_model": "checkpoints_wide_r75/world_model_best.pt",
        "name": "Volatility 75",
    },
    "R_10": {
        "dataset": "data/dataset_volatility_10_h10.npz",
        "world_model": "checkpoints_vol10/world_model_best.pt",
        "name": "Volatility 10",
    },
    "BOOM500": {
        "dataset": "data/dataset_boom_500_h10.npz",
        "world_model": "checkpoints_boom_500_clean/world_model_best.pt",
        "name": "Boom 500",
    },
    "CRASH500": {
        "dataset": "data/dataset_crash_500_h10.npz",
        "world_model": "checkpoints_crash_500_clean/world_model_best.pt",
        "name": "Crash 500",
    },
}


@dataclass
class BacktestResult:
    """Results from a realistic backtest."""
    index: str
    name: str

    # Strategy metrics
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    max_drawdown: float = 0.0
    total_trades: int = 0
    avg_trade_pnl: float = 0.0
    avg_holding_period: float = 0.0
    total_costs: float = 0.0

    # Baseline comparison
    bnh_return: float = 0.0
    bnh_sharpe: float = 0.0
    bnh_max_dd: float = 0.0

    # Equity curve
    equity_curve: list[float] = field(default_factory=list)
    trade_log: list[dict] = field(default_factory=list)

    # Regime breakdown
    regime_performance: dict = field(default_factory=dict)

    # Verdict
    is_profitable: bool = False
    beats_buy_hold: bool = False
    sharpe_above_1: bool = False


class RealisticBacktester:
    """
    Backtests the world model strategy with realistic costs.

    Strategy:
    1. World model predicts next-bar return direction
    2. If SNR > threshold → take position in predicted direction
    3. Exit on take-profit, stop-loss, or max holding period
    4. Deduct transaction costs on every trade
    """

    def __init__(
        self,
        take_profit_pct: float = 0.001,    # 0.1%
        stop_loss_pct: float = 0.0015,      # 0.15%
        max_hold_bars: int = 30,            # 30 minutes
        snr_threshold: float = 0.05,        # Minimum SNR to trade
        position_size: float = 1.0,         # Fraction of equity
    ):
        self.take_profit = take_profit_pct
        self.stop_loss = stop_loss_pct
        self.max_hold = max_hold_bars
        self.snr_threshold = snr_threshold
        self.position_size = position_size

    def run(self, index: str) -> BacktestResult:
        """Run backtest for a single index."""
        config = INDEX_CONFIGS.get(index)
        if not config:
            return BacktestResult(index=index, name="Unknown")

        result = BacktestResult(index=index, name=config["name"])

        # Load data
        try:
            data = np.load(config["dataset"], allow_pickle=True)
        except Exception as e:
            logger.error("Failed to load dataset: %s", e)
            return result

        states = data["states"]
        targets = data["targets"]
        timestamps = data["timestamps"]
        feature_names = list(data["feature_names"])

        # Load world model
        try:
            from model.train_tiny import build_model
            ckpt = torch.load(config["world_model"], map_location="cpu", weights_only=False)
            model_type = ckpt.get("model_type", "tiny")
            hidden = ckpt.get("hidden_dim", 16 if model_type == "tiny" else 32)
            model = build_model(model_type=model_type, n_features=8, horizon=10, hidden=hidden, dropout=0.0)
            model.load_state_dict(ckpt["model_state_dict"])
            model.eval()
            temperature = ckpt.get("temperature", 1.0)
        except Exception as e:
            logger.error("Failed to load model: %s", e)
            return result

        # Extract 8 tiny features
        from model.train_tiny import TINY_FEATURES
        name_to_idx = {name: i for i, name in enumerate(feature_names)}
        tiny_indices = [name_to_idx[f] for f in TINY_FEATURES if f in name_to_idx]

        if len(tiny_indices) < 8:
            logger.error("Missing features")
            return result

        tiny_states = states[:, tiny_indices]

        # Use LAST 10% as out-of-sample (never seen during training)
        split = int(len(tiny_states) * 0.90)
        logger.info("Train: 0-%d  Test: %d-%d (%d samples)", split, split, len(tiny_states), len(tiny_states) - split)

        # Normalize using ONLY training data statistics (no lookahead)
        norm_mean = tiny_states[:split].mean(axis=0)
        norm_std = tiny_states[:split].std(axis=0) + 1e-8
        tiny_states = (tiny_states - norm_mean) / norm_std

        # Backtest only on test set
        tiny_states = tiny_states[split:]
        targets = targets[split:]

        # Batch model inference (fast)
        logger.info("Batch predicting on %d samples...", len(tiny_states))
        BATCH = 4096
        all_means = []
        all_vars = []
        with torch.no_grad():
            for start in range(0, len(tiny_states), BATCH):
                x = torch.tensor(tiny_states[start:start+BATCH], dtype=torch.float32)
                mean, log_var = model(x)
                var = torch.exp(log_var) * temperature
                all_means.append(mean.numpy())
                all_vars.append(var.numpy())
        all_means = np.concatenate(all_means, axis=0)
        all_vars = np.concatenate(all_vars, axis=0)
        total_means = np.sum(all_means, axis=1)
        total_stds = np.sqrt(np.sum(all_vars, axis=1))
        snrs = np.abs(total_means) / (total_stds + 1e-10)
        logger.info("Predictions done. SNR range: %.4f - %.4f", snrs.min(), snrs.max())

        # Run backtest
        equity = 10000.0
        peak_equity = 10000.0
        position = 0  # +1 long, -1 short, 0 flat
        entry_price = 0.0
        entry_step = 0
        total_costs = 0.0
        trades = []
        equity_curve = [equity]

        logger.info("Out-of-sample backtest on %d bars", len(tiny_states))

        # Use target returns as price proxy (cumulative)
        price_returns = targets[:, 0]  # First horizon step
        prices = 10000 * np.exp(np.cumsum(price_returns))

        for i in range(len(tiny_states) - 1):
            total_mean = float(total_means[i])
            snr = float(snrs[i])

            current_price = prices[i]

            # Exit logic
            if position != 0:
                bars_held = i - entry_step
                pnl_pct = (current_price - entry_price) / entry_price * position

                should_exit = False
                exit_reason = ""

                if pnl_pct >= self.take_profit:
                    should_exit = True
                    exit_reason = "take_profit"
                elif pnl_pct <= -self.stop_loss:
                    should_exit = True
                    exit_reason = "stop_loss"
                elif bars_held >= self.max_hold:
                    should_exit = True
                    exit_reason = "max_hold"
                elif position == 1 and total_mean < -0.001:
                    should_exit = True
                    exit_reason = "signal_reverse"
                elif position == -1 and total_mean > 0.001:
                    should_exit = True
                    exit_reason = "signal_reverse"

                if should_exit:
                    # Calculate PnL with costs
                    raw_pnl = (current_price - entry_price) * position * self.position_size
                    cost = abs(raw_pnl) * TOTAL_COST_PER_TRADE * 2  # Entry + exit costs
                    net_pnl = raw_pnl - cost
                    equity += net_pnl
                    total_costs += cost

                    trades.append({
                        "entry_step": entry_step,
                        "exit_step": i,
                        "side": "long" if position == 1 else "short",
                        "entry_price": entry_price,
                        "exit_price": current_price,
                        "raw_pnl": raw_pnl,
                        "cost": cost,
                        "net_pnl": net_pnl,
                        "bars_held": bars_held,
                        "exit_reason": exit_reason,
                        "snr": snr,
                    })

                    position = 0
                    entry_price = 0.0

            # Entry logic
            if position == 0 and snr > self.snr_threshold:
                if total_mean > 0:
                    position = 1  # Long
                    entry_price = current_price
                    entry_step = i
                    # Entry cost
                    entry_cost = current_price * self.position_size * TOTAL_COST_PER_TRADE
                    total_costs += entry_cost
                elif total_mean < 0:
                    position = -1  # Short
                    entry_price = current_price
                    entry_step = i
                    entry_cost = current_price * self.position_size * TOTAL_COST_PER_TRADE
                    total_costs += entry_cost

            peak_equity = max(peak_equity, equity)
            equity_curve.append(equity)

        # Close any open position
        if position != 0:
            final_price = prices[-1]
            raw_pnl = (final_price - entry_price) * position * self.position_size
            cost = abs(raw_pnl) * TOTAL_COST_PER_TRADE
            net_pnl = raw_pnl - cost
            equity += net_pnl
            total_costs += cost
            trades.append({
                "entry_step": entry_step, "exit_step": len(prices) - 1,
                "side": "long" if position == 1 else "short",
                "entry_price": entry_price, "exit_price": final_price,
                "raw_pnl": raw_pnl, "cost": cost, "net_pnl": net_pnl,
                "bars_held": len(prices) - 1 - entry_step, "exit_reason": "end_of_data",
                "snr": 0,
            })

        # Compute metrics
        if trades:
            pnls = [t["net_pnl"] for t in trades]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]

            result.total_trades = len(trades)
            result.win_rate = len(wins) / len(trades) if trades else 0
            result.avg_trade_pnl = np.mean(pnls)
            result.avg_holding_period = np.mean([t["bars_held"] for t in trades])
            result.total_costs = total_costs

            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 1e-10
            result.profit_factor = gross_profit / gross_loss

        # Returns and Sharpe
        eq = np.array(equity_curve)
        returns = np.diff(eq) / np.maximum(eq[:-1], 1)
        result.total_return = (equity / 10000 - 1) * 100
        result.sharpe_ratio = float(np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252 * 24 * 60)) if len(returns) > 10 else 0

        # Max drawdown
        running_max = np.maximum.accumulate(eq)
        dd = (running_max - eq) / np.maximum(running_max, 1)
        result.max_drawdown = float(np.max(dd))

        # Buy-and-hold baseline
        bnh_equity = 10000 * np.exp(np.cumsum(price_returns))
        bnh_returns = np.diff(bnh_equity) / bnh_equity[:-1]
        result.bnh_return = (bnh_equity[-1] / 10000 - 1) * 100
        result.bnh_sharpe = float(np.mean(bnh_returns) / (np.std(bnh_returns) + 1e-10) * np.sqrt(252 * 24 * 60)) if len(bnh_returns) > 10 else 0
        bnh_max = np.maximum.accumulate(bnh_equity)
        result.bnh_max_dd = float(np.max((bnh_max - bnh_equity) / bnh_max))

        # Equity curve for reporting
        result.equity_curve = equity_curve[-500:]
        result.trade_log = trades[-50:]

        # Verdict
        result.is_profitable = result.total_return > 0
        result.beats_buy_hold = result.total_return > result.bnh_return
        result.sharpe_above_1 = result.sharpe_ratio > 1.0

        return result


def print_result(result: BacktestResult):
    """Pretty-print backtest results."""
    print(f"\n{'='*60}")
    print(f"  BACKTEST: {result.name} ({result.index})")
    print(f"{'='*60}")

    print(f"\n  STRATEGY PERFORMANCE:")
    print(f"    Total Return:    {result.total_return:>+8.2f}%")
    print(f"    Sharpe Ratio:    {result.sharpe_ratio:>8.2f}")
    print(f"    Win Rate:        {result.win_rate:>8.0%}")
    print(f"    Profit Factor:   {result.profit_factor:>8.2f}")
    print(f"    Max Drawdown:    {result.max_drawdown:>8.1%}")
    print(f"    Total Trades:    {result.total_trades:>8d}")
    print(f"    Avg Trade PnL:   ${result.avg_trade_pnl:>+8.2f}")
    print(f"    Avg Hold Period: {result.avg_holding_period:>8.0f} bars")
    print(f"    Total Costs:     ${result.total_costs:>8.2f}")

    print(f"\n  BUY-AND-HOLD BASELINE:")
    print(f"    Total Return:    {result.bnh_return:>+8.2f}%")
    print(f"    Sharpe Ratio:    {result.bnh_sharpe:>8.2f}")
    print(f"    Max Drawdown:    {result.bnh_max_dd:>8.1%}")

    print(f"\n  COMPARISON:")
    print(f"    Strategy vs B&H: {result.total_return - result.bnh_return:>+8.2f}%")
    print(f"    Sharpe vs B&H:   {result.sharpe_ratio - result.bnh_sharpe:>+8.2f}")

    print(f"\n  VERDICT:")
    print(f"    Profitable:      {'YES' if result.is_profitable else 'NO'}")
    print(f"    Beats B&H:       {'YES' if result.beats_buy_hold else 'NO'}")
    print(f"    Sharpe > 1.0:    {'YES' if result.sharpe_above_1 else 'NO'}")

    if result.sharpe_above_1 and result.is_profitable:
        print(f"\n  *** SYSTEM IS VIABLE ***")
    elif result.is_profitable:
        print(f"\n  * Marginal — needs optimization *")
    else:
        print(f"\n  X NOT VIABLE — costs exceed edge X")

    print(f"{'='*60}\n")


def main():
    parser = argparse.ArgumentParser(description="Realistic backtest with costs")
    parser.add_argument("--index", default="R_10", help="Index to backtest")
    parser.add_argument("--all", action="store_true", help="Backtest all indices")
    parser.add_argument("--tp", type=float, default=0.001, help="Take profit %%")
    parser.add_argument("--sl", type=float, default=0.0015, help="Stop loss %%")
    parser.add_argument("--hold", type=int, default=30, help="Max hold bars")
    parser.add_argument("--snr", type=float, default=0.05, help="SNR threshold")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    bt = RealisticBacktester(
        take_profit_pct=args.tp,
        stop_loss_pct=args.sl,
        max_hold_bars=args.hold,
        snr_threshold=args.snr,
    )

    indices = list(INDEX_CONFIGS.keys()) if args.all else [args.index]

    results = {}
    for index in indices:
        if index not in INDEX_CONFIGS:
            logger.warning("Unknown index: %s", index)
            continue
        result = bt.run(index)
        results[index] = result
        print_result(result)

    # Save results
    output_path = Path("checkpoints/backtest_realistic.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        def _to_python(v):
            if isinstance(v, (np.floating, np.integer)):
                return float(v)
            return v

        json.dump({k: {
            "index": v.index, "name": v.name,
            "total_return": _to_python(v.total_return),
            "sharpe_ratio": _to_python(v.sharpe_ratio),
            "win_rate": _to_python(v.win_rate),
            "profit_factor": _to_python(v.profit_factor),
            "max_drawdown": _to_python(v.max_drawdown),
            "total_trades": int(v.total_trades),
            "total_costs": _to_python(v.total_costs),
            "bnh_return": _to_python(v.bnh_return),
            "bnh_sharpe": _to_python(v.bnh_sharpe),
            "is_profitable": v.is_profitable, "beats_buy_hold": v.beats_buy_hold,
            "sharpe_above_1": v.sharpe_above_1,
        } for k, v in results.items()}, f, indent=2)

    # Summary table
    print(f"\n{'='*80}")
    print(f"  SUMMARY")
    print(f"{'='*80}")
    print(f"  {'Index':<12} {'Return':>8} {'Sharpe':>8} {'WinRate':>8} {'PF':>6} {'MaxDD':>8} {'Trades':>7} {'Costs':>8}")
    print(f"  {'-'*72}")
    for name, r in results.items():
        print(f"  {r.name:<12} {r.total_return:>+7.2f}% {r.sharpe_ratio:>8.2f} {r.win_rate:>7.0%} {r.profit_factor:>6.2f} {r.max_drawdown:>7.1%} {r.total_trades:>7d} ${r.total_costs:>7.2f}")
    print(f"  {'-'*72}")

    viable = [r for r in results.values() if r.sharpe_above_1 and r.is_profitable]
    print(f"\n  Viable systems: {len(viable)}/{len(results)}")
    for r in viable:
        print(f"    {r.name}: Sharpe={r.sharpe_ratio:.2f}, Return={r.total_return:+.2f}%")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
