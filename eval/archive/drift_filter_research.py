"""
eval/drift_filter_research.py — Find filters that improve Drift Fade win rate.

Tests:
  1. Volatility filter: Only trade when recent volatility is below median
  2. Spike size filter: Only trade after large spikes (stronger counter-drift)
  3. Time-of-day filter: Some hours may have better drift
  4. RSI filter: Confirm oversold/overbought before entry
  5. EMA alignment: Confirm trend direction matches drift
  6. Consecutive spike filter: After clusters vs isolated spikes
  7. Price position filter: How far price drifted from spike close
"""

import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.smc_spike_hunter import load_candles, compute_rsi, compute_ema, compute_sma


def detect_spikes(bars_arr, direction, threshold=3.0):
    ranges = bars_arr[:, 1] - bars_arr[:, 2]
    med = np.median(ranges)
    spikes = []
    for i in range(len(bars_arr)):
        if ranges[i] > threshold * med:
            if direction == 'up' and bars_arr[i, 3] > bars_arr[i, 0]:
                spikes.append(i)
            elif direction == 'down' and bars_arr[i, 3] < bars_arr[i, 0]:
                spikes.append(i)
    return np.array(spikes)


def backtest_with_filters(bars_arr, spikes, direction, dpp, filters=None):
    """Backtest drift fade with optional filters. Returns detailed stats."""
    if filters is None:
        filters = {}

    n = len(bars_arr)
    spike_set = set(spikes.tolist())
    last_spike = -1
    in_trade = False
    entry_price = 0
    entry_idx = 0
    closes = bars_arr[:, 3]
    highs = bars_arr[:, 1]
    lows = bars_arr[:, 2]
    opens = bars_arr[:, 0]

    # Pre-compute indicators
    ranges = highs - lows
    med_range = float(np.median(ranges))

    # RSI
    rsi = compute_rsi(closes, 14)

    # EMA
    ema9 = compute_ema(closes, 9)
    ema18 = compute_sma(closes, 18)

    # Volatility (20-bar rolling std of returns)
    returns = np.diff(closes) / closes[:-1]
    returns = np.concatenate([[0], returns])
    vol20 = np.full(n, np.nan)
    for i in range(20, n):
        vol20[i] = np.std(returns[i-20:i])
    vol_median = np.nanmedian(vol20)

    # Track which spikes are in clusters (>=3 spikes within 10 bars)
    spike_cluster = set()
    for s_idx in spikes:
        nearby = sum(1 for s2 in spikes if abs(s2 - s_idx) <= 10 and s2 != s_idx)
        if nearby >= 2:
            spike_cluster.add(s_idx)

    entry_bar = filters.get("entry_bar", 3)
    sl_val = filters.get("sl", 1.0)
    tp_val = filters.get("tp", 3.0)
    max_hold = filters.get("max_hold", 9)

    trades = 0
    wins = 0
    losses = 0
    total_pnl = 0
    filtered_out = 0
    tp_hits = 0
    sl_hits = 0
    spike_exits = 0
    timeout_hits = 0

    for i in range(n):
        if i in spike_set:
            last_spike = i
        bars_since = i - last_spike if last_spike >= 0 else 999

        if in_trade:
            h, l, c = highs[i], lows[i], closes[i]
            bars_held = i - entry_idx

            if direction == 'short':
                hit_tp = (entry_price - l) >= tp_val
                hit_sl = (entry_price - h) <= -sl_val
                unrealized = entry_price - c
            else:
                hit_tp = (h - entry_price) >= tp_val
                hit_sl = (l - entry_price) <= -sl_val
                unrealized = c - entry_price

            exit_pts = None
            reason = None
            if hit_tp:
                exit_pts, reason = tp_val, "tp"
            elif hit_sl:
                exit_pts, reason = -sl_val, "sl"
            elif i in spike_set:
                exit_pts, reason = unrealized, "spike"
            elif bars_held >= max_hold:
                exit_pts, reason = unrealized, "timeout"

            if exit_pts is not None:
                pnl = exit_pts * dpp
                total_pnl += pnl
                trades += 1
                if pnl > 0:
                    wins += 1
                    tp_hits += 1 if reason == "tp" else 0
                else:
                    losses += 1
                    sl_hits += 1 if reason == "sl" else 0
                    spike_exits += 1 if reason == "spike" else 0
                    timeout_hits += 1 if reason == "timeout" else 0
                in_trade = False

        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            # ── Apply filters ──
            skip = False

            # Filter 1: Volatility — only trade when vol is below median
            if filters.get("vol_low") and not np.isnan(vol20[i]):
                if vol20[i] > vol_median * filters["vol_low"]:
                    skip = True
                    filtered_out += 1

            # Filter 2: Spike size — only trade after large spikes
            if filters.get("min_spike_size"):
                spike_range = ranges[last_spike]
                if spike_range < filters["min_spike_size"] * med_range:
                    skip = True
                    filtered_out += 1

            # Filter 3: RSI confirmation
            if filters.get("rsi_confirm"):
                if direction == 'short':  # BOOM: want RSI dropping
                    if rsi[i] > filters["rsi_confirm"]:
                        skip = True
                        filtered_out += 1
                else:  # CRASH: want RSI rising
                    if rsi[i] < (100 - filters["rsi_confirm"]):
                        skip = True
                        filtered_out += 1

            # Filter 4: EMA alignment (EMA9 below EMA18 for short, above for long)
            if filters.get("ema_align"):
                if not np.isnan(ema9[i]) and not np.isnan(ema18[i]):
                    if direction == 'short' and ema9[i] > ema18[i]:
                        skip = True
                        filtered_out += 1
                    elif direction == 'long' and ema9[i] < ema18[i]:
                        skip = True
                        filtered_out += 1

            # Filter 5: Skip clustered spikes (more chaotic)
            if filters.get("skip_cluster"):
                if last_spike in spike_cluster:
                    skip = True
                    filtered_out += 1

            # Filter 6: Price position — only enter if price has drifted enough
            if filters.get("min_drift"):
                if direction == 'short':
                    drift = entry_price - closes[last_spike]
                else:
                    drift = closes[last_spike] - entry_price
                if drift < filters["min_drift"] * med_range:
                    skip = True
                    filtered_out += 1

            # Filter 7: Time-of-day (epoch-based)
            if filters.get("hours"):
                epoch = bars_arr[i, 4] if bars_arr.shape[1] > 4 else 0
                hour = (int(epoch) % 86400) // 3600
                if hour not in filters["hours"]:
                    skip = True
                    filtered_out += 1

            if skip:
                continue

            # Entry
            entry_price = closes[i]
            entry_idx = i
            in_trade = True

    wr = wins / trades * 100 if trades > 0 else 0
    pf = wins / max(losses, 1) if losses > 0 else 99

    return {
        "trades": trades, "wins": wins, "losses": losses,
        "win_rate": wr, "pf": pf, "total_pnl": total_pnl,
        "filtered_out": filtered_out,
        "tp": tp_hits, "sl": sl_hits, "spike_ex": spike_exits, "timeout": timeout_hits,
    }


def main():
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'up', 0.20),
        'BOOM1000': ('data/candles_boom1000_1min.parquet', 'up', 0.10),
    }

    print(f"\n{'='*80}")
    print(f"  DRIFT FADE FILTER RESEARCH")
    print(f"  Testing filters to improve win rate from baseline 62-84%")
    print(f"{'='*80}")

    for sym, (path, direction, dpp) in configs.items():
        if not Path(path).exists():
            continue

        candles = pd.read_parquet(path)
        candles.columns = [c.lower() for c in candles.columns]
        bars_arr = candles[['open', 'high', 'low', 'close']].values.astype(float)
        spikes = detect_spikes(bars_arr, direction, threshold=3.0)

        print(f"\n  {'─'*76}")
        print(f"  {sym}: {len(bars_arr)} bars, {len(spikes)} spikes, ${dpp}/point")
        print(f"  {'─'*76}")

        # Baseline
        baseline = backtest_with_filters(bars_arr, spikes, direction, dpp, {"entry_bar": 3, "sl": 2.0, "tp": 3.0, "max_hold": 9})
        print(f"\n  BASELINE: {baseline['trades']}T  WR={baseline['win_rate']:.1f}%  PF={baseline['pf']:.2f}  PnL=${baseline['total_pnl']:+.2f}")

        # Test individual filters
        filter_tests = [
            ("Vol<1.0x median", {"vol_low": 1.0}),
            ("Vol<0.8x median", {"vol_low": 0.8}),
            ("Vol<0.6x median", {"vol_low": 0.6}),
            ("Spike>3.5x range", {"min_spike_size": 3.5}),
            ("Spike>4.0x range", {"min_spike_size": 4.0}),
            ("Spike>5.0x range", {"min_spike_size": 5.0}),
            ("RSI<40 confirm", {"rsi_confirm": 40}),
            ("RSI<30 confirm", {"rsi_confirm": 30}),
            ("EMA align", {"ema_align": True}),
            ("Skip cluster", {"skip_cluster": True}),
            ("Min drift 1.5x", {"min_drift": 1.5}),
            ("Min drift 2.0x", {"min_drift": 2.0}),
        ]

        print(f"\n  {'Filter':<22} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>10} {'Filtered':>9}")
        print(f"  {'─'*65}")

        best_score = baseline['pf'] * (baseline['win_rate']/100)
        best_name = "BASELINE"

        for name, filt in filter_tests:
            r = backtest_with_filters(bars_arr, spikes, direction, dpp,
                                       {"entry_bar": 3, "sl": 2.0, "tp": 3.0, "max_hold": 9, **filt})
            score = r['pf'] * (r['win_rate']/100) if r['trades'] > 10 else 0
            marker = " <" if score > best_score else ""
            print(f"  {name:<22} {r['trades']:>7} {r['win_rate']:>6.1f}% {r['pf']:>5.2f} "
                  f"${r['total_pnl']:>+9.2f} {r['filtered_out']:>8}{marker}")
            if score > best_score:
                best_score = score
                best_name = name

        # Test best COMBINATION of filters
        print(f"\n  COMBINATION TESTS:")
        combos = [
            ("Vol+RSI", {"vol_low": 0.8, "rsi_confirm": 30}),
            ("Vol+EMA", {"vol_low": 0.8, "ema_align": True}),
            ("Vol+RSI+EMA", {"vol_low": 0.8, "rsi_confirm": 30, "ema_align": True}),
            ("Vol+SkipCluster", {"vol_low": 0.8, "skip_cluster": True}),
            ("All best", {"vol_low": 0.8, "rsi_confirm": 30, "ema_align": True, "skip_cluster": True}),
            ("All+MinDrift", {"vol_low": 0.8, "rsi_confirm": 30, "ema_align": True, "skip_cluster": True, "min_drift": 1.5}),
            ("Tight filters", {"vol_low": 0.6, "rsi_confirm": 40, "ema_align": True}),
            ("Loose filters", {"vol_low": 1.0, "rsi_confirm": 20}),
        ]

        print(f"  {'Combo':<22} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>10} {'Filtered':>9}")
        print(f"  {'─'*65}")

        best_combo_score = best_score
        best_combo_name = best_name

        for name, filt in combos:
            r = backtest_with_filters(bars_arr, spikes, direction, dpp,
                                       {"entry_bar": 3, "sl": 2.0, "tp": 3.0, "max_hold": 9, **filt})
            score = r['pf'] * (r['win_rate']/100) if r['trades'] > 10 else 0
            marker = " <" if score > best_combo_score else ""
            print(f"  {name:<22} {r['trades']:>7} {r['win_rate']:>6.1f}% {r['pf']:>5.2f} "
                  f"${r['total_pnl']:>+9.2f} {r['filtered_out']:>8}{marker}")
            if score > best_combo_score:
                best_combo_score = score
                best_combo_name = name

        print(f"\n  BEST: {best_combo_name} (score={best_combo_score:.3f})")

    print(f"\n{'='*80}")


if __name__ == "__main__":
    main()
