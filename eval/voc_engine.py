#!/usr/bin/env python3
"""
eval/voc_engine.py — Corrected-engine battery on the Vol over family.

Uses the EXACT semantics of eval/partial_m1_corrected.py (the engine that
killed every artifact before): entry at close of spike+1, SL=max(med,floor),
TP=2x, half-B adds next bar if profitable, gap-through fills on adverse
discharge bars, cluster filter, loss cooldown. Then OOS halves + random
controls (the checks that killed boom500/150s).
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"

# key -> (parquet slug, broker floor, is_crash)
SYMS = {
    "voc400":  ("Vol_over_Crash_400", 3.60, True),
    "vob400":  ("Vol_over_Boom_400",  3.60, False),
    "voc550":  ("Vol_over_Crash_550", 3.20, True),
    "vob550":  ("Vol_over_Boom_550",  3.20, False),
    "voc750":  ("Vol_over_Crash_750", 2.50, True),
    "vob750":  ("Vol_over_Boom_750",  2.50, False),
}


def load(key):
    slug, floor, is_crash = SYMS[key]
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df, floor, is_crash


def arrays(df, is_crash):
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng = df["range"].values; med = df["median_range"].values
    adv = (c < o) if is_crash else (c > o)
    return c, h, l, o, rng, med, adv


def spike_idx(df, is_crash, th=3.0):
    rng = df["range"].values; med = df["median_range"].values
    c = df["close"].values; o = df["open"].values
    adv = (c < o) if is_crash else (c > o)
    m = (rng > th * med) & adv & np.isfinite(med) & (rng > 0.2)
    return np.where(m)[0]


def half_exit(c, h, l, rng, med, adv, is_crash, entry, sl_l, tp_l, s, mb, n):
    for j in range(s, min(s + mb, n)):
        if is_crash:
            hit_sl, hit_tp = l[j] <= sl_l, h[j] >= tp_l
        else:
            hit_sl, hit_tp = h[j] >= sl_l, l[j] <= tp_l
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                return (l[j] if is_crash else h[j]), "sl"
            return sl_l, "sl"
        if hit_tp:
            return tp_l, "tp"
    return c[min(s + mb - 1, n - 1)], "timeout"


def one_trade(df, is_crash, floor, i, hold, partial=True):
    n = len(df)
    c, h, l, o, rng, med, adv = arrays(df, is_crash)
    e = i + 1
    if e + 2 >= n:
        return None
    entry = c[e]
    sl_d = max(med[i], floor)
    sl_l = (entry - sl_d) if is_crash else (entry + sl_d)
    tp_l = (entry + 2 * sl_d) if is_crash else (entry - 2 * sl_d)

    pA, _ = half_exit(c, h, l, rng, med, adv, is_crash, entry, sl_l, tp_l,
                      e + 1, hold, n)
    add_b = False
    pA1, rA1 = half_exit(c, h, l, rng, med, adv, is_crash, entry, sl_l, tp_l,
                         e + 1, 1, n)
    if rA1 == "timeout":
        fav = (c[e + 1] > entry) if is_crash else (c[e + 1] < entry)
        add_b = fav
    if add_b and partial:
        pB, _ = half_exit(c, h, l, rng, med, adv, is_crash, entry, sl_l, tp_l,
                          e + 2, hold - 1, n)
        pnl = 0.5 * ((pA - entry) + (pB - entry)) * (1 if is_crash else -1)
    else:
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
    return pnl


def run(df, is_crash, floor, hold, partial=True, cluster=True, cooldown=0,
        th=3.0, entry_lag=0):
    idx = spike_idx(df, is_crash, th)
    pnls = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        i2 = i + entry_lag
        tr = one_trade(df, is_crash, floor, i2, hold, partial)
        if tr is None:
            continue
        pnls.append(tr)
        if tr <= 0 and cooldown > 0:
            skip = cooldown
    return np.array(pnls)


def stats(pnls):
    if len(pnls) == 0:
        return None
    w = pnls[pnls > 0]; l = pnls[pnls <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) else 999.0
    # loss-gap: realized avg loss vs stop (gap-through)
    return dict(n=len(pnls), wr=100 * len(w) / len(pnls),
                pf=pf, avg=pnls.mean(), worst=pnls.min(),
                sum=pnls.sum())


def fmt(label, s):
    if s is None:
        print(f"    {label:<34} none")
        return
    print(f"    {label:<34}{s['n']:>6}  WR {s['wr']:>4.0f}%  PF {s['pf']:>6.2f}"
          f"  avg {s['avg']:>+7.3f}  sum {s['sum']:>8.1f}  worst {s['worst']:>+6.2f}")


def random_control(df, is_crash, floor, hold, n_tr):
    """Count-matched random entries with identical partial mechanics."""
    n = len(df)
    rng = np.random.default_rng(7)
    med = df["median_range"].values
    warm = np.where(np.isfinite(med) & (med > 0))[0]
    if len(warm) <= n_tr:
        return np.array([])
    picks = rng.choice(warm[20:-20], size=n_tr, replace=False)
    picks.sort()
    out = []
    for i in picks:
        tr = one_trade(df, is_crash, floor, i, hold, partial=True)
        if tr is not None:
            out.append(tr)
    return np.array(out)


def main():
    for key in ("voc400", "vob400", "voc550", "vob550", "voc750", "vob750"):
        df, floor, is_crash = load(key)
        medv = float(np.nanmedian(df["range"]))
        print(f"\n{'='*96}\n{key.upper()}  floor={floor} med={medv:.2f} "
              f"floor/med={floor/medv:.2f} is_crash={is_crash}")
        print("  spike th=3.0, corrected engine, hold sweep:")
        for hold in (2, 3, 5):
            f_ = stats(run(df, is_crash, floor, hold, partial=False))
            p_ = stats(run(df, is_crash, floor, hold, partial=True))
            fmt(f"FULL hold={hold}", f_)
            fmt(f"PARTIAL hold={hold}", p_)
        # cluster+cooldown stack on partial hold=2
        p_ = stats(run(df, is_crash, floor, 2, partial=True, cluster=True,
                        cooldown=3))
        fmt("PARTIAL h2 cluster+cd3", p_)
        # OOS halves (partial, hold=2, cluster+cd3)
        half = len(df) // 2
        for nm, sl in (("OOS1", slice(0, half)), ("OOS2", slice(half, None))):
            d = df.iloc[sl].reset_index(drop=True)
            o = stats(run(d, is_crash, floor, 2, partial=True, cluster=True,
                          cooldown=3))
            fmt(nm, o)
        # random control, count-matched to the spike book
        n_sp = len(run(df, is_crash, floor, 2, partial=True, cluster=True,
                       cooldown=3))
        rc = stats(random_control(df, is_crash, floor, 2, n_sp))
        fmt(f"RANDOM control (n~{n_sp})", rc)


if __name__ == "__main__":
    main()