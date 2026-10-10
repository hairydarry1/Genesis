#!/usr/bin/env python3
"""Sim-vs-realized survival reconciliation (flip1000).

Question (Sep 28): the deployed-condition day-block ladder says ~82% ruin
at 4 weeks from $10, yet the REAL book did $10 -> $21.91 in 11 days on the
same engine semantics. Which one is lying, and about what?

Method:
  1. Engine day pools under deployed semantics (rolling 5000-tick median +
     Menu-C delay 300): all / long-only / short-only (run_conv).
  2. Realized day pool from archive/flip_daily_digest.csv (the checkpoint
     digest, reconciled to the cent with the executor book; starts at the
     Sep 17 $10 reseed).
  3. Day-by-day alignment over the overlap window (digest days that fall
     inside the archive span): per-day engine vs realized PnL, trip counts,
     correlation, sign agreement.
  4. Instant bootstrap of each pool: N 4-week paths from $10 (day-block
     iid, identical convention to the ladder) -> ruin/flip/alive %.
  5. One-path trace of the realized pool in shuffled order (is ruin even
     reachable by reordering REAL days?).

Read-only: prints; writes nothing.
Usage: python analysis/flip_survival_recon.py
"""
import csv
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_rolling_engine import rolling_medians, run_conv, WINDOW
import flip_longonly_backtest as lb

DIGEST = "archive/flip_daily_digest.csv"
RESEED = "2026-09-17"          # digest book restarts at $10 here
N_PATHS = 20000
HORIZON = 28


def day_of(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def pool_stats(name, days):
    vals = np.array([v.sum() for _, v in days])
    print(f"  {name:11s}: {len(vals)} days, mean {vals.mean():+.2f}, "
          f"median {np.median(vals):+.2f}, min {vals.min():+.2f}, "
          f"max {vals.max():+.2f}, sum {vals.sum():+.2f}")
    return vals


def bootstrap(name, days, rng):
    """Ladder convention: whole days resampled, $10 -> $100 or ruin."""
    fl = ru = 0
    for _ in range(N_PATHS):
        eq, ruined, flipped, _, _ = lb.sim(days, None, rng,
                                           start_eq=10.0, target=100.0)
        fl += flipped
        ru += ruined
    print(f"  {name:11s}: flip {fl / N_PATHS * 100:5.1f}%  "
          f"ruin {ru / N_PATHS * 100:5.1f}%  "
          f"alive {(N_PATHS - fl - ru) / N_PATHS * 100:5.1f}%")


def main():
    # ---- realized days from the digest ----
    real = {}
    with open(DIGEST) as f:
        for r in csv.DictReader(f):
            if r["day_utc"] >= RESEED:
                real[r["day_utc"]] = dict(
                    pnl=float(r["day_pnl_usd"]), rt=int(r["round_trips"]),
                    wins=int(r["wins"]), eq=float(r["model_eq_usd"]))
    print("=== realized (digest, reseed-adjusted) ===")
    for d in sorted(real):
        r = real[d]
        print(f"  {d}  rt={r['rt']:2d} wr={100.0 * r['wins'] / max(r['rt'], 1):5.1f}%  "
              f"pnl={r['pnl']:+7.2f}  eq={r['eq']:6.2f}")

    # ---- engine pools under deployed semantics ----
    print("\n=== engine (rolling median + delay300), deployed semantics ===")
    ms, px = load("flip1000")
    med_roll = rolling_medians(ms, px)
    frozen = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    pools = {}
    for label, sd in (("all", 0), ("long", 1), ("short", -1)):
        nets, cms = run_conv(ms, px, med_roll, frozen, side=sd,
                             mode="counter", mult=5.0, delay=300)
        pools[label] = lb.day_blocks(list(zip(nets.tolist(), cms.tolist())))

    # ---- day-by-day alignment over the overlap window ----
    span0 = datetime.fromtimestamp(ms[WARMUP] / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    eng = {d: v for d, v in pools["all"] if d >= RESEED and d >= span0}
    common = sorted(set(eng) & set(real))
    only_real = sorted(set(real) - set(eng))
    print(f"\n=== day-by-day: engine (deployed) vs realized, overlap {len(common)}d ===")
    print(f"  {'day':10s} {'engLS':>7s} {'engL':>7s} {'engS':>7s} | "
          f"{'real':>7s} {'d/engine trips':>14s} {'real rt':>7s}")
    el, es = [], []
    for d in common:
        ev = dict(pools["all"]).get(d, np.array([0.0])).sum() if d in eng else 0.0
        lv = dict(pools["long"]).get(d, np.array([0.0])).sum() if d in eng else 0.0
        sv = dict(pools["short"]).get(d, np.array([0.0])).sum() if d in eng else 0.0
        nt = len(dict(pools["all"]).get(d, []))
        el.append(ev)
        es.append(real[d]["pnl"])
        print(f"  {d}  {ev:+7.2f} {lv:+7.2f} {sv:+7.2f} | "
              f"{real[d]['pnl']:+7.2f} {nt:14d} {real[d]['rt']:7d}")
    if only_real:
        print(f"  realized-only days (no engine day): {only_real}")
    el, es = np.array(el), np.array(es)
    if len(el) > 2:
        corr = np.corrcoef(el, es)[0, 1]
        agree = (np.sign(el) == np.sign(es)).mean() * 100
        print(f"  overlap sums: engineLS {el.sum():+.2f} vs realized {es.sum():+.2f} "
              f"| corr {corr:+.2f} | same-sign {agree:.0f}%")

    # ---- instant bootstraps (ladder convention) ----
    print(f"\n=== instant bootstrap, {N_PATHS} paths x {HORIZON}d from $10 ===")
    rng = np.random.default_rng(7)
    bootstrap("engine all", pools["all"], rng)
    bootstrap("engine long", pools["long"], rng)
    bootstrap("engine short", pools["short"], rng)
    real_days = [(d, np.array([real[d]["pnl"]])) for d in sorted(real)]
    bootstrap("realized", real_days, rng)

    # ---- can reordering REAL days ruin the book? ----
    print("\n=== realized pool, single shuffled 28d path traces (5 seeds) ===")
    vals = np.array([float(v[0]) for _, v in real_days])
    for seed in range(5):
        r = np.random.default_rng(seed)
        eq = 10.0
        path = []
        for _ in range(HORIZON):
            eq += float(vals[r.integers(len(vals))])
            path.append(eq)
            if eq <= 0:
                break
        print(f"  seed {seed}: final {eq:6.2f}  min {min(path):6.2f}  "
              f"path {' '.join(f'{x:.1f}' for x in path[:10])}...")

    # ---- tail comparison ----
    print("\n=== pool day stats (the ladder's raw material) ===")
    for label in ("all", "long", "short"):
        pool_stats(f"engine {label}", pools[label])
    pool_stats("realized", real_days)

    # ---- the actual funding config: long-only WITH the -$9 stop ----
    print("\n=== funding config: long-only + -$9 daily stop (ladder convention) ===")
    fl = ru = 0
    for _ in range(N_PATHS):
        eq, ruined, flipped, _, _ = lb.sim(pools["long"], 9.0, rng)
        fl += flipped
        ru += ruined
    print(f"  long-only -$9: flip {fl / N_PATHS * 100:5.1f}%  "
          f"ruin {ru / N_PATHS * 100:5.1f}%  "
          f"alive {(N_PATHS - fl - ru) / N_PATHS * 100:5.1f}%")

    # ---- per-trip economics: engine vs realized ----
    eng_trips = sum(len(v) for _, v in pools["all"])
    eng_sum = sum(v.sum() for _, v in pools["all"])
    real_trips = sum(r["rt"] for r in real.values())
    real_sum = sum(r["pnl"] for r in real.values())
    print("\n=== per-trip economics, full archive span vs reseed window ===")
    print(f"  engine (24.8d, full-time): {eng_trips} trips, "
          f"{eng_sum:+.2f} total, {eng_sum / max(eng_trips, 1):+.3f}/trip")
    print(f"  realized (10d, part-time): {real_trips} trips, "
          f"{real_sum:+.2f} total, {real_sum / max(real_trips, 1):+.3f}/trip")


if __name__ == "__main__":
    main()
