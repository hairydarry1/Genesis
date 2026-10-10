#!/usr/bin/env python3
"""
CLUSTER-ENTRY BACKTEST
======================
Hypothesis from the user's chart observation:
"After 2-4 BIG back-to-back crash spikes, the updrift runs longer
than 7 bars before the next big spike — so fade entries taken after
a big-spike CLUSTER survive longer and make more."

Test on M1 candles:
  - "BIG spike" = adverse bar (crash: close<open) with range > BIGx median
  - Entry: close of bar 1 after the big spike bar (EA-style delayed entry)
  - Two filters:
      ALL     = every big spike
      CLUSTER = big spike had >=1 more big spike within the prior 6 bars
                (i.e. it is the 2nd/3rd/4th of a tight group)
      QUAD    = >=2 more in prior 6 bars (3+ group)
  - Exits (EA-realistic, intrabar on M1):
      TP = 2.0 x SL distance; SL distance = max(1x median, broker floor)
      * If an ADVERSE SPIKE bar occurs while in the trade and gaps through
        SL, loss = gap-through penalty (the spike bar's adverse range), which
        is how real fills behave on Boom/Crash.
      * Timeout exit at hold bars (close).
  - Report: n, WR, PF, PnL, avg win/loss, survival (no adverse big spike
    during hold), per symbol and per hold.
"""

import pandas as pd
import numpy as np

# broker floor SL distances (price units) measured from live logs
BROKER_FLOOR = {"crash1000": 1.14, "crash500": 0.85, "crash300": 2.43,
                "boom1000": 1.14, "boom500": 0.85}

def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    return df

def big_spike_mask(df, mult):
    rng = df["range"].values
    med = df["median_range"].values
    adv_dir = (df["close"] < df["open"]).values if "crash" in sym else (df["close"] > df["open"]).values
    return (rng > mult * med) & adv_dir

def backtest(df, sym, big_mult=8.0, hold=7, min_group=1, tp_ratio=2.0):
    n = len(df)
    closes = df["close"].values
    highs = df["high"].values
    lows = df["low"].values
    opens = df["open"].values
    rng = df["range"].values
    med = df["median_range"].values
    floor = BROKER_FLOOR.get(sym, 1.0)
    is_crash = "crash" in sym
    adv_dir = (closes < opens) if is_crash else (closes > opens)
    big = big_spike_mask(df, big_mult)

    sl_dist = max(1.0 * np.median(rng), floor)
    trades = []

    # find big spike indices
    big_idx = np.where(big)[0]

    # group label: how many big spikes in the prior 6 bars INCLUDING this one
    group = np.zeros(n, dtype=int)
    for i in big_idx:
        group[i] = 1 + int(big[max(0, i - 6):i].sum())

    for i in big_idx:
        if group[i] < min_group:
            continue
        entry_i = i + 1          # enter at close of next bar (EA: bar 1 after spike)
        if entry_i + 2 >= n:
            continue
        entry = closes[entry_i]

        sl_level = entry - sl_dist if is_crash else entry + sl_dist
        tp_level = entry + tp_ratio * sl_dist if is_crash else entry - tp_ratio * sl_dist

        exit_price = None
        reason = None
        survived = True
        # walk hold bars
        for j in range(entry_i, min(entry_i + hold, n)):
            hi, lo = highs[j], lows[j]
            if is_crash:
                hit_sl = lo <= sl_level
                hit_tp = hi >= tp_level
            else:
                hit_sl = hi >= sl_level
                hit_tp = lo <= tp_level

            # gap-through penalty: if this bar is itself an adverse spike that
            # would gap past SL, the real fill is near this bar's adverse extreme
            if hit_sl:
                if adv_dir[j] and rng[j] > 3 * med[j]:
                    # gap through — fill near the spike extreme
                    fill = lo if is_crash else hi
                else:
                    fill = sl_level
                exit_price = fill
                reason = "sl"
                survived = False
                break
            if hit_tp:
                exit_price = tp_level
                reason = "tp"
                break

            # adverse big spike during hold without hitting SL yet counts as stress
            if j > entry_i and adv_dir[j] and big[j]:
                pass
        else:
            # timeout
            exit_price = closes[min(entry_i + hold - 1, n - 1)]
            reason = "timeout"

        pnl = (exit_price - entry) if is_crash else (entry - exit_price)
        # add the 'adverse big spike during hold' marker
        during = any(adv_dir[j] and big[j] for j in range(entry_i, min(entry_i + hold, n)))
        trades.append({"pnl": pnl, "reason": reason, "survived": not during})

    return trades

def report(sym, hold, results):
    print(f"\n=== {sym.upper()} hold={hold} bars ===")
    hdr = f"  {'entry filter':<16}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>10}{'avg':>8}{'avgWin':>8}{'avgLoss':>9}{'surv%':>7}"
    print(hdr)
    print("  " + "-" * 80)
    for label, tr in results:
        if not tr:
            print(f"  {label:<16}  no trades")
            continue
        pnls = np.array([t["pnl"] for t in tr])
        wins = pnls[pnls > 0]
        losses = pnls[pnls <= 0]
        pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
        wr = len(wins) / len(pnls) * 100
        surv = np.mean([t["survived"] for t in tr]) * 100
        print(f"  {label:<16}{len(tr):>6}{wr:>6.1f}%{pf:>7.2f}{pnls.sum():>10.1f}"
              f"{pnls.mean():>+8.3f}{wins.mean() if len(wins) else 0:>+8.3f}"
              f"{losses.mean() if len(losses) else 0:>+9.3f}{surv:>6.1f}%")

if __name__ == "__main__":
    for sym in ["crash300", "crash1000", "crash500"]:
        df = load(sym)
        df["range"] = df["high"] - df["low"]
        df["median_range"] = df["range"].rolling(100, min_periods=20).median()
        for hold in [7, 10]:
            all_tr = backtest(df, sym, big_mult=8.0, hold=hold, min_group=1)
            cl_tr = backtest(df, sym, big_mult=8.0, hold=hold, min_group=2)
            qu_tr = backtest(df, sym, big_mult=8.0, hold=hold, min_group=3)
            report(sym, hold, [("ALL big", all_tr), ("CLUSTER (>=2)", cl_tr), ("QUAD (>=3)", qu_tr)])

    # also sweep big threshold on crash300 (the user's symbol)
    sym = "crash300"
    df = load(sym)
    df["range"] = df["high"] - df["low"]
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    print("\n\n=== CRASH300: big-threshold sweep (hold=7, cluster>=2) ===")
    for mult in [5, 8, 12]:
        all_tr = backtest(df, sym, big_mult=mult, hold=7, min_group=1)
        cl_tr = backtest(df, sym, big_mult=mult, hold=7, min_group=2)
        report(sym + f" (>={mult}x)", 7, [("ALL", all_tr), ("CLUSTER>=2", cl_tr)])