#!/usr/bin/env python3
"""
eval/diagnose_replay_gap.py — Why does replay PnL differ from Phase 1 backtest?

Phase 1 backtest (eval/backtest_cold_hours.py): exits on M1 bar closes only.
  TP when the close is 0.4% favorable; NO intrabar SL; timeout 8 bars.
Replay executor (eval/paper_trading_protected.py): tick-driven.
  TP = 0.4% intrabar, SL = 3.0x median M1 range (a much larger distance),
  safety_spike exit, timeout 6 bars, entry on first tick of next bar.

This script decomposes the gap on the SAME Sep 1-7 data:
  1. SL/TP distance asymmetry per symbol (median M1 range vs 0.4% of price)
  2. Executor replay on real timestamps + realized exit audit (sl-exit losses)
  3. Phase1-style bar-close backtest on Sep (already computed by verify_sept) vs
     apples-to-apples executor variants:
       A) full executor (as built)
       B) + timeout 8 bars (match Phase 1 hold)
       C) + TP on bar close only (tick fills disabled)
       D) + no SL at all (Phase 1 semantics)

Usage: python eval/diagnose_replay_gap.py
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.paper_trading_protected import (  # noqa: E402
    EquityBook, SymbolEngine, COLD_HOURS, TP_PCT, SPIKE_MULT, SAFETY_MULT,
    MAX_DAILY_LOSS, MAX_CONSEC_LOSSES,
)

# Diagnostics must NEVER write to the live paper-trading tracker: OPEN rows
# are logged from SymbolEngine._open on every replayed entry. Silence the
# tracker writer at IMPORT time — gating it in main() is not enough, because
# ad-hoc runs of this module (python -c "from ... import run_variant") would
# still leak rows into the live session's CSV.
import eval.paper_trading_protected as _ptp
_ptp.log_trade = lambda *a, **k: None

SYMBOLS = ["boom500", "crash500", "boom1000", "crash1000"]


def load_candles(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min_sep.parquet")
    df["time"] = pd.to_datetime(df["time"])
    df["hour"] = df["time"].dt.hour
    df["range"] = df["high"] - df["low"]
    return df


# ---------- 1. asymmetry ----------

def asymmetry_report():
    print("=" * 78)
    print("  1) SL vs TP DISTANCE ASYMMETRY (median M1 range, Sep 1-7)")
    print("=" * 78)
    for sym in SYMBOLS:
        df = load_candles(sym)
        med = df["range"].median()
        med_pct = med / df["close"].mean() * 100
        sl_dist = SAFETY_MULT * med
        sl_pct = sl_dist / df["close"].mean() * 100
        ratio = sl_pct / (TP_PCT * 100)
        print(f"  {sym:10s} med_range={med:8.3f} ({med_pct:4.2f}% of px)  "
              f"SL={sl_pct:5.2f}%  TP={TP_PCT*100:.2f}%  "
              f"SL/TP = {ratio:5.1f}x")


# ---------- 2. tick loaders with REAL timestamps ----------

def load_ticks(sym):
    """Load tick npy + candle timestamps -> (ts_seconds, price) pairs.

    Maps tick i to bar floor(i*n_bars/n_ticks) (ticks-per-minute is roughly
    uniform on these symbols), then spreads each bar's ticks across its 60s.
    """
    prices = np.load(f"data/ticks_{sym}_sep.npy")
    df = load_candles(sym)
    epoch0 = df["time"].astype("int64").to_numpy() // 10**9
    n_bars, n_ticks = len(epoch0), len(prices)
    bar_of_tick = np.minimum((np.arange(n_ticks) * n_bars) // n_ticks, n_bars - 1)
    counts = np.bincount(bar_of_tick, minlength=n_bars)
    starts = np.concatenate(([0], np.cumsum(counts)))
    rank = np.arange(n_ticks) - starts[bar_of_tick]
    cnt = counts[bar_of_tick]
    ts = epoch0[bar_of_tick] + rank * (60.0 / np.maximum(cnt, 1))
    return ts, prices


# ---------- 3. instrumented executor ----------

# NOTE: day-rollover now lives in the base EquityBook.note_ts(), called from
# SymbolEngine.on_tick(), so replays on historical timestamps roll correctly.

class DiagEngine(SymbolEngine):
    """SymbolEngine + trade log so we can audit exits."""

    def __init__(self, symbol, book, cfg, fixed_sizing=False):
        super().__init__(symbol, book, fixed_sizing=fixed_sizing)
        self.cfg = cfg          # dict: timeout_bars, tick_tp, use_sl, ...
        self.trades = []
        self.cd_bars = 0        # EA-style bar-based loss cooldown (cfg['cooldown_bars'])

    def _exit(self, price, reason, ts):
        pos = self.position
        short = pos["direction"] == "short"
        move_price = (pos["entry"] - price) if short else (price - pos["entry"])
        pnl = move_price * pos["units"]
        # EA v2.14 LossCooldownBars: skip entries for N bars after a LOSS
        if pnl <= 0 and self.cfg.get("cooldown_bars", 0) > 0:
            self.cd_bars = self.cfg["cooldown_bars"]
        self.trades.append({
            "symbol": self.symbol, "reason": reason, "pnl_usd": pnl,
            "move_pct": move_price / pos["entry"] * 100,
            "bars": self.bars_held,
            "hour": datetime.fromtimestamp(ts, tz=timezone.utc).hour,
            "entry_px": pos["entry"], "exit_px": price, "units": pos["units"],
            "entry_ts": pos.get("entry_ts"), "exit_ts": ts,
            "equity_at": self.book.equity,
        })
        self.book.settle(pnl)
        self.book.on_trade_closed(pnl)
        self.position = None
        self.cooldown_until_bar = self.cur_minute_bar_index() + 1

    def _manage_position(self, price, change, ts):
        pos = self.position
        short = pos["direction"] == "short"
        adverse = (change > 0) if short else (change < 0)
        move_price = (pos["entry"] - price) if short else (price - pos["entry"])

        # SL: cfg['sl_mult'] x median range (0 disables)
        if self.cfg["use_sl"] and self.median_range is not None:
            if move_price <= -self.cfg["sl_mult"] * self.median_range:
                self._exit(price, "sl", ts)
                return
        # TP: cfg['tp_mult'] x median range, or 0.4% of entry if tp_mult==0
        if self.cfg["tick_tp"] and self.median_range is not None:
            tp_dist = (self.cfg["tp_mult"] * self.median_range) if self.cfg["tp_mult"] > 0 \
                else TP_PCT * pos["entry"]
            if move_price >= tp_dist:
                self._exit(price, "tp", ts)
                return
        if self.cfg.get("use_safety", True) and adverse and self.median_range is not None:
            if abs(change) > 2.5 * self.spike_mult * self.median_range:
                self.safety_exits += 1
                self._exit(price, "safety_spike", ts)

    def _close_bar(self, ts):
        o, h, l, c = self.bar["o"], self.bar["h"], self.bar["l"], self.bar["c"]
        rng = h - l
        self.ranges.append(rng)
        self.median_range = float(np.median(self.ranges)) if len(self.ranges) >= 50 else self.median_range
        # EA decrements its bar cooldown at the start of each new bar
        if self.cd_bars > 0:
            self.cd_bars -= 1

        is_spike = (self.median_range is not None and rng > SPIKE_MULT * self.median_range)
        spike_dir = 0
        if is_spike:
            spike_dir = 1 if c > o else -1
            self.spikes_seen += 1

        # Manage open position at bar close
        if self.position is not None:
            self.bars_held += 1
            # Bar-close TP when tick fills are disabled
            if not self.cfg["tick_tp"]:
                move_price = (self.position["entry"] - c) if self.position["direction"] == "short" \
                    else (c - self.position["entry"])
                if move_price >= TP_PCT * self.position["entry"]:
                    self._exit(c, "tp", ts)
                    self._maybe_queue_entry(is_spike, spike_dir, ts)
                    return
            if self.bars_held >= self.cfg["timeout_bars"]:
                self._exit(c, "timeout", ts)
            elif is_spike and spike_dir == self.position["spike_dir"]:
                self._exit(c, "spike_against", ts)

        self._maybe_queue_entry(is_spike, spike_dir, ts)

    def _maybe_queue_entry(self, is_spike, spike_dir, ts):
        if not (is_spike and self.position is None and self.pending_entry is None):
            return
        if self.cfg.get("use_halts", True) and (self.book.daily_halt or self.book.global_halt):
            return
        if self.cur_minute_bar_index() < self.cooldown_until_bar:
            return
        # EA v2.14 bar cooldown blocks entries (checked at entry time, like CheckEntry)
        if self.cd_bars > 0:
            return
        hours = EA_COLD_HOURS if self.cfg.get("cold_hours", "P1") == "EA" else COLD_HOURS
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        if hour not in hours:
            self.entries_skipped_cold += 1
        else:
            self.pending_entry = "short" if spike_dir == 1 else "long"


def run_variant(sym, ts, prices, cfg, equity=200.0, fixed_sizing=False):
    book = EquityBook(equity)
    eng = DiagEngine(sym, book, cfg, fixed_sizing=fixed_sizing)
    for t, p in zip(ts, prices):
        eng.on_tick(t, float(p))
    return eng


# EA cold-hours range (v2.15 defaults): hours 0-5 inclusive (start<end =>
# hour >= 0 and hour < 6), evaluated on the spike BAR's hour, matching
# CheckEntry's TimeCurrent-based gate as closely as replay allows.
EA_COLD_HOURS = set(range(0, 6))


# ---------- 4. phase1-style bar-close backtest (reference, same as verify_sept) ----------

def phase1_backtest(sym, cold_hours=COLD_HOURS):
    df = load_candles(sym)
    med = df["range"].median()
    df["is_spike"] = df["range"] > SPIKE_MULT * med
    df["spike_dir"] = 0
    df.loc[df["is_spike"] & (df["close"] > df["open"]), "spike_dir"] = 1
    df.loc[df["is_spike"] & (df["close"] < df["open"]), "spike_dir"] = -1
    prices = df["close"].values
    is_spike = df["is_spike"].values
    spike_dir = df["spike_dir"].values
    hours = df["hour"].values
    n = len(prices)
    trades = []
    cooldown_until = 0
    HOLD_BARS, ENTRY_DELAY = 8, 1
    for i in range(n):
        if not is_spike[i] or i < cooldown_until:
            continue
        if hours[i] not in cold_hours:
            continue
        e = i + ENTRY_DELAY
        if e >= n:
            continue
        entry = prices[e]
        d = spike_dir[i]
        exit_idx, exit_px, reason = None, None, "timeout"
        for j in range(e + 1, min(e + HOLD_BARS + 1, n)):
            pnl_pct = (entry - prices[j]) / entry if d == 1 else (prices[j] - entry) / entry
            if pnl_pct >= TP_PCT:
                exit_idx, exit_px, reason = j, prices[j], "tp"
                break
            if is_spike[j] and spike_dir[j] == d:
                exit_idx, exit_px, reason = j, prices[j], "spike_against"
                break
        if exit_idx is None:
            exit_idx = min(e + HOLD_BARS, n - 1)
            exit_px = prices[exit_idx]
        pnl = (entry - exit_px) / entry if d == 1 else (exit_px - entry) / entry
        trades.append(pnl * 100)  # percent
        cooldown_until = exit_idx
    return trades


def pct_trades_to_usd(pct_list, sym, equity=200.0, risk=0.02):
    """Convert %-moves to USD using executor sizing: units = risk*equity / sl_dist.
    Uses the executor's 1.5x-median attached stop for exact parity."""
    df = load_candles(sym)
    sl_dist = 1.5 * df["range"].median()
    units = risk * equity / sl_dist
    return [p / 100 * units * df["close"].mean() for p in pct_list]


def main():
    asymmetry_report()

    # ---- 2) realized sl-exit audit from executor replay (real timestamps) ----
    print("\n" + "=" * 78)
    print("  2) EXECUTOR REPLAY (real timestamps) + SL-EXIT AUDIT")
    print("=" * 78)
    full_cfg = {"timeout_bars": 8, "tick_tp": True, "use_sl": True, "sl_mult": 1.5, "tp_mult": 2.0, "use_safety": True}
    for sym in SYMBOLS:
        ts, prices = load_ticks(sym)
        eng = run_variant(sym, ts, prices, full_cfg)
        t = pd.DataFrame(eng.trades)
        if t.empty:
            print(f"  {sym}: no trades")
            continue
        sl = t[t["reason"] == "sl"]
        tp = t[t["reason"] == "tp"]
        print(f"\n  {sym.upper()}: {len(t)} closed trades | equity {eng.book.equity:.2f} "
              f"({(eng.book.equity/200-1)*100:+.1f}%)")
        print(f"    WR {100*(t['pnl_usd']>0).mean():.1f}% | total ${t['pnl_usd'].sum():+.2f}")
        if len(sl):
            print(f"    sl exits: n={len(sl)}  avg move {sl['move_pct'].mean():+.3f}%  "
                  f"total ${sl['pnl_usd'].sum():+.2f}  (intended loss/trade = "
                  f"${0.02*200:.2f}; sizing uses 1.5x-median attached stop)")
        if len(tp):
            print(f"    tp exits: n={len(tp)}  avg move {tp['move_pct'].mean():+.3f}%  "
                  f"total ${tp['pnl_usd'].sum():+.2f}")
        print(f"    exit mix: {t['reason'].value_counts().to_dict()}")

    # ---- 3) apples-to-apples: executor variants on same ticks ----
    print("\n" + "=" * 78)
    print("  3) VARIANT LADDER (same ticks, same sizing) — isolate the gap")
    print("=" * 78)
    variants = {
        "A EA geometry (1.5xSL,2xTP,to8)": {"timeout_bars": 8, "tick_tp": True, "use_sl": True, "sl_mult": 1.5, "tp_mult": 2.0, "use_safety": True, "use_halts": True},
        "B EA stops, no attach (1xSL)":    {"timeout_bars": 8, "tick_tp": True, "use_sl": True, "sl_mult": 1.0, "tp_mult": 2.0, "use_safety": True, "use_halts": True},
        "C Phase1 sems + halts":           {"timeout_bars": 8, "tick_tp": False, "use_sl": False, "sl_mult": 0, "tp_mult": 0, "use_safety": False, "use_halts": True},
        "D Phase1 sems, NO halts":         {"timeout_bars": 8, "tick_tp": False, "use_sl": False, "sl_mult": 0, "tp_mult": 0, "use_safety": False, "use_halts": False},
    }
    totals = {name: 0.0 for name in variants}
    for sym in SYMBOLS:
        ts, prices = load_ticks(sym)
        print(f"\n  {sym.upper()}:")
        for name, cfg in variants.items():
            eng = run_variant(sym, ts, prices, cfg)
            t = pd.DataFrame(eng.trades)
            pnl = t["pnl_usd"].sum() if not t.empty else 0.0
            wr = 100 * (t["pnl_usd"] > 0).mean() if not t.empty else 0.0
            totals[name] += pnl
            print(f"    {name:34s} n={len(t):4d}  WR {wr:5.1f}%  PnL ${pnl:+8.2f}")

    print(f"\n  {'ALL-SYMBOL TOTALS':34s}")
    for name, pnl in totals.items():
        print(f"    {name:34s} PnL ${pnl:+8.2f}")

    # ---- 3b) EA-DEFAULTS LADDER: decompose EA v2.15 defaults -> Phase 1 ----
    print("\n" + "=" * 78)
    print("  3b) EA-DEFAULTS LADDER (EA v2.15 config -> Phase 1 semantics)")
    print("=" * 78)
    ea_variants = {
        "E0 EA defaults (to5,SL1x,TP2x intrabar, safety, hrs0-6, cd10)":
            {"timeout_bars": 5, "tick_tp": True, "use_sl": True, "sl_mult": 1.0, "tp_mult": 2.0,
             "use_safety": True, "use_halts": False, "cold_hours": "EA", "cooldown_bars": 10},
        "E1 E0 w/ hold 8":
            {"timeout_bars": 8, "tick_tp": True, "use_sl": True, "sl_mult": 1.0, "tp_mult": 2.0,
             "use_safety": True, "use_halts": False, "cold_hours": "EA", "cooldown_bars": 10},
        "E2 E1 w/ Phase1 TP (0.4pct, bar-close)":
            {"timeout_bars": 8, "tick_tp": False, "use_sl": True, "sl_mult": 1.0, "tp_mult": 2.0,
             "use_safety": True, "use_halts": False, "cold_hours": "EA", "cooldown_bars": 10},
        "E3 E2 w/o tick safety exit":
            {"timeout_bars": 8, "tick_tp": False, "use_sl": True, "sl_mult": 1.0, "tp_mult": 2.0,
             "use_safety": False, "use_halts": False, "cold_hours": "EA", "cooldown_bars": 10},
        "E4 E3 w/o SL (Phase1 exits, EA hours)":
            {"timeout_bars": 8, "tick_tp": False, "use_sl": False, "sl_mult": 0, "tp_mult": 0,
             "use_safety": False, "use_halts": False, "cold_hours": "EA", "cooldown_bars": 10},
        "E5 E4 w/ cold hours [1,5,12,14,15,19] (= Phase1 sems, no cd)":
            {"timeout_bars": 8, "tick_tp": False, "use_sl": False, "sl_mult": 0, "tp_mult": 0,
             "use_safety": False, "use_halts": False, "cold_hours": "P1", "cooldown_bars": 0},
        "E6 E5 + cooldown 10 bars (full EA-align candidate)":
            {"timeout_bars": 8, "tick_tp": False, "use_sl": False, "sl_mult": 0, "tp_mult": 0,
             "use_safety": False, "use_halts": False, "cold_hours": "P1", "cooldown_bars": 10},
    }
    ea_totals = {name: 0.0 for name in ea_variants}
    for sym in SYMBOLS:
        ts, prices = load_ticks(sym)
        print(f"\n  {sym.upper()}:")
        for name, cfg in ea_variants.items():
            eng = run_variant(sym, ts, prices, cfg)
            t = pd.DataFrame(eng.trades)
            pnl = t["pnl_usd"].sum() if not t.empty else 0.0
            wr = 100 * (t["pnl_usd"] > 0).mean() if not t.empty else 0.0
            ea_totals[name] += pnl
            print(f"    {name:60s} n={len(t):4d}  WR {wr:5.1f}%  PnL ${pnl:+8.2f}")
    print(f"\n  {'EA-LADDER ALL-SYMBOL TOTALS':60s}")
    for name, pnl in ea_totals.items():
        print(f"    {name:60s} PnL ${pnl:+8.2f}")

    # ---- reference: phase1-style bar backtest, %-basis ----
    print("\n" + "=" * 78)
    print("  4) REFERENCE: PHASE1-STYLE BAR BACKTEST on Sep (WR basis, %-pnl)")
    print("=" * 78)
    for sym in SYMBOLS:
        tr = phase1_backtest(sym)
        arr = np.array(tr)
        wr = 100 * (arr > 0).mean() if len(arr) else 0.0
        usd = pct_trades_to_usd(tr, sym)
        print(f"  {sym:10s} n={len(arr):4d}  WR {wr:5.1f}%  "
              f"avg {arr.mean():+.4f}%  USD-equiv (executor sizing) ${np.sum(usd):+8.2f}")


if __name__ == "__main__":
    main()
