"""
eval/mc_mtf.py — Monte Carlo projection for the Multi-Timeframe strategy.

Takes actual trade results from the 30-day backtest, resamples them
to simulate 6-12 month forward performance. Runs 1000 simulations
to produce confidence intervals.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_trades(symbol: str, tp_pct=0.003, sl_pct=0.001, max_hold=15,
               spike_mult=3.0, ef=5, es=13, risk_pct=0.10):
    """Run MTF backtest and return list of trade PnLs."""
    import pandas as pd
    from eval.multitimeframe import MTFConfig, MultiTimeframeStrategy

    df_1m = pd.read_parquet(f"data/candles_{symbol.lower()}_1min.parquet")
    df_15m = pd.read_parquet(f"data/candles_{symbol.lower()}_15min.parquet")
    eps1 = df_1m['epoch'].values.astype(int)
    eps15 = df_15m['epoch'].values.astype(int)
    c1m = [{"epoch": int(eps1[i]), "open": float(df_1m['open'].values[i]),
             "high": float(df_1m['high'].values[i]), "low": float(df_1m['low'].values[i]),
             "close": float(df_1m['close'].values[i])} for i in range(len(df_1m))]
    c15m = [{"epoch": int(eps15[i]), "open": float(df_15m['open'].values[i]),
              "high": float(df_15m['high'].values[i]), "low": float(df_15m['low'].values[i]),
              "close": float(df_15m['close'].values[i])} for i in range(len(df_15m))]
    e2t = {b['epoch'] // 900: i for i, b in enumerate(c15m)}

    cfg = MTFConfig(ema_fast=ef, ema_slow=es, tp_pct=tp_pct, sl_pct=sl_pct,
                     max_hold=max_hold, spike_mult=spike_mult)
    strat = MultiTimeframeStrategy(symbol, config=cfg, risk_pct=risk_pct)
    last = -1
    for b in c1m:
        bucket = b['epoch'] // 900
        if bucket in e2t and e2t[bucket] != last:
            strat.update_15min(c15m[e2t[bucket]])
            last = e2t[bucket]
        strat.on_bar_1m(b)
    if strat.position != 0:
        strat._close_position(c1m[-1]['close'], 'end', c1m[-1]['close'])

    return [t['pnl'] for t in strat.trade_log]


def monte_carlo(trade_pnls: list, initial_equity: float = 10000.0,
                n_sims: int = 500, days: int = 180) -> dict:
    """
    Run Monte Carlo simulation.

    Takes 30-day trade results and projects forward `days` days.
    Assumes the same trade frequency and distribution.
    """
    n_trades_30d = len(trade_pnls)
    if n_trades_30d == 0:
        return {"error": "No trades"}

    # Scale trades to the target period
    trades_per_day = n_trades_30d / 30
    total_trades = int(trades_per_day * days)

    # Statistics
    avg_trade = np.mean(trade_pnls)
    win_trades = [p for p in trade_pnls if p > 0]
    loss_trades = [p for p in trade_pnls if p < 0]
    wr = len(win_trades) / n_trades_30d
    avg_win = np.mean(win_trades) if win_trades else 0
    avg_loss = np.mean(loss_trades) if loss_trades else 0

    # Run simulations
    final_equities = []
    max_drawdowns = []
    monthly_returns = []
    win_streaks = []
    loss_streaks = []

    for _ in range(n_sims):
        equity = initial_equity
        peak = equity
        max_dd = 0
        current_win_streak = 0
        current_loss_streak = 0
        best_win_streak = 0
        best_loss_streak = 0
        month_eq = [initial_equity]  # track equity at month boundaries

        # Pre-generate random trade indices for speed
        trade_indices = np.random.randint(0, n_trades_30d, size=total_trades)
        for i in range(total_trades):
            pnl = trade_pnls[trade_indices[i]]
            equity += pnl
            equity = max(equity, 0)  # can't go below 0

            peak = max(peak, equity)
            dd = (peak - equity) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)

            if pnl > 0:
                current_win_streak += 1
                current_loss_streak = 0
            else:
                current_loss_streak += 1
                current_win_streak = 0

            best_win_streak = max(best_win_streak, current_win_streak)
            best_loss_streak = max(best_loss_streak, current_loss_streak)

            # Track monthly equity (every ~30 days worth of trades)
            trades_per_month = int(trades_per_day * 30)
            if (i + 1) % trades_per_month == 0:
                month_eq.append(equity)

        final_equities.append(equity)
        max_drawdowns.append(max_dd)
        win_streaks.append(best_win_streak)
        loss_streaks.append(best_loss_streak)

        # Monthly returns
        for j in range(1, len(month_eq)):
            monthly_returns.append((month_eq[j] / month_eq[j - 1] - 1) * 100)

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
        "avg_win_streak": float(np.mean(win_streaks)),
        "avg_loss_streak": float(np.mean(loss_streaks)),
        "monthly_returns": monthly_returns,
    }


def print_results(symbol: str, mc: dict, label: str = ""):
    """Pretty-print Monte Carlo results."""
    f = mc["final_equity"]
    r = mc["return_pct"]
    d = mc["max_dd_pct"]

    print(f"\n{'=' * 72}")
    print(f"  {symbol} {label} — MONTE CARLO ({mc['days']} days, {mc['total_trades']} trades)")
    print(f"{'=' * 72}")
    print(f"  Trade stats:  {mc['trades_per_day']:.1f}/day  WR={mc['wr']*100:.1f}%  "
          f"R:R={mc['rr_ratio']:.2f}  AvgW={mc['avg_win']:+.4f}  AvgL={mc['avg_loss']:+.4f}")
    print(f"  Streaks:      Best win streak: {mc['avg_win_streak']:.1f}  "
          f"Best loss streak: {mc['avg_loss_streak']:.1f}")

    print(f"\n  FINAL EQUITY ($10,000 start):")
    print(f"    P5 (worst case):  ${f['p5']:,.0f}  ({r['p5']:+.1f}%)")
    print(f"    P25:              ${f['p25']:,.0f}  ({r['p25']:+.1f}%)")
    print(f"    Median:           ${f['median']:,.0f}  ({r['median']:+.1f}%)")
    print(f"    P75:              ${f['p75']:,.0f}  ({r['p75']:+.1f}%)")
    print(f"    P95 (best case):  ${f['p95']:,.0f}  ({r['p95']:+.1f}%)")

    print(f"\n  MAX DRAWDOWN:")
    print(f"    P5 (worst):   {d['p95']:.2f}%")  # p95 of DD = worst case
    print(f"    Median:       {d['median']:.2f}%")
    print(f"    P95 (best):   {d['p5']:.2f}%")   # p5 of DD = best case

    print(f"\n  PROBABILITIES:")
    print(f"    Chance of profit:     {mc['prob_profit']:.1f}%")
    print(f"    Chance of >5% loss:   {mc['prob_loss_5pct']:.1f}%")
    print(f"    Chance of >10% loss:  {mc['prob_loss_10pct']:.1f}%")


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  MONTE CARLO PROJECTION — MTF STRATEGY")
    print("  Based on 30-day backtest, projected forward")
    print("=" * 72)

    # Best configs for each symbol
    best_configs = [
        ("BOOM500", 0.0025, 0.0008, 12, 3.0, 5, 13, "TP=0.25% SL=0.08%"),
        ("CRASH500", 0.0035, 0.0012, 15, 3.0, 5, 13, "TP=0.35% SL=0.12%"),
    ]

    for sym, tp, sl, hold, sm, ef, es, label in best_configs:
        print(f"\n  Fetching trades for {sym}...")
        trades = get_trades(sym, tp_pct=tp, sl_pct=sl, max_hold=hold,
                            spike_mult=sm, ef=ef, es=es)
        print(f"  Got {len(trades)} trades from 30-day backtest")

        # 6-month projection
        mc_6m = monte_carlo(trades, days=180, n_sims=1000)
        print_results(sym, mc_6m, label=f"6-Month ({label})")

        # 12-month projection
        mc_12m = monte_carlo(trades, days=365, n_sims=1000)
        print_results(sym, mc_12m, label=f"12-Month ({label})")

    # Portfolio: both symbols simultaneously
    print(f"\n  Running portfolio simulation (BOOM500 + CRASH500)...")
    trades_b = get_trades("BOOM500", tp_pct=0.0025, sl_pct=0.0008, max_hold=12,
                           spike_mult=3.0, ef=5, es=13)
    trades_c = get_trades("CRASH500", tp_pct=0.0035, sl_pct=0.0012, max_hold=15,
                           spike_mult=3.0, ef=5, es=13)

    # Combine trades (interleaved)
    all_trades = []
    max_len = max(len(trades_b), len(trades_c))
    for i in range(max_len):
        if i < len(trades_b):
            all_trades.append(trades_b[i] * 0.5)  # 50% allocation each
        if i < len(trades_c):
            all_trades.append(trades_c[i] * 0.5)

    mc_port_6m = monte_carlo(all_trades, days=180, n_sims=1000)
    print_results("PORTFOLIO", mc_port_6m, label="6-Month (BOOM500 50% + CRASH500 50%)")

    mc_port_12m = monte_carlo(all_trades, days=365, n_sims=1000)
    print_results("PORTFOLIO", mc_port_12m, label="12-Month (BOOM500 50% + CRASH500 50%)")
