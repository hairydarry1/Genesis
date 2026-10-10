#!/usr/bin/env python3
"""HUNT 4 - executable mid-run removal on gated 300/500 fades.

Canonical gate mixes run-FINAL entries (PF 6-9, WR ~92%) with run-MID
entries (PF 0.13-0.24) - mids are only ~15% of trades but halve PnL and
triple DD. Mids = entries whose >=8x run continues >=1 spike after entry.

Rules tested (all no-lookahead; control = canonical engine gate):
  G0  canonical gate (engine, control)
  G1  gate AND <=2 adverse(3x+) spikes in prior 5 bars  (not in a discharge)
  G2  gate AND <=1 adverse spike in prior 3 bars        (strict calm)
  G4  isolated-big: >=8x spike with NO >=8x in prior 3 bars (no gate)
  G6  G1 with spacing: also require >=3 bars since the previous big spike
Report full + OOS halves. Track how many mid-run trades each rule removes.
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


def my_run(df, key, mask, hold, entry_bar, cooldown, cluster_win=10):
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
        if len(last_two) >= 2 and (i - last_two[-2]) <= cluster_win:
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


def prior_counts(df, key):
    """For every row: # adverse 3x+ spikes in prior w bars and # >=8x in
    prior w bars (cheap sliding windows on the spike index)."""
    base = E.spike_rows(df, key).astype(bool)
    med = df["median_range"].values
    rng = df["range"].values
    ratio = np.where(np.isfinite(med) & (med > 0),
                     rng / np.maximum(med, 1e-9), 0)
    big = base & (ratio >= 8.0)
    n = len(df)
    idx_base = np.where(base)[0]
    idx_big = np.where(big)[0]
    cb = np.searchsorted(idx_base, np.arange(n))      # # base spikes before row
    cB = np.searchsorted(idx_big, np.arange(n))
    return cb, cB, idx_base, idx_big


def main():
    for key in ("crash300", "crash500", "boom500"):
        cfg = ver(key)
        df = E.load_prep(key)
        n = len(df)
        cut = n // 2
        cb, cB, idx_base, idx_big = prior_counts(df, key)
        base = E.spike_rows(df, key).astype(bool)
        med = df["median_range"].values
        rng = df["range"].values
        ratio = np.where(np.isfinite(med) & (med > 0),
                         rng / np.maximum(med, 1e-9), 0)
        big = base & (ratio >= 8.0)
        gmask = E.gate_mask(df, key, 8.0, 3, base_mask=base)
        idx_g = np.where(gmask)[0]
        # counts per candidate row
        def count_before(w, rows, idx_arr):
            # rows: sorted spike rows; count of idx_arr elements in (row-w, row)
            pos = np.searchsorted(idx_arr, rows)
            # spikes at rows: idx_arr[pos-1] <= rows; subtract those < row-w
            res = np.zeros(len(rows), dtype=int)
            for j, r in enumerate(rows):
                lo = np.searchsorted(idx_arr, r - w, side="right")
                res[j] = pos[j] - lo
            return res

        bigrows = np.where(big)[0]
        # masks
        cA5 = count_before(5, bigrows, idx_base)    # adverse 3x+ in prior 5
        cA3 = count_before(3, bigrows, idx_base)    # adverse 3x+ in prior 3
        cB3 = count_before(3, bigrows, idx_big)     # big in prior 3
        m_g1 = np.zeros(n, dtype=bool); m_g1[bigrows[cA5 <= 2]] = True
        m_g2 = np.zeros(n, dtype=bool); m_g2[bigrows[cA3 <= 1]] = True
        m_g4 = np.zeros(n, dtype=bool); m_g4[bigrows[cB3 == 0]] = True  # isolated-so-far
        m_g6 = np.zeros(n, dtype=bool)
        m_g6[bigrows[(cA5 <= 2) & (cB3 == 0)]] = True   # calm AND first-of-run
        gmask_full = gmask
        m_g1 &= gmask_full
        m_g2 &= gmask_full

        hold, cd = cfg["hold"], cfg["cooldown"]
        print("=" * 100)
        print(f"{key} (verified hold={hold} cd={cd} gate)")
        print("=" * 100)
        ctl = E.run_cfg(key, hold, cooldown=cd, partial=True, gate=True,
                        gate_mult=8.0, gate_window=3, df=df)
        print(f"  G0 control (gate)              {f(ctl)}")
        for nm, m in (("G1 gate & calm5", m_g1), ("G2 gate & calm3", m_g2),
                      ("G4 isolated-first big", m_g4), ("G6 calm & first", m_g6)):
            trs = my_run(df, key, m, hold, 1, cd)
            print(f"  {nm:<28} {f(trs)}")
        # OOS halves of the survivors
        print("  -- OOS --")
        for nm, mk in (("G1", m_g1), ("G2", m_g2), ("G4", m_g4), ("G6", m_g6)):
            for hname, slc in (("1st", slice(0, cut)), ("2nd", slice(cut, None))):
                d = df.iloc[slc].reset_index(drop=True)
                cb2, cB2, ib2, iB2 = prior_counts(d, key)
                bg = np.where(E.spike_rows(d, key).astype(bool) &
                              (d["range"].values /
                               np.maximum(d["median_range"].values, 1e-9) >= 8.0))[0]
                g = E.gate_mask(d, key, 8.0, 3)
                # rebuild mask slice-safely by recomputing on the slice
                mm = np.zeros(len(d), dtype=bool)
                rows = bg
                if nm == "G4":
                    q = count_before(3, rows, iB2)
                    mm[rows[q == 0]] = True
                elif nm == "G6":
                    qA = count_before(5, rows, ib2); qB = count_before(3, rows, iB2)
                    mm[rows[(qA <= 2) & (qB == 0)]] = True
                elif nm == "G1":
                    qA = count_before(5, rows, ib2)
                    mm[rows[qA <= 2]] = True
                    mm &= g
                elif nm == "G2":
                    qA = count_before(3, rows, ib2)
                    mm[rows[qA <= 1]] = True
                    mm &= g
                t = my_run(d, key, mm, hold, 1, cd)
                print(f"  {nm:<4} {hname}: {f(t)}")
        print()


if __name__ == "__main__":
    main()
