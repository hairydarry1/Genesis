#!/usr/bin/env python3
"""
BAR-1 VALIDATION
================
The recovery-state backtest found an enormous edge: buying a CRASH fade only
when the bar right after the crash ALSO closed down (2-bar crash, buy the
bottom) gave PF 2.5-4.4, while buying the bounce (bar1 up) lost money.

Before believing it, validate:
  1. Out-of-sample: split data in half, edge must survive both halves
  2. Mechanics: win reasons (TP vs timeout), avg win/loss sanity
  3. Hold sweep: does the edge persist at 3/5/7/10 bars
  4. Boom check: same concept inverted (sell when bar1 after BOOM also UP)
"""
import pandas as pd
import numpy as np

BROKER_FLOOR = {"crash1000": 1.14, "crash500": 0.85, "crash300": 2.43,
                "boom1000": 1.14, "boom500": 0.85}

def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def prep(df):
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df

def fade_trade(df, sym, i, hold):
    n = len(df)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (closes < df["open"].values) if is_crash else (closes > df["open"].values)
    entry_i = i + 1
    if entry_i + 2 >= n:
        return None
    floor = BROKER_FLOOR.get(sym, 1.0)
    entry = closes[entry_i]
    sl_dist = max(med[i], floor)
    # direction-aware levels
    if is_crash:
        sl_level, tp_level = entry - sl_dist, entry + 2 * sl_dist
    else:
        sl_level, tp_level = entry + sl_dist, entry - 2 * sl_dist
    exit_price = None; reason = None
    for j in range(entry_i, min(entry_i + hold, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        else:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                exit_price = lows[j] if is_crash else highs[j]
            else:
                exit_price = sl_level
            reason = "sl"; break
        if hit_tp:
            exit_price = tp_level; reason = "tp"; break
    else:
        exit_price = closes[min(entry_i + hold - 1, n - 1)]
        reason = "timeout"
    pnl = (exit_price - entry) if is_crash else (entry - exit_price)
    return {"pnl": pnl, "reason": reason}

def run(df, sym, hold):
    idx = spike_idx(df, 3.0, sym)
    trades = {"cont": [], "bounce": []}
    n = len(df)
    closes = df["close"].values
    opens = df["open"].values
    is_crash = "crash" in sym
    cont_dir = (closes < opens) if is_crash else (closes > opens)
    for i in idx:
        if i + 2 >= n:
            continue
        tr = fade_trade(df, sym, i, hold)
        if tr is None:
            continue
        key = "cont" if cont_dir[i + 1] else "bounce"
        trades[key].append(tr)
    return trades

def spike_idx(df, mult, sym):
    rng = df["range"].values
    med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"] < df["open"]).values if is_crash else (df["close"] > df["open"]).values
    m = (rng > mult * med) & adv & np.isfinite(med)
    return np.where(m)[0]

def stats(trades):
    if not trades:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    reasons = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "sum": pnls.sum(), "avg": pnls.mean(),
            "tp": reasons.get("tp", 0), "tm": reasons.get("timeout", 0),
            "sl": reasons.get("sl", 0)}

def show(name, s):
    if s is None:
        print(f"  {name:<24}  none"); return
    print(f"  {name:<24}n={s['n']:>5} WR={s['wr']:5.1f}% PF={s['pf']:5.2f} "
          f"PnL={s['sum']:8.1f} avg={s['avg']:+6.3f} "
          f"[TP {s['tp']} | TO {s['tm']} | SL {s['sl']}]")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom1000", "boom500"]:
        df = prep(load(sym))
        half = len(df) // 2
        print(f"\n=== {sym.upper()} — bar1 color validation (hold=7) ===")
        for hname, sl in [("FIRST HALF", slice(0, half)), ("SECOND HALF", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            tr = run(d, sym, 7)
            print(f"  [{hname}]")
            show("bar1 CONT (enter bottom)", stats(tr["cont"]))
            show("bar1 bounce (current EA)", stats(tr["bounce"]))
        # hold sweep on full data
        print("  hold sweep (bar1-cont only):")
        for h in [3, 5, 7, 10]:
            tr = run(df, sym, h)["cont"]
            show(f"  hold={h}", stats(tr))
