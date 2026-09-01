"""
eval/backtest_combined.py — Combined backtest: Drift Fade + SMC + Bar Count filters.

Runs each strategy individually and combined on 30-day M1 data for all BOOM/CRASH variants.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles, detect_order_blocks,
    compute_rsi, compute_ema, compute_sma, price_in_zone, OrderBlock
)

# ─── Configuration ────────────────────────────────────────────
SYMBOLS = {
    "BOOM500":  {"mt5": "boom500",  "direction": "up",   "dpp": 0.20, "spike_dir": "up",   "is_boom": True},
    "CRASH500": {"mt5": "crash500", "direction": "down", "dpp": 0.20, "spike_dir": "down", "is_boom": False},
    "BOOM1000": {"mt5": "boom1000", "direction": "up",   "dpp": 0.10, "spike_dir": "up",   "is_boom": True},
    "CRASH1000":{"mt5": "crash1000","direction": "down", "dpp": 0.10, "spike_dir": "down", "is_boom": False},
}


# ─── DRIFT FADE STRATEGY ─────────────────────────────────────
def detect_spikes_m1(closes: np.ndarray, threshold: float = 3.0) -> np.ndarray:
    """Detect spikes on M1 by bar range vs median."""
    n = len(closes)
    ranges = np.zeros(n)
    for i in range(1, n):
        ranges[i] = abs(closes[i] - closes[i-1])
    ranges[0] = ranges[1] if n > 1 else 0
    med = np.median(ranges[ranges > 0])
    return np.where(ranges > med * threshold)[0]


def run_drift_fade(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                    entry_bar: int, sl: float, tp: float, max_hold: int,
                    direction: str, dpp: float) -> dict:
    """
    Drift Fade: After a spike, enter counter-drift N bars later.
    direction='up' means spikes go UP, we SELL (fade).
    direction='down' means spikes go DOWN, we BUY (fade).
    """
    n = len(closes)
    spikes = detect_spikes_m1(closes)
    spike_set = set(spikes.tolist())
    last_spike = -1

    in_trade = False
    entry_price = 0.0
    entry_idx = 0

    pnls = []
    reasons = []

    for i in range(n):
        if i in spike_set:
            last_spike = i

        bars_since = i - last_spike if last_spike >= 0 else 999

        if in_trade:
            bars_held = i - entry_idx

            if direction == "down":  # BUY after down-spike
                hit_tp = (highs[i] - entry_price) >= tp
                hit_sl = (lows[i] - entry_price) <= -sl
                unrealized = closes[i] - entry_price
            else:  # SELL after up-spike
                hit_tp = (entry_price - lows[i]) >= tp
                hit_sl = (highs[i] - entry_price) <= -sl
                unrealized = entry_price - closes[i]

            exit_pts = None
            reason = None

            if hit_tp:
                exit_pts = tp
                reason = "tp"
            elif hit_sl:
                exit_pts = -sl
                reason = "sl"
            elif i in spike_set:
                exit_pts = unrealized
                reason = "spike_against"
            elif bars_held >= max_hold:
                exit_pts = unrealized
                reason = "timeout"

            if exit_pts is not None:
                pnl_dollars = exit_pts * dpp
                pnls.append(pnl_dollars)
                reasons.append(reason)
                in_trade = False

        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = closes[i]
            entry_idx = i
            in_trade = True

    if in_trade:
        if direction == "down":
            pnls.append((closes[-1] - entry_price) * dpp)
        else:
            pnls.append((entry_price - closes[-1]) * dpp)
        reasons.append("eod")

    return _summarize(pnls, reasons, "DriftFade")


# ─── SMC STRATEGY ─────────────────────────────────────────────
def run_smc(symbol: str, m1_candles: list, h1_candles: list,
            sl_pct: float = 0.005, risk: float = 0.01) -> dict:
    """Run SMC Spike Hunter backtest."""
    zone_mult = 2.0 if "crash" in symbol.lower() else 2.5
    zones = detect_order_blocks(h1_candles, threshold_mult=zone_mult)

    cfg = SMCHunterConfig(
        sl_pct=sl_pct, risk_per_trade=risk,
        rsi_extreme_low=15 if "boom" in symbol.lower() else 10,
        rsi_extreme_high=85 if "crash" in symbol.lower() else 90,
        entry_slippage_pct=0.00011, use_scaling=False,
    )
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1_candles)

    for i, bar in enumerate(m1_candles):
        smc.on_bar(bar, i)

    if smc.position != 0:
        smc._close_position(m1_candles[-1]["close"], "eod", m1_candles[-1]["close"])

    pnls = [t["pnl"] for t in smc.trade_log]
    reasons = [t["exit_reason"] for t in smc.trade_log]
    return _summarize(pnls, reasons, "SMC")


# ─── BAR COUNT FILTER ─────────────────────────────────────────
def compute_bars_since_spike(closes: np.ndarray, spike_mult: float = 3.0) -> np.ndarray:
    """For each bar, count bars since last spike."""
    n = len(closes)
    spikes = detect_spikes_m1(closes, threshold=spike_mult)
    spike_set = set(spikes.tolist())

    bars_since = np.zeros(n, dtype=int)
    count = 0
    for i in range(n):
        if (i - 1) in spike_set:
            count = 0
        else:
            count += 1
        bars_since[i] = count
    return bars_since


def apply_bar_count_filter(trades: list, bars_since_spike: np.ndarray,
                           min_bars: int = 5, price_tolerance: float = 0.02) -> list:
    """Filter trades: only keep entries that happen >= min_bars after last spike."""
    filtered = []
    for t in trades:
        entry_price = t.get("entry_price", 0)
        # Find the closest bar index by price matching
        if entry_price == 0:
            filtered.append(t)
            continue
        diffs = np.abs(closes_arr - entry_price)
        idx = np.argmin(diffs)
        if bars_since_spike[idx] >= min_bars:
            filtered.append(t)
    return filtered


# ─── COMBINED STRATEGY ────────────────────────────────────────
def run_combined(symbol: str, m1_candles: list, h1_candles: list,
                 bars_since: np.ndarray, min_bars: int = 5) -> dict:
    """Combined: SMC entries filtered by bar count + drift fade as secondary."""
    closes = np.array([b["close"] for b in m1_candles], dtype=float)
    highs = np.array([b["high"] for b in m1_candles], dtype=float)
    lows = np.array([b["low"] for b in m1_candles], dtype=float)

    zone_mult = 2.0 if "crash" in symbol.lower() else 2.5
    zones = detect_order_blocks(h1_candles, threshold_mult=zone_mult)

    # Run SMC with full tracking
    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=0.01,
        rsi_extreme_low=15 if "boom" in symbol.lower() else 10,
        rsi_extreme_high=85 if "crash" in symbol.lower() else 90,
        entry_slippage_pct=0.00011, use_scaling=False,
    )

    # SMC entries
    smc_entries = []
    smc_tracker = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc_tracker.set_h1_zones(zones)
    smc_tracker.precompute(m1_candles)

    in_trade = False
    entry_price = 0.0
    entry_idx = 0
    trade_pnls = []
    trade_reasons = []

    for i, bar in enumerate(m1_candles):
        h, l, c = bar["high"], bar["low"], bar["close"]

        # Check exits
        if in_trade:
            bars_held = i - entry_idx
            if entry_price > 0:
                if "boom" in symbol.lower():  # long
                    hit_tp = (h - entry_price) >= entry_price * 0.005
                    hit_sl = (l - entry_price) <= -entry_price * 0.005
                    unrealized = c - entry_price
                else:  # short
                    hit_tp = (entry_price - l) >= entry_price * 0.005
                    hit_sl = (h - entry_price) <= entry_price * 0.005
                    unrealized = entry_price - c

                if hit_tp:
                    trade_pnls.append(entry_price * 0.005 * SYMBOLS[symbol]["dpp"])
                    trade_reasons.append("tp")
                    in_trade = False
                elif hit_sl:
                    trade_pnls.append(-entry_price * 0.005 * SYMBOLS[symbol]["dpp"])
                    trade_reasons.append("sl")
                    in_trade = False
                elif bars_held >= 60:
                    trade_pnls.append(unrealized * SYMBOLS[symbol]["dpp"])
                    trade_reasons.append("timeout")
                    in_trade = False

        # Check entry
        if not in_trade:
            result = smc_tracker.on_bar(bar, i)
            if result and result.get("action") == "open":
                # BAR COUNT FILTER: only enter if >= min_bars since last spike
                if bars_since[i] >= min_bars:
                    in_trade = True
                    entry_price = result["entry_price"]
                    entry_idx = i
                    smc_entries.append(i)

    if in_trade:
        c = m1_candles[-1]["close"]
        if "boom" in symbol.lower():
            trade_pnls.append((c - entry_price) * SYMBOLS[symbol]["dpp"])
        else:
            trade_pnls.append((entry_price - c) * SYMBOLS[symbol]["dpp"])
        trade_reasons.append("eod")

    return _summarize(trade_pnls, trade_reasons, f"Combined(bar>={min_bars})")


# ─── UTILITIES ─────────────────────────────────────────────────
def _summarize(pnls: list, reasons: list, strategy: str) -> dict:
    """Summarize trade results."""
    if not pnls:
        return {"strategy": strategy, "trades": 0}

    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    reason_counts = {}
    for r in reasons:
        if r not in reason_counts:
            reason_counts[r] = {"count": 0, "pnl": 0}
        reason_counts[r]["count"] += 1

    tw = sum(wins) if wins else 0
    tl = abs(sum(losses)) if losses else 0

    # Equity curve
    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100

    return {
        "strategy": strategy,
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
        "exit_reasons": reason_counts,
    }


def print_result(r: dict, dpp: float):
    """Print formatted result."""
    if r["trades"] == 0:
        print(f"  {r['strategy']:30s}  No trades")
        return

    print(f"  {r['strategy']:30s}  {r['trades']:3d}T  "
          f"WR={r['win_rate']:5.1f}%  PF={r['profit_factor']:5.2f}  "
          f"PnL=${r['total_pnl']:>+8.2f}  "
          f"DD={r['max_dd']:5.1f}%  "
          f"R:R=1:{r['avg_rr']:.1f}")

    if r["exit_reasons"]:
        reasons_str = "  ".join(f"{k}:{v['count']}" for k, v in sorted(r["exit_reasons"].items()))
        print(f"    Exits: {reasons_str}")


# ─── MAIN ──────────────────────────────────────────────────────
def main():
    print(f"\n{'='*72}")
    print(f"  COMBINED BACKTEST - Drift Fade + SMC + Bar Count")
    print(f"  30-day M1 data from MT5")
    print(f"{'='*72}")

    for symbol, cfg in SYMBOLS.items():
        mt5_name = cfg["mt5"]
        m1_path = f"data/candles_{mt5_name}_1min.parquet"
        h1_path = f"data/candles_{mt5_name}_60min.parquet"

        if not Path(m1_path).exists():
            print(f"\n  {symbol}: Data not found, skipping")
            continue

        # Load data
        df = pd.read_parquet(m1_path)
        closes = df["close"].values.astype(float)
        highs = df["high"].values.astype(float)
        lows = df["low"].values.astype(float)

        m1_candles = []
        for i in range(len(df)):
            m1_candles.append({
                "epoch": int(df["time"].values[i]) if "time" in df.columns else i * 60,
                "open": float(df["open"].values[i]),
                "high": float(df["high"].values[i]),
                "low": float(df["low"].values[i]),
                "close": float(df["close"].values[i]),
            })

        # Load H1
        h1_candles = []
        if Path(h1_path).exists():
            h1_df = pd.read_parquet(h1_path)
            for i in range(len(h1_df)):
                h1_candles.append({
                    "epoch": int(h1_df["time"].values[i]) if "time" in h1_df.columns else i * 3600,
                    "open": float(h1_df["open"].values[i]),
                    "high": float(h1_df["high"].values[i]),
                    "low": float(h1_df["low"].values[i]),
                    "close": float(h1_df["close"].values[i]),
                })

        # Bar count
        bars_since = compute_bars_since_spike(closes)

        print(f"\n  {'-'*70}")
        print(f"  {symbol}: {len(df)} bars, {len(h1_candles)} H1 bars, "
              f"${cfg['dpp']}/point, {cfg['direction']}-spike")
        print(f"  {'-'*70}")

        # ── Strategy 1: Drift Fade alone ──
        best_drift = None
        for entry_bar in [3, 5, 8, 10, 15]:
            for sl in [2, 3, 5]:
                for tp in [3, 5, 7, 10]:
                    for max_hold in [entry_bar * 2, entry_bar * 3]:
                        r = run_drift_fade(closes, highs, lows, entry_bar, sl, tp,
                                          max_hold, cfg["direction"], cfg["dpp"])
                        if r["trades"] >= 5:
                            score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"]) * (1 - r["max_dd"]/100)
                            if best_drift is None or score > best_drift[1]:
                                best_drift = (r, score)

        print(f"\n  [1] DRIFT FADE (best config):")
        if best_drift:
            print_result(best_drift[0], cfg["dpp"])
        else:
            print(f"  No viable drift fade configs found")

        # ── Strategy 2: SMC alone ──
        print(f"\n  [2] SMC SPIKE HUNTER:")
        smc_result = run_smc(symbol, m1_candles, h1_candles)
        print_result(smc_result, cfg["dpp"])

        # ── Strategy 3: Combined (SMC + bar count filter) ──
        print(f"\n  [3] COMBINED (SMC + Bar Count Filter):")
        best_combined = None
        for min_bars in [3, 5, 8, 10, 15, 20]:
            r = run_combined(symbol, m1_candles, h1_candles, bars_since, min_bars=min_bars)
            if r["trades"] >= 3:
                score = r["profit_factor"] * (r["win_rate"]/100) * np.log1p(r["trades"]) * (1 - r["max_dd"]/100)
                if best_combined is None or score > best_combined[1]:
                    best_combined = (r, min_bars, score)

        if best_combined:
            print_result(best_combined[0], cfg["dpp"])
            print(f"    Best filter: bars_since_spike >= {best_combined[1]}")
        else:
            print(f"  No viable combined configs found")

        # ── Bar count distribution ──
        print(f"\n  Bar Count Distribution:")
        for lo, hi in [(0,5), (5,10), (10,15), (15,20), (20,30), (30,50), (50,100)]:
            c = np.sum((bars_since >= lo) & (bars_since < hi))
            if c > 0:
                print(f"    {lo:3d}-{hi:3d} bars: {c:5d} ({c/len(bars_since)*100:.1f}%)")

        # ── Summary ──
        print(f"\n  {'='*50}")
        print(f"  SUMMARY: {symbol}")
        print(f"  {'='*50}")
        results = []
        if best_drift:
            results.append(best_drift[0])
        results.append(smc_result)
        if best_combined:
            results.append(best_combined[0])

        for r in results:
            if r["trades"] > 0:
                print(f"    {r['strategy']:30s}  {r['trades']:3d}T  WR={r['win_rate']:.1f}%  "
                      f"PnL=${r['total_pnl']:+.2f}")

    print(f"\n{'='*72}")
    print(f"  DONE")
    print(f"{'='*72}\n")


if __name__ == "__main__":
    main()
