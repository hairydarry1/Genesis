"""
eval/mc_smc.py — Monte Carlo projection for the SMC Spike Hunter.

Takes actual trade results from the 30-day backtest, resamples them
to simulate 6-12 month forward performance.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_smc_trades(symbol: str, sl_pct=0.005, risk=0.01, rsi_low=10, rsi_high=90):
    """Run SMC backtest and return list of trade PnLs."""
    from eval.smc_spike_hunter import SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks

    h1 = load_candles(symbol, "60min")
    m1 = load_candles(symbol, "1min")
    zones = detect_order_blocks(h1)

    cfg = SMCHunterConfig(sl_pct=sl_pct, risk_per_trade=risk,
                           rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high)
    strat = SMCSpikeHunter(symbol, config=cfg)
    strat.set_h1_zones(zones)
    strat.precompute(m1)

    for i, bar in enumerate(m1):
        strat.on_bar(bar, i)
    if strat.position != 0:
        strat._close_position(m1[-1]['close'], "end", m1[-1]['close'])

    return [t['pnl'] for t in strat.trade_log]


def monte_carlo(trade_pnls: list, initial_equity: float = 10000.0,
                n_sims: int = 1000, days: int = 180) -> dict:
    """Run Monte Carlo simulation."""
    n_trades_30d = len(trade_pnls)
    if n_trades_30d == 0:
        return {"error": "No trades"}

    trades_per_day = n_trades_30d / 30
    total_trades = int(trades_per_day * days)

    avg_trade = np.mean(trade_pnls)
    win_trades = [p for p in trade_pnls if p > 0]
    loss_trades = [p for p in trade_pnls if p < 0]
    wr = len(win_trades) / n_trades_30d
    avg_win = np.mean(win_trades) if win_trades else 0
    avg_loss = np.mean(loss_trades) if loss_trades else 0

    final_equities = []
    max_drawdowns = []

    for _ in range(n_sims):
        equity = initial_equity
        peak = equity
        max_dd = 0

        trade_indices = np.random.randint(0, n_trades_30d, size=total_trades)
        for i in range(total_trades):
            pnl = trade_pnls[trade_indices[i]]
            equity += pnl
            equity = max(equity, 0)
            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

        final_equities.append(equity)
        max_drawdowns.append(max_dd)

    final_arr = np.array(final_equities)
    dd_arr = np.array(max_drawdowns)
    ret_arr = (final_arr / initial_equity - 1) * 100

    return {
        "days": days,
        "total_trades": total_trades,
        "trades_per_day": trades_per_day,
        "wr": wr,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr_ratio": abs(avg_win / avg_loss) if avg_loss != 0 else 0,
        "final_equity": {
            "mean": float(np.mean(final_arr)),
            "median": float(np.median(final_arr)),
            "p5": float(np.percentile(final_arr, 5)),
            "p25": float(np.percentile(final_arr, 25)),
            "p75": float(np.percentile(final_arr, 75)),
            "p95": float(np.percentile(final_arr, 95)),
        },
        "return_pct": {
            "mean": float(np.mean(ret_arr)),
            "median": float(np.median(ret_arr)),
            "p5": float(np.percentile(ret_arr, 5)),
            "p25": float(np.percentile(ret_arr, 25)),
            "p75": float(np.percentile(ret_arr, 75)),
            "p95": float(np.percentile(ret_arr, 95)),
        },
        "max_dd_pct": {
            "mean": float(np.mean(dd_arr) * 100),
            "median": float(np.median(dd_arr) * 100),
            "p5": float(np.percentile(dd_arr, 5) * 100),
            "p95": float(np.percentile(dd_arr, 95) * 100),
        },
        "prob_profit": float(np.mean(final_arr > initial_equity) * 100),
        "prob_loss_5pct": float(np.mean(ret_arr < -5) * 100),
        "prob_loss_10pct": float(np.mean(ret_arr < -10) * 100),
    }


def print_results(symbol: str, mc: dict, label: str = ""):
    f = mc["final_equity"]
    r = mc["return_pct"]
    d = mc["max_dd_pct"]

    print(f"\n{'=' * 72}")
    print(f"  {symbol} {label} — MONTE CARLO ({mc['days']} days, {mc['total_trades']} trades)")
    print(f"{'=' * 72}")
    print(f"  Trade stats:  {mc['trades_per_day']:.1f}/day  WR={mc['wr']*100:.1f}%  "
          f"R:R={mc['rr_ratio']:.2f}  AvgW={mc['avg_win']:+.6f}  AvgL={mc['avg_loss']:+.6f}")

    print(f"\n  FINAL EQUITY ($10,000 start):")
    print(f"    P5 (worst case):  ${f['p5']:,.0f}  ({r['p5']:+.1f}%)")
    print(f"    P25:              ${f['p25']:,.0f}  ({r['p25']:+.1f}%)")
    print(f"    Median:           ${f['median']:,.0f}  ({r['median']:+.1f}%)")
    print(f"    P75:              ${f['p75']:,.0f}  ({r['p75']:+.1f}%)")
    print(f"    P95 (best case):  ${f['p95']:,.0f}  ({r['p95']:+.1f}%)")

    print(f"\n  MAX DRAWDOWN:")
    print(f"    P5 (worst):   {d['p95']:.2f}%")
    print(f"    Median:       {d['median']:.2f}%")
    print(f"    P95 (best):   {d['p5']:.2f}%")

    print(f"\n  PROBABILITIES:")
    print(f"    Chance of profit:     {mc['prob_profit']:.1f}%")
    print(f"    Chance of >5% loss:   {mc['prob_loss_5pct']:.1f}%")
    print(f"    Chance of >10% loss:  {mc['prob_loss_10pct']:.1f}%")


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  MONTE CARLO — SMC SPIKE HUNTER")
    print("=" * 72)

    configs = [
        ("BOOM500", 0.005, 0.01, 10, 90, "Baseline (1% risk)"),
        ("BOOM500", 0.005, 0.02, 10, 90, "2% risk"),
        ("BOOM500", 0.004, 0.01, 10, 90, "Tighter SL (0.4%)"),
        ("BOOM500", 0.005, 0.01, 15, 85, "Relaxed RSI (15/85)"),
        ("CRASH500", 0.005, 0.01, 90, 10, "Baseline (1% risk)"),
        ("CRASH500", 0.005, 0.02, 90, 10, "2% risk"),
    ]

    for sym, sl, risk, rsi_l, rsi_h, label in configs:
        print(f"\n  Fetching trades for {sym} ({label})...")
        trades = get_smc_trades(sym, sl_pct=sl, risk=risk, rsi_low=rsi_l, rsi_high=rsi_h)
        print(f"  Got {len(trades)} trades from 30-day backtest")
        if len(trades) == 0:
            print("  No trades — skipping")
            continue

        # 6-month
        mc_6m = monte_carlo(trades, days=180, n_sims=1000)
        print_results(sym, mc_6m, label=f"6-Month ({label})")

        # 12-month
        mc_12m = monte_carlo(trades, days=365, n_sims=1000)
        print_results(sym, mc_12m, label=f"12-Month ({label})")

    # Portfolio: BOOM500 + CRASH500 combined
    print(f"\n  Running portfolio simulation (BOOM500 + CRASH500)...")
    trades_b = get_smc_trades("BOOM500", sl_pct=0.005, risk=0.01, rsi_low=10, rsi_high=90)
    trades_c = get_smc_trades("CRASH500", sl_pct=0.005, risk=0.01, rsi_low=90, rsi_high=10)

    all_trades = []
    max_len = max(len(trades_b), len(trades_c))
    for i in range(max_len):
        if i < len(trades_b):
            all_trades.append(trades_b[i] * 0.5)
        if i < len(trades_c):
            all_trades.append(trades_c[i] * 0.5)

    mc_port_6m = monte_carlo(all_trades, days=180, n_sims=1000)
    print_results("PORTFOLIO", mc_port_6m, label="6-Month (BOOM500 50% + CRASH500 50%)")

    mc_port_12m = monte_carlo(all_trades, days=365, n_sims=1000)
    print_results("PORTFOLIO", mc_port_12m, label="12-Month (BOOM500 50% + CRASH500 50%)")
