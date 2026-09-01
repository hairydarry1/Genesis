"""
eval/smc_tick_v2.py — Fast SMC + Tick Count combined backtest.

Uses M1 bars for SMC signals, pre-computes tick counts per M1 bar,
then filters SMC entries by tick count threshold.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks
)


def compute_ticks_per_m1bar(tick_prices, m1_epochs):
    """Pre-compute ticks-since-spike at each M1 bar."""
    # Detect spikes in tick data
    changes = np.abs(np.diff(tick_prices))
    positive = changes[changes > 0]
    median = np.median(positive)
    spike_threshold = median * 3.0
    spike_indices = set(np.where(changes > spike_threshold)[0].tolist())

    # Compute ticks since spike for each tick
    tick_count = np.zeros(len(tick_prices), dtype=int)
    count = 0
    for i in range(len(tick_prices)):
        if i in spike_indices:
            count = 0
        else:
            count += 1
        tick_count[i] = count

    # Map each M1 bar to its tick count (at bar close)
    # Each M1 bar covers ~60 ticks
    result = np.zeros(len(m1_epochs), dtype=int)
    for i, epoch in enumerate(m1_epochs):
        # Approximate tick index from epoch
        # tick_prices[tick_idx] ~ price at second tick_idx
        tick_idx = min(epoch, len(tick_count) - 1)
        result[i] = tick_count[tick_idx]

    return result


def run_backtest(symbol, candle_sym, tick_sym):
    """Run combined SMC + tick count backtest."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — SMC + TICK COUNT COMBINED")
    print(f"{'='*70}")

    # Load M1 data
    m1 = load_candles(candle_sym, "1min")
    h1 = load_candles(candle_sym, "60min")
    zones = detect_order_blocks(h1, threshold_mult=2.0 if "crash" in symbol.lower() else 2.5)

    # Load tick data
    tick_path = Path(f"data/ticks_{tick_sym}.npy")
    tick_prices = np.load(tick_path)

    # Pre-compute tick counts per M1 bar
    m1_epochs = np.array([b["epoch"] for b in m1])
    ticks_per_bar = compute_ticks_per_m1bar(tick_prices, m1_epochs)

    print(f"  M1 bars: {len(m1)}")
    print(f"  H1 zones: {len(zones)}")
    print(f"  Ticks: {len(tick_prices):,}")
    print(f"  Tick count range: {ticks_per_bar.min()} - {ticks_per_bar.max()}")
    print(f"  Bars with tick_count>500: {np.sum(ticks_per_bar > 500)}")
    print(f"  Bars with tick_count>700: {np.sum(ticks_per_bar > 700)}")

    is_boom = "boom" in symbol.lower()
    is_crash = "crash" in symbol.lower()

    # Run SMC strategy
    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=0.01,
        rsi_extreme_low=15 if is_boom else 10,
        rsi_extreme_high=85 if is_crash else 90,
        entry_slippage_pct=0.00011,
        use_scaling=False,
    )
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    # Collect all SMC entry signals with their bar index
    smc_entries = []
    for i, bar in enumerate(m1):
        result = smc.on_bar(bar, i)
        if result and result.get("action") == "open":
            smc_entries.append({
                "bar_idx": i,
                "price": result.get("entry_price", 0),
                "side": result.get("side", ""),
                "tick_count": int(ticks_per_bar[i]),
            })

    # Also run SMC on close to get exits
    smc_full = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc_full.set_h1_zones(zones)
    smc_full.precompute(m1)
    for i, bar in enumerate(m1):
        smc_full.on_bar(bar, i)

    smc_trades = smc_full.trade_log
    print(f"  SMC signals: {len(smc_entries)}")
    print(f"  SMC trades (full): {len(smc_trades)}")

    # Test different tick count thresholds
    print(f"\n  {'Threshold':>10s}  {'Trades':>7s}  {'WR':>6s}  {'PF':>6s}  {'PnL':>9s}  {'Ret':>8s}")
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*9}  {'-'*8}")

    # Baseline: all SMC trades
    n = len(smc_trades)
    if n > 0:
        pnls = [t["pnl"] for t in smc_trades]
        wins = sum(1 for p in pnls if p > 0)
        wr = wins / n * 100
        tw = sum(p for p in pnls if p > 0)
        tl = abs(sum(p for p in pnls if p < 0))
        pf = tw / tl if tl > 0 else 0
        ret = sum(pnls) / 10000 * 100
        print(f"  {'No filter':>10s}  {n:7d}  {wr:5.1f}%  {pf:5.2f}  ${sum(pnls):>+8.2f}  {ret:+7.2f}%")

    # With tick count filter
    best_pnl = -999
    best_thresh = 0
    best_result = None

    for threshold in [300, 400, 500, 550, 600, 700, 800]:
        # Filter SMC trades by tick count at entry bar
        filtered = []
        for trade in smc_trades:
            entry_bar = trade.get("bars_held", 0)
            # We need to find which bar the trade was entered
            # Use the trade's entry price to find the bar
            entry_price = trade["entry_price"]
            for entry in smc_entries:
                if abs(entry["price"] - entry_price) < 0.01 and entry["tick_count"] >= threshold:
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

        print(f"  {threshold:>10d}  {n:7d}  {wr:5.1f}%  {pf:5.2f}  ${pnl:>+8.2f}  {ret:+7.2f}%")

        if pnl > best_pnl:
            best_pnl = pnl
            best_thresh = threshold
            best_result = {"trades": n, "wr": wr, "pf": pf, "pnl": pnl}

    if best_result:
        print(f"\n  BEST: tick_count >= {best_thresh}")
        print(f"    Trades: {best_result['trades']}")
        print(f"    Win Rate: {best_result['wr']:.1f}%")
        print(f"    Profit Factor: {best_result['pf']:.2f}")
        print(f"    PnL: ${best_result['pnl']:+.2f}")

        # Real returns
        lot = 0.20
        risk_per = lot * 5
        win_per = lot * 15
        n = best_result["trades"]
        w = int(n * best_result["wr"] / 100)
        l = n - w
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
    print("  SMC + TICK COUNT COMBINED (FAST)")
    print("=" * 70)

    run_backtest("Boom 500 Index", "boom500", "boom_500_index")
    run_backtest("Crash 500 Index", "crash500", "crash_500_index")
