#!/usr/bin/env python3
"""Manual-close counterfactual: what would the bot's exit rule have realized?

Question (Sep 28): the user's 11 manual/broker closes since the Sep 17
reseed netted +$19.01 (10W/1L) while the bot's autonomous trades netted
-$5.60. Is the human exit layer genuinely additive, or luck?

Method: for each manual-closed position (OPEN/CLOSE pair in trades.csv),
replay the archive forward from the real entry fill and apply the
EXECUTOR'S exit rule only:
  - close on the first opposite spike (|delta| > 5x rolling median,
    executor counter-spike exit, Menu C)
  - SL backstop at 60u adverse (the deployed catastrophic rail)
  - hole deltas are skipped as signals (executor GAP logic)
No daily stop, no TP, no time exit (the executor has none). Gross PnL
convention on BOTH sides (fills carry the spread; no separate charge).

Note: by construction the bot rule never fired before the manual close
(the executor would have exited itself), so every counterfactual exit is
at/after the manual close - this measures what holding under bot rules
would have done AFTER the moment the user chose to exit.

Read-only: prints a table + totals. Writes nothing.
Usage: python analysis/flip_manual_counterfactual.py
"""
import csv
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load
from flip_rolling_engine import rolling_medians, PER_UNIT
from flip_gapmask import bad_tick_mask

TRADES = "data/flip_exec/trades.csv"
SYMBOL = "flip1000"
MULT = 5.0
SL_UNITS = 60.0
RESEED = "2026-09-17"


def iso_ms(ts):
    return int(datetime.fromisoformat(ts).timestamp() * 1000)


def main():
    rows = list(csv.DictReader(open(TRADES)))
    manual = []
    pos = None
    for r in rows:
        if r["event"] == "OPEN":
            pos = (int(r["side"]), float(r["price"]), iso_ms(r["ts_utc"]))
        elif "manual" in r["note"] and pos is not None:
            s, e, oms = pos
            cpx, cms = float(r["price"]), iso_ms(r["ts_utc"])
            if r["ts_utc"][:10] >= RESEED:
                manual.append(dict(oms=oms, entry=e, side=s, cms=cms,
                                   exit=cpx,
                                   pnl=(cpx - e) * s * PER_UNIT))
            pos = None
        elif r["event"] == "CLOSE":
            pos = None

    print(f"manual closes since {RESEED}: {len(manual)}")
    ms, px = load(SYMBOL)
    bad = bad_tick_mask(ms)
    med = rolling_medians(ms, px)

    tot_man = tot_bot = 0.0
    bot_wins = 0
    print(f"\n{'entry (UTC)':16s} {'sd':>2s} {'manual':>8s} {'bot-rule':>9s} "
          f"{'delta':>8s}  bot-exit")
    for m in sorted(manual, key=lambda x: x["oms"]):
        j = int(np.searchsorted(ms, m["oms"]))
        exit_px = exit_ms = None
        tag = ""
        while j < len(px) - 1:
            if bad[j + 1]:
                j += 1
                continue
            d = px[j + 1] - px[j]
            adverse = (px[j + 1] - m["entry"]) * m["side"]
            if adverse <= -SL_UNITS:
                exit_px, exit_ms, tag = px[j + 1], ms[j + 1], "SL-backstop"
                break
            if (m["side"] > 0 and d < -MULT * med[j]) or \
               (m["side"] < 0 and d > MULT * med[j]):
                exit_px, exit_ms, tag = px[j + 1], ms[j + 1], "counter-spike"
                break
            j += 1
        if exit_px is None:
            bot_pnl = (px[-1] - m["entry"]) * m["side"] * PER_UNIT
            tag = "still open at archive end (MTM)"
        else:
            bot_pnl = (exit_px - m["entry"]) * m["side"] * PER_UNIT
        dt_h = ((exit_ms or ms[-1]) - m["cms"]) / 3_600_000
        tot_man += m["pnl"]
        tot_bot += bot_pnl
        bot_wins += bot_pnl > 0
        ets = datetime.fromtimestamp((exit_ms or ms[-1]) / 1000, tz=timezone.utc)
        print(f"{datetime.fromtimestamp(m['oms'] / 1000, tz=timezone.utc):%m-%d %H:%M} "
              f"{m['side']:+d} {m['pnl']:+8.2f} {bot_pnl:+9.2f} "
              f"{bot_pnl - m['pnl']:+8.2f}  {tag} @{ets:%m-%d %H:%M} (+{dt_h:.1f}h)")

    print(f"\nTOTALS: manual {tot_man:+.2f} vs bot-rule {tot_bot:+.2f} "
          f"(delta {tot_bot - tot_man:+.2f}) | bot-rule W/L "
          f"{bot_wins}/{len(manual) - bot_wins}")
    if tot_bot >= tot_man:
        print("VERDICT: hands-off would have done AT LEAST as well - "
              "manual exits not justified by this sample")
    else:
        print(f"VERDICT: manual exits added {tot_man - tot_bot:+.2f} on this "
              "sample - potentially real skill, but n is tiny")


if __name__ == "__main__":
    main()
