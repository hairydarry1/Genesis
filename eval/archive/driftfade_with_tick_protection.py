"""
eval/driftfade_with_tick_protection.py — DriftFade + Tick Count Exit Protection

Architecture:
  ENTRY: DriftFade (enter after spike, fade the drift)
  EXIT PROTECTION: If bars since spike > threshold, EXIT early (spike imminent)

Logic:
  - We enter DriftFade 3 bars after a spike
  - While in trade, we count bars since LAST spike
  - If bars_since_spike reaches threshold (e.g., 15-20 bars), we EXIT
  - Reason: At 700+ ticks without spike, the next spike is statistically imminent
  - 700 ticks ≈ 700 seconds ≈ 12 minutes ≈ 12 bars on M1

This protects DriftFade profits from being wiped by the next spike.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def detect_spikes(closes: np.ndarray, threshold: float = 3.0) -> np.ndarray:
    """Detect spikes by bar range vs median."""
    n = len(closes)
    ranges = np.zeros(n)
    for i in range(1, n):
        ranges[i] = abs(closes[i] - closes[i-1])
    ranges[0] = ranges[1] if n > 1 else 0
    med = np.median(ranges[ranges > 0])
    return np.where(ranges > med * threshold)[0]


def backtest_driftfade_with_protection(
    closes: np.ndarray,
    highs: np.ndarray,
    lows: np.ndarray,
    entry_bar: int,
    sl_pts: float,
    tp_pts: float,
    max_hold: int,
    direction: str,
    dpp: float,
    spike_threshold: float = 3.0,
    # TICK COUNT PROTECTION
    use_tick_protection: bool = True,
    tick_protection_threshold: int = 15,  # Exit if bars_since_spike > this
    # TRAILING STOP
    use_trailing: bool = True,
    trailing_activation: float = 0.5,  # Activate after 50% of TP reached
    trailing_distance: float = 0.3,    # Trail 30% of TP behind best price
) -> dict:
    """
    DriftFade with tick count exit protection.
    
    direction='down' means spikes go DOWN, we BUY (fade) = LONG.
    """
    n = len(closes)
    spikes = detect_spikes(closes, threshold=spike_threshold)
    spike_set = set(spikes.tolist())
    last_spike = -999

    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    best_price = 0.0

    pnls = []
    reasons = []
    bars_since_at_entry = []

    for i in range(n):
        if i in spike_set:
            last_spike = i

        bars_since = i - last_spike if last_spike >= 0 else 999

        if in_trade:
            bars_held = i - entry_idx
            price = closes[i]

            # Direction: BUY (long) for crash fade
            unrealized = price - entry_price

            # Check exits (check high/low for intra-bar)
            hit_tp = (highs[i] - entry_price) >= tp_pts
            hit_sl = (lows[i] - entry_price) <= -sl_pts

            exit_pts = None
            reason = None

            if hit_tp:
                exit_pts = tp_pts
                reason = "tp"
            elif hit_sl:
                exit_pts = -sl_pts
                reason = "sl"
            elif i in spike_set and bars_held > 2:
                # New spike against us
                exit_pts = unrealized
                reason = "spike_against"
            elif bars_held >= max_hold:
                exit_pts = unrealized
                reason = "timeout"
            
            # TICK COUNT PROTECTION: Exit if spike imminent
            elif use_tick_protection and bars_since >= tick_protection_threshold:
                exit_pts = unrealized
                reason = "tick_protection"

            # Trailing stop
            if use_trailing and exit_pts is None and bars_held >= 3:
                if price > best_price:
                    best_price = price
                trail_level = best_price - tp_pts * trailing_distance
                if price <= trail_level and best_price > entry_price + tp_pts * trailing_activation:
                    exit_pts = price - entry_price
                    reason = "trailing"

            if exit_pts is not None:
                pnl_dollars = exit_pts * dpp
                pnls.append(pnl_dollars)
                reasons.append(reason)
                bars_since_at_entry.append(bars_since)
                in_trade = False

        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = closes[i]
            entry_idx = i
            best_price = closes[i]
            in_trade = True

    if in_trade:
        pnls.append((closes[-1] - entry_price) * dpp)
        reasons.append("eod")
        bars_since_at_entry.append(999)

    return _summarize(pnls, reasons, bars_since_at_entry)


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
        "avg_rr": np.mean(wins) / abs(np.mean(losses)) if losses and np.mean(losses) != 0 else 99,
        "exit_reasons": reason_stats,
    }


def main():
    print(f"\n{'='*72}")
    print(f"  DRIFTFADE + TICK COUNT EXIT PROTECTION")
    print(f"  30-day M1 data from MT5")
    print(f"{'='*72}")

    symbols = {
        "CRASH500": {"mt5": "crash500", "dpp": 0.20, "direction": "down"},
        "CRASH1000": {"mt5": "crash1000", "dpp": 0.10, "direction": "down"},
    }

    for symbol, cfg in symbols.items():
        mt5_name = cfg["mt5"]
        dpp = cfg["dpp"]
        direction = cfg["direction"]

        df = pd.read_parquet(f"data/candles_{mt5_name}_1min.parquet")
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        print(f"\n  {'='*70}")
        print(f"  {symbol} ({dpp}/point, {len(closes)} bars)")
        print(f"  {'='*70}")

        # ── Baseline: No protection ──
        print(f"\n  [1] BASELINE (no tick protection, no trailing):")
        r_base = backtest_driftfade_with_protection(
            closes, highs, lows,
            entry_bar=3, sl_pts=1, tp_pts=2, max_hold=12,
            direction=direction, dpp=dpp,
            use_tick_protection=False, use_trailing=False,
        )
        _print_result(r_base, "No protection")

        # ── With trailing only ──
        print(f"\n  [2] TRAILING ONLY (no tick protection):")
        r_trail = backtest_driftfade_with_protection(
            closes, highs, lows,
            entry_bar=3, sl_pts=1, tp_pts=2, max_hold=12,
            direction=direction, dpp=dpp,
            use_tick_protection=False, use_trailing=True,
        )
        _print_result(r_trail, "Trailing only")

        # ── With tick protection only ──
        print(f"\n  [3] TICK PROTECTION ONLY (no trailing):")
        best_tp = None
        best_score_tp = -999
        for threshold in [8, 10, 12, 15, 18, 20, 25, 30]:
            r = backtest_driftfade_with_protection(
                closes, highs, lows,
                entry_bar=3, sl_pts=1, tp_pts=2, max_hold=12,
                direction=direction, dpp=dpp,
                use_tick_protection=True, tick_protection_threshold=threshold,
                use_trailing=False,
            )
            if r["trades"] >= 20:
                score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"])
                if score > best_score_tp:
                    best_score_tp = score
                    best_tp = (threshold, r)
        if best_tp:
            _print_result(best_tp[1], f"Threshold={best_tp[0]}")

        # ── COMBINED: Tick protection + Trailing ──
        print(f"\n  [4] COMBINED (tick protection + trailing):")
        best_combined = None
        best_score_combined = -999
        for threshold in [8, 10, 12, 15, 18, 20, 25, 30]:
            r = backtest_driftfade_with_protection(
                closes, highs, lows,
                entry_bar=3, sl_pts=1, tp_pts=2, max_hold=12,
                direction=direction, dpp=dpp,
                use_tick_protection=True, tick_protection_threshold=threshold,
                use_trailing=True,
            )
            if r["trades"] >= 20:
                score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"])
                if score > best_score_combined:
                    best_score_combined = score
                    best_combined = (threshold, r)
        if best_combined:
            _print_result(best_combined[1], f"Threshold={best_combined[0]}")

        # ── Summary ──
        print(f"\n  {'='*50}")
        print(f"  SUMMARY: {symbol}")
        print(f"  {'='*50}")
        
        results = [
            ("No protection", r_base),
            ("Trailing only", r_trail),
        ]
        if best_tp:
            results.append((f"Tick prot={best_tp[0]}", best_tp[1]))
        if best_combined:
            results.append((f"Combined={best_combined[0]}", best_combined[1]))
        
        for label, r in results:
            if r["trades"] > 0:
                prot_saves = 0
                if "tick_protection" in r.get("exit_reasons", {}):
                    prot_saves = r["exit_reasons"]["tick_protection"]["pnl"]
                print(f"    {label:25s}  {r['trades']:4d}T  WR={r['win_rate']:.1f}%  "
                      f"PnL=${r['total_pnl']:+.2f}  DD={r['max_dd']:.1f}%"
                      f"{'  (saved $'+str(round(prot_saves,2))+')' if prot_saves > 0 else ''}")


def _print_result(r, label):
    """Print formatted result."""
    if r["trades"] == 0:
        print(f"    {label}: No trades")
        return

    print(f"    {label}")
    print(f"      Trades:    {r['trades']}")
    print(f"      Win Rate:  {r['win_rate']:.1f}%")
    print(f"      PF:        {r['profit_factor']:.2f}")
    print(f"      PnL:       ${r['total_pnl']:+.2f}")
    print(f"      Max DD:    {r['max_dd']:.1f}%")
    print(f"      Avg Win:   ${r['avg_win']:+.2f}")
    print(f"      Avg Loss:  ${r['avg_loss']:+.2f}")

    if r["exit_reasons"]:
        print(f"      Exits:")
        for reason, stats in sorted(r["exit_reasons"].items()):
            wr = stats["wins"] / stats["count"] * 100 if stats["count"] > 0 else 0
            marker = " [PROTECTION]" if reason == "tick_protection" else ""
            print(f"        {reason:15s}: {stats['count']:3d}T, PnL=${stats['pnl']:+.2f}, WR={wr:.0f}%{marker}")


if __name__ == "__main__":
    main()
