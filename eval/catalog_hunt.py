#!/usr/bin/env python3
"""
CATALOG HUNT - new edge/concept tests on the corrected engine (canonical
accounting: entry at close of spike+entry_bar, sl=max(med, broker floor),
gap-through fills on adverse spike bars, 0.5-weighted partial halves).

Tests:
  T1  spike SIZE vs gap since prior spike  (anticipation: do long quiet
      stretches produce bigger spikes?)
  T2  drift momentum: lag-1 autocorr of non-spike bar moves + post-spike
      recovery drift shape
  T3  big-spike CLUSTER LADDER: fade PF + gap-to-next-big by group size k
      (1, 2, 3+)  -- user's "3-4 back-to-back spikes => long updrift"
  T4  DAILY R-BUDGET circuit breaker: after a day nets below -K R, stop
      entering until next day
  T5  HALF-SIZE-AFTER-LOSS smoothing vs cooldown (risk variant)
  T6  gated-entry PnL by entry hour (OOS halves, informational)
  T7  conservative LIMIT-ENTRY improvement (fill 0.25R better only if the
      entry bar trades through it; skip otherwise)
"""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, BROKER_FLOOR  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from partial_m1_corrected import half_exit  # noqa: E402

KEYS = [s["key"] for s in SYMBOLS]


def prep_cfg(s):
    return dict(s["verified"])


def base_trades(key):
    """Canonical verified-config trades with entry_row + reason."""
    cfg = prep_cfg([s for s in SYMBOLS if s["key"] == key][0])
    df = E.load_prep(key)
    return E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                     partial=True, gate=cfg.get("gate", False),
                     gate_mult=cfg.get("gate_mult", 8.0),
                     gate_window=cfg.get("gate_window", 3),
                     entry_bar=cfg.get("entry_bar", 1), df=df), df, cfg


def fmt_stats(trs, key):
    if not trs:
        return "none"
    pnls = np.array([t["pnl"] for t in trs])
    w = pnls[pnls > 0]; l = pnls[pnls <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) else 999
    eq = np.cumsum(pnls); dd = (np.maximum.accumulate(eq) - eq).max()
    return (f"n={len(pnls):>5} WR={len(w)/len(pnls)*100:>5.1f}% PF={pf:>6.2f} "
            f"PnL={pnls.sum():>+9.1f} avg={pnls.mean():>+6.3f} DD={dd:>7.1f}")


def fmt(v):
    return "none" if v is None else (f"n={v['n']:>5} WR={v['wr']:>5.1f}% "
            f"PF={v['pf']:>6.2f} PnL={v['pnl']:>+9.1f} avg={v['avg']:>+6.3f} "
            f"DD={v['maxdd']:>7.1f}")


def oos_split(df):
    cut = len(df) // 2
    return (df.iloc[:cut].reset_index(drop=True),
            df.iloc[cut:].reset_index(drop=True))


# ---------------------------------------------------------------- T1/T2
def test_t1_t2():
    print("=" * 100)
    print("T1 size-vs-gap | T2 drift momentum (non-spike autocorr + recovery)")
    print("=" * 100)
    for key in KEYS:
        df = E.load_prep(key)
        med = df["median_range"].values
        rng = df["range"].values
        adv = E.spike_rows(df, key)
        idx = np.where(adv)[0]
        if len(idx) < 20:
            continue
        gaps = np.diff(idx)
        sizes = np.array([rng[i] / med[i] for i in idx[1:]])
        corr = np.corrcoef(gaps, sizes)[0, 1] if len(gaps) > 10 else float("nan")
        # bucket by gap
        q = pd.qcut(gaps, 3, duplicates="drop")
        binned = pd.DataFrame({"g": gaps, "s": sizes}).groupby(q, observed=True)["s"].mean()
        # T2: autocorr of close-close moves on NON-spike bars
        closes = df["close"].values
        mv = np.diff(closes)
        nsp = ~adv[1:]
        m = mv[nsp]
        ac1 = np.corrcoef(m[:-1], m[1:])[0, 1] if len(m) > 50 else float("nan")
        # post-spike drift shape: mean next-1/2/3-bar move (crash: expect up)
        n = len(df)
        nxt = []
        for i in idx[:200]:
            if i + 4 < n:
                nxt.append([closes[i + k] - closes[i] for k in (1, 2, 3)])
        nxt = np.array(nxt).mean(axis=0) if nxt else np.zeros(3)
        print(f"  {key:<11} size-gap corr={corr:+.3f} | buckets(gap terciles)="
              f"{np.round(binned.values, 2)}")
        print(f"              non-spike bar AC1={ac1:+.4f} | mean move +1/+2/+3 bars "
              f"after spike: {np.round(nxt, 4)}")


# ---------------------------------------------------------------- T3
def big_ratio(df, key):
    med = df["median_range"].values
    rng = df["range"].values
    base = E.spike_rows(df, key)
    ratio = np.where(np.isfinite(med) & (med > 0), rng / np.maximum(med, 1e-9), 0)
    return base, ratio


def cluster_positions(df, key, mult=8.0, window=3):
    """For each spike row: position within a run of spikes all >= mult x
    median spaced <= window bars apart. Returns dict row->pos (1,2,3,4+)."""
    base, ratio = big_ratio(df, key)
    big = base & (ratio >= mult)
    pos = {}
    last = -10**9
    for i in np.where(base)[0]:
        if big[i] and i - last <= window:
            pos[i] = pos.get(last, 1) + 1 if last in pos else 2
            # pos[last] may be the previous big's run position
            if last in pos:
                pos[i] = pos[last] + 1
        else:
            pos[i] = 1
        last = i
    # fix chain (if a middle big was skipped, renumber) - simple version ok
    return pos


def test_t3():
    print("\n" + "=" * 100)
    print("T3 big-spike cluster ladder: after group of k big spikes (>=8x, "
          "<=3 bars apart), fade PF + gap to next big spike")
    print("=" * 100)
    for key in KEYS:
        df = E.load_prep(key)
        cfg = prep_cfg([s for s in SYMBOLS if s["key"] == key][0])
        base, ratio = big_ratio(df, key)
        big_idx = np.where(base & (ratio >= 8.0))[0]
        # group runs
        groups = []          # (start_row, size)
        cur, cur_size = -1, 0
        for i in big_idx:
            if cur < 0 or i - cur <= 3:
                cur, cur_size = i, cur_size + 1
            else:
                groups.append((cur - (cur_size - 1), cur_size))  # approx start
                cur, cur_size = i, 1
        if cur_size:
            groups.append((cur - (cur_size - 1), cur_size))
        # renumber properly by walking big_idx
        runs = []
        for i in big_idx:
            if runs and i - runs[-1][-1] <= 3:
                runs[-1].append(i)
            else:
                runs.append([i])
        print(f"\n  {key}: big-spike runs (size>=1): {len(runs)} runs, "
              f"mean size {np.mean([len(r) for r in runs]):.2f}")
        print(f"    {'run size':>9}{'n runs':>8}{'fade n':>8}{'PF':>7}"
              f"{'avg':>8}{'bars->next big':>15}")
        for k in (1, 2, 3, 4):
            rs = [r for r in runs if len(r) == k]
            if not rs:
                continue
            lastrows = [r[-1] for r in rs]
            nxt = []
            for r in rs:
                nxtrows = big_idx[big_idx > r[-1]]
                nxt.append(int(nxtrows[0] - r[-1]) if len(nxtrows) else np.nan)
            # fade after last spike of each run of exactly k, gate-style entry
            hold = cfg["hold"]
            # run fades via engine partial at each lastrow (no cooldown/gate)
            from partial_m1_corrected import arrays
            closes, highs, lows, rng2, med2, adv2, is_crash = arrays(df, key)
            n = len(df)
            trs = []
            for i in lastrows:
                e = i + cfg.get("entry_bar", 1)
                if e + 2 >= n:
                    continue
                entry = closes[e]
                sd = max(med2[i], BROKER_FLOOR.get(key, 1.0))
                sl = (entry - sd) if is_crash else (entry + sd)
                tp = (entry + 2 * sd) if is_crash else (entry - 2 * sd)
                pA, rA = half_exit(closes, highs, lows, rng2, med2, adv2,
                                   is_crash, entry, sl, tp, e + 1, hold, n)
                pA1, rA1 = half_exit(closes, highs, lows, rng2, med2, adv2,
                                     is_crash, entry, sl, tp, e + 1, 1, n)
                addb = False
                if rA1 == "timeout":
                    fav = (closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)
                    addb = fav
                if addb:
                    pB, _ = half_exit(closes, highs, lows, rng2, med2, adv2,
                                      is_crash, entry, sl, tp, e + 2, hold - 1, n)
                    trs.append({"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1)
                                + 0.5 * (pB - entry) * (1 if is_crash else -1)})
                else:
                    trs.append({"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1)})
            s = fmt_stats(trs, key)
            med_nxt = np.nanmedian(nxt) if nxt else np.nan
            print(f"    {k:>9}{len(rs):>8}{len(trs):>8}{s.split('PF=')[1][:8] if s != 'none' else '':>0}"
                  f"  med gap->next={med_nxt:.1f} bars")


# ---------------------------------------------------------------- T4
def test_t4():
    print("\n" + "=" * 100)
    print("T4 daily R-budget circuit breaker (stop entering after a day nets "
          "below -K R) vs baseline")
    print("=" * 100)
    for key in KEYS:
        trs, df, cfg = base_trades(key)
        if not trs:
            continue
        dt = E.datetimes(key)
        med = df["median_range"].values
        base = fmt_stats(trs, key)
        row = [f"  {key:<11} base {base}"]
        for K in (4.0, 8.0, 12.0):
            # per-trade R (points, sl dist used)
            out, day_pnl = [], {}
            for t in trs:
                d = pd.Timestamp(dt[t["entry_row"]]).floor("D")
                r_pts = max(med[t["entry_row"]], BROKER_FLOOR.get(key, 1.0))
                # check budget BEFORE entering
                if day_pnl.get(d, 0.0) <= -K * r_pts:
                    continue
                out.append(t)
                day_pnl[d] = day_pnl.get(d, 0.0) + t["pnl"]
            row.append(f"K={K:>4} {fmt_stats(out, key)}")
        print("\n".join(row))


# ---------------------------------------------------------------- T5
def test_t5():
    print("\n" + "=" * 100)
    print("T5 half-size-after-loss (risk smoothing) vs cooldown (skip)")
    print("=" * 100)
    for key in KEYS:
        cfg = prep_cfg([s for s in SYMBOLS if s["key"] == key][0])
        df = E.load_prep(key)
        n = len(df)
        base_mask = E.spike_rows(df, key)
        use_gate = cfg.get("gate", False)
        if use_gate:
            keep = E.gate_mask(df, key, cfg.get("gate_mult", 8.0),
                               cfg.get("gate_window", 3), base_mask=base_mask)
            idx = np.where(keep)[0]
        else:
            idx = np.where(base_mask)[0]
        from partial_m1_corrected import arrays
        closes, highs, lows, rng2, med2, adv2, is_crash = arrays(df, key)
        eb = cfg.get("entry_bar", 1)
        for mode in ("cd", "half"):
            trades, skip, half = [], 0, 1.0
            for i in idx:
                if skip > 0:
                    skip -= 1
                    continue
                e = i + eb
                if e + 2 >= n:
                    break
                entry = closes[e]
                sd = max(med2[i], BROKER_FLOOR.get(key, 1.0))
                sl = (entry - sd) if is_crash else (entry + sd)
                tp = (entry + 2 * sd) if is_crash else (entry - 2 * sd)
                pA, rA = half_exit(closes, highs, lows, rng2, med2, adv2,
                                   is_crash, entry, sl, tp, e + 1, cfg["hold"], n)
                pA1, rA1 = half_exit(closes, highs, lows, rng2, med2, adv2,
                                     is_crash, entry, sl, tp, e + 1, 1, n)
                addb = rA1 == "timeout" and ((closes[e + 1] > entry) if is_crash
                                             else (closes[e + 1] < entry))
                if addb:
                    pB, _ = half_exit(closes, highs, lows, rng2, med2, adv2,
                                      is_crash, entry, sl, tp, e + 2,
                                      cfg["hold"] - 1, n)
                    pnl = 0.5 * (pA - entry) * (1 if is_crash else -1) + \
                          0.5 * (pB - entry) * (1 if is_crash else -1)
                else:
                    pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
                trades.append({"pnl": half * pnl})
                if mode == "cd":
                    if pnl <= 0 and cfg["cooldown"] > 0:
                        skip = cfg["cooldown"]
                else:
                    half = 0.5 if pnl <= 0 else 1.0
            print(f"  {key:<11} {mode:>4}: {fmt_stats(trades, key)}")


# ---------------------------------------------------------------- T6
def test_t6():
    print("\n" + "=" * 100)
    print("T6 gated fade PnL by entry hour (OOS halves - informational)")
    print("=" * 100)
    for key in KEYS:
        cfg = prep_cfg([s for s in SYMBOLS if s["key"] == key][0])
        if not cfg.get("gate", False):
            continue
        df = E.load_prep(key)
        dt = E.datetimes(key)
        for hname, d in (("1st", df.iloc[:len(df) // 2].reset_index(drop=True)),
                         ("2nd", df.iloc[len(df) // 2:].reset_index(drop=True))):
            trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                            partial=True, gate=True, gate_mult=8.0,
                            gate_window=3, df=d)
            if not trs:
                continue
            hrs = pd.Series([pd.Timestamp(dt[t["entry_row"]]).hour
                             for t in trs])
            pnl = pd.Series([t["pnl"] for t in trs])
            g = pnl.groupby(hrs).agg(["count", "mean"])
            g = g[g["count"] >= 3]
            top = g["mean"].sort_values()
            print(f"  {key} {hname}: best hours {list(top.index[-2:])} "
                  f"({np.round(top.values[-2:],3)}), worst {list(top.index[:2])} "
                  f"({np.round(top.values[:2],3)})")


# ---------------------------------------------------------------- T7
def test_t7():
    print("\n" + "=" * 100)
    print("T7 conservative limit-entry: fill 0.25R better only if entry bar "
          "trades through it, else skip")
    print("=" * 100)
    for key in KEYS:
        cfg = prep_cfg([s for s in SYMBOLS if s["key"] == key][0])
        df = E.load_prep(key)
        base_mask = E.spike_rows(df, key)
        if cfg.get("gate", False):
            keep = E.gate_mask(df, key, cfg.get("gate_mult", 8.0),
                               cfg.get("gate_window", 3), base_mask=base_mask)
            idx = np.where(keep)[0]
        else:
            idx = np.where(base_mask)[0]
        from partial_m1_corrected import arrays
        closes, highs, lows, rng2, med2, adv2, is_crash = arrays(df, key)
        n = len(df)
        eb = cfg.get("entry_bar", 1)
        base_t, lim_t = [], []
        skip = 0
        for i in idx:
            if skip > 0:
                skip -= 1
                continue
            e = i + eb
            if e + 2 >= n:
                break
            entry = closes[e]
            sd = max(med2[i], BROKER_FLOOR.get(key, 1.0))
            # limit fill at entry -/+ 0.25R if bar e+1 trades through
            lim = (entry - 0.25 * sd) if is_crash else (entry + 0.25 * sd)
            touched = (lows[e + 1] <= lim) if is_crash else (highs[e + 1] >= lim)
            if not touched:
                skip = 0
                continue
            sl = (lim - sd) if is_crash else (lim + sd)
            tp = (lim + 2 * sd) if is_crash else (lim - 2 * sd)
            # walk from e+2 (strictly conservative: no credit for e+1)
            def walk(entry0):
                pA, _ = half_exit(closes, highs, lows, rng2, med2, adv2,
                                  is_crash, entry0, sl, tp, e + 2,
                                  max(1, cfg["hold"] - 1), n)
                return pA
            pA = walk(lim)
            base_t.append({"pnl": 0.5 * (walk(entry) - entry) * (1 if is_crash else -1)})
            lim_t.append({"pnl": 0.5 * (pA - lim) * (1 if is_crash else -1)})
            # cooldown on the market-version pnl for apples-to-apples skip
            if base_t[-1]["pnl"] <= 0 and cfg["cooldown"] > 0:
                skip = cfg["cooldown"]
        print(f"  {key:<11} market-touch {fmt_stats(base_t, key)}")
        print(f"  {key:<11} limit-touch  {fmt_stats(lim_t, key)}")


if __name__ == "__main__":
    test_t1_t2()
    test_t3()
    test_t4()
    test_t5()
    test_t6()
    test_t7()
