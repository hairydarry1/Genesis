#!/usr/bin/env python3
"""Heal the ledger for the Sep 16 18:10:06Z SL close the book never recorded.

Incident (found Sep 17): the executor opened a short at 2026-09-16 14:48:53Z
(ticket 8770365839, entry 9400.31430). The 60u backstop SL fired broker-side
at 18:10:06Z (deal 8652044438, buy 0.1 @ 9460.29910, pnl -$6.00) while the
PC was asleep. On wake, the executor's spike path found no position and
silently dropped pos_model without booking (fixed separately). Result: the
book is overstated by exactly $6.00 from 18:10Z Sep 16 onward.

This script, ONE TIME (idempotent: refuses to run twice):
  1. Backs up data/flip_exec/{trades.csv,slippage_ledger.csv,day_state.json}
     to archive/heal_slfail_20260916/ (fails if backups already exist).
  2. Appends CLOSE to trades.csv: ts 2026-09-16T18:10:06+00:00, side -1
     (short), price 9460.29910, note "SL backstop (broker deal 8652044438)".
  3. Appends EXIT to slippage_ledger.csv with the ENTRY's telemetry tuple
     (x=479.71, act=4, t24=0, min_2k=0), delta +59.98480, spread 0.62610,
     note 'SL-FIRED-RECORD'.
  4. Restates day_state.json for the Sep 16 UTC day: day_pnl = sum of all
     Sep 16 CLOSE pnl = -2.70 (this run) and marks it halted — the stop
     must apply retroactively to the day it belonged to.

Usage: python scripts/heal_ledger_slfail.py
"""
import csv
import json
import os
import shutil
import sys

BASE = "."
TRADES = os.path.join(BASE, "data", "flip_exec", "trades.csv")
SLIP = os.path.join(BASE, "data", "flip_exec", "slippage_ledger.csv")
DAYSTATE = os.path.join(BASE, "data", "flip_exec", "day_state.json")
BKDIR = os.path.join(BASE, "archive", "heal_slfail_20260916")

CLOSE_TS = "2026-09-16T18:10:06+00:00"
CLOSE_PX = "9460.29910"
CLOSE_NOTE = "SL backstop (broker deal 8652044438)"
ENTRY_TS_PREFIX = "2026-09-16T14:48"
EXIT_DELTA = "+59.98480"          # 9460.29910 - 9400.31430 (short adverse)
EXIT_NOTE = "SL-FIRED-RECORD"
SLIP_COLS = ["mode", "spike_ts_utc", "spike_delta", "side", "assumed_fill",
             "actual_deal", "adverse_price_units", "adverse_usd",
             "spread_at_order", "note", "entry_x", "act_2h", "trend_2h",
             "min_2k"]


def main():
    already = os.path.exists(BKDIR)
    if already:
        print(f"REFUSING: backup dir {BKDIR} exists - this heal already ran.")
        sys.exit(1)

    # verify the trades.csv hole is what we expect before touching anything
    with open(TRADES) as f:
        trows = list(csv.DictReader(f))
    i_entry = max(i for i, r in enumerate(trows)
                  if r["event"] == "OPEN"
                  and r["ts_utc"].startswith(ENTRY_TS_PREFIX))
    nxt = trows[i_entry + 1]
    if nxt["event"] != "OPEN":
        print(f"UNEXPECTED: row after the 14:48 OPEN is {nxt['event']} "
              "- the book may already be healed or changed. Aborting.")
        sys.exit(1)

    # 1. backup
    os.makedirs(BKDIR, exist_ok=True)
    for p in (TRADES, SLIP, DAYSTATE):
        shutil.copy2(p, os.path.join(BKDIR, os.path.basename(p)))
    print(f"backup -> {BKDIR}")

    # 2. trades.csv CLOSE (v2: inserted chronologically; see 3b below)
    # (row insertion happens in step 3b after the ledger block)

    # 3. slippage_ledger.csv EXIT (telemetry copied from the ENTRY row)
    #    INSERTED at its chronological slot (v2): the ledger is ordered by
    #    spike_ts_utc; appending at file end put the row after later-dated
    #    entries and mispaired the live book (found immediately on verify).
    with open(SLIP) as f:
        rows = list(csv.DictReader(f))
    entry = next(r for r in rows
                 if r["mode"] == "ENTRY"
                 and r["spike_ts_utc"].startswith(ENTRY_TS_PREFIX))
    i_e = rows.index(entry)
    exit_row = {c: "" for c in SLIP_COLS}
    exit_row.update({
        "mode": "EXIT",
        "spike_ts_utc": CLOSE_TS,
        "spike_delta": EXIT_DELTA,
        "side": "buy",                       # short's close is a buy
        "assumed_fill": CLOSE_PX,
        "actual_deal": CLOSE_PX,
        "adverse_price_units": "+0.00000",
        "adverse_usd": "+0.0000",
        "spread_at_order": entry["spread_at_order"],
        "note": EXIT_NOTE,
        "entry_x": entry["entry_x"],
        "act_2h": entry["act_2h"],
        "trend_2h": entry["trend_2h"],
        "min_2k": entry["min_2k"],
    })
    rows.insert(i_e + 1, exit_row)          # right after its ENTRY
    with open(SLIP, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SLIP_COLS)
        w.writeheader()
        w.writerows(rows)
    print(f"ledger      + EXIT  {CLOSE_TS} @ {CLOSE_PX} INSERTED after its "
          f"ENTRY (tel x={entry['entry_x']} act={entry['act_2h']} "
          f"t24={entry['trend_2h']} min_2k={entry['min_2k']})")

    # 3b. trades.csv CLOSE: also insert at its chronological slot (v2)
    i_open = next(i for i, r in enumerate(trows)
                  if r["event"] == "OPEN"
                  and r["ts_utc"].startswith(ENTRY_TS_PREFIX))
    trows.insert(i_open + 1, {"event": "CLOSE", "ts_utc": CLOSE_TS,
                              "side": "-1", "price": CLOSE_PX, "lots": "0.1",
                              "note": CLOSE_NOTE})
    with open(TRADES, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["event", "ts_utc", "side", "price",
                                          "lots", "note"])
        w.writeheader()
        w.writerows(trows)
    print(f"trades.csv  + CLOSE {CLOSE_TS} INSERTED after its OPEN")

    # 4. restate Sep 16 day PnL (from the healed chronological rows)
    day = "2026-09-16"
    pnl16 = 0.0
    for idx, r in enumerate(trows):
        if r["event"] != "CLOSE" or not r["ts_utc"].startswith(day):
            continue
        d = 1 if r["side"] == "1" else -1
        entry_px = None
        for e in reversed(trows[:idx]):
            if e["event"] == "OPEN":
                entry_px = float(e["price"])
                break
        if entry_px is None:
            continue
        pnl16 += d * (float(r["price"]) - entry_px) * 0.1
    with open(DAYSTATE) as f:
        st = json.load(f)
    if st.get("date") == day:
        # current live day is Sep 17's file; only rewrite if it holds Sep 16
        print(f"day_state.json holds {day} - rewriting (pnl "
              f"{st.get('day_pnl')} -> {pnl16:.2f}, halted=true)")
        st["day_pnl"] = round(pnl16, 2)
        st["day_halted"] = True
        with open(DAYSTATE, "w") as f:
            json.dump(st, f)
    else:
        print(f"day_state.json holds {st.get('date')} (live day) - Sep 16 "
              "state not rewritten (historical day; scorer reads ledger)")

    print(f"\nrestated Sep 16 day_pnl = ${pnl16:+.2f} "
          f"(was reported $-1.70 -> -$6.00 unbooked = ${pnl16:+.2f})")
    print("heal complete. Re-run scorers to verify pairing.")


if __name__ == "__main__":
    main()
