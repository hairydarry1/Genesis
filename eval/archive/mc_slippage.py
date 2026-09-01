"""
eval/mc_slippage.py — Monte Carlo with slippage model for accurate projections.

Uses real trade data from SMC backtest with 0.011% entry slippage,
then resamples to project 6-12 month outcomes with compounding.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks
)


def get_trade_pnls(symbol: str, risk: float = 0.01, rsi_low: float = 10,
                   rsi_high: float = 90, slippage: float = 0.00011,
                   m1_cache=None, h1_cache=None, zones_cache=None) -> list:
    """Run backtest and return per-trade PnLs."""
    m1 = m1_cache if m1_cache else load_candles(symbol, "1min")
    h1 = h1_cache if h1_cache else load_candles(symbol, "60min")
    zones = zones_cache if zones_cache else detect_order_blocks(h1, threshold_mult=2.0 if "crash" in symbol.lower() else 2.5)

    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=risk,
        rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high,
        entry_slippage_pct=slippage,
    )
    strat = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    strat.set_h1_zones(zones)
    strat.precompute(m1)

    for i, bar in enumerate(m1):
        strat.on_bar(bar, i)

    return [t['pnl'] for t in strat.trade_log]


def monte_carlo(pnls: list, initial_equity: float = 10.0,
                n_sims: int = 500, days: int = 365,
                risk_per_trade: float = 0.01) -> dict:
    """Monte Carlo simulation with fractional compounding."""
    np.random.seed(42)

    trades_per_day = len(pnls) / 30.0  # trades from 30-day backtest
    total_trades = int(trades_per_day * days)
    if total_trades == 0:
        return {"median_return": 0, "mean_return": 0, "p5_return": 0, "p25_return": 0,
                "p75_return": 0, "p95_return": 0, "median_equity": initial_equity,
                "p5_equity": initial_equity, "p95_equity": initial_equity,
                "median_dd": 0, "p95_dd": 0, "prob_profit": 0, "prob_loss_5pct": 0,
                "prob_zero": 0, "min_seen": initial_equity, "initial": initial_equity,
                "days": days, "total_trades": 0, "trades_per_day": 0}

    # Scale PnLs to $10 account (backtest uses $10,000)
    scale = initial_equity / 10000.0
    scaled_pnls = [p * scale for p in pnls]

    final_equities = np.zeros(n_sims)
    max_drawdowns = np.zeros(n_sims)
    min_equities = np.zeros(n_sims)
    hit_zero = 0

    for sim in range(n_sims):
        eq = initial_equity
        peak = eq
        max_dd = 0
        min_eq = eq

        for _ in range(total_trades):
            pnl = np.random.choice(scaled_pnls)
            # Fractional compounding: risk X% of current equity
            eq += pnl * (eq / initial_equity)
            if eq <= 0:
                hit_zero += 1
                break
            peak = max(peak, eq)
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
            min_eq = min(min_eq, eq)

        final_equities[sim] = eq
        max_drawdowns[sim] = max_dd
        min_equities[sim] = min_eq

    # Statistics
    returns = (final_equities / initial_equity - 1) * 100

    return {
        "initial": initial_equity,
        "days": days,
        "total_trades": total_trades,
        "trades_per_day": trades_per_day,

        "median_return": float(np.median(returns)),
        "mean_return": float(np.mean(returns)),
        "p5_return": float(np.percentile(returns, 5)),
        "p25_return": float(np.percentile(returns, 25)),
        "p75_return": float(np.percentile(returns, 75)),
        "p95_return": float(np.percentile(returns, 95)),

        "median_equity": float(np.median(final_equities)),
        "p5_equity": float(np.percentile(final_equities, 5)),
        "p95_equity": float(np.percentile(final_equities, 95)),

        "median_dd": float(np.median(max_drawdowns) * 100),
        "p95_dd": float(np.percentile(max_drawdowns, 95) * 100),

        "prob_profit": float(np.mean(final_equities > initial_equity) * 100),
        "prob_loss_5pct": float(np.mean(final_equities < initial_equity * 0.95) * 100),
        "prob_zero": float(hit_zero / n_sims * 100),

        "min_seen": float(np.min(min_equities)),
    }


def print_results(symbol: str, mc6: dict, mc12: dict):
    """Pretty-print Monte Carlo results."""
    print(f"\n{'='*60}")
    print(f"  {symbol} — MONTE CARLO WITH SLIPPAGE (0.011%)")
    print(f"{'='*60}")
    print(f"  Trades/month: {mc6['trades_per_day']*30:.0f}")
    print(f"  Slippage:     0.011% per entry")

    for label, mc in [("6-MONTH", mc6), ("12-MONTH", mc12)]:
        print(f"\n  --- {label} ({mc['total_trades']} trades) ---")
        print(f"    Median Return:     {mc['median_return']:+.2f}% (${mc['median_equity']:.2f})")
        print(f"    Mean Return:       {mc['mean_return']:+.2f}%")
        print(f"    Worst (P5):        {mc['p5_return']:+.2f}% (${mc['p5_equity']:.2f})")
        print(f"    Best (P95):        {mc['p95_return']:+.2f}% (${mc['p95_equity']:.2f})")
        print(f"    Max DD (median):   {mc['median_dd']:.2f}%")
        print(f"    Max DD (P95):      {mc['p95_dd']:.2f}%")
        print(f"    Chance of Profit:  {mc['prob_profit']:.1f}%")
        print(f"    Chance >5% Loss:   {mc['prob_loss_5pct']:.1f}%")
        print(f"    Blew Up:           {mc['prob_zero']:.1f}%")
        print(f"    Lowest Seen:       ${mc['min_seen']:.2f}")


if __name__ == "__main__":
    print("\n" + "="*60)
    print("  MONTE CARLO — SMC SPIKE HUNTER WITH SLIPPAGE")
    print("="*60)

    # Pre-load data (shared across runs)
    print("\n  Loading data...")
    boom_m1 = load_candles("BOOM500", "1min")
    boom_h1 = load_candles("BOOM500", "60min")
    boom_zones = detect_order_blocks(boom_h1, threshold_mult=2.5)
    crash_m1 = load_candles("CRASH500", "1min")
    crash_h1 = load_candles("CRASH500", "60min")
    crash_zones = detect_order_blocks(crash_h1, threshold_mult=2.0)
    print(f"    BOOM500: {len(boom_m1)} M1 bars, {len(boom_zones)} zones")
    print(f"    CRASH500: {len(crash_m1)} M1 bars, {len(crash_zones)} zones")

    # BOOM500 — relaxed RSI (best config)
    print("\n  Collecting BOOM500 trades...")
    boom_pnls = get_trade_pnls("BOOM500", risk=0.01, rsi_low=15, rsi_high=90,
                               m1_cache=boom_m1, h1_cache=boom_h1, zones_cache=boom_zones)
    print(f"    Got {len(boom_pnls)} trades from 30-day backtest")
    boom_mc6 = monte_carlo(boom_pnls, initial_equity=10.0, days=180)
    boom_mc12 = monte_carlo(boom_pnls, initial_equity=10.0, days=365)
    print_results("BOOM500 (Relaxed RSI)", boom_mc6, boom_mc12)

    # CRASH500
    print("\n  Collecting CRASH500 trades...")
    crash_pnls = get_trade_pnls("CRASH500", risk=0.01, rsi_low=10, rsi_high=90,
                                m1_cache=crash_m1, h1_cache=crash_h1, zones_cache=crash_zones)
    print(f"    Got {len(crash_pnls)} trades from 30-day backtest")
    crash_mc6 = monte_carlo(crash_pnls, initial_equity=10.0, days=180)
    crash_mc12 = monte_carlo(crash_pnls, initial_equity=10.0, days=365)
    print_results("CRASH500", crash_mc6, crash_mc12)

    # Portfolio (50/50)
    print("\n  Building portfolio...")
    all_pnls = boom_pnls + crash_pnls  # interleaved
    port_mc6 = monte_carlo(all_pnls, initial_equity=10.0, days=180)
    port_mc12 = monte_carlo(all_pnls, initial_equity=10.0, days=365)
    print_results("PORTFOLIO (50% BOOM + 50% CRASH)", port_mc6, port_mc12)

    # Compare with/without slippage
    print("\n" + "="*60)
    print("  SLIPPAGE IMPACT COMPARISON")
    print("="*60)

    boom_pnls_noslip = get_trade_pnls("BOOM500", risk=0.01, rsi_low=15, rsi_high=90, slippage=0.0,
                                      m1_cache=boom_m1, h1_cache=boom_h1, zones_cache=boom_zones)
    mc_noslip = monte_carlo(boom_pnls_noslip, initial_equity=10.0, days=365)
    mc_withslip = boom_mc12

    print(f"\n  BOOM500 12-Month:")
    print(f"    {'':20s} {'No Slippage':>12s} {'With Slippage':>14s} {'Impact':>10s}")
    print(f"    {'Median Return':20s} {mc_noslip['median_return']:+11.2f}% {mc_withslip['median_return']:+13.2f}% {mc_withslip['median_return']-mc_noslip['median_return']:+9.2f}%")
    print(f"    {'Chance of Profit':20s} {mc_noslip['prob_profit']:11.1f}% {mc_withslip['prob_profit']:13.1f}% {mc_withslip['prob_profit']-mc_noslip['prob_profit']:+9.1f}%")
    print(f"    {'Max DD (median)':20s} {mc_noslip['median_dd']:11.2f}% {mc_withslip['median_dd']:13.2f}% {mc_withslip['median_dd']-mc_noslip['median_dd']:+9.2f}%")
