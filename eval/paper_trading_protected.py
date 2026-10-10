#!/usr/bin/env python3
"""
eval/paper_trading_protected.py — SpikeCatcher v3 Paper Trading Executor (Phase 2)

Implements the locked Phase 1 parameters (semantics verified against the
Phase 1 backtest in eval/diagnose_replay_gap.py):
  - Spike fade entries (fade the spike direction after spike candle close)
  - COLD hours filter [1, 5, 12, 14, 15, 19] UTC
  - TP at 0.4% of entry; NO intrabar SL (safety_multiplier 3.0x = spike
    detection threshold, not a stop)
  - Timeout at 8 bars, spike-against exit at bar close (EA v2.15 tick-level
    safety exit is OFF by default -- docs/REPLAY_VS_BACKTEST_GAP.md; enable
    with --safety-tick-exit for A/B)
  - Risk: 2% of STARTING equity per trade (FIXED sizing — see
    docs/REPLAY_VS_BACKTEST_GAP.md: at PF~1.1, compounding vs fixed sizing
    swings the same trade sequence by ~$770/week), sized on the 1.5x-median
    risk distance. Opt back into compounding with --compounding.
  - Halts OFF by default (measurement mode — see docs/REPLAY_VS_BACKTEST_GAP.md
    "DECISION (Sep 8)"): Phase 2 paper trading risks nothing real and must
    measure the full edge comparably to Phase 1. Opt in with --halts
    (tuned via --daily-loss / --consec-losses).
    Sweep warning (Sep 1-7): with fixed sizing, tight halt thresholds are
    path-toxic — halt-after-loss-cluster selects an unrepresentative trade
    subset (losses cluster on double spikes) while fixed sizing never
    de-levers: daily 15% -> 62 trades, -$257, NEGATIVE equity. Least-harmful
    tested rails: daily 50% / 10 consec (+$483). Phase 3 (live) must NOT
    reuse these rails: it needs a real per-trade SL and de-levering first.

Paper only — no orders are sent to MT5.

Trade logs stamp EVENT time (UTC): live rows equal wall clock; replay rows
carry the historical clock, so replay CSVs are day-analyzable like live logs.

Usage:
  Live:   python eval/paper_trading_protected.py --symbols BOOM1000,CRASH1000 --equity 200
  Replay: python eval/paper_trading_protected.py --replay --symbols boom1000,crash1000 --equity 200
          (replays data/ticks_{sym}_sep.npy through the same engine, no MT5 needed)
  Default = measurement mode (pure Phase 1 semantics, halts off, fixed sizing).
  Add --halts to test the risk rails (then --daily-loss / --consec-losses to
  tune them) and --compounding for live-like sizing. See
  docs/REPLAY_VS_BACKTEST_GAP.md "DECISION (Sep 8)" for why.
"""

import argparse
import heapq
import itertools
import sys
import time as time_mod
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))
from eval.paper_trading_tracker import init_tracker, log_trade, print_report  # noqa: E402

# === LOCKED PARAMETERS (Phase 1) ===
COLD_HOURS = [1, 5, 12, 14, 15, 19]  # UTC
VELOCITY_THRESHOLD = 12              # consecutive adverse ticks -> velocity exit
TICK_EXIT = 8                        # bars to hold before timeout exit (Phase 1 hold)
SAFETY_MULT = 3.0                    # spike candle: range > 3.0x median M1 range (DETECTION, not a stop)
TP_PCT = 0.004                       # take-profit: 0.4% favorable move (Phase 1 backtest basis)
SPIKE_MULT = 3.0                     # spike candle: range > 3x median range
SAFETY_EXIT_MULT = 2.5               # EA v2.15: tick move > 2.5 x spikeMult x median = spike
RISK_PER_TRADE = 0.02                # 2% of starting equity (fixed sizing by default)
# Phase 2 decision (docs/REPLAY_VS_BACKTEST_GAP.md "DECISION (Sep 8)"): halts
# are OFF by default (measurement mode) and these are the opt-in rail
# defaults, set to the LEAST-HARMFUL values from the Sep 1-7 sweep (fixed
# sizing): tight thresholds are path-toxic (daily 15% -> 62 trades, -$257,
# negative equity; 25% identical); 50%/10 barely binds (+$483, 1 halt event).
# These are paper-measurement rails, NOT live risk management: Phase 3 needs
# a real per-trade SL and de-levering compounding before any halt tuning.
MAX_DAILY_LOSS = 0.50                # opt-in daily halt threshold (sweep-least-harmful)
MAX_CONSEC_LOSSES = 10               # opt-in consec-loss halt threshold (resets each UTC day)
WARMUP_BARS = 500                    # rolling median window for ranges

# Stops: Phase 1 semantics have NO intrabar SL (the locked safety_multiplier
# 3.0x is the spike-DETECTION threshold, not a stop). Sizing denominator uses
# a 1.5x-median risk distance for parity with the verified USD-equivalents in
# eval/diagnose_replay_gap.py. The only protective exits are the EA v2.15
# spike-against-tick close and the timeout.
RISK_DIST_MULT = 1.5


def seed_ranges_from_file(symbol, n=WARMUP_BARS):
    """Live-only warmup: pre-load the rolling M1 ranges from the most recent
    local candle file so spike detection is calibrated from the first minute
    (cold start would otherwise need ~50 live minutes of median accumulation).
    Replay keeps cold start — its parity with the Phase 1 backtest is exact
    and must not change."""
    try:
        import glob as _glob
        files = sorted(_glob.glob(f"data/candles_{symbol}_1min*.parquet"))
        if not files:
            return None
        df = pd.read_parquet(files[-1])  # lexicographically latest = *_sep
        rng = (df["high"] - df["low"]).tail(n)
        med = float(rng.median()) if len(rng) >= 50 else None
        return list(rng.astype(float)), med
    except Exception:
        return None


class SymbolEngine:
    """Tick-driven spike-fade paper engine for one BOOM/CRASH symbol."""

    def __init__(self, symbol, book, safety_tick_exit=True, fixed_sizing=False,
                 live_warmup=False, force_cold=False):
        self.symbol = symbol
        self.book = book  # shared EquityBook
        self.fixed_sizing = fixed_sizing  # size on starting equity (Phase 1 reference basis)
        self.force_cold = force_cold      # SOAK ONLY: bypass the cold-hours gate
        self.is_crash = "crash" in symbol.lower()
        # EA v2.15 fires a tick-level safety exit (safety_spike). Backtest
        # evidence (eval/diagnose_replay_gap.py variant ladder) shows this
        # realizes spike-tick fills every time and bleeds equity on 1-min
        # fades; bar-close spike_against exit prices nearly identically.
        self.safety_tick_exit = safety_tick_exit

        # Tick/bar state
        self.cur_minute = None
        self.bar = None                    # current forming M1 bar
        self.ranges = deque(maxlen=WARMUP_BARS)
        self.median_range = None
        self.bars_built = 0
        if live_warmup:
            seeded = seed_ranges_from_file(symbol)
            if seeded:
                hist, med = seeded
                for r in hist:
                    self.ranges.append(r)
                self.median_range = med
                print(f"  [WARMUP] {symbol}: seeded median range {med:.4f} "
                      f"from {len(hist)} historical bars")

        # Trading state
        self.position = None               # dict while a fade is open
        self.pending_entry = None          # direction queued after spike close
        self.bars_held = 0
        self.last_tick_price = None
        self.cooldown_until_bar = -1

        # EA v2.15 safety-exit calibration: single-tick spike threshold.
        # 300/500 bar medians are ~0.9-1.0 vs ~0.36 on the 1000s, so the
        # effective multiplier is tightened per series (see SpikeCatcher_v2.mq5).
        self.spike_mult = 1.0 if "1000" in symbol else 0.4

        # Diagnostics
        self.spikes_seen = 0
        self.safety_exits = 0
        self.entries_skipped_cold = 0
        self.entries_blocked_halt = 0
        self.ticks_seen = 0

    # ---------- market state ----------

    def _close_bar(self, ts):
        o, h, l, c = self.bar["o"], self.bar["h"], self.bar["l"], self.bar["c"]
        rng = h - l
        self.ranges.append(rng)
        self.median_range = float(np.median(self.ranges)) if len(self.ranges) >= 50 else self.median_range

        is_spike = (self.median_range is not None and rng > SPIKE_MULT * self.median_range)
        spike_dir = 0
        if is_spike:
            spike_dir = 1 if c > o else -1
            self.spikes_seen += 1

        # Manage open position at bar close (Phase 1 semantics, in reference
        # order: TP on close, then timeout, then adverse double-spike exit)
        if self.position is not None:
            self.bars_held += 1
            move_close = (self.position["entry"] - c) if self.position["direction"] == "short" \
                else (c - self.position["entry"])
            if move_close >= self.position["tp_dist"]:
                self._exit(c, "tp", ts)
            elif self.bars_held >= TICK_EXIT:
                self._exit(c, "timeout", ts)
            elif is_spike and spike_dir == self.position["spike_dir"]:
                self._exit(c, "spike_against", ts)

        # Queue fade entry on ANY spike bar while flat (incl. one that just
        # closed a position) -- matches Phase 1 reference, where the exit bar
        # itself can trigger the next entry at entry_delay=1
        if (is_spike and self.position is None and self.pending_entry is None
                and self.cur_minute_bar_index() >= self.cooldown_until_bar):
            if self.book.daily_halt or self.book.global_halt:
                self.entries_blocked_halt += 1
            else:
                hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
                # --force-cold (soak validation only): treat every hour as a
                # cold hour so the entry path can be exercised outside the
                # locked window. Never for production measurement.
                if self.force_cold or hour in COLD_HOURS:
                    self.pending_entry = "short" if spike_dir == 1 else "long"
                else:
                    self.entries_skipped_cold += 1

    def cur_minute_bar_index(self):
        # bar index proxy: minute-of-day is enough for cooldown spacing
        return getattr(self, "_bar_idx", 0)

    # ---------- tick handling ----------

    def on_tick(self, ts, price):
        self.book.note_ts(ts)
        minute = int(ts // 60)

        if self.cur_minute is None:
            self.cur_minute = minute
            self.bar = {"o": price, "h": price, "l": price, "c": price}
            self.last_tick_price = price
            return

        if minute != self.cur_minute:
            self._bar_idx = getattr(self, "_bar_idx", 0) + 1
            self.bars_built += 1
            self._close_bar(self.cur_minute * 60 + 59)
            self.cur_minute = minute
            self.bar = {"o": price, "h": price, "l": price, "c": price}
        else:
            self.bar["h"] = max(self.bar["h"], price)
            self.bar["l"] = min(self.bar["l"], price)
            self.bar["c"] = price

        change = price - self.last_tick_price if self.last_tick_price is not None else 0.0
        self.last_tick_price = price

        # Pending fade entry on first tick of new bar
        if self.pending_entry is not None and self.position is None:
            self._open(price, self.pending_entry, ts)
            self.pending_entry = None

        if self.position is not None:
            self._manage_position(price, change, ts)

    # ---------- trading ----------

    def _open(self, price, direction, ts):
        if self.median_range is None:
            return
        # Phase 1 semantics: no SL; TP = 0.4% of entry. Size on the 1.5x-median
        # risk distance (parity with the verified USD-equivalent backtest).
        risk_dist = RISK_DIST_MULT * self.median_range
        tp_dist = TP_PCT * price
        risk_usd = self.book.starting_equity * self.book.risk_per_trade if self.fixed_sizing \
            else self.book.equity * self.book.risk_per_trade
        units = risk_usd / risk_dist if risk_dist > 0 else 0.0
        self.position = {
            "direction": direction,          # short = faded UP spike (boom), long = faded DOWN spike (crash)
            "spike_dir": 1 if direction == "short" else -1,  # ENTRY spike's direction; spike_against fires on same-direction double spike (Phase 1)
            "entry": price, "units": units, "risk_dist": risk_dist, "tp_dist": tp_dist,
        }
        self.bars_held = 0
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        log_trade(self.symbol.upper(), "OPEN", direction, price, 0.0, 0.0,
                  "entry", 0, dt.hour, dt.hour in COLD_HOURS,
                  self.book.equity, event_ts=ts)

    def _manage_position(self, price, change, ts):
        pos = self.position
        short = pos["direction"] == "short"
        adverse = (change > 0) if short else (change < 0)

        move_price = (pos["entry"] - price) if short else (price - pos["entry"])

        # TP: favorable move of 0.4% of entry (Phase 1 backtest basis)
        if move_price >= pos["tp_dist"]:
            self._exit(price, "tp", ts)
            return
        # EA v2.15 safety exit: one tick move > 2.5 x spikeMult x median range
        # against the position = a spike is hitting us. When disabled, the
        # bar-close spike_against exit handles it one bar later.
        if (self.safety_tick_exit and adverse and self.median_range is not None):
            if abs(change) > SAFETY_EXIT_MULT * self.spike_mult * self.median_range:
                self.safety_exits += 1
                self._exit(price, "safety_spike", ts)

    def _exit(self, price, reason, ts):
        pos = self.position
        short = pos["direction"] == "short"
        price_move = (pos["entry"] - price) if short else (price - pos["entry"])
        pnl_usd = price_move * pos["units"]
        self.book.settle(pnl_usd)
        hour = datetime.fromtimestamp(ts, tz=timezone.utc).hour
        log_trade(self.symbol.upper(), "CLOSE", pos["direction"], pos["entry"], price,
                  pnl_usd, reason, self.bars_held, hour, hour in COLD_HOURS, self.book.equity,
                  event_ts=ts)
        self.position = None
        self.cooldown_until_bar = self.cur_minute_bar_index() + 1
        self.book.on_trade_closed(pnl_usd)


class EquityBook:
    """Shared paper equity across symbols with daily-loss and consecutive-loss halts."""

    def __init__(self, starting_equity, risk_per_trade=RISK_PER_TRADE, halts_enabled=True,
                 daily_loss_pct=MAX_DAILY_LOSS, consec_losses=MAX_CONSEC_LOSSES):
        self.equity = starting_equity
        self.starting_equity = starting_equity
        self.risk_per_trade = risk_per_trade
        self.halts_enabled = halts_enabled  # False = measurement mode (--no-halts)
        self.daily_loss_pct = daily_loss_pct
        self.max_consec_losses = consec_losses
        self.day_start_equity = starting_equity
        self.day = datetime.now(timezone.utc).date()
        self.consec_losses = 0
        self.halts_today = 0      # diagnostics: how many halt events fired
        self.daily_halt = False   # no new entries until next UTC day
        self.global_halt = False  # 5 consecutive losses: resets each UTC day

    def note_ts(self, ts):
        """Roll the trading day on the CURRENT tick timestamp.

        Must be called from the tick path, not only on trade closes: after a
        daily halt no trades close, so a close-driven roll would never fire
        and the halt would deadlock the engine for the rest of the session.
        Both halts reset each UTC day: the locked stop conditions are daily
        risk rails (a 5-loss streak on Tuesday must not kill Thursday's
        session over a 14-day paper phase).
        """
        today = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        if today != self.day:
            self.day = today
            self.day_start_equity = self.equity
            self.daily_halt = False
            self.consec_losses = 0
            self.global_halt = False

    def settle(self, pnl_usd):
        self.equity += pnl_usd

    def on_trade_closed(self, pnl_usd):
        if pnl_usd <= 0:
            self.consec_losses += 1
        else:
            self.consec_losses = 0
        if not self.halts_enabled:
            return
        if self.equity <= self.day_start_equity * (1 - self.daily_loss_pct) and not self.daily_halt:
            self.daily_halt = True
            self.halts_today += 1
            print(f"  [HALT] Daily loss limit {self.daily_loss_pct*100:.0f}% hit. No new entries today.")
        if self.consec_losses >= self.max_consec_losses and not self.global_halt:
            self.global_halt = True
            self.halts_today += 1
            print(f"  [HALT] {self.max_consec_losses} consecutive losses. Entries disabled for the day.")


# ---------- runners ----------

def run_live(args, engines, book):
    import MetaTrader5 as mt5
    from data.mt5_tick_connector import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

    MT5_NAMES = {"boom500": "Boom 500 Index", "crash500": "Crash 500 Index",
                 "boom1000": "Boom 1000 Index", "crash1000": "Crash 1000 Index"}

    kwargs = {"timeout": 10000}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        kwargs.update({"login": MT5_LOGIN, "password": MT5_PASSWORD, "server": MT5_SERVER})
    if not mt5.initialize(**kwargs):
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    acct = mt5.account_info()
    print(f"Connected: {acct.login} @ {acct.server} | balance {acct.balance:.2f} {acct.currency}")

    # Make sure every requested symbol is visible to the terminal, and know
    # its point size for the market-closed synthetic-tick fallback.
    point = {}
    for engine in engines:
        name = MT5_NAMES.get(engine.symbol)
        if name is None:
            print(f"  [WARN] unknown symbol key '{engine.symbol}' — will get no ticks")
            continue
        info = mt5.symbol_info(name)
        if info is None:
            print(f"  [WARN] '{name}' not found in terminal — will get no ticks")
        else:
            if not info.visible:
                mt5.symbol_select(name, True)
                print(f"  [INFO] selected '{name}' in Market Watch")
            point[engine.symbol] = info.point

    total_ticks = 0
    started = time_mod.time()
    last_hb = -1e9
    try:
        poll = args.poll_seconds
        limit = f", max-ticks={args.max_ticks}" if args.max_ticks else ""
        if args.max_minutes:
            limit += f", max-minutes={args.max_minutes:g}"
        print(f"Streaming {len(engines)} symbol(s), poll={poll}s{limit}. Ctrl+C to stop.\n")
        while True:
            for engine in engines:
                name = MT5_NAMES.get(engine.symbol)
                info = mt5.symbol_info_tick(name) if name else None
                if info is None or info.bid <= 0:
                    # Market closed (or no quote yet): feed a flat synthetic
                    # tick so bar-building/median calibration still runs —
                    # identical to what a quiet-market bid would deliver.
                    p = point.get(engine.symbol)
                    if p is None:
                        continue
                    info = mt5.symbol_info(name)
                    if info is None:
                        continue
                    px = info.bid if info.bid > 0 else info.trade_tick_price
                    if px <= 0:
                        continue
                    engine.on_tick(time_mod.time(), px)
                    engine.ticks_seen += 1
                    total_ticks += 1
                    continue
                engine.on_tick(time_mod.time(), info.bid)
                engine.ticks_seen += 1
                total_ticks += 1
            if args.max_ticks and total_ticks >= args.max_ticks:
                print(f"\nReached --max-ticks {args.max_ticks}, stopping.")
                break
            elapsed = time_mod.time() - started
            if args.max_minutes and elapsed >= args.max_minutes * 60:
                print(f"\nReached --max-minutes {args.max_minutes:g}, stopping.")
                break
            if elapsed - last_hb >= 300:
                last_hb = elapsed
                bars = sum(e.bars_built for e in engines)
                print(f"  [HB {int(elapsed//60):02d}:{int(elapsed%60):02d}] "
                      f"ticks={total_ticks} bars_closed={bars} equity={book.equity:.2f}")
            time_mod.sleep(poll)
    except KeyboardInterrupt:
        print("\nStopping (Ctrl+C)...")
    finally:
        mt5.shutdown()


# ---------- replay data loading ----------

def load_ticks(sym):
    """Load tick npy + Sep candle timestamps -> (ts_seconds, price).

    Maps tick i to bar floor(i*n_bars/n_ticks) (ticks-per-minute is roughly
    uniform on these symbols), then spreads each bar's ticks across its 60s.
    Real timestamps matter: uniform time synthesis reshuffles the hour-of-day
    distribution and changes which spikes fall in cold hours.
    """
    prices = np.load(f"data/ticks_{sym}_sep.npy")
    df = pd.read_parquet(f"data/candles_{sym}_1min_sep.parquet")
    df["time"] = pd.to_datetime(df["time"])
    epoch0 = df["time"].astype("int64").to_numpy() // 10**9
    n_bars, n_ticks = len(epoch0), len(prices)
    bar_of_tick = np.minimum((np.arange(n_ticks) * n_bars) // n_ticks, n_bars - 1)
    counts = np.bincount(bar_of_tick, minlength=n_bars)
    starts = np.concatenate(([0], np.cumsum(counts)))
    rank = np.arange(n_ticks) - starts[bar_of_tick]
    cnt = counts[bar_of_tick]
    ts = epoch0[bar_of_tick] + rank * (60.0 / np.maximum(cnt, 1))
    return ts, prices


def run_replay(args, engines, book):
    """Replay saved tick files through the identical engine (no MT5).

    Ticks from all symbols are interleaved by REAL timestamps, so the shared
    book sees every symbol concurrently at full size — the same regime as live
    paper trading. (Sequential per-symbol replay starves whichever symbols
    happen to run after a drawdown.)
    """
    streams = []
    for engine in engines:
        if not Path(f"data/ticks_{engine.symbol}_sep.npy").exists():
            print(f"  {engine.symbol}: tick file not found, skipping")
            continue
        ts, prices = load_ticks(engine.symbol)
        print(f"  {engine.symbol}: {len(prices):,} ticks")
        streams.append((engine, ts, prices))
    if not streams:
        print("  no tick data found")
        return

    if len(streams) == 1:
        events = zip(streams[0][1], itertools.repeat(0), streams[0][2])
    else:
        events = heapq.merge(
            *[zip(ts, itertools.repeat(k), prices)
              for k, (_, ts, prices) in enumerate(streams)],
            key=lambda ev: ev[0])

    total = 0
    for i, (t, k, p) in enumerate(events):
        streams[k][0].on_tick(t, float(p))
        total += 1
        if i % 500_000 == 0 and i > 0:
            print(f"    ... {i:,} ticks | equity {book.equity:.2f}")
    print(f"  Replay done: {total:,} ticks")


def main():
    ap = argparse.ArgumentParser(description="SpikeCatcher v3 paper trading executor (Phase 2)")
    ap.add_argument("--symbols", default="BOOM1000,CRASH1000")
    ap.add_argument("--equity", type=float, default=200.0)
    ap.add_argument("--risk", type=float, default=RISK_PER_TRADE)
    ap.add_argument("--poll-seconds", type=float, default=0.5, help="live tick poll interval")
    ap.add_argument("--max-ticks", type=int, default=0, help="stop after this many total ticks (0 = run until Ctrl+C; useful for connectivity dry-runs)")
    ap.add_argument("--max-minutes", type=float, default=0, help="live: stop after this many minutes (0 = run until Ctrl+C; soak tests)")
    ap.add_argument("--force-cold", action="store_true", help="SOAK VALIDATION ONLY: treat every hour as a cold hour so entries can fire outside the locked window (ignored in replay)")
    ap.add_argument("--live-warmup", action="store_true", help="live: seed median-range warmup from local candle files (replay keeps cold start)")
    ap.add_argument("--replay", action="store_true", help="replay data/ticks_{sym}_sep.npy instead of live MT5")
    ap.add_argument("--replay-days", type=float, default=7.0, help="(unused, kept for compat) replay uses real Sep timestamps")
    ap.add_argument("--safety-tick-exit", action="store_true", help="enable EA v2.15 tick-level safety exit (default off: bar-close spike_against only, see docs/REPLAY_VS_BACKTEST_GAP.md)")
    ap.add_argument("--halts", action="store_true", help="enable halt rails (default OFF = measurement mode; see docs/REPLAY_VS_BACKTEST_GAP.md DECISION)")
    ap.add_argument("--no-halts", action="store_true", help="(deprecated, halts are already off by default; kept for command compat)")
    ap.add_argument("--daily-loss", type=float, default=MAX_DAILY_LOSS, help="daily loss fraction that halts entries for the day when --halts is on (default 0.50 = sweep-least-harmful)")
    ap.add_argument("--consec-losses", type=int, default=MAX_CONSEC_LOSSES, help="consecutive losses that halt entries for the day when --halts is on (default 10)")
    ap.add_argument("--compounding", action="store_true", help="size each trade on CURRENT equity (default is FIXED sizing on starting equity — see docs/REPLAY_VS_BACKTEST_GAP.md)")
    args = ap.parse_args()

    syms = [s.strip().lower() for s in args.symbols.split(",") if s.strip()]
    book = EquityBook(args.equity, risk_per_trade=args.risk, halts_enabled=args.halts and not args.no_halts,
                      daily_loss_pct=args.daily_loss, consec_losses=args.consec_losses)
    if args.force_cold and args.replay:
        print("[WARN] --force-cold is ignored in replay (Phase 1 parity is protected)")
    engines = [SymbolEngine(s, book, safety_tick_exit=args.safety_tick_exit,
                            fixed_sizing=not args.compounding,
                            live_warmup=(not args.replay and args.live_warmup),
                            force_cold=args.force_cold and not args.replay) for s in syms]

    print("=" * 66)
    print(f"  SPIKECATCHER v3 PAPER EXECUTOR ({'REPLAY' if args.replay else 'LIVE'})")
    print(f"  Symbols: {', '.join(syms)} | Equity: ${args.equity:.2f} | Risk: {args.risk*100:.0f}%")
    print(f"  Cold hours: {COLD_HOURS} | Velocity: {VELOCITY_THRESHOLD} | Timeout: {TICK_EXIT} bars")
    print(f"  Halts: {'ON' if args.halts and not args.no_halts else 'OFF (measurement mode)'}"
          f" (daily {args.daily_loss*100:.0f}%, {args.consec_losses} consec)"
          f" | Tick safety exit: {'ON' if args.safety_tick_exit else 'OFF'}"
          f" | Sizing: {'compounding' if args.compounding else 'fixed'}")
    if args.force_cold and not args.replay:
        print("  [!] SOAK MODE: --force-cold active (cold-hours filter bypassed) - NOT measurement data")
    print("=" * 66)

    init_tracker()

    if args.replay:
        run_replay(args, engines, book)
    else:
        run_live(args, engines, book)

    print("\n" + "=" * 66)
    print(f"  SESSION SUMMARY | equity: ${book.equity:.2f} "
          f"({(book.equity/args.equity-1)*100:+.2f}%)")
    for e in engines:
        print(f"  {e.symbol:10s} ticks: {e.ticks_seen:6d} | spikes seen: {e.spikes_seen:4d} | "
              f"safety exits: {e.safety_exits:4d} | "
              f"entries skipped (hot hour): {e.entries_skipped_cold} | "
              f"blocked by halt: {e.entries_blocked_halt:4d}")
    print(f"  Halt events fired: {book.halts_today}")
    print("=" * 66)
    print_report()


if __name__ == "__main__":
    main()
