"""
eval/deep_research.py — Deep research into ALL missed optimization opportunities.

Analyzes:
1. Time-of-day patterns
2. Volatility regime impact
3. Consecutive spike patterns
4. Entry timing optimization
5. Trailing stop vs fixed TP/SL
6. Price drift direction before spike
7. Session analysis
8. Spread impact
"""

import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles

def analyze_all(sym):
    m1 = load_candles(sym, '1min')
    bars = np.array([[b['open'],b['high'],b['low'],b['close']] for b in m1], dtype=float)
    epochs = np.array([b['epoch'] for b in m1])
    c,h,l,o = bars[:,3],bars[:,1],bars[:,2],bars[:,0]
    rng=h-l; med=float(np.median(rng))
    is_boom='boom' in sym.lower()

    # Detect spikes
    sm = rng > 3.0*med
    if is_boom: sm &= c>o
    else: sm &= c<o
    spikes = np.where(sm)[0]
    ss = set(spikes.tolist())

    # Cluster filter
    clust = set()
    sa = np.array(spikes)
    for s in sa:
        if int(np.sum(np.abs(sa-s)<=10))-1>=2: clust.add(int(s))

    print(f"\n{'='*72}")
    print(f"  DEEP RESEARCH — {sym}")
    print(f"  {len(bars)} bars, {len(spikes)} spikes, {len(clust)} clustered")
    print(f"{'='*72}")

    # ================================================================
    # 1. TIME-OF-DAY ANALYSIS
    # ================================================================
    print(f"\n  1. TIME-OF-DAY PATTERNS")
    print(f"  {'─'*60}")

    # Get hour for each spike
    spike_hours = [(int(epochs[s]) % 86400) // 3600 for s in spikes]

    # For each hour, measure counter-drift quality
    print(f"  {'Hour':>6} {'Spikes':>7} {'AvgDrift':>9} {'WR(3pt)':>8} {'WR(2pt)':>8} {'Quality':>8}")
    print(f"  {'─'*52}")

    for hour in range(24):
        hour_spikes = [i for i,s in enumerate(spikes) if spike_hours[i] == hour]
        if len(hour_spikes) < 10: continue

        drifts = []
        for idx in hour_spikes:
            s = spikes[idx]
            max_d = 0
            for j in range(1,10):
                if s+j < len(bars):
                    if is_boom: d = c[s] - c[s+j]
                    else: d = c[s+j] - c[s]
                    max_d = max(max_d, d)
            drifts.append(max_d)

        avg_drift = np.mean(drifts)
        wr3 = np.mean([d>=3.0 for d in drifts])*100
        wr2 = np.mean([d>=2.0 for d in drifts])*100
        quality = wr3 * np.log1p(len(hour_spikes))

        print(f"  {hour:>5}h {len(hour_spikes):>7} {avg_drift:>8.3f} {wr3:>7.1f}% {wr2:>7.1f}% {quality:>7.1f}")

    # ================================================================
    # 2. VOLATILITY REGIME ANALYSIS
    # ================================================================
    print(f"\n  2. VOLATILITY REGIME ANALYSIS")
    print(f"  {'─'*60}")

    # Compute 20-bar rolling volatility
    rets = np.zeros(len(bars))
    rets[1:] = np.diff(c) / np.maximum(c[:-1], 0.001)
    vol20 = np.full(len(bars), np.nan)
    for i in range(20, len(bars)):
        vol20[i] = np.std(rets[i-20:i])
    vol_med = np.nanmedian(vol20)

    # Classify spikes by pre-spike volatility
    print(f"  {'Regime':>12} {'Spikes':>7} {'AvgDrift':>9} {'WR(3pt)':>8} {'WR(2pt)':>8}")
    print(f"  {'─'*48}")

    for label, lo, hi in [("Low Vol", 0, 0.7), ("Normal", 0.7, 1.3), ("High Vol", 1.3, 2.0), ("Extreme", 2.0, 10.0)]:
        drifts = []
        for s in spikes:
            if s < 20: continue
            v = vol20[s]
            if np.isnan(v): continue
            v_ratio = v / vol_med
            if lo <= v_ratio < hi:
                max_d = 0
                for j in range(1,10):
                    if s+j < len(bars):
                        if is_boom: d = c[s] - c[s+j]
                        else: d = c[s+j] - c[s]
                        max_d = max(max_d, d)
                drifts.append(max_d)

        if len(drifts) < 10: continue
        avg_drift = np.mean(drifts)
        wr3 = np.mean([d>=3.0 for d in drifts])*100
        wr2 = np.mean([d>=2.0 for d in drifts])*100
        print(f"  {label:>12} {len(drifts):>7} {avg_drift:>8.3f} {wr3:>7.1f}% {wr2:>7.1f}%")

    # ================================================================
    # 3. CONSECUTIVE SPIKE PATTERNS
    # ================================================================
    print(f"\n  3. CONSECUTIVE SPIKE PATTERNS")
    print(f"  {'─'*60}")

    # Classify spikes by what happened before
    print(f"  {'Pattern':>20} {'Count':>7} {'AvgDrift':>9} {'WR(3pt)':>8} {'WR(2pt)':>8}")
    print(f"  {'─'*52}")

    for prev_count in [0, 1, 2, 3, 4, 5]:
        drifts = []
        for idx, s in enumerate(spikes):
            # Count spikes before this one (within 20 bars)
            before = sum(1 for j in range(1, 21) if s-j >= 0 and (s-j) in ss)
            if before == prev_count or (prev_count == 5 and before >= 5):
                max_d = 0
                for j in range(1,10):
                    if s+j < len(bars):
                        if is_boom: d = c[s] - c[s+j]
                        else: d = c[s+j] - c[s]
                        max_d = max(max_d, d)
                drifts.append(max_d)

        if len(drifts) < 10: continue
        avg_drift = np.mean(drifts)
        wr3 = np.mean([d>=3.0 for d in drifts])*100
        wr2 = np.mean([d>=2.0 for d in drifts])*100
        label = f"{prev_count} prev" if prev_count < 5 else "5+ prev"
        print(f"  {label:>20} {len(drifts):>7} {avg_drift:>8.3f} {wr3:>7.1f}% {wr2:>7.1f}%")

    # ================================================================
    # 4. ENTRY TIMING OPTIMIZATION
    # ================================================================
    print(f"\n  4. ENTRY TIMING OPTIMIZATION")
    print(f"  {'─'*60}")

    print(f"  {'Entry':>8} {'Trades':>7} {'WR%':>7} {'AvgDrift':>9} {'EV(3,1)':>8}")
    print(f"  {'─'*44}")

    for entry_bar in [1, 2, 3, 4, 5, 7, 10]:
        trades = 0; wins = 0; total_drift = 0
        for s in spikes:
            entry_idx = s + entry_bar
            if entry_idx >= len(bars): continue
            if entry_idx in ss: continue  # Skip if entry bar is a spike

            # Check cluster filter
            if s in clust: continue

            trades += 1
            max_d = 0
            for j in range(entry_bar+1, entry_bar+10):
                if j < len(bars):
                    if is_boom: d = c[s] - c[j]
                    else: d = c[j] - c[s]
                    max_d = max(max_d, d)
            total_drift += max_d
            if max_d >= 3.0: wins += 1

        if trades < 10: continue
        wr = wins/trades*100
        avg_d = total_drift/trades
        ev = (wr/100)*3.0 - (1-wr/100)*1.0  # TP=3 SL=1
        print(f"  Bar {entry_bar:>3} {trades:>7} {wr:>6.1f}% {avg_d:>8.3f} {ev:>+7.3f}")

    # ================================================================
    # 5. TRAILING STOP ANALYSIS
    # ================================================================
    print(f"\n  5. TRAILING STOP vs FIXED TP/SL")
    print(f"  {'─'*60}")

    # Simulate different exit strategies
    configs = [
        ("Fixed TP=3 SL=1", 3.0, 1.0, 0),
        ("Fixed TP=2 SL=1", 2.0, 1.0, 0),
        ("Fixed TP=4 SL=1", 4.0, 1.0, 0),
        ("TP=3 SL=1 BE+1", 3.0, 1.0, 1.0),  # Move SL to breakeven+1 after 1pt profit
        ("TP=3 SL=1 Trail", 3.0, 1.0, -1),   # Trailing stop
    ]

    print(f"  {'Config':>20} {'Trades':>7} {'WR%':>7} {'AvgWin':>8} {'AvgLoss':>8} {'EV':>8}")
    print(f"  {'─'*56}")

    for label, tp, sl, trail in configs:
        trades = 0; wins = 0; total_pnl = 0; win_pnl = 0; loss_pnl = 0
        for s in spikes:
            entry_idx = s + 3
            if entry_idx >= len(bars) or entry_idx in ss or s in clust: continue

            entry_price = c[entry_idx]
            in_trade = True
            bars_held = 0
            best_price = entry_price

            for j in range(entry_idx+1, min(entry_idx+10, len(bars))):
                bars_held += 1
                if is_boom:  # SHORT
                    best_price = max(best_price, l[j])  # Worst price for short
                    if entry_price - l[j] >= tp:
                        pnl = tp; in_trade = False; break
                    if entry_price - h[j] <= -sl:
                        pnl = -sl; in_trade = False; break
                else:  # LONG
                    best_price = min(best_price, h[j])  # Worst price for long
                    if h[j] - entry_price >= tp:
                        pnl = tp; in_trade = False; break
                    if l[j] - entry_price <= -sl:
                        pnl = -sl; in_trade = False; break

                # Trailing stop logic
                if trail > 0 and bars_held >= 2:
                    unrealized = (entry_price - c[j]) if is_boom else (c[j] - entry_price)
                    if unrealized >= trail:
                        # Move SL to breakeven
                        if is_boom and entry_price - h[j] <= 0:
                            pnl = unrealized; in_trade = False; break
                        elif not is_boom and l[j] - entry_price <= 0:
                            pnl = unrealized; in_trade = False; break
                elif trail < 0:  # Full trailing
                    unrealized = (entry_price - c[j]) if is_boom else (c[j] - entry_price)
                    if unrealized >= 1.0:  # After 1pt profit, trail at 0.5pt behind
                        trailing_sl = entry_price + 0.5 if is_boom else entry_price - 0.5
                        if is_boom and h[j] >= trailing_sl:
                            pnl = unrealized; in_trade = False; break
                        elif not is_boom and l[j] <= trailing_sl:
                            pnl = unrealized; in_trade = False; break

            if in_trade:
                if is_boom: pnl = (entry_price - c[min(entry_idx+9, len(bars)-1)])
                else: pnl = (c[min(entry_idx+9, len(bars)-1)] - entry_price)

            trades += 1
            total_pnl += pnl
            if pnl > 0: wins += 1; win_pnl += pnl
            else: loss_pnl += abs(pnl)

        if trades == 0: continue
        wr = wins/trades*100
        avg_w = win_pnl/wins if wins > 0 else 0
        avg_l = loss_pnl/(trades-wins) if trades-wins > 0 else 0
        ev = (wr/100)*3.0 - (1-wr/100)*1.0
        print(f"  {label:>20} {trades:>7} {wr:>6.1f}% {avg_w:>7.3f} {avg_l:>7.3f} {ev:>+7.3f}")

    # ================================================================
    # 6. SPIKE SIZE FILTER
    # ================================================================
    print(f"\n  6. SPIKE SIZE FILTER (min spike size)")
    print(f"  {'─'*60}")

    print(f"  {'MinSize':>8} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>10}")
    print(f"  {'─'*40}")

    for min_mult in [3.0, 3.5, 4.0, 5.0, 6.0, 7.0, 10.0]:
        trades = 0; wins = 0; total_pnl = 0
        for s in spikes:
            if rng[s]/med < min_mult: continue
            if s in clust: continue
            entry_idx = s + 3
            if entry_idx >= len(bars) or entry_idx in ss: continue

            entry_price = c[entry_idx]
            pnl = 0
            for j in range(entry_idx+1, min(entry_idx+10, len(bars))):
                if is_boom:
                    if entry_price - l[j] >= 3: pnl = 3; break
                    if entry_price - h[j] <= -1: pnl = -1; break
                else:
                    if h[j] - entry_price >= 3: pnl = 3; break
                    if l[j] - entry_price <= -1: pnl = -1; break
            if pnl == 0:
                if is_boom: pnl = entry_price - c[min(entry_idx+9, len(bars)-1)]
                else: pnl = c[min(entry_idx+9, len(bars)-1)] - entry_price

            trades += 1; total_pnl += pnl
            if pnl > 0: wins += 1

        if trades < 10: continue
        wr = wins/trades*100
        pf = wins/max(trades-wins, 1) * 3  # Approximate PF
        print(f"  {min_mult:>7.1f}x {trades:>7} {wr:>6.1f}% {pf:>5.2f} {total_pnl:>+9.2f}")

    # ================================================================
    # 7. COMBINED OPTIMAL FILTER
    # ================================================================
    print(f"\n  7. COMBINED OPTIMAL FILTER")
    print(f"  {'─'*60}")

    # Test: No cluster + entry_bar=3 + TP=3 SL=1
    trades = 0; wins = 0; total_pnl = 0; pnls = []
    for s in spikes:
        if s in clust: continue
        entry_idx = s + 3
        if entry_idx >= len(bars) or entry_idx in ss: continue

        entry_price = c[entry_idx]
        pnl = 0
        for j in range(entry_idx+1, min(entry_idx+10, len(bars))):
            if is_boom:
                if entry_price - l[j] >= 3: pnl = 3; break
                if entry_price - h[j] <= -1: pnl = -1; break
            else:
                if h[j] - entry_price >= 3: pnl = 3; break
                if l[j] - entry_price <= -1: pnl = -1; break
        if pnl == 0:
            if is_boom: pnl = entry_price - c[min(entry_idx+9, len(bars)-1)]
            else: pnl = c[min(entry_idx+9, len(bars)-1)] - entry_price

        trades += 1; total_pnl += pnl; pnls.append(pnl)
        if pnl > 0: wins += 1

    wr = wins/trades*100 if trades else 0
    w = [p for p in pnls if p > 0]; l2 = [p for p in pnls if p <= 0]
    pf = sum(w)/max(abs(sum(l2)), 0.01) if w and l2 else 0
    print(f"  Base config: {trades}T  WR={wr:.1f}%  PF={pf:.2f}  PnL={total_pnl:+.2f}")

    # Now test adding best filters from above
    # (Will be filled in after analyzing results)

    print(f"\n{'='*72}")


# Run analysis
for sym in ['BOOM500', 'BOOM1000']:
    analyze_all(sym)
