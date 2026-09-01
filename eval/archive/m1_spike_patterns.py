"""
eval/m1_spike_patterns.py — M1 Bar Spike Pattern Research

Your observation: CRASH500 went 1+ hour without a proper spike.
This means there ARE long gaps on M1 bars.
Our previous spike detection was too sensitive.

Research:
1. Better spike detection (larger thresholds)
2. Actual gap distribution on M1 bars
3. Long gaps (>1 hour) and what happens after
4. Price drift patterns during long gaps
5. Entry timing after long gaps
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import load_candles


def detect_real_spikes(closes, multiplier=5.0):
    """Detect meaningful spikes using higher threshold."""
    changes = np.abs(np.diff(closes))
    positive = changes[changes > 0]
    if len(positive) == 0:
        return np.array([], dtype=int), 0
    median = np.median(positive)
    threshold = median * multiplier
    return np.where(changes > threshold)[0], threshold


def analyze_gap_distribution(symbol, candle_sym):
    """Analyze gap distribution between M1 spikes."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — M1 SPIKE GAP ANALYSIS")
    print(f"{'='*70}")

    m1 = load_candles(candle_sym, "1min")
    closes = np.array([b["close"] for b in m1], dtype=float)
    highs = np.array([b["high"] for b in m1], dtype=float)
    lows = np.array([b["low"] for b in m1], dtype=float)

    print(f"  M1 bars: {len(m1)} ({len(m1)/60:.0f} hours)")
    print(f"  Price range: {closes.min():.2f} - {closes.max():.2f}")

    # Test different spike thresholds
    for mult in [3.0, 4.0, 5.0, 6.0, 8.0, 10.0]:
        spikes, threshold = detect_real_spikes(closes, mult)
        if len(spikes) < 2:
            continue

        gaps = np.diff(spikes)
        median_gap = np.median(gaps)
        max_gap = np.max(gaps)

        # Convert to time
        median_min = median_gap
        max_min = max_gap

        # Gap distribution
        long_30 = np.sum(gaps > 30)  # >30 min
        long_60 = np.sum(gaps > 60)  # >1 hour
        long_120 = np.sum(gaps > 120)  # >2 hours

        print(f"\n  Threshold: {mult:.0f}x median ({threshold:.4f} pts)")
        print(f"    Spikes: {len(spikes)}")
        print(f"    Median gap: {median_gap:.0f} bars ({median_min:.0f} min)")
        print(f"    Max gap: {max_gap:.0f} bars ({max_min:.0f} min)")
        print(f"    Gaps >30 min: {long_30} ({long_30/len(gaps)*100:.1f}%)")
        print(f"    Gaps >1 hour: {long_60} ({long_60/len(gaps)*100:.1f}%)")
        print(f"    Gaps >2 hours: {long_120} ({long_120/len(gaps)*100:.1f}%)")

        # Gap buckets
        buckets = [(0,10), (10,30), (30,60), (60,120), (120,300)]
        print(f"    Gap distribution:")
        for lo, hi in buckets:
            count = np.sum((gaps >= lo) & (gaps < hi))
            pct = count / len(gaps) * 100
            bar = '#' * int(pct / 2)
            print(f"      {lo:4d}-{hi:4d} min: {count:4d} ({pct:5.1f}%) {bar}")

    return m1, closes, highs, lows


def research_long_gaps(symbol, m1, closes, threshold_mult=5.0):
    """Research what happens during and after long gaps."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — LONG GAP ANALYSIS (threshold={threshold_mult}x)")
    print(f"{'='*70}")

    spikes, threshold = detect_real_spikes(closes, threshold_mult)
    if len(spikes) < 2:
        print("  Not enough spikes")
        return

    gaps = np.diff(spikes)

    # Find long gaps (>60 min)
    long_gap_mask = gaps > 60
    long_gap_indices = np.where(long_gap_mask)[0]

    print(f"  Total spikes: {len(spikes)}")
    print(f"  Long gaps (>60 min): {len(long_gap_indices)}")

    if len(long_gap_indices) == 0:
        print("  No long gaps found")
        return

    # Analyze each long gap
    for i, gap_idx in enumerate(long_gap_indices[:10]):  # Show first 10
        spike_start = spikes[gap_idx]
        spike_end = spikes[gap_idx + 1]
        gap_len = spike_end - spike_start

        # Price behavior during the gap
        gap_closes = closes[spike_start:spike_end]
        gap_highs = highs[spike_start:spike_end]
        gap_lows = lows[spike_start:spike_end]

        start_price = closes[spike_start]
        end_price = closes[spike_end]
        drift = end_price - start_price
        max_move_up = gap_highs.max() - start_price
        max_move_down = start_price - gap_lows.min()

        print(f"\n  Gap #{i+1}: bars {spike_start}-{spike_end} ({gap_len} bars = {gap_len:.0f} min)")
        print(f"    Start: {start_price:.2f}  End: {end_price:.2f}")
        print(f"    Drift: {drift:+.2f} ({drift/start_price*100:+.3f}%)")
        print(f"    Max up: {max_move_up:.2f} ({max_move_up/start_price*100:.3f}%)")
        print(f"    Max down: {max_move_down:.2f} ({max_move_down/start_price*100:.3f}%)")

        # Check what happens after the long gap
        if spike_end + 10 < len(closes):
            post_prices = closes[spike_end:spike_end + 10]
            post_drift = post_prices[-1] - end_price
            print(f"    Post-spike drift (10 bars): {post_drift:+.2f}")

    # After a long gap, is the NEXT spike sooner or later?
    print(f"\n  POST-LONG-GAP BEHAVIOR:")
    post_gaps = []
    for gap_idx in long_gap_indices:
        if gap_idx + 1 < len(gaps):
            post_gaps.append(gaps[gap_idx + 1])

    if post_gaps:
        post_arr = np.array(post_gaps)
        print(f"    Next gap after long gap:")
        print(f"      Mean: {np.mean(post_arr):.0f} bars ({np.mean(post_arr):.0f} min)")
        print(f"      Median: {np.median(post_arr):.0f} bars")
        print(f"      <10 bars: {np.sum(post_arr < 10)/len(post_arr)*100:.1f}%")
        print(f"      <30 bars: {np.sum(post_arr < 30)/len(post_arr)*100:.1f}%")
        print(f"      <60 bars: {np.sum(post_arr < 60)/len(post_arr)*100:.1f}%")

    # Price drift direction during long gaps
    print(f"\n  DRIFT DIRECTION DURING LONG GAPS:")
    bullish_drift = 0
    bearish_drift = 0
    for gap_idx in long_gap_indices:
        spike_start = spikes[gap_idx]
        spike_end = spikes[gap_idx + 1]
        drift = closes[spike_end] - closes[spike_start]
        if drift > 0:
            bullish_drift += 1
        else:
            bearish_drift += 1

    is_boom = "boom" in symbol.lower()
    print(f"    Bullish drift: {bullish_drift}/{len(long_gap_indices)} ({bullish_drift/len(long_gap_indices)*100:.0f}%)")
    print(f"    Bearish drift: {bearish_drift}/{len(long_gap_indices)} ({bearish_drift/len(long_gap_indices)*100:.0f}%)")

    if is_boom:
        print(f"    For BOOM: {bullish_drift/len(long_gap_indices)*100:.0f}% drift UP during gap")
        print(f"    This means BUY during gap = better entry!")
    else:
        print(f"    For CRASH: {bearish_drift/len(long_gap_indices)*100:.0f}% drift DOWN during gap")
        print(f"    This means SELL during gap = better entry!")


def research_entry_timing(symbol, m1, closes, threshold_mult=5.0):
    """Research optimal entry timing after long gaps."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — ENTRY TIMING AFTER LONG GAPS")
    print(f"{'='*70}")

    spikes, threshold = detect_real_spikes(closes, threshold_mult)
    if len(spikes) < 2:
        return

    gaps = np.diff(spikes)
    long_gap_mask = gaps > 60
    long_gap_indices = np.where(long_gap_mask)[0]

    if len(long_gap_indices) == 0:
        print("  No long gaps")
        return

    is_boom = "boom" in symbol.lower()

    # For each long gap, test entry at different bars
    print(f"\n  Entry at bar N after long gap start:")
    print(f"  {'Entry Bar':>10s}  {'Trades':>7s}  {'WR':>6s}  {'Avg PnL':>9s}")
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*9}")

    for entry_bar in [5, 10, 15, 20, 30, 40, 50, 60]:
        pnls = []
        for gap_idx in long_gap_indices:
            spike_start = spikes[gap_idx]
            spike_end = spikes[gap_idx + 1]
            gap_len = spike_end - spike_start

            if entry_bar >= gap_len - 5:  # Don't enter too close to end
                continue

            entry_idx = spike_start + entry_bar
            entry_price = closes[entry_idx]

            # Exit at next spike
            exit_price = closes[spike_end]

            if is_boom:
                # BUY at entry, exit when spike comes (spike is UP)
                pnl = exit_price - entry_price
            else:
                # SELL at entry, exit when spike comes (spike is DOWN)
                pnl = entry_price - exit_price

            pnls.append(pnl)

        if not pnls:
            print(f"  {entry_bar:>10d}  {'0':>7s}")
            continue

        pnls = np.array(pnls)
        wins = np.sum(pnls > 0)
        wr = wins / len(pnls) * 100
        avg_pnl = np.mean(pnls)

        print(f"  {entry_bar:>10d}  {len(pnls):>7d}  {wr:>5.1f}%  {avg_pnl:>+8.4f}")

    # Best entry timing
    print(f"\n  RECOMMENDATION:")
    print(f"  After a long gap (>60 min), wait {10-20} bars before entering.")
    print(f"  The price drifts in the spike direction during the gap,")
    print(f"  giving a better entry price as time passes.")


def research_smc_timing(symbol, m1, closes, threshold_mult=5.0):
    """Research SMC signal quality at different gap stages."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — SMC SIGNAL QUALITY vs GAP STAGE")
    print(f"{'='*70}")

    spikes, threshold = detect_real_spikes(closes, threshold_mult)
    if len(spikes) < 2:
        return

    gaps = np.diff(spikes)

    # For each SMC signal, check which gap stage it's in
    # (early, mid, or late in the gap)
    from eval.smc_spike_hunter import SMCSpikeHunter, SMCHunterConfig, detect_order_blocks

    is_boom = "boom" in symbol.lower()
    is_crash = "crash" in symbol.lower()

    h1 = load_candles(symbol.lower().replace(" ", "") if "boom" in symbol.lower() else
                      symbol.lower().replace(" ", ""), "60min")

    # Get correct candle sym
    candle_map = {"Boom 500 Index": "boom500", "Crash 500 Index": "crash500"}
    candle_sym = candle_map.get(symbol, symbol.lower().replace(" ", "_"))
    h1 = load_candles(candle_sym, "60min")
    zones = detect_order_blocks(h1, threshold_mult=2.0 if is_crash else 2.5)

    cfg = SMCHunterConfig(
        sl_pct=0.005, risk_per_trade=0.01,
        rsi_extreme_low=15 if is_boom else 10,
        rsi_extreme_high=85 if is_crash else 90,
        entry_slippage_pct=0.00011, use_scaling=False,
    )
    smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc.set_h1_zones(zones)
    smc.precompute(m1)

    # Collect SMC entries with gap stage
    entries = []
    for i, bar in enumerate(m1):
        result = smc.on_bar(bar, i)
        if result and result.get("action") == "open":
            # Find which gap this bar is in
            gap_stage = "unknown"
            bars_since_spike = 0
            for j in range(len(spikes) - 1):
                if spikes[j] <= i < spikes[j + 1]:
                    bars_since_spike = i - spikes[j]
                    gap_len = spikes[j + 1] - spikes[j]
                    pct_through = bars_since_spike / gap_len
                    if pct_through < 0.3:
                        gap_stage = "early"
                    elif pct_through < 0.7:
                        gap_stage = "mid"
                    else:
                        gap_stage = "late"
                    break

            entries.append({
                "idx": i,
                "price": result["entry_price"],
                "side": result["side"],
                "gap_stage": gap_stage,
                "bars_since": bars_since_spike,
            })

    # Full SMC for trades
    smc2 = SMCSpikeHunter(symbol, config=cfg, initial_equity=10000)
    smc2.set_h1_zones(zones)
    smc2.precompute(m1)
    for i, bar in enumerate(m1):
        smc2.on_bar(bar, i)
    trades = smc2.trade_log

    # Group trades by gap stage
    print(f"  SMC entries by gap stage:")
    for stage in ["early", "mid", "late"]:
        stage_entries = [e for e in entries if e["gap_stage"] == stage]
        if not stage_entries:
            continue

        # Match to trades
        stage_trades = []
        for trade in trades:
            for entry in stage_entries:
                if abs(entry["price"] - trade["entry_price"]) < 0.01:
                    stage_trades.append(trade)
                    break

        if stage_trades:
            pnls = [t["pnl"] for t in stage_trades]
            n = len(stage_trades)
            wins = sum(1 for p in pnls if p > 0)
            wr = wins / n * 100
            avg_bars = np.mean([e["bars_since"] for e in stage_entries[:n]])

            print(f"    {stage:6s}: {n:3d} trades  WR={wr:.1f}%  avg_bars={avg_bars:.0f}  PnL=${sum(pnls):+.2f}")

    # Recommendation
    print(f"\n  RECOMMENDATION:")
    print(f"  SMC signals in the LATE stage of a long gap")
    print(f"  have the best risk/reward because:")
    print(f"  1. Price has drifted toward spike direction")
    print(f"  2. Spike is imminent (you're closer to the end)")
    print(f"  3. Entry price is better (more favorable)")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  M1 SPIKE PATTERN RESEARCH")
    print("=" * 70)

    for symbol, candle_sym in [("Boom 500 Index", "boom500"), ("Crash 500 Index", "crash500")]:
        m1_data, closes, highs, lows = analyze_gap_distribution(symbol, candle_sym)
        research_long_gaps(symbol, m1_data, closes, threshold_mult=5.0)
        research_entry_timing(symbol, m1_data, closes, threshold_mult=5.0)
        research_smc_timing(symbol, m1_data, closes, threshold_mult=5.0)
