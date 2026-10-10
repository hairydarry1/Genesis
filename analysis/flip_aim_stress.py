#!/usr/bin/env python3
"""Daily-profit-aim SEED STRESS TEST (Appendix X follow-up, Sep 15).

Why: Appendix X's seed check was partial (3 seeds, subset of cells). Before
the Oct 8 decision, aim $3's headline (incumbent ladder 97.5% flip / 2.3%
ruin vs 65.2/19.1 no-aim) needs a full 7-seed treatment across every menu
cell it could reach, with paired sampling for power.

Design (paired, 2000 day-block sims each):
  - ONE sim = 28 days; one draw = one whole archive UTC day's realized PnL.
  - SAME seed drives the identical day-draw sequence in the no-aim and aim
    walks of a pair (each seed = a paired comparison; seeds vary the draw).
  - Sizing semantics exactly as flip_next_gen section 4: trades generated
    at fixed 0.1 lot, ladder applied in the bootstrap via ladder_fn
    (anti-martingale: m = ladder_fn(eq) AFTER each day's equity update).

Aim semantics = the deployable mechanism (executor day_state): halt NEW
entries once cumulative day PnL >= aim; the trade that crosses still lands
in full, later trades that day are skipped. Days never crossing keep their
full sum.

Anchors:
  1. no-aim rows vs flip_next_gen.day_block (same seed, same day values) —
     tolerance 2*sqrt(2)*SE (~6.3pp combined); exact match expected if the
     RNG consumption patterns coincide.
  2. aim$5 + SCALED-halt bug cell (Appendix X: incumbent 55.8 / timebnd
     81.4) — approximate intra-day repro, sanity band only.

Also printed: transformed day-sum mean/SD per rule (the SD-compression
mechanism claim: $5.98 -> $3.92 at aim $3).

Usage: python analysis/flip_aim_stress.py [--seeds 11 23 29 37 47 53 61]
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_next_gen import SYMBOL, run, day_block, ladder_fn

OUT_DIR = "data/flip_aim_stress"
N_SIMS = 2000
HORIZON = 28
START_EQ = 10.0
TARGET = 100.0
SE = 100.0 / np.sqrt(N_SIMS)               # 1 sd of a percentage ~ 2.24pp
TOL = 2.0 * np.sqrt(2.0) * SE              # combined anchor tolerance ~6.3pp


def to_days(nets, cms):
    """(nets, close_ms) -> ordered {date: [pnl, ...]} (archive day order)."""
    days = {}
    for pnl, t in zip(nets, cms):
        d = datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()
        days.setdefault(d, []).append(float(pnl))
    return days


def day_values(days_dict):
    """Ordered (keys, per-trade arrays, day-sum values) sorted by date,
    matching flip_next_gen.day_block's sorted-days construction."""
    keys = sorted(days_dict.keys())
    lists = [np.asarray(days_dict[k], dtype=float) for k in keys]
    vals = np.array([l.sum() for l in lists])
    return keys, lists, vals


def walk(vals, lists, aim, seed, sizing):
    """Day-block bootstrap, optional entry-halt aim. RNG consumption is the
    same shape as flip_next_gen.day_block (one choice(.,28) per sim)."""
    rng = np.random.default_rng(seed)
    n = len(vals)
    flipped = ruined = 0
    for _ in range(N_SIMS):
        eq = START_EQ
        m = 1.0
        idx = rng.choice(n, size=HORIZON, replace=True)
        for di in idx:
            tl = lists[di]
            raw = vals[di]
            if aim is not None:
                cum = np.cumsum(tl)
                hit = np.nonzero(cum >= aim)[0]
                dp = float(cum[hit[0]]) if len(hit) else float(raw)
            else:
                dp = float(raw)
            eq += dp * m
            if eq <= 0:
                ruined += 1
                break
            if eq >= TARGET:
                flipped += 1
                break
            m = ladder_fn(eq) if sizing == "ladder" else 1.0
    return flipped / N_SIMS * 100.0, ruined / N_SIMS * 100.0


def scaled_halt_flip(lists, seed, aim):
    """Approximate Appendix X bug cell: aim + a -9 SCALED daily halt
    (executor semantics: halt when raw_cum * m <= -9, crossing trade lands)."""
    rng = np.random.default_rng(seed)
    n = len(lists)
    flipped = 0
    for _ in range(N_SIMS):
        eq = START_EQ
        m = 1.0
        idx = rng.choice(n, size=HORIZON, replace=True)
        for di in idx:
            tl = lists[di]
            cum = np.cumsum(tl)
            dp = float(cum[-1]) if len(cum) else 0.0
            stop_hit = np.nonzero(cum * m <= -9.0)[0]
            if len(stop_hit):
                dp = float(cum[stop_hit[0]])      # crossing trade lands
            if aim is not None:
                aim_hit = np.nonzero(cum >= aim)[0]
                if len(aim_hit):
                    dp = min(dp, float(cum[aim_hit[0]]))
            eq += dp * m
            if eq <= 0:
                break
            if eq >= TARGET:
                flipped += 1
                break
            m = ladder_fn(eq)
    return flipped / N_SIMS * 100.0


def transform_stats(lists, vals, aim):
    """Deterministic transformed day sums (no bootstrap): mean/SD."""
    if aim is None:
        t = vals
    else:
        t = []
        for tl in lists:
            cum = np.cumsum(tl)
            hit = np.nonzero(cum >= aim)[0]
            t.append(float(cum[hit[0]]) if len(hit) else float(tl.sum()))
        t = np.array(t)
    return t.mean(), t.std(ddof=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, nargs="+",
                    default=[11, 23, 29, 37, 47, 53, 61])
    args = ap.parse_args()
    seeds = args.seeds

    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    arc_days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {arc_days:.1f} days gap-masked, "
          f"seeds={seeds} (N={N_SIMS}/cell, paired)\n")

    engines = {"incumbent": dict(mode="counter", k=None),
               "timebnd5000": dict(mode="timebnd", k=5000)}
    sim = {}
    for ename, kw in engines.items():
        nets, cms = run(ms, px, med, side=1, want_ms=True, **kw)
        sim[ename] = to_days(nets, cms)
        n_tr = int(len(nets))
        print(f"  engine {ename:11s}: {n_tr:4d} long-only trips, "
              f"{len(sim[ename])} archive days")

    _, lists_i, vals_i = day_values(sim["incumbent"])
    _, lists_t, vals_t = day_values(sim["timebnd5000"])
    lists = {"incumbent": lists_i, "timebnd5000": lists_t}
    vals = {"incumbent": vals_i, "timebnd5000": vals_t}

    # ---- anchor 1: no-aim vs flip_next_gen.day_block -----------------------
    print("\nanchor 1 - no-aim ladder vs flip_next_gen.day_block:")
    ok_all = True
    # build trades exactly as flip_next_gen section 4 does
    for ename, kw in engines.items():
        nets, cms = run(ms, px, med, side=1, want_ms=True, **kw)
        trades = list(zip(nets.tolist(), cms.tolist()))
        ref_fl, ref_ru = day_block(trades, lots_mult=ladder_fn,
                                   seed=seeds[0])
        mine = walk(vals[ename], lists[ename], None, seeds[0], "ladder")
        d = max(abs(mine[0] - ref_fl), abs(mine[1] - ref_ru))
        ok = d <= TOL
        ok_all &= ok
        print(f"  {ename:11s} mine {mine[0]:5.1f}/{mine[1]:5.1f}  "
              f"ref {ref_fl:5.1f}/{ref_ru:5.1f}  maxd={d:4.2f}pp "
              f"tol={TOL:.1f}  {'OK' if ok else 'MISMATCH'}")
    if not ok_all:
        print("\nANCHOR FAILED - stopping, numbers below untrustworthy.")
        sys.exit(2)

    # ---- anchor 2: scaled-halt bug cell (sanity band) ----------------------
    print("\nanchor 2 - aim$5 + SCALED -9 halt (bug repro, sanity band):")
    for ename in engines:
        fls = [scaled_halt_flip(lists[ename], s, 5.0) for s in seeds[:3]]
        print(f"  {ename:11s} flip {np.mean(fls):5.1f}% "
              f"(per-seed {', '.join(f'{x:.1f}' for x in fls)})  "
              f"[Appendix X ref: incumbent 55.8 / timebnd 81.4]")

    # ---- day-sum transform stats (mechanism) -------------------------------
    print("\nday-sum transform (deterministic, archive):")
    for ename in engines:
        for aim in (None, 3.0, 4.0):
            mu, sd = transform_stats(lists[ename], vals[ename], aim)
            lbl = "no-aim" if aim is None else f"aim ${aim:.0f}"
            print(f"  {ename:11s} {lbl:8s} mean=${mu:+5.2f}  SD=${sd:4.2f}")

    # ---- main sweep ---------------------------------------------------------
    os.makedirs(OUT_DIR, exist_ok=True)
    rows = []
    print("\nper-seed flip%/ruin% (mean±sd over seeds):")
    print(f"{'engine':11s} {'sizing':7s} {'rule':8s} "
          + " ".join(f"{s:>11d}" for s in seeds)
          + "     mean        sd")
    for ename in engines:
        for sizing in ("fixed", "ladder"):
            for aim in (None, 3.0, 4.0):
                rname = "no-aim" if aim is None else f"aim ${aim:.0f}"
                cells = [walk(vals[ename], lists[ename], aim, s, sizing)
                         for s in seeds]
                for s, (fl, ru) in zip(seeds, cells):
                    rows.append(dict(engine=ename, sizing=sizing, rule=rname,
                                     seed=s, flip=fl, ruin=ru))
                fls = np.array([c[0] for c in cells])
                rus = np.array([c[1] for c in cells])
                print(f"{ename:11s} {sizing:7s} {rname:8s} "
                      + " ".join(f"{f:5.1f}/{r:5.1f}" for f, r in cells)
                      + f"  {fls.mean():5.1f}/{rus.mean():5.1f}"
                      f"  ±{fls.std(ddof=1):4.1f}")
            print()

    # ---- paired deltas: aim $3 vs no-aim ------------------------------------
    print("paired aim-$3 minus no-aim (same seed, 7 seeds):")
    for ename in engines:
        for sizing in ("fixed", "ladder"):
            for metric, mname in ((0, "flip"), (1, "ruin")):
                d = []
                for s in seeds:
                    a = walk(vals[ename], lists[ename], 3.0, s, sizing)
                    n = walk(vals[ename], lists[ename], None, s, sizing)
                    d.append(a[metric] - n[metric])
                d = np.array(d)
                stable = "STABLE" if (d > 0).all() or (d < 0).all() else "MIXED"
                print(f"  {ename:11s} {sizing:7s} {mname:5s}: "
                      f"{d.mean():+6.1f}pp  range [{d.min():+.1f}, "
                      f"{d.max():+.1f}]  {stable}")

    # ---- persist -------------------------------------------------------------
    csv_path = os.path.join(OUT_DIR, "per_seed.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(OUT_DIR, "summary.json"), "w") as f:
        json.dump(dict(archive_days=arc_days, seeds=seeds, n_sims=N_SIMS,
                       rows=rows), f, indent=1)
    print(f"\nwrote {csv_path} and {OUT_DIR}/summary.json")


if __name__ == "__main__":
    main()
