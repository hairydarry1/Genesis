#!/usr/bin/env python3
"""
C-ANALYSIS — the lottery's knife edge, quantified
==================================================
For the wide-TP lottery (SL=1R, TP=K*R) the EV is:
    EV = P(first-touch -K*R before +1R) * K  -  (1 - P) * 1   (R units)
       = P*K - (1-P)
    > 0  <=>  P > 1/(K+1)  <=>  c := P*K > K/(K+1)  (~1 for large K)

c is the survival-race product. For an unbiased random walk c = K/(K+1)
(breakeven). c > breakeven means the price process is biased toward the
chase side during that window -> the lottery is +EV there.

This script:
  1. Full-sample c per symbol (first-touch race, no drift-artifact
     timeouts: only TP-vs-SL resolutions count).
  2. Rolling ~equal-size windows per symbol: c(K), P(K), EV with Wilson
     CIs at K=8 and K=15, flagging windows where EV > 0 credibly.
  3. Persistence check (does an EV>0 window have EV>0 neighbours?).
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E

KS = [3, 5, 8, 12, 20, 30]
HORIZON = 200


def race(df, sym, i, D, K):
    """First-touch of TP(-K*R) vs SL(+1R) for crash-SHORT / boom-LONG.
    Returns +1 if TP first, -1 if SL first, 0 if neither within HORIZON."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + D
    if e + 2 >= n:
        return None
    entry = closes[e]
    R = max(med[i], E.BROKER_FLOOR.get(sym, 1.0))
    if is_crash:  # SHORT: TP below at -K*R, SL above at +1R
        sl_level = entry + R
        tp_level = entry - K * R
    else:         # LONG: TP above, SL below
        sl_level = entry - R
        tp_level = entry + K * R
    for j in range(e + 1, min(e + 1 + HORIZON, n)):
        if is_crash:
            if highs[j] >= sl_level:
                return -1
            if lows[j] <= tp_level:
                return 1
        else:
            if lows[j] <= sl_level:
                return -1
            if highs[j] >= tp_level:
                return 1
    return 0


def collect(df, sym, D, idx=None):
    if idx is None:
        base = E.spike_rows(df, sym)
        idx = np.where(base)[0]
    res = {K: [] for K in KS}
    for i in idx:
        for K in KS:
            r = race(df, sym, i, D, K)
            if r is not None:
                res[K].append(r)
    return res


def wilson(k, n, z=1.96):
    if n == 0:
        return 0.0, 0.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return centre - half, centre + half


def stats_for(K, outcomes):
    a = np.array(outcomes, float)
    tp = (a == 1).sum()
    sl = (a == -1).sum()
    resolved = tp + sl
    n = len(a)
    p = tp / resolved if resolved else 0.0
    ev = p * K - (1 - p) if resolved else 0.0
    lo, hi = wilson(tp, resolved) if resolved else (0, 0)
    return {"n": n, "res": resolved, "p": p, "ev": ev, "c": p * K,
            "ev_lo": lo * K - (1 - lo), "ev_hi": hi * K - (1 - hi)}


if __name__ == "__main__":
    D = 7 if len(sys.argv) < 2 else int(sys.argv[1])
    print(f"Race: first touch of TP(-K*R) vs SL(+1R); entry D={D} bars after spike; "
          f"unresolved-in-{HORIZON} excluded (no drift artifact)")
    for key in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = E.load_prep(key)
        n = len(df)
        base = E.spike_rows(df, key)
        idx = np.where(base)[0]
        print(f"\n=== {key.upper()} full sample (n={len(idx)}) ===")
        print(f"  {'K':>4}{'P(TP|res)':>12}{'c=P*K':>8}{'EV(R)':>8}   (breakeven P=1/(K+1), EV>0 = lottery alive)")
        full = collect(df, key, D, idx)
        for K in KS:
            s = stats_for(K, full[K])
            be_p = 1 / (K + 1)
            flag = "  <<< +EV" if s["ev"] > 0 else ""
            print(f"  K={K:>3}{s['p']:>12.4f}{s['c']:>8.3f}{s['ev']:>+8.3f}  "
                  f"(be P={be_p:.3f}, c_be={K/(K+1):.3f}, res={s['res']}/{s['n']}){flag}")

        # rolling windows (6 equal slices)
        print(f"  --- rolling windows (6 x ~{n//6} bars) at K=8 and K=12 ---")
        cuts = np.linspace(0, n, 7).astype(int)
        for K in [8, 12]:
            row = f"  K={K:<3}"
            for w in range(6):
                d = df.iloc[cuts[w]:cuts[w + 1]].reset_index(drop=True)
                b = E.spike_rows(d, key)
                ii = np.where(b)[0]
                res = collect(d, key, D, ii)
                s = stats_for(K, res[K])
                mark = "+" if s["ev"] > 0 else " "
                mark2 = "*" if s["ev_lo"] > 0 else " "
                row += f"  w{w}:EV={s['ev']:+.2f}{mark}{mark2} (c={s['c']:.2f},n={s['res']})"
            print(row)
        # best window EVs at K=8 with CI
        for K in [8]:
            out = []
            for w in range(6):
                d = df.iloc[cuts[w]:cuts[w + 1]].reset_index(drop=True)
                ii = np.where(E.spike_rows(d, key))[0]
                res = collect(d, key, D, ii)
                s = stats_for(K, res[K])
                out.append(s)
            best = max(out, key=lambda s: s["ev"])
            print(f"  K=8 best window: EV={best['ev']:+.3f}R (CI [{best['ev_lo']:+.3f}, "
                  f"{best['ev_hi']:+.3f}]) c={best['c']:.3f} res={best['res']}")
            pos = sum(1 for s in out if s["ev"] > 0)
            adj = sum(1 for a, b in zip(out[:-1], out[1:]) if a["ev"] > 0 and b["ev"] > 0)
            print(f"  K=8 windows with EV>0: {pos}/6; adjacent EV>0 pairs: {adj}/5")