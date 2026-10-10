#!/usr/bin/env python3
"""Entry-delay confirmation study (Sep 16, P15).

USER HYPOTHESIS: "when we enter in the direction of a spike immediately
after the spike, it could end up going the opposite way - what if it
waits for about 3 bars to be sure"

Mechanism: on a qualifying spike (phase age >= MIN_AGE+1, i.e. the
market voted twice - unchanged), do NOT enter immediately. Start a
pending entry in that direction; enter at the then-current price after
DELAY ticks. Cancel the pending entry if an opposite-sign spike arrives
first (the phase flipped while we waited). Exit logic unchanged.

Note: "3 bars" on flip1000 ~= 180 ticks (stream runs ~1 tick/s, so a
1-minute bar ~= 60 ticks). Sweep covers that band and beyond.

SHARP PRIOR (P13, Appendix AA): after an UP major, drift is +62.5u/1k
ticks decaying to zero by N=2000; after a DOWN major, negative drift
persists for thousands of ticks. So delay should COST longs (it gives
up the freshest part of the drift) and cost shorts less. If the user's
reversal-avoidance beats the lost drift, delay wins; if not, tombstone.

Engine discipline: clone of flip_next_gen.run with a pending-entry
layer. delay=0 must reproduce fleet run() bit-exact on all validation
cells before any sweep number is trusted.

Usage: python analysis/flip_entry_delay.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, STEP_LOTS
from flip_next_gen import day_block, ladder_fn
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT


def run_fleet(ms, px, med, side=0, mode="counter", k=None, mult=5.0):
    """Verbatim flip_next_gen.run (mult parameterized for fence tests)."""
    thr = mult * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    pend_j = 0
    nets = []
    cms = []

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            if is_spike and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if mode == "counter" or pnl_now >= 0:
                    close(px[j], j)
                    continue
                if not pending:
                    pending, pend_j = True, j
                    continue
            elif pending and is_spike and s == np.sign(pos):
                close(px[j], j)
                continue
            elif pending:
                if mode == "timebnd" and j - pend_j >= k:
                    close(px[j], j)
                    continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and is_spike:
                pos, entry = STEP_LOTS * cur_dir, px[j]
                worst, pending = 0.0, False
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms)


def run_delay(ms, px, med, side=0, mode="counter", k=None, mult=5.0,
              delay=0, census=None, r0=None, major_x=8.0):
    """Fleet clone + pending-entry delay layer.

    delay=0 -> identical semantics to run_fleet (validated below).
    delay>0: qualifying spike starts a pending entry (dir, start tick).
      - enter at px[j] once j - start >= delay
      - cancel if an opposite-sign spike arrives first
      - bad-tick reset clears pending
    r0>0: quiet-entry gate (Appendix Y) - no NEW pending entry while
      >= r0 minor spikes (5x..major_x) sit in the trailing 2000 ticks.
      Applied at pending-start only; a pending entry already started
      still fills (same convention as the fleet quiet clone).
    """
    thr = mult * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    pend_j = 0
    nets = []
    cms = []
    ep_dir, ep_j = 0, -1
    n_cancel = n_filled = 0
    minor_ts = []   # indices of recent minor spikes (bounded)

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    def minors_recent(j):
        # count minors in trailing 2000 ticks ending at j
        return sum(1 for i in reversed(minor_ts) if j - i <= 2000)

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            ep_dir, ep_j = 0, -1
            minor_ts = []
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if minor_ts is not None and abs(d[j - 1]) <= major_x * med:
                minor_ts.append(j - 1)
                if len(minor_ts) > 4096:
                    del minor_ts[:-2048]
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if ep_dir != 0 and s != 0 and s != ep_dir:
                n_cancel += 1
                ep_dir, ep_j = 0, -1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            if is_spike and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if mode == "counter" or pnl_now >= 0:
                    close(px[j], j)
                    continue
                if not pending:
                    pending, pend_j = True, j
                    continue
            elif pending and is_spike and s == np.sign(pos):
                close(px[j], j)
                continue
            elif pending:
                if mode == "timebnd" and j - pend_j >= k:
                    close(px[j], j)
                    continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    n_filled += 1
                    ep_dir, ep_j = 0, -1
                    worst, pending = 0.0, False
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= MIN_AGE + 1 and is_spike:
                    if r0 is not None and minors_recent(j) >= r0:
                        pass          # quiet gate: flood blocks new pending
                    elif delay == 0:
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        worst, pending = 0.0, False
                    else:
                        ep_dir, ep_j = cur_dir, j
    if pos != 0:
        close(px[-1], len(px) - 1)
    if census is not None:
        census["cancel"] = n_cancel
        census["fill"] = n_filled
    return np.array(nets), np.array(cms)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:26s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f} "
          f"(~1 tick/s -> 3 bars ~= 180 ticks)\n")

    print("=== 0. VALIDATION: run_delay(delay=0) vs fleet run() ===")
    ok = True
    for side, sname in ((0, "LS"), (1, "L")):
        for mode, kw in (("counter", {}), ("timebnd", dict(k=5000))):
            for mult in (5.0, 8.0):
                nf, cf = run_fleet(ms, px, med, side=side, mode=mode,
                                   mult=mult, **kw)
                nd, cd = run_delay(ms, px, med, side=side, mode=mode,
                                   mult=mult, delay=0, **kw)
                same = len(nf) == len(nd) and np.array_equal(nf, nd)
                ok &= same
                print(f"  {sname:2s} {mode:8s} fence{mult:.0f}  "
                      f"fleet={len(nf):4d}/${nf.sum():+8.2f}  "
                      f"clone={len(nd):4d}/${nd.sum():+8.2f}  "
                      f"{'OK' if same else 'MISMATCH'}")
    if not ok:
        print("VALIDATION FAILED - aborting sweep")
        return
    print()

    print("=== 1. DELAY SWEEP (ticks; 180 ~ user's 3 bars) ===")
    delays = (0, 60, 180, 600)
    for mult, fence in ((5.0, "fence5 (live)"), (8.0, "fence8 (candidate)")):
        for mode, kw, ename in (("counter", {}, "counter"),
                                ("timebnd", dict(k=5000), "TB5000")):
            for side, sname in ((1, "L"), (0, "LS")):
                base = None
                for dl in delays:
                    nets, _ = run_delay(ms, px, med, side=side, mode=mode,
                                        mult=mult, delay=dl, **kw)
                    net_wk = nets.sum() / days * 7
                    if dl == 0:
                        base = net_wk
                        delta = 0.0
                    else:
                        delta = net_wk - base
                    wins = nets[nets > 0]
                    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
                    print(f"  {fence:18s} {ename:7s} {sname:2s} "
                          f"delay={dl:4d}  trips={len(nets):4d}  "
                          f"WR={wr:4.1f}%  net=${net_wk:+7.2f}/wk  "
                          f"d={delta:+6.2f}")
                print()

    print("=== 2. S-SPLIT (P13 prediction: delay hurts L, spares S) ===")
    for mult in (5.0, 8.0):
        for dl in (0, 180, 600):
            nets, _ = run_delay(ms, px, med, side=-1, mode="counter",
                                mult=mult, delay=dl)
            print(f"  fence{mult:.0f} S counter delay={dl:4d}  "
                  f"trips={len(nets):4d}  net=${nets.sum()/days*7:+7.2f}/wk")
    print()

    print("=== 3. CENSUS: how often does the wait actually save you? ===")
    for mult in (5.0, 8.0):
        for dl in (60, 180, 600):
            cen = {}
            run_delay(ms, px, med, side=1, mode="counter", mult=mult,
                      delay=dl, census=cen)
            print(f"  fence{mult:.0f} L delay={dl:4d}  "
                  f"canceled-by-flip={cen['cancel']:3d}  "
                  f"filled={cen['fill']:3d}")
    print()

    print("=== 4. SURVIVAL for any promising cell (L, fence8, TB5000) ===")
    for dl in (0, 180):
        nets, cms = run_delay(ms, px, med, side=1, mode="timebnd", k=5000,
                              mult=8.0, delay=dl)
        trades = list(zip(nets.tolist(), cms.tolist()))
        fl, ru = day_block(trades)
        print(f"  delay={dl:4d}  fixed 0.1: flip={fl:5.1f}%  ruin={ru:4.1f}%")
        fl2, ru2 = day_block(trades, lots_mult=ladder_fn)
        print(f"  delay={dl:4d}  ladder : flip={fl2:5.1f}%  ruin={ru2:4.1f}%")


if __name__ == "__main__":
    main()
