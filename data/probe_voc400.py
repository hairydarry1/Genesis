import sys
import MetaTrader5 as mt5
print("init...", flush=True)
mt5.initialize()
print("symbol_select...", flush=True)
ok = mt5.symbol_select("Vol over Crash 400", True)
print("select:", ok, flush=True)
print("symbol_info...", flush=True)
info = mt5.symbol_info("Vol over Crash 400")
print("info:", None if info is None else
      f"digits={info.digits} spread={info.spread} pt={info.point} "
      f"tick_val={info.trade_tick_value} vol_min={info.volume_min} "
      f"vol_max={info.volume_max} vol_step={info.volume_step}", flush=True)
if info is not None:
    print("copy 5 rates...", flush=True)
    rates = mt5.copy_rates_from_pos("Vol over Crash 400", mt5.TIMEFRAME_M1, 0, 5)
    print("candles5:", 0 if rates is None else len(rates), flush=True)
    if rates is not None and len(rates):
        import pandas as pd
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        print(df[["time", "open", "high", "low", "close"]].to_string(), flush=True)
print("done", flush=True)
mt5.shutdown()