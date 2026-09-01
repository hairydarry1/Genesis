"""
eval/mc_final.py — Final Monte Carlo for $10 account.

Uses actual trade PnLs from backtest, simulates min lot constraints,
and projects 6-12 month returns.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def get_trades(symbol, sl_pct, risk, rsi_low, rsi_high, zone_mult):
    from eval.smc_spike_hunter import SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks

    h1 = load_candles(symbol, "60min")
    m1 = load_candles(symbol, "1min")
    zones = detect_order_blocks(h1, threshold_mult=zone_mult)

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


def monte_carlo_10k(trade_pnls, initial_equity=10.0, n_sims=2000, days=365):
    """Monte Carlo for $10 account with min lot constraint."""
    n_30d = len(trade_pnls)
    if n_30d == 0:
        return None

    trades_per_day = n_30d / 30
    total_trades = int(trades_per_day * days)

    # Rescale PnLs to $10 account (they were computed on $10000)
    scale = initial_equity / 10000.0
    pnls = [p * scale for p in trade_pnls]

    final_eq = []
    max_dds = []
    min_eqs = []

    for _ in range(n_sims):
        eq = initial_equity
        peak = eq
        max_dd = 0
        min_eq = eq

        indices = np.random.randint(0, n_30d, size=total_trades)
        for i in range(total_trades):
            eq += pnls[indices[i]]
            eq = max(eq, 0.01)  # can't go below $0.01
            peak = max(peak, eq)
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
            min_eq = min(min_eq, eq)

        final_eq.append(eq)
        max_dds.append(max_dd)
        min_eqs.append(min_eq)

    fa = np.array(final_eq)
    da = np.array(max_dds)
    ma = np.array(min_eqs)
    ra = (fa / initial_equity - 1) * 100

    return {
        "days": days,
        "trades": total_trades,
        "eq": {"p5": float(np.percentile(fa, 5)), "p25": float(np.percentile(fa, 25)),
               "median": float(np.median(fa)), "p75": float(np.percentile(fa, 75)),
               "p95": float(np.percentile(fa, 95)), "mean": float(np.mean(fa))},
        "ret": {"p5": float(np.percentile(ra, 5)), "p25": float(np.percentile(ra, 25)),
                "median": float(np.median(ra)), "p75": float(np.percentile(ra, 75)),
                "p95": float(np.percentile(ra, 95))},
        "dd": {"p5": float(np.percentile(da, 95)*100), "median": float(np.median(da)*100),
               "p95": float(np.percentile(da, 5)*100)},
        "min_eq": {"p5": float(np.percentile(ma, 5)), "median": float(np.median(ma)),
                   "p95": float(np.percentile(ma, 95))},
        "prob_profit": float(np.mean(fa > initial_equity) * 100),
        "prob_blowup": float(np.mean(fa < 1.0) * 100),  # lost >90%
        "prob_loss_50": float(np.mean(ra < -50) * 100),
        "prob_loss_25": float(np.mean(ra < -25) * 100),
    }


def print_mc(label, mc):
    e = mc['eq']
    r = mc['ret']
    d = mc['dd']
    m = mc['min_eq']

    print(f"\n{'='*70}")
    print(f"  {label} — {mc['days']} days, {mc['trades']} trades")
    print(f"{'='*70}")
    print(f"  FINAL EQUITY ($10 start):")
    print(f"    P5 (worst):   ${e['p5']:.2f}  ({r['p5']:+.1f}%)")
    print(f"    P25:          ${e['p25']:.2f}  ({r['p25']:+.1f}%)")
    print(f"    Median:       ${e['median']:.2f}  ({r['median']:+.1f}%)")
    print(f"    P75:          ${e['p75']:.2f}  ({r['p75']:+.1f}%)")
    print(f"    P95 (best):   ${e['p95']:.2f}  ({r['p95']:+.1f}%)")
    print(f"\n  WORST DRAWDOWN:")
    print(f"    P5 (worst):   {d['p5']:.1f}%")
    print(f"    Median:       {d['median']:.1f}%")
    print(f"    P95 (best):   {d['p95']:.1f}%")
    print(f"\n  LOWEST EQUITY SEEN:")
    print(f"    P5 (worst):   ${m['p5']:.2f}")
    print(f"    Median:       ${m['median']:.2f}")
    print(f"    P95 (best):   ${m['p95']:.2f}")
    print(f"\n  PROBABILITIES:")
    print(f"    Profit:           {mc['prob_profit']:.1f}%")
    print(f"    Lost >50%:        {mc['prob_loss_50']:.1f}%")
    print(f"    Lost >25%:        {mc['prob_loss_25']:.1f}%")
    print(f"    Blew up (<$1):    {mc['prob_blowup']:.1f}%")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  FINAL MONTE CARLO — $10 ACCOUNT (min lot 0.01)")
    print("=" * 70)

    configs = [
        ("BOOM500", 0.005, 0.01, 15, 85, 2.5, "Relaxed RSI (best)"),
        ("BOOM500", 0.005, 0.01, 10, 90, 2.5, "Strict RSI"),
        ("CRASH500", 0.005, 0.01, 90, 90, 2.0, "Optimized"),
    ]

    for sym, sl, risk, rsi_l, rsi_h, zm, label in configs:
        print(f"\n  Running {sym} ({label})...")
        trades = get_trades(sym, sl, risk, rsi_l, rsi_h, zm)
        print(f"  Got {len(trades)} trades")
        if len(trades) == 0:
            continue

        # 6-month
        mc6 = monte_carlo_10k(trades, initial_equity=10.0, days=180, n_sims=2000)
        print_mc(f"{sym} 6-Month ({label})", mc6)

        # 12-month
        mc12 = monte_carlo_10k(trades, initial_equity=10.0, days=365, n_sims=2000)
        print_mc(f"{sym} 12-Month ({label})", mc12)

    # Portfolio: both symbols
    print(f"\n  Running portfolio (BOOM500 + CRASH500)...")
    trades_b = get_trades("BOOM500", 0.005, 0.01, 15, 85, 2.5)
    trades_c = get_trades("CRASH500", 0.005, 0.01, 90, 90, 2.0)

    # Split $10 into $5 each
    scale_b = [p * 0.5 for p in trades_b]
    scale_c = [p * 0.5 for p in trades_c]
    all_trades = scale_b + scale_c

    mc_port_6m = monte_carlo_10k(all_trades, initial_equity=10.0, days=180, n_sims=2000)
    print_mc("PORTFOLIO 6-Month ($5 BOOM + $5 CRASH)", mc_port_6m)

    mc_port_12m = monte_carlo_10k(all_trades, initial_equity=10.0, days=365, n_sims=2000)
    print_mc("PORTFOLIO 12-Month ($5 BOOM + $5 CRASH)", mc_port_12m)
