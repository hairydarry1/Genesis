#!/usr/bin/env python3
"""
INDICATOR COURSE BATTERY — the 4 new testable claims from the scalping transcripts
==================================================================================
A. RUN-EXHAUSTION ENTRY (RSI(1)/Momentum<5 videos): RSI(1) is binary (0=down
   bar, 100=up bar), so "RSI(1) below 10 + cross" = "k consecutive adverse bars,
   then the first favorable bar".  Enter drift at that close.  k in {3,5,8}.
   Control: matched-count random bars, same exit machinery.
B. SPIKE-THEN-RETRACE ENTRY (CCI/ADX videos): after a discharge bar, wait for
   CCI(14) to mean-revert through 50% of its extreme, then enter drift.
   Proxy: enter at the close of the first bar whose close retraces >=50% of
   the DISCHARGE BAR's range (intra-product variant of our pullback).
   Compare: our pullback (bounce retrace) vs discharge-body retrace.
C. MARTINGALE-ADD (spike video): enter drift; if -6 adverse closes, add a
   second position; timeout both at -10 adverse closes.  vs flat 50% partial.
D. CANDLE-RUN TARGET (all videos): "catch 10-15 candles" = is the favorable
   run-length after exhaustion events long-tailed enough to reward hold>3?
   Empirical distribution of post-entry favorable runs + EV at hold 3/5/10/15
   on exhaustion entries.
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, stats, row, m15_regime
from partial_m1_corrected import arrays, half_exit, BROKER_FLOOR

RNG = np.random.RandomState(77)


def favorable_close(df, sym):
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    return (closes > closes_prev) if False else None


def run_lengths(df, sym):
    """Return array of consecutive-adverse-run length ending at each bar."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    o = df["open"].values
    adverse = (closes < o) if is_crash else (closes > o)
    runs = np.zeros(len(df), dtype=int)
    c = 0
    for t in range(len(df)):
        if adverse[t]:
            c += 1
        else:
            c = 0
        runs[t] = c
    return runs


def trade_at_bar(df, sym, e, hold=3, sl_mult=1.0, timeout_close=None):
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_d = max(sl_mult * med[e], BROKER_FLOOR.get(sym, 1.0))
    tp_d = 2 * sl_d
    sl_level = (entry - sl_d) if is_crash else (entry + sl_d)
    tp_level = (entry + tp_d) if is_crash else (entry - tp_d)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, e + 1, hold, n)
    pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, 1, n)
    add_b = rA1 == "timeout" and ((closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry))
    if add_b:
        pB, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_level, tp_level, e + 2, hold - 1, n)
        return 0.5 * (pA - entry) * (1 if is_crash else -1) + \
               0.5 * (pB - entry) * (1 if is_crash else -1)
    return 0.5 * (pA - entry) * (1 if is_crash else -1)


if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = load(sym)
        runs = run_lengths(df, sym)
        n = len(df)
        print(f"\n{'='*90}\n{sym.upper()}\n{'='*90}")

        # ---- A. run-exhaustion entries ----
        print("A. RUN-EXHAUSTION (k adverse bars then first favorable close):")
        for k in [3, 5, 8]:
            entries = [t for t in range(200, n - 5)
                       if runs[t - 1] >= k and runs[t] == 0]
            pnls = [trade_at_bar(df, sym, e) for e in entries]
            pnls = [p for p in pnls if p is not None]
            s = stats(pnls)
            row(f"  k={k} (n={len(entries)})", s)
            if s and s["n"] > 100:
                ctrl = []
                for _ in range(3):
                    rb = RNG.randint(200, n - 5, len(entries))
                    for e in rb:
                        p = trade_at_bar(df, sym, e)
                        if p is not None:
                            ctrl.append(p)
                row(f"    ctrl", stats(ctrl))

        # ---- B. discharge-body retrace vs bounce pullback ----
        print("B. SPIKE-THEN-RETRACE (discharge-body 50% retrace):")
        idx = discharges(df, sym)
        closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
        body_entries = []
        for i in idx:
            if is_crash:
                body = df["open"].values[i] - lows[i]
                lvl = lows[i] + 0.5 * body
                for b in range(i + 1, min(i + 7, n)):
                    if closes[b] >= lvl:
                        body_entries.append(b)
                        break
            else:
                body = highs[i] - df["open"].values[i]
                lvl = highs[i] - 0.5 * body
                for b in range(i + 1, min(i + 7, n)):
                    if closes[b] <= lvl:
                        body_entries.append(b)
                        break
        pnls = [p for p in (trade_at_bar(df, sym, e) for e in body_entries) if p is not None]
        row(f"  body-retrace (n={len(body_entries)})", stats(pnls))
        # our bounce pullback for reference
        from ict_pullback_test import pullback_bar
        pb = []
        for i in idx:
            b = pullback_bar(df, sym, i, 6)
            if b >= 0:
                p = trade_at_bar(df, sym, b)
                if p is not None:
                    pb.append(p)
        row("  bounce-pullback (ref)", stats(pb))

        # ---- D. favorable run length after exhaustion ----
        print("D. FAVORABLE RUN LENGTH after k=5 exhaustion events:")
        entries = [t for t in range(200, n - 20)
                   if runs[t - 1] >= 5 and runs[t] == 0]
        fav_runs = []
        for e in entries:
            c = 0
            for j in range(e + 1, min(e + 40, n)):
                o = df["open"].values[j]
                fav = (closes[j] > o) if is_crash else (closes[j] < o)
                if fav:
                    c += 1
                else:
                    break
            fav_runs.append(c)
        fr = np.array(fav_runs)
        if len(fr):
            print(f"  mean={fr.mean():.1f} median={np.median(fr):.0f} "
                  f"p75={np.percentile(fr,75):.0f} p90={np.percentile(fr,90):.0f} "
                  f"P(run>=10)={np.mean(fr>=10)*100:.0f}%")
        # EV by hold on these entries
        for hold in [3, 5, 10, 15]:
            pnls = [trade_at_bar(df, sym, e, hold) for e in entries[:400]]
            row(f"  hold={hold}", stats(pnls))

        # ---- C. martingale-add sim (adverse DISTANCE trigger, as the video means it) ----
        print("C. MARTINGALE-ADD (drawdown>6x med -> add; >10x med or favorable -> exit):")
        results_flat, results_mart = [], []
        for e in range(200, n - 40):
            entry = closes[e]
            unit = max(med[e], BROKER_FLOOR.get(sym, 1.0))
            lots = 1.0
            added = False
            done = False
            for j in range(e + 1, min(e + 60, n)):
                adverse_px = (lows[j]) if is_crash else (highs[j])
                dd = (entry - adverse_px) if is_crash else (adverse_px - entry)
                pnl = (closes[j] - entry) * (1 if is_crash else -1)
                if not added and dd > 6 * unit:
                    lots = 2.0  # add second unit at worse price
                    added = True
                if dd > 10 * unit:
                    results_mart.append(pnl * lots)
                    results_flat.append(pnl)
                    done = True
                    break
                if pnl > 0:
                    results_mart.append(pnl * lots)
                    results_flat.append(pnl)
                    done = True
                    break
            if not done:
                j = min(e + 59, n - 1)
                pnl = (closes[j] - entry) * (1 if is_crash else -1)
                results_mart.append(pnl * lots)
                results_flat.append(pnl)
        row("  flat 1.0 lot", stats(results_flat))
        row("  martingale lots", stats(results_mart))