"""
eval/backtest_compound.py — Dual strategy backtest WITH compounding.

Shows how equity grows when position sizes scale with account balance.
Includes equity curve, monthly breakdown, and Monte Carlo projections.
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks, compute_rsi,
)


def backtest_compound(symbol, equity=200.0, risk_pct=0.02, smc_share=0.6,
                      rsi_low=15.0, rsi_high=85.0, use_cluster_filter=True):
    """Run combined backtest WITH compounding."""
    dpp = 0.10 if "1000" in symbol else 0.20
    is_boom = "boom" in symbol.lower()

    m1 = load_candles(symbol, "1min")
    try:
        h1 = load_candles(symbol, "60min")
        zone_mult = 2.0 if not is_boom else 2.5
        zones = detect_order_blocks(h1, threshold_mult=zone_mult)
    except FileNotFoundError:
        zones = []

    bars_arr = np.array([[b['open'], b['high'], b['low'], b['close']] for b in m1], dtype=float)
    closes, highs, lows, opens = bars_arr[:, 3], bars_arr[:, 1], bars_arr[:, 2], bars_arr[:, 0]
    ranges = highs - lows
    med_r = float(np.median(ranges))
    spike_mask = ranges > 3.0 * med_r
    if is_boom:
        spike_mask &= closes > opens
    else:
        spike_mask &= closes < opens
    spikes = np.where(spike_mask)[0]
    spike_set = set(spikes.tolist())

    # Cluster detection
    spike_cluster = set()
    if use_cluster_filter:
        spikes_arr = np.array(spikes)
        for s in spikes_arr:
            nearby = int(np.sum(np.abs(spikes_arr - s) <= 10)) - 1
            if nearby >= 2:
                spike_cluster.add(int(s))

    # SMC
    smc_cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=risk_pct * smc_share,
        rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high,
    )
    smc = SMCSpikeHunter(symbol, config=smc_cfg, initial_equity=equity)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    # Drift state
    drift_entry_bar, drift_sl, drift_tp, drift_max = 3, 1.0, 3.0, 9
    drift_pos = 0
    drift_ep = 0.0
    drift_held = 0
    drift_ls = -999
    drift_psize = 0.0
    drift_ranges = []

    # Shared equity (COMPOUNDING — position sizes grow with equity)
    eq = equity
    peak = equity
    equity_curve = []
    all_pnls = []
    smc_pnls = []
    drift_pnls = []
    monthly_pnls = {}  # epoch_month → pnl

    for i, bar in enumerate(m1):
        h, l, c, o = bar['high'], bar['low'], bar['close'], bar['open']
        epoch = bar.get('epoch', 0)
        month = epoch // (30 * 86400)  # approximate month

        # ── Drift logic ──
        bar_range = h - l
        drift_ranges.append(bar_range)
        if len(drift_ranges) > 100:
            drift_ranges.pop(0)
        drift_med = float(np.median(drift_ranges))

        is_spike = bar_range > 3.0 * drift_med
        is_boom_spike = is_spike and c > o
        is_crash_spike = is_spike and c < o
        if (is_boom and is_boom_spike) or (not is_boom and is_crash_spike):
            drift_ls = i

        # Drift exit
        if drift_pos != 0:
            drift_held += 1
            if drift_pos == -1:
                hit_tp = (drift_ep - l) >= drift_tp
                hit_sl = (drift_ep - h) <= -drift_sl
                unreal = drift_ep - c
            else:
                hit_tp = (h - drift_ep) >= drift_tp
                hit_sl = (l - drift_ep) <= -drift_sl
                unreal = c - drift_ep

            ep = None
            if hit_tp: ep = drift_tp
            elif hit_sl: ep = -drift_sl
            elif i in spike_set: ep = unreal
            elif drift_held >= drift_max: ep = unreal

            if ep is not None:
                pnl = ep * dpp * drift_psize
                eq += pnl
                drift_pnls.append(pnl)
                all_pnls.append(pnl)
                monthly_pnls[month] = monthly_pnls.get(month, 0) + pnl
                drift_pos = 0
                drift_held = 0

        # Drift entry
        elif drift_pos == 0 and smc.position == 0:
            bs = i - drift_ls if drift_ls >= 0 else 999
            if bs == drift_entry_bar and drift_ls >= 0:
                skip = use_cluster_filter and drift_ls in spike_cluster
                if not skip:
                    drift_pos = -1 if is_boom else 1
                    drift_ep = c
                    drift_held = 0
                    # COMPOUNDING: risk based on CURRENT equity
                    risk_dollars = eq * risk_pct * (1 - smc_share)
                    drift_psize = risk_dollars / (drift_sl * dpp) if drift_sl * dpp > 0 else 0

        # ── SMC logic ──
        smc.equity = eq  # COMPOUNDING: use current equity
        smc.peak_equity = max(smc.peak_equity, eq)
        smc_result = smc.on_bar(bar, i)

        if smc_result and smc_result["action"] == "close":
            pnl = smc_result["pnl"]
            eq += pnl
            smc_pnls.append(pnl)
            all_pnls.append(pnl)
            monthly_pnls[month] = monthly_pnls.get(month, 0) + pnl

        peak = max(peak, eq)
        equity_curve.append(eq)

    # Close open positions
    if smc.position != 0:
        smc._close_position(m1[-1]['close'], "end_of_data", m1[-1]['close'])
        if smc.trade_log:
            pnl = smc.trade_log[-1]['pnl']
            eq += pnl
            smc_pnls.append(pnl)
            all_pnls.append(pnl)

    if drift_pos != 0:
        c = m1[-1]['close']
        unreal = (drift_ep - c) if drift_pos == -1 else (c - drift_ep)
        pnl = unreal * dpp * drift_psize
        eq += pnl
        drift_pnls.append(pnl)
        all_pnls.append(pnl)

    dd = ((peak - eq) / peak * 100) if peak > 0 else 0

    return {
        "symbol": symbol,
        "zones": len(zones),
        "initial_equity": equity,
        "final_equity": eq,
        "total_return_pct": (eq / equity - 1) * 100,
        "max_dd": dd,
        "smc": _stats(smc_pnls, "SMC"),
        "drift": _stats(drift_pnls, "Drift"),
        "combined": _stats(all_pnls, "Combined"),
        "all_pnls": all_pnls,
        "equity_curve": equity_curve,
        "monthly_pnls": monthly_pnls,
    }


def _stats(pnls, label):
    n = len(pnls)
    if n == 0:
        return {"strategy": label, "trades": 0, "total_pnl": 0, "pf": 0,
                "win_rate": 0, "avg_win": 0, "avg_loss": 0, "avg_rr": 0}
    w = [p for p in pnls if p > 0]
    l = [p for p in pnls if p <= 0]
    tw = sum(w); tl = abs(sum(l))
    return {
        "strategy": label, "trades": n,
        "wins": len(w), "losses": len(l),
        "win_rate": len(w) / n * 100,
        "total_pnl": sum(pnls),
        "pf": tw / tl if tl > 0 else 99,
        "avg_win": np.mean(w) if w else 0,
        "avg_loss": np.mean(l) if l else 0,
        "avg_rr": abs(np.mean(w) / np.mean(l)) if w and l and np.mean(l) != 0 else 99,
    }


def monte_carlo_compound(all_pnls, months=12, n_sims=1000, initial_equity=200.0):
    """Monte Carlo with compounding — position sizes scale with equity."""
    np.random.seed(42)
    pnls = np.array(all_pnls)
    if len(pnls) == 0:
        return {"median": initial_equity, "p5": initial_equity, "p95": initial_equity,
                "profit_pct": 0, "blowup_pct": 0}
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls)
    # Per-trade returns as fraction of equity at time of trade
    # Use FIXED fraction approach: each trade risks X% of current equity
    # So win = +X% * R:R, loss = -X%
    avg_win_pts = np.mean(wins) if len(wins) > 0 else 0
    avg_loss_pts = abs(np.mean(losses)) if len(losses) > 0 else 0
    # These are dollar PnL at $200 equity with risk_pct sizing
    # Normalize to per-trade return fraction
    avg_win_frac = avg_win_pts / initial_equity if initial_equity > 0 else 0.001
    avg_loss_frac = avg_loss_pts / initial_equity if initial_equity > 0 else 0.001
    # Cap per-trade returns to prevent overflow
    avg_win_frac = min(avg_win_frac, 0.05)   # Max 5% per trade
    avg_loss_frac = min(avg_loss_frac, 0.05)  # Max 5% per trade
    trades_per_month = len(pnls) / 30
    total_trades = int(trades_per_month * months)

    results = np.zeros(n_sims)
    for sim in range(n_sims):
        eq = initial_equity
        for _ in range(total_trades):
            if np.random.random() < wr:
                eq *= (1 + avg_win_frac)  # Compound win
            else:
                eq *= (1 - avg_loss_frac)  # Compound loss
            eq = max(eq, 0)
        results[sim] = eq

    return {
        "median": np.median(results), "p5": np.percentile(results, 5),
        "p95": np.percentile(results, 95),
        "blowup_pct": np.sum(results < initial_equity * 0.1) / n_sims * 100,
        "profit_pct": np.sum(results > initial_equity) / n_sims * 100,
        "median_return": (np.median(results) / initial_equity - 1) * 100,
        "p5_return": (np.percentile(results, 5) / initial_equity - 1) * 100,
        "p95_return": (np.percentile(results, 95) / initial_equity - 1) * 100,
    }


def main():
    print(f"\n{'='*80}")
    print(f"  COMPOUNDING BACKTEST — Dual Strategy (SMC + Drift Fade)")
    print(f"  Position sizes SCALE with equity (realistic growth)")
    print(f"{'='*80}")

    for risk_pct in [0.01, 0.02, 0.03]:
        print(f"\n{'─'*80}")
        print(f"  RISK: {risk_pct*100:.0f}% per trade (compounding)")
        print(f"{'─'*80}")

        for sym in ["BOOM500", "BOOM1000"]:
            r = backtest_compound(sym, equity=200.0, risk_pct=risk_pct, use_cluster_filter=True)
            c = r["combined"]

            print(f"\n  {sym}:")
            print(f"    Start: ${r['initial_equity']:.0f}  →  End: ${r['final_equity']:.2f}  "
                  f"({r['total_return_pct']:+.1f}%)")
            print(f"    Trades: {c['trades']}  |  WR: {c['win_rate']:.1f}%  |  PF: {c['pf']:.2f}")
            print(f"    Max DD: {r['max_dd']:.2f}%")

            # Monthly breakdown
            months = sorted(r["monthly_pnls"].keys())
            if months:
                print(f"    Monthly PnL:")
                for m in months[:6]:
                    print(f"      Month {m}: ${r['monthly_pnls'][m]:+.2f}")

            # Monte Carlo
            mc = monte_carlo_compound(r["all_pnls"], months=12, initial_equity=200.0)
            print(f"    12-Month Projection:")
            print(f"      Median: ${mc['median']:.2f} ({mc['median_return']:+.1f}%)")
            print(f"      P5:     ${mc['p5']:.2f} ({mc['p5_return']:+.1f}%)")
            print(f"      P95:    ${mc['p95']:.2f} ({mc['p95_return']:+.1f}%)")
            print(f"      Profit: {mc['profit_pct']:.0f}%  |  Blowup: {mc['blowup_pct']:.1f}%")

    # Growth projection table
    print(f"\n{'='*80}")
    print(f"  GROWTH PROJECTION — $200 start, BOOM500 + BOOM1000 combined")
    print(f"{'='*80}")
    print(f"\n  {'Risk':>6}  {'3-Month':>10}  {'6-Month':>10}  {'12-Month':>10}  {'24-Month':>10}")
    print(f"  {'─'*50}")

    for risk_pct in [0.01, 0.02, 0.03]:
        r500 = backtest_compound("BOOM500", equity=200.0, risk_pct=risk_pct, use_cluster_filter=True)
        r1000 = backtest_compound("BOOM1000", equity=200.0, risk_pct=risk_pct, use_cluster_filter=True)
        
        # Combine PnLs
        combined_pnls = r500["all_pnls"] + r1000["all_pnls"]
        tpm = len(combined_pnls) / 30  # trades per month
        
        # Simple compound projection
        wr = sum(1 for p in combined_pnls if p > 0) / max(len(combined_pnls), 1)
        avg_win = np.mean([p for p in combined_pnls if p > 0]) if any(p > 0 for p in combined_pnls) else 0
        avg_loss = abs(np.mean([p for p in combined_pnls if p <= 0])) if any(p <= 0 for p in combined_pnls) else 0
        # Cap per-trade returns to prevent overflow
        avg_win_frac = min(avg_win / 400, 0.05)
        avg_loss_frac = min(avg_loss / 400, 0.05)
        
        results = []
        for months in [3, 6, 12, 24]:
            eq = 400.0  # $200 per symbol
            trades = int(tpm * months)
            for _ in range(trades):
                if np.random.random() < wr:
                    eq *= (1 + avg_win_frac)
                else:
                    eq *= (1 - avg_loss_frac)
                eq = max(eq, 0)
            results.append(eq)
        
        print(f"  {risk_pct*100:.0f}%   ${results[0]:>9.2f}  ${results[1]:>9.2f}  ${results[2]:>9.2f}  ${results[3]:>9.2f}")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    main()
