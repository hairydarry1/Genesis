#!/usr/bin/env python3
"""Score live entry telemetry against the two pre-registered observations.

Pre-registered flags (docs/FLIP_INDEX_MECHANICS.md, Appendices O/S):
  P1  SESSION POCKET : long-only entries 12-16Z carried +$21 of +$38 net,
      WR 74%, spread across days (n=19, flagged observation-only).
  P2  2H-MOMENTUM    : entries with M5 close(n) > close(n-24) (t24=+1)
      averaged +$0.60 vs +$0.18 for t24=-1 (1 of 6 comparisons, fragile).

The executor stamps entry_x/act_2h/trend_2h/min_2k on every ledger row since
2026-09-15 18:49 UTC (telemetry; min_2k since 2026-09-16). This script pairs
ENTRY/EXIT rows into trips and scores P1/P2/P3 on the live book.

Honesty rails:
  - n-gate: each bucket needs >= 8 trips before any claim.
  - t24 = 0 is "dark" (flat market OR session warmup before 25 M5 bars)
    and is reported separately, never pooled with +/-1.
  - day-concentration table: a pocket confirmed by one day is noise.  - act_2h undercounts during the first 2h of any session (spike_hist
    warmup) - reported as a caveat, affects only early-session trips.
  - min_2k (P3, quiet/burst) exists only on rows stamped since 2026-09-16;
    older trips are excluded from P3 (not pooled as "dark").

Usage: python analysis/flip_telemetry_score.py
"""
import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# VPU: $ per price unit at the book's 0.1 lot = 0.10. FIX (2026-09-16): this
# used to import VPU from flip_entry_filters, which is the per-1.0-LOT spec
# value ($1.0/unit) - every PnL this script printed before was 10x too big.
# Caught when a real -$2.88 trip printed as -$28.76. No decision was ever
# based on the wrong numbers (all prior runs were below the n-gate).
VPU = 0.10

LEDGER = os.path.join("data", "flip_exec", "slippage_ledger.csv")
MIN_BUCKET_N = 8          # n-gate per bucket
MISSION_N = 20            # the "score it" threshold from the plan


def load_trips():
    """Pair ledger rows into telemetry-stamped round trips."""
    try:
        with open(LEDGER) as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    trips = []
    i = 0
    while i < len(rows) - 1:
        e, x = rows[i], rows[i + 1]
        if (e["mode"] == "ENTRY" and x["mode"] == "EXIT"
                and e.get("entry_x") and x.get("entry_x")
                and e.get("actual_deal") and x.get("actual_deal")):
            d = 1 if e["side"] == "buy" else -1
            pnl = d * (float(x["actual_deal"]) - float(e["actual_deal"])) * VPU
            ts = e["spike_ts_utc"]
            dt = datetime.fromisoformat(ts)
            trips.append({
                "ts": ts, "day": dt.strftime("%Y-%m-%d"),
                "hour": dt.hour, "side": e["side"], "pnl": pnl,
                "x": float(e["entry_x"]), "act": int(e["act_2h"]),
                "t24": int(e["trend_2h"]),
                "mn": int(e["min_2k"]) if e.get("min_2k") else None,
            })
            i += 2
        else:
            i += 1
    return trips


def bucket_stats(trips):
    if not trips:
        return None
    n = len(trips)
    wins = sum(1 for t in trips if t["pnl"] > 0)
    return {"n": n, "wr": wins / n, "total": sum(t["pnl"] for t in trips),
            "avg": sum(t["pnl"] for t in trips) / n}


def line(label, s):
    if s is None:
        print(f"  {label:28s}  n=0")
        return
    print(f"  {label:28s}  n={s['n']:3d}  WR={s['wr']:5.0%}  "
          f"net=${s['total']:+7.2f}  avg=${s['avg']:+.2f}")


def gate(label, s, ref=None):
    if s is None or s["n"] < MIN_BUCKET_N:
        print(f"  -> {label}: INSUFFICIENT n (need >={MIN_BUCKET_N} per bucket)")
        return
    if ref is None:
        print(f"  -> {label}: measurable, see table")
        return
    d = s["avg"] - ref["avg"]
    print(f"  -> {label}: avg diff ${d:+.2f}/trip "
          f"({'CONFIRMS' if d > 0.05 else 'CONTRADICTS' if d < -0.05 else 'NOISE vs'}) "
          f"the pre-registered direction")


def day_concentration(trips, keyfn, label):
    """Per-day contribution to the bucket-vs-rest difference."""
    per_day = defaultdict(lambda: [0, 0.0, 0])   # day -> [n_b, pnl_b, n_rest]
    for t in trips:
        b = keyfn(t)
        per_day[t["day"]][0 if b else 2] += 1
        if b:
            per_day[t["day"]][1] += t["pnl"]
    print(f"  day concentration ({label}):")
    for day in sorted(per_day):
        n_b, pnl_b, n_rest = per_day[day]
        print(f"    {day}  bucket n={n_b:2d} pnl=${pnl_b:+6.2f}   rest n={n_rest}")


def main():
    trips = load_trips()
    print(f"=== telemetry scoring | stamped trips n={len(trips)} "
          f"(mission gate: {MISSION_N}) ===")
    if not trips:
        print("no stamped trips yet - accumulator live since 2026-09-15 18:49Z.")
        print("rerun around Sep 20-22 when n>=20 (entries need a 2-spike phase gate).")
        return

    for t in trips:
        mn = f" mn={t['mn']:3d}" if t["mn"] is not None else ""
        print(f"  {t['ts'][:19]} {t['side']:4s} pnl=${t['pnl']:+.2f} "
              f"x={t['x']:.2f} act={t['act']} t24={t['t24']:+d}{mn}")

    overall = bucket_stats(trips)
    line("ALL stamped trips", overall)

    print("\n--- P1: session pocket (12-16Z, long-only per deployment plan) ---")
    longs = [t for t in trips if t["side"] == "buy"]
    pocket = [t for t in longs if 12 <= t["hour"] < 16]
    rest = [t for t in longs if not (12 <= t["hour"] < 16)]
    line("long 12-16Z", bucket_stats(pocket))
    line("long other", bucket_stats(rest))
    gate("session pocket", bucket_stats(pocket),
         bucket_stats(rest) if rest else None)
    if len(trips) >= MISSION_N:
        day_concentration(longs, lambda t: 12 <= t["hour"] < 16, "12-16Z longs")

    print("\n--- P2: 2h momentum (t24 +1 vs -1) ---")
    up = [t for t in trips if t["t24"] == 1]
    dn = [t for t in trips if t["t24"] == -1]
    dark = [t for t in trips if t["t24"] == 0]
    line("t24=+1 (up trend)", bucket_stats(up))
    line("t24=-1 (down trend)", bucket_stats(dn))
    line("t24=0 (dark/warmup)", bucket_stats(dark))
    gate("2h momentum", bucket_stats(up), bucket_stats(dn) if dn else None)
    if len(trips) >= MISSION_N:
        day_concentration(trips, lambda t: t["t24"] == 1, "t24=+1")

    # P3: quiet/burst (Appendix Y variable, stamped since 2026-09-16 only)
    stamped = [t for t in trips if t["mn"] is not None]
    if not stamped:
        print("\n--- P3: quiet/burst (min_2k) ---")
        print("  no min_2k-stamped trips yet (accumulator live since 2026-09-16)")
    else:
        print(f"\n--- P3: quiet/burst (min_2k; stamped n={len(stamped)}) ---")
        quiet = [t for t in stamped if t["mn"] <= 2]
        burst = [t for t in stamped if t["mn"] > 2]
        line("quiet (min_2k <= 2)", bucket_stats(quiet))
        line("burst (min_2k > 2)", bucket_stats(burst))
        gate("quiet-entry", bucket_stats(quiet),
             bucket_stats(burst) if burst else None)
        if len(stamped) >= MISSION_N:
            day_concentration(stamped, lambda t: t["mn"] <= 2, "quiet")
        mns = sorted(t["mn"] for t in stamped)
        print(f"  min_2k: min={mns[0]} med={mns[len(mns)//2]} max={mns[-1]}")
        print("  (pre-registered split: quiet <= 2, burst > 2; the study found")
        print("   burst entries -$0.49/trip vs quiet +$0.28 on the archive)")

    print("\n--- telemetry health (live vs backtest conventions) ---")
    xs = sorted(t["x"] for t in trips)
    print(f"  entry_x: min={xs[0]:.2f} med={xs[len(xs)//2]:.2f} max={xs[-1]:.2f} "
          f"(backtest min possible = 5.0)")
    acts = defaultdict(int)
    for t in trips:
        acts[t["act"]] += 1
    print(f"  act_2h histogram: {dict(sorted(acts.items()))}")
    dark_n = sum(1 for t in trips if t["t24"] == 0)
    print(f"  t24=0 share: {dark_n}/{len(trips)} "
          f"(warmup makes this high in early days; expect it to shrink)")

    # cache for follow-up tooling
    with open(os.path.join("archive", "_tel_trips.json"), "w") as f:
        json.dump(trips, f)


if __name__ == "__main__":
    main()
