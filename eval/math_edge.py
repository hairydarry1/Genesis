#!/usr/bin/env python3
"""
MATH EDGE — what does math actually say about this strategy?
============================================================
1. CONDITIONAL LOSS PROBABILITY: P(loss | previous trade lost) vs
   P(loss | previous trade won). If a loss predicts a loss, the
   cooldown is mathematically justified, and we can measure exactly
   how many spikes to skip.
2. KELLY CRITERION: optimal fraction of equity to risk per trade
   from the measured p, b (avg win / avg loss). Compare to the
   current flat 2% / 5-lot sizing.
3. RISK OF RUIN: simulate account paths (empirical trade stream with
   measured loss-dependence) at different risk fractions, with and
   without cooldown. Where does ruin probability spike?
4. STATE-DEPENDENT EXIT: after bar 1, group trades by current PnL.
   Is E[continuation] negative in any state? If so, cutting there is
   a mathematically justified loss reducer (smarter than fixed hold).
"""

import pandas as pd
import numpy as np
import os

DATA_DIR = "data"
BROKER_FLOOR = {"crash1000": 1.14, "crash500": 1.14, "boom1000": 1.14, "boom500": 1.14}

def load_candles(symbol):
    fpath = os.path.join(DATA_DIR, f"candles_{symbol}_1min.parquet")
    if not os.path.exists(fpath):
        return None
    return pd.read_parquet(fpath)

def find_bar_spikes(df, threshold=3.0):
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    ranges = highs - lows
    n = len(ranges)
    median_ranges = np.full(n, 0.01)
    for i in range(20, n):
        median_ranges[i] = np.median(ranges[max(0, i-20):i])
    spikes = []
    for i in range(1, n - 12):
        bar_range = ranges[i]
        if median_ranges[i] > 0 and bar_range > threshold * median_ranges[i]:
            is_boom = closes[i] > opens[i]
            is_crash = closes[i] < opens[i]
            if is_boom or is_crash:
                spikes.append({'type': 'boom' if is_boom else 'crash',
                               'idx': i, 'range': bar_range,
                               'median': median_ranges[i],
                               'ratio': bar_range / median_ranges[i]})
    return spikes

def simulate_trades(df, spikes, symbol, hold=3, cooldown=0):
    """Return full trade records incl. per-bar PnL path and spike seq number."""
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    floor = BROKER_FLOOR.get(symbol, 1.14)

    trades = []
    skip_cool = 0
    spike_seq = 0          # count of spikes since last loss (for cooldown decay study)
    last_loss = False

    for spike in spikes:
        idx = spike['idx']
        if idx + hold + 2 >= len(closes):
            continue

        if skip_cool > 0:
            skip_cool -= 1
            spike_seq += 1
            continue

        median = spike['median']
        sl_dist = max(1.0 * median, floor)
        tp_dist = 2.0 * sl_dist
        direction = 1 if spike['type'] == 'crash' else -1
        entry = closes[idx + 1]

        exit_price = None
        reason = 'timeout'
        bar_path = []   # (bar_offset, pnl_at_close)

        for j in range(idx + 2, idx + hold + 2):
            if j >= len(closes):
                break
            hi, lo = highs[j], lows[j]
            best = (hi - entry) if direction == 1 else (entry - lo)
            worst = (lo - entry) if direction == 1 else (entry - hi)
            bar_pnl_at_close = (closes[j] - entry) * direction
            bar_path.append((j - (idx + 1), bar_pnl_at_close))

            if worst <= -sl_dist:
                exit_price = entry - sl_dist * direction
                reason = 'sl'
                break
            if best >= tp_dist:
                exit_price = entry + tp_dist * direction
                reason = 'tp'
                break
        else:
            exit_price = closes[min(idx + hold + 1, len(closes) - 1)]
            reason = 'timeout'

        pnl = (exit_price - entry) * direction
        lost = pnl <= 0
        trades.append({'pnl': pnl, 'reason': reason, 'lost': lost,
                       'prev_lost': last_loss, 'seq_after_loss': spike_seq,
                       'bar_path': bar_path, 'sl_dist': sl_dist})
        if lost:
            spike_seq = 0
            if cooldown > 0:
                skip_cool = cooldown
        else:
            spike_seq += 1
        last_loss = lost

    return trades

def sect(title):
    print("\n" + "=" * 110)
    print(title)
    print("=" * 110)

for sym in ["crash1000", "crash500", "boom1000", "boom500"]:
    df = load_candles(sym)
    if df is None:
        continue
    spikes = find_bar_spikes(df)
    trades = simulate_trades(df, spikes, sym, hold=3, cooldown=0)

    pnls = np.array([t['pnl'] for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    p_win = len(wins) / len(pnls)
    avg_win = wins.mean()
    avg_loss = abs(losses.mean())
    b = avg_win / avg_loss if avg_loss > 0 else 0
    # Kelly fraction of BANKROLL risked per trade (point units -> fraction)
    # Standard Kelly: f* = (b*p - q) / b   where p=win prob, q=1-p
    kelly = (b * p_win - (1 - p_win)) / b if b > 0 else 0

    sect(f"{sym.upper()} — {len(trades)} trades, hold=3, no cooldown")

    # 1. CONDITIONAL LOSS PROBABILITY
    after_win = [t for t in trades if not t['prev_lost']]
    after_loss = [t for t in trades if t['prev_lost']]
    def loss_rate(ts):
        if not ts:
            return float('nan')
        return sum(1 for t in ts if t['lost']) / len(ts)

    r_after_win = loss_rate(after_win)
    r_after_loss = loss_rate(after_loss)
    print(f"\n[1] CONDITIONAL LOSS PROBABILITY")
    print(f"    P(loss | prev trade WON)  = {r_after_win*100:6.1f}%   (n={len(after_win)})")
    print(f"    P(loss | prev trade LOST) = {r_after_loss*100:6.1f}%   (n={len(after_loss)})")
    if r_after_win > 0:
        print(f"    Loss multiplier after a loss: {r_after_loss/r_after_win:.2f}x  "
              f"{'-> cooldown MATHEMATICALLY justified' if r_after_loss > r_after_win else '-> no dependence'}")

    # 2. COOLDOWN DECAY: loss rate by spike position after a loss
    print(f"\n[2] COOLDOWN DECAY — loss rate of the k-th spike after a loss")
    print(f"    spike k: ", end="")
    for k in range(1, 7):
        sub = [t for t in trades if t['seq_after_loss'] == k]
        if sub:
            print(f"  k={k}:{loss_rate(sub)*100:5.1f}%", end="")
    print("   (baseline loss rate: %.1f%%)" % ((1 - p_win) * 100))

    # 3. KELLY
    print(f"\n[3] KELLY CRITERION")
    print(f"    win rate p = {p_win*100:.1f}% | avg win = {avg_win:.3f} | avg loss = {avg_loss:.3f} | b = {b:.2f}")
    print(f"    Kelly fraction f* = {kelly*100:.2f}% of equity risked per trade")
    print(f"    Half-Kelly (recommended in practice) = {kelly*50:.2f}%")
    # What the EA actually risks: RiskPercent 2% with floored SL
    print(f"    Current EA: RiskPercent=2.0 -> {('ABOVE Kelly' if kelly < 2.0/100 else 'at/below Kelly')}")

# 4. RISK OF RUIN simulation with dependence (crash1000 numbers)
sect("RISK OF RUIN — CRASH1000, empirical trade stream with loss-dependence")
df = load_candles("crash1000")
spikes = find_bar_spikes(df)

def run_ruin(sym, risk_frac, cooldown, n_paths=3000, trades_per_path=600, start=1000.0):
    """Simulate account paths. Each trade risks risk_frac of equity.
    Win adds avg_win in 'risk units'; loss subtracts avg_loss units.
    Position size = risk_frac * equity / sl_dist_price, so pnl in $ tracks
    the empirical point PnL distribution scaled by position."""
    df = load_candles(sym)
    spikes_all = find_bar_spikes(df)
    trades = simulate_trades(df, spikes_all, sym, hold=3, cooldown=cooldown)
    pnls = np.array([t['pnl'] for t in trades])
    # sample with replacement from empirical stream
    rng = np.random.default_rng(42)
    sl_dist = 1.14  # crash1000 broker floor
    ruined = 0
    for _ in range(n_paths):
        eq = start
        for __ in range(trades_per_path):
            idx = rng.integers(0, len(pnls))
            pts = pnls[idx]
            # position sized so a full SL loss = risk_frac of equity
            risk_per_pt = (eq * risk_frac) / sl_dist
            eq += pts * risk_per_pt
            if eq <= 0:
                ruined += 1
                break
    return ruined / n_paths

for risk in [0.005, 0.01, 0.02, 0.03, 0.05]:
    r_cd0 = run_ruin("crash1000", risk, cooldown=0)
    r_cd3 = run_ruin("crash1000", risk, cooldown=3)
    print(f"    risk {risk*100:4.1f}%/trade: ruin prob no-cooldown={r_cd0*100:5.1f}%   with-cooldown={r_cd3*100:5.1f}%")

# 5. STATE-DEPENDENT EXIT (crash1000): given PnL state after bar 1, E[continuation]
sect("STATE-DEPENDENT EXIT — CRASH1000: after bar 1, what does holding bar 2-3 add?")
df = load_candles("crash1000")
spikes = find_bar_spikes(df)
trades = simulate_trades(df, spikes, "crash1000", hold=3, cooldown=0)
# trades that survived past bar 1 (no tp/sl on bar 1) and have bar_path len>=2
print(f"    {'state after bar1':<24}{'n':>6}{'E[close now]':>13}{'E[hold to bar3]':>16}{'hold better?':>14}")
bins = [(-1e9, -0.5), (-0.5, -0.2), (-0.2, 0), (0, 0.2), (0.2, 0.5), (0.5, 1e9)]
for lo, hi in bins:
    sub = [t for t in trades if len(t['bar_path']) >= 2 and lo <= t['bar_path'][0][1] / t['sl_dist'] < hi]
    if len(sub) < 30:
        continue
    now = np.mean([t['bar_path'][0][1] for t in sub])
    final = np.mean([t['pnl'] for t in sub])
    better = "HOLD" if final > now else "CUT"
    print(f"    pnl={lo:+.1f}..{hi:+.1f} xSL        {len(sub):>6}{now:>13.4f}{final:>16.4f}{better:>14}")