#!/usr/bin/env python3
"""Exit-family sweep: five untested rule families motivated by the same
absorption asymmetry that made retrace-hold the first positive exit variant
(Appendix M). All use identical entries (phase-follower, min_age=1), identical
conventions (frozen median, 5x detector, live spread/episode, $0.10/unit,
gap-masked), and identical fill timing (close at px[j], the tick AFTER the
spike delta d[j-1] lands). Engine validated against flip_concepts.

Families (each parameterized, LS and long-only):
  1. STAGED   - counter-spike closes HALF now, HALF on the next tick
                (the counter-spike is often the local extreme; the second
                half exits into the bounce it triggers)
  2. THICK    - "thick skin": ignore the FIRST adverse spike if its size is
                small relative to entry x (x_entry * RATIO); close on any
                bigger counter-move. Not the M-rule (pnl-sign based) - this
                is size-of-attack based, orthogonal logic.
  3. TIMEBND  - pure retrace-hold but with a patience cap: if no favorable
                spike fills the exit within K ticks of the adverse spike,
                take the market (answers "is unlimited patience the risk?")
  4. RATCHET  - best-price trail in x-median UNITS of the entry spike
                (trail = entry_x * R units) - the trail tombstone was fixed
                units; scaling by the entry's energy is untested
  5. ABSORB   - after the exit spike, wait for the NEXT counter-spike (the
                bounce) to re-enter opposite - tests whether the bounce
                after a kill is itself an entry (spread-tax risk, ABS-adjacent
                but keyed on the exit event, not the phase age)

Usage: python analysis/flip_exit_families.py
"""
import sys

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


def run(ms, px, med, side=0, family="counter", param=None, tp_usd=None):
    """Phase-follower with pluggable exit family. Returns nets array."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    entry_x = 1.0        # entry spike size in x-median units
    cur_dir, cnt = 0, 0
    worst = 0.0
    best = 0.0
    pending = False      # retrace-hold state (TIMEBND / shared)
    pend_j = 0           # tick index where the adverse spike was seen
    stage = 0            # STAGED: 1 = half closed, awaiting second half
    half_fill = 0.0
    wait_next = False    # ABSORB: waiting for next counter-spike to re-enter
    nets = []
    close_ms = []
    cur_j = {"j": WARMUP}   # close() needs the current tick index

    def close(fill, frac=1.0):
        nonlocal pos, worst, best, pending, stage
        nets.append((fill - entry) * pos * VPU * frac - SPREAD_USD * frac)
        close_ms.append(ms[cur_j["j"]])
        pos *= (1.0 - frac)
        if pos == 0.0:
            worst, best, pending, stage = 0.0, 0.0, False, 0

    for j in range(WARMUP, len(px)):
        cur_j["j"] = j
        if bad[j]:
            pos, cur_dir, cnt = 0.0, 0, 0
            worst, best, pending, stage, wait_next = 0.0, 0.0, False, 0, False
            continue

        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0

        if is_spike:
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

        # ---------------- exits ----------------
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            best = max(best, (px[j] - entry) * np.sign(pos))
            against = pos < 0

            if tp_usd is not None and \
                    (px[j] - entry) * pos * VPU >= tp_usd:
                close(px[j])          # bank at threshold (tick-close fill)
                continue

            if family == "staged" and is_spike and s != np.sign(pos) and stage == 0:
                close(px[j], frac=0.5)
                stage = 1
                half_fill = px[j]
            elif family == "staged" and stage == 1:
                close(px[j], frac=1.0)   # close remaining half next tick
            elif family == "thick" and is_spike and s != np.sign(pos):
                if abs(d[j - 1]) / med > entry_x * param:
                    close(px[j])
                # else: small attack absorbed, hold
            elif family == "timebnd":
                if is_spike and s != np.sign(pos):
                    pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                    if pnl_now >= 0:
                        close(px[j])
                    elif not pending:
                        pending, pend_j = True, j
                    # pending: keep holding
                elif pending and s == np.sign(pos) and is_spike:
                    close(px[j])                      # retrace arrived
                elif pending and j - pend_j >= param:
                    close(px[j])                      # patience ran out
            elif family == "ratchet":
                drop = (best - (px[j] - entry) * np.sign(pos))
                if drop >= entry_x * param * med:
                    close(px[j])
            elif family == "counter" and is_spike and s != np.sign(pos):
                close(px[j])
            elif family == "absorb" and is_spike and s != np.sign(pos):
                close(px[j])            # absorb holds like incumbent until killed

        # ---------------- entries ----------------
        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            gate = cnt >= MIN_AGE + 1 and is_spike
            if family == "absorb":
                # standard gate OR the bounce entry right after a kill
                if gate and want:
                    pos, entry = STEP_LOTS * cur_dir, px[j]
                    entry_x = abs(d[j - 1]) / med
                    worst = best = 0.0
                elif wait_next and is_spike:
                    pos, entry = STEP_LOTS * s, px[j]
                    entry_x = abs(d[j - 1]) / med
                    worst = best = 0.0
                    wait_next = False
            else:
                if gate and want:
                    entry_x = abs(d[j - 1]) / med   # every family records entry energy
                    pos, entry = STEP_LOTS * cur_dir, px[j]
                    worst = best = 0.0

        # ABSORB bookkeeping: an exit that leaves us flat arms wait_next
        if family == "absorb" and pos == 0 and not wait_next \
                and is_spike and s != 0:
            wait_next = True

        # STAGED bookkeeping: stage must reset if position fully gone
        if family == "staged" and pos == 0 and stage == 1 and not is_spike:
            stage = 0

    if pos != 0:
        close(px[-1])
    return np.array(nets), np.array(close_ms)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = (peak - eq).max() if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:34s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f}\n")

    grid = [
        ("counter", None, "incumbent (reference)"),
        ("staged", 0.5, "STAGED half-now half-next"),
        ("thick", 0.5, "THICK ignore attacks < 0.5x entry"),
        ("thick", 0.75, "THICK ignore attacks < 0.75x entry"),
        ("timebnd", 500, "TIMEBND patience 500 ticks"),
        ("timebnd", 1000, "TIMEBND patience 1000 ticks"),
        ("timebnd", 2000, "TIMEBND patience 2000 ticks"),
        ("timebnd", 3000, "TIMEBND patience 3000 ticks"),
        ("timebnd", 5000, "TIMEBND patience 5000 ticks"),
        ("ratchet", 0.5, "RATCHET trail = 0.5x entry energy"),
        ("ratchet", 1.0, "RATCHET trail = 1.0x entry energy"),
        ("ratchet", 2.0, "RATCHET trail = 2.0x entry energy"),
        ("absorb", None, "ABSORB re-enter on exit bounce"),
    ]
    for side, sname in ((0, "LS"), (1, "L")):
        print(f"=== {sname} ===")
        for fam, p, label in grid:
            nets, _ = run(ms, px, med, side=side, family=fam, param=p)
            stats(nets, days, label)
        print()

    # time-split robustness: does the patience elevation survive in BOTH
    # halves of the archive? (guard against one lucky week)
    if "--split" in sys.argv:
        print("=== time-split (trades partitioned by close time) ===")
        for side, sname in ((0, "LS"), (1, "L")):
            for fam, p, label in (("counter", None, "incumbent"),
                                  ("timebnd", 1000, "TIMEBND 1000"),
                                  ("timebnd", 2000, "TIMEBND 2000"),
                                  ("timebnd", 5000, "TIMEBND 5000")):
                nets, tms = run(ms, px, med, side=side, family=fam, param=p)
                mid = np.median(tms)
                h1, h2 = nets[tms <= mid], nets[tms > mid]
                d1 = (mid - ms[WARMUP]) / 86_400_000
                d2 = (ms[-1] - mid) / 86_400_000
                print(f"  {sname:3s} {label:14s} "
                      f"H1(${h1.sum()/max(d1,1e-9)*7:+6.2f}/wk)  "
                      f"H2(${h2.sum()/max(d2,1e-9)*7:+6.2f}/wk)")


if __name__ == "__main__":
    main()
