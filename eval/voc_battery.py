#!/usr/bin/env python3
"""
eval/voc_battery.py — Full battery on the newly-obtainable "Vol over"
family (Crash/Boom 400/550/750): structure (direction bias, opposite-bar
caps), reversion-by-threshold, tick hazard, and fade EV with partial
entry, entry sweep. Vectorized median via pandas rolling.
"""
import sys
import numpy as np
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
DATA = ROOT / "data"

SYMBOLS = {
    "vol_over_crash_400": ("Vol over Crash 400", 3.60, "crash"),
    "vol_over_crash_550": ("Vol over Crash 550", 3.20, "crash"),
    "vol_over_crash_750": ("Vol over Crash 750", 2.50, "crash"),
    "vol_over_boom_400":  ("Vol over Boom 400", 3.60, "boom"),
    "vol_over_boom_550":  ("Vol over Boom 550", 3.20, "boom"),
    "vol_over_boom_750":  ("Vol over Boom 750", 2.50, "boom"),
}
FLOOR = {k: v[1] for k, v in SYMBOLS.items()}
KIND = {k: v[2] for k, v in SYMBOLS.items()}
MED_WIN = 100


def load(key):
    import pandas as pd
    name = SYMBOLS[key][0]
    slug = name.replace(" ", "_").replace("-", "")
    df = pd.read_parquet(DATA / f"candles_{slug}_1min.parquet")
    df = df.sort_values("epoch").reset_index(drop=True)
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    l = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    rng = pd.Series(h - l)
    med = rng.rolling(MED_WIN, min_periods=30).median().values.astype(float)
    return o, h, l, c, med


def load_ticks(key):
    name = SYMBOLS[key][0]
    slug = name.replace(" ", "_").replace("-", "")
    bpath = DATA / f"ticks_{slug}.npy"
    apath = DATA / f"ticks_{slug}_ask.npy"
    if not bpath.exists():
        return None
    return np.load(bpath), np.load(apath)


def structure(key, h, l, c, med):
    rng = h - l
    medv = float(np.nanmedian(rng))
    up = c - np.concatenate([[c[0]], c[:-1]])  # placeholder, unused
    adv = (c > np.concatenate([[o := c[0]], o])) if False else None
    return dict(med=medv)


def fade_ev(key, o, h, l, c, med, entry, th, floor, hold=2):
    """Fade discharge. crash-kind: LONG after crash bar; boom: SHORT after
    boom bar. SL = max(med,floor), TP = 2x, hold bars. Returns list of R."""
    n = len(o)
    body = c - o
    adv = (body < 0) if KIND[key] == "crash" else (body > 0)
    # median-adaptive: a bar is a discharge if its range > th * rolling med
    ok = (med > 0) & ~np.isnan(med)
    out = []
    i = MED_WIN
    while i < n:
        if not ok[i] or not adv[i] or (h[i] - l[i]) <= th * med[i]:
            i += 1
            continue
        sl = max(med[i], floor)
        tp = 2.0 * sl
        e = i + entry
        if e + 1 >= n:
            break
        entry_px = c[e]
        pnl = None
        for k in range(e + 1, min(e + hold + 1, n)):
            if KIND[key] == "crash":
                if (h[k] - entry_px) >= tp:
                    pnl = tp
                    break
                if (entry_px - l[k]) >= sl:
                    pnl = -sl
                    break
            else:
                if (entry_px - l[k]) >= tp:
                    pnl = tp
                    break
                if (h[k] - entry_px) >= sl:
                    pnl = -sl
                    break
        if pnl is None:
            close = c[min(e + hold, n - 1)]
            pnl = (close - entry_px) if KIND[key] == "crash" \
                else (entry_px - close)
        out.append(pnl / sl)
        i += 1
    return np.array(out)


def tick_hazard(key):
    t = load_ticks(key)
    if t is None:
        return
    bids, asks = t
    mid = (bids + asks) / 2
    spr = asks - bids
    print(f"    ticks {len(mid):,} median spread {np.median(spr):.4f} "
          f"max {spr.max():.4f}", flush=True)
    d = np.abs(np.diff(mid))
    th = float(np.percentile(d, 99))
    sig = d > th
    if sig.sum() < 50:
        return
    idx = np.where(sig)[0]
    base = float(np.median(d))
    parts = []
    for lag in (1, 3, 5, 10, 30):
        nxt = idx[idx + lag < len(d)]
        if len(nxt) == 0:
            continue
        parts.append(f"lag{lag}:{d[nxt + lag].mean() / base:.2f}x")
    print("    hazard after big tick: " + " ".join(parts), flush=True)


def run_symbol(key):
    name = SYMBOLS[key][0]
    floor = FLOOR[key]
    kind = KIND[key]
    o, h, l, c, med = load(key)
    rng = h - l
    medv = float(np.nanmedian(rng))
    print(f"\n== {name} (floor={floor}, kind={kind}) ==", flush=True)
    # direction bias over full sample
    body = c - o
    up_sum = float(body[body > 0].sum())
    dn_sum = float(-body[body < 0].sum())
    bias = up_sum / dn_sum if dn_sum > 0 else np.inf
    # adverse (opposite-kind) bars and their max size as fraction of med
    if kind == "crash":
        fav = body < 0      # down bars carry the discharge
        advb = body > 0
    else:
        fav = body > 0
        advb = body < 0
    fav_rng = rng[fav]
    adv_rng = rng[advb]
    print(f"    median bar {medv:.3f}  floor/med {floor/medv:.2f}  "
          f"dir bias {bias:.2f}  adverse max {adv_rng.max():.2f} pts "
          f"({adv_rng.max()/medv:.2f}x med)  favorable max {fav_rng.max():.2f} "
          f"({fav_rng.max()/medv:.2f}x med)", flush=True)
    # capped retracement? fraction of adverse bars over thresholds
    for thv in (1.5, 2.0, 3.0):
        fa = int((adv_rng > thv * medv).sum())
        fb = int((fav_rng > thv * medv).sum())
        print(f"    bars > {thv}x med: adverse {fa}, favorable {fb}", flush=True)
    tick_hazard(key)
    print("    fade EV (R/trade, SL=max(med,floor), TP=2x, hold 2):", flush=True)
    for th in (2.0, 2.5, 3.0):
        out = fade_ev(key, o, h, l, c, med, 1, th, floor)
        if len(out):
            print(f"      th={th}: EV={out.mean():+.3f}R n={len(out)} "
                  f"win%={100*(out>0).mean():.0f}% worst={out.min():+.2f}R",
                  flush=True)
        else:
            print(f"      th={th}: n=0", flush=True)
    print("    entry sweep at th=2.5:", flush=True)
    for en in (1, 2, 3):
        out = fade_ev(key, o, h, l, c, med, en, 2.5, floor)
        if len(out):
            print(f"      entry+{en}: EV={out.mean():+.3f}R n={len(out)} "
                  f"win%={100*(out>0).mean():.0f}% worst={out.min():+.2f}R",
                  flush=True)
        else:
            print(f"      entry+{en}: n=0", flush=True)


def main():
    for key in SYMBOLS:
        run_symbol(key)


if __name__ == "__main__":
    main()