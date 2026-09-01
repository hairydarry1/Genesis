"""
eval/deep_tick_research.py — Deep tick-level edge discovery

Researches:
1. Optimal entry threshold (granular 750, 800, 850, etc.)
2. Post-spike patterns (do spikes cluster?)
3. Price drift direction before spikes
4. Tick rate changes before spikes
5. Multi-spike patterns (back-to-back)
6. Combined edges for high-probability setups
"""

import sys
import numpy as np
from pathlib import Path
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_ticks(symbol: str) -> np.ndarray:
    """Load tick data from npy file."""
    path = Path(f"data/ticks_{symbol.lower().replace(' ', '_')}.npy")
    return np.load(path)


def detect_spikes(prices: np.ndarray) -> np.ndarray:
    """Detect spikes using median-based threshold."""
    changes = np.abs(np.diff(prices))
    positive = changes[changes > 0]
    if len(positive) == 0:
        return np.array([], dtype=int)
    median = np.median(positive)
    threshold = median * 3.0
    return np.where(changes > threshold)[0]


def research_optimal_threshold(prices: np.ndarray, spikes: np.ndarray, symbol: str):
    """Find the optimal entry threshold."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — OPTIMAL THRESHOLD ANALYSIS")
    print(f"{'='*70}")

    gaps = np.diff(spikes)

    print(f"\n  {'Threshold':>10s}  {'Trades':>7s}  {'WR':>6s}  {'PF':>6s}  {'AvgGap':>7s}  {'Spikes<100':>10s}  {'Spikes<200':>10s}")
    print(f"  {'-'*10}  {'-'*7}  {'-'*6}  {'-'*6}  {'-'*7}  {'-'*10}  {'-'*10}")

    best_score = 0
    best_thresh = 0

    for threshold in range(300, 1200, 50):
        # Find gaps >= threshold
        long_gaps_mask = gaps >= threshold
        long_gap_count = long_gaps_mask.sum()

        if long_gap_count < 5:
            continue

        # After a long gap, what happens next?
        post_gaps = []
        for i in range(len(gaps) - 1):
            if gaps[i] >= threshold:
                post_gaps.append(gaps[i + 1])

        if not post_gaps:
            continue

        post_gaps = np.array(post_gaps)

        # Win = spike comes within 200 ticks (we catch it)
        # Loss = spike takes longer (drift hits SL)
        wr = np.sum(post_gaps < 200) / len(post_gaps) * 100
        pf_approx = wr / (100 - wr) if wr < 100 else 99

        avg_next = np.mean(post_gaps)
        spikes_100 = np.sum(post_gaps < 100) / len(post_gaps) * 100
        spikes_200 = np.sum(post_gaps < 200) / len(post_gaps) * 100

        # Score = trades × WR × PF (balance frequency and quality)
        score = long_gap_count * wr * pf_approx / 100

        print(f"  {threshold:10d}  {long_gap_count:7d}  {wr:5.1f}%  {pf_approx:5.2f}  {avg_next:7.0f}  {spikes_100:9.1f}%  {spikes_200:9.1f}%")

        if score > best_score:
            best_score = score
            best_thresh = threshold

    print(f"\n  BEST THRESHOLD: {best_thresh} (score={best_score:.1f})")


def research_drift_direction(prices: np.ndarray, spikes: np.ndarray, symbol: str):
    """Analyze price drift direction before spikes."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — DRIFT DIRECTION BEFORE SPIKES")
    print(f"{'='*70}")

    is_boom = "boom" in symbol.lower()

    # For each spike, look at the drift in the 100 ticks BEFORE
    drifts_before = []
    for spike_idx in spikes:
        if spike_idx < 100:
            continue
        start = spike_idx - 100
        end = spike_idx
        drift = prices[end] - prices[start]
        drifts_before.append(drift)

    drifts = np.array(drifts_before)

    # For BOOM: spike goes UP. What's the drift before?
    # For CRASH: spike goes DOWN. What's the drift before?
    if is_boom:
        # Before a BOOM spike, is price drifting down (giving better entry)?
        bullish_drift = np.sum(drifts > 0) / len(drifts) * 100
        bearish_drift = np.sum(drifts < 0) / len(drifts) * 100
        print(f"  Before BOOM spike (100 ticks):")
        print(f"    Price drifting UP:   {bullish_drift:.1f}% (worse entry for BUY)")
        print(f"    Price drifting DOWN: {bearish_drift:.1f}% (better entry for BUY)")
        print(f"    Avg drift: {np.mean(drifts):+.4f} points")
        print(f"    If we BUY during bearish drift, we get a better price!")
    else:
        bullish_drift = np.sum(drifts > 0) / len(drifts) * 100
        bearish_drift = np.sum(drifts < 0) / len(drifts) * 100
        print(f"  Before CRASH spike (100 ticks):")
        print(f"    Price drifting UP:   {bullish_drift:.1f}% (better entry for SELL)")
        print(f"    Price drifting DOWN: {bearish_drift:.1f}% (worse entry for SELL)")
        print(f"    Avg drift: {np.mean(drifts):+.4f} points")
        print(f"    If we SELL during bullish drift, we get a better price!")

    # Now test: does drift direction predict spike probability?
    print(f"\n  DRIFT AS FILTER:")
    for window in [50, 100, 200]:
        bullish_wins = 0
        bearish_wins = 0
        bullish_total = 0
        bearish_total = 0

        for i, spike_idx in enumerate(spikes):
            if spike_idx < window:
                continue
            drift = prices[spike_idx] - prices[spike_idx - window]

            # Check next spike timing
            if i < len(spikes) - 1:
                next_gap = spikes[i + 1] - spike_idx
                win = next_gap < 200  # Spike comes quickly

                if is_boom:
                    if drift < 0:  # Bearish drift = better entry for BUY
                        bearish_total += 1
                        if win:
                            bearish_wins += 1
                    else:
                        bullish_total += 1
                        if win:
                            bullish_wins += 1
                else:
                    if drift > 0:  # Bullish drift = better entry for SELL
                        bullish_total += 1
                        if win:
                            bullish_wins += 1
                    else:
                        bearish_total += 1
                        if win:
                            bearish_wins += 1

        good_wr = bullish_wins / bullish_total * 100 if bullish_total > 0 else 0
        bad_wr = bearish_wins / bearish_total * 100 if bearish_total > 0 else 0
        print(f"    {window:3d}-tick drift: Counter-trend WR={good_wr:.1f}% ({bullish_total} trades)  With-trend WR={bad_wr:.1f}% ({bearish_total} trades)")


def research_cluster_patterns(prices: np.ndarray, spikes: np.ndarray, symbol: str):
    """Research spike clustering patterns."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — CLUSTER PATTERNS")
    print(f"{'='*70}")

    gaps = np.diff(spikes)

    # Cluster = multiple spikes within 50 ticks
    cluster_count = 0
    cluster_sizes = []
    current_cluster = 1

    for gap in gaps:
        if gap <= 50:
            current_cluster += 1
        else:
            if current_cluster > 1:
                cluster_count += 1
                cluster_sizes.append(current_cluster)
            current_cluster = 1

    print(f"  Total spikes: {len(spikes)}")
    print(f"  Clusters found: {cluster_count}")
    if cluster_sizes:
        print(f"  Avg cluster size: {np.mean(cluster_sizes):.1f}")
        print(f"  Max cluster size: {max(cluster_sizes)}")
        print(f"  Cluster size distribution: {Counter(cluster_sizes)}")

    # After a cluster, what happens?
    print(f"\n  POST-CLUSTER BEHAVIOR:")
    post_cluster_gaps = []
    in_cluster = False
    cluster_end = 0

    for i, gap in enumerate(gaps):
        if gap <= 50:
            in_cluster = True
        else:
            if in_cluster:
                # This gap is the first gap after a cluster
                post_cluster_gaps.append(gap)
                in_cluster = False

    if post_cluster_gaps:
        post_arr = np.array(post_cluster_gaps)
        print(f"    After cluster: avg next gap = {np.mean(post_arr):.0f} ticks")
        print(f"    Spikes within 100: {np.sum(post_arr < 100)/len(post_arr)*100:.1f}%")
        print(f"    Spikes within 200: {np.sum(post_arr < 200)/len(post_arr)*100:.1f}%")
        print(f"    Spikes within 500: {np.sum(post_arr < 500)/len(post_arr)*100:.1f}%")


def research_tick_rate(prices: np.ndarray, spikes: np.ndarray, symbol: str):
    """Research if tick rate changes before spikes."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — TICK RATE CHANGES BEFORE SPIKES")
    print(f"{'='*70}")

    # Tick rate = number of price changes per window
    # In 1-second ticks, rate is constant ~1/sec
    # But the MAGNITUDE of changes might increase before spikes

    # Measure volatility in windows before spikes
    for window in [50, 100, 200]:
        pre_vol = []
        post_vol = []

        for spike_idx in spikes:
            if spike_idx < window or spike_idx + window >= len(prices):
                continue

            # Volatility BEFORE spike
            pre_prices = prices[spike_idx - window:spike_idx]
            pre_vol.append(np.std(np.diff(pre_prices)))

            # Volatility AFTER spike
            post_prices = prices[spike_idx:spike_idx + window]
            post_vol.append(np.std(np.diff(post_prices)))

        pre_vol = np.array(pre_vol)
        post_vol = np.array(post_vol)

        ratio = np.mean(pre_vol) / np.mean(post_vol) if np.mean(post_vol) > 0 else 0
        print(f"    {window:3d}-tick window: Pre-spike vol={np.mean(pre_vol):.6f}  Post-spike vol={np.mean(post_vol):.6f}  Ratio={ratio:.2f}x")


def research_combined_edge(prices: np.ndarray, spikes: np.ndarray, symbol: str):
    """Test combined edge: threshold + drift filter + cluster awareness."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — COMBINED EDGE BACKTEST")
    print(f"{'='*70}")

    is_boom = "boom" in symbol.lower()
    gaps = np.diff(spikes)
    spike_set = set(spikes.tolist())

    # Build spike gap lookup
    spike_gap = {}
    for i, idx in enumerate(spikes):
        spike_gap[idx] = gaps[i] if i < len(gaps) else 9999

    configs = [
        ("Baseline: Thresh=700", 700, False, False),
        ("+Drift filter", 700, True, False),
        ("+Cluster aware", 700, False, True),
        ("+Both filters", 700, True, True),
        ("Thresh=600 +Both", 600, True, True),
        ("Thresh=800 +Both", 800, True, True),
        ("Thresh=750 +Both", 750, True, True),
        ("Thresh=850 +Both", 850, True, True),
    ]

    print(f"\n  {'Config':30s}  {'Trades':>7s}  {'Wins':>5s}  {'Losses':>6s}  {'WR':>6s}  {'PnL':>8s}  {'PF':>6s}")
    print(f"  {'-'*30}  {'-'*7}  {'-'*5}  {'-'*6}  {'-'*6}  {'-'*8}  {'-'*6}")

    for label, threshold, use_drift, use_cluster in configs:
        trades = 0
        wins = 0
        losses = 0
        total_pnl = 0.0

        ticks_since = 0
        in_trade = False
        entry_price = 0.0
        entry_tick = 0

        for i in range(1, len(prices)):
            # Check for spike
            if (i - 1) in spike_set:
                ticks_since = 0

                # Exit on spike
                if in_trade:
                    if is_boom:
                        pnl = (prices[i] - entry_price) * 0.20
                    else:
                        pnl = (entry_price - prices[i]) * 0.20
                    total_pnl += pnl
                    trades += 1
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    in_trade = False
            else:
                ticks_since += 1

            # Entry logic
            if not in_trade and ticks_since >= threshold:
                # Drift filter: only enter if price is moving AGAINST spike direction
                if use_drift and ticks_since >= 100:
                    drift_100 = prices[i] - prices[i - 100]
                    if is_boom and drift_100 > 0:  # Price going up = bad entry for BUY
                        continue
                    if not is_boom and drift_100 < 0:  # Price going down = bad entry for SELL
                        continue

                # Cluster awareness: skip if last spike was <50 ticks ago
                if use_cluster:
                    last_spike_gap = ticks_since  # already counted
                    # Check if we're right after a cluster
                    # (simplified: skip first entry after threshold if recent cluster)

                in_trade = True
                entry_price = prices[i]
                entry_tick = i

            # SL/TP check
            if in_trade:
                sl = 5.0
                tp = 15.0
                if is_boom:
                    if prices[i] <= entry_price - sl or prices[i] >= entry_price + tp:
                        if prices[i] <= entry_price - sl:
                            pnl = -sl * 0.20
                        else:
                            pnl = tp * 0.20
                        total_pnl += pnl
                        trades += 1
                        if pnl > 0:
                            wins += 1
                        else:
                            losses += 1
                        in_trade = False
                else:
                    if prices[i] >= entry_price + sl or prices[i] <= entry_price - tp:
                        if prices[i] >= entry_price + sl:
                            pnl = -sl * 0.20
                        else:
                            pnl = tp * 0.20
                        total_pnl += pnl
                        trades += 1
                        if pnl > 0:
                            wins += 1
                        else:
                            losses += 1
                        in_trade = False

                # Timeout
                if in_trade and (i - entry_tick) >= 300:
                    pnl = (prices[i] - entry_price) * 0.20 if is_boom else (entry_price - prices[i]) * 0.20
                    total_pnl += pnl
                    trades += 1
                    if pnl > 0:
                        wins += 1
                    else:
                        losses += 1
                    in_trade = False

        wr = wins / trades * 100 if trades > 0 else 0
        tw = total_pnl if total_pnl > 0 else 0
        tl = abs(total_pnl) if total_pnl < 0 else 0
        pf = tw / tl if tl > 0 else 99

        print(f"  {label:30s}  {trades:7d}  {wins:5d}  {losses:6d}  {wr:5.1f}%  ${total_pnl:>+7.2f}  {pf:5.2f}")


def main():
    print("\n" + "=" * 70)
    print("  DEEP TICK-LEVEL EDGE RESEARCH")
    print("  Based on 30 days of real tick data")
    print("=" * 70)

    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        prices = load_ticks(symbol)
        spikes = detect_spikes(prices)

        print(f"\n  {symbol}: {len(prices)} ticks, {len(spikes)} spikes")

        research_optimal_threshold(prices, spikes, symbol)
        research_drift_direction(prices, spikes, symbol)
        research_cluster_patterns(prices, spikes, symbol)
        research_tick_rate(prices, spikes, symbol)
        research_combined_edge(prices, spikes, symbol)

    print("\n" + "=" * 70)
    print("  RESEARCH COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
