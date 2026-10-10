# -*- coding: utf-8 -*-
"""
data/fetch_vol400.py — Fetch Deriv's over-400 vol products from MT5.

There is no "Volatility 400 Index" on this broker — the classic series tops
at Volatility 250. The >400%-vol products are the NEW directional ones:
    Spot Up - Volatility Down Index   (downward-drift, 400%+ vol)
    Spot Up - Volatility Up Index     (upward-drift, 400%+ vol)
We fetch BOTH: candles (M1, ~60d) + ticks (~10d) + broker stop-level (for the
engine floor). Symbol object fields are used directly (symbol_info() can
return None for symbols not loaded in Market Watch).
"""
import sys
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta

sys.stdout.reconfigure(encoding='utf-8')

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 not installed. Run: pip install MetaTrader5")
    sys.exit(1)

MT5_LOGIN = 32341786
MT5_PASSWORD = "@Mikolo123"
MT5_SERVER = "Deriv-Demo"

TARGETS = [
    "Spot Up - Volatility Down Index",
    "Spot Up - Volatility Up Index",
]
OUT_PREFIX = Path(__file__).parent / "vol400_{sym}_1min.parquet"
TICK_PREFIX = Path(__file__).parent / "ticks_vol400_{sym}.npy"


def connect():
    if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER, timeout=15000):
        print(f"  FAILED: {mt5.last_error()}")
        return False
    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server}")
    return True


def list_vol_symbols():
    syms = mt5.symbols_get()
    cands = []
    for s in syms:
        low = s.name.lower()
        if "volatilit" in low or "vol" == low:
            cands.append(s)
    cands.sort(key=lambda s: s.name)
    for s in cands:
        print(f"  {s.name:35s} digits={s.digits} spread={s.spread} "
              f"stops={s.trade_stops_level} freeze={s.trade_freeze_level}")
    return cands


def fetch_candles(mt5_name, days=60):
    import pandas as pd
    end = datetime.now()
    start = end - timedelta(days=days)
    chunks = []
    cur = start
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
    for c in ["volume", "spread"]:
        if c in df.columns:
            keep.append(c)
    df = df[[c for c in keep if c in df.columns]]
    df["time"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    return df


def fetch_ticks(mt5_name, days=10):
    end = datetime.now()
    start = end - timedelta(days=days)
    all_prices = []
    cur = start
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
        list_vol_symbols()
        for name in TARGETS:
            sym = None
            for s in mt5.symbols_get():
                if s.name == name:
                    sym = s
                    break
            if sym is None:
                print(f"\n  MISSING: {name}")
                continue
            point = sym.point
            min_stop = sym.trade_stops_level * point
            print(f"\n  Using: {name}  point={point}  stops_pts={sym.trade_stops_level} "
                  f"min_stop_dist={min_stop:.5f}  freeze={sym.trade_freeze_level * point:.5f}")
            mt5.symbol_select(name, True)

            out = OUT_PREFIX if False else Path(str(OUT_PREFIX).replace("{sym}", name.replace(" ", "_").replace("-", "")))
            tpx = Path(str(TICK_PREFIX).replace("{sym}", name.replace(" ", "_").replace("-", "")))

            df = fetch_candles(name, days=60)
            if df is not None:
                df.to_parquet(out, index=False)
                span = (df["time"].max() - df["time"].min()).total_seconds() / 86400
                print(f"  candles: {len(df):,} rows, span {span:.1f} days, "
                      f"price {df['close'].min():.2f}-{df['close'].max():.2f} -> {out}")
            else:
                print("  no candles")

            px = fetch_ticks(name, days=10)
            if px is not None and len(px) > 1000:
                np.save(tpx, px)
                print(f"  ticks: {len(px):,} -> {tpx}")
            else:
                print(f"  no ticks (got {0 if px is None else len(px)})")
    finally:
        mt5.shutdown()
        print("  MT5 disconnected")


if __name__ == "__main__":
    main()