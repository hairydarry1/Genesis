#!/usr/bin/env python3
"""
NEW DEFENCE AXES — corrected M1 engine
======================================
Core defence (partial + cooldown) is established. These four axes each attack
a DIFFERENT failure mode and are tested ON TOP of that core:

  TP_RATIO   : exit geometry. TP 1.5x instead of 2x = wins close sooner =
               less time in market = fewer gap encounters. (time-in-market)
  TRAIL      : profit protection. After the 2nd half joins, trail the stop
               behind price with a buffer. Gaps still fill at the gap price
               (same as every stop here) — so this only changes grind exits,
               not gap losses. Tested honestly with that fill rule.
  MONSTER    : entry gate. Skip entries whose spike arrived within 3 bars of
               a >=6x median monster spike (the regime where the NEXT gap is
               most likely). Different from the cluster count filter.
  PROFITLOCK : stop for the day once cumulative PnL >= target pts/day.
               (risk management: locks wins, ends exposure for the day)

Base config: PARTIAL + CD3 + hold=3 + cluster filter (all EA-realistic).
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
    else:
        df["dt"] = pd.date_range("2020-01-01", periods=len(df), freq="min", tz="UTC")
    return df

def spike_info(df, sym):
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0], rng, med

def trade(df, sym, i, hold, tp_ratio, trail_mult):
    """One fade trade. Partial 50%, 2nd half at bar e+1 close if profitable.
    Optional trailing stop (buffer x sl_dist behind best close) once added.
    Returns pnl or None."""
    n = len(df)
    closes = df["close"].values; highs = df["high"].values; lows = df["low"].values
    opens = df["open"].values
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (closes < opens) if is_crash else (closes > opens)
    e = i + 1
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    slL = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tpL = (entry + tp_ratio * sl_dist) if is_crash else (entry - tp_ratio * sl_dist)
    sign = 1 if is_crash else -1

    size = 0.5
    sl = slL
    added = False
    best = entry
    exit_price = None
    for j in range(e + 1, min(e + 1 + hold, n)):
        if not added:
            fav = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
            if fav:
                added = True
                size = 1.0
                best = closes[e + 1]
        # trailing update on bar close BEFORE the SL/TP checks of next bar
        if added and trail_mult > 0:
            if is_crash:
                best = max(best, closes[j])
                new_sl = best - trail_mult * sl_dist
                sl = max(sl, min(new_sl, entry + (best - entry) * 0.99))
            else:
                best = min(best, closes[j])
                new_sl = best + trail_mult * sl_dist
                sl = min(sl, max(new_sl, entry - (entry - best) * 0.99))
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl, highs[j] >= tpL
        else:
            hit_sl, hit_tp = highs[j] >= sl, lows[j] <= tpL
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                exit_price = lows[j] if is_crash else highs[j]  # gap through any stop
            else:
                exit_price = sl
            break
        if hit_tp:
            exit_price = tpL
            break
    else:
        exit_price = closes[min(e + 1 + hold - 1, n - 1)]
    pnl = (exit_price - entry) * sign * size
    return {"pnl": pnl, "loss": pnl <= 0, "dt": df["dt"].values[i], "bar": i}

def pipeline(df, sym, hold=3, cd=3, tp_ratio=2.0, trail_mult=0.0,
             monster=0, profit_lock=0.0):
    idx, rng, med = spike_info(df, sym)
    dts = df["dt"].values
    trades = []
    skip = 0
    consec = 0
    day_pnl = 0.0
    last_day = None
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if k >= 2 and (i - idx[k - 2]) <= 10:
            continue                       # cluster filter (EA standard)
        if monster > 0:
            # skip if any spike >= monster x median within the prior 3 bars
            m = np.where((idx[:k] >= i - 3) & (rng[idx[:k]] > monster * med[idx[:k]]))[0]
            if len(m) > 0:
                continue
        day = pd.Timestamp(dts[i]).date()
        if last_day is not None and day != last_day:
            day_pnl = 0.0
        last_day = day
        if profit_lock > 0 and day_pnl >= profit_lock:
            continue
        tr = trade(df, sym, i, hold, tp_ratio, trail_mult)
        if tr is None:
            continue
        trades.append(tr)
        day_pnl += tr["pnl"]
        if tr["loss"]:
            consec += 1
            skip = cd
        else:
            consec = 0
    return trades

def stats(trades):
    if not trades:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    eq = np.cumsum(pnls)
    dd = (np.maximum.accumulate(eq) - eq).max()
    mx = cur = 0
    for t in trades:
        cur = cur + 1 if t["loss"] else 0
        mx = max(mx, cur)
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean(), "maxdd": dd, "maxcl": mx}

def show(label, s):
    if s is None:
        print(f"  {label:<12}{'--':>6}"); return
    print(f"  {label:<12}{s['n']:>6}{s['wr']:>6.1f}%{s['pf']:>6.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}"
          f"  maxCL={s['maxcl']:>3}")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = prep(load(sym))
        print(f"\n=== {sym.upper()} — core = PARTIAL+CD3 hold=3 ===")
        print(f"  {'axis':<12}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD   maxCL")
        show("CORE", stats(pipeline(df, sym)))
        show("+TP=1.5x", stats(pipeline(df, sym, tp_ratio=1.5)))
        show("+TP=1.25x", stats(pipeline(df, sym, tp_ratio=1.25)))
        show("+TRAIL .5", stats(pipeline(df, sym, trail_mult=0.5)))
        show("+TRAIL 1.0", stats(pipeline(df, sym, trail_mult=1.0)))
        show("+MONSTER6", stats(pipeline(df, sym, monster=6.0)))
        show("+LOCK +5", stats(pipeline(df, sym, profit_lock=5.0)))
        show("+LOCK +10", stats(pipeline(df, sym, profit_lock=10.0)))
