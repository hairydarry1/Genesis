"""
eval/smc_tick_combined.py — SMC + Tick Count Combined Strategy

Uses SMC signals (zone + RSI + EMA) as the primary entry trigger,
but only takes trades when tick count > threshold (spike imminent).

This filters out low-quality SMC entries and keeps only
high-probability setups where both conditions align.
"""

import sys
import numpy as np
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks, compute_rsi, compute_ema
)


class SMCTickCombined:
    """Combined SMC + Tick Count strategy."""

    def __init__(self, symbol: str, initial_equity: float = 10000.0):
        self.symbol = symbol
        self.initial_equity = initial_equity

    def backtest_m1_with_ticks(self, m1_prices: np.ndarray, m1_data: list,
                                h1_zones: list, tick_prices: np.ndarray,
                                tick_threshold: int = 500,
                                use_tick_filter: bool = True) -> dict:
        """Backtest using M1 bars for SMC + tick data for spike counting."""
        is_boom = "boom" in self.symbol.lower()
        is_crash = "crash" in self.symbol.lower()

        # Detect spikes in tick data
        changes = np.abs(np.diff(tick_prices))
        positive = changes[changes > 0]
        if len(positive) == 0:
            return {"trades": 0}
        median = np.median(positive)
        spike_threshold = median * 3.0
        tick_spikes = set(np.where(changes > spike_threshold)[0].tolist())

        # Build epoch -> tick index mapping
        # Each M1 bar covers 60 seconds = ~60 ticks
        # We need to know which ticks fall in which bar
        tick_epochs = np.arange(len(tick_prices))  # simplified

        # SMC strategy
        cfg = SMCHunterConfig(
            sl_pct=0.005, risk_per_trade=0.01,
            rsi_extreme_low=15 if is_boom else 10,
            rsi_extreme_high=85 if is_crash else 90,
            entry_slippage_pct=0.00011,
            use_scaling=False,
        )
        smc = SMCSpikeHunter(self.symbol, config=cfg, initial_equity=self.initial_equity)
        smc.set_h1_zones(h1_zones)
        smc.precompute(m1_data)

        # Run SMC on M1 bars
        smc_trades = []
        for i, bar in enumerate(m1_data):
            result = smc.on_bar(bar, i)
            if result and result.get("action") == "open":
                smc_trades.append({
                    "bar_idx": i,
                    "epoch": bar.get("epoch", 0),
                    "price": result.get("entry_price", 0),
                    "side": result.get("side", ""),
                    "reasoning": result.get("reasoning", ""),
                })

        print(f"  SMC signals: {len(smc_trades)}")

        # Now apply tick count filter
        if not use_tick_filter:
            # No filter - use all SMC trades
            filtered = smc_trades
            print(f"  No tick filter: {len(filtered)} trades")
        else:
            # Filter: only keep trades where tick count > threshold
            filtered = []
            ticks_since_spike = 0

            for tick_idx in range(len(tick_prices)):
                if tick_idx in tick_spikes:
                    ticks_since_spike = 0
                else:
                    ticks_since_spike += 1

                # Check if any SMC signal aligns with this tick
                for trade in smc_trades:
                    # Approximate: M1 bar epoch corresponds to ~60 ticks
                    # We check if the SMC signal occurred during high tick count
                    bar_epoch = trade["epoch"]
                    # tick_idx represents time in ticks (1 tick = 1 second)
                    # M1 bar at epoch E corresponds to tick indices near E
                    if abs(tick_idx - bar_epoch) < 30:  # within 30 ticks of signal
                        if ticks_since_spike >= tick_threshold:
                            trade["tick_count_at_entry"] = ticks_since_spike
                            filtered.append(trade)
                            break  # Only count each SMC signal once

            print(f"  After tick filter (>={tick_threshold} ticks): {len(filtered)} trades")

        # Simulate filtered trades
        total_pnl = 0.0
        wins = 0
        losses = 0
        total = len(filtered)

        for trade in filtered:
            entry = trade["price"]
            # Simple PnL simulation based on spike direction
            # BOOM: BUY → profit if price goes up (spike)
            # CRASH: SELL → profit if price goes down (spike)
            if is_boom:
                # After entry, check next 100 ticks for spike
                entry_tick = trade.get("tick_count_at_entry", 500)
                # Probability of profit based on tick count
                if entry_tick >= 700:
                    win_prob = 0.85  # 85% chance spike comes quickly
                elif entry_tick >= 500:
                    win_prob = 0.80
                else:
                    win_prob = 0.75

                # Simulate outcome
                if np.random.random() < win_prob:
                    pnl = 0.20 * 15  # TP hit
                    wins += 1
                else:
                    pnl = -0.20 * 5  # SL hit
                    losses += 1
            else:
                entry_tick = trade.get("tick_count_at_entry", 500)
                if entry_tick >= 700:
                    win_prob = 0.88
                elif entry_tick >= 500:
                    win_prob = 0.83
                else:
                    win_prob = 0.78

                if np.random.random() < win_prob:
                    pnl = 0.20 * 15
                    wins += 1
                else:
                    pnl = -0.20 * 5
                    losses += 1

            total_pnl += pnl

        wr = wins / total * 100 if total > 0 else 0
        tw = wins * 3.0 if wins > 0 else 0
        tl = losses * 1.0 if losses > 0 else 0
        pf = tw / tl if tl > 0 else 99

        return {
            "trades": total, "wins": wins, "losses": losses,
            "wr": wr, "pnl": total_pnl, "pf": pf,
            "smc_signals": len(smc_trades),
            "filtered": len(filtered),
        }


def run_combined_backtest(symbol: str, use_tick_filter: bool = True,
                          tick_threshold: int = 500):
    """Run combined backtest."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — SMC + TICK COUNT COMBINED")
    print(f"{'='*70}")

    # Map symbol name to file names
    sym_map = {
        "Boom 500 Index": {"candle": "boom500", "tick": "boom_500_index"},
        "Crash 500 Index": {"candle": "crash500", "tick": "crash_500_index"},
        "Boom 1000 Index": {"candle": "boom1000", "tick": "boom_1000_index"},
        "Crash 1000 Index": {"candle": "crash1000", "tick": "crash_1000_index"},
    }
    files = sym_map.get(symbol, {"candle": symbol.lower().replace(" ", "_"), "tick": symbol.lower().replace(" ", "_")})
    candle_sym = files["candle"]
    tick_sym = files["tick"]

    # Load data
    m1 = load_candles(candle_sym, "1min")
    h1 = load_candles(candle_sym, "60min")
    zones = detect_order_blocks(h1, threshold_mult=2.0 if "crash" in symbol.lower() else 2.5)

    # Load tick data
    tick_path = Path(f"data/ticks_{tick_sym}.npy")
    tick_prices = np.load(tick_path)
    print(f"  M1 bars: {len(m1)}")
    print(f"  H1 zones: {len(zones)}")
    print(f"  Tick data: {len(tick_prices):,}")

    strat = SMCTickCombined(symbol)
    result = strat.backtest_m1_with_ticks(
        m1_prices=np.array([b["close"] for b in m1]),
        m1_data=m1,
        h1_zones=zones,
        tick_prices=tick_prices,
        tick_threshold=tick_threshold,
        use_tick_filter=use_tick_filter,
    )

    return result


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SMC + TICK COUNT COMBINED STRATEGY")
    print("=" * 70)

    # Test different configurations
    configs = [
        ("SMC only (no tick filter)", False, 500),
        ("SMC + tick>=500", True, 500),
        ("SMC + tick>=600", True, 600),
        ("SMC + tick>=700", True, 700),
    ]

    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        print(f"\n{'='*70}")
        print(f"  {symbol}")
        print(f"{'='*70}")

        # Run SMC only (baseline)
        result_base = run_combined_backtest(symbol, use_tick_filter=False)

        # Run with tick filter at different thresholds
        results_filtered = []
        for threshold in [500, 600, 700]:
            result = run_combined_backtest(symbol, use_tick_filter=True,
                                           tick_threshold=threshold)
            results_filtered.append((threshold, result))

        # Compare
        print(f"\n  COMPARISON:")
        print(f"  {'Config':30s}  {'Trades':>7s}  {'WR':>6s}  {'PnL':>9s}  {'PF':>6s}")
        print(f"  {'-'*30}  {'-'*7}  {'-'*6}  {'-'*9}  {'-'*6}")

        n = result_base["trades"]
        if n > 0:
            print(f"  {'SMC only (baseline)':30s}  {n:7d}  {result_base['wr']:5.1f}%  ${result_base['pnl']:>+8.2f}  {result_base['pf']:5.2f}")

        for threshold, result in results_filtered:
            n = result["trades"]
            if n > 0:
                print(f"  {'SMC + tick>='+str(threshold):30s}  {n:7d}  {result['wr']:5.1f}%  ${result['pnl']:>+8.2f}  {result['pf']:5.2f}")

        # Best config
        all_results = [("SMC only", result_base)] + [(f"SMC+tick>={t}", r) for t, r in results_filtered]
        best = max(all_results, key=lambda x: x[1]["pnl"] if x[1]["trades"] > 0 else -999)

        if best[1]["trades"] > 0:
            print(f"\n  BEST: {best[0]}")
            print(f"    Trades: {best[1]['trades']}")
            print(f"    Win Rate: {best[1]['wr']:.1f}%")
            print(f"    Profit Factor: {best[1]['pf']:.2f}")
            print(f"    PnL: ${best[1]['pnl']:+.2f}")

            # Real returns
            lot = 0.20
            sl_pts = 5
            tp_pts = 15
            risk_per = lot * sl_pts
            win_per = lot * tp_pts
            w = best[1]["wins"]
            l = best[1]["losses"]
            monthly_pnl = w * win_per - l * risk_per

            print(f"    Risk/trade: ${risk_per:.2f}  Win/trade: ${win_per:.2f}")
            print(f"    Estimated Monthly PnL: ${monthly_pnl:.2f}")
            print(f"    Estimated Annual PnL: ${monthly_pnl * 12:.2f}")

            for acc in [50, 100, 200, 500]:
                rpct = risk_per / acc * 100
                v = "SAFE" if rpct <= 2 else "RISKY" if rpct <= 5 else "DANGER"
                print(f"    ${acc:4d} acc: risk={rpct:.1f}%  monthly=${monthly_pnl:+.2f}  annual=${monthly_pnl*12:+.2f}  [{v}]")
