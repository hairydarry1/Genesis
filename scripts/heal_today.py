#!/usr/bin/env python3
"""Rescue the current UTC day's flip ticks into the archive.

Motivation: after any sleep wedge the once-daily 01:05 store run can leave the
current day nearly empty (Sep 14 had 5 minutes of ticks), and broker tick
history depth is only a few hours - every hour of delay makes part of the day
permanently unhealable. This pulls 00:00 -> now in 1h chunks (small spans are
served reliably; large spans get silently truncated) and merges into the
append-only npy archive. Idempotent: duplicate epochs are deduped.

Usage:
  python scripts/heal_today.py          # all four flip symbols
  python scripts/heal_today.py flip1000 # one symbol
"""
import sys
import os
from datetime import datetime, timezone, timedelta

import numpy as np
import MetaTrader5 as mt5

ARCHIVE = "data/flip_ticks"
SYMBOLS = {
    "flip1000": "Crash Boom Flip 1000 Index",
    "flip500": "Crash Boom Flip 500 Index",
    "flip300": "Crash Boom Flip 300 Index",
    "flip150": "Crash Boom Flip 150 Index",
}


def heal(key, symbol):
    now = datetime.now(timezone.utc)
    day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    path = f"{ARCHIVE}/{key}_{now:%Y-%m-%d}.npy"

    prev_len = 0
    have_ms = int(day0.timestamp() * 1000)
    if os.path.exists(path):
        old = np.load(path)
        if len(old):
            have_ms = int(old[-1][0])
        prev_len = len(old)
        print(f"[{key}] existing: {len(old)} ticks to "
              f"{datetime.fromtimestamp(have_ms/1000, timezone.utc):%H:%M:%S}Z")

    cursor = max(int(day0.timestamp() * 1000), have_ms)
    end_ms = int(now.timestamp() * 1000)
    chunks = []
    while cursor < end_ms:
        t0 = datetime.fromtimestamp(cursor / 1000, timezone.utc)
        t1 = min(t0 + timedelta(hours=1), now)
        r = mt5.copy_ticks_range(symbol, t0, t1, mt5.COPY_TICKS_ALL)
        n = 0 if r is None else len(r)
        chunks.append(f"{t0:%H:%M}->{t1:%H:%M}:{n}")
        if n:
            ms = (r["time_msc"] if "time_msc" in (r.dtype.names or ())
                  else r["time"] * 1000).astype(np.float64)
            mid = (r["bid"] + r["ask"]) / 2.0
            rows = np.column_stack([ms, mid])
            merged = rows
            if os.path.exists(path):
                base = np.load(path)
                if len(base):
                    merged = np.concatenate([base, rows], axis=0)
                    merged = merged[np.argsort(merged[:, 0], kind="stable")]
                    merged = merged[np.concatenate([[True], np.diff(merged[:, 0]) > 0])]
            np.save(path, merged)
            prev_len = len(merged)
        cursor = int(t1.timestamp() * 1000)

    if os.path.exists(path):
        fin = np.load(path)
        print(f"[{key}] saved {now:%Y-%m-%d}: {len(fin)} ticks "
              f"({datetime.fromtimestamp(float(fin[0][0])/1000, timezone.utc):%H:%M:%SZ}"
              f"->{datetime.fromtimestamp(float(fin[-1][0])/1000, timezone.utc):%H:%M:%SZ}), "
              f"+{len(fin) - 0 if prev_len == 0 else len(fin)} total")
    print(f"[{key}] chunks: {' '.join(chunks) if chunks else 'none (already current)'}")


def main():
    want = sys.argv[1] if len(sys.argv) > 1 else "ALL"
    keys = list(SYMBOLS) if want == "ALL" else [want]
    mt5.initialize()
    for k in keys:
        try:
            heal(k, SYMBOLS[k])
        except Exception as e:
            print(f"[{k}] FAILED: {e!r}")
    mt5.shutdown()


if __name__ == "__main__":
    main()
