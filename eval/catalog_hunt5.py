#!/usr/bin/env python3
"""HUNT 5 - pyramid a 3rd slice on sustained drift.

Canonical partial = 50% at e+1 + 50% at e+1 if profitable (total 100%).
Pyramid variant: A=50% at e+1; B=25% joins at e+1 if A profitable; C=25%
joins at e+2 close only if the position is STILL profitable then AND no
adverse 3x+ spike printed on bars e+1 or e+2 (never add into an active
cluster). All slices share the entry price of the first half (same levels),
windows staggered. Compares PnL/DD on gated charts + boom1000.
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, BROKER_FLOOR  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from partial_m1_corrected import half_exit, arrays  # noqa: E402


def ver(key):
    return dict([s for s in SYMBOLS if s["key"] == key][0]["verified"])


def f(trs):
    if not trs:
        return "none"
    p = np.array([t["pnl"] for t in trs])
    w = p[p > 0]; l = p[p <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) else 999
    eq = np.cumsum(p); dd = (np.maximum.accumulate(eq) - eq).max()
    return (f"n={len(p):>5} WR={len(w)/len(p)*100:>5.1f}% PF={pf:>6.2f} "
            f"PnL={p.sum():>+9.1f} avg={p.mean():>+6.3f} DD={dd:>7.1f}")


def run_pyr(df, key, mask, hold, entry_bar, cooldown, third=False):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, key)
    base = E.spike_rows(df, key).astype(bool)
    idx = np.where(mask)[0]
    trades, skip = [], 0
    last_two = []
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if len(last_two) >= 2 and (i - last_two[-2]) <= 10:
            last_two.append(i)
            continue
        e = i + entry_bar
        if e + 2 >= n:
            break
        entry = closes[e]
        sd = max(med[i], BROKER_FLOOR.get(key, 1.0))
        sl = (entry - sd) if is_crash else (entry + sd)
        tp = (entry + 2 * sd) if is_crash else (entry - 2 * sd)
        sign = 1 if is_crash else -1
        # A: 0.5 at e+1 window e+1..e+hold
        pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl, tp, e + 1, hold, n)
        fav1 = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
        spike_e1 = bool(base[e + 1]) if e + 1 < n else True
        pnl = 0.5 * (pA - entry) * sign
        if not third:
            if rA == "timeout" and fav1:
                pB, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                  entry, sl, tp, e + 2, max(1, hold - 1), n)
                pnl = 0.5 * (pA - entry) * sign + 0.5 * (pB - entry) * sign
        else:
            # B 0.25 joins at e+1 if A didn't die on e+1 and fav1
            b_join = fav1 and e + 2 < n
            wA = 0.5
            if b_join:
                pB, rB = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                                   entry, sl, tp, e + 2, max(1, hold - 1), n)
                # C 0.25 joins at e+2 if B still open, still favourable, no
                # adverse spike on e+1/e+2
                c_join = (rB == "timeout" and e + 3 < n and
                          (closes[e + 2] > entry) if is_crash
                          else (rB == "timeout" and e + 3 < n and
                                closes[e + 2] < entry))
                if c_join:
                    spike_win = bool(base[e + 1]) or bool(base[e + 2])
                    c_join = not spike_win
                if c_join:
                    pC, _ = half_exit(closes, highs, lows, rng, med, adv,
                                      is_crash, entry, sl, tp, e + 3,
                                      max(1, hold - 2), n)
                    pnl = 0.5 * (pA - entry) * sign + \
                          0.25 * (pB - entry) * sign + \
                          0.25 * (pC - entry) * sign
                    wA = 0.75
                else:
                    pnl = 0.5 * (pA - entry) * sign + 0.25 * (pB - entry) * sign
                    wA = 0.75
            # if no B join, add B weight back onto A exposure
            if not b_join:
                pnl = 0.75 * (pA - entry) * sign
        last_two.append(i)
        trades.append({"pnl": pnl, "entry_row": i})
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def main():
    for key in KEYS_ALL:
        pass


KEYS_ALL = ["crash300", "crash500", "boom500", "boom1000"]
if __name__ == "__main__":
    for key in KEYS_ALL:
        cfg = ver(key)
        df = E.load_prep(key)
        cut = len(df) // 2
        base = E.spike_rows(df, key).astype(bool)
        if cfg.get("gate", False):
            mask = E.gate_mask(df, key, 8.0, 3, base_mask=base)
        else:
            mask = base
        hold, cd, eb = cfg["hold"], cfg["cooldown"], cfg.get("entry_bar", 1)
        print("=" * 100)
        print(f"{key} (hold={hold} cd={cd} eb={eb} gate={cfg.get('gate', False)})")
        print("=" * 100)
        c2 = run_pyr(df, key, mask, hold, eb, cd, third=False)
        c3 = run_pyr(df, key, mask, hold, eb, cd, third=True)
        print(f"  canonical partial  {f(c2)}")
        print(f"  pyramid (3rd add)  {f(c3)}")
        for nm, fn in (("partial", False), ("pyr", True)):
            for hname, slc in (("1st", slice(0, cut)), ("2nd", slice(cut, None))):
                d = df.iloc[slc].reset_index(drop=True)
                b = E.spike_rows(d, key).astype(bool)
                if cfg.get("gate", False):
                    m = E.gate_mask(d, key, 8.0, 3, base_mask=b)
                else:
                    m = b
                t = run_pyr(d, key, m, hold, eb, cd, third=fn)
                print(f"  {nm:<7} {hname}: {f(t)}")
        print()
