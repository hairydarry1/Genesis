#!/usr/bin/env python3
"""
ICT PULLBACK PROXY TEST
=======================
Video claim (https://www.youtube.com/watch/uQSALWvB3oc):
  M15 break-of-structure -> bullish regime -> wait for pullback to the
  50% retracement of the drift leg -> enter in drift direction -> TP at
  the prior swing high.

Proxy on the M1 book (drift direction = LONG crash / SHORT boom):
  - Discharge bar i (adverse, range > 3x median) ends a drift leg.
  - Baseline entry: close of bar i+1 (the EA's current entry).
  - Pullback entry: first bar j in [i+1, i+6] whose close has retraced
    >= 50% of the bounce off the discharge low (crash) / high (boom),
    without breaking the discharge extreme.  Skip if none (no trade).
  - Regime gate: M15 break-of-structure (close broke the recent 4-bar
    M15 high -> bullish; broke low -> bearish).  Drift entries only in
    the matching regime.
  - Same exit machinery as the corrected engine: gap-through SL fills on
    adverse spike bars, partial add-B-if-profitable, TP = 2x SL.
  - Controls: random-entry drift fades with identical mechanics;
    OOS halves.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from partial_m1_corrected import BROKER_FLOOR, half_exit, arrays

SPIKE_MULT = 3.0


def load(sym):
    df = pd.read_parquet(f"data/candles_{sym}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    if "epoch" in df.columns and "time" not in df.columns:
        df["time"] = pd.to_datetime(df["epoch"], unit="s")
    else:
        df["time"] = pd.to_datetime(df["time"])
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def m15_regime(df, sym):
    """Return boolean array over M1 rows: True if M15 regime matches drift dir."""
    df = df.copy()
    m15 = df.resample("15min", on="time").agg(
        o=("open", "first"), h=("high", "max"), l=("low", "min"), c=("close", "last")
    ).dropna().reset_index()
    c = m15["c"].values; h = m15["h"].values; l = m15["l"].values
    n = len(m15)
    bull = np.zeros(n, dtype=bool)
    bear = np.zeros(n, dtype=bool)
    for t in range(5, n):
        if c[t] > h[t - 4:t].max():
            bull[t] = True
        if c[t] < l[t - 4:t].min():
            bear[t] = True
    # regime = most recent BOS in the last 6 M15 bars wins
    reg = np.zeros(n, dtype=bool)  # True = bullish regime
    for t in range(n):
        w = bull[max(0, t - 6):t + 1]
        if w.any():
            last_bull = t - np.where(w)[0][-1]
            wb = bear[max(0, t - 6):t + 1]
            if not wb.any() or (np.where(w)[0][-1] > np.where(wb)[0][-1]):
                reg[t] = True
    m15["bull"] = reg
    m15t = m15["time"].values.astype("datetime64[ns]")
    df_t = df["time"].values.astype("datetime64[ns]")
    idx = np.searchsorted(m15t, df_t, side="right") - 1
    idx = np.clip(idx, 0, n - 1)
    return m15["bull"].values[idx]


def discharges(df, sym):
    rng = df["range"].values; med = df["median_range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    m = (rng > SPIKE_MULT * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]


def trade_at(df, sym, entry_bar, hold, partial=True):
    """Run the corrected partial engine entering at close of entry_bar."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    entry_bar = int(entry_bar)
    if entry_bar + 2 >= n:
        return None
    entry = closes[entry_bar]
    sl_dist = max(med[entry_bar], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)

    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, entry_bar + 1, hold, n)
    add_b = False
    if partial:
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, entry_bar + 1, 1, n)
        if rA1 == "timeout":
            fav = (closes[entry_bar + 1] > entry) if is_crash else (closes[entry_bar + 1] < entry)
            add_b = fav
    if add_b:
        pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, entry_bar + 2, hold - 1, n)
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return pnl


def pullback_bar(df, sym, i, maxwait=6):
    """First bar j in [i+1, i+maxwait] whose close retraced >=50% of the
    bounce off the discharge extreme without breaking it.  -1 if none."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    ext = lows[i] if is_crash else highs[i]  # discharge extreme
    bounce = None
    for j in range(i + 1, min(i + 1 + maxwait, n)):
        bh = highs[j] if is_crash else lows[j]
        if bounce is None or (bh > bounce if is_crash else bh < bounce):
            bounce = bh
        retrace = abs(closes[j] - bounce)
        total = abs(bounce - ext)
        broke = (lows[j] < ext) if is_crash else (highs[j] > ext)
        if not broke and total > 0 and retrace >= 0.5 * total:
            return j
    return -1


def run_cfg(df, sym, hold, mode, regime=None, partial=True, cooldown=0):
    """mode: 'e1' | 'pull' | 'rand'.  regime: bool array where True = M15 bullish.
    For crash (LONG drift) the gate is bullish; for boom (SHORT drift) bearish."""
    if regime is not None and "boom" in sym:
        regime = ~regime
    idx = discharges(df, sym)
    rng_state = np.random.RandomState(42)
    trades = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if regime is not None and not regime[i]:
            continue
        if mode == "e1":
            b = i + 1
        elif mode == "pull":
            b = pullback_bar(df, sym, i)
            if b < 0:
                continue
        elif mode == "rand":
            b = int(rng_state.randint(0, len(df) - 10))
            if regime is not None and not regime[b]:
                continue
        pnl = trade_at(df, sym, b, hold, partial)
        if pnl is None:
            continue
        trades.append(pnl)
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def stats(trades):
    if not trades:
        return None
    pnls = np.array(trades)
    wins = pnls[pnls > 0]; losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean()}


def row(label, s):
    if s is None:
        print(f"  {label:<34}  none"); return
    print(f"  {label:<34}{s['n']:>6}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>10.1f}{s['avg']:>+8.3f}")


if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = load(sym)
        reg = m15_regime(df, sym)
        print(f"\n{'='*96}\n{sym.upper()}   (drift fade, partial, hold=3)\n{'='*96}")
        print(f"  {'config':<34}{'n':>6}{'WR%':>7}{'PF':>7}{'PnL':>10}{'avg':>8}")
        for label, mode, rg in [
            ("DISCHARGE e+1 (EA baseline)", "e1", None),
            ("DISCHARGE e+1 + M15 BOS gate", "e1", reg),
            ("PULLBACK 50% retrace", "pull", None),
            ("PULLBACK 50% + M15 BOS gate", "pull", reg),
            ("RANDOM entries (control)", "rand", None),
            ("RANDOM + M15 BOS gate", "rand", reg),
        ]:
            row(label, stats(run_cfg(df, sym, 3, mode, regime=rg)))
        half = len(df) // 2
        print("  OOS halves (PULLBACK + gate):")
        for hname, sl in [("1st", slice(0, half)), ("2nd", slice(half, None))]:
            d = df.iloc[sl].reset_index(drop=True)
            r2 = m15_regime(d, sym)
            row(f"  {hname}", stats(run_cfg(d, sym, 3, "pull", regime=r2)))