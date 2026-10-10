#!/usr/bin/env python3
"""
SCHEDULE OPT — step (overlap) x cooldown-window sweep for the base rate
========================================================================
The base-rate finding: partial-entry geometry on a plain schedule ≈ or >
the spike book. Now find the optimal schedule:
  A. STEP SWEEP: S = 1..60 bars. S < 3 allows overlapping concurrent
     positions (a ladder); S >= 3 is non-overlapping. Report EV/trade,
     throughput (PnL per 1000 bars), worst trade, loss rate.
  B. COOLDOWN SWEEP: after a losing trade at bar b, skip entries until
     b + cd bars. cd in {0, 10, 20, 40, 80, 160}.
  C. BEST COMBO per symbol.
Efficient: precomputed arrays, one trade walk per entry bar.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

DATA = Path("../data")
FLOORS = {"crash300": 2.43, "crash500": 0.85, "crash1000": 1.14,
          "boom500": 0.85, "boom1000": 1.14, "crash150": 0.1, "boom150": 0.1}
FILES = {s: f"candles_{s}_1min.parquet" for s in FLOORS}


def prep(sym):
    df = pd.read_parquet(DATA / FILES[sym])
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("time" if "time" in df.columns else "epoch").reset_index(drop=True)
    opens = df["open"].values.astype(float)
    closes = df["close"].values.astype(float)
    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    rng = (highs - lows)
    med = pd.Series(rng).rolling(100, min_periods=20).median().values
    # adverse/discharge direction (mirrors engine arrays()): crash=down, boom=up
    adv = (closes < opens) if "crash" in sym else (closes > opens)
    n = len(df)
    return opens, closes, highs, lows, rng, med, adv, n


# ---- actual runner ----
def run_schedule(sym, opens, closes, highs, lows, rng, med, adv, n, step, cd):
    is_crash = "crash" in sym
    floor = FLOORS[sym]
    pnls = []
    entry_bars = []
    skip_until = -1
    i = 100
    while i < n:
        if i < skip_until:
            i += step
            continue
        if not (np.isfinite(med[i]) and med[i] > 0):
            i += 1
            continue
        e = i + 1
        if e + 2 >= n:
            break
        entry = closes[e]
        sl_d = max(med[i], floor)
        # Half A: bars e+1 .. e+2 (gap-through on adverse discharge fills)
        pa = None
        for j in range(e + 1, min(e + 3, n)):
            if is_crash:
                if lows[j] <= entry - sl_d:
                    pa = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                    break
                if highs[j] >= entry + 2 * sl_d:
                    pa = entry + 2 * sl_d
                    break
            else:
                if highs[j] >= entry + sl_d:
                    pa = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                    break
                if lows[j] <= entry - 2 * sl_d:
                    pa = entry - 2 * sl_d
                    break
        if pa is None:
            pa = closes[min(e + 2, n - 1)]
        # is A open at close[e+1]? and favorable?
        b1 = e + 1
        if is_crash:
            a_open = not (lows[b1] <= entry - sl_d or highs[b1] >= entry + 2 * sl_d)
            fav = closes[b1] > entry
        else:
            a_open = not (highs[b1] >= entry + sl_d or lows[b1] <= entry - 2 * sl_d)
            fav = closes[b1] < entry
        if a_open and fav and b1 + 1 < n:
            # Half B: entered at close[b1], walks bar b1+1 (one bar)
            pb = None
            for j in range(b1 + 1, min(b1 + 2, n)):
                if is_crash:
                    if lows[j] <= entry - sl_d:
                        pb = lows[j] if (adv[j] and rng[j] > 3 * med[j]) else entry - sl_d
                        break
                    if highs[j] >= entry + 2 * sl_d:
                        pb = entry + 2 * sl_d
                        break
                else:
                    if highs[j] >= entry + sl_d:
                        pb = highs[j] if (adv[j] and rng[j] > 3 * med[j]) else entry + sl_d
                        break
                    if lows[j] <= entry - 2 * sl_d:
                        pb = entry - 2 * sl_d
                        break
            if pb is None:
                pb = closes[min(b1 + 1, n - 1)]
            if is_crash:
                pnl = 0.5 * (pa - entry) + 0.5 * (pb - entry)
            else:
                pnl = 0.5 * (entry - pa) + 0.5 * (entry - pb)
        else:
            pnl = 0.5 * ((pa - entry) if is_crash else (entry - pa))
        pnls.append(pnl)
        entry_bars.append(i)
        if pnl < 0 and cd > 0:
            skip_until = i + cd
        i += step
    return np.array(pnls), np.array(entry_bars)


def report(sym, step, cd, pnls, nbars):
    n = len(pnls)
    if n < 20:
        return None
    w = pnls[pnls > 0]; l = pnls[pnls <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) and l.sum() else float("inf")
    throughput = pnls.sum() / nbars * 1000
    return {"n": n, "ev": pnls.mean(), "pf": pf, "thr": throughput,
            "worst": pnls.min(), "loss_rate": len(l) / n}


def main():
    syms = sys.argv[1:] if len(sys.argv) > 1 else \
        ["crash300", "crash500", "crash1000", "boom500", "boom1000", "crash150", "boom150"]
    print(f"{'sym':<10}{'step':>5}{'cd':>4}{'n':>7}{'EV/tr':>9}{'PF':>6}"
          f"{'PnL/1kb':>9}{'worst':>8}{'loss%':>7}")
    # phase A: step (overlap) sweep
    for sym in syms:
        opens, closes, highs, lows, rng, med, adv, n = prep(sym)
        for step in (1, 2, 3, 5, 10, 20, 40):
            pnls, bars = run_schedule(sym, opens, closes, highs, lows, rng, med, adv, n, step, cd=0)
            r = report(sym, step, 0, pnls, n)
            if r:
                print(f"{sym:<10}{step:>5}{0:>4}{r['n']:>7}{r['ev']:>+9.4f}{r['pf']:>6.2f}"
                      f"{r['thr']:>9.2f}{r['worst']:>+8.3f}{100*r['loss_rate']:>6.1f}%")
        print()
    # phase B: cooldown sweep at each symbol's best-throughput step (1)
    print("== cooldown sweep @ step 1 ==")
    for sym in syms:
        opens, closes, highs, lows, rng, med, adv, n = prep(sym)
        for cd in (0, 10, 20, 40, 80, 160):
            pnls, bars = run_schedule(sym, opens, closes, highs, lows, rng, med, adv, n, 1, cd)
            r = report(sym, 1, cd, pnls, n)
            if r:
                print(f"{sym:<10}{1:>5}{cd:>4}{r['n']:>7}{r['ev']:>+9.4f}{r['pf']:>6.2f}"
                      f"{r['thr']:>9.2f}{r['worst']:>+8.3f}{100*r['loss_rate']:>6.1f}%")
        print()
    # phase C: best combo per symbol (step x cd grid)
    print("== best (step, cd) per symbol ==")
    for sym in syms:
        opens, closes, highs, lows, rng, med, adv, n = prep(sym)
        best = None
        for step in (1, 2, 3, 5, 10):
            for cd in (0, 10, 20, 40, 80):
                pnls, bars = run_schedule(sym, opens, closes, highs, lows, rng, med, adv, n, step, cd)
                r = report(sym, step, cd, pnls, n)
                if r and r["n"] >= 200 and (best is None or r["ev"] > best["ev"]):
                    best = dict(r, step=step, cd=cd)
        print(f"{sym:<10} best step={best['step']:>2} cd={best['cd']:>3} "
              f"EV/tr={best['ev']:>+9.4f} PF={best['pf']:>5.2f} "
              f"PnL/1kb={best['thr']:>8.2f} worst={best['worst']:>+8.3f} n={best['n']}")


if __name__ == "__main__":
    main()