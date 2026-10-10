#!/usr/bin/env python3
"""
FINAL CONFIGS v2 — combined candidates vs current baselines, OOS halves.

Candidates (from edge_hunt_v2 + edge_hunt_oos, mechanism-driven, both
halves better individually):
  crash1000 : entry+3  (baseline entry+1), hold5 cd3 partial
              + optional drift gate (>= median pre-spike 1h drift)
  boom1000  : entry+2, hold2 cd3 partial + optional drift gate
  boom500   : gate ON, entry+3 vs entry+1, hold2 cd1 partial
  crash500 / crash300 gated: untouched (entry delay & drift hurt there)
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import by_key, BROKER_FLOOR  # noqa: E402


def fmt(s):
    if not s or s["n"] == 0:
        return "none"
    return (f"n={s['n']:>5} WR={s['wr']:>5.1f}% PF={s['pf']:>6.2f} "
            f"avg={s['avg']:>+7.3f} DD={s['maxdd']:>6.1f}")


def partial_e(df, sym, i, hold, e_off):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + e_off
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, rA = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, hold, n)
    pA1, rA1 = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, e + 1, 1, n)
    add_b = (rA1 == "timeout" and
             ((closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)))
    if add_b:
        pB, rB = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl_level, tp_level, e + 2, hold - 1, n)
        return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1) +
                0.5 * (pB - entry) * (1 if is_crash else -1), "nhalves": 2,
                "entry_row": i}
    return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1), "nhalves": 1,
            "entry_row": i}


def run(key, hold, cd, e_off, df, gate=False, gate_mult=8.0, gate_window=3,
        drift_thr=None):
    is_crash = "crash" in key
    closes = df["close"].values
    base = E.spike_rows(df, key)
    if gate:
        keep = E.gate_mask(df, key, gate_mult, gate_window, base_mask=base)
        idx = np.where(keep)[0]
    else:
        idx = np.where(base)[0]
    trs, skip, last_two = [], 0, []
    n = len(df)
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if len(last_two) >= 2 and (i - last_two[-2]) <= 10:
            last_two.append(i)
            continue
        last_two.append(i)
        if i + e_off + 2 >= n:
            break
        # drift gate (feature computed strictly from pre-spike bars)
        if drift_thr is not None:
            ref = max(0, i - 60)
            d = closes[i - 1] - closes[ref]
            if is_crash:
                d = -d  # crash favorable drift = down? no: crash fade is LONG.
            # crash fade profits from UP drift; boom fade profits from DOWN drift
            fav = (closes[i - 1] - closes[ref]) if is_crash else -(closes[i - 1] - closes[ref])
            if fav < drift_thr:
                continue
        tr = partial_e(df, key, i, hold, e_off)
        if tr is None:
            continue
        trs.append(tr)
        if tr["pnl"] <= 0 and cd > 0:
            skip = cd
    return trs


def pair(key, hold, cd, e_off, gate, label, drift_thr=None):
    df = E.load_prep(key)
    cut = len(df) // 2
    print(f"\n{label}")
    for hname, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
        d = d.reset_index(drop=True)
        print(f"  {hname}: {fmt(E.outcome_stats(run(key, hold, cd, e_off, d, gate=gate, drift_thr=drift_thr)))}")


if __name__ == "__main__":
    pair("crash1000", 5, 3, 1, False, "crash1000 baseline  entry+1 hold5 cd3 partial")
    pair("crash1000", 5, 3, 3, False, "crash1000 CANDIDATE entry+3 hold5 cd3 partial")
    # drift threshold: median of favorable 1h drift on 1st half of full df
    df = E.load_prep("crash1000")
    closes = df["close"].values
    cut0 = len(df) // 2
    fav = np.array([closes[i] - closes[max(0, i - 60)]
                    for i in range(60, cut0)])
    thr = float(np.median(fav))
    pair("crash1000", 5, 3, 3, False, f"crash1000 + drift gate (fav drift >= {thr:+.1f}pts)", drift_thr=thr)

    pair("boom1000", 2, 3, 1, False, "boom1000 baseline  entry+1 hold2 cd3 partial")
    pair("boom1000", 2, 3, 2, False, "boom1000 CANDIDATE entry+2 hold2 cd3 partial")
    df = E.load_prep("boom1000")
    closes = df["close"].values
    cut0 = len(df) // 2
    fav = np.array([-(closes[i] - closes[max(0, i - 60)])
                    for i in range(60, cut0)])
    thr = float(np.median(fav))
    pair("boom1000", 2, 3, 2, False, f"boom1000 + drift gate (fav drift >= {thr:+.1f}pts)", drift_thr=thr)

    pair("boom500", 2, 1, 1, True, "boom500 baseline  gate entry+1 hold2 cd1 partial")
    pair("boom500", 2, 1, 3, True, "boom500 CANDIDATE gate entry+3 hold2 cd1 partial")
    pair("crash500", 2, 1, 1, True, "crash500 baseline gate entry+1 (control)")
    pair("crash300", 2, 1, 1, True, "crash300 baseline gate entry+1 (control)")
