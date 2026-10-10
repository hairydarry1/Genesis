#!/usr/bin/env python3
"""Entry-filter sweep: the last untested 'filter bad trades' families.

Prior tombstones (documented, not re-litigated): MAG spike-size (M), grind-
prediction pre-entry features (I), THICK attack-size (N), daily halts (K),
session buckets (O - observation only). Entries fire on the first spike
after calm; winners/losers look identical at entry. These four are the
corners that were never measured:

  1. CHURN      - require N distinct spike-free ticks since the last spike
                  before allowing entry (filters spike-clusters, where the
                  detector's median may be locally uncalibrated).
  2. LSTREAK    - skip the k-th entry after j consecutive losses (classic
                  loss-streak cooldown; daily-stop ladder showed halts are
                  poison, but per-streak short cooldowns never measured).
  3. ACTREG     - regime filter on TRAILING spike activity (spikes/hour over
                  the last 2h before entry): trade only 'normal' activity
                  bands, skip the extreme-calm and extreme-busy books.
  4. LIVE-SPREAD (ledger forensics) - does the spread AT ORDER predict the
                  trade's outcome? If wide-spread entries lose more, a live
                  spread ceiling is the one filter with a causal story.

Usage: python analysis/flip_entry_filters.py
"""
import sys
from collections import defaultdict

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT


def run(ms, px, med, side=0, churn=None, lstreak=None, act_lo=None,
        act_hi=None, collect=False):
    """Phase-follower with optional entry filters. Returns nets (+ extras)."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    quiet_since = 0        # churn: ticks since last spike
    last_spike_j = -10**9  # churn: index of previous spike tick
    losses_row = 0         # lstreak: consecutive realized losses
    spike_times = []       # actreg: recent spike timestamps (ms)
    nets, eidx = [], []

    def close(fill):
        nonlocal pos, losses_row
        pnl = (fill - entry) * pos * VPU - SPREAD_USD
        nets.append(pnl)
        losses_row = losses_row + 1 if pnl <= 0 else 0
        pos = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt = 0.0, 0, 0
            continue
        is_spike = bool(big[j - 1])
        if is_spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            spike_times.append(ms[j - 1])
            gap = j - last_spike_j       # quiet ticks BEFORE this spike
            last_spike_j = j
            quiet_since = 0
        else:
            gap = None
            quiet_since += 1

        if pos != 0 and is_spike and int(np.sign(d[j - 1])) != np.sign(pos):
            close(px[j])
            continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and is_spike:
                if churn is not None and gap is not None and gap < churn:
                    continue
                if lstreak is not None and losses_row >= lstreak:
                    losses_row = 0      # one-entry cooldown, then re-arm
                    continue
                if act_lo is not None or act_hi is not None:
                    cutoff = ms[j] - 2 * 3600_000
                    n_spikes = sum(1 for t in spike_times if t >= cutoff)
                    if act_lo is not None and n_spikes < act_lo:
                        continue
                    if act_hi is not None and n_spikes > act_hi:
                        continue
                pos, entry = STEP_LOTS * cur_dir, px[j]
                eidx.append(j)
    if pos != 0:
        close(px[-1])
    if collect:
        return np.array(nets), np.array(eidx)
    return np.array(nets)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    dd = float((np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:] - eq).max()) \
        if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:34s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f}\n")

    print("=== 1. CHURN: min quiet ticks since last spike ===")
    for side, sname in ((0, "LS"), (1, "L")):
        stats(run(ms, px, med, side=side), days, f"{sname} no churn (incumbent)")
        for c in (10, 30, 60):
            stats(run(ms, px, med, side=side, churn=c), days,
                  f"{sname} churn>={c} quiet ticks")
        print()

    print("=== 2. LSTREAK: cooldown after consecutive losses ===")
    for side, sname in ((0, "LS"), (1, "L")):
        stats(run(ms, px, med, side=side), days, f"{sname} no cooldown")
        for k in (2, 3):
            stats(run(ms, px, med, side=side, lstreak=k), days,
                  f"{sname} skip entry after {k} losses")
        print()

    print("=== 3. ACTREG: trailing 2h spike-count band ===")
    for side, sname in ((0, "LS"), (1, "L")):
        stats(run(ms, px, med, side=side), days, f"{sname} no band")
        for lo, hi, lbl in ((4, 60, "4-60"), (10, 60, "10-60"),
                            (10, 40, "10-40")):
            stats(run(ms, px, med, side=side, act_lo=lo, act_hi=hi), days,
                  f"{sname} band {lbl} spikes/2h")
        print()

    print("=== 4. LIVE-SPREAD forensics (ledger): spread vs outcome ===")
    import csv
    rows = [r for r in csv.DictReader(open("data/flip_exec/slippage_ledger.csv"))
            if r["mode"] == "ENTRY" and r["note"] == "done"]
    trips = []
    pend = None
    for r in csv.DictReader(open("data/flip_exec/slippage_ledger.csv")):
        if r["mode"].startswith("PROBE") or r["note"] != "done":
            continue
        if r["mode"] == "ENTRY":
            pend = r
        elif r["mode"] == "EXIT" and pend is not None:
            e, x = float(pend["actual_deal"]), float(r["actual_deal"])
            pnl = (x - e) * 0.1 if pend["side"] == "buy" else (e - x) * 0.1
            trips.append((float(pend["spread_at_order"]), pnl,
                          pend["side"]))
            pend = None
    if len(trips) >= 8:
        sp = np.array([t[0] for t in trips])
        pn = np.array([t[1] for t in trips])
        med_sp = np.median(sp)
        lo_m, hi_m = sp <= med_sp, sp > med_sp
        for m, lbl in ((lo_m, f"spread<= {med_sp:.3f}u"),
                       (hi_m, f"spread > {med_sp:.3f}u")):
            sel = pn[m]
            if len(sel):
                print(f"  {lbl:22s} n={len(sel):2d}  WR={(sel>0).mean()*100:4.0f}%  "
                      f"mean=${sel.mean():+.2f}")
        if len(sp) > 8:
            print(f"  corr(spread, pnl) = {np.corrcoef(sp, pn)[0,1]:+.3f}  (n={len(sp)})")
    else:
        print(f"  n={len(trips)} ledger trips - below 8-trip read floor; re-run later")


if __name__ == "__main__":
    main()
