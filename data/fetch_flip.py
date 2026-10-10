# -*- coding: utf-8 -*-
"""
data/fetch_flip.py — Fetch the Crash Boom Flip family (150/300/500/1000) from
MT5: 60d M1 candles + ticks + broker stop floors. The Flip family is the
closest OBTAINABLE proxy for the unavailable "vol over" products.
"""
import sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')
try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 not installed.")
    sys.exit(1)

MT5_LOGIN = 32341786
MT5_PASSWORD = "@Mikolo123"
MT5_SERVER = "Deriv-Demo"

SYMBOLS = ["Crash Boom Flip 150 Index", "Crash Boom Flip 300 Index",
           "Crash Boom Flip 500 Index", "Crash Boom Flip 1000 Index"]


def connect():
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD,
                          server=MT5_SERVER, timeout=15000):
        print(f"  FAILED: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    print(f"  Connected: {info.login} @ {info.server}")
    return True


def fetch_candles(mt5_name, days=60):
    import pandas as pd
    end = datetime.now()
    start = end - timedelta(days=days)
    chunks, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        rates = mt5.copy_rates_range(mt5_name, mt5.TIMEFRAME_M1, cur, nxt)
        if rates is not None and len(rates):
            df = pd.DataFrame(rates.tolist(), columns=rates.dtype.names)
            chunks.append(df)
        cur = nxt
    if not chunks:
        return None
    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset=["time"]).sort_values("time").reset_index(drop=True)
    df = df.rename(columns={"time": "epoch", "tick_volume": "volume"})
    keep = ["epoch", "open", "high", "low", "close"]
    for c in ("volume", "spread"):
        if c in df.columns:
            keep.append(c)
    df = df[[c for c in keep if c in df.columns]]
    df["time"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    return df


def fetch_ticks(mt5_name, days=7):
    end = datetime.now()
    start = end - timedelta(days=days)
    all_prices, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=3), end)
        ticks = mt5.copy_ticks_range(mt5_name, cur, nxt, mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks):
            all_prices.append(np.array([t["bid"] for t in ticks], dtype=float))
        cur = nxt
    if not all_prices:
        return None
    return np.concatenate(all_prices)


def main():
    if not connect():
        return
    try:
        out_dir = Path(__file__).parent
        for name in SYMBOLS:
            mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
            if info is None:
                print(f"\n  NOT LOADABLE: {name} {mt5.last_error()}")
                continue
            slug = name.replace(" ", "_").replace("-", "")
            cpath = out_dir / f"candles_{slug}_1min.parquet"
            tpath = out_dir / f"ticks_{slug}.npy"
            print(f"\n  {name}: digits={info.digits} "
                  f"stops={info.trade_stops_level*info.point:.4f} spread={info.spread}")
            df = fetch_candles(name)
            if df is not None:
                df.to_parquet(cpath, index=False)
                span = (df["time"].max() - df["time"].min()).total_seconds() / 86400
                print(f"    candles {len(df):,} rows / {span:.1f}d, "
                      f"price {df['close'].min():.2f}-{df['close'].max():.2f}")
            px = fetch_ticks(name)
            if px is not None and len(px) > 1000:
                np.save(tpath, px)
                print(f"    ticks {len(px):,}")
    finally:
        mt5.shutdown()
        print("  MT5 disconnected")


if __name__ == "__main__":
    main()