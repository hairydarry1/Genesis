#!/usr/bin/env python3
"""
LEVEL-TOUCH TRIGGER TEST — the user's observation, precisely:
"spikes happen at the same moment as a previous low or high"

Three hypotheses, all conditional on a TOUCH event (bar's range overlaps a
prior significant swing level), NOT on where spikes land:

  H1 HAZARD:  P(discharge within 1-5 bars | touch) > unconditional base rate.
              Permutation via matched random bars.
  H2 BOUNCE:  Touch of a prior swing LOW (crash) / HIGH (boom) = classic SNR
              bounce entry: drift-LONG at close of the touch bar, standard
              2xSL/1xSL partial exit hold=3.  vs matched random entries.
  H3 EQUALITY: discharge-bar extreme lands within 0.25 med-range of a prior
              pivot at a HIGHER rate than random-bar extremes (sharper than
              the earlier nearest-distance test).
"""
import pandas as pd
import numpy as np
import sys

sys.path.insert(0, "eval")
from ict_pullback_test import load, discharges, stats, row
from partial_m1_corrected import arrays, half_exit, BROKER_FLOOR

RNG = np.random.RandomState(31)


def pivots(df, sym):
    """3-bar pivots with bar range >= 2x median, deduped within 1 med-range.
    Returns (highs_arr, lows_arr)."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    med0 = np.nanmedian(med[np.isfinite(med)])
    hs, ls = [], []
    for k in range(2, n - 1):
        if not np.isfinite(med[k]) or med[k] <= 0 or rng[k] < 2 * med[k]:
            continue
        if highs[k] >= highs[k - 1] and highs[k] >= highs[k + 1]:
            hs.append(highs[k])
        if lows[k] <= lows[k - 1] and lows[k] <= lows[k + 1]:
            ls.append(lows[k])
    def dedupe(a):
        a = np.array(sorted(a))
        out = []
        for p in a:
            if not out or p - out[-1] > med0:
                out.append(p)
        return np.array(out)
    return dedupe(hs), dedupe(ls)


def touch_bars(df, sym, levels, direction):
    """Bars whose range overlaps any level in `levels` in the given direction.
    direction='below': lows side (swing lows under price, crash bounce)
    direction='above': highs side (swing highs over price, boom bounce).
    A touch requires the level to be approached from the drift side."""
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    n = len(df)
    bars = []
    for j in range(200, n - 10):
        if direction == "below":
            seg = levels[(levels >= lows[j]) & (levels <= highs[j])]
            # approached from above: prior close above the level
            if len(seg) and closes[j - 1] > seg.min():
                bars.append(j)
        else:
            seg = levels[(levels >= lows[j]) & (levels <= highs[j])]
            if len(seg) and closes[j - 1] < seg.max():
                bars.append(j)
    return np.array(bars)


def hazard_test(df, sym, idx_d, touch, horizons=(1, 3, 5)):
    n = len(df)
    spike_flag = np.zeros(n + 1, dtype=bool)
    spike_flag[idx_d] = True
    results = {}
    for h in horizons:
        # vectorized: spike within (b, b+h]
        cs = np.cumsum(spike_flag[:n])  # cs[t] = spikes in [0, t]
        def within(b_arr):
            b_arr = np.asarray(b_arr)
            hi = np.minimum(b_arr + h, n - 1)
            lo = b_arr
            return (cs[hi] - cs[np.maximum(lo - 1, 0)]) > 0
        base = within(np.arange(200, n - h - 1)).mean()
        cond = within(touch).mean()
        rnd = []
        for _ in range(300):
            rb = RNG.randint(200, n - h - 1, len(touch))
            rnd.append(within(rb).mean())
        p = (np.sum(np.array(rnd) >= cond) + 1) / 301
        results[h] = (cond, base, p)
    return results


def bounce_trade(df, sym, touch_bar):
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = int(touch_bar)
    if e + 2 >= len(df):
        return None
    entry = closes[e]
    sl_d = max(med[e], BROKER_FLOOR.get(sym, 1.0))
    tp_d = 2 * sl_d
    sl_level = (entry - sl_d) if is_crash else (entry + sl_d)
    tp_level = (entry + tp_d) if is_crash else (entry - tp_d)
    pA, rA = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                       entry, sl_level, tp_level, e + 1, 3, len(df))
    pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, 1, len(df))
    add_b = rA1 == "timeout" and ((closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry))
    if add_b:
        pB, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_level, tp_level, e + 2, 2, len(df))
        return 0.5 * (pA - entry) * (1 if is_crash else -1) + \
               0.5 * (pB - entry) * (1 if is_crash else -1)
    return 0.5 * (pA - entry) * (1 if is_crash else -1)


if __name__ == "__main__":
    for sym in ["crash300", "crash500", "crash1000", "boom500", "boom1000"]:
        df = load(sym)
        closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
        n = len(df)
        idx_d = discharges(df, sym)
        ph, pl = pivots(df, sym)

        # crash bounce = touch prior swing LOWS; boom bounce = touch prior swing HIGHS
        lvl, direction = (pl, "below") if is_crash else (ph, "above")
        touch = touch_bars(df, sym, lvl, direction)

        print(f"\n{'='*88}\n{sym.upper()}  (pivots: {len(ph)} highs / {len(pl)} lows; touch bars: {len(touch)})\n{'='*88}")

        # H1 hazard
        res = hazard_test(df, sym, idx_d, touch)
        for h, (cond, base, p) in res.items():
            print(f"  H1 HAZARD h={h}: P(spike|touch)={cond*100:.1f}%  base={base*100:.1f}%  p={p:.4f}")

        # H2 bounce trade
        pnls = [bounce_trade(df, sym, b) for b in touch]
        pnls = [p for p in pnls if p is not None]
        s = stats(pnls)
        row("  H2 BOUNCE trade", s)
        # matched random control
        if len(touch) > 20:
            ctrl = []
            for _ in range(5):
                rb = RNG.randint(200, n - 10, len(touch))
                for b in rb:
                    p2 = bounce_trade(df, sym, b)
                    if p2 is not None:
                        ctrl.append(p2)
            row("     random ctrl", stats(ctrl))

        # H3 equality: discharge extreme within 0.25 med-range of a pivot
        med0 = np.nanmedian(med[np.isfinite(med)])
        def eq_rate(bars):
            cnt = 0
            for b in bars:
                ex = lows[b] if is_crash else highs[b]
                allv = np.concatenate([ph, pl])
                j = np.searchsorted(allv, ex)
                d1 = abs(ex - allv[j]) if j < len(allv) else np.inf
                d0 = abs(ex - allv[j - 1]) if j > 0 else np.inf
                if min(d1, d0) <= 0.25 * med0:
                    cnt += 1
            return cnt / len(bars)
        spike_eq = eq_rate(idx_d)
        rnd_eq = []
        for _ in range(200):
            rb = RNG.randint(200, n - 1, len(idx_d))
            rnd_eq.append(eq_rate(rb))
        p3 = (np.sum(np.array(rnd_eq) >= spike_eq) + 1) / 201
        print(f"  H3 EQUALITY: spikes-at-pivot={spike_eq*100:.1f}%  random={np.median(rnd_eq)*100:.1f}%  p={p3:.4f}")
