"""
eval/optimize_drift_crash1000.py — Deep optimization of DriftFade for CRASH1000.

CRASH1000 characteristics:
- 76.3% WR with basic config
- Spikes go DOWN (we BUY the fade = UP direction)
- Longer quiet periods between spikes
- $0.10 per point

Strategy: After a down-spike, wait N bars, BUY (counter-drift), TP/SL exit.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path
import json
import time

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Load Data ─────────────────────────────────────────────────
DATA_DIR = Path("data")

def load_parquet(name: str) -> tuple:
    """Load parquet and return (closes, highs, lows, opens) arrays."""
    df = pd.read_parquet(DATA_DIR / name)
    return (
        df["open"].values.astype(float),
        df["high"].values.astype(float),
        df["low"].values.astype(float),
        df["close"].values.astype(float),
    )

# ─── Spike Detection ──────────────────────────────────────────
def detect_spikes(closes: np.ndarray, threshold: float = 3.0) -> np.ndarray:
    """Detect spikes by bar range vs median."""
    n = len(closes)
    ranges = np.zeros(n)
    for i in range(1, n):
        ranges[i] = abs(closes[i] - closes[i-1])
    ranges[0] = ranges[1] if n > 1 else 0
    med = np.median(ranges[ranges > 0])
    return np.where(ranges > med * threshold)[0]

def detect_spike_clusters(spikes: np.ndarray, max_gap: int = 5) -> list:
    """Group spikes into clusters."""
    if len(spikes) == 0:
        return []
    clusters = []
    current = [spikes[0]]
    for i in range(1, len(spikes)):
        if spikes[i] - spikes[i-1] <= max_gap:
            current.append(spikes[i])
        else:
            clusters.append(current)
            current = [spikes[i]]
    clusters.append(current)
    return clusters

# ─── DriftFade Backtest Engine ────────────────────────────────
def backtest_driftfade(
    opens, highs, lows, closes,
    entry_bar: int,
    sl_pts: float,
    tp_pts: float,
    max_hold: int,
    direction: str,
    dpp: float,
    spike_threshold: float = 3.0,
    min_spike_gap: int = 2,
    use_trailing: bool = False,
    trailing_activation: float = 0.5,
    trailing_distance: float = 0.3,
    use_vol_adjust: bool = False,
    vol_window: int = 100,
) -> dict:
    """
    DriftFade: After a spike, enter counter-drift N bars later.
    direction='down' means spikes go DOWN, we BUY (fade) = LONG.
    """
    n = len(closes)
    spikes = detect_spikes(closes, threshold=spike_threshold)
    spike_set = set(spikes.tolist())
    last_spike = -999
    last_spike_price = 0.0

    # Volatility for vol-adjusted exits
    if use_vol_adjust:
        ranges = np.abs(np.diff(closes))
        ranges = np.concatenate([[ranges[0]], ranges])

    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    best_price = 0.0

    pnls = []
    reasons = []
    bars_since_arr = []

    for i in range(n):
        if i in spike_set:
            # Cooldown: don't count cluster members
            if i - last_spike > min_spike_gap:
                last_spike = i
                last_spike_price = closes[i]

        bars_since = i - last_spike if last_spike >= 0 else 999

        if in_trade:
            bars_held = i - entry_idx
            price = closes[i]

            # Direction: BUY (long) for crash fade
            unrealized = price - entry_price

            # Vol-adjusted TP/SL
            if use_vol_adjust:
                recent_vol = np.mean(ranges[max(0, i-vol_window):i]) if i > 0 else 0.001
                adj_tp = tp_pts * (1 + recent_vol * 100)
                adj_sl = sl_pts * (1 + recent_vol * 50)
            else:
                adj_tp = tp_pts
                adj_sl = sl_pts

            # Check exits (check high/low for intra-bar)
            hit_tp = (highs[i] - entry_price) >= adj_tp
            hit_sl = (lows[i] - entry_price) <= -adj_sl

            exit_pts = None
            reason = None

            if hit_tp:
                exit_pts = adj_tp
                reason = "tp"
            elif hit_sl:
                exit_pts = -adj_sl
                reason = "sl"
            elif i in spike_set and (i - entry_idx) > 3:
                # New spike against us
                exit_pts = unrealized
                reason = "spike_against"
            elif bars_held >= max_hold:
                exit_pts = unrealized
                reason = "timeout"

            # Trailing stop
            if use_trailing and exit_pts is None and bars_held >= 3:
                if price > best_price:
                    best_price = price
                trail_level = best_price - adj_tp * trailing_distance
                if price <= trail_level and best_price > entry_price + adj_tp * trailing_activation:
                    exit_pts = price - entry_price
                    reason = "trailing"

            if exit_pts is not None:
                pnl_dollars = exit_pts * dpp
                pnls.append(pnl_dollars)
                reasons.append(reason)
                bars_since_arr.append(bars_since)
                in_trade = False

        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = closes[i]
            entry_idx = i
            best_price = closes[i]
            in_trade = True

    if in_trade:
        pnls.append((closes[-1] - entry_price) * dpp)
        reasons.append("eod")
        bars_since_arr.append(999)

    return _summarize(pnls, reasons, bars_since_arr)


def _summarize(pnls, reasons, bars_since_arr):
    """Compute performance metrics."""
    if not pnls:
        return {"trades": 0}

    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    tw = sum(wins) if wins else 0
    tl = abs(sum(losses)) if losses else 0

    # Equity curve
    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100

    # Reason breakdown
    reason_stats = {}
    for r, p in zip(reasons, pnls):
        if r not in reason_stats:
            reason_stats[r] = {"count": 0, "pnl": 0, "wins": 0}
        reason_stats[r]["count"] += 1
        reason_stats[r]["pnl"] += p
        if p > 0:
            reason_stats[r]["wins"] += 1

    # Bars since spike distribution
    avg_bars = np.mean(bars_since_arr) if bars_since_arr else 0

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": len(wins) / n * 100,
        "total_pnl": sum(pnls),
        "profit_factor": tw / tl if tl > 0 else 99,
        "avg_win": np.mean(wins) if wins else 0,
        "avg_loss": np.mean(losses) if losses else 0,
        "max_dd": dd.max(),
        "avg_bars_since_spike": avg_bars,
        "exit_reasons": reason_stats,
        "avg_hold": np.mean([r["count"] for r in reason_stats.values()]) if reason_stats else 0,
    }


# ─── Grid Search ───────────────────────────────────────────────
def grid_search(opens, highs, lows, closes, dpp):
    """Exhaustive grid search over parameters."""
    print("\n  GRID SEARCH: Testing all parameter combinations...")
    print(f"  {'Config':45s} {'T':>4s} {'WR%':>5s} {'PF':>5s} {'PnL$':>9s} {'DD%':>5s} {'Score':>6s}")
    print(f"  {'-'*45} {'-'*4} {'-'*5} {'-'*5} {'-'*9} {'-'*5} {'-'*6}")

    best_score = -999
    best_params = None
    best_result = None
    all_results = []

    total = 0
    for entry_bar in [3, 5, 7, 10]:
        for sl in [1, 1.5, 2, 3]:
            for tp in [2, 3, 5]:
                for max_hold in [8, 12, 18]:
                    for spike_thresh in [2.5, 3.0]:
                        for use_trailing in [False, True]:
                            total += 1
                            r = backtest_driftfade(
                                opens, highs, lows, closes,
                                entry_bar=entry_bar,
                                sl_pts=sl, tp_pts=tp,
                                max_hold=max_hold,
                                direction="down",
                                dpp=dpp,
                                spike_threshold=spike_thresh,
                                use_trailing=use_trailing,
                                trailing_activation=0.4,
                                trailing_distance=0.25,
                            )
                            if r["trades"] < 20:
                                continue

                            # Score: balance WR, PF, trade count, drawdown
                            score = (
                                r["profit_factor"]
                                * (r["win_rate"] / 100)
                                * np.log1p(r["trades"])
                                * (1 - r["max_dd"] / 100)
                            )

                            params = {
                                "entry_bar": entry_bar,
                                "sl": sl,
                                "tp": tp,
                                "max_hold": max_hold,
                                "spike_thresh": spike_thresh,
                                "trailing": use_trailing,
                            }

                            all_results.append((score, r, params))

                            if score > best_score:
                                best_score = score
                                best_result = r
                                best_params = params

    all_results.sort(key=lambda x: x[0], reverse=True)

    # Print top 20
    for i, (score, r, params) in enumerate(all_results[:20]):
        trail_str = "T" if params["trailing"] else " "
        config_str = f"e={params['entry_bar']:2d} SL={params['sl']:4.1f} TP={params['tp']:4.1f} h={params['max_hold']:2d} st={params['spike_thresh']:.1f} {trail_str}"
        print(f"  {config_str:45s} {r['trades']:4d} {r['win_rate']:5.1f} {r['profit_factor']:5.2f} "
              f"${r['total_pnl']:>+8.2f} {r['max_dd']:5.1f} {score:6.2f}")

    print(f"\n  Tested {total} combinations, {len(all_results)} had >= 30 trades")

    return best_params, best_result, all_results


# ─── Volatility-Adjusted Exits ─────────────────────────────────
def test_vol_adjusted(opens, highs, lows, closes, dpp, base_params):
    """Test volatility-adjusted exits on top configs."""
    print("\n  VOLATILITY-ADJUSTED EXITS:")
    print(f"  {'Window':>6s} {'T':>4s} {'WR%':>5s} {'PF':>5s} {'PnL$':>9s} {'DD%':>5s}")
    print(f"  {'-'*6} {'-'*4} {'-'*5} {'-'*5} {'-'*9} {'-'*5}")

    best = None
    best_score = -999

    for vol_window in [50, 100, 200, 300, 500]:
        r = backtest_driftfade(
            opens, highs, lows, closes,
            entry_bar=base_params["entry_bar"],
            sl_pts=base_params["sl"],
            tp_pts=base_params["tp"],
            max_hold=base_params["max_hold"],
            direction="down",
            dpp=dpp,
            spike_threshold=base_params["spike_thresh"],
            use_trailing=base_params["trailing"],
            trailing_activation=0.4,
            trailing_distance=0.25,
            use_vol_adjust=True,
            vol_window=vol_window,
        )
        if r["trades"] >= 20:
            score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"])
            marker = ""
            if score > best_score:
                best_score = score
                best = (vol_window, r)
                marker = " *"
            print(f"  {vol_window:>6d} {r['trades']:4d} {r['win_rate']:5.1f} {r['profit_factor']:5.2f} "
                  f"${r['total_pnl']:>+8.2f} {r['max_dd']:5.1f}{marker}")

    return best


# ─── Compounding Projection ────────────────────────────────────
def project_compounding(result, dpp, risk_pct=0.03):
    """Project returns with compounding."""
    if result["trades"] == 0:
        return

    trades_per_day = result["trades"] / 30
    avg_pnl = result["total_pnl"] / result["trades"]
    wr = result["win_rate"] / 100

    print(f"\n  COMPUNDING PROJECTION ({risk_pct*100:.0f}% risk per trade):")
    print(f"  {'Equity':>8s} {'Risk/Trade':>10s} {'Monthly':>10s} {'Annual':>12s}")
    print(f"  {'-'*40}")

    for equity in [10, 20, 50, 100, 200, 500]:
        risk_dollars = equity * risk_pct
        # How many "units" of avg loss can we risk?
        min_loss = abs(result["avg_loss"]) if result["avg_loss"] != 0 else 1
        units = risk_dollars / min_loss if min_loss > 0 else 0

        monthly = trades_per_day * 30 * avg_pnl * units
        annual = monthly * 12

        status = "OK" if monthly > 0 else "NEG"
        print(f"  ${equity:>6d}  ${risk_dollars:>8.2f}  ${monthly:>+9.2f}  ${annual:>+11.2f}  {status}")


# ─── Main ──────────────────────────────────────────────────────
def main():
    print(f"\n{'='*72}")
    print(f"  DRIFTFADE OPTIMIZATION — CRASH1000")
    print(f"  30-day M1 data, $0.10/point")
    print(f"{'='*72}")

    # Load CRASH1000 data
    opens, highs, lows, closes = load_parquet("candles_crash1000_1min.parquet")
    dpp = 0.10

    print(f"\n  Data: {len(closes)} bars")
    print(f"  Price range: {closes.min():.4f} - {closes.max():.4f}")
    print(f"  Avg bar range: {np.mean(highs - lows):.4f}")

    # Show spike characteristics
    spikes = detect_spikes(closes, threshold=3.0)
    print(f"  Spikes (3x median): {len(spikes)}")
    if len(spikes) > 1:
        gaps = np.diff(spikes)
        print(f"  Avg gap between spikes: {np.mean(gaps):.0f} bars")
        print(f"  Median gap: {np.median(gaps):.0f} bars")

    # Grid search
    t0 = time.time()
    best_params, best_result, all_results = grid_search(opens, highs, lows, closes, dpp)
    elapsed = time.time() - t0
    print(f"\n  Grid search took {elapsed:.1f}s")

    if best_params:
        print(f"\n  {'='*72}")
        print(f"  BEST CONFIGURATION")
        print(f"  {'='*72}")
        print(f"  Entry bar:      {best_params['entry_bar']}")
        print(f"  Stop loss:      {best_params['sl']} pts")
        print(f"  Take profit:    {best_params['tp']} pts")
        print(f"  Max hold:       {best_params['max_hold']} bars")
        print(f"  Spike threshold:{best_params['spike_thresh']}x median")
        print(f"  Trailing stop:  {'Yes' if best_params['trailing'] else 'No'}")

        print(f"\n  Performance:")
        print(f"    Trades:       {best_result['trades']}")
        print(f"    Win Rate:     {best_result['win_rate']:.1f}%")
        print(f"    Profit Factor:{best_result['profit_factor']:.2f}")
        print(f"    Total PnL:    ${best_result['total_pnl']:+.2f}")
        print(f"    Max Drawdown: {best_result['max_dd']:.2f}%")
        print(f"    Avg Win:      ${best_result['avg_win']:+.2f}")
        print(f"    Avg Loss:     ${best_result['avg_loss']:+.2f}")
        print(f"    Avg R:R:      1:{best_result['avg_win']/abs(best_result['avg_loss']):.1f}")

        print(f"\n  Exit Reasons:")
        for reason, stats in sorted(best_result["exit_reasons"].items()):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            print(f"    {reason:15s}: {stats['count']:3d} trades, PnL=${stats['pnl']:+.2f}, WR={wr:.0f}%")

        # Vol-adjusted test
        vol_result = test_vol_adjusted(opens, highs, lows, closes, dpp, best_params)

        # Compounding projections
        print(f"\n  {'='*72}")
        print(f"  COMPOUNDING PROJECTIONS")
        print(f"  {'='*72}")
        project_compounding(best_result, dpp, risk_pct=0.02)
        project_compounding(best_result, dpp, risk_pct=0.03)
        project_compounding(best_result, dpp, risk_pct=0.05)

        # Also test on CRASH500 with same params
        print(f"\n  {'='*72}")
        print(f"  SAME PARAMS ON CRASH500 (for comparison)")
        print(f"  {'='*72}")
        try:
            c5_opens, c5_highs, c5_lows, c5_closes = load_parquet("candles_crash500_1min.parquet")
            r5 = backtest_driftfade(
                c5_opens, c5_highs, c5_lows, c5_closes,
                entry_bar=best_params["entry_bar"],
                sl_pts=best_params["sl"],
                tp_pts=best_params["tp"],
                max_hold=best_params["max_hold"],
                direction="down", dpp=0.20,
                spike_threshold=best_params["spike_thresh"],
                use_trailing=best_params["trailing"],
            )
            if r5["trades"] > 0:
                print(f"    Trades: {r5['trades']}, WR: {r5['win_rate']:.1f}%, PnL: ${r5['total_pnl']:+.2f}")
            else:
                print(f"    No trades with these params")
        except Exception as e:
            print(f"    Error: {e}")

    # Save best config
    output = {
        "symbol": "CRASH1000",
        "best_params": best_params,
        "best_result": {k: v for k, v in best_result.items() if k != "exit_reasons"},
        "exit_reasons": best_result.get("exit_reasons", {}),
        "top_10": [
            {"params": p, "trades": r["trades"], "wr": r["win_rate"],
             "pf": r["profit_factor"], "pnl": r["total_pnl"], "dd": r["max_dd"]}
            for s, r, p in all_results[:10]
        ],
    }
    with open("checkpoints_tiny/driftfade_crash1000_optimized.json", "w") as f:
        json.dump(output, f, indent=2, default=str)
    print(f"\n  Saved to checkpoints_tiny/driftfade_crash1000_optimized.json")

    print(f"\n{'='*72}\n")


if __name__ == "__main__":
    main()
