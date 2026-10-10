#!/usr/bin/env python3
"""
HEDGED FADE TEST — hold the fade, add a small opposite hedge when deep
=======================================================================
User's idea: instead of flipping out of the fade, KEEP the fade position
and ADD a small hedge in the spike direction only once the drift has run
beyond the stop distance (the long is >= +1R). Rationale:
  - the fade's real damage is the single-tick spike gapping through the
    SL (measured live: 4-8x the stop, e.g. -$55.85 through a 5.23 stop);
  - a hedge added at +1R captures s x the spike drop, converting tail
    losses of D into (1-s)*D, at the cost of bleeding while the drift
    continues (the drift cases where the long is winning anyway).

Design (bar space, corrected-engine accounting for the MAIN leg:
gap-through fills on adverse spike bars). Per-symbol verified configs
from brain/config.py. The hedge is a SEPARATE small position opened at
the first bar close where the drift exceeds the stop distance, closed at
market (that bar's close) when the main leg exits for any reason.

Comparison: identical selection, full-lot main, hedge size s swept.
The delta isolates the hedge's value.
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E
from config import by_key

HELP = None


def walk(closes, highs, lows, rng, med, adv, is_crash, entry, sl_level,
         tp_level, s, maxbars, n):
    """Mirror E.half_exit but return the exit bar index too."""
    for j in range(s, min(s + maxbars, n)):
        if is_crash:
            hit_sl, hit_tp = lows[j] <= sl_level, highs[j] >= tp_level
        else:
            hit_sl, hit_tp = highs[j] >= sl_level, lows[j] <= tp_level
        if hit_sl:
            if adv[j] and rng[j] > 3 * med[j]:
                return (lows[j] if is_crash else highs[j]), "sl", j
            return sl_level, "sl", j
        if hit_tp:
            return tp_level, "tp", j
    j = min(s + maxbars - 1, n - 1)
    return closes[j], "timeout", j


def hedged_trade(df, sym, i, entry_bar, hold, hedge_size, trig_R=1.0):
    """Main fade position + optional opposite hedge at +trig_R drift."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + entry_bar
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], E.BROKER_FLOOR.get(sym, 1.0))
    if is_crash:  # main = LONG (fade the crash dip)
        sl_level = entry - sl_dist
        tp_level = entry + 2 * sl_dist
        trig_level = entry + trig_R * sl_dist
    else:         # main = SHORT (fade the boom pump)
        sl_level = entry + sl_dist
        tp_level = entry - 2 * sl_dist
        trig_level = entry - trig_R * sl_dist

    # find first bar close crossing the hedge trigger BEFORE main exit
    hedge_entry = None
    hedge_bar = None
    main_p, main_r, main_j = None, None, None
    # scan bar by bar, checking trigger at close BEFORE processing exits? The
    # trigger uses the CLOSE; the main exit uses intra-bar high/low. Order:
    # at bar j, if main would have exited intra-bar (TP/SL), exit happened
    # (possibly before the close). Trigger only applies if still open at close.
    for j in range(e + 1, min(e + 1 + hold, n)):
        # does the main leg exit THIS bar?
        p0, r0, j0 = walk(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_level, tp_level, j, 1, n)
        if r0 != "timeout":  # exited intra-bar at j
            main_p, main_r, main_j = p0, r0, j
            break
        # still open at bar j: trigger check at this close
        if hedge_entry is None:
            if (is_crash and closes[j] >= trig_level) or \
               (not is_crash and closes[j] <= trig_level):
                hedge_entry = closes[j]
                hedge_bar = j
    else:
        # never exited: timeout at e+hold close
        jj = min(e + hold, n - 1)
        main_p, main_r, main_j = closes[jj], "timeout", jj

    # main pnl (direction-correct)
    main_pnl = (main_p - entry) if is_crash else (entry - main_p)

    hedge_pnl = 0.0
    nhedge = 0
    if hedge_entry is not None:
        close_p = closes[min(main_j, n - 1)]
        # hedge is OPPOSITE the main: crash->hedge SHORT, boom->hedge LONG
        if is_crash:
            hedge_pnl = hedge_size * (hedge_entry - close_p)
        else:
            hedge_pnl = hedge_size * (close_p - hedge_entry)
        nhedge = 1
    return {"pnl": main_pnl + hedge_pnl, "main": main_pnl,
            "hedge": hedge_pnl, "nhalves": 1 + nhedge, "reason": main_r,
            "triggered": nhedge}


def run(key, hedge_size=None, trig_R=1.0):
    cfg = by_key(key)
    df = E.load_prep(key)
    hold, cd, e_off = cfg["verified"]["hold"], cfg["verified"]["cooldown"], \
        cfg["verified"]["entry_bar"]
    gate = cfg["verified"].get("gate", False)
    gm, gw = cfg["verified"].get("gate_mult", 8.0), cfg["verified"].get("gate_window", 3)
    base = E.spike_rows(df, key)
    if gate:
        keep = E.gate_mask(df, key, gm, gw, base_mask=base)
        idx = np.where(keep)[0]
    else:
        idx = np.where(base)[0]
    trs, skip, last_two = [], 0, []
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if len(last_two) >= 2 and (i - last_two[-2]) <= 10:
            last_two.append(i)
            continue
        last_two.append(i)
        tr = hedged_trade(df, key, i, e_off, hold, hedge_size, trig_R) \
            if hedge_size else _plain(df, key, i, e_off, hold)
        if tr is None:
            continue
        trs.append(tr)
        if tr["main"] <= 0 and cd > 0:
            skip = cd
    return trs, df


def _plain(df, sym, i, entry_bar, hold):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = E.arrays(df, sym)
    e = i + entry_bar
    if e + 1 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], E.BROKER_FLOOR.get(sym, 1.0))
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    p, r, j = walk(closes, highs, lows, rng, med, adv, is_crash,
                   entry, sl_level, tp_level, e + 1, hold, n)
    pnl = (p - entry) if is_crash else (entry - p)
    return {"pnl": pnl, "main": pnl, "hedge": 0.0, "nhalves": 1,
            "reason": r, "triggered": 0}


def stats(trs):
    if not trs:
        return None
    pnls = np.array([t["pnl"] for t in trs])
    mains = np.array([t["main"] for t in trs])
    hedges = np.array([t["hedge"] for t in trs])
    wins = pnls[pnls > 0]
    losses = pnls[pnls <= 0]
    pf = wins.sum() / abs(losses.sum()) if len(losses) else 999
    eq = np.cumsum(pnls)
    dd = (np.maximum.accumulate(eq) - eq).max()
    # tail metrics on the MAIN leg (what the hedge is supposed to cut)
    main_losses = mains[mains < 0]
    sl_dist_avg = None
    trig = 100.0 * np.mean([t["triggered"] for t in trs])
    return {"n": len(pnls), "wr": len(wins) / len(pnls) * 100, "pf": pf,
            "pnl": pnls.sum(), "avg": pnls.mean(), "maxdd": dd,
            "worst": pnls.min(), "main_worst": mains.min(),
            "main_p95loss": -np.percentile(-main_losses, 95) if len(main_losses) else 0,
            "trig": trig}


def line(label, s):
    if s is None:
        print(f"  {label:<34}  none")
        return
    print(f"  {label:<34}{s['n']:>5}{s['wr']:>6.1f}%{s['pf']:>7.2f}"
          f"{s['pnl']:>9.1f}{s['avg']:>+7.3f}  DD={s['maxdd']:>7.1f}"
          f"  worst={s['worst']:>7.1f}  trig={s['trig']:.0f}%")


if __name__ == "__main__":
    for key in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        print(f"\n=== {key.upper()} ===")
        b, df = run(key)
        line("base (full fade, no hedge)", stats(b))
        for s in [0.25, 0.5, 0.75]:
            h, _ = run(key, hedge_size=s)
            line(f"hedged s={s:.2f} @ +1R", stats(h))
        for s in [0.5]:
            h, _ = run(key, hedge_size=s, trig_R=1.5)
            line(f"hedged s={s:.2f} @ +1.5R", stats(h))