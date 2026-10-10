#!/usr/bin/env python3
"""Deep probe of Flip mechanics: loopholes and structural edges (flip1000).

Motivated by Deriv's official description (blog, Aug 2026):
  - "Each large move may END the current phase ... or BE ABSORBED, leaving the
    current phase in play." -> Section A measures absorption and whether the
    rate differs for up-spikes vs down-spikes (could explain the L/S edge).
  - "Large moves can cluster or stretch out ... no tick-counting tells you
    when the next arrives." -> Section D tests the memoryless claim directly.
  - "Moves at the higher end tend to be larger" -> Section C tests spike-size
    clustering within the index (conditional next-spike size).

Sections:
  A. ABSORPTION: for every counter-spike, does the post-spike drift confirm
     the flip or resume the old phase? Rates by direction and phase age.
  B. MICRO-DRIFT: mean price change 5/10/20/50/100 ticks after each spike.
  C. SPIKE-SIZE CLUSTERING: E[|next|] given |last| quartile + lag-1 rank corr.
  D. TIME HAZARD: inter-spike interval CV (Poisson = 1), hazard curve.
  E. HOUR-OF-DAY: spike rate + phase-follow PnL by UTC hour.
  F. CONFIRMATION-EXIT backtest: on counter-spike, wait K ticks; reverse only
     if drift confirms; keep the position if the spike was absorbed.

Usage: python analysis/flip_deep_probe.py
"""
import sys
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
MULT = 5.0
LIVE_SPREAD = 0.6261


def vpu_per_01():
    sp = get_specs()[SYMBOL]
    return sp["tick_value"] / sp["tick_size"] * 0.1


def phase_track(ms, px, med, mult=MULT):
    d = np.diff(px)
    big_idx = np.where(np.abs(d) > mult * med)[0]
    big_idx = big_idx[big_idx >= WARMUP]
    dirs = np.sign(d[big_idx]).astype(int)
    # phase id per large move
    phase = np.zeros(len(dirs), dtype=int)
    p = 0
    for k in range(1, len(dirs)):
        if dirs[k] != dirs[k - 1]:
            p += 1
        phase[k] = p
    return ms, px, d, big_idx, dirs, phase


def section_absorption(ms, px, d, big_idx, dirs, med):
    print("\nA. ABSORPTION of counter-spikes (post-spike drift over H ticks)")
    for H in (50, 100, 200, 400):
        res = {("up", "flip"): 0, ("up", "abs"): 0, ("up", "flat"): 0,
               ("dn", "flip"): 0, ("dn", "abs"): 0, ("dn", "flat"): 0}
        ages = defaultdict(list)
        phase_start = 0
        for k in range(1, len(dirs)):
            if dirs[k] != dirs[k - 1]:
                phase_start = k
            if k == phase_start:
                continue                      # same-direction move: no claim
            i = big_idx[k]
            j = min(i + 1 + H, len(px) - 1)
            drift = px[j] - px[i + 1]
            key = "up" if dirs[k] > 0 else "dn"
            if dirs[k] * drift > 0:
                res[(key, "flip")] += 1
            elif dirs[k] * drift < 0:
                res[(key, "abs")] += 1
                ages[key].append(k - phase_start)
            else:
                res[(key, "flat")] += 1
        up_n = res[("up", "flip")] + res[("up", "abs")]
        dn_n = res[("dn", "flip")] + res[("dn", "abs")]
        up_a = res[("up", "abs")] / up_n * 100 if up_n else float("nan")
        dn_a = res[("dn", "abs")] / dn_n * 100 if dn_n else float("nan")
        print(f"  H={H:4d}: up-spikes absorbed {up_a:5.1f}% (n={up_n})   "
              f"down-spikes absorbed {dn_a:5.1f}% (n={dn_n})")
    if ages.get("up") or ages.get("dn"):
        for key in ("up", "dn"):
            a = ages.get(key, [])
            if a:
                print(f"  absorbed {key}-spikes: mean phase age at absorption "
                      f"{np.mean(a):.1f} (n={len(a)})")


def section_microdrift(ms, px, d, big_idx, dirs, med):
    print("\nB. MICRO-DRIFT after spikes (units; + = continues spike direction)")
    vpu = vpu_per_01()
    print(f"  {'ticks':>6s} {'all spikes':>12s} {'up-spike':>12s} {'dn-spike':>12s}  "
          f"{'$ per 0.1lot':>13s}")
    for m in (5, 10, 20, 50, 100):
        vals = {0: [], 1: [], -1: []}
        for k, i in enumerate(big_idx):
            j = min(i + 1 + m, len(px) - 1)
            vals[dirs[k]].append(px[j] - px[i + 1])
        a = np.concatenate([vals[1], vals[-1]])
        line = (f"  {m:6d} {np.mean(a):+12.5f} {np.mean(vals[1]):+12.5f} "
                f"{np.mean(vals[-1]):+12.5f}  {np.mean(a) * vpu:+13.4f}")
        print(line)


def section_size_clustering(px, d, big_idx, med):
    print("\nC. SPIKE-SIZE CLUSTERING")
    sizes = np.abs(d[big_idx]) / med
    s0, s1 = sizes[:-1], sizes[1:]
    q = np.quantile(s0, [0.25, 0.5, 0.75])
    print(f"  n pairs={len(s0)}, lag-1 Spearman = "
          f"{np.corrcoef(np.argsort(np.argsort(s0)), np.argsort(np.argsort(s1)))[0, 1]:+.3f}")
    for name, m in [("q1 small", s0 <= q[0]), ("q2", (s0 > q[0]) & (s0 <= q[1])),
                    ("q3", (s0 > q[1]) & (s0 <= q[2])), ("q4 large", s0 > q[2])]:
        print(f"  after {name:9s}: mean next size {s1[m].mean():5.1f}x median  "
              f"(n={int(m.sum())})")
    # direction: P(next same dir)
    dirsseq = np.sign(np.diff(px)[big_idx])
    same = (dirsseq[1:] == dirsseq[:-1]).mean() * 100
    print(f"  P(next large move same direction) = {same:.1f}% (grind-follow)")


def section_time_hazard(ms, px, big_idx):
    print("\nD. TIME HAZARD between spikes")
    gaps = np.diff(big_idx)
    cv = gaps.std() / gaps.mean()
    g0, g1 = gaps[:-1], gaps[1:]
    rho = np.corrcoef(np.argsort(np.argsort(g0)), np.argsort(np.argsort(g1)))[0, 1]
    print(f"  interval mean {gaps.mean():.0f} ticks, CV={cv:.2f} (Poisson=1.00), "
          f"lag-1 rank corr {rho:+.3f}")
    # hazard: P(spike in next 200 | none in last Y)
    print("  P(spike within next 200 ticks | quiet for Y ticks):")
    for Y in (0, 500, 1000, 2000, 4000):
        quiet = g0 > Y
        h = (g1[quiet] <= 200).mean() * 100 if quiet.sum() > 10 else float("nan")
        print(f"    Y={Y:5d}: {h:5.1f}%  (n={int(quiet.sum())})")


def section_hour(ms, px, big_idx, dirs, med):
    print("\nE. HOUR-OF-DAY (UTC): spike rate + phase-follow PnL contribution")
    vpu = vpu_per_01()
    hours = defaultdict(lambda: [0, 0.0])
    # attribute each phase episode's pnl to its open hour
    pos, entry, entry_h = 0, 0.0, 0
    for k, i in enumerate(big_idx):
        fill = px[i + 1]
        if pos == 0:
            pos, entry = dirs[k], fill
            entry_h = datetime.fromtimestamp(ms[i] / 1000, tz=timezone.utc).hour
        elif pos != dirs[k]:
            pnl = (pos * (fill - entry) - LIVE_SPREAD) * vpu
            hours[entry_h][0] += 1
            hours[entry_h][1] += pnl
            pos, entry = dirs[k], fill
            entry_h = datetime.fromtimestamp(ms[i] / 1000, tz=timezone.utc).hour
    print(f"  {'UTC h':>5s} {'episodes':>8s} {'net$':>8s} {'$/ep':>7s}   "
          f"(spike rate share by tick count)")
    for h in range(24):
        n, p = hours[h][0], hours[h][1]
        print(f"  {h:5d} {n:8d} {p:+8.2f} {(p / n if n else 0):+7.3f}")


def confirmation_backtest(ms, px, med, K=150):
    """Counter-spike -> wait K ticks; reverse only if drift confirms."""
    d = np.diff(px)
    big = set(np.where(np.abs(d) > MULT * med)[0])
    bad = bad_tick_mask(ms)                    # gap-masking
    vpu = vpu_per_01()
    pos, entry = 0, 0.0
    cur_dir, cnt = 0, 0
    confirm = None            # (dir_of_counter_spike, confirm_until_idx, fill_at_spike)
    trades = []
    for j in range(WARMUP, len(px)):
        if bad[j]:                       # hole: drop position + all state
            pos = 0
            cur_dir, cnt = 0, 0
            confirm = None
            continue
        spike = (j - 1) in big
        if spike:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                confirm = (s, j + K, px[j])       # start confirmation window
                continue
        if confirm:
            cdir, until, _ = confirm
            if spike and cdir * int(np.sign(d[j - 1])) > 0:
                # second same-direction spike before confirmation ended:
                # treat as flip confirmed immediately
                close_fill = px[j]
                trades.append((np.sign(pos) * (close_fill - entry)
                               - LIVE_SPREAD) * vpu)
                pos, entry = cdir, close_fill
                confirm = None
                cur_dir, cnt = cdir, 2
                continue
            if j >= until:
                drift = px[j] - px[until - K + 1]
                if cdir * drift > 0:              # confirmed flip: close+reverse
                    trades.append((np.sign(pos) * (px[j] - entry)
                                   - LIVE_SPREAD) * vpu)
                    pos, entry = cdir, px[j]
                elif cdir * drift < 0:            # absorbed: keep position
                    pass
                else:
                    trades.append((np.sign(pos) * (px[j] - entry)
                                   - LIVE_SPREAD) * vpu)
                    pos = 0
                confirm = None
            continue
        if pos == 0 and cnt >= 2 and spike:
            pos, entry = cur_dir, px[j]
    if pos != 0:
        trades.append((np.sign(pos) * (px[-1] - entry) - LIVE_SPREAD) * vpu)
    return np.array(trades)


def immediate_baseline(ms, px, med):
    from flip_concepts import simulate
    tr, _ = simulate(ms, px, med)
    return np.array([t[0] for t in tr])


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    ms, px, d, big_idx, dirs, phase = phase_track(ms, px, med)
    dur_wk = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7

    section_absorption(ms, px, d, big_idx, dirs, med)
    section_microdrift(ms, px, d, big_idx, dirs, med)
    section_size_clustering(px, d, big_idx, med)
    section_time_hazard(ms, px, big_idx)
    section_hour(ms, px, big_idx, dirs, med)

    print("\nF. CONFIRMATION-EXIT vs IMMEDIATE REVERSAL")
    base = immediate_baseline(ms, px, med)
    print(f"  immediate: n={len(base)} net {base.sum() / dur_wk:+.2f}/wk "
          f"WR {(base > 0).mean() * 100:.1f}%")
    for K in (50, 150, 300):
        tr = confirmation_backtest(ms, px, med, K=K)
        if len(tr):
            print(f"  K={K:3d}: n={len(tr)} net {tr.sum() / dur_wk:+.2f}/wk "
                  f"WR {(tr > 0).mean() * 100:.1f}% worst {tr.min():+.2f}")


if __name__ == "__main__":
    main()
