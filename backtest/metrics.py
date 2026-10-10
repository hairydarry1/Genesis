"""
backtest/metrics.py — Full metrics suite for backtest results (brief section 4).

Beyond Sharpe/DSR (already covered by eval/dsr.py), computes:
  - Max drawdown AND drawdown duration (bars/time spent underwater)
  - Win rate, profit factor
  - Calmar ratio (annualized return relative to max drawdown)
  - Per-volatility-regime breakdown (same calm/choppy/spike classification
    used by the world-model milestone in model/train_tiny.py)
  - Confidence calibration across the full simulated pipeline

Usage:
    python -m backtest.metrics --trade-log backtest_results/trade_log_R75.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
# Drawdown analytics
# ---------------------------------------------------------------------------

def drawdown_series(equity: np.ndarray) -> np.ndarray:
    """Fractional drawdown from running peak at each point (0 = at peak)."""
    eq = np.asarray(equity, dtype=np.float64)
    peak = np.maximum.accumulate(eq)
    return (peak - eq) / peak


def drawdown_stats(
    equity: np.ndarray,
    timestamps: np.ndarray | None = None,
) -> dict:
    """
    Max drawdown depth and duration.

    Duration is measured in bars (and in wall-clock time if timestamps given):
    how long the curve stayed below its previous peak, peak-to-recovery.
    """
    eq = np.asarray(equity, dtype=np.float64)
    dd = drawdown_series(eq)

    # Longest underwater episode: longest span between successive new peaks
    peaks = np.concatenate([[0], np.flatnonzero(np.diff(eq) >= 0) + 1])
    # Walk explicitly for robustness (handles flat segments correctly)
    longest_bars = 0
    peak_idx = 0
    for i in range(len(eq)):
        if eq[i] >= eq[peak_idx]:
            peak_idx = i
        else:
            longest_bars = max(longest_bars, i - peak_idx)

    out = {
        "max_drawdown_pct": float(dd.max() * 100),
        "max_drawdown_frac": float(dd.max()),
        "longest_underwater_bars": int(longest_bars),
        "final_drawdown_pct": float(dd[-1] * 100),
    }
    if timestamps is not None and len(timestamps) == len(eq):
        ts = np.asarray(timestamps)
        i_end = peak_idx + int(np.argmax(dd[peak_idx:])) if dd[peak_idx:].size else peak_idx
        out["max_dd_start"] = str(ts[peak_idx])
        out["max_dd_trough"] = str(ts[min(i_end, len(ts) - 1)])
    return out


# ---------------------------------------------------------------------------
# Trade-level statistics
# ---------------------------------------------------------------------------

def win_rate(trade_returns: list[float]) -> float | None:
    if not trade_returns:
        return None
    wins = sum(1 for r in trade_returns if r > 0)
    return float(wins / len(trade_returns))


def profit_factor(trade_returns: list[float]) -> float | None:
    """Gross profit / gross loss. >=1 means winners outweigh losers."""
    gains = sum(r for r in trade_returns if r > 0)
    losses = -sum(r for r in trade_returns if r < 0)
    if losses == 0:
        return float("inf") if gains > 0 else None
    return float(gains / losses)


def expectancy(trade_returns: list[float]) -> float | None:
    """Mean PnL per trade."""
    if not trade_returns:
        return None
    return float(np.mean(trade_returns))


# ---------------------------------------------------------------------------
# Return-based ratios
# ---------------------------------------------------------------------------

def sharpe_ratio(returns: np.ndarray, periods_per_year: float | None = None) -> float | None:
    r = np.asarray(returns, dtype=np.float64)
    if len(r) < 2 or np.std(r, ddof=1) == 0:
        return None
    sr = float(np.mean(r) / np.std(r, ddof=1))
    if periods_per_year:
        sr *= float(np.sqrt(periods_per_year))
    return sr


def calmar_ratio(equity: np.ndarray, periods_per_year: float | None = None) -> float | None:
    """Annualized total return / max drawdown. None if no drawdown or bad input."""
    eq = np.asarray(equity, dtype=np.float64)
    mdd = drawdown_series(eq).max()
    if mdd <= 0 or len(eq) < 2:
        return None
    total_return = eq[-1] / eq[0] - 1
    if periods_per_year:
        years = len(eq) / periods_per_year
        if years <= 0:
            return None
        ann_return = (eq[-1] / eq[0]) ** (1 / years) - 1
    else:
        ann_return = total_return
    return float(ann_return / mdd)


# ---------------------------------------------------------------------------
# Regime breakdown (same classification as model/train_tiny.py)
# ---------------------------------------------------------------------------

def classify_regimes(returns: np.ndarray) -> dict[str, np.ndarray]:
    """
    calm / choppy / spike masks on per-bar portfolio returns, using the same
    thresholds as the world-model milestone: |r| < 0.5*vol, < 1.5*vol, else.
    """
    r = np.asarray(returns, dtype=np.float64)
    vol = np.std(r)
    return {
        "calm": np.abs(r) < vol * 0.5,
        "choppy": (np.abs(r) >= vol * 0.5) & (np.abs(r) < vol * 1.5),
        "spike": np.abs(r) >= vol * 1.5,
    }


def regime_breakdown(equity: np.ndarray) -> dict[str, dict]:
    """Per-regime return, share of bars, and hit rate. Reported separately."""
    eq = np.asarray(equity, dtype=np.float64)
    returns = np.diff(eq) / eq[:-1]
    masks = classify_regimes(returns)
    out: dict[str, dict] = {}
    for name, mask in masks.items():
        n = int(mask.sum())
        if n < 5:
            continue
        rr = returns[mask]
        out[name] = {
            "n_bars": n,
            "share_pct": round(n / len(returns) * 100, 1),
            "total_return_pct": float((np.prod(1 + rr) - 1) * 100),
            "hit_rate": float((rr > 0).mean()),
            "sharpe": sharpe_ratio(rr),
        }
    return out


# ---------------------------------------------------------------------------
# Confidence calibration over the full pipeline
# ---------------------------------------------------------------------------

def calibration_stats(
    confidences: np.ndarray,
    corrects: np.ndarray,
    n_bins: int = 5,
) -> dict:
    """
    Does stated confidence match realized accuracy?

    Bins decisions by stated confidence and compares mean confidence vs
    empirical accuracy per bin, plus overall Brier-style gap.
    """
    c = np.asarray(confidences, dtype=np.float64)
    y = np.asarray(corrects, dtype=np.float64)
    ok = ~np.isnan(c) & ~np.isnan(y)
    c, y = c[ok], y[ok]
    if len(c) == 0:
        return {}

    edges = np.linspace(0, 1, n_bins + 1)
    bins = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        m = (c >= lo) & (c < hi) if i < n_bins - 1 else (c >= lo) & (c <= hi)
        if m.sum() == 0:
            continue
        bins.append({
            "range": [round(lo, 2), round(hi, 2)],
            "n": int(m.sum()),
            "mean_confidence": float(c[m].mean()),
            "accuracy": float(y[m].mean()),
            "gap": float(c[m].mean() - y[m].mean()),
        })

    return {
        "n_decisions": int(len(c)),
        "overall_accuracy": float(y.mean()),
        "overall_mean_confidence": float(c.mean()),
        "mean_gap": float(abs(c.mean() - y.mean())),
        "bins": bins,
    }


def realized_correctness(decisions: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """
    Derive per-decision correctness from an engine decision log:
    a decision was 'correct' when the executed direction made money
    (mark-to-market equity change in the traded direction).

    Flat decisions are excluded from the accuracy estimate (confidence kept
    as NaN so they drop out of calibration).
    """
    confs, corr = [], []
    for d in decisions:
        pos_moved = d.get("executed_action", 0) != 0
        if pos_moved and d["equity_after"] != d["equity_before"]:
            pnl = d["equity_after"] - d["equity_before"]
            confs.append(d["confidence"])
            corr.append(pnl > 0)
        else:
            confs.append(np.nan)
            corr.append(np.nan)
    return np.array(confs, dtype=np.float64), np.array(corr, dtype=np.float64)


# ---------------------------------------------------------------------------
# Aggregate report
# ---------------------------------------------------------------------------

def compute_metrics(
    equity_curve: np.ndarray,
    trade_returns: list[float] | None = None,
    periods_per_year: float | None = None,
    decisions: list[dict] | None = None,
) -> dict:
    """Full metrics suite from an equity curve and optional extras."""
    eq = np.asarray(equity_curve, dtype=np.float64)
    returns = np.diff(eq) / eq[:-1]

    out: dict = {
        "total_return_pct": float((eq[-1] / eq[0] - 1) * 100),
        **drawdown_stats(eq),
        "sharpe": sharpe_ratio(returns, periods_per_year),
        "calmar": calmar_ratio(eq, periods_per_year),
        "n_bars": int(len(eq) - 1),
    }
    if trade_returns is not None:
        out.update({
            "trades_closed": len(trade_returns),
            "win_rate": win_rate(trade_returns),
            "profit_factor": profit_factor(trade_returns),
            "expectancy": expectancy(trade_returns),
        })
    if decisions is not None:
        confs, corr = realized_correctness(decisions)
        out["calibration"] = calibration_stats(confs, corr)
    out["regime_breakdown"] = regime_breakdown(eq)
    return out


def summarize_backtest_result(result: dict) -> dict:
    """Convenience wrapper taking a BacktestEngine.run() output dict."""
    eq = [d["equity_before"] for d in result["decisions"]]
    eq.append(result["decisions"][-1]["equity_after"])
    return compute_metrics(
        equity_curve=np.array(eq),
        decisions=result["decisions"],
    )


# ---------------------------------------------------------------------------
# CLI: report on a saved engine trade log
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Full metrics suite for backtest logs")
    parser.add_argument("--trade-log", required=True, help="Engine trade_log_*.json")
    args = parser.parse_args()

    result = json.loads(Path(args.trade_log).read_text())
    m = summarize_backtest_result(result)

    print("=" * 60)
    print("FULL BACKTEST METRICS")
    print("=" * 60)
    print(f"Total return:     {m['total_return_pct']:+.2f}%")
    print(f"Max drawdown:     {m['max_drawdown_pct']:.2f}%")
    print(f"Longest underwater: {m['longest_underwater_bars']} bars")
    print(f"Sharpe:           {m['sharpe']}")
    print(f"Calmar:           {m['calmar']}")
    if "win_rate" in m:
        pf = m["profit_factor"]
        print(f"Trades: {m['trades_closed']}  win={m['win_rate']:.1%}  PF={pf:.2f}" if pf else f"Trades: {m['trades_closed']}")
    cal = m.get("calibration") or {}
    if cal:
        print(f"Calibration: acc={cal['overall_accuracy']:.3f} "
              f"conf={cal['overall_mean_confidence']:.3f} gap={cal['mean_gap']:.3f}")
    print("\nPer-regime:")
    for name, rr in m["regime_breakdown"].items():
        print(f"  {name:7s} n={rr['n_bars']:6d} ({rr['share_pct']:4.1f}%)  "
              f"ret={rr['total_return_pct']:+8.2f}%  hit={rr['hit_rate']:.1%}")


if __name__ == "__main__":
    main()
