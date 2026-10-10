#!/usr/bin/env python3
"""
LOTTERY-C MONITOR — is the wide-TP spike lottery +EV right now?
================================================================
The chase lottery (SL=1R, TP=K*R) is +EV iff c := P(TP before SL) * K
exceeds K/(K+1) (~1). Full-history c never exceeds breakeven on any
symbol, but crash300 showed ONE rolling window at c=1.12 (K=12). If a
real c>1 regime ever persists, the lottery is tradable there — this
monitor measures c daily over the recent window so the regime can't be
missed (docs/CHASE_SPIKE_VERDICT Part 6).

Race = first touch of TP(-K*R) vs SL(+1R) on the chase side (crash:
SHORT the discharge, boom: LONG), entry D bars after a 3x spike.
Unresolved-in-HORIZON races are excluded (no secular-drift artifact).
"""
import numpy as np

from engine import load_prep, datetimes, arrays, spike_rows
from config import by_key

KS = [8, 12]
D = 7
HORIZON = 200
BREAKEVEN = {K: K / (K + 1.0) for K in KS}


def lottery_state(key, df=None):
    """Measure the recent-window (or full if short) lottery race for a symbol."""
    from config import BROKER_FLOOR
    if df is None:
        df = load_prep(key)
    n = len(df)
    if n == 0:
        return None
    # use the most recent third when no window requested (keeps n meaningful)
    cut = int(n * 0.7)
    d = df.iloc[cut:].reset_index(drop=True)
    closes, highs, lows, rng, med, adv, is_crash = arrays(d, key)
    base = spike_rows(d, key)
    idx = np.where(base)[0]
    floor = BROKER_FLOOR.get(key, 1.0)
    out = {}
    for K in KS:
        tp = sl = 0
        for i in idx:
            e = i + D
            if e + 2 >= len(closes):
                continue
            entry = closes[e]
            R = max(med[i], floor)
            sl_level = (entry + R) if is_crash else (entry - R)
            tp_level = (entry - K * R) if is_crash else (entry + K * R)
            for j in range(e + 1, min(e + 1 + HORIZON, len(closes))):
                if is_crash:
                    hit_sl = highs[j] >= sl_level
                    hit_tp = lows[j] <= tp_level
                else:
                    hit_sl = lows[j] <= sl_level
                    hit_tp = highs[j] >= tp_level
                if hit_sl:
                    sl += 1
                    break
                if hit_tp:
                    tp += 1
                    break
        res = tp + sl
        p = tp / res if res else 0.0
        c = p * K
        be = BREAKEVEN[K]
        ev = p * K - (1 - p) if res else 0.0
        out[K] = {"n_spikes": len(idx), "resolved": res, "tp": tp, "sl": sl,
                  "p_win": p, "c": c, "breakeven_c": be, "ev": ev,
                  "alive": res >= 100 and c > be}
    return out


def verdict(key, st):
    if not st:
        return "NO DATA"
    alive = [K for K in KS if st[K]["alive"]]
    if alive:
        return f"LOTTERY OPEN at K={alive} (c > breakeven in recent window) — MONITOR"
    cs = [st[K]["c"] for K in KS]
    best = max(cs)
    if best > 0.85:
        return f"WATCH (c up to {best:.2f}, below breakeven {st[KS[1]]['breakeven_c']:.2f})"
    return "DEAD"


if __name__ == "__main__":
    import json
    from config import SYMBOLS
    for s in SYMBOLS:
        key = s["key"]
        st = lottery_state(key)
        print(f"{key}: " + json.dumps({str(K): {k: round(v, 3) if isinstance(v, float) else v
                                                  for k, v in st[K].items()}
                                       for K in KS}, default=str))
        print(f"   -> {verdict(key, st)}")
