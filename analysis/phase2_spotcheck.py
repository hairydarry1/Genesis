#!/usr/bin/env python3
"""Phase 2 live-vs-replay spot-check (read-only diagnostics, 2026-09-21).

Feeds REAL broker ticks for a UTC window (copy_ticks_range) through the
IDENTICAL SymbolEngine/EquityBook from eval/paper_trading_protected.py with
the locked measurement flags (halts OFF, fixed sizing, no safety-tick exit,
cold-start warmup), driven by REAL timestamps.

Tests SEMANTIC parity, not bit-identity: live samples quotes at ~2Hz poll
while this replay consumes every archived broker tick - identical strategy
logic on a differently-sampled feed of the same generator. Entry/exit hours,
hold lengths, exit modes, per-trade PnL signs and book path should line up
within sampling noise.

log_trade is patched to capture rows in-memory: this tool NEVER writes to
SpikeCatcher/paper_trading_log.csv (the live measurement log).

Usage: python analysis/phase2_spotcheck.py [--poll-sim] [snapshot.csv]
       (window: 2026-09-21 14:33 UTC -> snapshot's last CLOSE row + margin)

--poll-sim: resample the broker tick record onto the live loop's 0.5s poll
grid (last-quote-hold) before feeding the engine, mimicking what the live
session actually saw. If THIS matches the live trades while full-tick replay
does not, the divergence is feed sampling, not engine semantics.
"""
import csv
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import MetaTrader5 as mt5

sys.path.insert(0, str(Path(__file__).parent.parent))
import eval.paper_trading_protected as p2  # noqa: E402

ARGS = [a for a in sys.argv[1:] if not a.startswith("-")]
POLL_SIM = "--poll-sim" in sys.argv
SNAPSHOT = ARGS[0] if ARGS else "archive/phase2_spotcheck_snapshot.csv"
SYMS = ["boom1000", "crash1000", "boom500", "crash500"]
MT5_NAMES = {"boom500": "Boom 500 Index", "crash500": "Crash 500 Index",
             "boom1000": "Boom 1000 Index", "crash1000": "Crash 1000 Index"}
END_MARGIN_MIN = 2   # keep replaying this many minutes past the snapshot's last row
REPLAY_FLOOR = "2026-09-21 14:30"  # just before process start 14:33:51 UTC

# ---------- live ground truth ----------
live_rows = list(csv.DictReader(open(SNAPSHOT)))
live_closes = [r for r in live_rows if r["action"] == "CLOSE"]
last_close = live_closes[-1]["timestamp"][:19]
end_dt = (datetime.fromisoformat(last_close)
          .replace(tzinfo=timezone.utc)
          + timedelta(minutes=END_MARGIN_MIN))
start_dt = datetime.fromisoformat(REPLAY_FLOOR).replace(tzinfo=timezone.utc)
print(f"window: {start_dt:%H:%M} -> {end_dt:%H:%M} UTC "
      f"({(end_dt - start_dt).total_seconds()/60:.0f} min) | "
      f"live: {len(live_rows)} rows, {len(live_closes)} closes")

# ---------- MT5 fetch ----------
kwargs = {"timeout": 10000}
from data.mt5_tick_connector import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
if MT5_PATH:
    kwargs["path"] = MT5_PATH
if MT5_LOGIN:
    kwargs.update({"login": MT5_LOGIN, "password": MT5_PASSWORD, "server": MT5_SERVER})
if not mt5.initialize(**kwargs):
    print(f"MT5 init failed: {mt5.last_error()}")
    sys.exit(1)

streams = []
for s in SYMS:
    ticks = mt5.copy_ticks_range(MT5_NAMES[s], start_dt, end_dt, mt5.COPY_TICKS_ALL)
    n = 0 if ticks is None else len(ticks)
    print(f"  {s}: {n:,} ticks")
    if n:
        streams.append((s, ticks["time_msc"].astype(float) / 1000.0,
                        ticks["bid"].astype(float)))
mt5.shutdown()
if not streams:
    print("no tick data")
    sys.exit(1)

if POLL_SIM:
    grid_streams = []
    STEP = 0.5
    for s, ts, px in streams:
        t0, t1 = ts[0], ts[-1]
        n_g = int((t1 - t0) / STEP) + 1
        grid = t0 + np.arange(n_g) * STEP
        idx = np.searchsorted(ts, grid, side="right") - 1  # last quote <= grid point
        gpx = px[np.maximum(idx, 0)]
        keep = grid[1:][idx[1:] >= 0] if False else None  # (idx>=0 always after warmup)
        grid_streams.append((s, grid, gpx))
    streams = grid_streams
    print(f"poll-sim: resampled onto 0.5s grid ({sum(len(g[1]) for g in streams):,} samples)")

# ---------- capture log_trade ----------
CAPTURED = []


def fake_log_trade(symbol, action, direction, entry_price, exit_price, pnl,
                   reason, bars_held, hour, cold, equity, event_ts=None):
    ts = (datetime.fromtimestamp(event_ts, tz=timezone.utc).isoformat()
          if event_ts else "")
    CAPTURED.append({"timestamp": ts, "symbol": symbol, "action": action,
                     "direction": direction, "entry_price": entry_price,
                     "exit_price": exit_price, "pnl": pnl, "reason": reason,
                     "bars_held": bars_held, "hour": hour, "cold_hour": cold,
                     "equity_after": equity})


p2.log_trade = fake_log_trade  # patch BEFORE engines exist; no CSV writes

# ---------- identical engines, locked measurement flags ----------
book = p2.EquityBook(200.0, risk_per_trade=p2.RISK_PER_TRADE, halts_enabled=False)
engines = [p2.SymbolEngine(s, book, safety_tick_exit=False, fixed_sizing=True,
                           live_warmup=False, force_cold=False) for s in SYMS]
eng_by = {e.symbol: e for e in engines}

import heapq
import itertools

events = heapq.merge(*[zip(ts, itertools.repeat(k), prices)
                       for k, (_, ts, prices) in enumerate(streams)],
                     key=lambda ev: ev[0])
total = 0
for t, k, p in events:
    engines[k].on_tick(t, float(p))
    engines[k].ticks_seen += 1
    total += 1
print(f"replayed {total:,} ticks | final equity {book.equity:.2f}")

rep_closes = [r for r in CAPTURED if r["action"] == "CLOSE"]

# ---------- comparison ----------
def pnl_f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


print("\n===== per-trade: LIVE (L) vs REPLAY (R) =====")
print(f"{'#':>2} {'sym':9s} {'dir':5s} {'exit':13s} | {'L pnl':>9s} {'R pnl':>9s} | "
      f"{'L b':>3s} {'R b':>3s} | {'L hr':>4s} {'R hr':>4s}")
sign_mismatch = 0
n = max(len(live_closes), len(rep_closes))
for i in range(n):
    lc = live_closes[i] if i < len(live_closes) else None
    rc = rep_closes[i] if i < len(rep_closes) else None
    if lc and rc:
        lp, rp = pnl_f(lc["pnl"]), rc["pnl"]
        flag = "" if (lp > 0) == (rp > 0) else "  <-- SIGN"
        if flag:
            sign_mismatch += 1
        print(f"{i+1:>2} {lc['symbol']:9s} {lc['direction']:5s} "
              f"{lc['reason'][:13]:13s} | {lp:9.4f} {rp:9.4f} | "
              f"{lc['bars_held']:>3s} {rc['bars_held']:>3d} | "
              f"{lc['hour']:>4s} {rc['hour']:>4d}{flag}")
    elif lc:
        print(f"{i+1:>2} {lc['symbol']:9s} LIVE ONLY: {lc['reason']}")
    else:
        print(f"{i+1:>2} {rc['symbol']:9s} REPLAY ONLY: {rc['reason']}")

live_pnl = sum(pnl_f(r["pnl"]) for r in live_closes)
rep_pnl = sum(r["pnl"] for r in rep_closes)
print("\n===== summary =====")
print(f"trades closed : live {len(live_closes)} | replay {len(rep_closes)}")
print(f"net pnl       : live {live_pnl:+.2f} | replay {rep_pnl:+.2f}")
print(f"final equity  : live (snapshot) {live_rows[-1]['equity_after']} | replay {book.equity:.2f}")
print(f"exit-mode mix : live { {k: sum(1 for r in live_closes if r['reason']==k) for k in {r['reason'] for r in live_closes}} }")
print(f"                replay { {k: sum(1 for r in rep_closes if r['reason']==k) for k in {r['reason'] for r in rep_closes}} }")
print(f"sign mismatches: {sign_mismatch} / {min(len(live_closes), len(rep_closes))} paired")

# diagnostic dumps for eyeball diff
with open("archive/phase2_spotcheck_replay_rows.csv", "w", newline="") as f:
    w = csv.DictWriter(f, fieldnames=list(CAPTURED[0].keys()))
    w.writeheader()
    w.writerows(CAPTURED)
print("\nreplay rows -> archive/phase2_spotcheck_replay_rows.csv (no live files touched)")
