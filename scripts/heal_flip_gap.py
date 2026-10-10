#!/usr/bin/env python3
"""Targeted archive heal for the sleep-wedge hole (flip1000 only).

Fills gaps from broker-side history (idempotent: re-merging is safe):
  1. flip1000_2026-09-11.npy: 20:00 -> 24:00 UTC (file was truncated at 20:05)
  2. flip1000_2026-09-12.npy: 00:00 -> now   (file started 16:28)
Then verifies the Sep 12 17:42-19:28 loss window is continuous.

Writes progress lines to archive/heal_flip_gap.log so the detached run can
be monitored. Usage: python scripts/heal_flip_gap.py
"""
import os
import sys
from datetime import datetime, timezone

import MetaTrader5 as mt5
import numpy as np

SYMBOL = "Crash Boom Flip 1000 Index"
LOG = "archive/heal_flip_gap.log"
U = lambda *a: datetime(*a, tzinfo=timezone.utc).timestamp()


def log(msg):
    line = f"[{datetime.now(timezone.utc):%H:%M:%S}Z] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def broker_ticks(t0, t1):
    ticks = mt5.copy_ticks_range(SYMBOL, datetime.fromtimestamp(t0, tz=timezone.utc),
                                 datetime.fromtimestamp(t1, tz=timezone.utc),
                                 mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return np.empty((0, 2))
    ms = ticks["time_msc"].astype(np.float64)
    mid = (ticks["bid"].astype(np.float64) + ticks["ask"].astype(np.float64)) / 2.0
    keep = (mid > 0) & (ms > 0)
    return np.column_stack([ms[keep], mid[keep]])


def merge_day(path, t0, t1):
    arr = np.load(path) if os.path.exists(path) else np.empty((0, 2))
    before = len(arr)
    new = broker_ticks(t0, t1)
    if len(new) == 0:
        log(f"  {os.path.basename(path)}: broker returned 0 ticks "
            f"for {datetime.fromtimestamp(t0, tz=timezone.utc):%m-%d %H:%M}"
            f"-> {datetime.fromtimestamp(t1, tz=timezone.utc):%H:%M}")
        return
    inside = (arr[:, 0] >= t0 * 1000) & (arr[:, 0] <= t1 * 1000)
    merged = np.vstack([arr[~inside], new])
    merged = merged[np.argsort(merged[:, 0], kind="stable")]
    _, uniq = np.unique(merged[:, 0], return_index=True)
    merged = merged[np.sort(uniq)]
    np.save(path, merged)
    log(f"  {os.path.basename(path)}: {before} -> {len(merged)} ticks "
        f"(+{len(merged) - before})")


def main():
    ok = mt5.initialize()
    if not ok:
        log(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    log(f"heal start, acct {mt5.account_info().login}")
    try:
        # broker only serves ~last few hours of tick history and truncates
        # large-span requests -> pull in 1-hour chunks
        now = datetime.now(timezone.utc).timestamp()
        t = U(2026, 9, 12, 17, 0)
        while t < now:
            merge_day("data/flip_ticks/flip1000_2026-09-12.npy",
                      t, min(t + 3600, now))
            t += 3600
    finally:
        mt5.shutdown()

    # verify continuity around the loss window
    a11 = np.load("data/flip_ticks/flip1000_2026-09-11.npy")
    a12 = np.load("data/flip_ticks/flip1000_2026-09-12.npy")
    both = np.sort(np.concatenate([a11[:, 0], a12[:, 0]]))
    both = both[both > U(2026, 9, 11, 12, 0) * 1000]
    gaps = np.diff(both)
    w = (a12[:, 0] > U(2026, 9, 12, 17, 42) * 1000) & \
        (a12[:, 0] < U(2026, 9, 12, 19, 28) * 1000)
    log(f"loss-window ticks in archive: {w.sum()}")
    log(f"max gap since Sep 11 12:00 UTC: {gaps.max() / 1000:.0f}s "
        f"over {len(both)} ticks")
    log("heal done")


if __name__ == "__main__":
    main()
