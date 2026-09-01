"""
eval/research_risks.py — Research all6 potential risks against actual data.

Analyzes:
1. Zone decay: how fast do H1 zones become invalid
2. Entry timing: tick vs bar close slippage
3. EMA crossover false signals
4. Spread widening during spikes
5. RSI partial vs closed bar accuracy
6. Re-entry after loss effectiveness
"""

import sys
import numpy as np
from pathlib import Path
from collections import defaultdict
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    load_candles, detect_order_blocks, compute_rsi, compute_ema,
    OrderBlock, SMCHunterConfig, SMCSpikeHunter, price_in_zone
)


def research_zone_decay():
    """RISK1: How fast do H1 zones become invalid?"""
    print("="*70)
    print("  RESEARCH 1: ZONE DECAY — How Fast Do H1 Zones Become Invalid?")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")
        h1 = load_candles(symbol, "60min")

        # Split H1 into two halves: first15 days zones, test on second15 days
        mid = len(h1) // 2
        h1_first = h1[:mid]
        h1_second = h1[mid:]

        # Detect zones from first half
        zones_first = detect_order_blocks(h1_first, threshold_mult=2.5)
        zones_second = detect_order_blocks(h1_second, threshold_mult=2.5)

        # Build M1 index by epoch
        m1_epochs = [b['epoch'] for b in m1]
        split_epoch = h1[mid]['epoch']

        # Find M1 index at the split point
        split_m1_idx = 0
        for i, e in enumerate(m1_epochs):
            if e >= split_epoch:
                split_m1_idx = i
                break

        # Test first-half zones on second-half price action
        second_half_m1 = m1[split_m1_idx:]
        zone_touches = 0
        zone_trades = 0
        zone_wins = 0
        zone_times = []  # How many bars until zone is touched

        for zone in zones_first:
            touched = False
            for i, bar in enumerate(second_half_m1):
                if zone.low <= bar['close'] <= zone.high:
                    touched = True
                    zone_touches += 1
                    zone_times.append(i)
                    # Did price bounce or break through?
                    if i + 10 < len(second_half_m1):
                        future_prices = [second_half_m1[j]['close'] for j in range(i, min(i+10, len(second_half_m1)))]
                        # For supply zone: did price drop after touching?
                        if zone.zone_type == 'supply':
                            if future_prices[-1] < bar['close']:
                                zone_wins += 1
                        else:  # demand zone
                            if future_prices[-1] > bar['close']:
                                zone_wins += 1
                        zone_trades += 1
                    break

        n_first = len(zones_first)
        n_second = len(zones_second)
        touch_rate = zone_touches / n_first * 100 if n_first > 0 else 0
        trade_rate = zone_wins / zone_trades * 100 if zone_trades > 0 else 0
        avg_bars = np.mean(zone_times) if zone_times else 0

        print(f"  First-half zones: {n_first}")
        print(f"  Second-half zones: {n_second} (new structure formed)")
        print(f"  First-half zones touched in second half: {zone_touches}/{n_first} ({touch_rate:.0f}%)")
        print(f"  Zone bounce trades (10-bar window): {zone_wins}/{zone_trades} ({trade_rate:.0f}% win)")
        print(f"  Avg bars until zone touch: {avg_bars:.0f} bars ({avg_bars/60:.1f} hours)")

        # Test full-period zones on each week
        all_zones = detect_order_blocks(h1, threshold_mult=2.5)
        weeks = 4
        bars_per_week = len(m1) // weeks

        print(f"\n  Zone validity by week (using full-period zones):")
        for w in range(weeks):
            week_start = w * bars_per_week
            week_end = min((w + 1) * bars_per_week, len(m1))
            week_m1 = m1[week_start:week_end]
            week_epoch_start = week_m1[0]['epoch']
            week_epoch_end = week_m1[-1]['epoch']

            touched = 0
            for zone in all_zones:
                # Zone was created before this week?                    if zone.origin_epoch < week_epoch_start:
                    for bar in week_m1:
                        if zone.low <= bar['close'] <= zone.high:
                            touched += 1
                            break

            old_zones = sum(1 for z in all_zones if z.origin_epoch < week_epoch_start)
            print(f"    Week {w+1}: {touched}/{old_zones} old zones touched ({touched/old_zones*100:.0f}%)" if old_zones > 0 else f"    Week {w+1}: no old zones")

    print()


def research_entry_timing():
    """RISK 2: Entry at bar close vs tick price — how much slippage?"""
    print("="*70)
    print("  RESEARCH 2: ENTRY TIMING — Bar Close vs Tick Slippage")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")

        # For each bar, compute the difference between:
        # - bar close price (backtest entry)
        # - worst tick in the bar (live entry if unlucky)
        # - best tick in the bar (live entry if lucky)
        close_prices = np.array([b['close'] for b in m1], dtype=float)
        high_prices = np.array([b['high'] for b in m1], dtype=float)
        low_prices = np.array([b['low'] for b in m1], dtype=float)
        open_prices = np.array([b['open'] for b in m1], dtype=float)

        # Bar range (high - low) as % of close
        bar_ranges = (high_prices - low_prices) / close_prices * 100

        # For SHORT entries (we enter when price is high):
        # Backtest: enters at close
        # Live worst case: enters at high (0.08% worse on average)
        short_slippage = (high_prices - close_prices) / close_prices * 100

        # For LONG entries (we enter when price is low):
        # Live worst case: enters at low
        long_slippage = (close_prices - low_prices) / close_prices * 100

        print(f"  Bar analysis ({len(m1)} bars):")
        print(f"    Avg bar range:  {np.mean(bar_ranges):.4f}%")
        print(f"    P90 bar range:  {np.percentile(bar_ranges, 90):.4f}%")
        print(f"    P95 bar range:  {np.percentile(bar_ranges, 95):.4f}%")
        print(f"    Max bar range:  {np.max(bar_ranges):.4f}%")
        print()
        print(f"  SHORT entry slippage (close vs high):")
        print(f"    Mean:   {np.mean(short_slippage):.4f}%")
        print(f"    Median: {np.median(short_slippage):.4f}%")
        print(f"    P90:    {np.percentile(short_slippage, 90):.4f}%")
        print(f"    P95:    {np.percentile(short_slippage, 95):.4f}%")
        print()
        print(f"  LONG entry slippage (close vs low):")
        print(f"    Mean:   {np.mean(long_slippage):.4f}%")
        print(f"    Median: {np.median(long_slippage):.4f}%")
        print(f"    P90:    {np.percentile(long_slippage, 90):.4f}%")
        print(f"    P95:    {np.percentile(long_slippage, 95):.4f}%")

        # Impact on our strategy: we enter SHORT on BOOM (sell spike)
        # So slippage = (high - close) at entry bar
        # Over21 trades, total slippage cost:
        avg_slip = np.mean(short_slippage) if symbol == "BOOM500" else np.mean(long_slippage)
        print(f"\n  Impact on 21 trades/month:")
        print(f"    Avg slippage per trade: {avg_slip:.4f}%")
        print(f"    Monthly cost: {avg_slip * 21:.3f}%")
        print(f"    Annual cost:  {avg_slip * 21 * 12:.2f}%")
        print(f"    vs Strategy return: +27.8% annual → slippage eats {avg_slip*21*12/27.8*100:.1f}%")

    print()


def research_ema_false_signals():
    """RISK 3: How often do EMA crosses reverse within10 bars?"""
    print("="*70)
    print("  RESEARCH 3: EMA CROSSOVER FALSE SIGNALS")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")
        closes = np.array([b['close'] for b in m1], dtype=float)

        ema9 = compute_ema(closes, 9)
        ema18 = compute_ema(closes, 18)

        # Find all crossovers
        crosses = []
        for i in range(1, len(closes)):
            prev_diff = ema9[i-1] - ema18[i-1]
            curr_diff = ema9[i] - ema18[i]
            if prev_diff <= 0 and curr_diff > 0:
                crosses.append((i, 'bullish'))
            elif prev_diff >= 0 and curr_diff < 0:
                crosses.append((i, 'bearish'))

        print(f"  Total crossovers: {len(crosses)}")

        # For each crossover, check if it persists for5/10/20 bars
        for hold in [5, 10, 20]:
            persistent = 0
            for idx, direction in crosses:
                if idx + hold >= len(closes):
                    continue
                # Check if direction holds at bar+hold
                if direction == 'bullish':
                    if ema9[idx + hold] > ema18[idx + hold]:
                        persistent += 1
                else:
                    if ema9[idx + hold] < ema18[idx + hold]:
                        persistent += 1

            valid = min(hold, len(crosses))
            print(f"    Cross persists {hold} bars: {persistent}/{valid} ({persistent/valid*100:.0f}%)")

        # Specifically: how often does a bullish cross happen AFTER RSI extreme?
        rsi = compute_rsi(closes, 14)
        rsi_low = 10 if symbol == "BOOM500" else 10
        rsi_high = 90 if symbol == "CRASH500" else 90

        # Find RSI extremes
        if symbol == "BOOM500":
            rsi_extremes = [i for i in range(len(rsi)) if rsi[i] <= rsi_low]
        else:
            rsi_extremes = [i for i in range(len(rsi)) if rsi[i] >= rsi_high]

        print(f"\n  RSI extremes (<=10 or >=90): {len(rsi_extremes)}")

        # How many have a cross within15 bars AFTER?
        confirmed = 0
        for rsi_idx in rsi_extremes:
            for c_idx, c_dir in crosses:
                if rsi_idx < c_idx <= rsi_idx + 15:
                    if symbol == "BOOM500" and c_dir == 'bullish':
                        confirmed += 1
                        break
                    elif symbol == "CRASH500" and c_dir == 'bearish':
                        confirmed += 1
                        break

        print(f"  RSI extreme followed by correct cross within15 bars: {confirmed}/{len(rsi_extremes)} ({confirmed/len(rsi_extremes)*100:.0f}%)" if rsi_extremes else "  No RSI extremes found")

        # Of those confirmed crosses, how many are still valid at bar+10?
        if confirmed > 0:
            still_valid = 0
            for rsi_idx in rsi_extremes:
                for c_idx, c_dir in crosses:
                    if rsi_idx < c_idx <= rsi_idx + 15:
                        if c_idx + 10 < len(closes):
                            if symbol == "BOOM500" and c_dir == 'bullish':
                                if ema9[c_idx + 10] > ema18[c_idx + 10]:
                                    still_valid += 1
                                break
                            elif symbol == "CRASH500" and c_dir == 'bearish':
                                if ema9[c_idx + 10] < ema18[c_idx + 10]:
                                    still_valid += 1
                                break
            print(f"  Still valid at bar+10: {still_valid}/{confirmed} ({still_valid/confirmed*100:.0f}%)")

    print()


def research_spread_widening():
    """RISK4: How much does spread widen during spikes?"""
    print("="*70)
    print("  RESEARCH4: SPREAD WIDENING DURING SPIKES")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")
        closes = np.array([b['close'] for b in m1], dtype=float)
        highs = np.array([b['high'] for b in m1], dtype=float)
        lows = np.array([b['low'] for b in m1], dtype=float)

        # Compute "spread proxy" = bar range as % of price
        ranges = (highs - lows) / closes * 100

        # Identify spike bars (range > 3x median)
        median_range = np.median(ranges)
        spike_mask = ranges > 3 * median_range
        normal_mask = ~spike_mask

        print(f"  Bar range analysis:")
        print(f"    Median range:  {median_range:.4f}%")
        print(f"    Normal bars:   {np.sum(normal_mask)} ({np.sum(normal_mask)/len(ranges)*100:.0f}%)")
        print(f"    Spike bars:    {np.sum(spike_mask)} ({np.sum(spike_mask)/len(ranges)*100:.0f}%)")
        print()
        print(f"  Normal bar spread proxy:  {np.mean(ranges[normal_mask]):.4f}%")
        print(f"  Spike bar spread proxy:   {np.mean(ranges[spike_mask]):.4f}%")
        print(f"  Spike/Normal ratio:       {np.mean(ranges[spike_mask])/np.mean(ranges[normal_mask]):.1f}x")

        # What % of spike bars exceed our SL (0.5%)?
        exceed_sl = np.sum(ranges > 0.5)
        print(f"\n  Bars exceeding 0.5% SL: {exceed_sl}/{len(ranges)} ({exceed_sl/len(ranges)*100:.2f}%)")

        # During spike bars, how much of the range is "unfillable"?
        # If we enter at close, the worst fill is at high (for SHORT)
        slip_on_spike = (highs[spike_mask] - closes[spike_mask]) / closes[spike_mask] * 100
        print(f"\n  On spike bars (SHORT entry):")
        print(f"    Avg slippage: {np.mean(slip_on_spike):.4f}%")
        print(f"    P90 slippage: {np.percentile(slip_on_spike, 90):.4f}%")
        print(f"    P95 slippage: {np.percentile(slip_on_spike, 95):.4f}%")
        print(f"    Max slippage: {np.max(slip_on_spike):.4f}%")

    print()


def research_rsi_partial_bars():
    """RISK5: RSI on partial bars vs closed bars."""
    print("="*70)
    print("  RESEARCH5: RSI ON PARTIAL BARS vs CLOSED BARS")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")
        closes = np.array([b['close'] for b in m1], dtype=float)

        rsi = compute_rsi(closes, 14)

        # Simulate "partial bar" RSI by using open price instead of close
        opens = np.array([b['open'] for b in m1], dtype=float)
        rsi_open = compute_rsi(opens, 14)

        # How often do they disagree on extreme signals?
        if symbol == "BOOM500":
            close_extreme = rsi <= 15
            open_extreme = rsi_open <= 15
        else:
            close_extreme = rsi >= 85
            open_extreme = rsi_open >= 85

        both = np.sum(close_extreme & open_extreme)
        close_only = np.sum(close_extreme & ~open_extreme)
        open_only = np.sum(open_extreme & ~close_extreme)

        print(f"  RSI extremes (close vs open):")
        print(f"    Both agree:     {both}")
        print(f"    Close only:     {close_only} (backtest would enter, live might miss)")
        print(f"    Open only:      {open_only} (live might enter, backtest wouldn't)")
        total = both + close_only + open_only
        if total > 0:
            print(f"    Agreement rate: {both/total*100:.0f}%")
            print(f"    Missed entries: {close_only/total*100:.0f}%")
            print(f"    False entries:  {open_only/total*100:.0f}%")

        # How big is the RSI difference on average?
        rsi_diff = np.abs(rsi - rsi_open)
        print(f"\n  RSI difference (close vs open):")
        print(f"    Mean:   {np.mean(rsi_diff):.2f}")
        print(f"    Median: {np.median(rsi_diff):.2f}")
        print(f"    P90:    {np.percentile(rsi_diff, 90):.2f}")
        print(f"    Max:    {np.max(rsi_diff):.2f}")

    print()


def research_reentry():
    """RISK6: Re-entry after loss effectiveness."""
    print("="*70)
    print("  RESEARCH6: RE-ENTRY AFTER LOSS — HOW OFTEN DOES IT WORK?")
    print("="*70)

    for symbol in ["BOOM500", "CRASH500"]:
        print(f"\n  --- {symbol} ---")
        m1 = load_candles(symbol, "1min")
        closes = np.array([b['close'] for b in m1], dtype=float)
        highs = np.array([b['high'] for b in m1], dtype=float)
        lows = np.array([b['low'] for b in m1], dtype=float)

        # Simulate: after a spike_against exit, what happens in next10 bars?
        # A spike_against means price moved against us → we exited at SL
        # If we re-entered immediately, would we win?

        from eval.smc_spike_hunter import detect_spikes
        spikes = detect_spikes(closes.tolist())

        # For each spike, check if re-entry after the next loss would win
        reentry_wins = 0
        reentry_losses = 0
        reentry_no_signal = 0

        for i in range(len(spikes) - 2):
            spike = spikes[i]  # (bar_index, direction_int, magnitude)
            next_spike = spikes[i + 1]
            bar_idx, spike_dir, spike_mag = spike
            next_idx, next_dir, next_mag = next_spike

            # Spike dir: 1=up (boom), -1=down (crash)
            # If first spike triggered entry, and second spike went against us...
            if spike_dir == 1 and next_dir == 1:
                # Re-entry after the loss: buy at next_spike close, wait for retrace
                reentry_bar = next_idx + 1
                if reentry_bar + 10 < len(closes):
                    entry_price = closes[reentry_bar]
                    # Check next5 bars for profit
                    future = closes[reentry_bar:reentry_bar + 10]
                    max_favorable = max(future) - entry_price
                    max_adverse = entry_price - min(future)
                    if max_favorable > max_adverse:
                        reentry_wins += 1
                    else:
                        reentry_losses += 1

            elif spike_dir == -1 and next_dir == -1:
                reentry_bar = next_idx + 1
                if reentry_bar + 10 < len(closes):
                    entry_price = closes[reentry_bar]
                    future = closes[reentry_bar:reentry_bar + 10]
                    max_favorable = entry_price - min(future)
                    max_adverse = max(future) - entry_price
                    if max_favorable > max_adverse:
                        reentry_wins += 1
                    else:
                        reentry_losses += 1

        total = reentry_wins + reentry_losses
        print(f"  Re-entry simulation (after consecutive same-direction spikes):")
        print(f"    Re-entries: {total}")
        print(f"    Wins:       {reentry_wins} ({reentry_wins/total*100:.0f}%)" if total > 0 else "    No re-entries")
        print(f"    Losses:     {reentry_losses} ({reentry_losses/total*100:.0f}%)" if total > 0 else "")
        print(f"    Verdict:    {'Re-entry is profitable' if reentry_wins > reentry_losses else 'Re-entry loses money'}")

    print()


if __name__ == "__main__":
    print("\n" + "="*70)
    print("  COMPREHENSIVE RISK RESEARCH — BOOM500 / CRASH500")
    print("  Based on actual 30-day historical data")
    print("="*70 + "\n")

    research_zone_decay()
    research_entry_timing()
    research_ema_false_signals()
    research_spread_widening()
    research_rsi_partial_bars()
    research_reentry()

    print("="*70)
    print("  RESEARCH COMPLETE")
    print("="*70)
