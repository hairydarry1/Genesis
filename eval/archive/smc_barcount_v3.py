"""
eval/smc_barcount_v3.py — SMC + Bar Count Combined Strategy

Counts M1 bars since last spike, uses as filter for SMC entries.
Simpler and faster than tick-level counting.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks
)


def detect_m1_spikes(closes, high_mult=3.0):
    """Detect spikes in M1 close prices."""
    changes = np.abs(np.diff(closes))
    positive = changes[changes > 0]
    if len(positive) == 0:
        return set()
    median = np.median(positive)
    threshold = median * high_mult
    return set(np.where(changes > threshold)[0].tolist())


def run_backtest(symbol, candle_sym):
    """Run combined SMC + bar count backtest."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — SMC + BAR COUNT COMBINED")
    print(f"{'='*70}")

    # Load data
    m1 = load_candles(candle_sym, "1min")
    h1 = load_candles(candle_sym, "60min")
    zones = detect_order_blocks(h1, threshold_mult=2.0 if "crash" in symbol.lower() else 2.5)

    closes = np.array([b["close"] for b in m1], dtype=float)
    highs = np.array([b["high"] for b in m1], dtype=float)
    lows = np.array([b["low"] for b in m1], dtype=float)

    print(f"  M1 bars: {len(m1)}")
    print(f"  H1 zones: {len(zones)}")

    # Detect spikes in M1 data
    spike_indices = detect_m1_spikes(closes, high_mult=3.0)
    print(f"  M1 spikes detected: {len(spike_indices)}")

    # Compute bars since last spike for each bar
    bars_since_spike = np.zeros(len(m1), dtype=int)
    count = 0
    for i in range(len(m1)):
        if (i - 1) in spike_indices:
            count = 0
        else:
            count += 1
        bars_since_spike[i] = count

    print(f"  Bars since spike range: {bars_since_spike.min()} - {bars_since_spike.max()}")
    print(f"  Bars with count>100: {np.sum(bars_since_spike > 100)}")
    print(f"  Bars with count>200: {np.sum(bars_since_spike > 200)}")
    print(f"  Bars with count>300: {np.sum(bars_since_spike > 300)}")

    # Run SMC strategy
    is_boom = "boom" in symbol.lower()
    is_crash = "crash" in symbol.lower()

    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=0.01,
        rsi_extreme_low=15 if is_boom else 10,
        rsi_extreme_high=85 if is_crash else 90,
        entry_slippage_pct=0.00011,
        use_scaling=False,
    )

    # Collect all SMC signals with their bar index
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    smc_entries = []
    for i, bar in enumerate(m1):
        result = smc.on_bar(bar, i)
        if result and result.get("action") == "open":
            smc_entries.append({
                "bar_idx": i,
                "price": result.get("entry_price", 0),
                "side": result.get("side", ""),
                "bars_since_spike": int(bars_since_spike[i]),
            })

    # Run full SMC for trade results
    smc_full = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc_full.set_h1_zones(zones)
    smc_full.precompute(m1)
    for i, bar in enumerate(m1):
        smc_full.on_bar(bar, i)

    smc_trades = smc_full.trade_log
    print(f"  SMC signals: {len(smc_entries)}")
    print(f"  SMC trades: {len(smc_trades)}")

    # Test different thresholds
    print(f"\n  {'Threshold':>10s}  {'Trades':>7s}  {'WR':>6s}  {'PF':>6s}  {'PnL':>9s}  {'Ret':>8s}")
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*9}  {'-'*8}")

    # Baseline
    n = len(smc_trades)
    if n > 0:
        pnls = [t["pnl"] for t in smc_trades]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100
        tw = sum(p for p in pnls if p > 0)
        tl = abs(sum(p for p in pnls if p < 0))
        pf = tw / tl if tl > 0 else 0
        pnl = sum(pnls)
        ret = pnl / 10000 * 100
        print(f"  {'No filter':>10s}  {n:7d}  {wr:5.1f}%  {pf:5.2f}  ${pnl:>+8.2f}  {ret:+7.2f}%")

    best_pnl = -999
    best_thresh = 0

    for threshold in [50, 100, 150, 200, 250, 300, 400, 500]:
        # Match SMC trades to entries with bar count
        filtered = []
        for trade in smc_trades:
            entry_price = trade["entry_price"]
            for entry in smc_entries:
                if abs(entry["price"] - entry_price) < 0.01 and entry["bars_since_spike"] >= threshold:
                    filtered.append(trade)
                    break

        if len(filtered) == 0:
            print(f"  {threshold:>10d}  {'0':>7s}")
            continue

        pnls = [t["pnl"] for t in filtered]
        wins = sum(1 for p in pnls if p > 0)
        n = len(filtered)
        wr = wins / n * 100 if n > 0 else 0
        tw = sum(p for p in pnls if p > 0)
        tl = abs(sum(p for p in pnls if p < 0))
        pf = tw / tl if tl > 0 else 0
        pnl = sum(pnls)
        ret = pnl / 10000 * 100

        marker = ""
        if pnl > best_pnl:
            best_pnl = pnl
            best_thresh = threshold
            marker = " *"

        print(f"  {threshold:>10d}  {n:7d}  {wr:5.1f}%  {pf:5.2f}  ${pnl:>+8.2f}  {ret:+7.2f}%{marker}")

    # Best result
    if best_pnl > -999:
        # Re-run to get best stats
        filtered = []
        for trade in smc_trades:
            entry_price = trade["entry_price"]
            for entry in smc_entries:
                if abs(entry["price"] - entry_price) < 0.01 and entry["bars_since_spike"] >= best_thresh:
                    filtered.append(trade)
                    break

        if filtered:
            pnls = [t["pnl"] for t in filtered]
            n = len(filtered)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / n * 100
            tw = sum(p for p in pnls if p > 0)
            tl = abs(sum(p for p in pnls if p < 0))
            pf = tw / tl if tl > 0 else 0

            print(f"\n  BEST: bar_count >= {best_thresh}")
            print(f"    Trades: {n}")
            print(f"    Win Rate: {wr:.1f}%")
            print(f"    Profit Factor: {pf:.2f}")
            print(f"    PnL: ${best_pnl:+.2f}")

            # Real returns
            lot = 0.20
            risk_per = lot * 5
            win_per = lot * 15
            w = wins
            l = n - wins
            monthly_pnl = w * win_per - l * risk_per

            print(f"    Risk/trade: ${risk_per:.2f}  Win/trade: ${win_per:.2f}")
            print(f"    Estimated Monthly: ${monthly_pnl:.2f}")
            print(f"    Estimated Annual: ${monthly_pnl * 12:.2f}")

            for acc in [50, 100, 200, 500]:
                rpct = risk_per / acc * 100
                v = "SAFE" if rpct <= 2 else "RISKY" if rpct <= 5 else "DANGER"
                print(f"    ${acc:4d} acc: risk={rpct:.1f}%  monthly=${monthly_pnl:+.2f}  annual=${monthly_pnl*12:+.2f}  [{v}]")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SMC + BAR COUNT COMBINED STRATEGY")
    print("=" * 70)

    run_backtest("Boom 500 Index", "boom500")
    run_backtest("Crash 500 Index", "crash500")
