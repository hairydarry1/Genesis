#!/usr/bin/env python3
"""
VOL400 ARSENAL — the full Boom/Crash edge battery applied to the Vol Up index
=============================================================================
Runs every concept family we developed for Boom/Crash, on the Spot Up -
Volatility Up Index (floor 10.0, spread 0), with corrected-engine accounting
and random-entry controls wherever a direction bias could fake an edge.

Sections:
  1. PARTIAL — does half-entry (add B if A profitable) improve the e+2 short?
  2. LOTTERY — c-curve (P(win at K·R) x K) both directions, clean race + timeout
  3. ROLLING-C — does c ever exceed breakeven in rolling windows?
  4. CLUSTER — cluster-gate sensitivity on the short (aftershock clusters)
  5. TIME — entry-hour PnL filter
  6. GRIND — long-side grind/dip strategies + random controls
  7. AC1 — small-bar autocorrelation / drift momentum
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
import partial_m1_corrected as eng

FLOOR = 10.0
DFILE = Path("../data/vol400_Spot_Up__Volatility_Up_Index_1min.parquet")


def load():
    df = pd.read_parquet(DFILE)
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def short_half(closes, highs, lows, rng, med, adv, is_crash, entry, sl_d, e, h, n):
    """Walk ONE half of a SHORT: SL above, TP below, gap-through on adv bars."""
    return eng.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, entry + sl_d, entry - 2 * sl_d, e, h, n)


def short_partial(df, i, entry_off=2, hold=2):
    """Half A at close[e], add half B at close[e+1] if A open & profitable."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    e = i + entry_off
    if e + 3 >= n:
        return None
    entry = closes[e]
    sl_d = max(med[i], FLOOR)
    pA, rA = short_half(closes, highs, lows, rng, med, adv, is_crash,
                        entry, sl_d, e + 1, hold, n)
    # is A still open at close[e+1]? (didn't exit on bar e+1)
    pA1, rA1 = short_half(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_d, e + 1, 1, n)
    add = rA1 == "timeout" and closes[e + 1] < entry  # profitable short
    if add:
        pB, rB = short_half(closes, highs, lows, rng, med, adv, is_crash,
                            entry, sl_d, e + 2, hold - 1, n)
        return 0.5 * (entry - pA) + 0.5 * (entry - pB), 2
    return 0.5 * (entry - pA), 1


def section1(df):
    print("\n=== 1. PARTIAL ENTRY on the e+2 short (add B if profitable) ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    full, part = [], []
    for i in big:
        e = i + 2
        if e + 3 >= n:
            continue
        entry = closes[e]
        sl_d = max(med[i], FLOOR)
        p, r = short_half(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_d, e + 1, 2, n)
        full.append(entry - p)
        pt = short_partial(df, i, 2, 2)
        if pt:
            part.append(pt[0])
    full = np.array(full); part = np.array(part)
    for name, arr in (("full 2.5x e+2", full), ("partial e+2  ", part)):
        w = arr[arr > 0]; l = arr[arr <= 0]
        pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
        print(f"  {name}: n={len(arr):4d} WR={100*(arr>0).mean():5.1f}% PF={pf:5.2f} "
              f"EV={arr.mean():+.3f} worst={arr.min():+.1f}")
    # add-rate
    adds = sum(1 for i in big if short_partial(df, i, 2, 2) and short_partial(df, i, 2, 2)[1] == 2)
    print(f"  half-B add rate: {adds}/{len(big)} = {100*adds/len(big):.0f}%")
    # OOS halves of partial
    mid = n // 2
    for half, (lo, hi) in (("OOS1", (0, mid)), ("OOS2", (mid, n))):
        arr = np.array([short_partial(df, i, 2, 2)[0] for i in big
                        if lo <= i < hi and short_partial(df, i, 2, 2)])
        w = arr[arr > 0]; l = arr[arr <= 0]
        pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
        print(f"  partial {half}: n={len(arr):4d} WR={100*(arr>0).mean():5.1f}% "
              f"PF={pf:5.2f} EV={arr.mean():+.3f}")


def section2(df):
    print("\n=== 2. LOTTERY c-CURVE (clean race: +1R stop vs -K*R TP) ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    rngs = np.random.RandomState(7)
    rand = np.sort(rngs.choice(np.where(ok)[0], size=2000, replace=False))
    maxbars = 30

    def race(idxs, side, K):
        """First touch of +1R (bad) vs -K*R (good) for a SHORT; mirror for LONG.
        Returns array of outcomes in R units (positive = win)."""
        out = []
        for i in idxs:
            e = i + 2
            if e + 1 >= n:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR)
            if side == "short":
                sl, tp = entry + R, entry - K * R
            else:
                sl, tp = entry - R, entry + K * R
            hit = None
            for j in range(e + 1, min(e + 1 + maxbars, n)):
                if side == "short":
                    if highs[j] >= sl:
                        hit = -1; break
                    if lows[j] <= tp:
                        hit = K; break
                else:
                    if lows[j] <= sl:
                        hit = -1; break
                    if highs[j] >= tp:
                        hit = K; break
            if hit is None:
                out.append(0.0)  # timeout -> 0 (race only counts touches)
            else:
                out.append(hit)
        return np.array(out)

    print("  (short = fading the up-discharge; long = catching the up-move)")
    for side in ("short", "long"):
        for K in (2, 3, 5, 8, 12, 20, 30):
            o_big = race(big, side, K)
            o_rand = race(rand[:len(big)], side, K)
            w = o_big[o_big > 0]
            pwin = len(w) / len(o_big)
            c = pwin * K
            be = K / (K + 1)
            ev = (o_big.mean())
            print(f"  {side:5s} K={K:2d}: P(win)={pwin:.3f} c={c:.3f} "
                  f"(breakeven {be:.3f}) EV={ev:+.2f}R  | random c={pwin*K:.3f}")


def section3(df):
    print("\n=== 3. ROLLING C (6 windows, K=8) ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    K = 8
    edges = np.linspace(0, n, 7).astype(int)
    for t in range(6):
        lo, hi = edges[t], edges[t + 1]
        idxs = [i for i in big if lo <= i < hi]
        wins = 0
        for i in idxs:
            e = i + 2
            if e + 1 >= n:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR)
            sl, tp = entry + R, entry - K * R
            for j in range(e + 1, min(e + 1 + 30, n)):
                if highs[j] >= sl:
                    break
                if lows[j] <= tp:
                    wins += 1
                    break
        pwin = wins / len(idxs) if idxs else 0
        print(f"  window{t+1}: n={len(idxs):3d} P(win)={pwin:.3f} c={pwin*K:.3f} "
              f"(breakeven {K/(K+1):.3f})")


def section4(df):
    print("\n=== 4. CLUSTER-GATE SENSITIVITY (skip if N+ discharges in W bars) ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    for N, W in ((3, 5), (3, 10), (2, 3), (4, 10)):
        pnls = []
        for k, i in enumerate(big):
            if k >= N - 1 and (i - big[k - (N - 1)]) <= W:
                continue
            e = i + 2
            if e + 2 >= n:
                continue
            entry = closes[e]
            sl_d = max(med[i], FLOOR)
            p, r = short_half(closes, highs, lows, rng, med, adv, is_crash,
                              entry, sl_d, e + 1, 2, n)
            pnls.append(entry - p)
        pnls = np.array(pnls)
        w = pnls[pnls > 0]; l = pnls[pnls <= 0]
        pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
        print(f"  skip if {N}+ in {W} bars: n={len(pnls):4d} WR={100*(pnls>0).mean():5.1f}% "
              f"PF={pf:5.2f} EV={pnls.mean():+.3f}")


def section5(df):
    print("\n=== 5. ENTRY-HOUR FILTER ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    big = np.where((rng > 2.5 * med) & ok)[0]
    hour = df["time"].dt.hour.values
    buckets = {}
    for i in big:
        e = i + 2
        if e + 2 >= n:
            continue
        entry = closes[e]
        sl_d = max(med[i], FLOOR)
        p, r = short_half(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_d, e + 1, 2, n)
        h = hour[i]
        buckets.setdefault(h, []).append(entry - p)
    for h in sorted(buckets):
        arr = np.array(buckets[h])
        print(f"  hour {h:2d}: n={len(arr):4d} EV={arr.mean():+.3f}")
    # variance across hours vs pooled
    evs = [np.mean(v) for v in buckets.values() if len(v) >= 5]
    print(f"  hour EVs: min={min(evs):+.2f} max={max(evs):+.2f} "
          f"sd={np.std(evs):.2f} vs pooled EV={np.mean(evs):+.2f}")


def section6(df):
    print("\n=== 6. GRIND / DIP LONG STRATEGIES (secular uptrend) ===")
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = eng.arrays(df, "vol400")
    up = (df["close"] > df["open"]).values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    # dip = bar with close < open and range < 1.5x med (small down bar)
    dip = np.where((~up) & (rng < 1.5 * med) & ok)[0]
    # deep dip = after 3+ consecutive small down bars
    consec = np.zeros(n, dtype=bool)
    cnt = 0
    for i in range(n):
        if ok[i] and not up[i] and rng[i] < 1.5 * med[i]:
            cnt += 1
            consec[i] = cnt >= 3
        else:
            cnt = 0
    deep = np.where(consec)[0]

    def long_half(entry, R, e, h):
        """Proper LONG walk: SL below, TP above, gap-through on DOWN discharges."""
        sl_l, tp_l = entry - R, entry + 2 * R
        for j in range(e, min(e + h, n)):
            if lows[j] <= sl_l:
                # gap-through on adverse (down) discharge bars
                if (not adv[j]) and rng[j] > 3 * med[j]:
                    return lows[j]
                return sl_l
            if highs[j] >= tp_l:
                return tp_l
        return closes[min(e + h - 1, n - 1)]

    def long_run(idxs, entry_off, hold, sl_mult, tp_mult):
        pnls = []
        for i in idxs:
            e = i + entry_off
            if e + hold >= n:
                continue
            entry = closes[e]
            R = max(med[i], FLOOR) * sl_mult
            p = long_half(entry, R, e + 1, hold)
            pnls.append(p - entry)
        pnls = np.array(pnls)
        w = pnls[pnls > 0]; l = pnls[pnls <= 0]
        pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
        return pnls, pf

    for name, idxs in (("after 1 small down bar ", dip), ("after 3+ small down bars", deep)):
        for entry_off, hold, sl_m, tp_m in ((1, 5, 3.0, 1.0), (1, 10, 3.0, 1.0), (1, 20, 2.0, 1.0)):
            pnls, pf = long_run(idxs, entry_off, hold, sl_m, tp_m)
            print(f"  LONG {name} e+{entry_off} h={hold:2d} SL={sl_m:.0f}R: "
                  f"n={len(pnls):5d} WR={100*(pnls>0).mean():5.1f}% PF={pf:5.2f} "
                  f"EV={pnls.mean():+.3f} worst={pnls.min():+.1f}")
    # random control: same long config on random bars
    rngs = np.random.RandomState(3)
    for rep in range(3):
        r = np.sort(rngs.choice(np.where(ok)[0], size=len(dip), replace=False))
        pnls, pf = long_run(r, 1, 10, 3.0, 1.0)
        print(f"  LONG random control#{rep+1} e+1 h=10 SL=3R: n={len(pnls):5d} "
              f"WR={100*(pnls>0).mean():5.1f}% PF={pf:5.2f} EV={pnls.mean():+.3f}")


def section7(df):
    print("\n=== 7. AC1 / DRIFT MOMENTUM ===")
    closes = df["close"].values
    moves = np.diff(closes)
    rng = df["range"].values; med = df["median_range"].values
    ok = np.isfinite(med) & (med > 0) & (rng > 0.2)
    small = (rng[:-1] < 1.5 * med[:-1]) & ok[:-1]
    sm = moves[small]
    print(f"  small-bar move AC1: {np.corrcoef(sm[:-1], sm[1:])[0,1]:+.3f} (n={len(sm):,})")
    bigm = (rng[:-1] >= 1.5 * med[:-1]) & ok[:-1]
    bm = moves[bigm]
    print(f"  big-bar move AC1:   {np.corrcoef(bm[:-1], bm[1:])[0,1]:+.3f} (n={len(bm):,})")
    # drift of small bars: mean move
    print(f"  mean small-bar move: {sm.mean():+.4f}  mean big-bar move: {bm.mean():+.4f}")
    print(f"  P(small bar up): {(sm > 0).mean():.3f}")


if __name__ == "__main__":
    df = load()
    section1(df)
    section2(df)
    section3(df)
    section4(df)
    section5(df)
    section6(df)
    section7(df)