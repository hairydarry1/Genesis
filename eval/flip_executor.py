#!/usr/bin/env python3
"""Flip1000 phase-follower demo executor + slippage measurement.

Strategy (docs/FLIP_INDEX_MECHANICS.md section 5, recommended config):
  - phase = direction of the last large move (|delta| > 5.0 x rolling median
    of the last 5000 tick deltas)
  - enter only after the phase prints MIN_AGE+1 consecutive same-direction
    large moves (the market "voted" twice)
  - exit on counter-spike; re-entry only when the new phase reaches the gate
  - fixed 0.1 lot (the study's "1 step")

Purpose: measure REAL fills vs the backtest's assumed fill ("first quote
after the spike tick"). Every order writes a row to the slippage ledger:
  assumed = the live quote we acted on (the backtest's fill model)
  actual  = the deal price MT5 reports back
  adverse slippage = (actual - assumed) priced so positive = worse than model

This number (section "Execution fragility": edge = $0.235/trade) is the one
variable the backtest cannot measure. Run on DEMO for >=1 week, then the
sensitivity table converts from hypothesis to measurement.

Usage:
  python eval/flip_executor.py --probe-order        # 1 open+close, exit (order-path validation)
  python eval/flip_executor.py --max-minutes 60     # bounded session
  python eval/flip_executor.py                      # until Ctrl+C
"""
import argparse
import ctypes
import csv
import json
import os
import sys
import time
from collections import deque
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SYMBOL = "Crash Boom Flip 1000 Index"
KEY = "flip1000"
MAGIC = 620001
MULT = 5.0
# Absolute spike floor (2026-10-05, raw-tick audit): the relative test alone
# sits inside the tick-noise body. The 2026-10-05 00:40:31 arm fired on a
# 0.046u tick (x=5.24) while that session's real spikes were 12.7/13.7u
# (x=1465/1569) - a ~300x gap the 5x-median gate cannot see, because the
# median (~0.0088u) is stable while the signal is not. 5d distribution over
# 539k ticks: noise ceiling ~0.046u, [0.05,0.10) empty, 558 real spikes >=0.5u.
# 0.15u keeps 579/581 real spikes and blocks the noise class. Same family as
# SL_BACKSTOP_UNITS / EQUITY_FLOOR_USD / GAP_MS: an absolute guard layered on
# an adaptive one. Shadow mode logs would-blocks WITHOUT suppressing them.
SPIKE_FLOOR_UNITS = 0.15   # |delta| must clear this to count as a spike
SPIKE_FLOOR_SHADOW = False  # False = block sub-floor arms live; calibrated 2026-10-05 (0.15u keeps 579/581 real spikes), shadow run validated on demo then flipped live here on the $10 account
MIN_AGE = 1                # enter after MIN_AGE+1 consecutive same-direction spikes
WINDOW = 5000              # rolling median window (matches backtest warmup)
POLL_SECONDS = 0.5
HB_SECONDS = 300
LOTS = 0.1                 # the study's "1 step"; fixed for the whole slippage phase
VPU = 0.10                 # USD per 1.0 price unit at 0.1 lot (verified from specs)
# OUT_DIR is overridable so a DEMO session can run in its own directory: without
# this, a demo run appended demo trades into the LIVE trades.csv /
# slippage_ledger.csv / day_state.json and silently corrupted the book. Live and
# the keepalive launcher set nothing, so the default path is unchanged.
OUT_DIR = os.environ.get("FLIP_OUT_DIR", "data/flip_exec")
SLIP_CSV = f"{OUT_DIR}/slippage_ledger.csv"
TRADE_CSV = f"{OUT_DIR}/trades.csv"
DAILY_STOP_RAW_USD = 9.0   # study: halt new entries at RAW day PnL <= -$9 (UTC day).
                           # Halt-scaling fix (FLIP_PLAN 'MANDATORY before funding'):
                           # the threshold is defined at the 0.1-lot reference; at
                           # larger lots it must scale or a 0.3-lot rung halts on a
                           # -$3 raw dip (Sep 9 'halts are poison' lesson).


def daily_stop_usd():
    """Active halt threshold in SCALED day-PnL dollars for the current lot size."""
    # round() kills the float artifact (9.0 * 0.3/0.1 = 26.999...96);
    # lots are 0.1-steps so 6dp is exact.
    return DAILY_STOP_RAW_USD * round(LOTS / 0.1, 6)
# 2026-10-06: floor lowered 4.00 -> 1.50. The $4.00 value was calibrated as
# "outside every simulated recovery path FROM $10"; after the Oct 5 -$4.00
# balance op the live book is only $3.70, so an honest $4.00 floor sits ABOVE
# the account and halts everything the instant it is anchored. 1.50 is the
# highest round value that (a) still leaves room to keep trading and (b) still
# fires ABOVE the broker stop-out (~$0.80 equity: margin $1.60 at level 50%),
# so the FLOOR - not a margin call - is what ends the run.
# DEVIATION: this weakens a pre-registered guard mid-season, on a book that is
# already 63% impaired. The Oct 8 gate must score against the original $4.00
# rule as well as this one; do not quietly backfill it.
EQUITY_FLOOR_USD = 1.5
# ---- 2026-10-06 LIVE BOOK ANCHOR (floor-honesty fix) ---------------------
# ledger_equity() = $10 + realized TRADES, which is structurally blind to
# DEAL_TYPE_BALANCE movements. On Oct 5 20:58 a -$4.00 balance op (deal
# 20709033588, comment R-d2932223-5662-49cc-bbaa-743c5) took the real account
# to $3.70 while the book still read $7.63 - the floor sat $3.93 ABOVE
# reality and could not have fired before a wipeout. A live run now anchors
# the book to account_info().balance at startup (the broker's own number
# already includes every past trade AND every balance op) and reconciles
# against it on the heartbeat, so a mid-session withdrawal/fee is booked
# instead of silently inflating the floor. Demo keeps the $10 model book.
BOOK_BASE_FALLBACK_USD = 10.0    # demo model-book baseline (live uses the real balance)
BALANCE_OP_TOLERANCE_USD = 0.02  # drift below this is float noise, not an op
RECONCILE_SECONDS = 60           # live book<->balance reconciliation cadence
BALANCE_OPS_CSV = f"{OUT_DIR}/balance_ops.csv"   # non-trade movements, for audit
# Telemetry (2026-09-15): the flagged pre-registered observations from the
# studies (Appendix O session pocket, Appendix S 2h-momentum split, ACTREG
# activity regime) are logged at every order so the Oct 8 gate can score
# them for free from the ledger - no code change to strategy logic.
# min_2k (2026-09-16): minors (5-8x median) in the trailing 2000 tick deltas
# - the QUIET-ENTRY study variable (Appendix Y). Telemetry only: the executor
# does NOT suppress entries on it; scoring happens at the Oct 8 gate.
ACT_WINDOW_MS = 2 * 3600_000   # trailing spike-activity window (matches ACTREG)
BAR_MS = 300_000               # M5 bar size (matches flip_structural)
MINOR_MAX_X = 8.0              # minor = 5x < |delta| <= 8x (matches study)
MINOR_WINDOW_TICKS = 2000      # trailing window for min_2k (matches study)

SL_BACKSTOP_UNITS = 60.0   # catastrophic server-side SL distance. Measured
                           # on the broker: stops_level = 1.878u minimum, and
                           # 60u was accepted (10009) while 1.90u was rejected
                           # (10016). 60u is beyond the worst historical phase
                           # excursion (41.9u) - ~$6 risk at 0.1 lot, caps the
                           # unmanaged worst case (sleep/wifi/crash) at roughly
                           # daily-stop scale. NOT the strategy exit.
DAY_STATE = f"{OUT_DIR}/day_state.json"
# Menu C (2026-09-17 reseed, Appendices AF/AI): entries are PENDING for
# DELAY_TICKS ticks after the qualifying spike, canceled by an opposite
# spike, filled at market otherwise. Mirrors run_conv(delay=300) exactly
# (tick-counted, not wall-clock). Live-safety deltas vs run_conv, documented:
# arm/fill are suppressed while stale or day-halted, and a blocked pending
# expires after PENDING_MAX_OVER extra ticks instead of filling at any price.
DELAY_TICKS = 300
PENDING_MAX_OVER = 600
ENGINE_STATE = f"{OUT_DIR}/engine_state.json"   # restart-neutral phase state
UPTIME_JSON = f"{OUT_DIR}/uptime.json"          # AI-audit availability stats
MAN_SYNC_SECONDS = 15      # heartbeat position-sync: detects user manual MT5
                           # closes within ~15s (2026-09-16: manual closes were
                           # silently dropped - no EXIT row, PnL unbooked, book
                           # diverged). Now detected and booked as MANEXIT.
STALE_ENTRY_SECONDS = 120  # ticks older than this are a reconnect flush:
                           # process for exits/median but never open entries
TICK_STALL_SECONDS = 300   # no tick this fresh with an empty batch = deaf
GAP_MS = 300000            # consecutive-tick jump >5min = data hole (sleep,
                           # wifi). Same rule as the backtest gap-mask: close
                           # across it, never signal across it.
                           # session (post-sleep MT5 wedge, proven 2026-09-12:
                           # PC slept ~18h, heartbeats kept printing while the
                           # tick feed froze) -> rebuild the session


def utcnow():
    return datetime.now(timezone.utc)


def ts_ms(dt):
    return int(dt.timestamp() * 1000)


def acquire_lock():
    """Refuse a second instance (double-execution would double orders).

    Hardened 2026-09-10 after a real double-instance incident (nohup child
    outlived its shell, pidfile got deleted, second launch sailed through):
    - creation is atomic (open 'x' fails if the file exists)
    - an existing pidfile is verified: if that pid is alive -> REFUSE; only a
      provably dead pid's file is removed and re-created
    - os.getpid() on Windows IS the Windows pid, so the liveness check is
      exact. Note MSYS/nohup $! values are NOT Windows pids - never compare
      them against this file or kill from them (use taskkill //PID instead).
    """
    pidfile = f"{OUT_DIR}/executor.pid"
    os.makedirs(OUT_DIR, exist_ok=True)
    k32 = ctypes.windll.kernel32
    for _ in range(3):
        try:
            with open(pidfile, "x") as f:
                f.write(str(os.getpid()))
            return True, os.getpid()
        except FileExistsError:
            pass
        try:
            pid = int(open(pidfile).read().strip())
        except (OSError, ValueError):
            pid = 0
        if pid:
            h = k32.OpenProcess(0x1000, False, pid)
            if h:
                k32.CloseHandle(h)
                return False, pid          # genuinely alive -> refuse
        try:
            os.remove(pidfile)             # stale: dead pid / corrupt file
        except OSError:
            pass
    return False, pid


def release_lock():
    try:
        os.remove(f"{OUT_DIR}/executor.pid")
    except OSError:
        pass


def rolling_median_window():
    """Seed the rolling window with the most recent stored tick deltas so
    spike detection is calibrated from tick 1 (no 40-min cold start).
    Also seeds the minor-spike history (min_2k telemetry) so the quiet/burst
    classifier is valid immediately instead of warming up ~33 minutes."""
    from data.flip_tick_store import load
    try:
        _, px = load(KEY)
        d = np.abs(np.diff(px[-(WINDOW + 1):]))
        print(f"[WARMUP] seeded median window with {len(d)} stored deltas "
              f"(median |delta| = {np.median(d):.5f})")
        return (deque(d.tolist(), maxlen=WINDOW),
                deque(d[-MINOR_WINDOW_TICKS:].tolist(),
                      maxlen=MINOR_WINDOW_TICKS))
    except FileNotFoundError:
        print("[WARMUP] no stored ticks - cold start, calibration takes a while")
        return deque(maxlen=WINDOW), deque(maxlen=MINOR_WINDOW_TICKS)


# Funding-day arm: live trading is possible ONLY when the process is
# launched with FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY in its
# environment - a deliberate act on launch day, impossible to trip by
# accident, and NOT inherited by the 02:30 checkpoint auto-restart (a
# dead live executor alerts a human instead of silently reviving).
ALLOW_LIVE = os.environ.get("FLIP_ALLOW_LIVE") == "I_UNDERSTAND_THIS_IS_REAL_MONEY"


def assert_demo_account(mt5):
    """Hard account gate, called before every order AND once at startup.
    Default: orders only on a DEMO account. With FLIP_ALLOW_LIVE armed,
    a live account is accepted - but the launch must have carried the
    env var, so going live is always a conscious choice made in the
    shell that started this process."""
    acct = mt5.account_info()
    if acct is None:
        return False, "account_info() returned None"
    if acct.trade_mode == 0:
        return True, f"{acct.login} (demo)"
    if ALLOW_LIVE:
        return True, f"{acct.login} (LIVE - FLIP_ALLOW_LIVE armed at launch)"
    return False, (f"account {acct.login} is NOT demo (trade_mode="
                   f"{acct.trade_mode}) - orders disabled. To trade live "
                   "deliberately: set FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_"
                   "REAL_MONEY when launching.")


def make_filling(mt5, info):
    m = getattr(info, "filling_mode", 0)
    if m & 2:
        return mt5.ORDER_FILLING_IOC
    if m & 1:
        return mt5.ORDER_FILLING_FOK
    return mt5.ORDER_FILLING_RETURN


def reconnect(mt5):
    """Rebuild the MT5 session after an order_send transport failure
    (order_send returned None). Ticks can keep streaming over a stale
    data session while the trade channel is dead (seen after terminal
    account switches); a fresh initialize rebinds both.

    NOTE: retcode 10027 is NOT a link failure - it means the terminal's
    AutoTrading button is OFF. That needs a human click, not a reconnect."""
    print("[RECONNECT] transport failure - rebuilding MT5 session")
    mt5.shutdown()
    time.sleep(1)
    ok = mt5.initialize()
    if ok:
        info = mt5.symbol_info(SYMBOL)
        if info is not None and not info.visible:
            mt5.symbol_select(SYMBOL, True)
        a = mt5.account_info()
        print(f"[RECONNECT] session rebuilt: acct {a.login if a else '?'}")
    else:
        print(f"[RECONNECT] initialize failed: {mt5.last_error()}")
    return ok


def market_order(mt5, side, comment, filling):
    """Send a market order. Returns (ok, assumed_price, deal_price, retcode)."""
    ok_acct, why = assert_demo_account(mt5)
    if not ok_acct:
        print(f"[ACCOUNT GUARD] REFUSED entry: {why}")
        return False, None, None, f"account-guard: {why}"
    info = mt5.symbol_info(SYMBOL)
    tick = mt5.symbol_info_tick(SYMBOL)
    if tick is None:
        return False, None, None, "no tick"
    assumed = tick.ask if side == "buy" else tick.bid
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": LOTS,
        "type": mt5.ORDER_TYPE_BUY if side == "buy" else mt5.ORDER_TYPE_SELL,
        "price": assumed,
        "deviation": 500,          # spikes move fast; 500 pts = one spread
        "magic": MAGIC,
        "comment": comment,
        "type_filling": filling,
        # catastrophic backstop, NOT the strategy exit: far beyond any normal
        # phase excursion, so the tick-precision counter-spike exit always
        # fires first in normal operation. Only matters when the machine is
        # already gone (sleep / wifi / crash) - caps that worst case.
        "sl": round(assumed - SL_BACKSTOP_UNITS if side == "buy"
                    else assumed + SL_BACKSTOP_UNITS, 5),
    }
    r = mt5.order_send(req)
    if r is None:
        reconnect(mt5)   # transport-level failure: rebuild for next attempt
        return False, assumed, None, f"order_send None ({mt5.last_error()})"
    if r.retcode != mt5.TRADE_RETCODE_DONE:
        if r.retcode == 10027:   # TRADE_RETCODE_CLIENT_DISABLES_AT
            ti = mt5.terminal_info()
            if ti is not None and not ti.trade_allowed:
                print("[AUTO TRADING OFF] terminal refuses ALL orders - click the "
                      "green 'AutoTrading' button in the MT5 toolbar. Open-position "
                      "SL backstop is unaffected (it lives on the broker).")
            else:
                reconnect(mt5)   # retcode says disabled but terminal allows: stale session
        return False, assumed, getattr(r, "price", None), f"retcode {r.retcode}"
    return True, assumed, r.price, "done"


def close_position(mt5, pos, comment, filling):
    ok_acct, why = assert_demo_account(mt5)
    if not ok_acct:
        print(f"[ACCOUNT GUARD] REFUSED close: {why}")
        return False, None, None, f"account-guard: {why}"
    side = "sell" if pos.type == mt5.POSITION_TYPE_BUY else "buy"
    tick = mt5.symbol_info_tick(SYMBOL)
    assumed = tick.bid if side == "sell" else tick.ask
    req = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": pos.volume,
        "type": mt5.ORDER_TYPE_SELL if side == "sell" else mt5.ORDER_TYPE_BUY,
        "position": pos.ticket,
        "price": assumed,
        "deviation": 500,
        "magic": MAGIC,
        "comment": comment,
        "type_filling": filling,
    }
    r = mt5.order_send(req)
    if r is None:
        reconnect(mt5)   # transport-level failure: rebuild for next attempt
        return False, assumed, None, f"order_send None ({mt5.last_error()})"
    if r.retcode != mt5.TRADE_RETCODE_DONE:
        if r.retcode == 10027:   # TRADE_RETCODE_CLIENT_DISABLES_AT
            ti = mt5.terminal_info()
            if ti is not None and not ti.trade_allowed:
                print("[AUTO TRADING OFF] terminal refuses ALL orders - click the "
                      "green 'AutoTrading' button in the MT5 toolbar. Open-position "
                      "SL backstop is unaffected (it lives on the broker).")
            else:
                reconnect(mt5)   # retcode says disabled but terminal allows: stale session
        return False, assumed, getattr(r, "price", None), f"retcode {r.retcode}"
    return True, assumed, r.price, "done"


def log_slip(mode, spike_ts, spike_delta, side, assumed, actual, note="",
             spread=None, tel=None):
    """Slippage ledger: adverse = (actual-assumed) signed so positive = worse.

    tel (optional) = (entry_x, act_2h, trend_2h, min_2k): telemetry context
    stamped on the row (entries carry it measured at the entry spike; closes
    carry the entry's values) so the Oct 8 gate can score the flagged
    observations (session pocket, 2h-momentum, activity regime, quiet/burst)
    without any strategy change. min_2k added 2026-09-16; rows written before
    that simply have an empty 4th telemetry column.
    """
    if assumed is None or not actual:
        # refused or FAILED order (account guard, no tick, retcode failure -
        # r.price can come back 0.0): no fill happened, so there is no
        # slippage to measure - skip the row entirely. A 0-price once
        # poisoned the stats with -9435u phantom rows (Sep 11).
        return None
    adverse = (actual - assumed) if side == "buy" else (assumed - actual)
    new = not os.path.exists(SLIP_CSV)
    with open(SLIP_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["mode", "spike_ts_utc", "spike_delta", "side",
                        "assumed_fill", "actual_deal", "adverse_price_units",
                        "adverse_usd", "spread_at_order", "note",
                        "entry_x", "act_2h", "trend_2h", "min_2k"])
        t4 = (list(tel) + [""] * 4)[:4] if tel else ["", "", "", ""]
        x, act, t24, mn = t4
        w.writerow([mode, spike_ts, f"{spike_delta:+.5f}", side,
                    f"{assumed:.5f}", f"{actual:.5f}",
                    f"{adverse:+.5f}",
                    f"{adverse * VPU:+.4f}",
                    f"{spread:.5f}" if spread else "", note,
                    f"{x:.2f}" if x != "" else "",
                    act, t24, mn])
    return adverse


def log_trade(mode, **kw):
    new = not os.path.exists(TRADE_CSV)
    cols = ["event", "ts_utc", "side", "price", "lots", "note"]
    with open(TRADE_CSV, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(cols)
        row = [kw.get(c, "") for c in cols]
        row[0] = mode                      # event column IS the mode arg
        w.writerow(row)


def ledger_equity(base=BOOK_BASE_FALLBACK_USD):
    """Recover the mission book's cumulative equity from the trades ledger.

    Pairing identical to the scorers: adjacent ENTRY->EXIT/MANEXIT rows with
    real fills, PnL = side * (exit - entry) * VPU at 0.1 lot. The book is
    cumulative across sessions; a session-local reset once understated a
    broker-side SL loss (Sep 16 incident).

    2026-10-06: `base` is the assumed starting balance, so a live run can pass
    the broker's real balance instead of a hardcoded $10 (see
    BOOK_BASE_FALLBACK_USD). This function only ever sees TRADES, so it can
    never account for a DEAL_TYPE_BALANCE op by itself - that is what the
    anchor + reconcile path is for.
    Returns (equity, n_trips)."""
    try:
        with open(SLIP_CSV) as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return base, 0
    sig = [r for r in rows
           if not r["mode"].startswith("PROBE") and r.get("actual_deal")]
    tot, n = 0.0, 0
    i = 0
    while i < len(sig) - 1:
        e, x = sig[i], sig[i + 1]
        if e["mode"] == "ENTRY" and x["mode"] in ("EXIT", "MANEXIT"):
            d = 1 if e["side"] == "buy" else -1
            tot += d * (float(x["actual_deal"]) - float(e["actual_deal"])) * VPU
            n += 1
            i += 2
        else:
            i += 1
    return base + tot, n


def live_close_book(mt5, is_live, model_eq, pnl):
    """Apply a realized close PnL onto the model book.

    2026-10-07 incident. reconcile_book() (60s) reads account_info().balance
    INSTANTLY, while history_deals_get lags a few seconds after an SL/TP
    close. So on a backstop SL firing, the balance drop is absorbed as a
    balance op BEFORE the close is booked. Adding pnl on top then books the
    same loss twice (book $4.11 -> -$1.89) and falsely trips the $1.50 equity
    floor, latching the run halted for the rest of the day.

    In LIVE the broker balance is authoritative and already contains this PnL,
    so re-anchor to it - idempotent and independent of which of the two
    detectors wins the race. day_pnl still accrues the trade exactly once, at
    the call site. Demo keeps the pure price-derived book.
    """
    if is_live:
        try:
            return float(mt5.account_info().balance)
        except Exception:                                    # noqa: BLE001
            pass
    return model_eq + pnl


def book_balance_op(amount, when, note, dedupe=True):
    """Record a non-trade balance movement so the floor stays honest.

    A DEAL_TYPE_BALANCE deal (deposit, withdrawal, transfer, charge) changes
    the broker balance with no ENTRY/EXIT row, so the ledger-derived book
    cannot see it (2026-10-05 -$4.00 incident). The startup anchor catches ops
    that landed while the executor was down; the heartbeat reconcile catches
    the ones that land while it is up. Kept in its own file so the two scored
    books (slippage_ledger.csv, trades.csv) stay free of non-trade rows.
    """
    new = not os.path.exists(BALANCE_OPS_CSV)
    if dedupe and not new:
        # A history op is re-listed on EVERY launch (the 14d history scan), so
        # without this the file would grow a duplicate row per restart.
        try:
            with open(BALANCE_OPS_CSV, newline="") as f:
                if any(r.get("note") == note for r in csv.DictReader(f)):
                    return False
        except (OSError, ValueError):
            pass
    try:
        with open(BALANCE_OPS_CSV, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["ts_utc", "amount_usd", "note"])
            w.writerow([when, f"{amount:+.4f}", note])
    except OSError:
        return False
    return True


def report_balance_ops(mt5, days=14):
    """Print recent DEAL_TYPE_BALANCE deals so book-vs-balance drift is
    explained at launch instead of staying silent."""
    try:
        frm = utcnow() - timedelta(days=days)
        deals = mt5.history_deals_get(frm, utcnow() + timedelta(days=1)) or []
    except Exception as e:                                   # noqa: BLE001
        print(f"    [BALANCE-OPS] history unavailable: {e}")
        return
    ops = [d for d in deals
           if getattr(d, "type", None) == getattr(mt5, "DEAL_TYPE_BALANCE", 2)]
    if not ops:
        print(f"    [BALANCE-OPS] none in the last {days}d - book matches balance")
        return
    total, n_new = 0.0, 0
    for d in ops:
        try:
            when = datetime.fromtimestamp(d.time, timezone.utc).isoformat()
        except (OSError, OverflowError, ValueError):
            when = str(d.time)
        total += d.profit
        note = f"history deal {d.ticket} {d.comment or ''}".strip()
        if book_balance_op(d.profit, when, note):
            n_new += 1
            print(f"    [BALANCE-OPS] {when} deal {d.ticket} type={d.type} "
                  f"profit ${d.profit:+.2f} ({d.comment or 'no comment'})")
    print(f"    [BALANCE-OPS] {len(ops)} non-trade op(s) in the last {days}d "
          f"totalling ${total:+.2f} ({n_new} new). These move the REAL balance "
          f"without any trade row - the anchor already includes them; the "
          f"ledger trade-book does not, which is why the two disagree.")


def load_day_state():
    """Restore today's realized PnL AND halt flag across restarts so a relaunch
    mid-day can neither reset the daily stop nor un-halt a halted day."""
    try:
        with open(DAY_STATE) as f:
            st = json.load(f)
        if st.get("date") == utcnow().strftime("%Y-%m-%d"):
            return st["date"], float(st.get("day_pnl", 0.0)), \
                bool(st.get("day_halted", False)), bool(st.get("floored", False))
    except (OSError, ValueError, KeyError):
        pass
    return utcnow().strftime("%Y-%m-%d"), 0.0, False, False


def save_day_state(day, pnl, halted=False, floored=False):
    try:
        with open(DAY_STATE, "w") as f:
            json.dump({"date": day, "day_pnl": pnl,
                       "day_halted": halted, "floored": floored}, f)
    except OSError:
        pass


def telemetry_fields(spike_delta, med, spike_ms, spike_hist, bar_closes,
                     minor_hist):
    """Pre-entry context for the Oct 8 gate, computed exactly like the studies.

    entry_x   : entry-spike size in x-median units (MAG/THICK convention)
    act_2h    : spikes in the trailing 2h (ACTREG convention, incl. this one)
    trend_2h  : sign of M5 close(n) - close(n-24) (flip_structural t24)
    min_2k    : minors (5-8x median) in the trailing 2000 tick deltas incl.
                this one (QUIET-ENTRY study convention; the just-printed
                entry spike is part of its own trailing window there too).
                TELEMETRY ONLY - the executor never suppresses entries on it.
    All inputs are strictly historical state maintained by the main loop.
    """
    x = abs(spike_delta) / med if med else 0.0
    cutoff = spike_ms - ACT_WINDOW_MS
    act = sum(1 for t in spike_hist if t >= cutoff)
    b = spike_ms // BAR_MS
    t24 = 0
    if (b - 25) in bar_closes and (b - 1) in bar_closes:
        t24 = int(np.sign(bar_closes[b - 1] - bar_closes[b - 25]))
    mn = (sum(1 for d in minor_hist if MULT * med < d <= MINOR_MAX_X * med)
          if med else 0)
    return x, act, t24, mn


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe-order", action="store_true",
                    help="open+close one 0.1-lot position, log fills, exit")
    ap.add_argument("--probe-manual-close", action="store_true",
                    help="open a position, clear pos_model (simulate the user "
                         "closing it in MT5), then run one heartbeat sync to "
                         "prove the manual-close detection path; exits")
    ap.add_argument("--max-minutes", type=float, default=0,
                    help="bounded run (0 = until Ctrl+C)")
    ap.add_argument("--poll-seconds", type=float, default=POLL_SECONDS)
    args = ap.parse_args()

    ok, pid = acquire_lock()
    if not ok:
        print(f"REFUSED: another executor instance runs as PID {pid}")
        sys.exit(2)
    try:
        run(args)
    finally:
        release_lock()


def run(args):
    import MetaTrader5 as mt5
    if not mt5.initialize():
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    # startup gate: never run (not even adopt/exits) against a live account.
    # Keeps the 02:30 auto-restart safe if the user left the terminal on live.
    ok_acct, why = assert_demo_account(mt5)
    if not ok_acct:
        print(f"[ACCOUNT GUARD] refusing to start: {why}")
        print("  (log the terminal back into the DEMO account, then restart)")
        sys.exit(3)
    if ALLOW_LIVE:
        print("*** LIVE MODE ARMED - REAL MONEY AT RISK - fixed 0.1 lot, "
              "never step up ***")
    start_login = mt5.account_info().login   # detect account switches mid-session
    info = mt5.symbol_info(SYMBOL)
    if info is None:
        print(f"symbol not found: {SYMBOL}")
        sys.exit(1)
    if not info.visible:
        mt5.symbol_select(SYMBOL, True)
    acct = mt5.account_info()
    filling = make_filling(mt5, info)
    print(f"=== Flip1000 executor | {acct.server} acct {acct.login} | "
          f"{SYMBOL} | {MULT}x median, min_age={MIN_AGE}, {LOTS} lot | "
          f"filling={filling} ===")
    print("    DEMO orders are real for the demo account; the $10 model book "
          f"is tracked in {TRADE_CSV}")
    print(f"    daily stop: new entries halt at day PnL <= -${daily_stop_usd():.2f} "
          f"(raw -${DAILY_STOP_RAW_USD:.2f} at 0.1 lot, scales with size; "
          "UTC-day reset, realized PnL only)")
    print("    manual closes: if you close a position yourself in MT5, the "
          "heartbeat syncs within ~15s and books it (MANEXIT). Do NOT open "
          "positions by hand while the executor holds one (no hedging logic).")
    print(f"    spike floor: |delta| >= {SPIKE_FLOOR_UNITS}u required "
          f"(shadow={SPIKE_FLOOR_SHADOW}) - absolute guard under the "
          f"{MULT}x-median test")
    print("    telemetry: entry_x/act_2h/trend_2h/min_2k stamped on every "
          "ledger row for Oct 8 gate scoring (measurement only)")
    print(f"    config: Menu C - entries PENDING {DELAY_TICKS} ticks after the "
          "qualifying spike, canceled by an opposite spike (Appendix AF)")

    window, minor_hist = rolling_median_window()
    prev_bid = None
    prev_ms = None
    pos_model = None                # (side, entry_price[, ticket]) model book
    cur_dir, cnt = 0, 0             # consecutive spike direction/length
    # Menu-C pending-entry state (mirrors run_conv's ep_dir/ep_j): armed on a
    # qualifying spike at cur_dir, filled DELAY_TICKS ticks later, canceled
    # by an opposite spike. ep_tel carries the arming spike's telemetry.
    ep_dir, ep_j = 0, -10**9
    ep_tel = [0.0, None, None, None]
    ep_delta = 0.0                  # ARMING spike's raw delta (ledger stamp)
    n_cancel = n_fill = n_expire = n_floor_block = 0   # uptime instrumentation
    is_live = (acct.trade_mode == getattr(mt5, "ACCOUNT_TRADE_MODE_REAL", 2))
    ledger_eq, n0 = ledger_equity()
    if is_live:
        # Anchor to the broker's own balance: it already includes every past
        # trade AND every balance op, so the floor can never read high again.
        # The ledger trade-book is now only a cross-check - any gap between it
        # and the balance is by definition a balance op, not trading.
        model_eq = float(acct.balance)
        print(f"    [BOOK] LIVE anchor: book = real balance ${model_eq:.2f} "
              f"(ledger trade-book ${ledger_eq:.2f} over {n0} trips; gap "
              f"${ledger_eq - model_eq:+.2f} = balance op(s), not trades)")
        report_balance_ops(mt5)
    else:
        model_eq = ledger_eq
        if n0:
            print(f"    [BOOK] equity restored from ledger: ${model_eq:.2f} "
                  f"over {n0} trips (cumulative "
                  f"${BOOK_BASE_FALLBACK_USD:.2f} model book; floor checks "
                  "honest)")
        else:
            model_eq = BOOK_BASE_FALLBACK_USD
    day, day_pnl, day_halted, floored = load_day_state()
    if floored:
        day_halted = True
        print("    [EQUITY FLOOR] restored from state - STAYING halted; after "
              "manual review set \"floored\": false in data/flip_exec/day_state.json")
    if day_halted:
        print(f"    [HALT] restored from state: day PnL ${day_pnl:.2f} "
              "- entries blocked until next UTC day")

    # ---- restart-neutral phase state (Appendix AI bucket 3) ----
    # A restart used to reseed median/phase from archive; on wake it entered
    # on stale "phase age 7" state the backtests refuse. Now phase/pending
    # state persists and is restored ONLY if it survived the restart: the
    # newest archived tick must overlap the saved tick cursor, and the saved
    # median must sit within 3x of the archive-warmup median (regime drift
    # guard). Otherwise the session starts flat/pending-clear - the honest
    # cold start.
    _wmed = float(np.median(window)) if len(window) else 0.0
    _arch_last = 0
    try:
        from data.flip_tick_store import load as _store_load
        _ams, _ = _store_load(KEY)
        _arch_last = int(_ams[-1])
    except Exception:
        pass
    try:
        with open(ENGINE_STATE) as f:
            es = json.load(f)
        _age = (time.time() - float(es.get("saved_at", 0))) / 3600.0
        _fresh = _arch_last and es.get("last_ms", 0) > _arch_last - 5000
        _cal = (_wmed <= 0 or float(es.get("med", 0)) <= 3 * _wmed)
        if _fresh and _cal and _age < 24:
            cur_dir = int(es.get("cur_dir", 0))
            cnt = int(es.get("cnt", 0))
            # A restored PENDING is dropped on purpose: filling it would act
            # on stale context. It re-arms naturally on the next qualifying
            # spike (identical to any fresh phase entry).
            print(f"    [STATE] restored phase={int(es.get('cur_dir', 0)):+d}x"
                  f"{int(es.get('cnt', 0))} (saved {_age:.1f}h ago, "
                  f"freshness+calibration OK; pending dropped, re-arms on "
                  "next qualifying spike)")
        else:
            print(f"    [STATE] save stale or diverged (age {_age:.1f}h, "
                  f"fresh={bool(_fresh)}, cal={bool(_cal)}) - flat start")
    except (OSError, ValueError, KeyError):
        print("    [STATE] no prior state - flat start")
    ticks_seen = spikes = 0
    started = time.time()
    last_hb = 0.0
    last_stall_reconnect = 0.0   # cooldown for the deafness watchdog
    slip_summary = []
    backlog_note = False          # True while stale (reconnect-flush) ticks stream
    # cursor: process only ticks NEWER than the terminal's latest at startup
    last_ms = int(mt5.symbol_info_tick(SYMBOL).time_msc)

    # adopt any position our magic already holds (restart sync - never
    # enter fresh while an orphan exists)
    orphans = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]
    if orphans:
        newest = max(orphans, key=lambda p: p.time)
        pos_model = (1 if newest.type == mt5.POSITION_TYPE_BUY else -1,
                     newest.price_open, newest.ticket)
        print(f"[ADOPT] synced book to existing position: "
              f"{'long' if pos_model[0] == 1 else 'short'} @ {pos_model[1]:.5f} "
              f"(ticket {newest.ticket}; {len(orphans)} own position(s) found)")
        if not newest.sl:
            # adopted positions may predate the backstop era - attach one so
            # an unmanaged orphan is still capped at catastrophic scale
            slp = (newest.price_open - SL_BACKSTOP_UNITS if pos_model[0] == 1
                   else newest.price_open + SL_BACKSTOP_UNITS)
            r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                                "position": newest.ticket, "symbol": SYMBOL,
                                "sl": round(slp, 5), "tp": newest.tp})
            oksl = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
            print(f"[ADOPT] backstop SL "
                  f"{'attached' if oksl else 'FAILED rc=' + str(getattr(r, 'retcode', None))}"
                  f" @ {slp:.5f}")

    if model_eq <= EQUITY_FLOOR_USD and not floored:
        floored = True
        day_halted = True
        save_day_state(day, day_pnl, day_halted, floored)
        print(f"    [EQUITY FLOOR] book equity ${model_eq:.2f} <= "
              f"${EQUITY_FLOOR_USD:.2f} - STAYING halted (standing rule 2: "
              'review required; set "floored": false in '
              "data/flip_exec/day_state.json to re-arm)")

    spike_hist = []                # telemetry: ms timestamps of spikes this session
    bar_closes = {}                # telemetry: M5 bar_id -> last bid of that bar
    # minor_hist is seeded by rolling_median_window(); refilled tick by tick
    pending_tel = [None, None, None, None]   # (x, act, t24, min_2k) at entry
    cur_tel = None                 # last qualifying spike's telemetry tuple
    ep_tel = [0.0, None, None, None]  # ARMING spike's telemetry (ledger stamp)
    hb_current = [0, 0]            # heartbeats with a current stream [ok, total]
    spread_sum = [0.0, 0]          # running [sum, n] of live ask-bid samples
    last_manual_sync = 0.0         # last heartbeat position-sync (manual-close detection)
    last_reconcile = 0.0           # last live book<->balance reconciliation

    def book_broker_close(pm, mode="MANEXIT", trade_note="manual"):
        """Book a position close that happened broker-side without us.

        Sep 17 fix: the old paths dropped pos_model silently when the
        position vanished (user close in MT5, or the 60u backstop SL firing
        while the process was suspended) - the Sep 16 18:10Z SL close
        (-$6.00) never reached the book. Truth source now is the account's
        deal history (last 48h), keyed by the position ticket when known,
        else by entry price. Writes the EXIT/MANEXIT ledger row (entry
        telemetry carried over) + CLOSE trade row, applies PnL to
        model_eq/day_pnl with daily-stop and floor checks. Returns True
        when booked; callers fall back / flag loudly when False.
        """
        side, entry_px = pm[0], pm[1]
        ticket = pm[2] if len(pm) > 2 else None
        nonlocal model_eq, day_pnl, day_halted, floored
        now = datetime.now(timezone.utc)
        try:
            deals = mt5.history_deals_get(now - timedelta(hours=48),
                                          now + timedelta(hours=1)) or []
        except Exception:
            deals = []
        outs = [d for d in deals
                if getattr(d, "symbol", "") == SYMBOL
                and getattr(d, "entry", -1) != mt5.DEAL_ENTRY_IN]
        if ticket is not None:
            matched = [d for d in outs
                       if getattr(d, "position_id", None) == ticket]
        else:
            ids = {getattr(d, "position_id", None) for d in deals
                   if getattr(d, "symbol", "") == SYMBOL
                   and getattr(d, "entry", -1) == mt5.DEAL_ENTRY_IN
                   and abs(getattr(d, "price", 0.0) - entry_px) < 5e-4}
            matched = [d for d in outs
                       if getattr(d, "position_id", None) in ids]
        if not matched:
            return False
        d = matched[-1]                  # latest close deal for the position
        px = float(d.price)
        ts = datetime.fromtimestamp(d.time, tz=timezone.utc).isoformat()
        reason = getattr(d, "reason", -1)
        tag = ("SL backstop" if reason == 4   # DEAL_REASON_SL = 4 (MT5 docs;
               else "manual/broker close")    #  package lacks the constant)
        adverse = log_slip(mode, ts, 0.0,
                           "sell" if side == 1 else "buy",
                           px, px,
                           note=f"{trade_note} [{tag} deal {d.ticket}]",
                           spread=(spread_sum[0] / spread_sum[1])
                           if spread_sum[1] else None,
                           tel=(tuple(pending_tel)
                                if pending_tel[0] is not None else None))
        slip_summary.append(adverse)
        pnl = side * (px - entry_px) * VPU
        model_eq = live_close_book(mt5, is_live, model_eq, pnl)
        day_pnl += pnl
        if not day_halted and day_pnl <= -daily_stop_usd():
            day_halted = True
            print(f"[DAILY STOP] day PnL ${day_pnl:.2f} "
                  f"<= -${daily_stop_usd():.2f}")
        if model_eq <= EQUITY_FLOOR_USD and not day_halted:
            day_halted = True
            floored = True
            print("[EQUITY FLOOR] manual review required")
        save_day_state(day, day_pnl, day_halted, floored)
        print(f"[BROKER-CLOSE] {'long' if side == 1 else 'short'} closed "
              f"broker-side @ {px:.5f} ({tag})  pnl ${pnl:+.2f}  "
              f"eq ${model_eq:.2f}")
        log_trade("CLOSE", ts_utc=ts, side=side, price=f"{px:.5f}",
                  lots=LOTS, note=f"{tag} eq {model_eq:.2f}")
        pending_tel[0] = None
        return True

    def sample_spread(mt5):
        t = mt5.symbol_info_tick(SYMBOL)
        if t is not None and t.bid > 0 and t.ask > 0:
            spread_sum[0] += t.ask - t.bid
            spread_sum[1] += 1

    def sync_open_position(now_ts, mode="MANEXIT", trade_note="manual"):
        """Manual-close reconciliation (2026-09-16).

        The executor previously synced pos_model to the broker only at
        adopt/entry/exit, so a position the USER closed in MT5 stayed in
        pos_model until the next counter-spike hit the silent
        'broker really lost it' path: no EXIT row, PnL never booked, book
        diverged from the account while heartbeats still showed the ghost.

        Now, every HEARTBEAT (not every tick):
          pos_model set, own-magic position gone  -> the user closed it.
              Book the PnL from pos_model at the current quote, write an
              EXIT row (mode MANEXIT, note manual) so every downstream
              consumer (side-split, telemetry scorer, digest) pairs it.
          pos_model set, position still there but SL was stripped ->
              re-attach the 60u backstop (same as adopt).
          pos_model None but an own-magic position exists -> user opened a
              position manually; adopt it into the book (tracked, capped).
        mode/trade_note: the probe passes PROBE-prefixed values so its rows
        land in the standard consumers' PROBE filter instead of the book.
        """
        # (docstring above describes the historical quote-booking; the
        # booking itself now routes through book_broker_close below, which
        # prefers the broker's own deal history - correct for user closes
        # AND for SL closes discovered late (Sep 16 incident).)
        nonlocal pos_model, model_eq, day_pnl, day_halted, floored
        mine = [p for p in (mt5.positions_get(symbol=SYMBOL) or [])
                if p.magic == MAGIC]
        if pos_model is not None and not mine:
            # Broker truth first: history_deals_get knows whether this was a
            # user close or the backstop SL, and at what price/time. Quote
            # booking below is only the fallback when no deal is found.
            if book_broker_close(pos_model, mode, trade_note):
                pos_model = None
                return
            side = pos_model[0]
            t = mt5.symbol_info_tick(SYMBOL)
            px_now = float(t.bid) if (t is not None and t.bid > 0) else 0.0
            if px_now > 0:
                assumed = px_now            # user closed at ~market: quote is
                actual = px_now             # the best available exit print
                adverse = log_slip(mode, utcnow().isoformat(), 0.0,
                                   "sell" if side == 1 else "buy",
                                   assumed, actual, note=trade_note,
                                   spread=(spread_sum[0] / spread_sum[1])
                                   if spread_sum[1] else None,
                                   tel=(tuple(pending_tel)
                                        if pending_tel[0] is not None else None))
                slip_summary.append(adverse)
                pnl = side * (actual - pos_model[1]) * VPU
                model_eq = live_close_book(mt5, is_live, model_eq, pnl)
                day_pnl += pnl
                if not day_halted and day_pnl <= -daily_stop_usd():
                    day_halted = True
                    print(f"[DAILY STOP] day PnL ${day_pnl:.2f} "
                          f"<= -${daily_stop_usd():.2f} (after manual close)")
                if model_eq <= EQUITY_FLOOR_USD and not day_halted:
                    day_halted = True
                    floored = True
                    print("[EQUITY FLOOR] manual review required")
                save_day_state(day, day_pnl, day_halted, floored)
                print(f"[MAN-SYNC] detected user close of "
                      f"{'long' if side == 1 else 'short'} "
                      f"@ {pos_model[1]:.5f} - booked at quote {actual:.5f} "
                      f"pnl ${pnl:+.2f} eq ${model_eq:.2f}")
                log_trade("CLOSE", ts_utc=utcnow().isoformat(), side=side,
                          price=f"{actual:.5f}", lots=LOTS,
                          note=f"{trade_note} eq {model_eq:.2f}")
                pending_tel[0] = None
                pos_model = None
            else:
                print("[MAN-SYNC] own position gone but no quote - retry "
                      "next heartbeat")
        elif pos_model is not None and mine:
            if not mine[0].sl:
                slp = (mine[0].price_open - SL_BACKSTOP_UNITS
                       if pos_model[0] == 1
                       else mine[0].price_open + SL_BACKSTOP_UNITS)
                r = mt5.order_send({"action": mt5.TRADE_ACTION_SLTP,
                                    "position": mine[0].ticket, "symbol": SYMBOL,
                                    "sl": round(slp, 5), "tp": mine[0].tp})
                oksl = r is not None and r.retcode == mt5.TRADE_RETCODE_DONE
                print(f"[MAN-SYNC] SL was stripped - backstop "
                      f"{'re-attached' if oksl else 'FAILED rc=' + str(getattr(r, 'retcode', None))}")
        elif pos_model is None and mine:
            newest = max(mine, key=lambda p: p.time)
            pos_model = (1 if newest.type == mt5.POSITION_TYPE_BUY else -1,
                         newest.price_open, newest.ticket)
            print(f"[MAN-SYNC] adopted manual position: "
                  f"{'long' if pos_model[0] == 1 else 'short'} "
                  f"@ {pos_model[1]:.5f} (ticket {newest.ticket})")

    def reconcile_book():
        """Live: keep the book equal to the broker's real balance.

        A balance op that lands while the executor is RUNNING (withdrawal,
        transfer, charge) would otherwise leave model_eq reading high, making
        the equity floor unreachable - exactly the 2026-10-05 -$4.00 case.
        Book the drift and re-check the floor so a charge can trip it at once.
        """
        nonlocal model_eq, day_halted, floored
        if not is_live:
            return
        try:
            bal = float(mt5.account_info().balance)
        except Exception:                                    # noqa: BLE001
            return
        drift = bal - model_eq
        if abs(drift) < BALANCE_OP_TOLERANCE_USD:
            return
        # The real balance also carries swap/commission, which the
        # price-derived book does not; a small drift is usually those, a
        # large one is a genuine op. Either way the money is gone/added, so
        # the honest move is to book it rather than ignore it.
        kind = "withdrawal/charge" if drift < 0 else "deposit/credit"
        print(f"    [BALANCE-OP] book ${model_eq:.2f} -> real ${bal:.2f} "
              f"(${drift:+.4f}, {kind}; incl. any swap/commission)")
        # Unique per distinct amount: the note is the dedup key, so a constant
        # note (the old "in-session withdrawal/charge") silently swallowed
        # every later op of the same kind - 2026-10-07's +/-$6.00 reconcile ops
        # never reached the CSV. Including the amount keeps the anti-restart
        # dedup while recording genuinely distinct movements.
        book_balance_op(drift, utcnow().isoformat(),
                        f"in-session {kind} {drift:+.4f}")
        model_eq = bal
        if model_eq <= EQUITY_FLOOR_USD and not day_halted:
            floored = True
            day_halted = True
            print(f"    [EQUITY FLOOR] book equity ${model_eq:.2f} <= "
                  f"${EQUITY_FLOOR_USD:.2f} AFTER a balance op - STAYING "
                  "halted (standing rule 2: review required)")
        save_day_state(day, day_pnl, day_halted, floored)

    def heartbeat(force=False):
        nonlocal last_hb, last_manual_sync, last_reconcile
        now = time.time()
        if now - last_manual_sync >= MAN_SYNC_SECONDS:
            last_manual_sync = now
            sync_open_position(now)
        if now - last_reconcile >= RECONCILE_SECONDS:
            last_reconcile = now
            reconcile_book()
        if force or now - last_hb >= HB_SECONDS:
            mins = (now - started) / 60
            ph = f"{cur_dir:+d} x{cnt}" if cur_dir else "flat"
            pd_ = f" armed {ep_dir:+d} @{ep_j}/{DELAY_TICKS}" if ep_dir else ""
            ps = f"{pos_model[0]} @ {pos_model[1]:.5f}" if pos_model else "none"
            mean_sp = spread_sum[0] / spread_sum[1] if spread_sum[1] else float("nan")
            halt = "  DAILY-HALT" if day_halted else ""
            ti = mt5.terminal_info()
            auto = "" if (ti is None or ti.trade_allowed) else "  AUTOTRADING-OFF!"
            # uptime instrumentation (Appendix AI bucket 1): the AI audit
            # showed 69% of engine opportunities were missed while the
            # executor was blind. Track stream-currency + armed/filled so
            # the reseed's availability is a measured number, not a hope.
            hb_current[1] += 1
            cur_ok = (last_ms / 1000.0 > time.time() - STALE_ENTRY_SECONDS)
            hb_current[0] += 1 if cur_ok else 0
            try:
                with open(UPTIME_JSON, "w") as f:
                    json.dump({"updated": utcnow().isoformat(),
                               "session_started": started,
                               "ticks": ticks_seen, "spikes": spikes,
                               "hb_current": hb_current,
                               "pending": {"armed": int(ep_dir),
                                           "age": int(ep_j if ep_dir else 0),
                                           "cancels": n_cancel,
                                           "fills": n_fill,
                                           "expires": n_expire,
                                           "floor_blocks": n_floor_block}}, f)
            except OSError:
                pass
            try:
                with open(ENGINE_STATE, "w") as f:
                    json.dump({"saved_at": time.time(),
                               "last_ms": int(last_ms),
                               "med": float(np.median(window)),
                               "cur_dir": int(cur_dir), "cnt": int(cnt),
                               "ep_dir": int(ep_dir)}, f)
            except OSError:
                pass
            print(f"[HB {mins:5.1f}m] ticks={ticks_seen:,} spikes={spikes} "
                  f"median={np.median(window):.5f} phase={ph}{pd_} pos={ps} "
                  f"model_eq=${model_eq:.2f} day_pnl=${day_pnl:.2f}{halt} "
                  f"stop=${daily_stop_usd():.2f} "
                  f"mean_spread={mean_sp:.4f}u "
                  f"cur={hb_current[0]}/{hb_current[1]} "
                  f"(model assumes 0.05u = ${0.05 * VPU:.3f}/trip){auto}")
            last_hb = now

    # ---------------- probe manual-close sync (order-path validation)
    if args.probe_manual_close:
        # redirect ledger writes to probe files so the production book and
        # slippage ledger stay clean (PROBE-prefixed rows + temp paths).
        probe_slip, probe_trade = f"{OUT_DIR}/probe_slip.csv", f"{OUT_DIR}/probe_trades.csv"
        real_slip, real_trade = SLIP_CSV, TRADE_CSV
        globals()["SLIP_CSV"], globals()["TRADE_CSV"] = probe_slip, probe_trade
        tick = mt5.symbol_info_tick(SYMBOL)
        print(f"[PROBE-MC] live quote: bid={tick.bid:.5f} ask={tick.ask:.5f}")
        ok, assumed, actual, rc = market_order(mt5, "buy", "flip-probe-mc",
                                               filling)
        detected = False
        if not ok:
            print(f"[PROBE-MC] open FAILED rc={rc}")
        else:
            time.sleep(2)
            pos_model = (1, actual)
            pending_tel[0] = None
            # snapshot production accumulators: the sync books probe PnL into
            # day_pnl/model_eq and saves day_state - restore + re-save after
            dp0, eq0, dh0, fl0 = day_pnl, model_eq, day_halted, floored
            # simulate the user clicking close in MT5: position vanishes
            # behind our back, pos_model stale
            positions = mt5.positions_get(symbol=SYMBOL)
            mine = [p for p in (positions or []) if p.magic == MAGIC]
            if mine:
                okc, _, actc, rcc = close_position(mt5, mine[0],
                                                   "flip-probe-mc-user", filling)
                print(f"[PROBE-MC] simulated user close: ok={okc} rc={rcc}")
            was_set = pos_model is not None
            sync_open_position(time.time(), mode="PROBE-MANEXIT",
                               trade_note="probe manual")
            detected = was_set and pos_model is None
            day_pnl, model_eq, day_halted, floored = dp0, eq0, dh0, fl0
            save_day_state(day, day_pnl, day_halted, floored)
        # never leak a position, whatever the probe did
        positions = mt5.positions_get(symbol=SYMBOL)
        mine = [p for p in (positions or []) if p.magic == MAGIC]
        if mine:
            okc2, _, _, rcc2 = close_position(mt5, mine[0], "flip-probe-mc-cleanup",
                                              filling)
            print(f"[PROBE-MC] cleanup close: ok={okc2} rc={rcc2}")
            pos_model = None
        rows = []
        try:
            with open(probe_slip) as f:
                rows = list(csv.DictReader(f))
        except OSError:
            pass
        man = [r for r in rows if r.get("mode") == "PROBE-MANEXIT"]
        trows = []
        try:
            with open(probe_trade) as f:
                trows = list(csv.DictReader(f))
        except OSError:
            pass
        print(f"[PROBE-MC] detection {'WORKS' if detected else 'FAILED'}; "
              f"PROBE-MANEXIT slip rows: {len(man)}; "
              f"CLOSE trade rows: {sum(1 for r in trows if r.get('event') == 'CLOSE')}")
        for r in man:
            print(f"[PROBE-MC] slip row: mode={r['mode']} "
                  f"actual={r['actual_deal']} note={r['note']} "
                  f"adverse={r['adverse_price_units']}")
        globals()["SLIP_CSV"], globals()["TRADE_CSV"] = real_slip, real_trade
        print("[PROBE-MC] done.")
        return

    # ---------------- probe order: validate the order path deterministically
    if args.probe_order:
        tick = mt5.symbol_info_tick(SYMBOL)
        print(f"[PROBE] live quote: bid={tick.bid:.5f} ask={tick.ask:.5f} "
              f"spread={tick.ask - tick.bid:.5f}")
        ok, assumed, actual, rc = market_order(mt5, "buy", "flip-probe-open", filling)
        adverse = log_slip("PROBE-ENTRY", utcnow().isoformat(), 0.0, "buy",
                           assumed, actual, rc, spread=tick.ask - tick.bid)
        slip_summary.append(adverse)
        print(f"[PROBE] open: assumed {assumed:.5f} actual {actual} rc={rc} "
              f"adverse={adverse}")
        if not ok:
            print("[PROBE] open FAILED - aborting probe")
            return
        time.sleep(2)
        positions = mt5.positions_get(symbol=SYMBOL)
        mine = [p for p in positions if p.magic == MAGIC]
        if not mine:
            print("[PROBE] position not found after open?!")
            return
        ok, assumed, actual, rc = close_position(mt5, mine[0], "flip-probe-close", filling)
        adverse = log_slip("PROBE-EXIT", utcnow().isoformat(), 0.0, "sell",
                           assumed, actual, rc,
                           spread=mt5.symbol_info_tick(SYMBOL).ask
                           - mt5.symbol_info_tick(SYMBOL).bid)
        slip_summary.append(adverse)
        print(f"[PROBE] close: assumed {assumed:.5f} actual {actual} rc={rc} "
              f"adverse={adverse}")
        good = [a for a in slip_summary if a is not None]
        if good:
            print(f"[PROBE] realized adverse slippage: mean {np.mean(good):+.5f} "
                  f"units = ${np.mean(good) * VPU:+.4f}/side")
        print("[PROBE] order path validated." if all(
            a is not None for a in slip_summary) else "[PROBE] check ledger")
        return

    # ---------------- main live loop
    deadline = time.time() + args.max_minutes * 60 if args.max_minutes else None
    try:
        while True:
            if deadline and time.time() > deadline:
                print(f"[STOP] --max-minutes {args.max_minutes} reached")
                break
            batch = mt5.copy_ticks_from(
                SYMBOL, datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc),
                1000, mt5.COPY_TICKS_ALL)
            if batch is None or len(batch) == 0:
                # Deafness watchdog: a healthy terminal streams ~60 ticks/min,
                # so an empty batch with a stale last_ms means THIS process's
                # MT5 session went deaf (the post-sleep wedge of 2026-09-12
                # that idled the machine ~18h while heartbeats kept printing).
                # Rebuild it; the backlog/STALE guards make the flush safe.
                if (time.time() - last_ms / 1000 > TICK_STALL_SECONDS
                        and time.time() - last_stall_reconnect > 60):
                    last_stall_reconnect = time.time()
                    print(f"[STALL] no tick for > {TICK_STALL_SECONDS}s "
                          "- session deaf (sleep/wake wedge), rebuilding")
                    reconnect(mt5)
                heartbeat()
                time.sleep(args.poll_seconds)
                continue
            for tk in batch:
                tms = int(tk["time_msc"])
                bid = float(tk["bid"])
                if tms <= last_ms or bid <= 0:
                    continue
                last_ms = tms
                ticks_seen += 1
                sample_spread(mt5)
                now_iso = datetime.fromtimestamp(tms / 1000, tz=timezone.utc).isoformat()
                # backlog guard: after sleep/wifi loss the reconnect flush
                # replays hours of stale ticks. Exits stay live (protective),
                # but entries on stale prices are measurement pollution.
                stale = (utcnow() - datetime.fromtimestamp(
                    tms / 1000, tz=timezone.utc)).total_seconds() \
                    > STALE_ENTRY_SECONDS
                if stale and not backlog_note:
                    backlog_note = True
                    print(f"[BACKLOG] stale ticks (> {STALE_ENTRY_SECONDS}s "
                          "old) - entries suppressed until stream is current")
                elif not stale and backlog_note:
                    backlog_note = False
                    print("[BACKLOG] stream current - entries re-enabled")
                # UTC day roll: reset the daily-stop accumulator
                if now_iso[:10] != day:
                    day = now_iso[:10]
                    day_pnl = 0.0
                    if floored:
                        print(f"[DAY RESET] {day} - equity floor active, "
                              "STILL halted (manual review required)")
                    elif day_halted:
                        print(f"[DAY RESET] {day} - entries re-enabled")
                        day_halted = False
                    save_day_state(day, day_pnl, day_halted, floored)
                # gap guard (mirrors the backtest in-sim hole logic): a jump of
                # >5min between consecutive ticks is a data hole (sleep wedge,
                # reconnect flush). The delta across it is fake; act on the
                # position, never on the signal.
                if prev_ms is not None and tms - prev_ms > GAP_MS:
                    hole_min = round((tms - prev_ms) / 60000, 1)
                    if pos_model is not None:
                        print(f"[GAP] {hole_min}min hole - closing position "
                              "across it (backtest rule)")
                        positions = mt5.positions_get(symbol=SYMBOL)
                        mine = [p for p in (positions or []) if p.magic == MAGIC]
                        if mine:
                            ok, assumed, actual, rc = close_position(
                                mt5, mine[0], "flip-gap-close", filling)
                            if ok:
                                pnl = pos_model[0] * (actual - pos_model[1]) * VPU
                                model_eq = live_close_book(
                                    mt5, is_live, model_eq, pnl)
                                day_pnl += pnl
                                if not day_halted and day_pnl <= -daily_stop_usd():
                                    day_halted = True
                                    print(f"[DAILY STOP] day PnL ${day_pnl:.2f} "
                                          f"<= -${daily_stop_usd():.2f}")
                                if model_eq <= EQUITY_FLOOR_USD and not day_halted:
                                    day_halted = True
                                    floored = True
                                    print("[EQUITY FLOOR] manual review required")
                                save_day_state(day, day_pnl, day_halted, floored)
                                side_word = "long" if pos_model[0] == 1 else "short"
                                print(f"[GAP] CLOSE {side_word} @ {actual:.5f}  "
                                      f"pnl ${pnl:+.2f}  eq ${model_eq:.2f}")
                                log_trade("CLOSE", ts_utc=now_iso, side=pos_model[0],
                                          price=f"{actual:.5f}", lots=LOTS,
                                          note=f"gap eq {model_eq:.2f}")
                                pos_model = None
                            else:
                                print(f"[GAP] close FAILED rc={rc} - SL remains "
                                      "attached; retry on next spike")
                    cur_dir, cnt = 0, 0
                    ep_dir, ep_j = 0, -10**9   # data hole: phase AND pending die
                    ep_tel = [0.0, None, None, None]
                    ep_delta = 0.0
                    print(f"[GAP] {hole_min}min hole - phase reset, gap delta skipped")
                    prev_ms = tms
                    prev_bid = bid
                    continue
                prev_ms = tms
                if prev_bid is not None:
                    delta = bid - prev_bid
                    window.append(abs(delta))
                    minor_hist.append(abs(delta))
                    med = np.median(window)
                    gate_rel = abs(delta) > MULT * med
                    gate_abs = abs(delta) >= SPIKE_FLOOR_UNITS
                    if gate_rel and not gate_abs:
                        n_floor_block += 1
                        print(f"[FLOOR] {'SHADOW ' if SPIKE_FLOOR_SHADOW else ''}"
                              f"blocked arm delta={delta:+.5f} "
                              f"x={abs(delta) / med if med else 0.0:.2f} "
                              f"med={med:.5f} (blocks={n_floor_block})")
                    if gate_rel and (gate_abs or SPIKE_FLOOR_SHADOW):
                        spikes += 1
                        spike_hist.append(tms)
                        cur_tel = telemetry_fields(delta, med, tms,
                                                   spike_hist, bar_closes,
                                                   minor_hist)
                        s = int(np.sign(delta))
                        spike_ts = now_iso
                        if s == cur_dir:
                            cnt += 1
                        else:
                            cur_dir, cnt = s, 1
                        # Menu-C pending cancel: opposite qualifying spike
                        # kills an armed pending (run_conv semantics).
                        if ep_dir != 0 and s != ep_dir:
                            n_cancel += 1
                            print(f"[PENDING] canceled by opposite spike "
                                  f"({ep_dir:+d} armed, {s:+d} arrived) "
                                  f"cancels={n_cancel}")
                            ep_dir, ep_j = 0, -10**9
                            ep_tel = [0.0, None, None, None]
                            ep_delta = 0.0
                        # exit: counter-spike against an open position
                        if pos_model and s != pos_model[0]:
                            positions = mt5.positions_get(symbol=SYMBOL)
                            mine = [p for p in positions if p.magic == MAGIC]
                            if mine:
                                ok, assumed, actual, rc = close_position(
                                    mt5, mine[0], "flip-exit", filling)
                                adverse = log_slip("EXIT", spike_ts, delta,
                                                   "sell" if pos_model[0] == 1 else "buy",
                                                   assumed, actual, rc,
                                                   spread=(spread_sum[0] / spread_sum[1])
                                                   if spread_sum[1] else None,
                                                   tel=(tuple(pending_tel)
                                                        if pending_tel[0] is not None
                                                        else None))
                                slip_summary.append(adverse)
                                if ok:
                                    pnl = pos_model[0] * (actual - pos_model[1]) * VPU
                                    model_eq = live_close_book(
                                        mt5, is_live, model_eq, pnl)
                                    day_pnl += pnl
                                    if not day_halted and day_pnl <= -daily_stop_usd():
                                        day_halted = True
                                        print(f"[DAILY STOP] day PnL ${day_pnl:.2f} "
                                              f"<= -${daily_stop_usd():.2f} - "
                                              "no new entries until next UTC day")
                                    if model_eq <= EQUITY_FLOOR_USD and not day_halted:
                                        day_halted = True
                                        floored = True
                                        print(f"[EQUITY FLOOR] model equity "
                                              f"${model_eq:.2f} <= ${EQUITY_FLOOR_USD:.2f} "
                                              "- flat, no entries until manual restart")
                                    save_day_state(day, day_pnl, day_halted, floored)
                                    print(f"CLOSE {'long' if pos_model[0] == 1 else 'short'} "
                                          f"@ {actual:.5f}  assumed {assumed:.5f}  "
                                          f"adverse {adverse:+.5f}  pnl ${pnl:+.2f}  "
                                          f"eq ${model_eq:.2f}")
                                    log_trade("CLOSE", ts_utc=now_iso,
                                              side=pos_model[0], price=f"{actual:.5f}",
                                              lots=LOTS, note=f"eq {model_eq:.2f}")
                                    pos_model = None
                                else:
                                    print(f"CLOSE FAILED rc={rc} - will retry next spike")
                            else:
                                acct_now = mt5.account_info()
                                if acct_now is None or acct_now.login != start_login:
                                    print("[ACCOUNT SWITCH] terminal moved off the "
                                          f"session account ({start_login}) - keeping "
                                          "position tracking until it returns")
                                elif book_broker_close(pos_model, "EXIT",
                                                       "broker-close (spike path)"):
                                    pos_model = None
                                else:
                                    print("[ORPHAN] position vanished and NO broker "
                                          "deal found - PnL NOT booked; heal from "
                                          "broker history (scripts/heal_ledger_slfail.py "
                                          "pattern) before trusting the book")
                                    pos_model = None     # stop tracking; flagged loudly
                        # entry: new phase reached the age gate (suppressed by
                        # the daily stop; exits above are never suppressed).
                        # Duplicate guard: never open when an own-magic
                        # position already exists (belt and braces for the
                        # instance lock - a second instance must not double
                        # the book even if it somehow starts).
                        # entry (Menu-C): a qualifying spike ARMS a pending
                        # entry - it fills DELAY_TICKS ticks later at market,
                        # unless an opposite spike cancels it first (the
                        # "wait 3 bars for confirmation" rule, Appendix AF).
                        # Arm is suppressed while stale or day-halted; the
                        # pending itself persists and fills when eligible.
                        elif (not pos_model and ep_dir == 0 and cur_dir == s
                              and cnt >= MIN_AGE + 1 and not day_halted
                              and not stale):
                            ep_dir, ep_j = s, 0
                            ep_tel = list(cur_tel) if cur_tel else [0.0, None, None, None]
                            ep_delta = float(delta)
                            print(f"[PENDING] armed {ep_dir:+d} (phase age {cnt - 1}, "
                                  f"x={ep_tel[0]:.1f}) - fills in {DELAY_TICKS} ticks "
                                  f"unless an opposite spike cancels")
                        elif (not pos_model and day_halted and cur_dir == s
                              and cnt >= MIN_AGE + 1):
                            if floored:
                                print("[HALT] entry skipped - equity floor "
                                      f"tripped (book ${model_eq:.2f}, "
                                      "floored:true); manual review required")
                            else:
                                print(f"[HALT] entry skipped - day PnL "
                                      f"${day_pnl:.2f} <= "
                                      f"-${daily_stop_usd():.2f}")
                bar_closes[tms // BAR_MS] = bid   # telemetry: M5 closes
                prev_bid = bid
                # Menu-C pending FILL/EXPIRE: counts ticks (run_conv counts
                # loop iterations, not wall-clock), fills at market when the
                # delay elapses and the book allows; expires after a grace
                # window if halts/staleness blocked it past DELAY+PENDING_MAX_OVER.
                if pos_model is None and ep_dir != 0:
                    ep_j += 1
                    if ep_j >= DELAY_TICKS + PENDING_MAX_OVER:
                        n_expire += 1
                        print(f"[PENDING] expired unfilled (blocked "
                              f"{PENDING_MAX_OVER}+ ticks past delay) "
                              f"expires={n_expire}")
                        ep_dir, ep_j, ep_tel = 0, -10**9, [0.0, None, None, None]
                        ep_delta = 0.0
                    elif (ep_j >= DELAY_TICKS and not day_halted and not stale):
                        already = [p for p in (mt5.positions_get(symbol=SYMBOL) or [])
                                   if p.magic == MAGIC]
                        if not already:
                            side = "buy" if ep_dir > 0 else "sell"
                            ok, assumed, actual, rc = market_order(
                                mt5, side, "flip-enter-d300", filling)
                            tel_fill = list(ep_tel)
                            tel_fill[0] = float(tel_fill[0] or 0.0)
                            adverse = log_slip("ENTRY", now_iso, ep_delta, side,
                                               assumed, actual, rc,
                                               spread=(spread_sum[0] / spread_sum[1])
                                               if spread_sum[1] else None,
                                               tel=tuple(tel_fill))
                            slip_summary.append(adverse)
                            if ok:
                                mine_now = [p for p in
                                            (mt5.positions_get(symbol=SYMBOL) or [])
                                            if p.magic == MAGIC]
                                tk = mine_now[0].ticket if mine_now else None
                                pos_model = (ep_dir, actual, tk)
                                pending_tel = tel_fill
                                n_fill += 1
                                print(f"OPEN {side} @ {actual:.5f}  assumed {assumed:.5f}  "
                                      f"adverse {adverse:+.5f}  (delay300 fill "
                                      f"{n_fill}, armed x={ep_tel[0]:.1f})")
                                log_trade("OPEN", ts_utc=now_iso, side=ep_dir,
                                          price=f"{actual:.5f}", lots=LOTS,
                                          note=f"age {cnt - 1} d300")
                                ep_dir, ep_j, ep_tel = 0, -10**9, [0.0, None, None, None]
                                ep_delta = 0.0
                            else:
                                print(f"OPEN FAILED rc={rc} - pending stays "
                                      "armed, retry next tick")
                        else:
                            print(f"[PENDING] fill skipped - own position "
                                  "already open; dropping pending")
                            ep_dir, ep_j, ep_tel = 0, -10**9, [0.0, None, None, None]
                            ep_delta = 0.0
            heartbeat()
            time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        print("\n[STOP] Ctrl+C")
    finally:
        heartbeat(force=True)
        good = [a for a in slip_summary if a is not None]
        if pos_model:
            print(f"[NOTE] position still open ({pos_model[0]} @ {pos_model[1]:.5f}) "
                  "- orphaned at exit, close manually or next session adopts it")
        if good:
            print(f"=== slippage summary: n={len(good)}  mean adverse "
                  f"{np.mean(good):+.5f}u (${np.mean(good) * VPU:+.4f}/side)  "
                  f"worst {min(good):+.5f}u ===")
        if spread_sum[1]:
            mean_sp = spread_sum[0] / spread_sum[1]
            extra = (mean_sp - 0.05) * VPU
            print(f"=== spread: mean {mean_sp:.4f}u over {spread_sum[1]} samples "
                  f"(model: 0.05u) -> extra friction ${extra:+.4f}/round-trip "
                  f"vs the $0.235/trade edge ===")
        if good:
            print(f"    compare: backtest edge = $0.235/trade. If (extra friction "
                  f"+ mean adverse slippage) approaches that, the $10 flip is "
                  f"dead on arrival.")
        print(f"=== session: {ticks_seen:,} ticks, {spikes} spikes, "
              f"floor_blocks {n_floor_block} "
              f"(shadow={SPIKE_FLOOR_SHADOW})  "
              f"model_eq ${model_eq:.2f} ===")
        mt5.shutdown()


if __name__ == "__main__":
    main()
