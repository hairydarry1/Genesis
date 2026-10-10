# -*- coding: utf-8 -*-
"""
data/fetch_vol_down.py — Fetch the Down twin (Spot Up - Volatility Down Index)
from the Deriv PUBLIC websocket API (no token needed), which MT5 doesn't serve
history for.

1. Query active_symbols, find the volatility-down product (and report its
   exact API code).
2. Paginate M1 candles back ~60 days (or as far as the API allows).
3. Save to data/vol400_Spot_Up__Volatility_Down_Index_1min.parquet
   (same schema as the Up twin).
"""
import asyncio
import sys
import time
from pathlib import Path

import pandas as pd

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, str(Path(__file__).resolve().parent))

from data.deriv_connection import DerivConnection  # noqa: E402

OUT = Path(__file__).parent / "vol400_Spot_Up__Volatility_Down_Index_1min.parquet"
GRANULARITY = 60
CANDLES_PER_CHUNK = 1000
TARGET_DAYS = 60


async def discover(conn):
    """List candidate symbols; return the Down product code."""
    syms = await conn.get_active_symbols()
    cands = []
    for s in syms:
        d = s.get("display_name", "")
        low = d.lower()
        if "volatilit" in low or "spot up" in low:
            cands.append((s.get("symbol"), d, s.get("submarket_display_name", "")))
    cands.sort()
    for sym, d, sub in cands:
        print(f"  {sym:20s} {d:45s} {sub}")
    for sym, d, sub in cands:
        if "down" in d.lower() and ("volatilit" in d.lower() or "spot up" in d.lower()):
            return sym
    return None


async def fetch_candles(conn, symbol, days=TARGET_DAYS):
    target_start = int(time.time()) - days * 86400
    all_candles = []
    end: str | int = "latest"
    for chunk in range(200):
        req = {
            "ticks_history": symbol,
            "end": end,
            "style": "candles",
            "count": CANDLES_PER_CHUNK,
            "granularity": GRANULARITY,
            "adjust_start_time": 1,
        }
        try:
            resp = await conn.send(req, timeout=30)
        except TimeoutError:
            print(f"  timeout on chunk {chunk}, retrying once...")
            resp = await conn.send(req, timeout=45)
        if "error" in resp:
            print(f"  API error: {resp['error']}")
            break
        candles = resp.get("candles", [])
        if not candles:
            break
        all_candles.extend(candles)
        earliest = min(c["epoch"] for c in candles)
        if chunk % 20 == 0:
            print(f"  chunk {chunk}: {len(all_candles):,} candles, "
                  f"earliest {pd.Timestamp(earliest, unit='s', tz='UTC').date()}")
        if earliest <= target_start:
            print(f"  reached target ({days}d)")
            break
        end = earliest - 1
        await asyncio.sleep(0.4)
    if not all_candles:
        return None
    df = pd.DataFrame(all_candles)
    df = df.drop_duplicates(subset=["epoch"], keep="last").sort_values("epoch")
    df["time"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    return df


async def main():
    conn = DerivConnection()
    await conn.connect()
    try:
        print("=== ACTIVE SYMBOLS (volatility / spot up) ===")
        symbol = await discover(conn)
        if symbol is None:
            print("\n  No volatility-Down product found on the public API.")
            return
        print(f"\n  DOWN product API code: {symbol}")
        df = await fetch_candles(conn, symbol)
        if df is None:
            print("  no candles returned")
            return
        df.to_parquet(OUT, index=False)
        span = (df["time"].max() - df["time"].min()).total_seconds() / 86400
        print(f"\n  saved {len(df):,} candles, span {span:.1f} days, "
              f"price {df['close'].min():.2f}-{df['close'].max():.2f} -> {OUT}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())