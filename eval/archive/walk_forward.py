"""
eval/walk_forward.py — Walk-forward optimization for VOL10 strategy.

Method:
  1. Split data into rolling windows (train=3 months, test=1 month)
  2. On each training window, sweep parameters to find optimal settings
  3. Test on the next out-of-sample window
  4. Roll forward and repeat
  5. Aggregate results across all windows

This prevents overfitting by ensuring parameters work on unseen data.

Usage:
    python -m eval.walk_forward
    python -m eval.walk_forward --train-bars 90000 --test-bars 30000
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import torch


logger = logging.getLogger(__name__)

# Cost model
TOTAL_COST = 0.00015  # 0.015% per side = 0.03% round-trip


@dataclass
class WindowResult:
    """Results from one walk-forward window."""
    window_id: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int

    # Best params found during training
    best_snr: float = 0.30
    best_tp: float = 0.0030
    best_sl: float = 0.0010
    best_hold: int = 60

    # Training performance
    train_return: float = 0.0
    train_sharpe: float = 0.0
    train_trades: int = 0

    # Test performance (out-of-sample)
    test_return: float = 0.0
    test_sharpe: float = 0.0
    test_trades: int = 0
    test_win_rate: float = 0.0
    test_max_dd: float = 0.0


def load_model_and_data():
    """Load VOL10 data and ensemble models."""
    from model.train_tiny import TINY_FEATURES, build_model

    data = np.load("data/dataset_volatility_10_h10.npz", allow_pickle=True)
    states = data["states"]
    targets = data["targets"]
    feature_names = list(data["feature_names"])

    name_to_idx = {n: i for i, n in enumerate(feature_names)}
    tiny_idx = [name_to_idx[f] for f in TINY_FEATURES if f in name_to_idx]
    tiny_states = states[:, tiny_idx]

    # Load ensemble
    from model.ensemble import VOL10Ensemble
    ens = VOL10Ensemble()
    ens.load()

    return tiny_states, targets, ens


def batch_predict(model, states, batch_size=4096):
    """Run model inference on states."""
    all_m, all_v = [], []
    with torch.no_grad():
        for s in range(0, len(states), batch_size):
            x = torch.tensor(states[s:s + batch_size], dtype=torch.float32)
            m, lv = model(x)
            all_m.append(m.numpy())
            all_v.append(torch.exp(lv).numpy())
    return np.concatenate(all_m), np.concatenate(all_v)


def run_backtest(
    means: np.ndarray,
    stds: np.ndarray,
    prices: np.ndarray,
    snr_threshold: float,
    tp: float,
    sl: float,
    max_hold: int,
    temperature: float = 1.0,
) -> dict:
    """Run backtest with given parameters. Returns metrics dict."""
    total_means = np.sum(means, axis=1)
    total_vars = np.sum(stds * temperature, axis=1)
    snrs = np.abs(total_means) / (np.sqrt(total_vars) + 1e-10)

    equity = 10000.0
    pos = 0
    ep = 0.0
    es = 0
    trades = []
    wins = 0
    equity_curve = [equity]

    for i in range(len(prices) - 1):
        m = float(total_means[i])
        snr = float(snrs[i])
        cp = prices[i]

        if pos != 0:
            pp = (cp - ep) / ep * pos
            bars_held = i - es
            should_exit = (
                pp >= tp or pp <= -sl or bars_held >= max_hold or
                (pos == 1 and m < -0.0005) or (pos == -1 and m > 0.0005)
            )
            if should_exit:
                cost = abs(pp) * TOTAL_COST * 2
                pnl = equity * (pp - cost)
                equity += pnl
                trades.append(pnl > 0)
                if pnl > 0:
                    wins += 1
                pos = 0

        if pos == 0 and snr > snr_threshold:
            d = 1 if m > 0 else (-1 if m < 0 else 0)
            if d != 0:
                pos = d
                ep = cp
                es = i

        equity_curve.append(equity)

    # Metrics
    n = len(trades)
    wr = wins / max(n, 1)
    ret = (equity / 10000 - 1) * 100

    eq = np.array(equity_curve)
    rets = np.diff(eq) / np.maximum(eq[:-1], 1)
    sharpe = float(np.mean(rets) / (np.std(rets) + 1e-10) * np.sqrt(3628800)) if len(rets) > 10 else 0

    peak = np.maximum.accumulate(eq)
    max_dd = float(np.max((peak - eq) / np.maximum(peak, 1)))

    return {
        "return": ret,
        "sharpe": sharpe,
        "trades": n,
        "win_rate": wr,
        "max_dd": max_dd,
    }


def optimize_on_window(
    ens: VOL10Ensemble,
    train_states: np.ndarray,
    train_targets: np.ndarray,
) -> dict:
    """Find best parameters on training window."""
    # Predict on training data
    tiny_m, tiny_v = batch_predict(ens.tiny_model, train_states)
    wide_m, wide_v = batch_predict(ens.wide_model, train_states)
    means = 0.5 * tiny_m + 0.5 * wide_m
    stds = 0.5 * tiny_v + 0.5 * wide_v

    prices = 10000 * np.exp(np.cumsum(train_targets[:, 0]))

    # Parameter grid (reduced for speed)
    snr_thresholds = [0.20, 0.25, 0.30, 0.40]
    tps = [0.0020, 0.0030, 0.0050]
    sls = [0.0010, 0.0015, 0.0020]
    holds = [30, 60]

    best_sharpe = -999
    best_params = {}

    for snr, tp, sl, hold in itertools.product(snr_thresholds, tps, sls, holds):
        r = run_backtest(means, stds, prices, snr, tp, sl, hold)
        # Score: Sharpe, but penalize if too few trades
        score = r["sharpe"] if r["trades"] >= 10 else -999
        if score > best_sharpe:
            best_sharpe = score
            best_params = {"snr": snr, "tp": tp, "sl": sl, "hold": hold, "sharpe": r["sharpe"], "return": r["return"], "trades": r["trades"]}

    return best_params


def walk_forward(
    states: np.ndarray,
    targets: np.ndarray,
    ens: VOL10Ensemble,
    train_bars: int = 90000,
    test_bars: int = 30000,
) -> list[WindowResult]:
    """Run walk-forward optimization."""
    n = len(states)
    results = []
    window_id = 0

    start = 0
    while start + train_bars + test_bars <= n:
        train_start = start
        train_end = start + train_bars
        test_start = train_end
        test_end = min(train_end + test_bars, n)

        logger.info("Window %d: train=%d-%d test=%d-%d", window_id, train_start, train_end, test_start, test_end)

        # Normalize on train only
        norm_mean = states[train_start:train_end].mean(axis=0)
        norm_std = states[train_start:train_end].std(axis=0) + 1e-8

        train_states = (states[train_start:train_end] - norm_mean) / norm_std
        test_states = (states[test_start:test_end] - norm_mean) / norm_std

        train_targets = targets[train_start:train_end]
        test_targets = targets[test_start:test_end]

        # Optimize on training window
        t0 = time.time()
        best = optimize_on_window(ens, train_states, train_targets)
        train_time = time.time() - t0

        # Test on out-of-sample window
        tiny_m, tiny_v = batch_predict(ens.tiny_model, test_states)
        wide_m, wide_v = batch_predict(ens.wide_model, test_states)
        test_means = 0.5 * tiny_m + 0.5 * wide_m
        test_stds = 0.5 * tiny_v + 0.5 * wide_v

        test_prices = 10000 * np.exp(np.cumsum(test_targets[:, 0]))

        test_result = run_backtest(
            test_means, test_stds, test_prices,
            snr_threshold=best["snr"],
            tp=best["tp"],
            sl=best["sl"],
            max_hold=best["hold"],
        )

        wr = WindowResult(
            window_id=window_id,
            train_start=train_start,
            train_end=train_end,
            test_start=test_start,
            test_end=test_end,
            best_snr=best["snr"],
            best_tp=best["tp"],
            best_sl=best["sl"],
            best_hold=best["hold"],
            train_return=best["return"],
            train_sharpe=best["sharpe"],
            train_trades=best["trades"],
            test_return=test_result["return"],
            test_sharpe=test_result["sharpe"],
            test_trades=test_result["trades"],
            test_win_rate=test_result["win_rate"],
            test_max_dd=test_result["max_dd"],
        )
        results.append(window_id)
        results[-1] = wr  # Oops, fix this

        logger.info(
            "  Window %d: train_sharpe=%.2f test_sharpe=%.2f test_return=%.2f%% params=(SNR=%.2f TP=%.3f%% SL=%.3f%% Hold=%d) [%.1fs]",
            window_id, best["sharpe"], test_result["sharpe"], test_result["return"],
            best["snr"], best["tp"] * 100, best["sl"] * 100, best["hold"], train_time,
        )

        window_id += 1
        start += test_bars  # Roll forward by test window

    return results


def print_report(results: list[WindowResult]):
    """Print walk-forward report."""
    print(f"\n{'='*90}")
    print(f"  WALK-FORWARD OPTIMIZATION — VOL10")
    print(f"  {len(results)} windows, rolling train=90K test=30K bars")
    print(f"{'='*90}")

    print(f"\n  {'Win':>4} {'SNR':>5} {'TP%':>6} {'SL%':>6} {'Hold':>5} | {'Train':>8} {'Sharpe':>7} {'Trades':>6} | {'Test':>8} {'Sharpe':>7} {'WinRate':>7} {'MaxDD':>6} {'Trades':>6}")
    print(f"  {'-'*86}")

    test_returns = []
    test_sharpes = []
    test_trades = []

    for r in results:
        test_returns.append(r.test_return)
        test_sharpes.append(r.test_sharpe)
        test_trades.append(r.test_trades)

        marker = " +" if r.test_return > 0 else " -"
        print(
            f"  {r.window_id:>4} {r.best_snr:>5.2f} {r.best_tp*100:>5.2f}% {r.best_sl*100:>5.2f}% {r.best_hold:>5} | "
            f"{r.train_return:>+7.2f}% {r.train_sharpe:>7.2f} {r.train_trades:>6} | "
            f"{r.test_return:>+7.2f}% {r.test_sharpe:>7.2f} {r.test_win_rate:>6.0%} {r.test_max_dd:>5.1%} {r.test_trades:>6}{marker}"
        )

    # Aggregate
    print(f"  {'-'*86}")

    avg_test_return = np.mean(test_returns)
    avg_test_sharpe = np.mean(test_sharpes)
    total_test_trades = sum(test_trades)
    pct_profitable = sum(1 for r in test_returns if r > 0) / len(test_returns) * 100

    # Most common params
    all_snrs = [r.best_snr for r in results]
    all_tps = [r.best_tp for r in results]
    all_sls = [r.best_sl for r in results]
    all_holds = [r.best_hold for r in results]

    from collections import Counter
    most_common_snr = Counter(all_snrs).most_common(1)[0]
    most_common_tp = Counter(all_tps).most_common(1)[0]
    most_common_sl = Counter(all_sls).most_common(1)[0]
    most_common_hold = Counter(all_holds).most_common(1)[0]

    print(f"\n  AGGREGATE RESULTS:")
    print(f"    Avg test return:     {avg_test_return:+.2f}%")
    print(f"    Avg test Sharpe:     {avg_test_sharpe:.2f}")
    print(f"    Total test trades:   {total_test_trades}")
    print(f"    % Profitable windows:{pct_profitable:.0f}%")

    print(f"\n  MOST COMMON OPTIMAL PARAMS:")
    print(f"    SNR threshold:  {most_common_snr[0]:.2f} (appeared {most_common_snr[1]}/{len(results)} windows)")
    print(f"    Take profit:    {most_common_tp[0]*100:.2f}% (appeared {most_common_tp[1]}/{len(results)} windows)")
    print(f"    Stop loss:      {most_common_sl[0]*100:.2f}% (appeared {most_common_sl[1]}/{len(results)} windows)")
    print(f"    Max hold:       {most_common_hold[0]} bars (appeared {most_common_hold[1]}/{len(results)} windows)")

    # Robustness check: do params stay stable?
    snr_std = np.std(all_snrs)
    tp_std = np.std(all_tps)
    sl_std = np.std(all_sls)

    print(f"\n  PARAMETER STABILITY:")
    print(f"    SNR std:        {snr_std:.3f} {'(stable)' if snr_std < 0.05 else '(unstable)'}")
    print(f"    TP std:         {tp_std*100:.3f}% {'(stable)' if tp_std < 0.001 else '(unstable)'}")
    print(f"    SL std:         {sl_std*100:.3f}% {'(stable)' if sl_std < 0.0005 else '(unstable)'}")

    # Verdict
    print(f"\n  {'='*90}")
    if pct_profitable >= 75 and avg_test_sharpe > 1.0:
        print(f"  VERDICT: ROBUST — profitable in {pct_profitable:.0f}% of windows, Sharpe {avg_test_sharpe:.2f}")
        print(f"  Recommended params: SNR={most_common_snr[0]:.2f}, TP={most_common_tp[0]*100:.2f}%, SL={most_common_sl[0]*100:.2f}%, Hold={most_common_hold[0]}")
    elif pct_profitable >= 50:
        print(f"  VERDICT: CONDITIONAL — profitable in {pct_profitable:.0f}% of windows")
        print(f"  Consider using the most common params or adding regime filter")
    else:
        print(f"  VERDICT: WEAK — only profitable in {pct_profitable:.0f}% of windows")
        print(f"  Strategy needs improvement before live deployment")
    print(f"{'='*90}\n")

    return {
        "avg_test_return": avg_test_return,
        "avg_test_sharpe": avg_test_sharpe,
        "pct_profitable": pct_profitable,
        "recommended": {
            "snr": most_common_snr[0],
            "tp": most_common_tp[0],
            "sl": most_common_sl[0],
            "hold": most_common_hold[0],
        },
    }


def main():
    parser = argparse.ArgumentParser(description="Walk-forward optimization")
    parser.add_argument("--train-bars", type=int, default=90000, help="Training window size")
    parser.add_argument("--test-bars", type=int, default=30000, help="Test window size")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    print("Loading data and models...")
    states, targets, ens = load_model_and_data()
    print(f"  Data: {len(states)} bars")
    print(f"  Train window: {args.train_bars} bars (~{args.train_bars/60/24:.0f} days)")
    print(f"  Test window: {args.test_bars} bars (~{args.test_bars/60/24:.0f} days)")

    n_windows = (len(states) - args.train_bars) // args.test_bars
    print(f"  Estimated windows: {n_windows}")
    print()

    results = walk_forward(states, targets, ens, args.train_bars, args.test_bars)
    report = print_report(results)

    # Save results
    output = Path("checkpoints/walk_forward_results.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w") as f:
        json.dump({
            "n_windows": len(results),
            "report": report,
            "windows": [{
                "id": r.window_id,
                "best_snr": r.best_snr,
                "best_tp": r.best_tp,
                "best_sl": r.best_sl,
                "best_hold": r.best_hold,
                "train_return": r.train_return,
                "test_return": r.test_return,
                "test_sharpe": r.test_sharpe,
                "test_win_rate": r.test_win_rate,
            } for r in results],
        }, f, indent=2)
    print(f"Results saved to {output}")


if __name__ == "__main__":
    main()
