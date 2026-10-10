#!/usr/bin/env python3
"""
RECOVERY-STATE FADE BACKTEST
============================
Concept B from state_edges.py found: crashes that arrive while price has NOT
recovered past the previous crash are ~2x bigger (corr -0.44..-0.52).

Test entry-time versions of that state on the EA-realistic fade backtest:
  - LADDER-DOWN : this crash made a LOWER LOW than the previous crash
                  (declining regime) vs HIGHER LOW (recovering regime)
  - CONT-BAR    : the bar right after the crash (bar 1, where we enter on its
                  close) is ANOTHER down bar (crash continuing) vs an up bar
  Compare fade PnL / PF / survival across states.
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

def spike_idx(df, mult=3.0):
    rng = df["range"].values
    med = df["median_range"].values
    adv = (df["close"] < df["open"]).values
    m = (rng > mult * med) & adv & np.isfinite(med)
    return np.where(m)[0]

def fade_trade(df, sym, i, hold, sl_mult=1.0, tp_ratio=2.0, gap_min=3.0):
    """EA-realistic fade trade entered on close of bar i+1 after crash at i."""
    n = len(df)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    rng = df["range"].values
    med = df["median_range"].values
    adv = (closes < df["open"].values)
    entry_i = i + 1
    if entry_i + 2 >= n:
        return None
    floor = BROKER_FLOOR.get(sym, 1.0)
    entry = closes[entry_i]
    sl_dist = max(sl_mult * med[i], floor)
    sl_level = entry - sl_dist
    tp_level = entry + tp_ratio * sl_dist
    exit_price = None
    reason = None
    for j in range(entry_i, min(entry_i + hold, n)):
        if lows[j] <= sl_level:
            # gap-through penalty on adverse spike bars
            if adv[j] and rng[j] > gap_min * med[j]:
                exit_price = lows[j]
            else:
                exit_price = sl_level
            reason = "sl"
            break
        if highs[j] >= tp_level:
            exit_price = tp_level
            reason = "tp"
            break
    else:
        exit_price = closes[min(entry_i + hold - 1, n - 1)]
        reason = "timeout"
    pnl = exit_price - entry
    during = any(adv[j] and rng[j] > 3 * med[j]
                 for j in range(entry_i, min(entry_i + hold, n)))
    return {"pnl": pnl, "reason": reason, "survived": not during}

def report(sym, label, trades):
    if not trades:
        print(f"  {label:<28}  no trades"); return
    pnls = np.array([t["pnl"] for t in trades])
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    wr = len(wins) / len(pnls) * 100
    surv = np.mean([t["survived"] for t in trades]) * 100
    print(f"  {label:<28}{len(pnls):>6}{wr:>6.1f}%{pf:>7.2f}{pnls.sum():>9.1f}"
          f"{pnls.mean():>+7.3f}{surv:>7.1f}%")

if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000"]:
        df = prep(load(sym))
        idx = spike_idx(df, 3.0)
        lows = df["low"].values
        closes = df["close"].values
        opens = df["open"].values
        for hold in [7]:
            all_t, ladder_t, recover_t = [], [], []
            cont_t, upbar_t = [], []
            for k, i in enumerate(idx):
                tr = fade_trade(df, sym, i, hold)
                if tr is None:
                    continue
                all_t.append(tr)
                if k > 0:
                    prev = idx[k - 1]
                    higher_low = lows[i] > lows[prev]
                    (recover_t if higher_low else ladder_t).append(tr)
                # bar-1 character: bar i+1 (entry bar) direction
                if i + 1 < len(df):
                    bar1_down = closes[i + 1] < opens[i + 1]
                    (cont_t if bar1_down else upbar_t).append(tr)
            print(f"\n=== {sym.upper()} hold={hold} — recovery-state filters ===")
            report(sym, "ALL crashes", all_t)
            report(sym, "HIGHER LOW than prev (recov)", recover_t)
            report(sym, "LOWER LOW than prev (ladder)", ladder_t)
            report(sym, "bar1 = UP (fade working)", upbar_t)
            report(sym, "bar1 = DOWN (continuation)", cont_t)
