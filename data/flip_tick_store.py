#!/usr/bin/env python3
"""Flip index tick store: daily incremental collection.

For each Flip symbol, fetches the trailing 72 h of ticks from MT5 and
merges them into an append-only per-day npy archive under data/flip_ticks/
(YYYY-MM-DD.npy, columns [time_msc, bid]). The 72 h window overlaps the
previous run, so gaps from terminal downtime are healed and partial days
are completed on the next run — the store is idempotent.

Also provides load(sym, days) used by analysis scripts to read the whole
accumulated archive as one array.

Usage:
  python data/flip_tick_store.py            # daily update (scheduled job)
  python data/flip_tick_store.py --status   # show coverage
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# NOTE: MetaTrader5 and the credential connector are imported lazily inside
# mt5_init() so analysis scripts can load the archive without MT5.

SYMBOLS = {
    "flip150": "Crash Boom Flip 150 Index",
    "flip300": "Crash Boom Flip 300 Index",
    "flip500": "Crash Boom Flip 500 Index",
    "flip1000": "Crash Boom Flip 1000 Index",
}
STORE_DIR = "data/flip_ticks"
WINDOW_H = 72          # rebuild window; > 24 h so downtime gaps self-heal
DAY_MS = 86_400_000


def mt5_init():
    import MetaTrader5 as mt5
    from data.mt5_tick_connector import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH
    kwargs = {"timeout": 10000}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        kwargs.update({"login": MT5_LOGIN, "password": MT5_PASSWORD, "server": MT5_SERVER})
    if not mt5.initialize(**kwargs):
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    return mt5


def update_symbol(mt5, key, name, now_utc):
    start = now_utc - timedelta(hours=WINDOW_H)
    ticks = mt5.copy_ticks_range(name, start, now_utc, mt5.COPY_TICKS_ALL)
    if ticks is None or len(ticks) == 0:
        return f"{key}: NO TICKS ({mt5.last_error()})"
    arr = np.column_stack([
        ticks["time_msc"].astype(np.int64),
        ticks["bid"].astype(np.float64),
    ])
    arr = arr[arr[:, 1] > 0]
    if len(arr) == 0:
        return f"{key}: no valid bids"

    day_start = int(datetime(now_utc.year, now_utc.month, now_utc.day,
                             tzinfo=timezone.utc).timestamp() * 1000)
    day_idx = (arr[:, 0] - day_start) // DAY_MS
    written = []
    for off in np.unique(day_idx):
        d0 = day_start + int(off) * DAY_MS
        seg = arr[day_idx == off]
        path = f"{STORE_DIR}/{key}_{datetime.fromtimestamp(d0 / 1000, tz=timezone.utc):%Y-%m-%d}.npy"
        if os.path.exists(path):
            old = np.load(path)
            merged = np.concatenate((old, seg))
            # dedupe on FULL rows (ms, bid): same-ms different-bid ticks are
            # real and kept; identical rows collapse. Keep first occurrence,
            # preserve time order.
            _, idx = np.unique(merged, axis=0, return_index=True)
            merged = merged[np.sort(idx)]
            _, old_idx = np.unique(old, axis=0, return_index=True)
            n_new = len(merged) - len(old_idx)
            if n_new > 0:
                np.save(path, merged)
            written.append(f"{n_new:,} new")
        else:
            np.save(path, seg)
            written.append(f"{len(seg):,} init")
    return f"{key}: {len(arr):,} ticks in window; " + ", ".join(written)


def status():
    import glob
    import os
    print(f"{'symbol':9s} {'days':>4s} {'ticks':>10s}  first -> last (UTC)")
    for key in SYMBOLS:
        files = sorted(glob.glob(f"{STORE_DIR}/{key}_*.npy"))
        if not files:
            print(f"{key:9s} {'-':>4s} {'-':>10s}")
            continue
        n = sum(len(np.load(f)) for f in files)
        t0 = np.load(files[0])[0, 0]
        t1 = np.load(files[-1])[-1, 0]
        f0 = datetime.fromtimestamp(t0 / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        f1 = datetime.fromtimestamp(t1 / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")
        print(f"{key:9s} {len(files):>4d} {n:>10,}  {f0} -> {f1}")


def load(sym, days=None):
    """Load the whole accumulated archive for a symbol as (ms int64, bid f8).
    days=None -> everything; days=N -> trailing N day-files."""
    import glob
    import os
    files = sorted(glob.glob(f"{STORE_DIR}/{sym}_*.npy"))
    if not files:
        raise FileNotFoundError(f"no stored ticks for {sym}")
    if days:
        files = files[-days:]
    parts = [np.load(f) for f in files]
    arr = np.concatenate(parts)
    arr = arr[np.argsort(arr[:, 0], kind="stable")]
    return arr[:, 0], arr[:, 1]


def seed_from_legacy():
    """One-time: convert the original 7-day data/ticks_{sym}.npy fetches into
    per-day store files so the archive starts with full history."""
    now = datetime.now(timezone.utc)
    day_start = int(datetime(now.year, now.month, now.day, tzinfo=timezone.utc).timestamp() * 1000)
    for key in SYMBOLS:
        path = f"data/ticks_{key}.npy"
        if not os.path.exists(path):
            print(f"{key}: no legacy file, skipped")
            continue
        arr = np.load(path)
        arr = arr[arr[:, 1] > 0]
        day_idx = (arr[:, 0] - day_start) // DAY_MS
        n = 0
        for off in np.unique(day_idx):
            d0 = day_start + int(off) * DAY_MS
            seg = arr[day_idx == off].astype(np.float64)
            seg[:, 0] = seg[:, 0].astype(np.int64)
            f = f"{STORE_DIR}/{key}_{datetime.fromtimestamp(d0 / 1000, tz=timezone.utc):%Y-%m-%d}.npy"
            if os.path.exists(f):
                continue
            np.save(f, seg)
            n += 1
        print(f"{key}: seeded {n} day files")


if __name__ == "__main__":
    if "--status" in sys.argv:
        status()
        sys.exit(0)
    if "--seed" in sys.argv:
        os.makedirs(STORE_DIR, exist_ok=True)
        seed_from_legacy()
        sys.exit(0)
    os.makedirs(STORE_DIR, exist_ok=True)
    now = datetime.now(timezone.utc)
    mt5 = mt5_init()
    try:
        for key, name in SYMBOLS.items():
            print(update_symbol(mt5, key, name, now))
    finally:
        mt5.shutdown()
