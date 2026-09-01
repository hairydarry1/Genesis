"""Verify all strategies on CRASH300 — the actual EA symbol."""
import numpy as np
import pandas as pd
from collections import Counter

df = pd.read_parquet("data/candles_crash300_1min.parquet")
closes = df["close"].values.astype(float)
opens = df["open"].values.astype(float)
highs = df["high"].values.astype(float)
lows = df["low"].values.astype(float)
ranges = highs - lows

# Spike detection
med_range = np.median(ranges[100:])
spike_thresh = med_range * 3.0
spikes = np.where(ranges > spike_thresh)[0]

up_spikes = sum(1 for i in spikes if closes[i] > opens[i])
down_spikes = sum(1 for i in spikes if closes[i] < opens[i])
print(f"CRASH300: {len(closes)} bars, {len(spikes)} spikes")
print(f"  UP: {up_spikes} ({up_spikes/len(spikes)*100:.1f}%) | DOWN: {down_spikes} ({down_spikes/len(spikes)*100:.1f}%)")
print(f"  Median range: {med_range:.4f}")

# Median range buffer
med_buf = np.full(len(closes), 0.01)
for i in range(100, len(closes)):
    med_buf[i] = np.median(ranges[max(0, i - 100):i])

DPP = 0.20  # dollars per point for CRASH300
FIXED_LOTS = 0.20
RISK_PCT = 0.02


def backtest(label, entry_fn, exit_fn, tp_mode="fixed_pct", tp_pct=0.005, max_hold=9):
    """Generic backtest runner."""
    eq = 10000.0
    peak = 10000.0
    trades = []
    in_pos = False
    entry_p = 0.0
    entry_med = 0.0
    bars_held = 0
    state = {"last_sp": -999, "bars_since": 999, "bars_since_any": 999}

    for i in range(101, len(closes)):
        med = med_buf[i - 1]
        if med <= 0:
            med = 0.01
        is_spike = ranges[i - 1] > 3.0 * med
        is_down_spike = is_spike and closes[i - 1] < opens[i - 1]
        is_up_spike = is_spike and closes[i - 1] > opens[i - 1]

        # Update bars since spike
        if is_spike and closes[i - 1] < opens[i - 1]:
            state["last_sp"] = i - 1
            state["bars_since"] = 0
        else:
            state["bars_since"] += 1

        state["bars_since_any"] = state["bars_since_any"] + 1
        if is_spike:
            state["bars_since_any"] = 0

        if in_pos:
            bars_held += 1
            exit_result = exit_fn(i, closes, highs, lows, ranges, med_buf, is_spike,
                                  is_down_spike, is_up_spike, entry_p, entry_med,
                                  bars_held, state)
            if exit_result is not None:
                pnl = exit_result["pnl"]
                reason = exit_result["reason"]
                eq += pnl
                peak = max(peak, eq)
                trades.append({"pnl": pnl, "reason": reason})
                in_pos = False
                continue

        if not in_pos:
            do_entry = entry_fn(i, closes, opens, ranges, med_buf, is_spike,
                                is_down_spike, is_up_spike, state)
            if do_entry:
                entry_p = closes[i]
                entry_med = med_buf[i]
                bars_held = 0
                in_pos = True

    # Print results
    n = len(trades)
    if n == 0:
        print(f"  {label}: No trades!")
        return

    wins = [t for t in trades if t["pnl"] > 0]
    losses = [t for t in trades if t["pnl"] <= 0]
    wr = len(wins) / n * 100
    tw = sum(t["pnl"] for t in wins)
    tl = abs(sum(t["pnl"] for t in losses))
    pf = tw / tl if tl > 0 else 0
    total = sum(t["pnl"] for t in trades)
    dd = (peak - eq) / peak * 100 if peak > 0 else 0

    reasons = Counter(t["reason"] for t in trades)
    reason_pnl = {}
    for t in trades:
        r = t["reason"]
        reason_pnl[r] = reason_pnl.get(r, 0) + t["pnl"]

    print(f"\n  {label}")
    print(f"    Trades:  {n}")
    print(f"    WR:      {wr:.1f}%")
    print(f"    PF:      {pf:.2f}")
    print(f"    PnL:     ${total:+.2f}")
    print(f"    Return:  {(eq / 10000 - 1) * 100:+.2f}%")
    print(f"    Max DD:  {dd:.2f}%")
    for r in sorted(reasons):
        wr_r = sum(1 for t in trades if t["reason"] == r and t["pnl"] > 0) / reasons[r] * 100
        print(f"      {r:15s}: {reasons[r]:4d}T  PnL=${reason_pnl.get(r, 0):+.2f}  WR={wr_r:.0f}%")


# ═══════════════════════════════════════════════════════════════
# STRATEGY 1: SpikeCatcher v2 (current EA) — with SL
# ═══════════════════════════════════════════════════════════════
def sc2_entry(i, closes, opens, ranges, med_buf, is_spike, is_down, is_up, state):
    return state["bars_since"] == 1 and state["last_sp"] >= 0 and med_buf[i] > 0

def sc2_exit(i, closes, highs, lows, ranges, med_buf, is_spike, is_down, is_up,
             entry_p, entry_med, bars_held, state):
    tp_pts = 2.0 * entry_med
    sl_pts = 1.0 * entry_med
    if highs[i] - entry_p >= tp_pts:
        lots = (eq_ref[0] * RISK_PCT) / (sl_pts * DPP)
        return {"pnl": lots * tp_pts * DPP, "reason": "tp"}
    if entry_p - lows[i] >= sl_pts:
        lots = (eq_ref[0] * RISK_PCT) / (sl_pts * DPP)
        return {"pnl": -lots * sl_pts * DPP, "reason": "sl"}
    if bars_held >= 9:
        lots = (eq_ref[0] * RISK_PCT) / (sl_pts * DPP)
        diff = closes[i] - entry_p
        return {"pnl": diff * lots * DPP, "reason": "timeout"}
    return None

# ═══════════════════════════════════════════════════════════════
# STRATEGY 2: No SL — safety exit + timeout
# ═══════════════════════════════════════════════════════════════
def nosl_exit(i, closes, highs, lows, ranges, med_buf, is_spike, is_down, is_up,
              entry_p, entry_med, bars_held, state):
    tp_pts = 2.0 * entry_med
    # Safety exit
    if is_spike and closes[i] > entry_p and bars_held > 1:
        diff = closes[i] - entry_p
        return {"pnl": diff * FIXED_LOTS * DPP, "reason": "safety_exit"}
    # TP
    if highs[i] - entry_p >= tp_pts:
        return {"pnl": tp_pts * FIXED_LOTS * DPP, "reason": "tp"}
    # Timeout
    if bars_held >= 9:
        diff = closes[i] - entry_p
        return {"pnl": diff * FIXED_LOTS * DPP, "reason": "timeout"}
    return None

# ═══════════════════════════════════════════════════════════════
# STRATEGY 3: Tick protection
# ═══════════════════════════════════════════════════════════════
def make_tick_prot_exit(threshold):
    def exit_fn(i, closes, highs, lows, ranges, med_buf, is_spike, is_down, is_up,
                entry_p, entry_med, bars_held, state):
        tp_pts = 2.0 * entry_med
        if is_spike and closes[i] > entry_p and bars_held > 1:
            diff = closes[i] - entry_p
            return {"pnl": diff * FIXED_LOTS * DPP, "reason": "safety_exit"}
        if highs[i] - entry_p >= tp_pts:
            return {"pnl": tp_pts * FIXED_LOTS * DPP, "reason": "tp"}
        if state["bars_since_any"] >= threshold:
            diff = closes[i] - entry_p
            return {"pnl": diff * FIXED_LOTS * DPP, "reason": "tick_prot"}
        if bars_held >= 9:
            diff = closes[i] - entry_p
            return {"pnl": diff * FIXED_LOTS * DPP, "reason": "timeout"}
        return None
    return exit_fn

# ═══════════════════════════════════════════════════════════════
# STRATEGY 4: Spike Fade (isolated only) — TP = 0.5% fixed
# ═══════════════════════════════════════════════════════════════
spike_list = spikes.tolist()

def fade_entry(i, closes, opens, ranges, med_buf, is_spike, is_down, is_up, state):
    if not is_down:
        return False
    # Check isolated
    prev_spike = -999
    next_spike = 999
    for s in spike_list:
        if s < i - 1:
            prev_spike = s
        if s > i - 1 and next_spike == 999:
            next_spike = s
            break
    if (i - 1 - prev_spike) > 3 and (next_spike - (i - 1)) > 3:
        state["last_sp"] = i - 1
        state["bars_since"] = 0
        return True
    return False

def fade_exit(i, closes, highs, lows, ranges, med_buf, is_spike, is_down, is_up,
              entry_p, entry_med, bars_held, state):
    tp_pts = entry_p * 0.005
    if is_spike and closes[i] > entry_p and bars_held > 1:
        diff = closes[i] - entry_p
        return {"pnl": diff * FIXED_LOTS * DPP, "reason": "safety_exit"}
    if highs[i] - entry_p >= tp_pts:
        return {"pnl": tp_pts * FIXED_LOTS * DPP, "reason": "tp"}
    if bars_held >= 8:
        diff = closes[i] - entry_p
        return {"pnl": diff * FIXED_LOTS * DPP, "reason": "timeout"}
    return None


# ═══════════════════════════════════════════════════════════════
# RUN ALL STRATEGIES
# ═══════════════════════════════════════════════════════════════
print("\n" + "=" * 60)
print("  CRASH300 — All Strategies Verified")
print("=" * 60)

# Strategy 1: SC2 with SL
eq_ref = [10000.0]
backtest("[1] SpikeCatcher v2 (with SL)", sc2_entry, sc2_exit)

# Strategy 2: No SL
backtest("[2] No SL (safety + timeout)", sc2_entry, nosl_exit)

# Strategy 3: Tick protection
for thresh in [8, 12, 15, 20]:
    backtest(f"[3] Tick Prot thresh={thresh}", sc2_entry, make_tick_prot_exit(thresh))

# Strategy 4: Spike Fade (isolated)
backtest("[4] Spike Fade (isolated only, TP=0.5%)", fade_entry, fade_exit)

print("\n" + "=" * 60)
print("  VERIFIED: All results above are from real CRASH300 data")
print("=" * 60)
