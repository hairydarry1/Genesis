#!/usr/bin/env python3
"""
CATALOG HUNT 2 - executable "cluster-end confirmation" fades.

T3 (catalog_hunt.py) found, in hindsight, that fades entered after the LAST
>=8x spike of a run vastly outperform fades entered after every mid-cluster
member (crash500: PF 8.2 vs canonical 1.65). The executable formulation:
wait `w` bars after a big spike; if NO new qualifying spike arrives within
the window, enter at the close of bar i+w (the cluster has ENDED). If one
arrives, it replaces the candidate (mid-cluster entries never taken).

Variants (all no-lookahead, canonical accounting - partial entry, hold and
cooldown per symbol from the verified configs):
  CONTROL    : engine.run_cfg with the verified config (gate on 300/500)
  V_D3       : entry+3 delay on ALL 3x+ spikes, no gate (delay-only)
  V_C3x      : confirmation window 3 vs ANY 3x+ spike, enter close[i+3]
  V_C3B      : confirmation window 3 vs only >=8x spikes, enter close[i+3]
Cross-check: custom runner reproduced against engine gate output first.
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

KEYS = [s["key"] for s in SYMBOLS]


def ver(key):
    return dict([s for s in SYMBOLS if s["key"] == key][0]["verified"])


def my_run(df, key, mask, hold, entry_bar, cooldown):
    """Mirror engine.run_cfg semantics (incl. cluster-10 skip on mask rows)
    but with an arbitrary entry mask. Trades carry pnl/nhalves/entry_row."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, key)
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
        pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl, tp, e + 1, hold, n)
        pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                             entry, sl, tp, e + 1, 1, n)
        addb = False
        if rA1 == "timeout":
            fav = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
            addb = fav
        if addb:
            pB, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                              entry, sl, tp, e + 2, max(1, hold - 1), n)
            pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
                  0.5 * (pB - entry) * (1 if is_crash else -1)
        else:
            pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
        last_two.append(i)
        trades.append({"pnl": pnl, "entry_row": i})
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def confirm_mask(df, key, quiet_vs="3x", w=3):
    """Executable post-big-spike confirmation mask: row i qualifies if it is
    a >=8x adverse spike and no qualifying spike (>=3x or >=8x) lands in
    (i, i+w]. Entry happens at close[i+w] -> entry_bar = w+1 in my_run."""
    base = E.spike_rows(df, key).astype(bool)
    med = df["median_range"].values
    rng = df["range"].values
    ratio = np.where(np.isfinite(med) & (med > 0), rng / np.maximum(med, 1e-9), 0)
    big = base & (ratio >= 8.0)
    watch = base if quiet_vs == "3x" else big
    out = np.zeros(len(df), dtype=bool)
    idx_big = np.where(big)[0]
    wrow = np.where(watch)[0]
    # for each big spike i: qualifies iff no watch-spike in (i, i+w]
    import bisect
    for i in idx_big:
        j = bisect.bisect_right(wrow, i)
        if j < len(wrow) and wrow[j] <= i + w:
            continue          # follow-up arrived -> cluster not over
        out[i] = True
    return out


def f(trs):
    if not trs:
        return "none"
    p = np.array([t["pnl"] for t in trs])
    w = p[p > 0]; l = p[p <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) else 999
    eq = np.cumsum(p); dd = (np.maximum.accumulate(eq) - eq).max()
    return (f"n={len(p):>5} WR={len(w)/len(p)*100:>5.1f}% PF={pf:>6.2f} "
            f"PnL={p.sum():>+9.1f} avg={p.mean():>+6.3f} DD={dd:>7.1f}")


def main():
    for key in KEYS:
        cfg = ver(key)
        df = E.load_prep(key)
        n = len(df)
        cut = n // 2
        base = E.spike_rows(df, key).astype(bool)
        med = df["median_range"].values
        rng = df["range"].values
        ratio = np.where(np.isfinite(med) & (med > 0),
                         rng / np.maximum(med, 1e-9), 0)
        big = base & (ratio >= 8.0)

        print("=" * 100)
        print(f"{key}  (verified: hold={cfg['hold']} cd={cfg['cooldown']} "
              f"eb={cfg.get('entry_bar',1)} gate={cfg.get('gate',False)})")
        print("=" * 100)
        # control: exact engine verified config (full)
        ctl = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                        partial=True, gate=cfg.get("gate", False),
                        gate_mult=8.0, gate_window=3,
                        entry_bar=cfg.get("entry_bar", 1), df=df)
        print(f"  CONTROL (engine, verified)     {f(ctl)}")
        # custom-runner cross-check: reproduce gate set
        gmask = E.gate_mask(df, key, 8.0, 3, base_mask=base)
        mine = my_run(df, key, gmask, cfg["hold"], 1, cfg["cooldown"])
        eng_g = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                          partial=True, gate=True, gate_mult=8.0,
                          gate_window=3, entry_bar=1, df=df)
        same = (len(mine) == len(eng_g) and
                np.allclose([t["pnl"] for t in mine],
                            [t["pnl"] for t in eng_g]))
        print(f"  [cross-check gate runner identical: {same}]")

        hold, cd = cfg["hold"], cfg["cooldown"]
        variants = []
        # V_D3: delay-only entry+3 on all 3x spikes (no gate, no confirm)
        d3 = my_run(df, key, base, hold, 3, cd)
        variants.append(("V_D3 delay+3 all spikes", d3))
        # V_C3x: confirm 3 quiet bars vs any 3x+ spike -> enter close[i+3]
        m3x = confirm_mask(df, key, "3x", 3)
        v3x = my_run(df, key, m3x, hold, 4, cd) if m3x.any() else []
        variants.append(("V_C3x confirm-vs-3x w=3", v3x))
        # V_C3B: confirm 3 quiet bars vs only >=8x -> enter close[i+3]
        m3b = confirm_mask(df, key, "8x", 3)
        v3b = my_run(df, key, m3b, hold, 4, cd) if m3b.any() else []
        variants.append(("V_C3B confirm-vs-8x w=3", v3b))
        # V_C2x: confirm 2 quiet bars vs 3x -> enter close[i+2]
        m2x = confirm_mask(df, key, "3x", 2)
        v2x = my_run(df, key, m2x, hold, 3, cd) if m2x.any() else []
        variants.append(("V_C2x confirm-vs-3x w=2", v2x))
        # V_C3xh: confirm + longer hold (crash300 drift runs to 3+ bars)
        if key == "crash300":
            m = confirm_mask(df, key, "3x", 3)
            v = my_run(df, key, m, max(3, hold), 4, cd) if m.any() else []
            variants.append(("V_C3x crash300 hold3", v))

        for name, trs in variants:
            print(f"  {name:<28} {f(trs)}")

        # OOS halves for the best-looking confirm variant
        print("  -- OOS halves --")
        for name, trs in (("V_C3x", v3x), ("V_C3B", v3b), ("V_D3", d3)):
            for hname, slc in (("1st", slice(0, cut)), ("2nd", slice(cut, None))):
                d = df.iloc[slc].reset_index(drop=True)
                if name == "V_C3x":
                    mm = confirm_mask(d, key, "3x", 3)
                    t = my_run(d, key, mm, hold, 4, cd) if mm.any() else []
                elif name == "V_C3B":
                    mm = confirm_mask(d, key, "8x", 3)
                    t = my_run(d, key, mm, hold, 4, cd) if mm.any() else []
                else:
                    t = my_run(d, key, E.spike_rows(d, key).astype(bool),
                               hold, 3, cd)
                print(f"  {name:<7} {hname}: {f(t)}")
            print()


if __name__ == "__main__":
    main()
