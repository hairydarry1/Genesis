#!/usr/bin/env python3
"""Flip tick-archive integrity audit (docs/FLIP_PLAN.md gate).

Deriv synthetics stream 24/7 with no session breaks, so ANY internal gap
beyond a few minutes is data loss (terminal offline > the store's 72h
heal window, broker history trim, or collector failure). This script
quantifies exactly that, per day-file and across day boundaries:

  1. per-symbol: total span, tick count, median inter-tick, coverage %
     (1 - gap time / span) using gaps > 60 s as lost
  2. gap census: buckets (1-5 min, 5-30 min, 30-120 min, >2 h) +
     the 10 largest gaps with UTC timestamps
  3. Oct 8 readiness: usable days now, projected by Oct 8 (min 28),
     verdict PASS / WARN / FAIL for the confirmation-window data quality

Read-only over data/flip_ticks/*.npy (no MT5 needed).
"""
import glob
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.flip_tick_store import SYMBOLS, STORE_DIR  # noqa: E402

DAY_MS = 86_400_000
GAP_LOST_S = 60          # > this = lost data
OCT8 = datetime(2026, 10, 8, tzinfo=timezone.utc)
MIN_WINDOW_DAYS = 28     # confirmation gate needs 4+ weeks
TARGET_RATE_MS = 200     # nominal inter-tick for lost-tick extrapolation

BUCKETS = [(60, 300, "1-5min"), (300, 1800, "5-30min"),
           (1800, 7200, "30-120min"), (7200, 10**12, ">2h")]


def fmt_ts(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def audit_symbol(key):
    files = sorted(glob.glob(f"{STORE_DIR}/{key}_*.npy"))
    if not files:
        return None
    parts = [np.load(f) for f in files]
    arr = np.concatenate(parts)
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    ms = arr[:, 0].astype(np.int64)

    span_ms = int(ms[-1] - ms[0])
    n = len(ms)
    d = np.diff(ms)
    gaps = d[d > GAP_LOST_S * 1000]
    gap_ms = int(gaps.sum())
    coverage = 100.0 * (1.0 - gap_ms / span_ms) if span_ms > 0 else 0.0
    lost_ticks = int(gap_ms / TARGET_RATE_MS)

    print(f"\n=== {key} ===")
    print(f"  day-files: {len(files)}  ticks: {n:,}  "
          f"span: {fmt_ts(ms[0])} -> {fmt_ts(ms[-1])} ({span_ms / DAY_MS:.1f} d)")
    med = np.median(d[d > 0])
    print(f"  median inter-tick: {med:.0f} ms   coverage: {coverage:.2f}%   "
          f"lost: {len(gaps):,} gaps ~{lost_ticks:,} ticks")

    if len(gaps):
        print("  gap census:")
        for lo, hi, name in BUCKETS:
            c = int(((gaps > lo * 1000) & (gaps <= hi * 1000)).sum())
            if c:
                print(f"    {name:>9s}: {c}")
        print("  10 largest gaps (UTC):")
        big_idx = np.argsort(d)[-10:][::-1]
        for i in big_idx:
            if d[i] > GAP_LOST_S * 1000:
                print(f"    {fmt_ts(ms[i])} -> {fmt_ts(ms[i + 1])}  "
                      f"({d[i] / 60000:.0f} min)")
    else:
        print("  gap census: none >60s — archive is continuous")

    # per-day-file sanity: any day-file with absurdly few ticks flags quietly
    bad_days = []
    for f, p in zip(files, parts):
        dn = os.path.basename(f).split("_")[1].replace(".npy", "")
        if len(p) < 20_000:  # a full 24h at even 10/s would be 864k; relaxed floor
            bad_days.append((dn, len(p)))
    if bad_days:
        print("  thin day-files (<20k ticks): " +
              ", ".join(f"{d}({n:,})" for d, n in bad_days))

    return {"days": len(files), "span_days": span_ms / DAY_MS,
            "coverage": coverage, "ticks": n}


def main():
    print(f"Flip archive integrity audit — {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    print(f"gate: {MIN_WINDOW_DAYS}+ days usable by {OCT8:%b %d} for the long-only confirmation")
    results = {}
    for key in SYMBOLS:
        results[key] = audit_symbol(key)

    print("\n=== Oct 8 readiness ===")
    now = datetime.now(timezone.utc)
    days_to_oct8 = (OCT8 - now).days
    ok = True
    for key, r in results.items():
        if r is None:
            print(f"  {key}: NO ARCHIVE")
            ok = False
            continue
        # only flip1000 gates the confirmation; others informational
        projected = r["days"] + days_to_oct8
        line = (f"  {key:9s}: {r['days']:2d} day-files now -> ~{projected} by Oct 8  "
                f"coverage {r['coverage']:.2f}%")
        if key == "flip1000":
            if projected >= MIN_WINDOW_DAYS and r["coverage"] >= 97.0:
                line += "  PASS"
            elif projected >= MIN_WINDOW_DAYS:
                line += "  WARN (coverage <97% — read gap census)"
                ok = None  # warn, not fail
            else:
                line += "  FAIL (<28 days)"
                ok = False
        print(line)

    print("\nverdict: " + ("PASS — archive quality supports the Oct 8 gate"
                           if ok is True else
                           "WARN — usable but read the gap census" if ok is None else
                           "FAIL — do not trust the Oct 8 read until gaps are explained"))


if __name__ == "__main__":
    main()
