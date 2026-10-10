#!/usr/bin/env python3
"""
MATH EDGES — poker-style number hunting on the corrected engine.

A. CONDITIONAL INTENSITY (Hawkes-style): the spike process is Poisson-ish
   at the macro level, but do spikes SELF-EXCITE? Measure the adverse-spike
   rate in the minutes right after each spike vs the global rate. If the
   first minutes are elevated and decay, we have a real number for the
   post-spike skip window (cooldown); if flat, the "hot hand" is fake.
B. EWMA RATE GATE: estimate the CURRENT spike rate with an EWMA of recent
   counts and bucket entries by it — entering when the estimated rate is
   low should reduce hold-window hits. OOS halves.
C. KELLY / GROWTH-OPTIMAL: from the real per-trade PnL distribution
   (verified configs), compute edge-per-trade, variance, empirical Kelly f*
   and the implied max-risk number. Poker-style EV math.
D. CONDITIONAL ENTRY DELAY: for the gated 300/500s, if the bar right after
   the spike BOUNCED (closed in the fade direction) enter +1; if it kept
   dumping, wait to +3. State-dependent delay.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, by_key, BROKER_FLOOR  # noqa: E402

# ---------------------------------------------------------------- helpers
def fmt(s):
    if not s or s["n"] == 0:
        return "none"
    return (f"n={s['n']:>5} WR={s['wr']:>5.1f}% PF={s['pf']:>6.2f} "
            f"avg={s['avg']:>+7.3f} DD={s['maxdd']:>6.1f}")


def ver(key):
    return dict(by_key(key)["verified"])


# ---------------------------------------------------------------- A
def test_a():
    print("=" * 100)
    print("A. CONDITIONAL INTENSITY — adverse-spike rate right after a spike (x global)")
    print("=" * 100)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        adv = E.spike_rows(df, key)
        idx = np.where(adv)[0]
        n = len(df)
        rate_global = len(idx) / n  # per bar
        print(f"\n{key}  (global rate {rate_global*60:.2f}/h)")
        # windows after each spike: 1-5, 6-10, 11-20, 21-40 bars
        wins = [(1, 5), (6, 10), (11, 20), (21, 40), (41, 90)]
        for (a, b) in wins:
            cnt = 0
            for i in idx:
                j = i + a
                while j <= min(i + b, n - 1):
                    if adv[j]:
                        cnt += 1
                    j += 1
            bars = sum(max(0, min(i + b, n - 1) - (i + a) + 1) for i in idx)
            if bars > 0:
                ratio = (cnt / bars) / rate_global
                print(f"    bars +{a}-{b}: intensity x {ratio:5.2f}  (n={cnt})")


# ---------------------------------------------------------------- B
def ewma_rate(key, df, half_life_bars=180):
    """Per-bar EWMA hazard estimate of adverse spikes (history only)."""
    adv = E.spike_rows(df, key).astype(float)
    alpha = 1 - np.exp(-np.log(2) / half_life_bars)
    out = np.zeros(len(df))
    h = 0.0
    for i in range(len(df)):
        h = (1 - alpha) * h + alpha * adv[i]
        out[i] = h
    return out


def test_b():
    print("\n" + "=" * 100)
    print("B. EWMA RATE GATE — trade only when estimated current spike rate is low?")
    print("=" * 100)
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        rate = ewma_rate(key, df)
        cfg = ver(key)
        cut = len(df) // 2
        print(f"\n{key} (cfg hold={cfg['hold']} cd={cfg['cooldown']} eb={cfg.get('entry_bar',1)}):")
        for hname, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
            d = d.reset_index(drop=True)
            trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                            gate=cfg.get("gate", False), gate_mult=cfg.get("gate_mult", 8.0),
                            gate_window=cfg.get("gate_window", 3),
                            entry_bar=cfg.get("entry_bar", 1), df=d)
            if not trs:
                print(f"  {hname}: no trades"); continue
            # rate at the bar BEFORE the spike (entry-row's own bar is the spike)
            vals = np.array([rate[t["entry_row"] - 1] if t["entry_row"] - 1 >= 0
                             else np.nan for t in trs])
            base = E.outcome_stats(trs)
            m = np.isfinite(vals)
            lo = [t for t, v in zip(trs, vals) if np.isfinite(v) and v < np.median(vals[m])]
            hi = [t for t, v in zip(trs, vals) if np.isfinite(v) and v > np.median(vals[m])]
            print(f"  {hname}: all {fmt(base)}")
            print(f"  {hname}: low-rate {fmt(E.outcome_stats(lo))}")
            print(f"  {hname}: high-rate {fmt(E.outcome_stats(hi))}")


# ---------------------------------------------------------------- C
def test_c():
    print("\n" + "=" * 100)
    print("C. KELLY / EV MATH on the real trade distribution (verified configs)")
    print("=" * 100)
    print(f"{'sym':<11}{'n':>6}{'p(win)':>8}{'avg+':>8}{'avg-':>8}{'edge/sl':>9}"
          f"{'mu':>8}{'sigma':>8}{'Kelly f*':>10}{'2% safe?':>10}")
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        cfg = ver(key)
        trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                        gate=cfg.get("gate", False), gate_mult=cfg.get("gate_mult", 8.0),
                        gate_window=cfg.get("gate_window", 3),
                        entry_bar=cfg.get("entry_bar", 1), df=df)
        if not trs:
            continue
        pnls = np.array([t["pnl"] for t in trs])
        win = pnls[pnls > 0]; loss = pnls[pnls <= 0]
        p = len(win) / len(pnls)
        mu = pnls.mean(); var = pnls.var()
        # empirical Kelly: maximize E[log(1 + f*r)]
        best_f, best_g = 0.0, -1e9
        for f in np.arange(0.01, 0.5, 0.01):
            g = np.mean(np.log(1 + f * pnls)) if np.all(1 + f * pnls > 0) else -1e9
            if g > best_g:
                best_g, best_f = g, f
        approx = mu / var if var > 0 else 0
        print(f"{key:<11}{len(pnls):>6}{p*100:>7.1f}%{win.mean() if len(win) else 0:>8.2f}"
              f"{loss.mean() if len(loss) else 0:>8.2f}"
              f"{(win.mean() if len(win) else 0) / abs(loss.mean() if len(loss) else 1):>9.2f}"
              f"{mu:>8.3f}{np.sqrt(var):>8.2f}{best_f*100:>9.1f}%"
              f"{'YES' if 2.0 <= best_f*100*0.5 else 'NO':>10}")


# ---------------------------------------------------------------- D
def partial_cond(df, sym, i, hold, e_bounce, e_dump):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    # what did the bar right after the spike do? (i+1)
    if i + 1 < n:
        bounced = (closes[i + 1] > closes[i]) if is_crash else (closes[i + 1] < closes[i])
        e_off = e_bounce if bounced else e_dump
    else:
        e_off = e_bounce
    e = i + e_off
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, _ = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                        entry, sl_level, tp_level, e + 1, hold, n)
    pA1, rA1 = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                           entry, sl_level, tp_level, e + 1, 1, n)
    add_b = (rA1 == "timeout" and
             ((closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)))
    if add_b:
        pB, _ = E.half_exit(closes, highs, lows, rng, med, adv, is_crash,
                            entry, sl_level, tp_level, e + 2, hold - 1, n)
        return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1) +
                0.5 * (pB - entry) * (1 if is_crash else -1), "nhalves": 2}
    return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1), "nhalves": 1}


def run_cond(key, hold, cd, e_bounce, e_dump, df, gate=True):
    trs, skip = [], 0
    base = E.spike_rows(df, key)
    if gate:
        keep = E.gate_mask(df, key, 8.0, 3, base_mask=base)
        idx = np.where(keep)[0]
    else:
        idx = np.where(base)[0]
    last_two = []
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if len(last_two) >= 2 and (i - last_two[-2]) <= 10:
            last_two.append(i)
            continue
        last_two.append(i)
        tr = partial_cond(df, key, i, hold, e_bounce, e_dump)
        if tr is None:
            continue
        trs.append(tr)
        if tr["pnl"] <= 0 and cd > 0:
            skip = cd
    return trs


def test_d():
    print("\n" + "=" * 100)
    print("D. CONDITIONAL ENTRY DELAY — bounce => +1, still-dumping => +3 (gated 300/500)")
    print("=" * 100)
    for key in ("crash300", "crash500", "boom500"):
        df = E.load_prep(key)
        cfg = ver(key)
        cut = len(df) // 2
        print(f"\n{key} (gated hold={cfg['hold']} cd={cfg['cooldown']}):")
        for hname, d in (("1st", df.iloc[:cut]), ("2nd", df.iloc[cut:])):
            d = d.reset_index(drop=True)
            flat = E.outcome_stats(E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                                             partial=True, gate=True, gate_mult=8.0,
                                             gate_window=3, df=d))
            cond = E.outcome_stats(run_cond(key, cfg["hold"], cfg["cooldown"], 1, 3, d, gate=True))
            print(f"  {hname}: flat+1 {fmt(flat)}")
            print(f"  {hname}: cond    {fmt(cond)}")


if __name__ == "__main__":
    test_a()
    test_b()
    test_c()
    test_d()
