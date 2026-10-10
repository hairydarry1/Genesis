#!/usr/bin/env python3
"""Fetch tick history for the Crash Boom Flip indices (read-only).

Saves data/ticks_flip{N}.npy with columns [time_msc, bid] for each of
Flip 150/300/500/1000. Uses CopyTicksRange against the connected Deriv
terminal; does NOT write to the paper tracker (no executor imports).
"""
import sys
import numpy as np
import MetaTrader5 as mt5
from datetime import datetime, timezone

sys.path.insert(0, ".")
from data.mt5_tick_connector import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH

SYMBOLS = {
    "flip150": "Crash Boom Flip 150 Index",
    "flip300": "Crash Boom Flip 300 Index",
    "flip500": "Crash Boom Flip 500 Index",
    "flip1000": "Crash Boom Flip 1000 Index",
}

DAYS_BACK = 7


def main():
    kwargs = {"timeout": 10000}
    if MT5_PATH:
        kwargs["path"] = MT5_PATH
    if MT5_LOGIN:
        kwargs.update({"login": MT5_LOGIN, "password": MT5_PASSWORD, "server": MT5_SERVER})
    if not mt5.initialize(**kwargs):
        print(f"MT5 init failed: {mt5.last_error()}")
        sys.exit(1)
    try:
        end = datetime.now(timezone.utc)
        start = datetime.fromtimestamp(end.timestamp() - DAYS_BACK * 86400, tz=timezone.utc)
        for key, name in SYMBOLS.items():
            info = mt5.symbol_info(name)
            if info is None:
                print(f"{key}: symbol not found")
                continue
            if not info.visible:
                mt5.symbol_select(name, True)
            if info.description:
                print(f"{key}: {info.description}")
            ticks = mt5.copy_ticks_range(name, start, end, mt5.COPY_TICKS_ALL)
            if ticks is None or len(ticks) == 0:
                print(f"  {key}: NO TICKS returned ({mt5.last_error()})")
                continue
            arr = np.column_stack([
                ticks["time_msc"].astype(np.float64),
                ticks["bid"].astype(np.float64),
            ])
            # drop zero-bid rows (no-quote gaps)
            arr = arr[arr[:, 1] > 0]
            np.save(f"data/ticks_{key}.npy", arr)
            t0 = datetime.fromtimestamp(arr[0, 0] / 1000, tz=timezone.utc)
            t1 = datetime.fromtimestamp(arr[-1, 0] / 1000, tz=timezone.utc)
            print(f"  {key}: {len(arr):,} ticks  {t0:%m-%d %H:%M} -> {t1:%m-%d %H:%M} UTC")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
