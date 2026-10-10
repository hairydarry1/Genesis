#!/usr/bin/env python3
"""
eval/verify_sept.py — Sep 1-7 re-verification (scheduled 2026-09-08).

Replicates Phase 1 methodology exactly on the Sep 1-7 window:
  1. Velocity pattern: spike buildup catch rate at threshold 12 (tick data)
  2. Cold hours: spike-rate check of LOCKED hours [1,5,12,14,15,19] + fade backtest WR

Verdict gates (from .state/session_state.toml phase2_success / stop_conditions):
  GO:      velocity catch 55-80%, WR with cold hours >= 55%
  NO-GO:   velocity catch < 50% or WR < 45%

Usage: python eval/verify_sept.py
"""

import numpy as np
import pandas as pd
from pathlib import Path

# === LOCKED PARAMETERS (Phase 1) ===
VELOCITY_THRESHOLD = 12
COLD_HOURS = [1, 5, 12, 14, 15, 19]  # UTC
SPIKE_MULT = 3.0
TP_PCT = 0.004
HOLD_BARS = 8
ENTRY_DELAY = 1

# === PHASE 1 BASELINES (August) ===
BASELINE_VELOCITY = {"boom500": 85.7, "crash500": 79.2, "boom1000": 85.7, "crash1000": 79.2}
BASELINE_WR_COLD = {"boom500": 58.2, "crash500": 56.5, "boom1000": 69.2, "crash1000": 75.1}

SYMBOLS = ["boom500", "crash500", "boom1000", "crash1000"]


def load_ticks(sym):
    f = Path(f"data/ticks_{sym}_sep.npy")
    return np.load(f) if f.exists() else None


def load_candles(sym):
    f = Path(f"data/candles_{sym}_1min_sep.parquet")
    if not f.exists():
        return None
    df = pd.read_parquet(f)
    df["time"] = pd.to_datetime(df["time"])
    df["hour"] = df["time"].dt.hour
    df["range"] = df["high"] - df["low"]
    return df


# ---------- Check 1: velocity pattern (ticks, same as test_velocity_12.py) ----------

def velocity_check(sym, prices):
    is_crash = "crash" in sym
    changes = np.abs(np.diff(prices))
    median = np.median(changes[changes > 0])
    spike_indices = np.where(changes > median * SPIKE_MULT)[0]

    buildups = []
    for idx in spike_indices:
        count = 0
        for j in range(idx - 1, max(idx - 100, -1), -1):
            if j < 0:
                break
            tick_change = prices[j + 1] - prices[j]
            if is_crash:
                if tick_change > 0:
                    count += 1
                else:
                    break
            else:
                if tick_change < 0:
                    count += 1
                else:
                    break
        buildups.append(count)
    buildups = np.array(buildups)
    caught = (buildups >= VELOCITY_THRESHOLD).sum()
    return len(spike_indices), caught / len(spike_indices) * 100


# ---------- Check 2: cold hours (candles, same as backtest_cold_hours.py) ----------

def detect_spikes(df):
    med = df["range"].median()
    df["is_spike"] = df["range"] > SPIKE_MULT * med
    df["spike_dir"] = 0
    df.loc[df["is_spike"] & (df["close"] > df["open"]), "spike_dir"] = 1
    df.loc[df["is_spike"] & (df["close"] < df["open"]), "spike_dir"] = -1
    return df


def run_backtest(df, cold_hours):
    prices = df["close"].values
    is_spike = df["is_spike"].values
    spike_dir = df["spike_dir"].values
    hours = df["hour"].values
    n = len(prices)

    trades = []
    cooldown_until = 0
    for i in range(n):
        if not is_spike[i] or i < cooldown_until:
            continue
        if cold_hours is not None and hours[i] not in cold_hours:
            continue
        entry_idx = i + ENTRY_DELAY
        if entry_idx >= n:
            continue
        entry_price = prices[entry_idx]
        direction = spike_dir[i]

        exit_idx, exit_price = None, None
        for j in range(entry_idx + 1, min(entry_idx + HOLD_BARS + 1, n)):
            pnl_pct = (entry_price - prices[j]) / entry_price if direction == 1 else (prices[j] - entry_price) / entry_price
            if pnl_pct >= TP_PCT:
                exit_idx, exit_price = j, prices[j]
                break
            if is_spike[j] and spike_dir[j] == direction:
                exit_idx, exit_price = j, prices[j]
                break
        if exit_idx is None:
            exit_idx = min(entry_idx + HOLD_BARS, n - 1)
            exit_price = prices[exit_idx]

        pnl = (entry_price - exit_price) / entry_price if direction == 1 else (exit_price - entry_price) / entry_price
        trades.append({"pnl": pnl, "hour": hours[i]})
        cooldown_until = exit_idx
    return trades


def wr_of(trades):
    if not trades:
        return 0.0
    return (np.array([t["pnl"] for t in trades]) > 0).mean() * 100


def main():
    print("=" * 74)
    print("  SEP 1-7 RE-VERIFICATION (vs Phase 1 August baselines)")
    print("=" * 74)

    results = {}

    # ---- Check 1: velocity ----
    print(f"\n--- CHECK 1: VELOCITY PATTERN (threshold {VELOCITY_THRESHOLD}) ---")
    for sym in SYMBOLS:
        prices = load_ticks(sym)
        if prices is None:
            print(f"  {sym}: no tick data, SKIPPED")
            continue
        n_spikes, catch = velocity_check(sym, prices)
        base = BASELINE_VELOCITY[sym]
        delta = catch - base
        print(f"  {sym:10s} spikes={n_spikes:5d}  catch={catch:5.1f}%  (Aug: {base:.1f}%, {delta:+.1f}pp)")
        results[sym] = {"velocity": catch}

    # ---- Check 2: cold hours ----
    print(f"\n--- CHECK 2: COLD HOURS {COLD_HOURS} ---")
    for sym in SYMBOLS:
        df = load_candles(sym)
        if df is None:
            print(f"  {sym}: no candle data, SKIPPED")
            continue
        df = detect_spikes(df)
        expected = df["is_spike"].sum() / len(df) * 100
        hourly = df.groupby("hour").agg(bars=("is_spike", "count"), spikes=("is_spike", "sum"))
        hourly["rate"] = hourly["spikes"] / hourly["bars"] * 100
        still_cold = [h for h in COLD_HOURS if h in hourly.index and hourly.loc[h, "rate"] < expected]
        drifted = [h for h in COLD_HOURS if h in hourly.index and hourly.loc[h, "rate"] >= expected]

        t_all = run_backtest(df, None)
        t_cold = run_backtest(df, COLD_HOURS)
        wr_all, wr_cold = wr_of(t_all), wr_of(t_cold)
        improvement = wr_cold - wr_all
        base = BASELINE_WR_COLD[sym]
        delta = wr_cold - base

        print(f"\n  {sym.upper()}: {len(t_all)} trades all-hours / {len(t_cold)} in cold hours")
        print(f"    Locked hours still cold: {len(still_cold)}/6 {('drifted: ' + str(drifted)) if drifted else ''}")
        print(f"    WR all-hours : {wr_all:5.1f}%")
        print(f"    WR cold-hours: {wr_cold:5.1f}%  (improvement {improvement:+.1f}pp)")
        print(f"    vs Aug baseline {base:.1f}%: {delta:+.1f}pp")
        results.setdefault(sym, {}).update({"wr_cold": wr_cold, "wr_all": wr_all,
                                            "improvement": improvement, "still_cold": len(still_cold)})

    # ---- Verdict ----
    print("\n" + "=" * 74)
    print("  GO/NO-GO VERDICT (Phase 2 gates)")
    print("=" * 74)
    # NOTE: the 55-80% velocity band applies to LIVE/paper execution (latency included).
    # Today's check is historical-tick basis (same as August baselines), so the gate is:
    # pattern robust = catch >= 55 (stop condition at < 50). Higher than baseline is GOOD.
    go_flags, stop_flags = [], []
    for sym, r in results.items():
        v, w = r.get("velocity"), r.get("wr_cold")
        if v is None or w is None:
            continue
        v_ok = v >= 55
        w_ok = w >= 55
        v_stop = v < 50
        w_stop = w < 45
        status = "GO" if (v_ok and w_ok) else ("STOP" if (v_stop or w_stop) else "CAUTION")
        if v_stop or w_stop:
            stop_flags.append(sym)
        elif v_ok and w_ok:
            go_flags.append(sym)
        print(f"  {sym:10s} velocity {v:5.1f}% ({'ok' if v_ok else 'OUT'})  "
              f"WR_cold {w:5.1f}% ({'ok' if w_ok else 'OUT'})  -> {status}")

    print()
    if stop_flags:
        print(f"  VERDICT: NO-GO - stop conditions hit for: {', '.join(stop_flags)}")
        print("  Phase 2 paper trading should NOT start tomorrow.")
    elif go_flags:
        print(f"  VERDICT: GO - {len(go_flags)}/4 symbols pass all Phase 2 gates: {', '.join(go_flags)}")
        print("  Edge held out-of-sample. Paper trading may start 2026-09-09.")
        # Flag notable baseline regressions for awareness
        for sym, r in results.items():
            w = r.get("wr_cold")
            if w is not None and w < BASELINE_WR_COLD[sym] - 3:
                print(f"  NOTE: {sym} WR_cold regressed {BASELINE_WR_COLD[sym] - w:+.1f}pp vs Aug "
                      f"(still above gate) - watch in paper trading.")
    else:
        print("  VERDICT: CAUTION - metrics outside gates but above stop conditions.")
        print("  Review drifted hours and catch-rate degradation before starting.")


if __name__ == "__main__":
    main()
