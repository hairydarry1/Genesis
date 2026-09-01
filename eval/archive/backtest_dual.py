"""
eval/backtest_dual.py — Backtest dual strategy (SMC + Drift Fade) on historical data.

Uses FIXED dollar risk per trade (no compounding) — realistic for small accounts
where minimum lot sizes constrain position sizing.
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks, OrderBlock, compute_rsi,
)


# ─── Drift Fade Backtest ─────────────────────────────────────────────────────

def backtest_drift(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction, dpp):
    """Standalone drift fade backtest with fixed dollar risk."""
    n = len(bars_arr)
    spike_set = set(spikes.tolist())
    last_spike = -1
    in_trade = False
    entry_price = 0
    entry_idx = 0
    pnls = []
    reasons = []

    for i in range(n):
        if i in spike_set:
            last_spike = i
        bars_since = i - last_spike if last_spike >= 0 else 999

        if in_trade:
            h, l, c = bars_arr[i, 1], bars_arr[i, 2], bars_arr[i, 3]
            bars_held = i - entry_idx

            if direction == 'short':  # SHORT after BOOM spike UP
                hit_tp = (entry_price - l) >= tp
                hit_sl = (entry_price - h) <= -sl
                unrealized = entry_price - c
            else:  # LONG after CRASH spike DOWN
                hit_tp = (h - entry_price) >= tp
                hit_sl = (l - entry_price) <= -sl
                unrealized = c - entry_price

            exit_pts = None
            reason = None
            if hit_tp:
                exit_pts, reason = tp, 'tp'
            elif hit_sl:
                exit_pts, reason = -sl, 'sl'
            elif i in spike_set:
                exit_pts, reason = unrealized, 'spike'
            elif bars_held >= max_hold:
                exit_pts, reason = unrealized, 'timeout'

            if exit_pts is not None:
                pnls.append(exit_pts * dpp)
                reasons.append(reason)
                in_trade = False

        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = bars_arr[i, 3]
            entry_idx = i
            in_trade = True

    if not pnls:
        return None

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    reason_counts = {}
    for r in reasons:
        reason_counts[r] = reason_counts.get(r, 0) + 1

    tw = sum(wins); tl = abs(sum(losses))
    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100

    return {
        'trades': len(pnls), 'wins': len(wins), 'losses': len(losses),
        'win_rate': len(wins) / len(pnls) * 100,
        'total_pnl': sum(pnls),
        'pf': tw / tl if tl > 0 else 99,
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'avg_rr': abs(np.mean(wins) / np.mean(losses)) if wins and losses and np.mean(losses) != 0 else 99,
        'max_dd': dd.max(),
        'reasons': reason_counts,
        'pnls': pnls,
    }


# ─── SMC Backtest ─────────────────────────────────────────────────────────────

def backtest_smc(symbol, equity=200.0, risk_pct=0.012, rsi_low=15.0, rsi_high=85.0,
                 sl_pct=0.005, max_hold=60):
    """Standalone SMC backtest."""
    m1 = load_candles(symbol, "1min")
    try:
        h1 = load_candles(symbol, "60min")
        zone_mult = 2.0 if "crash" in symbol.lower() else 2.5
        zones = detect_order_blocks(h1, threshold_mult=zone_mult)
    except FileNotFoundError:
        zones = []

    cfg = SMCHunterConfig(
        sl_pct=sl_pct, risk_per_trade=risk_pct,
        rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high,
        max_hold=max_hold,
    )
    strat = SMCSpikeHunter(symbol, config=cfg, initial_equity=equity)
    strat.set_h1_zones(zones)
    strat.precompute(m1)

    for i, bar in enumerate(m1):
        strat.on_bar(bar, i)
    if strat.position != 0:
        strat._close_position(m1[-1]['close'], "end_of_data", m1[-1]['close'])

    pnls = [t['pnl'] for t in strat.trade_log]
    n = len(pnls)
    if n == 0:
        return {'trades': 0, 'total_pnl': 0, 'pf': 0, 'pnls': []}

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    tw = sum(wins); tl = abs(sum(losses))
    return {
        'trades': n, 'wins': len(wins), 'losses': len(losses),
        'win_rate': len(wins) / n * 100,
        'total_pnl': sum(pnls),
        'pf': tw / tl if tl > 0 else 99,
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'avg_rr': abs(np.mean(wins) / np.mean(losses)) if wins and losses and np.mean(losses) != 0 else 99,
        'exit_breakdown': {},
        'zones': len(zones),
        'pnls': pnls,
    }


# ─── Combined Backtest ────────────────────────────────────────────────────────

def backtest_combined(symbol, equity=200.0, risk_pct=0.02, smc_share=0.6,
                      rsi_low=15.0, rsi_high=85.0):
    """Run both strategies on same bars with SHARED equity (no compounding)."""
    dpp = 0.10 if "1000" in symbol else 0.20
    is_boom = "boom" in symbol.lower()
    direction = "short" if is_boom else "long"

    m1 = load_candles(symbol, "1min")
    try:
        h1 = load_candles(symbol, "60min")
        zone_mult = 2.0 if not is_boom else 2.5
        zones = detect_order_blocks(h1, threshold_mult=zone_mult)
    except FileNotFoundError:
        zones = []

    # Detect spikes on M1
    bars_arr = np.array([[b['open'], b['high'], b['low'], b['close']] for b in m1], dtype=float)
    ranges = bars_arr[:, 1] - bars_arr[:, 2]
    med = float(np.median(ranges))
    spike_mask = ranges > 3.0 * med
    if is_boom:
        spike_mask &= bars_arr[:, 3] > bars_arr[:, 0]  # UP spikes for BOOM
    else:
        spike_mask &= bars_arr[:, 3] < bars_arr[:, 0]  # DOWN spikes for CRASH
    spikes = np.where(spike_mask)[0]
    spike_set = set(spikes.tolist())

    # ── SMC strategy ──
    smc_cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=risk_pct * smc_share,
        rsi_extreme_low=rsi_low, rsi_extreme_high=rsi_high,
    )
    smc = SMCSpikeHunter(symbol, config=smc_cfg, initial_equity=equity)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    # ── Drift Fade state ──
    drift_entry_bar = 3
    drift_sl, drift_tp, drift_max = 1.0, 3.0, 9
    drift_position = 0
    drift_entry_price = 0.0
    drift_bars_held = 0
    drift_last_spike = -999
    drift_position_size = 0.0
    drift_ranges = []
    drift_median = 1.0

    # Cluster detection: spikes in groups of 3+ within 10 bars
    spike_cluster = set()
    spikes_arr = np.array(spikes)
    for s in spikes_arr:
        nearby = int(np.sum(np.abs(spikes_arr - s) <= 10)) - 1
        if nearby >= 2:
            spike_cluster.add(int(s))

    # Shared equity
    shared_equity = equity
    shared_peak = equity
    all_pnls = []
    smc_pnls = []
    drift_pnls = []
    trade_count = 0

    for i, bar in enumerate(m1):
        h, l, c, o = bar['high'], bar['low'], bar['close'], bar['open']

        # ── Drift Fade logic ──
        bar_range = h - l
        drift_ranges.append(bar_range)
        if len(drift_ranges) > 100:
            drift_ranges.pop(0)
        drift_median = float(np.median(drift_ranges))

        # Update drift spike tracking
        is_spike = bar_range > 3.0 * drift_median
        is_boom_spike = is_spike and c > o
        is_crash_spike = is_spike and c < o
        if (is_boom and is_boom_spike) or (not is_boom and is_crash_spike):
            drift_last_spike = i

        # Drift Fade exit
        if drift_position != 0:
            drift_bars_held += 1
            drift_exit = None
            drift_reason = None

            if drift_position == -1:  # SHORT (BOOM counter-drift)
                hit_tp = (drift_entry_price - l) >= drift_tp
                hit_sl = (drift_entry_price - h) <= -drift_sl
                unrealized = drift_entry_price - c
            else:  # LONG (CRASH counter-drift)
                hit_tp = (h - drift_entry_price) >= drift_tp
                hit_sl = (l - drift_entry_price) <= -drift_sl
                unrealized = c - drift_entry_price

            if hit_tp:
                drift_exit, drift_reason = drift_tp, "tp"
            elif hit_sl:
                drift_exit, drift_reason = -drift_sl, "sl"
            elif i in spike_set:
                drift_exit, drift_reason = unrealized, "spike"
            elif drift_bars_held >= drift_max:
                drift_exit, drift_reason = unrealized, "timeout"

            if drift_exit is not None:
                pnl = drift_exit * dpp * drift_position_size
                shared_equity += pnl
                drift_pnls.append(pnl)
                all_pnls.append(pnl)
                trade_count += 1
                drift_position = 0
                drift_bars_held = 0

        # Drift Fade entry (only if SMC is flat)
        elif drift_position == 0 and smc.position == 0:
            bars_since = i - drift_last_spike if drift_last_spike >= 0 else 999
            if bars_since == drift_entry_bar and drift_last_spike >= 0:
                # FILTER: Skip clustered spikes (chaotic, lower WR)
                if drift_last_spike in spike_cluster:
                    pass  # Skip this entry
                else:
                    drift_position = -1 if is_boom else 1
                    drift_entry_price = c
                    drift_bars_held = 0
                    risk_dollars = equity * risk_pct * (1 - smc_share)
                    drift_position_size = risk_dollars / (drift_sl * dpp) if drift_sl * dpp > 0 else 0

        # ── SMC logic ──
        smc.equity = equity  # Use INITIAL equity for sizing (no compounding)
        smc.peak_equity = max(smc.peak_equity, shared_equity)
        smc_result = smc.on_bar(bar, i)

        if smc_result:
            if smc_result["action"] == "close":
                pnl = smc_result["pnl"]
                shared_equity += pnl
                smc_pnls.append(pnl)
                all_pnls.append(pnl)
                trade_count += 1

        shared_peak = max(shared_peak, shared_equity)

    # Close open positions
    if smc.position != 0:
        smc._close_position(m1[-1]['close'], "end_of_data", m1[-1]['close'])
        if smc.trade_log:
            pnl = smc.trade_log[-1]['pnl']
            shared_equity += pnl
            smc_pnls.append(pnl)
            all_pnls.append(pnl)

    if drift_position != 0:
        c = m1[-1]['close']
        unrealized = (drift_entry_price - c) if drift_position == -1 else (c - drift_entry_price)
        pnl = unrealized * dpp * drift_position_size
        shared_equity += pnl
        drift_pnls.append(pnl)
        all_pnls.append(pnl)

    return {
        "symbol": symbol,
        "zones": len(zones),
        "initial_equity": equity,
        "final_equity": shared_equity,
        "total_pnl": shared_equity - equity,
        "max_dd": ((shared_peak - shared_equity) / shared_peak * 100) if shared_peak > 0 else 0,
        "smc": _stats(smc_pnls, "SMC"),
        "drift": _stats(drift_pnls, "Drift"),
        "combined": _stats(all_pnls, "Combined"),
        "all_pnls": all_pnls,
        "smc_pnls": smc_pnls,
        "drift_pnls": drift_pnls,
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


# ─── Monte Carlo ──────────────────────────────────────────────────────────────

def monte_carlo(all_pnls, months=12, n_sims=1000, initial_equity=200.0):
    np.random.seed(42)
    pnls = np.array(all_pnls)
    if len(pnls) == 0:
        return {"median": initial_equity, "p5": initial_equity, "p95": initial_equity,
                "profit_pct": 0, "blowup_pct": 0, "median_return": 0, "p5_return": 0, "p95_return": 0}
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    wr = len(wins) / len(pnls)
    avg_win = np.mean(wins) if len(wins) > 0 else 1.0
    avg_loss = abs(np.mean(losses)) if len(losses) > 0 else 1.0
    trades_per_month = len(pnls) / 30
    total_trades = int(trades_per_month * months)

    results = np.zeros(n_sims)
    for sim in range(n_sims):
        eq = initial_equity
        for _ in range(total_trades):
            if np.random.random() < wr:
                eq += avg_win
            else:
                eq -= avg_loss
            eq = max(eq, 0)
        results[sim] = eq

    return {
        "median": np.median(results), "p5": np.percentile(results, 5),
        "p95": np.percentile(results, 95),
        "blowup_pct": np.sum(results < initial_equity * 0.5) / n_sims * 100,
        "profit_pct": np.sum(results > initial_equity) / n_sims * 100,
        "median_return": (np.median(results) / initial_equity - 1) * 100,
        "p5_return": (np.percentile(results, 5) / initial_equity - 1) * 100,
        "p95_return": (np.percentile(results, 95) / initial_equity - 1) * 100,
    }


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    EQUITY = 200.0
    RISK = 0.02
    SMC_SHARE = 0.6

    print(f"\n{'='*80}")
    print(f"  DUAL STRATEGY BACKTEST — 30 Days Historical Data")
    print(f"  SMC ({SMC_SHARE*100:.0f}% risk) + Drift Fade ({(1-SMC_SHARE)*100:.0f}% risk)")
    print(f"  Equity: ${EQUITY:.0f}  |  Risk: {RISK*100:.0f}%/trade  |  FIXED (no compounding)")
    print(f"{'='*80}")

    for sym in ["BOOM500", "CRASH500", "BOOM1000"]:
        print(f"\n  {'─'*76}")
        print(f"  {sym}")
        print(f"  {'─'*76}")

        r = backtest_combined(sym, equity=EQUITY, risk_pct=RISK, smc_share=SMC_SHARE)

        # Individual strategies for comparison
        smc_only = backtest_smc(sym, equity=EQUITY, risk_pct=RISK,
                                rsi_low=15.0 if "boom" in sym.lower() else 10.0,
                                rsi_high=85.0 if "boom" in sym.lower() else 90.0)
        dpp = 0.10 if "1000" in sym else 0.20
        direction = "short" if "boom" in sym.lower() else "long"
        drift_only = backtest_drift(
            np.array([[b['open'], b['high'], b['low'], b['close']] for b in load_candles(sym, '1min')], dtype=float),
            np.array([i for i in range(len(load_candles(sym, '1min')))
                      if (lambda b, dr: (b[1]-b[2] > 3.0*np.median([bb[1]-bb[2] for bb in [[bb2['open'],bb2['high'],bb2['low'],bb2['close']] for bb2 in load_candles(sym,'1min')][:max(1,i)]]) and ((dr=='short' and b[3]>b[0]) or (dr=='long' and b[3]<b[0]))))(
                          [load_candles(sym,'1min')[i]['open'], load_candles(sym,'1min')[i]['high'],
                           load_candles(sym,'1min')[i]['low'], load_candles(sym,'1min')[i]['close']],
                          direction)]),
            entry_bar=3, sl=1.0, tp=3.0, max_hold=9, direction=direction, dpp=dpp,
        ) if False else None  # Skip standalone drift — too complex inline

        # Simple standalone drift for comparison
        bars_data = load_candles(sym, '1min')
        bars_arr = np.array([[b['open'], b['high'], b['low'], b['close']] for b in bars_data], dtype=float)
        ranges = bars_arr[:, 1] - bars_arr[:, 2]
        med = float(np.median(ranges))
        spike_mask = ranges > 3.0 * med
        if "boom" in sym.lower():
            spike_mask &= bars_arr[:, 3] > bars_arr[:, 0]
        else:
            spike_mask &= bars_arr[:, 3] < bars_arr[:, 0]
        spikes_standalone = np.where(spike_mask)[0]
        drift_only = backtest_drift(bars_arr, spikes_standalone, entry_bar=3, sl=1.0, tp=3.0,
                                     max_hold=9, direction=direction, dpp=dpp)

        # Display comparison
        print(f"\n  {'Strategy':<12} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>10} {'AvgWin':>8} {'AvgLoss':>8} {'AvgRR':>7}")
        print(f"  {'─'*72}")

        for label, s in [("SMC Only", smc_only), ("Drift Only", drift_only), ("COMBINED", r["combined"])]:
            if not s or s.get("trades", 0) == 0:
                print(f"  {label:<12} {'No trades':>7}")
                continue
            marker = " <" if label == "COMBINED" else ""
            print(f"  {label:<12} {s['trades']:>7} {s['win_rate']:>6.1f}% {s['pf']:>5.2f} "
                  f"${s['total_pnl']:>+9.2f} ${s['avg_win']:>+7.2f} ${s['avg_loss']:>+7.2f} "
                  f"1:{s['avg_rr']:.1f}{marker}")

        print(f"\n  H1 Zones: {r['zones']}")
        print(f"  Final Equity: ${r['final_equity']:.2f}  (started ${r['initial_equity']:.0f})")
        print(f"  Total PnL: ${r['total_pnl']:+.2f}")
        print(f"  Max Drawdown: {r['max_dd']:.2f}%")

        # Monte Carlo
        mc = monte_carlo(r["all_pnls"], months=12, initial_equity=EQUITY)
        print(f"\n  Monte Carlo (12-month, ${EQUITY:.0f} start, no compounding):")
        print(f"    Median:  ${mc['median']:.2f} ({mc['median_return']:+.1f}%)")
        print(f"    P5:      ${mc['p5']:.2f} ({mc['p5_return']:+.1f}%)")
        print(f"    P95:     ${mc['p95']:.2f} ({mc['p95_return']:+.1f}%)")
        print(f"    Profit:  {mc['profit_pct']:.0f}% chance  |  Blowup: {mc['blowup_pct']:.1f}%")

    # Portfolio summary
    print(f"\n{'='*80}")
    print(f"  PORTFOLIO SUMMARY")
    print(f"{'='*80}")
    total_monthly = 0
    for sym in ["BOOM500", "BOOM1000"]:
        r = backtest_combined(sym, equity=EQUITY, risk_pct=RISK, smc_share=SMC_SHARE)
        c = r["combined"]
        if c["trades"] > 0:
            monthly = c["total_pnl"]
            total_monthly += monthly
            print(f"  {sym}: ${monthly:+.2f}/mo  ({c['trades']}T, WR={c['win_rate']:.1f}%, PF={c['pf']:.2f})")
    print(f"  {'─'*50}")
    print(f"  TOTAL: ${total_monthly:+.2f}/mo  (${total_monthly*12:+.0f}/year)")
    print(f"  On ${EQUITY:.0f} per symbol (${EQUITY*2:.0f} total)")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
