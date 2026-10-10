#!/usr/bin/env python3
"""
LAYERED DEFENCE TEST — corrected M1 engine
==========================================
Question: can protective layers be STACKED, or do they just remove the same
losses twice? Each layer is added cumulatively and its MARGINAL effect is
measured (does PF rise, does DD/streak fall, how many trades does it cost?).

Layers (in order):
  L0 FULL      : full size, no protection          (what live feels like)
  L1 PARTIAL   : 50% now, 2nd half only if profitable at bar e+1 close
  L2 CD3       : skip 3 spikes after a loss
  L3 BE        : after the 2nd half joins, move SL to entry (free trade)
  L4 ESCALATE  : cooldown grows 3 -> 5 -> 8 with consecutive losses
  L5 DAYCAP    : stop for the day after 3 losses

All rows keep the EA cluster filter (skip 3+ spikes in 10 bars). Fill model:
entry at M1 close, only bars AFTER entry can fill, gap-through on adverse
spike bars, TP = 2x floored SL.
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

def spike_idx(df, sym):
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]

def joint_trade(df, sym, i, hold, be_after_add):
    """One fade trade. Returns pnl (points per full unit) + loss flag."""
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
    tpL = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    sign = 1 if is_crash else -1

    size = 0.5
    sl = slL
    added = False
    exit_price = None
    for j in range(e + 1, min(e + 1 + hold, n)):
        if not added:
            # decision point was close of bar e+1: profitable & still open?
            fav = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
            if fav:
                added = True
                size = 1.0
                if be_after_add:
                    sl = entry          # breakeven stop once second half joins
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl, highs[j] >= tpL
        else:
            hit_sl, hit_tp = highs[j] >= sl, lows[j] <= tpL
        if hit_sl:
            # Same fill rule for every stop level (SL, BE, ...): a stop
            # crossed by an adverse spike bar fills at the gap price — a
            # BE stop gaps through just like an SL stop on these symbols.
            if adv[j] and rng[j] > 3 * med[j]:
                exit_price = lows[j] if is_crash else highs[j]
            else:
                exit_price = sl
            break
        if hit_tp:
            exit_price = tpL
            break
    else:
        exit_price = closes[min(e + 1 + hold - 1, n - 1)]
    pnl = (exit_price - entry) * sign * size
    return {"pnl": pnl, "loss": pnl <= 0, "dt": df["dt"].values[i]}

def pipeline(df, sym, hold=3, partial=True, cd=0, be=False, escalate=False,
             daycap=0, cluster=True):
    idx = spike_idx(df, sym)
    dts = df["dt"].values
    trades = []
    skip = 0
    consec_loss = 0
    losses_today = 0
    last_day = None
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        day = pd.Timestamp(dts[i]).date()
        if daycap > 0 and last_day is not None and day != last_day:
            losses_today = 0
        last_day = day
        if daycap > 0 and losses_today >= daycap:
            continue
        tr = joint_trade(df, sym, i, hold, be) if partial else \
             full_trade(df, sym, i, hold)
        if tr is None:
            continue
        trades.append(tr)
        if tr["loss"]:
            consec_loss += 1
            losses_today += 1
            if cd > 0:
                skip = cd if not escalate else min(cd * consec_loss, 12)
        else:
            consec_loss = 0
    return trades

def full_trade(df, sym, i, hold):
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
    tpL = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    sign = 1 if is_crash else -1
    exit_price = None
    for j in range(e + 1, min(e + 1 + hold, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= slL, highs[j] >= tpL
        else:
            hit_sl, hit_tp = highs[j] >= slL, lows[j] <= tpL
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                exit_price = lows[j] if is_crash else highs[j]
            else:
                exit_price = slL
            break
        if hit_tp:
            exit_price = tpL
            break
    else:
        exit_price = closes[min(e + 1 + hold - 1, n - 1)]
    pnl = (exit_price - entry) * sign
    return {"pnl": pnl, "loss": pnl <= 0, "dt": df["dt"].values[i]}

def stats(trades):
    if not trades:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    eq = np.cumsum(pnls)
    dd = (np.maximum.accumulate(eq) - eq).max()
    # max consecutive losses
    mx = cur = 0
    for t in trades:
        cur = cur + 1 if t["loss"] else 0
        mx = max(mx, cur)
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean(), "maxdd": dd, "maxcl": mx}

def show(label, s):
    if s is None:
        print(f"  {label:<9}{'--':>6}"); return
    print(f"  {label:<9}{s['n']:>6}{s['wr']:>6.1f}%{s['pf']:>6.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}"
          f"  maxCL={s['maxcl']:>3}")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = prep(load(sym))
        print(f"\n=== {sym.upper()} (hold=3) — layered defence ===")
        print(f"  {'layer':<9}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>9}{'avg':>8}  maxDD   maxCL")
        show("L0 FULL", stats(pipeline(df, sym, partial=False)))
        show("L1 +PART", stats(pipeline(df, sym, partial=True)))
        show("L2 +CD3", stats(pipeline(df, sym, partial=True, cd=3)))
        show("L3 +BE", stats(pipeline(df, sym, partial=True, cd=3, be=True)))
        show("L4 +ESC", stats(pipeline(df, sym, partial=True, cd=3, be=True, escalate=True)))
        show("L5 +DAYCAP", stats(pipeline(df, sym, partial=True, cd=3, be=True, escalate=True, daycap=3)))
