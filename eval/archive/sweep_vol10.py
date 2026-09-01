"""
eval/sweep_vol10.py — Sweep strategy parameters to maximize VOL10 out-of-sample Sharpe.

Tests combinations of:
  - SNR threshold: 0.02 to 0.20
  - Take profit: 0.05% to 0.5%
  - Stop loss: 0.05% to 0.5%
  - Max hold: 10 to 60 bars
"""

from __future__ import annotations

import itertools
import json
import logging
import time
from pathlib import Path

import numpy as np
import torch

logger = logging.getLogger(__name__)

SPREAD_COST = 0.0001
SLIPPAGE_COST = 0.00005
TOTAL_COST = SPREAD_COST + SLIPPAGE_COST


def load_data():
    """Load VOL10 data and run model inference on test set."""
    data = np.load("data/dataset_volatility_10_h10.npz", allow_pickle=True)
    states = data["states"]
    targets = data["targets"]
    feature_names = list(data["feature_names"])

    from model.train_tiny import TINY_FEATURES, build_model

    name_to_idx = {name: i for i, name in enumerate(feature_names)}
    tiny_indices = [name_to_idx[f] for f in TINY_FEATURES if f in name_to_idx]
    tiny_states = states[:, tiny_indices]

    # Train/test split (90/10)
    split = int(len(tiny_states) * 0.90)

    # Normalize on train only
    norm_mean = tiny_states[:split].mean(axis=0)
    norm_std = tiny_states[:split].std(axis=0) + 1e-8

    test_states = (tiny_states[split:] - norm_mean) / norm_std
    test_targets = targets[split:]

    # Load model
    ckpt = torch.load("checkpoints_vol10/world_model_best.pt", map_location="cpu", weights_only=False)
    model_type = ckpt.get("model_type", "tiny")
    hidden = ckpt.get("hidden_dim", 16 if model_type == "tiny" else 32)
    model = build_model(model_type=model_type, n_features=8, horizon=10, hidden=hidden, dropout=0.0)
    model.load_state_dict(ckpt["model_state_dict"])
    model.eval()
    temperature = ckpt.get("temperature", 1.0)

    # Batch predict
    BATCH = 4096
    all_means, all_vars = [], []
    with torch.no_grad():
        for start in range(0, len(test_states), BATCH):
            x = torch.tensor(test_states[start:start + BATCH], dtype=torch.float32)
            mean, log_var = model(x)
            var = torch.exp(log_var) * temperature
            all_means.append(mean.numpy())
            all_vars.append(var.numpy())

    all_means = np.concatenate(all_means, axis=0)
    all_vars = np.concatenate(all_vars, axis=0)
    total_means = np.sum(all_means, axis=1)
    total_stds = np.sqrt(np.sum(all_vars, axis=1))
    snrs = np.abs(total_means) / (total_stds + 1e-10)

    # Price series from actual returns
    price_returns = test_targets[:, 0]
    prices = 10000 * np.exp(np.cumsum(price_returns))

    return total_means, snrs, prices, price_returns


def run_backtest(total_means, snrs, prices, snr_thresh, tp, sl, max_hold):
    """Run a single backtest with given parameters. Returns metrics dict."""
    equity = 10000.0
    position = 0
    entry_price = 0.0
    entry_step = 0
    total_costs = 0.0
    trades = []
    equity_curve = [equity]

    for i in range(len(prices) - 1):
        total_mean = float(total_means[i])
        snr = float(snrs[i])
        current_price = prices[i]

        # Exit
        if position != 0:
            bars_held = i - entry_step
            pnl_pct = (current_price - entry_price) / entry_price * position

            should_exit = False
            if pnl_pct >= tp:
                should_exit = True
            elif pnl_pct <= -sl:
                should_exit = True
            elif bars_held >= max_hold:
                should_exit = True
            elif position == 1 and total_mean < -0.0005:
                should_exit = True
            elif position == -1 and total_mean > 0.0005:
                should_exit = True

            if should_exit:
                raw_pnl = (current_price - entry_price) * position
                cost = abs(raw_pnl) * TOTAL_COST * 2
                net_pnl = raw_pnl - cost
                equity += net_pnl
                total_costs += cost
                trades.append({"pnl": net_pnl, "bars": bars_held})
                position = 0

        # Entry
        if position == 0 and snr > snr_thresh:
            if total_mean > 0:
                position = 1
                entry_price = current_price
                entry_step = i
            elif total_mean < 0:
                position = -1
                entry_price = current_price
                entry_step = i

        equity_curve.append(equity)

    # Close open position
    if position != 0:
        raw_pnl = (prices[-1] - entry_price) * position
        cost = abs(raw_pnl) * TOTAL_COST
        net_pnl = raw_pnl - cost
        equity += net_pnl
        total_costs += cost
        trades.append({"pnl": net_pnl, "bars": len(prices) - 1 - entry_step})

    eq = np.array(equity_curve)
    rets = np.diff(eq) / np.maximum(eq[:-1], 1)
    total_return = (equity / 10000 - 1) * 100
    sharpe = float(np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(3628800))
    running_max = np.maximum.accumulate(eq)
    max_dd = float(np.max((running_max - eq) / np.maximum(running_max, 1)))

    n_trades = len(trades)
    win_rate = sum(1 for t in trades if t["pnl"] > 0) / max(n_trades, 1)
    avg_pnl = np.mean([t["pnl"] for t in trades]) if trades else 0

    # Buy and hold
    bnh_eq = 10000 * np.exp(np.cumsum(np.concatenate([[0], [float(r) for r in np.diff(np.log(prices))]])))
    bnh_return = (bnh_eq[-1] / 10000 - 1) * 100

    return {
        "snr": snr_thresh, "tp": tp, "sl": sl, "hold": max_hold,
        "return": total_return, "sharpe": sharpe, "max_dd": max_dd,
        "trades": n_trades, "win_rate": win_rate, "avg_pnl": float(avg_pnl),
        "costs": total_costs, "bnh_return": bnh_return,
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("Loading VOL10 data and running model inference...")
    t0 = time.time()
    total_means, snrs, prices, price_returns = load_data()
    print(f"Data ready: {len(prices)} bars, {time.time()-t0:.1f}s")
    print(f"SNR range: {snrs.min():.4f} - {snrs.max():.4f}, median: {np.median(snrs):.4f}")

    # Parameter grid
    snr_thresholds = [0.01, 0.02, 0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]
    take_profits = [0.0005, 0.001, 0.002, 0.003, 0.005]       # 0.05% to 0.5%
    stop_losses = [0.0005, 0.001, 0.002, 0.003, 0.005]        # 0.05% to 0.5%
    max_holds = [10, 20, 30, 60]

    total = len(snr_thresholds) * len(take_profits) * len(stop_losses) * len(max_holds)
    print(f"Sweeping {total} combinations...")

    results = []
    count = 0
    for snr_thresh, tp, sl, hold in itertools.product(snr_thresholds, take_profits, stop_losses, max_holds):
        count += 1
        r = run_backtest(total_means, snrs, prices, snr_thresh, tp, sl, hold)
        results.append(r)

        if count % 100 == 0:
            print(f"  [{count}/{total}]")

    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)

    # Save all results
    output_path = Path("checkpoints/sweep_vol10.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    def _p(v):
        return float(v) if isinstance(v, (np.floating, np.integer)) else v
    with open(output_path, "w") as f:
        json.dump([{k: _p(v) for k, v in r.items()} for r in results[:50]], f, indent=2)

    # Print top 20
    print(f"\n{'='*100}")
    print(f"  TOP 20 STRATEGIES FOR VOL10 (out-of-sample)")
    print(f"{'='*100}")
    print(f"  {'Rank':>4} {'SNR':>6} {'TP%':>6} {'SL%':>6} {'Hold':>5} {'Return':>8} {'Sharpe':>8} {'MaxDD':>7} {'Trades':>7} {'Win%':>6} {'Costs':>8}")
    print(f"  {'-'*92}")

    for i, r in enumerate(results[:20]):
        marker = " <-- BEST" if i == 0 else ""
        print(f"  {i+1:>4} {r['snr']:>6.2f} {r['tp']*100:>5.2f}% {r['sl']*100:>5.2f}% {r['hold']:>5} {r['return']:>+7.2f}% {r['sharpe']:>8.2f} {r['max_dd']:>6.1%} {r['trades']:>7} {r['win_rate']:>5.0%} ${r['costs']:>7.2f}{marker}")

    # Best params
    best = results[0]
    print(f"\n{'='*100}")
    print(f"  BEST STRATEGY:")
    print(f"    SNR threshold: {best['snr']}")
    print(f"    Take profit:   {best['tp']*100:.2f}%")
    print(f"    Stop loss:     {best['sl']*100:.2f}%")
    print(f"    Max hold:      {best['hold']} bars")
    print(f"    Return:        {best['return']:+.2f}%")
    print(f"    Sharpe:        {best['sharpe']:.2f}")
    print(f"    Win rate:      {best['win_rate']:.0%}")
    print(f"    Trades:        {best['trades']}")
    print(f"    Buy-and-hold:  {best['bnh_return']:+.2f}%")
    print(f"    Edge vs B&H:   {best['return'] - best['bnh_return']:+.2f}%")
    print(f"{'='*100}")


if __name__ == "__main__":
    main()
