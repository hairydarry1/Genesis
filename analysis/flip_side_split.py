#!/usr/bin/env python3
"""Appendix P settle-check: live long/short split vs backtest, matched windows.

Pre-registered watch (Appendix P, Sep 14): live sides were inverted vs the
backtest — longs −$2.09 (WR 33%) vs shorts +$2.19 (WR 43%) — while the whole
backtest says longs are the strong side. n=26 then; settle gate is n>=50.

Live side: data/flip_exec/slippage_ledger.csv — ENTRY/EXIT pairs (both filled),
PnL = side * (exit - entry) * VPU with VPU = $0.1/price-unit at 0.1 lot.
Backtest side: analysis/flip_concepts.simulate (the fleet engine, gap-masked,
live spread 0.6261u per episode) on the healed archive, run three ways:
  base  = both sides (matches the live all-sides book)
  L     = long-only    S = short-only
Matched window: backtest arrays restricted to the live window with a 1h
pre-roll for warmup; trips counted if their close lands inside the window.
Conventions note (recorded, not hidden): live PnL is gross-of-spread (the
spread is charged on the real account, not in our book) and live trips close
on counter-spike OR SL backstop; the backtest closes on counter-spike only
and charges spread explicitly. This makes the backtest slightly conservative
relative to the live book — stated, not tuned away.
Median convention (P17 rule: every study states it): the backtest reference
here uses the FROZEN warmup median. Under the live rolling convention the
differences shrink (rolling removes false-minor noise in vol-regime windows),
so this scorer is conservative. Settle verdict (Sep 17, n=50): escalation NO
— recorded in Appendix P addendum, FLIP_INDEX_MECHANICS.md.

Usage: python analysis/flip_side_split.py
"""
import csv
import math
import os
import sys
from datetime import datetime

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_concepts import simulate            # noqa: E402
from flip_conditionality import load, WARMUP  # noqa: E402

SYMBOL = "flip1000"
LEDGER = os.path.join("data", "flip_exec", "slippage_ledger.csv")
VPU = 0.1          # $ per price-unit at 0.1 lot
SETTLE_N = 50      # pre-registered settle gate (Appendix P)
PREROLL_MS = 3600_000   # backtest warmup before the live window starts


def parse_ts(s):
    return datetime.fromisoformat(s).timestamp()


def load_trips():
    """Pair ledger rows into round trips (adjacent ENTRY->EXIT, both filled)."""
    with open(LEDGER) as f:
        rows = list(csv.DictReader(f))
    sig = [r for r in rows
           if not r["mode"].startswith("PROBE") and r.get("actual_deal")]
    trips = []
    i = 0
    while i < len(sig) - 1:
        e, x = sig[i], sig[i + 1]
        if e["mode"] == "ENTRY" and x["mode"] == "EXIT":
            d = 1 if e["side"] == "buy" else -1
            pnl = d * (float(x["actual_deal"]) - float(e["actual_deal"])) * VPU
            trips.append({
                "entry_ts": e["spike_ts_utc"],
                "exit_ts": x["spike_ts_utc"],
                "side": e["side"],
                "pnl": pnl,
                "epoch": parse_ts(e["spike_ts_utc"]),
                "telem": bool(e.get("entry_x")),
            })
            i += 2
        else:
            i += 1
    return trips


def binom_tail(k_wins, n, p=0.5):
    """P(X <= k_wins) for X ~ Binomial(n, p) — 'how unlucky is this WR'."""
    if n <= 0:
        return 1.0
    acc = 0.0
    for k in range(0, k_wins + 1):
        acc += math.comb(n, k) * (p ** k) * ((1 - p) ** (n - k))
    return min(1.0, acc)


def stats(trips):
    if not trips:
        return {"n": 0, "wr": float("nan"), "total": 0.0, "avg": float("nan")}
    n = len(trips)
    w = sum(1 for t in trips if t["pnl"] > 0)
    tot = sum(t["pnl"] for t in trips)
    return {"n": n, "wr": w / n, "total": tot, "avg": tot / n}


def show(label, s):
    if s["n"] == 0:
        print(f"  {label:26s} n=0")
        return
    print(f"  {label:26s} n={s['n']:3d}  WR={s['wr']:5.0%}  "
          f"total=${s['total']:+7.2f}  avg=${s['avg']:+.2f}")


def backtest_side(ms, px, med, mode, win=None):
    """Run the fleet engine; optionally restrict to the live window.

    mode: 'base' = both sides, 'L' = long-only, 'S' = short-only — mapped to
    the engine's `side` parameter (0 / 1 / -1; see flip_concepts.simulate).
    win = (epoch_start, epoch_end) in seconds; trips whose close_ms falls
    inside the window are returned (pre-roll keeps entries near the edge sane).
    """
    side_map = {"base": 0, "L": 1, "S": -1}
    trades, _ = simulate(ms, px, med, side=side_map[mode])
    if win is None:
        return [{"pnl": p} for p, _ in trades]
    t0, t1 = win
    return [{"pnl": p} for p, cms in trades
            if t0 <= cms / 1000.0 <= t1]


def main():
    live = load_trips()
    n_live = len(live)
    print(f"=== Appendix P side-split | live trips n={n_live} "
          f"(settle gate n>={SETTLE_N}) ===")
    if n_live == 0:
        print("no live trips parsed - nothing to compare")
        return

    print("\n--- LIVE (gross-of-spread book, counter-spike + SL closes) ---")
    show("ALL sides", stats(live))
    live_l = [t for t in live if t["side"] == "buy"]
    live_s = [t for t in live if t["side"] == "sell"]
    show("longs", stats(live_l))
    show("shorts", stats(live_s))

    print("\n--- BACKTEST (fleet engine, healed archive) ---")
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[WARMUP:WARMUP + 5000]))
    win = (live[0]["epoch"] - 0, parse_ts(live[-1]["exit_ts"]))
    for label, mode in (("full archive, both sides", "base"),
                        ("full archive, long-only", "L"),
                        ("full archive, short-only", "S")):
        show(label, stats(backtest_side(ms, px, med, mode)))
    print("  matched live window (first entry -> last exit):")
    show("  both sides", stats(backtest_side(ms, px, med, "base", win)))
    show("  long-only", stats(backtest_side(ms, px, med, "L", win)))
    show("  short-only", stats(backtest_side(ms, px, med, "S", win)))

    print("\n--- inversion sign test (live longs vs fair coin) ---")
    if live_l:
        k = sum(1 for t in live_l if t["pnl"] > 0)
        p = binom_tail(k, len(live_l))
        print(f"  longs: {k}/{len(live_l)} wins -> P(X<={k} | p=0.5) = {p:.3f}")
        print("  (small p = long weakness is unlikely to be luck)")
    if live_s:
        k = sum(1 for t in live_s if t["pnl"] > 0)
        p = binom_tail(k, len(live_s))
        print(f"  shorts: {k}/{len(live_s)} wins -> P(X>={k} | p=0.5) "
              f"= {1 - binom_tail(k - 1, len(live_s)):.3f}")

    print("\n--- settle ---")
    if n_live >= SETTLE_N:
        bl = stats(backtest_side(ms, px, med, "L", win))
        bs = stats(backtest_side(ms, px, med, "S", win))
        ll, ls = stats(live_l), stats(live_s)
        # Appendix P pre-registered escalation = BOTH prongs:
        #   (a) live longs underperform live shorts AND bt longs beat bt shorts
        #       (execution-defect signature: live worse than test, longs only)
        #   (b) the long weakness is significant (sign test p < 0.05)
        k_l = sum(1 for t in live_l if t["pnl"] > 0)
        p_l = binom_tail(k_l, len(live_l)) if live_l else 1.0
        inv = (ll["avg"] < ls["avg"]) and (bl["avg"] > bs["avg"])
        sig = p_l < 0.05
        escalate = inv and sig
        print(f"  SETTLE (n={n_live}): live long-avg ${ll['avg']:+.2f} vs "
              f"bt long-avg ${bl['avg']:+.2f}; live short-avg ${ls['avg']:+.2f} "
              f"vs bt short-avg ${bs['avg']:+.2f}")
        print(f"  inversion still present: {'YES' if inv else 'NO'} "
              f"(live long sign-test p={p_l:.3f}, "
              f"{'significant' if sig else 'not significant'})")
        print(f"  Appendix P escalation (defect, not regime): "
              f"{'YES - re-examine long-only branch before Oct 8' if escalate else 'NO'}")
    else:
        need = SETTLE_N - n_live
        print(f"  INTERIM (n={n_live} < {SETTLE_N}): "
              f"~{need / 13.0:.0f}-{need / 6.5:.0f} more day(s) at recent "
              f"cadence (~6.5-13 trips/day) - checkpoint will re-capture nightly")

    # cache for the checkpoint capture and follow-up tooling
    import json
    with open(os.path.join("archive", "_side_split_live.json"), "w") as f:
        json.dump(live, f)


if __name__ == "__main__":
    main()
