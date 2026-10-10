#!/usr/bin/env python3
"""
EDGE HUNT — OOS confirmation (the only part that matters).

Candidates to confirm:
  A2. Drift gate on the 1000s (no gate works there; a drift filter may be
      the missing quality filter). Threshold learned on the 1st half only.
  D2. Entry-bar delay on the real partial configs (crash1000 hold5 cd3,
      boom1000 hold2 cd3, boom500 hold2 cd1) — full-size scan said later
      entries win on the 1000s.
  E2. Crash body-ratio: low-body (rejected) spike bars faded better on
      both crash charts in the scan — ungated, larger sample, OOS.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, by_key, BROKER_FLOOR  # noqa: E402


def fmt(s):
    if not s or s["n"] == 0:
        return "none"
    return (f"n={s['n']:>5} WR={s['wr']:>5.1f}% PF={s['pf']:>6.2f} "
            f"avg={s['avg']:>+7.3f} DD={s['maxdd']:>6.1f}")


def cfg_of(key, gate=False):
    c = dict(by_key(key)["verified"])
    c["gate"] = gate
    return c


def drift_of(key, trades, closes, lookback=60):
    out = []
    for t in trades:
        i = t["entry_row"]
        ref = max(0, i - lookback)
        d = closes[i - 1] - closes[ref] if i - 1 >= 0 else np.nan
        out.append(d if "crash" in key else -d)
    return out


# ----------------------------------------------------------------------
def a2():
    print("\n" + "=" * 92)
    print("A2. DRIFT GATE (1000s + 500s) — threshold from 1st half, applied to both")
    print("=" * 92)
    for key in ("crash1000", "boom1000", "crash500", "boom500"):
        df = E.load_prep(key)
        n = len(df)
        cut = n // 2
        closes = df["close"].values
        cfg = cfg_of(key)
        print(f"\n{key} (cfg hold={cfg['hold']} cd={cfg['cooldown']} gate={cfg['gate']}):")
        for hname, d in (("1st half", df.iloc[:cut]), ("2nd half", df.iloc[cut:])):
            d = d.reset_index(drop=True)
            trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                            gate=cfg["gate"], df=d)
            if not trs:
                print(f"  {hname}: no trades"); continue
            dr = drift_of(key, trs, d["close"].values)
            all_s = E.outcome_stats(trs)
            thr = float(np.nanmedian(np.array([v for v in dr if v == v]))) if dr else 0.0
            sel = [t for t, v in zip(trs, dr) if v == v and v >= thr]
            hi_s = E.outcome_stats(sel)
            print(f"  {hname}: all   {fmt(all_s)}")
            print(f"  {hname}: drift>={thr:+.2f} {fmt(hi_s)}")


# partial trade with configurable entry offset (mirrors engine.partial_trade)
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
        pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
              0.5 * (pB - entry) * (1 if is_crash else -1)
        return {"pnl": pnl, "nhalves": 2}
    return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1), "nhalves": 1}


def run_partial_e(key, hold, cd, e_off, df):
    trs = []
    skip = 0
    idx = np.where(E.spike_rows(df, key))[0]
    last_two = []
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if len(last_two) >= 2 and (i - last_two[-2]) <= 10:
            last_two.append(i)
            continue
        last_two.append(i)
        if i + e_off + 2 >= len(df):
            break
        tr = partial_e(df, key, i, hold, e_off)
        if tr is None:
            continue
        trs.append(tr)
        if tr["pnl"] <= 0 and cd > 0:
            skip = cd
    return trs


def d2():
    print("\n" + "=" * 92)
    print("D2. ENTRY-BAR DELAY on REAL partial configs — OOS halves")
    print("=" * 92)
    plans = {"crash1000": {"hold": 5, "cd": 3, "offs": (1, 2, 3)},
             "boom1000": {"hold": 2, "cd": 3, "offs": (1, 2)},
             "boom500": {"hold": 2, "cd": 1, "offs": (1, 2, 3)},
             "crash500": {"hold": 2, "cd": 1, "offs": (1, 2)}}
    for key, p in plans.items():
        df = E.load_prep(key)
        cut = len(df) // 2
        print(f"\n{key} (hold={p['hold']} cd={p['cd']} partial):")
        for e_off in p["offs"]:
            halves = []
            for hname, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
                d = d.reset_index(drop=True)
                halves.append((hname, E.outcome_stats(
                    run_partial_e(key, p["hold"], p["cd"], e_off, d))))
            h1, h2 = halves
            print(f"  entry+{e_off}: 1st {fmt(h1[1])} | 2nd {fmt(h2[1])}")


def e2():
    print("\n" + "=" * 92)
    print("E2. CRASH BODY RATIO (ungated partial, larger n) — low body = faded spike")
    print("=" * 92)
    for key in ("crash300", "crash500"):
        df = E.load_prep(key)
        rng = df["range"].values
        body = np.abs(df["close"].values - df["open"].values) / np.maximum(rng, 1e-9)
        cfg = cfg_of(key, gate=False)
        cut = len(df) // 2
        print(f"\n{key} (cfg hold={cfg['hold']} cd={cfg['cooldown']} UNGATED):")
        for hname, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
            d = d.reset_index(drop=True)
            trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                            gate=False, df=d)
            if not trs:
                continue
            vals = [body[t["entry_row"]] for t in trs]
            q = np.quantile(vals, 0.5)
            lo = [t for t, v in zip(trs, vals) if v <= q]
            hi = [t for t, v in zip(trs, vals) if v > q]
            print(f"  {hname}: low-body {fmt(E.outcome_stats(lo))}")
            print(f"  {hname}: high-body {fmt(E.outcome_stats(hi))}")


if __name__ == "__main__":
    a2()
    d2()
    e2()
