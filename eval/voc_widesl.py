#!/usr/bin/env python3
"""
eval/voc_widesl.py — Wider-SL lever test for Vol over Crash 400/550.

The broker floor is tiny relative to the monster bars (40-53x median), so
gaps blow stops wide open. Hypothesis: gap m collapses with SL distance;
if the partial engine's edge survives at SL = 2-5x median, the products
become live-able.

For each SL multiplier we measure BOTH sides:
  A) tick-level loss-gap m at that SL distance (real bid/ask ticks),
  B) corrected-engine EV in R with partial + cluster + cooldown,
  C) breakeven m = win pts / loss pts of that engine config.
Live-able iff EV > 0 AND measured gap tail (p90) < breakeven m.
"""
import sys
import numpy as np
import pandas as pd
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"

# key -> (slug, floor pts, is_crash)
SYMS = {
    "voc400": ("Vol_over_Crash_400", 3.60, True),
    "voc550": ("Vol_over_Crash_550", 3.20, True),
    "vob400": ("Vol_over_Boom_400", 3.60, False),
    "vob550": ("Vol_over_Boom_550", 3.20, False),
}


def load_candles(key):
    slug = SYMS[key][0]
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df.columns = [c.strip().lower() for c in df.columns]
    df = df.sort_values("epoch").reset_index(drop=True)
    df["range"] = (df["high"] - df["low"]).values
    df["median_range"] = df["range"].rolling(100, min_periods=20).median()
    return df


def load_ticks(key):
    slug = SYMS[key][0]
    b = DATA / f"ticks_{slug}.npy"
    a = DATA / f"ticks_{slug}_ask.npy"
    if not b.exists() or not a.exists():
        return None
    bids = np.load(b)
    asks = np.load(a)
    return (bids + asks) / 2.0


# ---------------------------------------------------------- A) tick gap
def tick_gap(key, sl_d, n_tries=4000, seed=11):
    """Random entries; walk ticks; when SL is hit, m = distance through the
    stop / sl_d. Returns array of m."""
    ticks = load_ticks(key)
    if ticks is None:
        return None
    is_crash = SYMS[key][2]
    rng = np.random.default_rng(seed)
    n = len(ticks)
    gaps = []
    i = 0
    tries = 0
    while tries < n_tries and i < n - 5000:
        tries += 1
        entry = ticks[i]
        tp_d = 2 * sl_d
        j = i + 1
        while j < min(i + 4000, n - 1):
            px = ticks[j]
            if is_crash:  # long
                if px <= entry - sl_d:
                    gaps.append((entry - sl_d - px) / sl_d)
                    break
                if px >= entry + tp_d:
                    break
            else:  # short
                if px >= entry + sl_d:
                    gaps.append((px - (entry + sl_d)) / sl_d)
                    break
                if px <= entry - tp_d:
                    break
            j += 1
        i += rng.integers(200, 3000)
    if not gaps:
        return None
    return np.array(gaps)


# -------------------------------------------------- B) engine at SL mult
def engine_ev(key, sl_mult, hold=2, partial=True, cluster=True, cooldown=3,
              th=3.0):
    df = load_candles(key)
    is_crash = SYMS[key][2]
    n = len(df)
    c = df["close"].values; h = df["high"].values; l = df["low"].values
    o = df["open"].values
    rng_ = (h - l); med = df["median_range"].values
    adv = (c < o) if is_crash else (c > o)
    idx = np.where((rng_ > th * med) & adv & np.isfinite(med) & (rng_ > 0.2))[0]

    pnls = []
    sl_sizes = []
    skip = 0
    for k, i in enumerate(idx):
        if skip > 0:
            skip -= 1
            continue
        if cluster and k >= 2 and (i - idx[k - 2]) <= 10:
            continue
        e = i + 1
        if e + 2 >= n:
            continue
        entry = c[e]
        sl_d = max(med[i], SYMS[key][1]) * sl_mult
        sl_l = (entry - sl_d) if is_crash else (entry + sl_d)
        tp_l = (entry + 2 * sl_d) if is_crash else (entry - 2 * sl_d)

        def half(s, mb):
            for j in range(s, min(s + mb, n)):
                if is_crash:
                    hs, ht = l[j] <= sl_l, h[j] >= tp_l
                else:
                    hs, ht = h[j] >= sl_l, l[j] <= tp_l
                if hs:
                    if adv[j] and rng_[j] > 3 * med[j]:
                        return (l[j] if is_crash else h[j])
                    return sl_l
                if ht:
                    return tp_l
            return c[min(s + mb - 1, n - 1)]

        pA = half(e + 1, hold)
        add_b = False
        # is A still open after bar e+1?
        pA1 = half(e + 1, 1)
        if pA1 == sl_l or pA1 == tp_l:
            add_b = False
        else:
            fav = (c[e + 1] > entry) if is_crash else (c[e + 1] < entry)
            add_b = fav and partial
        if add_b:
            pB = half(e + 2, hold - 1)
            pnl = 0.5 * (pA + pB - 2 * entry) * (1 if is_crash else -1)
        else:
            pnl = 0.5 * (pA - entry) * (1 if is_crash else -1)
        pnls.append(pnl)
        sl_sizes.append(sl_d)
        if pnl <= 0 and cooldown > 0:
            skip = cooldown
    pnls = np.array(pnls)
    if len(pnls) == 0:
        return None
    w = pnls[pnls > 0]; l_ = pnls[pnls <= 0]
    pf = w.sum() / abs(l_.sum()) if len(l_) else 999.0
    # breakeven m: how many x losses can grow before EV=0
    be = w.sum() / abs(l_.sum()) if len(l_) else np.inf
    return dict(n=len(pnls), wr=100 * len(w) / len(pnls), pf=pf,
                ev=pnls.mean(), be=be, worst=pnls.min())


def main():
    for key in ("voc400", "voc550", "vob400", "vob550"):
        print(f"\n{'='*100}\n{key.upper()} ({SYMS[key][0]})")
        print(f"  {'sl_mult':>8}{'SL pts':>8}{'gap m med':>10}{'p90':>7}"
              f"{'max':>7} | {'eng EV':>8}{'WR':>5}{'PF':>6}{'breakeven m':>12}"
              f"{'worst':>8}  verdict")
        for sm in (1.0, 1.5, 2.0, 3.0, 5.0, 8.0):
            sl_d = max(3.6 if "400" in key else 3.2, 0) * sm
            # use actual med-based SL like engine: report max(med,floor)*sm
            df = load_candles(key)
            medv = float(np.nanmedian(df["range"]))
            floor = SYMS[key][1]
            sl_pts = max(medv, floor) * sm
            g = tick_gap(key, sl_pts, n_tries=2500)
            if g is not None:
                gm = f"{np.median(g):.2f}"
                g90 = f"{np.percentile(g,90):.1f}"
                gmax = f"{g.max():.1f}"
            else:
                gm = g90 = gmax = "--"
            e = engine_ev(key, sm)
            if e is None:
                print(f"  {sm:>8.1f}{sl_pts:>8.2f}{gm:>10}{g90:>7}{gmax:>7}"
                      f" | engine none")
                continue
            ev = e["ev"]
            # verdict: gap median vs breakeven
            ok = "OK" if (g is not None and ev > 0
                          and np.percentile(g, 90) < e["be"]) else "no"
            print(f"  {sm:>8.1f}{sl_pts:>8.2f}{gm:>10}{g90:>7}{gmax:>7}"
                  f" | {ev:>+8.3f}{e['wr']:>4.0f}%{e['pf']:>6.2f}"
                  f"{e['be']:>12.2f}{e['worst']:>8.2f}  {ok}")


if __name__ == "__main__":
    main()