"""
eval/backtest_compounding.py — Compounding backtest for ALL BOOM/CRASH variants.

Tests fractional Kelly with intra-trade drawdown tracking across
BOOM500, CRASH500, BOOM1000, CRASH1000.
"""

import time

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Symbol configs (from backtested optimal parameters)
# ---------------------------------------------------------------------------

SYMBOLS = {
    "BOOM500": {
        "path": "data/candles_boom_500_1min.parquet",
        "spike_dir": "up",
        "tp": 0.005,
        "sl": 0.001,
        "hold": 8,
        "max_bars": 30 * 1440,  # 30 days
    },
    "CRASH500": {
        "path": "data/candles_crash_500_1min.parquet",
        "spike_dir": "down",
        "tp": 0.005,
        "sl": 0.001,
        "hold": 15,
        "max_bars": 30 * 1440,
    },
    "BOOM1000": {
        "path": "data/candles_boom1000_1min.parquet",
        "spike_dir": "up",
        "tp": 0.005,
        "sl": 0.001,
        "hold": 15,
        "max_bars": None,  # use all (only ~3.5 days)
    },
    "CRASH1000": {
        "path": "data/candles_crash1000_1min.parquet",
        "spike_dir": "down",
        "tp": 0.003,
        "sl": 0.001,
        "hold": 5,
        "max_bars": None,
    },
}

INITIAL = 10000


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def detect_fadeable_spikes(df, spike_dir, mult=3.0, cluster_gap=5, large_pct=0.0016):
    """Detect fadeable spikes with cluster classification."""
    df = df.copy()
    df["ret"] = df["close"].pct_change()
    df["abs_ret"] = df["ret"].abs()
    med = df["abs_ret"].median()
    thresh = med * mult

    if spike_dir == "up":
        df["is_spike"] = df["ret"] > thresh
    else:
        df["is_spike"] = df["ret"] < -thresh
    df["spike_size"] = df["ret"].abs()

    spike_idx = df[df["is_spike"]].index.tolist()
    gaps = np.diff(spike_idx) if len(spike_idx) > 1 else []

    positions = {}
    cid = 0
    for i, idx in enumerate(spike_idx):
        prev_g = gaps[i - 1] if i > 0 else 999
        next_g = gaps[i] if i < len(gaps) else 999
        in_prev = i > 0 and prev_g <= cluster_gap
        in_next = i < len(gaps) and next_g <= cluster_gap
        if not in_prev and not in_next:
            positions[idx] = "isolated"
        elif not in_prev and in_next:
            positions[idx] = "first"
            cid += 1
        elif in_prev and in_next:
            positions[idx] = "middle"
        else:
            positions[idx] = "last"

    df["spike_pos"] = df.index.map(lambda x: positions.get(x))
    df["fadeable"] = (
        df["is_spike"]
        & df["spike_pos"].isin(["isolated", "last"])
        & (df["spike_size"] < large_pct)
    )
    return df


def generate_trades(df, spike_dir, tp, sl, hold):
    """Generate trade list with intra-trade max adverse/favorable excursion."""
    trades = []
    pos = 0
    entry_p = 0.0
    bars = 0
    pending = None

    fade_dir = -1 if spike_dir == "up" else 1  # BOOM: short, CRASH: long

    for i in range(1, len(df)):
        r = df.iloc[i]
        p, h, lo = r["close"], r["high"], r["low"]

        if pos != 0:
            bars += 1
            if pos == 1:  # Long (CRASH fade)
                worst = min(
                    (lo - entry_p) / entry_p,
                    (h - entry_p) / entry_p,
                    (p - entry_p) / entry_p,
                )
                best = max(
                    (lo - entry_p) / entry_p,
                    (h - entry_p) / entry_p,
                    (p - entry_p) / entry_p,
                )
                if h >= entry_p * (1 + tp):
                    trades.append({"pnl_pct": tp, "reason": "tp", "bars": bars, "mfe": tp, "mae": -sl * 0.5})
                    pos = 0; bars = 0; continue
                if lo <= entry_p * (1 - sl):
                    trades.append({"pnl_pct": -sl, "reason": "sl", "bars": bars, "mfe": best, "mae": -sl})
                    pos = 0; bars = 0; continue
                if bars >= hold:
                    pnl_pct = (p - entry_p) / entry_p
                    trades.append({"pnl_pct": pnl_pct, "reason": "timeout", "bars": bars, "mfe": best, "mae": worst})
                    pos = 0; bars = 0; continue
            else:  # Short (BOOM fade)
                worst = min(
                    (entry_p - lo) / entry_p,
                    (entry_p - h) / entry_p,
                    (entry_p - p) / entry_p,
                )
                best = max(
                    (entry_p - lo) / entry_p,
                    (entry_p - h) / entry_p,
                    (entry_p - p) / entry_p,
                )
                if lo <= entry_p * (1 - tp):
                    trades.append({"pnl_pct": tp, "reason": "tp", "bars": bars, "mfe": tp, "mae": -sl * 0.5})
                    pos = 0; bars = 0; continue
                if h >= entry_p * (1 + sl):
                    trades.append({"pnl_pct": -sl, "reason": "sl", "bars": bars, "mfe": best, "mae": -sl})
                    pos = 0; bars = 0; continue
                if bars >= hold:
                    pnl_pct = (entry_p - p) / entry_p
                    trades.append({"pnl_pct": pnl_pct, "reason": "timeout", "bars": bars, "mfe": best, "mae": worst})
                    pos = 0; bars = 0; continue

        if pending is not None:
            pending -= 1
            if pending <= 0:
                pos = fade_dir
                entry_p = p
                bars = 0
                pending = None
                continue

        if pos == 0 and r.get("fadeable", False):
            pending = 1

    return trades


def backtest_compound(trades, risk_pct, initial=INITIAL):
    """Backtest with fractional compounding and intra-trade DD tracking."""
    equity = initial
    peak = initial
    max_dd_pct = 0
    equity_curve = [equity]
    pos_sizes = []
    wins = 0
    losses = 0
    total_pnl_pct = 0.0

    for t in trades:
        pnl = t["pnl_pct"]
        mae = t["mae"]

        # Position sizing (pure fractional compounding)
        size = equity * risk_pct

        pos_sizes.append(size)

        # Intra-trade drawdown
        intra_dd = size * abs(mae)
        eq_after_dd = equity - intra_dd
        if eq_after_dd < peak:
            dd = (peak - eq_after_dd) / peak
            max_dd_pct = max(max_dd_pct, dd)

        # Apply PnL
        pnl_dollar = size * pnl
        equity += pnl_dollar
        peak = max(peak, equity)
        equity_curve.append(equity)

        total_pnl_pct += pnl
        if pnl > 0:
            wins += 1
        else:
            losses += 1

    wr = wins / (wins + losses) if (wins + losses) > 0 else 0
    return {
        "final": equity,
        "return_pct": (equity / initial - 1) * 100,
        "max_dd_pct": max_dd_pct * 100,
        "trades": len(trades),
        "wr": wr * 100,
        "avg_pos": np.mean(pos_sizes),
        "equity_curve": equity_curve,
    }


def print_growth_curve(trades, risk_pct, label, trades_per_day):
    """Print milestone growth curve."""
    equity = INITIAL
    milestones = [10500, 11000, 12000, 13000, 15000, 17000, 20000, 25000, 30000, 50000, 100000]
    next_ms = 0
    hit = []

    for i, t in enumerate(trades):
        size = equity * risk_pct
        equity += size * t["pnl_pct"]
        day = i / trades_per_day if trades_per_day > 0 else 0

        if next_ms < len(milestones) and equity >= milestones[next_ms]:
            hit.append(f"  Day {day:5.1f}: ${equity:,.0f} ({(equity / INITIAL - 1) * 100:+.1f}%)")
            next_ms += 1

    hit.append(f"  Final:  ${equity:,.0f} ({(equity / INITIAL - 1) * 100:+.1f}%)")
    return hit


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    risk_levels = [0.03, 0.05, 0.08, 0.10, 0.15, 0.20, 0.30]

    print("=" * 90)
    print("  BOOM/CRASH COMPOUNDING BACKTEST — ALL VARIANTS")
    print("=" * 90)

    all_results = {}

    for sym, cfg in SYMBOLS.items():
        t0 = time.time()
        df = pd.read_parquet(cfg["path"])
        if cfg["max_bars"]:
            df = df.head(cfg["max_bars"])

        days = len(df) / 1440
        df = detect_fadeable_spikes(df, cfg["spike_dir"])
        n_spikes = int(df["is_spike"].sum())
        n_fadeable = int(df["fadeable"].sum())
        trades = generate_trades(df, cfg["spike_dir"], cfg["tp"], cfg["sl"], cfg["hold"])
        n_trades = len(trades)
        trades_per_day = n_trades / days if days > 0 else 1

        # Raw trade stats
        pnl_arr = np.array([t["pnl_pct"] for t in trades])
        n_wins = int(np.sum(pnl_arr > 0))
        wr = n_wins / n_trades * 100 if n_trades > 0 else 0
        avg_win = float(np.mean(pnl_arr[pnl_arr > 0])) * 100 if n_wins > 0 else 0
        avg_loss = float(np.mean(pnl_arr[pnl_arr <= 0])) * 100 if (n_trades - n_wins) > 0 else 0

        # Kelly
        wr_f = n_wins / n_trades if n_trades > 0 else 0
        avg_w_f = np.mean(pnl_arr[pnl_arr > 0]) if n_wins > 0 else 0
        avg_l_f = abs(np.mean(pnl_arr[pnl_arr <= 0])) if (n_trades - n_wins) > 0 else 0
        kelly_full = (wr_f * avg_w_f - (1 - wr_f) * avg_l_f) / avg_w_f if avg_w_f > 0 else 0

        elapsed = time.time() - t0

        print(f"\n{'-' * 90}")
        print(f"  {sym}  |  {days:.0f} days  |  {n_spikes} spikes  |  {n_fadeable} fadeable  |  {n_trades} trades  |  {trades_per_day:.0f}/day")
        print(f"  WR: {wr:.1f}%  |  Avg win: {avg_win:.3f}%  |  Avg loss: {avg_loss:.3f}%  |  Kelly: {kelly_full * 100:.0f}%")
        print(f"{'─' * 90}")

        # Risk level comparison
        print(f"  {'Risk':>6s}  {'Return':>9s}  {'MaxDD':>7s}  {'Ret/DD':>7s}  {'Final':>12s}  {'Sharpe':>7s}")
        print(f"  {'-' * 6}  {'-' * 9}  {'-' * 7}  {'-' * 7}  {'-' * 12}  {'-' * 7}")

        best_for_sym = {"risk": 0, "return": 0, "dd": 999, "ratio": 0}

        for risk in risk_levels:
            r = backtest_compound(trades, risk)
            ratio = r["return_pct"] / r["max_dd_pct"] if r["max_dd_pct"] > 0.01 else 999

            # Sharpe
            eq = np.array(r["equity_curve"])
            rets = np.diff(eq) / eq[:-1]
            tpd = max(1, int(trades_per_day))
            daily_rets = []
            for d in range(0, len(rets), tpd):
                daily_rets.append(np.prod(1 + rets[d:d + tpd]) - 1)
            daily_rets = np.array(daily_rets)
            sharpe = float(np.mean(daily_rets) / np.std(daily_rets) * np.sqrt(365)) if np.std(daily_rets) > 0 else 0

            marker = ""
            if ratio > best_for_sym["ratio"] and ratio < 999:
                best_for_sym = {"risk": risk, "return": r["return_pct"], "dd": r["max_dd_pct"], "ratio": ratio}

            print(f"  {risk * 100:5.1f}%  {r['return_pct']:+8.1f}%  {r['max_dd_pct']:6.2f}%  {ratio:6.1f}x  ${r['final']:11,.0f}  {sharpe:6.2f}")

        # Growth curve at 10% risk
        r10 = backtest_compound(trades, 0.10)
        growth = print_growth_curve(trades, 0.10, f"{sym} 10%", trades_per_day)
        print(f"\n  Growth @ 10% risk:")
        for line in growth:
            print(f"    {line}")

        all_results[sym] = {
            "days": days,
            "trades": n_trades,
            "trades_per_day": trades_per_day,
            "wr": wr,
            "kelly": kelly_full,
            "best": best_for_sym,
            "r10": r10,
        }

    # ---- CROSS-VARIANT SUMMARY ----
    print(f"\n{'=' * 90}")
    print("  CROSS-VARIANT SUMMARY")
    print(f"{'=' * 90}")
    print(f"  {'Symbol':12s} {'Days':>5s} {'Trades':>7s} {'T/day':>6s} {'WR':>6s} {'Kelly':>6s} {'Best Risk':>9s} {'Best Ret':>9s} {'Best DD':>8s} {'10% Ret':>8s}")
    print(f"  {'-' * 12} {'-' * 5} {'-' * 7} {'-' * 6} {'-' * 6} {'-' * 6} {'-' * 9} {'-' * 9} {'-' * 8} {'-' * 8}")

    total_trades = 0
    for sym, res in all_results.items():
        b = res["best"]
        total_trades += res["trades"]
        print(
            f"  {sym:12s} {res['days']:5.0f} {res['trades']:7d} {res['trades_per_day']:6.0f} "
            f"{res['wr']:5.1f}% {res['kelly'] * 100:5.0f}% "
            f"{b['risk'] * 100:8.1f}% {b['return']:+8.1f}% {b['dd']:7.2f}% "
            f"{res['r10']['return_pct']:+7.1f}%"
        )

    print(f"\n  Total trades across all variants: {total_trades}")

    # ---- PORTFOLIO COMPOUNDING (all 4 variants at 10% risk) ----
    print(f"\n{'=' * 90}")
    print("  PORTFOLIO COMPOUNDING (all 4 variants, 10% risk each)")
    print(f"{'=' * 90}")

    # Merge all trades sorted by index (simulate running all in parallel)
    all_trades = []
    for sym, cfg in SYMBOLS.items():
        df = pd.read_parquet(cfg["path"])
        if cfg["max_bars"]:
            df = df.head(cfg["max_bars"])
        df = detect_fadeable_spikes(df, cfg["spike_dir"])
        trades = generate_trades(df, cfg["spike_dir"], cfg["tp"], cfg["sl"], cfg["hold"])
        for t in trades:
            all_trades.append(t)
            all_trades[-1]["symbol"] = sym

    # Sort by trade order (interleaved)
    all_trades.sort(key=lambda x: x["bars"])  # approximate ordering

    # Simulate portfolio with $10k total, 2.5% per variant (10% total)
    variant_equity = {sym: INITIAL / 4 for sym in SYMBOLS}
    portfolio_equity = INITIAL
    portfolio_peak = INITIAL
    portfolio_max_dd = 0

    for sym, cfg in SYMBOLS.items():
        sym_trades = [t for t in all_trades if t.get("symbol") == sym]
        eq = INITIAL / 4
        peak = eq
        for t in sym_trades:
            size = eq * 0.10
            eq += size * t["pnl_pct"]
            peak = max(peak, eq)
        variant_equity[sym] = eq

    total_final = sum(variant_equity.values())
    total_return = (total_final / INITIAL - 1) * 100

    print(f"  Starting equity: ${INITIAL:,.0f} ($2,500 per variant)")
    print(f"  Risk per variant: 10% of variant equity")
    print()
    for sym in SYMBOLS:
        eq = variant_equity[sym]
        ret = (eq / (INITIAL / 4) - 1) * 100
        print(f"  {sym:12s}: ${eq:11,.2f}  ({ret:+.1f}%)")
    print(f"  {'-' * 40}")
    print(f"  {'PORTFOLIO':12s}: ${total_final:11,.2f}  ({total_return:+.1f}%)")

    # ---- PROJECTION ----
    print(f"\n  30-DAY PROJECTIONS (per variant, 10% risk):")
    print(f"  {'Symbol':12s} {'30d':>10s} {'60d':>10s} {'90d':>10s}")
    print(f"  {'─' * 12} {'─' * 10} {'─' * 10} {'─' * 10}")

    for sym, res in all_results.items():
        daily_return = res["r10"]["return_pct"] / res["days"] if res["days"] > 0 else 0
        projections = []
        for period in [30, 60, 90]:
            # Compound daily return
            projected = INITIAL * (1 + daily_return / 100) ** period
            projections.append(projected)
        print(
            f"  {sym:12s} ${projections[0]:9,.0f} ${projections[1]:9,.0f} ${projections[2]:9,.0f}"
        )

    print(f"\n{'=' * 90}")


if __name__ == "__main__":
    main()
