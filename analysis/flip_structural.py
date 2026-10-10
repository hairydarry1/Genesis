#!/usr/bin/env python3
"""Structural concepts on flip1000: S/R, order blocks, MTF trend, round numbers.

Classic retail concepts, tested on OUR symbol with the validated engine.
Causal honesty: a synthetic index has an algorithmic counterparty — no
institutions, no human order clusters. But two concepts have mechanical
analogs here:
  - "support/resistance" -> prior spike EXTREMES (the market's own violence
    map; spike bases are the documented absorption zones)
  - "order block"        -> the pre-spike calm ZONE (last price area before
    violence; "return to the zone = continuation" is the SMC claim)
  - "MTF confirmation"   -> M5/M30/H1 trend derived from the same ticks.
    NOTE: on a synthetic, higher TFs contain ZERO information not already
    in the tick stream (unlike human markets with different participant
    cohorts per timeframe). Any signal is a re-slicing of what the tick
    studies already searched. Tested anyway to make it empirical.

Method: incumbent engine (validated vs fleet), entries bucketed by
pre-entry features computed strictly from ticks BEFORE the entry spike.
All buckets must sum to the incumbent trade count (reconciliation check).

Usage: python analysis/flip_structural.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_entry_filters import PER_UNIT, SPREAD_USD, VPU, run
from flip_conditionality import load, WARMUP
from flip_concepts import MULT, STEP_LOTS

SYMBOL = "flip1000"
OB_TICKS = 30          # calm window before a spike = the "order block"
OB_KEEP = 10           # remember the last 10 zones
LEVELS_KEEP = 10       # last 10 spike highs / lows as S/R levels
TOL_NEAR = 25 * 0.0084  # ~0.21u: "near" a level (in price units)
TOL_TIGHT = 5 * 0.0084  # ~0.042u: one spike-width away


def hms(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%H:%MZ")


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    nets, eidx = run(ms, px, med, side=1, collect=True)  # long-only branch
    entry_set = set(eidx.tolist())
    print(f"archive {days:.1f}d, med={med:.5f}, long-only incumbent trips={len(nets)}")

    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr

    highs, lows = [], []   # (j, price) spike extremes, strictly historical
    obs = []               # (j, lo, hi) pre-spike calm zones
    # M5 bar closes for trend
    bar = {}               # bar_id -> last price in that bar

    def bar_trend(j, nback):
        """sign of close(now completed bar) - close(nback bars earlier)."""
        b = ms[j] // 300_000
        if (b - 1 - nback) not in bar or (b - 1) not in bar:
            return 0
        return int(np.sign(bar[b - 1] - bar[b - 1 - nback]))

    feats = []             # per-entry feature dict
    pos = 0.0
    cur_dir, cnt = 0, 0
    for j in range(WARMUP, len(px)):
        # level/OB memory update on spike (before entry check: the entry's own
        # spike is at j-1; we must NOT include it, so store with j and filter
        # by spike time < j-1 when reading)
        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if s > 0:
                highs.append((j, px[j]))
            else:
                lows.append((j, px[j]))
            w = px[max(WARMUP, j - OB_TICKS):j]
            obs.append((j, float(w.min()), float(w.max())))
            highs[:] = highs[-LEVELS_KEEP:]
            lows[:] = lows[-LEVELS_KEEP:]
            obs[:] = obs[-OB_KEEP:]
        bar[ms[j] // 300_000] = px[j]

        if pos == 0 and j in entry_set:
            p = float(px[j])
            old_h = [h for hj, h in highs if hj < j - 1]
            old_l = [l for lj, l in lows if lj < j - 1]
            res = min((h - p for h in old_h if h > p), default=np.inf)
            sup = min((p - l for l in old_l if l < p), default=np.inf)
            in_ob = any(lo <= p <= hi for _, lo, hi in obs)
            feats.append(dict(
                j=j,
                res=res, sup=sup,
                near=res < TOL_NEAR or sup < TOL_NEAR,
                tight=res < TOL_TIGHT or sup < TOL_TIGHT,
                in_ob=in_ob,
                t6=bar_trend(j, 6), t12=bar_trend(j, 12), t24=bar_trend(j, 24),
                round_dist=abs(p - round(p / 100.0) * 100.0),
            ))

    assert len(feats) == len(nets), (len(feats), len(nets))
    net_of = {f["j"]: n for f, n in zip(feats, nets)}

    def bucket(name, pred):
        sel = [net_of[f["j"]] for f in feats if pred(f)]
        if not sel:
            print(f"  {name:38s} n=  0  (never fires)")
            return
        a = np.array(sel)
        wr = (a > 0).mean() * 100
        print(f"  {name:38s} n={len(a):3d}  WR={wr:4.0f}%  mean=${a.mean():+.2f}"
              f"  sum=${a.sum():+7.2f}  ({a.sum()/days*7:+.2f}/wk)")

    print("\n=== A. S/R: distance to prior spike extremes (res above / sup below) ===")
    bucket("near any level (<25 med)", lambda f: f["near"])
    bucket("far from all levels (>=25 med)", lambda f: not f["near"])
    bucket("tight (<5 med, at the level)", lambda f: f["tight"])
    bucket("res <25 med (overhead extreme)", lambda f: f["res"] < TOL_NEAR)
    bucket("clear overhead (res >=25 med)", lambda f: f["res"] >= TOL_NEAR)
    bucket("sup <25 med (floor extreme)", lambda f: f["sup"] < TOL_NEAR)

    print("\n=== B. ORDER BLOCK: entry inside pre-spike calm zone ===")
    bucket("inside a recent OB zone", lambda f: f["in_ob"])
    bucket("outside all OB zones", lambda f: not f["in_ob"])

    print("\n=== C. MTF: higher-TF trend at entry (long-only) ===")
    def tb(f, k):
        return f[f"t{k}"]
    for lbl, k in (("M5 x6 (30m momentum)", 6), ("M5 x12 (1h)", 12), ("M5 x24 (2h)", 24)):
        sel_up = [net_of[f["j"]] for f in feats if tb(f, k) > 0]
        sel_dn = [net_of[f["j"]] for f in feats if tb(f, k) < 0]
        for lbl2, sel in (("UP", sel_up), ("DOWN", sel_dn)):
            if sel:
                a = np.array(sel)
                print(f"  {lbl:22s} trend {lbl2:4s}      n={len(a):3d}  WR={(a>0).mean()*100:4.0f}%  mean=${a.mean():+.2f}")
            else:
                print(f"  {lbl:22s} trend {lbl2:4s}      n=  0")

    print("\n=== D. ROUND NUMBERS (nearest 100.0 price level) ===")
    bucket("within 0.25 of round 100", lambda f: f["round_dist"] < 0.25)
    bucket("within 0.50 of round 100", lambda f: f["round_dist"] < 0.50)
    bucket(">= 0.50 away", lambda f: f["round_dist"] >= 0.50)

    print("\nreference: overall WR/mean printed by engine above; bucket ns sum"
          f" to {len(nets)} per family where partitions are complete")


if __name__ == "__main__":
    main()
