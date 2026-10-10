#!/usr/bin/env python3
"""
PRINCIPLE DERIVATIONS — concrete strategies implied by P1-P4, untested until now
================================================================================
D1 (P2 on exits): BREAKEVEN-RAISE — after bar-1 closes favorable, move the stop
   to entry instead of (or in addition to) adding half. Conditional exit ratchet.
D2 (P2 on exits): CONDITIONAL HOLD — hold=3 base, but each favorable close
   extends the trade by 1 bar (let winners run only while confirmed).
D3 (P3): HAZARD STATE — are we in a discharge-dense period? Entries in
   low-rolling-hazard windows should beat high-hazard windows (P3 says losses
   cluster — but does the SPIKE rate itself cluster visibly in the M1 rate?).
D4 (P4 in time): AGING ENTRY — enter only if the last adverse discharge was
   >= N bars ago (discharge-age gate, the M1 mirror of M15 freshness).
D5 (P4 in price): SETTLE-BREAK — require the post-discharge bounce to have
   broken above the pre-discharge swing high (structure digested) before
   allowing the pullback entry.
D6 (P1): GRIND HARVEST with tail stop — the purest P1 trade: long crash every
   bar (dense), but the stop is 3x median instead of 1x (P5: stop width is the
   risk model) — does a wide stop beat the 1x stop on the dense schedule?
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, pullback_bar, stats, row, m15_regime
from ict_bosconfirm_test import trade_at
from partial_m1_corrected import arrays, half_exit, BROKER_FLOOR


# ---------- D1 / D2: conditional exits on the gated pullback config ----------

def trade_cond_exit(df, sym, entry_bar, hold, mode):
    """mode: 'base' (2xSL/1xSL), 'be' (breakeven-raise after fav close),
    'ch' (conditional hold +1 per fav close)."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = int(entry_bar)
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_d = max(med[e], BROKER_FLOOR.get(sym, 1.0))
    tp_d = 2 * sl_d
    sl_level = (entry - sl_d) if is_crash else (entry + sl_d)
    tp_level = (entry + tp_d) if is_crash else (entry - tp_d)
    stop = sl_level
    ext_hold = hold
    fav_count = 0
    for j in range(e + 1, min(e + 40, n)):
        # stop check (gap-through on adverse discharge bars)
        if is_crash:
            if lows[j] <= stop:
                px = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else stop
                pnl = (px - entry)
            elif highs[j] >= tp_level:
                pnl = (tp_level - entry)
            else:
                pnl = None
        else:
            if highs[j] >= stop:
                px = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else stop
                pnl = (entry - px)
            elif lows[j] <= tp_level:
                pnl = (entry - tp_level)
            else:
                pnl = None
        if pnl is not None:
            return pnl
        # end-of-bar updates
        fav = (closes[j] > entry) if is_crash else (closes[j] < entry)
        if fav:
            fav_count += 1
            if mode == "be" and fav_count >= 1:
                stop = entry  # ratchet to breakeven after 1 fav close
            if mode == "ch":
                ext_hold += 1
        if mode == "base" and (j - e) >= hold:
            return (closes[j] - entry) if is_crash else (entry - closes[j])
        if mode != "base" and (j - e) >= ext_hold:
            return (closes[j] - entry) if is_crash else (entry - closes[j])
    j = min(e + 39, n - 1)
    return (closes[j] - entry) if is_crash else (entry - closes[j])


def run_pull(df, sym, i, hold=3, mode="base"):
    b = pullback_bar(df, sym, i, 6)
    if b < 0:
        return None
    return trade_cond_exit(df, sym, b, hold, mode)


# ---------- D3: rolling hazard state on the dense schedule ----------

def dense_hazard(df, sym, cd=10, haz_win=240):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    idx = discharges(df, sym)
    spike_flag = np.zeros(n, dtype=bool)
    spike_flag[idx] = True
    hazard = pd.Series(spike_flag).rolling(haz_win, min_periods=50).mean().values
    med_h = np.nanmedian(hazard[np.isfinite(hazard)])
    hi = hazard > 1.25 * med_h
    lo = hazard < 0.75 * med_h
    skip_until = -1
    pnls_hi, pnls_lo = [], []
    for i in range(100, n - 10):
        if i < skip_until:
            continue
        if not (np.isfinite(med[i]) and med[i] > 0):
            continue
        e = i + 1
        if e + 2 >= n:
            break
        entry = closes[e]
        sl_d = max(med[i], BROKER_FLOOR.get(sym, 1.0))
        tp_d = 2 * sl_d
        # half A
        pa = None
        for j in range(e + 1, min(e + 3, n)):
            if is_crash:
                if lows[j] <= entry - sl_d:
                    pa = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                    break
                if highs[j] >= entry + tp_d:
                    pa = entry + tp_d
                    break
            else:
                if highs[j] >= entry + sl_d:
                    pa = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                    break
                if lows[j] <= entry - tp_d:
                    pa = entry - tp_d
                    break
        if pa is None:
            pa = closes[min(e + 2, n - 1)]
        b1 = e + 1
        if is_crash:
            a_open = not (lows[b1] <= entry - sl_d or highs[b1] >= entry + tp_d)
            fav = closes[b1] > entry
        else:
            a_open = not (highs[b1] >= entry + sl_d or lows[b1] <= entry - tp_d)
            fav = closes[b1] < entry
        if a_open and fav and b1 + 1 < n:
            pb = None
            for j in range(b1 + 1, min(b1 + 2, n)):
                if is_crash:
                    if lows[j] <= entry - sl_d:
                        pb = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                        break
                    if highs[j] >= entry + tp_d:
                        pb = entry + tp_d
                        break
                else:
                    if highs[j] >= entry + sl_d:
                        pb = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                        break
                    if lows[j] <= entry - tp_d:
                        pb = entry - tp_d
                        break
            if pb is None:
                pb = closes[min(b1 + 1, n - 1)]
            pnl = 0.5 * (pa - entry) + 0.5 * (pb - entry) if is_crash else \
                  0.5 * (entry - pa) + 0.5 * (entry - pb)
        else:
            pnl = 0.5 * ((pa - entry) if is_crash else (entry - pa))
        bucket = pnls_hi if hi[i] else (pnls_lo if lo[i] else None)
        if bucket is not None:
            bucket.append(pnl)
        if pnl < 0 and cd > 0:
            skip_until = i + cd
    return pnls_hi, pnls_lo


# ---------- D4: discharge-age gate on the dense schedule ----------

def dense_agegate(df, sym, cd=10, min_age=60):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    idx = discharges(df, sym)
    age = np.full(n, 9999)
    last = -9999
    for t in range(n):
        if last >= 0 and t > last:
            age[t] = t - last
    for i in idx:
        age[i] = 0
        last = i
    skip_until = -1
    pnls = []
    for i in range(100, n - 10):
        if i < skip_until or age[i] < min_age:
            continue
        if not (np.isfinite(med[i]) and med[i] > 0):
            continue
        e = i + 1
        if e + 2 >= n:
            break
        entry = closes[e]
        sl_d = max(med[i], BROKER_FLOOR.get(sym, 1.0))
        tp_d = 2 * sl_d
        pa = None
        for j in range(e + 1, min(e + 3, n)):
            if is_crash:
                if lows[j] <= entry - sl_d:
                    pa = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                    break
                if highs[j] >= entry + tp_d:
                    pa = entry + tp_d
                    break
            else:
                if highs[j] >= entry + sl_d:
                    pa = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                    break
                if lows[j] <= entry - tp_d:
                    pa = entry - tp_d
                    break
        if pa is None:
            pa = closes[min(e + 2, n - 1)]
        pnl = 0.5 * ((pa - entry) if is_crash else (entry - pa))
        pnls.append(pnl)
        if pnl < 0 and cd > 0:
            skip_until = i + cd
    return pnls


# ---------- D5: settle-break requirement on the pullback config ----------

def run_settlebreak(df, sym, gated, require_break=True):
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    out = []
    for i in gated:
        # pre-discharge swing high (crash) / low (boom) in the 30 bars before
        w = 30
        a, b0 = max(0, i - w), i
        if b0 - a < 5:
            continue
        swing = highs[a:b0].max() if is_crash else lows[a:b0].min()
        # bounce must exceed the swing (structure digested) before/at entry
        b = pullback_bar(df, sym, i, 6)
        if b < 0:
            continue
        seg_h = highs[i + 1:b + 1]
        seg_l = lows[i + 1:b + 1]
        if len(seg_h) == 0:
            continue
        bounce_ok = (seg_h.max() > swing) if is_crash else (seg_l.min() < swing)
        if require_break and not bounce_ok:
            continue
        if not require_break and bounce_ok:
            continue
        p = trade_at(df, sym, b, 3)
        if p is not None:
            out.append(p)
    return out


# ---------- D6: wide-stop dense harvest ----------

def dense_widestop(df, sym, cd=10, sl_mult=3.0):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    skip_until = -1
    pnls = []
    for i in range(100, n - 10):
        if i < skip_until:
            continue
        if not (np.isfinite(med[i]) and med[i] > 0):
            continue
        e = i + 1
        if e + 2 >= n:
            break
        entry = closes[e]
        sl_d = max(sl_mult * med[i], BROKER_FLOOR.get(sym, 1.0))
        tp_d = 2 * sl_d
        pa = None
        for j in range(e + 1, min(e + 4, n)):
            if is_crash:
                if lows[j] <= entry - sl_d:
                    pa = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                    break
                if highs[j] >= entry + tp_d:
                    pa = entry + tp_d
                    break
            else:
                if highs[j] >= entry + sl_d:
                    pa = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                    break
                if lows[j] <= entry - tp_d:
                    pa = entry - tp_d
                    break
        if pa is None:
            pa = closes[min(e + 3, n - 1)]
        pnl = 0.5 * ((pa - entry) if is_crash else (entry - pa))
        pnls.append(pnl)
        if pnl < 0 and cd > 0:
            skip_until = i + cd
    return pnls


if __name__ == "__main__":
    for sym in ["crash300", "boom500"]:
        df = load(sym)
        reg = m15_regime(df, sym)
        if "boom" in sym:
            reg = ~reg
        gated = [i for i in discharges(df, sym) if reg[i]]
        print(f"\n{'='*90}\n{sym.upper()}\n{'='*90}")

        print("D1/D2 — conditional exits (gated pullback entries):")
        for mode, lab in [("base", "base hold=3"), ("be", "breakeven-raise"),
                          ("ch", "conditional hold")]:
            pnls = [run_pull(df, sym, i, 3, mode) for i in gated]
            pnls = [p for p in pnls if p is not None]
            row(f"  {lab}", stats(pnls))

        print("D3 — hazard state (dense cd=10):")
        hi, lo = dense_hazard(df, sym)
        row("  HIGH hazard windows", stats(hi))
        row("  LOW hazard windows", stats(lo))

        print("D4 — discharge-age gate (dense cd=10):")
        for ma in [30, 60, 120]:
            row(f"  last discharge >= {ma} bars", stats(dense_agegate(df, sym, min_age=ma)))

        print("D5 — settle-break (gated pullback):")
        row("  bounce broke pre-swing", stats(run_settlebreak(df, sym, gated, True)))
        row("  bounce DID NOT break", stats(run_settlebreak(df, sym, gated, False)))

        print("D6 — wide-stop dense (cd=10):")
        for m_ in [1.0, 2.0, 3.0]:
            row(f"  SL={m_}x median", stats(dense_widestop(df, sym, sl_mult=m_)))