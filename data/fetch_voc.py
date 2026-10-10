# -*- coding: utf-8 -*-
"""
data/fetch_voc.py — Fetch the "Vol over" family (Crash/Boom 400/550/750)
from MT5: 60d M1 candles + ticks (with bid/ask for spread measurement) +
broker stop floors. These were catalog ghosts before; re-probe now.
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

SYMBOLS = ["Vol over Crash 400", "Vol over Crash 550", "Vol over Crash 750",
           "Vol over Boom 400", "Vol over Boom 550", "Vol over Boom 750"]


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
    """Return bid/ask arrays so the REAL spread can be measured."""
    end = datetime.now()
    start = end - timedelta(days=days)
    bids, asks, cur = [], [], start
    while cur < end:
        nxt = min(cur + timedelta(days=3), end)
        ticks = mt5.copy_ticks_range(mt5_name, cur, nxt, mt5.COPY_TICKS_ALL)
        if ticks is not None and len(ticks):
            bids.append(np.array([t["bid"] for t in ticks], dtype=float))
            asks.append(np.array([t["ask"] for t in ticks], dtype=float))
        cur = nxt
    if not bids:
        return None, None
    return np.concatenate(bids), np.concatenate(asks)


def main():
    if not connect():
        return
    try:
        out_dir = Path(__file__).parent
        for name in SYMBOLS:
            ok = mt5.symbol_select(name, True)
            info = mt5.symbol_info(name)
            if info is None:
                print(f"\n  NOT LOADABLE: {name} select={ok} {mt5.last_error()}")
                continue
            slug = name.replace(" ", "_").replace("-", "")
            cpath = out_dir / f"candles_{slug}_1min.parquet"
            tpath = out_dir / f"ticks_{slug}.npy"
            tpath_a = out_dir / f"ticks_{slug}_ask.npy"
            print(f"\n  {name}: digits={info.digits} "
                  f"stops={info.trade_stops_level*info.point:.4f} "
                  f"freeze={info.trade_freeze_level*info.point:.4f} "
                  f"spread_pts={info.spread} vol_min={info.volume_min}")
            df = fetch_candles(name)
            if df is not None:
                df.to_parquet(cpath, index=False)
                span = (df["time"].max() - df["time"].min()).total_seconds() / 86400
                rng = (df["high"] - df["low"])
                print(f"    candles {len(df):,} rows / {span:.1f}d, "
                      f"price {df['close'].min():.2f}-{df['close'].max():.2f}, "
                      f"median bar range {rng.median():.3f}")
            else:
                print("    no candles")
            bids, asks = fetch_ticks(name)
            if bids is not None and len(bids) > 1000:
                np.save(tpath, bids)
                np.save(tpath_a, asks)
                spr = asks - bids
                print(f"    ticks {len(bids):,}, median spread {np.median(spr):.4f} "
                      f"({np.median(spr)*100:.1f} pts), p90 {np.percentile(spr,90):.4f}, "
                      f"max {spr.max():.4f}")
            else:
                print(f"    no ticks ({0 if bids is None else len(bids)})")
    finally:
        mt5.shutdown()
        print("  MT5 disconnected")


if __name__ == "__main__":
    main()