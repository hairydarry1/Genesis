#!/usr/bin/env python3
"""
CONTINUATION-BAR FULL BACKTEST
==============================
Full EA-realistic backtest of the continuation-bar entry concept:
  - Spike bar (range > 3x median, adverse direction) closes
  - EA waits for bar 1 (the next M1 bar) to close
  - CONT entry: bar 1 closed in the SAME direction (crash down / boom up)
    -> enter at bar 1 close (the exhausted extreme)
  - BOUNCE entry: bar 1 closed opposite -> the old behavior (for comparison)

EA mechanics modeled:
  - SL = max(1x median range, broker floor); TP = 2x SL (ratio preserved)
  - Gap-through fills: if the SL is crossed by an adverse spike bar, the
    fill lands at that bar's adverse extreme (measured live behavior)
  - Cluster filter (EA): skip if 3+ spikes in the last 10 bars
  - Loss cooldown: after a losing close, skip the next K spike entries
  - Partial entry: 50% now, other 50% added after 1 bar if profitable

Reports: config table (WR/PF/PnL/avg/maxDD), out-of-sample halves, and a
daily equity view for the best config.
"""
import pandas as pd
import numpy as np

BROKER_FLOOR = {"crash1000": 1.14, "crash500": 0.85, "crash300": 2.43,
                "boom1000": 1.14, "boom500": 0.85}
SPIKE_MULT = 3.0

def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def prep(df):
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    if "time" in df.columns:
        df["dt"] = pd.to_datetime(df["time"], utc=True)
    elif "epoch" in df.columns:
        df["dt"] = pd.to_datetime(df["epoch"], unit="s", utc=True)
    return df

def spike_mask(df, sym):
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"] < df["open"]).values if is_crash else (df["close"] > df["open"]).values
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med)
    # avoid double counting: require range > MinSpikeSize equivalent — small spikes
    m = m & (rng > 0.2)
    return m

def one_trade(df, sym, i, hold, sl_dist, entry_sel):
    """Run a single fade trade after spike at bar i. entry_sel: 'cont'/'bounce'/'any'."""
    n = len(df)
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    opens = df["open"].values
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (closes < opens) if is_crash else (closes > opens)
    e = i + 1                       # bar 1 after spike; entry bar
    if e + 2 >= n:
        return None
    if entry_sel == "cont" and not adv[e]:
        return None
    if entry_sel == "bounce" and adv[e]:
        return None
    entry = closes[e]
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    exit_price = None; reason = None
    # Position opens at close[e] = start of bar e+1. Only bars AFTER entry can
    # fill the position (never the entry bar itself — its high/low predate us).
    for j in range(e + 1, min(e + 1 + hold, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        else:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                exit_price = lows[j] if is_crash else highs[j]   # gap fill
            else:
                exit_price = sl_level
            reason = "sl"; break
        if hit_tp:
            exit_price = tp_level; reason = "tp"; break
    else:
        exit_price = closes[min(e + hold, n - 1)]
        reason = "timeout"
    pnl = (exit_price - entry) if is_crash else (entry - exit_price)
    return {"pnl": pnl, "reason": reason, "bar": i, "dt": df["dt"].values[i]}

def run_pipeline(df, sym, hold, entry_sel="cont", cluster=True, cooldown=0,
                 sl_override=None):
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    sm = spike_mask(df, sym)
    idx = np.where(sm)[0]
    med = df["median_range"].values
    floor = BROKER_FLOOR.get(sym, 1.0)
    trades = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        # cluster filter: 3+ spikes in prior 10 bars (incl this) -> skip
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        sl_dist = max(1.0 * med[i], floor) if sl_override is None else sl_override
        tr = one_trade(df, sym, i, hold, sl_dist, entry_sel)
        if tr is None:
            continue
        trades.append(tr)
        if tr["pnl"] <= 0 and cooldown > 0:
            skip = cooldown
    return trades

def stats(trades):
    if not trades:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    eq = np.cumsum(pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq).max()
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean(), "maxdd": dd}

def row(label, s):
    if s is None:
        print(f"  {label:<28}  none"); return
    print(f"  {label:<28}{s['n']:>6}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}")

if __name__ == "__main__":
    all_symbols = ["crash300", "crash500", "crash1000", "boom500", "boom1000"]
    for sym in all_symbols:
        df = prep(load(sym))
        is_crash = "crash" in sym
        print(f"\n{'='*86}\n{sym.upper()}  ({'fade = LONG' if is_crash else 'fade = SHORT'})\n{'='*86}")
        print(f"  {'config':<28}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD")
        # hold sweep on cont
        for hold in [2, 3, 5, 7]:
            tr = run_pipeline(df, sym, hold, "cont", cluster=True)
            row(f"CONT hold={hold}", stats(tr))
        # current EA proxy: any bar1 entry, hold=3 (its typical preset)
        tr = run_pipeline(df, sym, 3, "any", cluster=True)
        row("CURRENT-EA (any, hold=3)", stats(tr))
        # best-config check with protections
        tr = run_pipeline(df, sym, 3, "cont", cluster=True, cooldown=3)
        row("CONT hold=3 +cooldown3", stats(tr))
        # out-of-sample split
        half = len(df) // 2
        print("  OOS halves (CONT hold=3, cluster):")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            tr = run_pipeline(d, sym, 3, "cont", cluster=True)
            row(f"  {hname} half", stats(tr))
        # daily equity for cont hold=3
        tr = run_pipeline(df, sym, 3, "cont", cluster=True, cooldown=3)
        if tr:
            daily = pd.DataFrame([{"dt": t["dt"], "pnl": t["pnl"]} for t in tr])
            daily = daily.set_index("dt").resample("1D")["pnl"].agg(["sum", "count"])
            win_days = (daily["sum"] > 0).sum(); lose_days = (daily["sum"] < 0).sum()
            print(f"  days: {len(daily)} | +{win_days} / -{lose_days} | "
                  f"best={daily['sum'].max():+.1f} worst={daily['sum'].min():+.1f} "
                  f"| worst-day trades={int(daily.loc[daily['sum'].idxmin(),'count'])}")
