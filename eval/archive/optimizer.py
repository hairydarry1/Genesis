"""
eval/optimizer.py — Parameter Optimizer for Spike Fade Strategy.

Grid searches over TP/SL/hold/risk to find optimal settings per symbol.
Uses backtest results to score combinations by profit factor, return, and drawdown.

Usage:
    python -m eval.optimizer                       # Optimize all symbols
    python -m eval.optimizer --symbol BOOM500      # Single symbol
    python -m eval.optimizer --metric pf           # Optimize for profit factor
"""

import argparse
import io
import itertools
import sys
import time
from pathlib import Path
from dataclasses import dataclass

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.paper_trading_v2 import SpikeFadeEngine, SPIKE_FADE_CONFIGS, SPIKE_DIR_MAP


@dataclass
class OptResult:
    """Result of a single parameter combination."""
    tp_pct: float
    sl_pct: float
    max_hold: int
    risk_pct: float
    trades: int
    win_rate: float
    profit_factor: float
    total_return_pct: float
    max_drawdown_pct: float
    avg_trade_pnl: float
    sharpe: float
    score: float  # composite score


def df_to_candles(df: pd.DataFrame) -> list[dict]:
    """Pre-convert DataFrame to list of dicts using numpy (10x faster than iterrows)."""
    epochs = df["epoch"].values.astype(int) if "epoch" in df.columns else np.zeros(len(df), dtype=int)
    opens = df["open"].values
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    return [
        {"epoch": int(epochs[i]), "open": float(opens[i]), "high": float(highs[i]),
         "low": float(lows[i]), "close": float(closes[i]), "volume": 1}
        for i in range(len(df))
    ]


def backtest_params(symbol: str, candles: list[dict], tp: float, sl: float,
                    hold: int, risk: float, spike_mult: float = 3.0) -> dict:
    """Run backtest with specific parameters. candles should be pre-converted."""

    # Create engine with custom config
    config = {
        "spike_mult": spike_mult,
        "cluster_gap": 5,
        "large_spike_pct": 0.0016,
        "entry_delay": 1,
        "tp_pct": tp,
        "sl_pct": sl,
        "max_hold": hold,
    }

    eng = SpikeFadeEngine(symbol, config=config, risk_pct=risk)
    eng.use_slippage = True
    eng.use_vol_regime = False  # Disable for clean optimization
    eng.use_auto_tuner = False
    eng.use_session_filter = False
    eng.use_anomaly_detector = False  # Disable during optimization
    # Disable adaptive thinker (live-learning feature blocks too aggressively in backtest)
    eng.thinker.max_consecutive_losses = 999
    eng.thinker.min_conviction = 0.0
    eng.thinker.drift_filter = 999
    eng.thinker.spike_size_filter = 999
    eng.thinker.vol_filter = None
    # Suppress all print output during optimization (anomaly detector, etc.)
    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        for candle in candles:
            eng.on_bar(candle)
    finally:
        sys.stdout = old_stdout

    # Collect results
    closes = [t for t in eng.trade_log if t.get("action") == "close"]
    if not closes:
        return {"trades": 0, "win_rate": 0, "profit_factor": 0,
                "return_pct": 0, "max_dd_pct": 0, "avg_pnl": 0, "sharpe": 0}

    pnls = [t["pnl"] for t in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    n_trades = len(closes)
    win_rate = len(wins) / n_trades if n_trades > 0 else 0
    total_win = sum(wins)
    total_loss = abs(sum(losses))
    profit_factor = total_win / total_loss if total_loss > 0 else 99
    return_pct = (eng.equity / 10000 - 1) * 100
    max_dd = max((p["dd"] for p in eng.equity_curve), default=0) * 100
    avg_pnl = np.mean(pnls) if pnls else 0

    # Sharpe ratio (annualized, approximate)
    if len(pnls) > 1:
        pnl_std = np.std(pnls)
        sharpe = (np.mean(pnls) / pnl_std * np.sqrt(252)) if pnl_std > 0 else 0
    else:
        sharpe = 0

    return {
        "trades": n_trades,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "return_pct": return_pct,
        "max_dd_pct": max_dd,
        "avg_pnl": avg_pnl,
        "sharpe": sharpe,
        "equity": eng.equity,
    }


def compute_score(result: dict, metric: str) -> float:
    """Compute composite score for ranking."""
    if result["trades"] < 5:
        return -999  # Need minimum trades

    if metric == "return":
        return result["return_pct"]
    elif metric == "pf":
        return result["profit_factor"]
    elif metric == "sharpe":
        return result["sharpe"]
    elif metric == "composite":
        # Weighted composite: 40% return, 30% PF, 20% Sharpe, 10% low DD
        pf_score = min(result["profit_factor"], 10)  # Cap at 10
        dd_penalty = max(0, 1 - result["max_dd_pct"] / 5)  # Penalize >5% DD
        return (result["return_pct"] * 0.4 +
                pf_score * 3 * 0.3 +
                result["sharpe"] * 2 * 0.2 +
                dd_penalty * 10 * 0.1)
    else:
        return result["return_pct"]


def optimize_symbol(symbol: str, df: pd.DataFrame, metric: str,
                    grid: dict = None) -> list[OptResult]:
    """Grid search all parameter combinations for a symbol."""
    if grid is None:
        grid = {
            "tp": [0.005, 0.008],
            "sl": [0.0005, 0.001],
            "hold": [8, 12],
            "risk": [0.10],
            "spike_mult": [2.0, 3.0],
        }

    combos = list(itertools.product(
        grid["tp"], grid["sl"], grid["hold"], grid["risk"],
        grid.get("spike_mult", [3.0])
    ))

    print(f"\n  {symbol}: Testing {len(combos)} parameter combinations...")

    results = []
    start_time = time.time()

    # Pre-convert DataFrame once (huge speedup)
    candles_cache = df_to_candles(df)
    print(f"    Pre-converted {len(candles_cache):,} candles")

    for i, (tp, sl, hold, risk, spike_mult) in enumerate(combos):
        if sl >= tp:  # SL must be less than TP
            continue

        result = backtest_params(symbol, candles_cache, tp, sl, hold, risk, spike_mult)
        score = compute_score(result, metric)

        results.append(OptResult(
            tp_pct=tp, sl_pct=sl, max_hold=hold, risk_pct=risk,
            trades=result["trades"],
            win_rate=result["win_rate"],
            profit_factor=result["profit_factor"],
            total_return_pct=result["return_pct"],
            max_drawdown_pct=result["max_dd_pct"],
            avg_trade_pnl=result["avg_pnl"],
            sharpe=result["sharpe"],
            score=score,
        ))

        # Progress
        if (i + 1) % 50 == 0:
            elapsed = time.time() - start_time
            rate = (i + 1) / elapsed
            eta = (len(combos) - i - 1) / rate
            print(f"    [{i+1}/{len(combos)}] {elapsed:.0f}s elapsed, ~{eta:.0f}s remaining")

    elapsed = time.time() - start_time
    print(f"    Done in {elapsed:.1f}s")

    # Sort by score
    results.sort(key=lambda r: r.score, reverse=True)
    return results


def print_results(symbol: str, results: list[OptResult], top_n: int = 10):
    """Print top parameter combinations."""
    print(f"\n{'='*90}")
    print(f"  {symbol} — TOP {top_n} PARAMETER COMBINATIONS")
    print(f"{'='*90}")
    print(f"  {'Rank':>4}  {'TP%':>6}  {'SL%':>6}  {'Hold':>5}  {'Risk%':>6}  "
          f"{'Trades':>6}  {'WR%':>6}  {'PF':>6}  {'Return%':>8}  {'MaxDD%':>7}  {'Score':>7}")
    print(f"  {'-'*4}  {'-'*6}  {'-'*6}  {'-'*5}  {'-'*6}  "
          f"{'-'*6}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*7}  {'-'*7}")

    for i, r in enumerate(results[:top_n]):
        print(f"  {i+1:>4}  {r.tp_pct*100:>5.2f}%  {r.sl_pct*100:>5.3f}%  {r.max_hold:>5}  "
              f"{r.risk_pct*100:>5.1f}%  {r.trades:>6}  {r.win_rate*100:>5.1f}%  "
              f"{r.profit_factor:>6.2f}  {r.total_return_pct:>+7.2f}%  "
              f"{r.max_drawdown_pct:>6.3f}%  {r.score:>7.2f}")

    # Best config
    best = results[0]
    print(f"\n  BEST CONFIG:")
    print(f"    TP: {best.tp_pct*100:.2f}%  SL: {best.sl_pct*100:.3f}%  "
          f"Hold: {best.max_hold} bars  Risk: {best.risk_pct*100:.0f}%")
    print(f"    → {best.trades} trades, {best.win_rate*100:.1f}% WR, "
          f"PF={best.profit_factor:.2f}, Return={best.total_return_pct:+.2f}%, "
          f"MaxDD={best.max_drawdown_pct:.3f}%")

    return best


def main():
    parser = argparse.ArgumentParser(description="Optimize spike fade parameters")
    parser.add_argument("--symbol", default=None, help="Single symbol to optimize")
    parser.add_argument("--metric", default="composite", choices=["return", "pf", "sharpe", "composite"],
                        help="Optimization metric")
    parser.add_argument("--days", type=int, default=30, help="Days of data to use")
    parser.add_argument("--top", type=int, default=10, help="Show top N results")
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  SPIKE FADE PARAMETER OPTIMIZER")
    print(f"{'='*60}")
    print(f"  Metric: {args.metric}")

    symbols = [args.symbol] if args.symbol else ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000", "BOOM300", "CRASH300", "BOOM150", "CRASH150"]

    all_best = {}
    for sym in symbols:
        # Load data
        data_file = Path(__file__).parent.parent / "data" / f"candles_{sym.lower()}_1min.parquet"
        if not data_file.exists():
            print(f"\n  {sym}: No data file found at {data_file}")
            print(f"  Run: python data/download_history.py --symbol {sym} --days {args.days}")
            continue

        df = pd.read_parquet(data_file)
        print(f"\n  {sym}: {len(df):,} candles loaded")

        # Optimize
        results = optimize_symbol(sym, df, args.metric)
        best = print_results(sym, results, args.top)
        all_best[sym] = best

    # Summary
    if all_best:
        print(f"\n{'='*60}")
        print(f"  OPTIMIZATION SUMMARY")
        print(f"{'='*60}")
        for sym, best in all_best.items():
            print(f"\n  {sym}:")
            print(f"    TP={best.tp_pct*100:.2f}% SL={best.sl_pct*100:.3f}% "
                  f"Hold={best.max_hold} Risk={best.risk_pct*100:.0f}%")
            print(f"    → PF={best.profit_factor:.2f} Return={best.total_return_pct:+.2f}% "
                  f"MaxDD={best.max_drawdown_pct:.3f}%")

        # Write optimal configs
        print(f"\n  Copy these into SPIKE_FADE_CONFIGS in paper_trading_v2.py:")
        for sym, best in all_best.items():
            print(f'    "{sym}": {{"tp_pct": {best.tp_pct}, "sl_pct": {best.sl_pct}, "max_hold": {best.max_hold}}},')


if __name__ == "__main__":
    main()
