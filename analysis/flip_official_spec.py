#!/usr/bin/env python3
"""Official-spec reconciliation + grind-phase detector (Sep 16, P16/P17).

Deriv Academy (Sep 2026) officially describes Flip mechanics. Reconcile
against the measured model, then test the one new tradeable idea it
contains: "You can see which [flip] happened by whether the GRIND
CHANGES DIRECTION" - i.e. the official phase oracle is DRIFT-based,
not spike-vote-based. Nobody has run that detector family.

PART A - RNG stationarity: the exploit probes found minor 5x-spike
density swings 17.7x by UTC hour (22Z 0.90/1k vs 00Z 15.97/1k) while
the article insists the RNG is uniform. If the swing is real market
structure it should replicate in both halves of the archive; if it is
small-sample luck it should not. Week1 vs week2 halves, per-hour
density, rank correlation.

PART B - P16 grind detector: phase = sign(sum of last W good-tick
deltas) with hysteresis theta (flip registers only when the new sign's
|sum| >= theta). Enter at next good tick on a phase flip to the wanted
side; exit on adverse major (fleet counter) or exit when the grind
flips against the position. TB5000 patience ported as-is. Compare vs
fleet spike-vote engine at fence5 (live) and fence8 (menu B), LS.

Usage: python analysis/flip_official_spec.py
"""
import sys
from collections import deque
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, STEP_LOTS
from flip_entry_delay import run_fleet
from flip_gapmask import bad_tick_mask
from flip_next_gen import day_block
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT
MULT = 5.0


def part_a(ms, px):
    """Minor-density by hour, first half vs second half of the archive."""
    d = np.diff(px)
    ok = ~bad_tick_mask(ms)[1:len(px)]
    big = (np.abs(d) > MULT * float(np.median(np.abs(d[:WARMUP])))) & ok
    idx = np.where(big)[0]
    idx = idx[idx >= WARMUP]
    signs = np.sign(d[idx])
    mags = np.abs(d[idx])
    minors = mags <= 8.0 * float(np.median(np.abs(d[:WARMUP])))
    hrs = np.array([datetime.fromtimestamp(ms[i + 1] / 1000,
                                           tz=timezone.utc).hour
                    for i in idx])
    mid = (ms[WARMUP] + ms[-1]) // 2
    half = np.array([ms[i + 1] < mid for i in idx])
    # per-band tick denominators: good ticks in each hour x half cell
    good_idx = np.arange(WARMUP, len(px) - 1)
    good_ok = ok[good_idx]           # ok is indexed to d-space (i+1)
    good_ms = ms[good_idx + 1]
    good_half = good_ms < mid
    good_hrs = np.array([datetime.fromtimestamp(t / 1000,
                                                 tz=timezone.utc).hour
                         for t in good_ms])
    print("=== PART A: minor 5x..8x density by UTC hour, half1 vs half2 ===")
    print(f"{'hour':>5s} {'h1/1k':>7s} {'h2/1k':>7s}")
    rows = []
    tot1 = tot2 = 0
    for h in range(24):
        m = hrs == h
        n1 = int((m & half & minors).sum())
        n2 = int((m & ~half & minors).sum())
        t1 = int(((good_hrs == h) & good_half & good_ok).sum())
        t2 = int(((good_hrs == h) & ~good_half & good_ok).sum())
        r1 = n1 / t1 * 1000 if t1 else 0.0
        r2 = n2 / t2 * 1000 if t2 else 0.0
        tot1 += n1
        tot2 += n2
        rows.append((h, r1, r2))
        print(f"{h:02d}Z  {r1:7.2f} {r2:7.2f}")
    print(f"totals: minors half1={tot1}  half2={tot2}")
    # rank correlation between halves (Spearman fallback: Pearson on ranks)
    r1 = np.array([r[1] for r in rows])
    r2 = np.array([r[2] for r in rows])
    k1 = np.argsort(np.argsort(r1)).astype(float)
    k2 = np.argsort(np.argsort(r2)).astype(float)
    rho = np.corrcoef(k1, k2)[0, 1]
    print(f"rank correlation h1 vs h2: {rho:+.2f}  "
          f"({'>0.5 = hour structure replicates' if rho > 0.5 else 'weak = suspect small-sample'})")
    # the two extreme cells explicitly
    for h in (22, 0):
        h_, a, b = rows[h]
        print(f"  {h:02d}Z: half1 {a:.2f}/1k  half2 {b:.2f}/1k")
    print()


def run_grind(ms, px, med, side=0, mode="counter", k=None, mult=5.0,
              W=1000, theta=10.0):
    """Drift-window phase detector engine (the article's oracle).

    phase flips to sign(gsum) when sign changes AND |gsum| >= theta;
    entry at next good tick on a flip event to the wanted side;
    exit: adverse major (counter) / patience (timebnd), plus exit on
    grind-flip against the position.
    """
    thr = mult * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    worst = 0.0
    pending = False
    pend_j = 0
    nets = []
    cms = []

    win = deque(maxlen=W)
    gsum = 0.0
    phase = 0
    entered_flip = 0
    flip_id = 0

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, worst, pending = 0.0, 0.0, False
            win.clear()
            gsum = 0.0
            phase = 0
            continue
        is_spike = bool(big[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0

        # grind update (this tick's delta)
        gsum += d[j - 1]
        if len(win) == W:
            gsum -= win[0]
        win.append(d[j - 1])
        new_ph = 0
        if gsum > theta:
            new_ph = 1
        elif gsum < -theta:
            new_ph = -1
        if new_ph != 0 and new_ph != phase:
            phase = new_ph
            flip_id += 1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
            if is_spike and s != 0 and s != np.sign(pos):
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
            # grind-flip against the position = detector says regime turned
            if phase != 0 and phase != np.sign(pos) and flip_id != entered_flip:
                close(px[j], j)
                continue

        if pos == 0 and phase != 0 and flip_id != entered_flip:
            want = (side == 0) or (phase == side)
            if want:
                pos, entry = STEP_LOTS * phase, px[j]
                entered_flip = flip_id
                worst, pending = 0.0, False
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms)


def part_b(ms, px, med, days):
    print("=== PART B: P16 grind-direction detector (LS) vs fleet ===")
    print("fleet spike-vote reference:")
    for mult in (5.0, 8.0):
        for mode, kw in (("counter", {}), ("timebnd", dict(k=5000))):
            nf, cf = run_fleet(ms, px, med, side=0, mode=mode,
                               mult=mult, **kw)
            print(f"  fleet fence{mult:.0f} {mode:8s} trips={len(nf):4d}  "
                  f"net=${nf.sum()/days*7:+7.2f}/wk")
    print()
    print("grind detector (LS): W x theta x fence, counter exit")
    results = []
    for mult in (5.0, 8.0):
        for W in (500, 2000):
            for theta in (0.0, 10.0):
                nets, cms = run_grind(ms, px, med, side=0, mode="counter",
                                      mult=mult, W=W, theta=theta)
                nw = nets.sum() / days * 7
                results.append((nw, mult, W, theta, len(nets), nets, cms))
                print(f"  fence{mult:.0f} W={W:4d} theta={theta:5.1f}  "
                      f"trips={len(nets):4d}  net=${nw:+7.2f}/wk")
    results.sort(reverse=True, key=lambda r: r[0])
    print()
    print("top-2 grind cells with TB5000 patience + survival:")
    for nw, mult, W, theta, ntrip, nets, cms in results[:2]:
        nt, ct = run_grind(ms, px, med, side=0, mode="timebnd", k=5000,
                           mult=mult, W=W, theta=theta)
        print(f"  fence{mult:.0f} W={W} theta={theta} counter: "
              f"${nw:+.2f}/wk | TB5000: ${nt.sum()/days*7:+.2f}/wk "
              f"({len(nt)}t)")
        trades = list(zip(nt.tolist(), ct.tolist()))
        row = []
        for seed in (11, 12, 13):
            fl, ru = day_block(trades, seed=seed)
            row.append(f"{fl:5.1f}/{ru:4.1f}")
        print(f"    TB5000 survival flip/ruin: " + "  ".join(row))


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"flip1000: {days:.1f} days, med={med:.5f}u, "
          f"spec clock 1 large move / 1000 ticks (official)\n")
    part_a(ms, px)
    part_b(ms, px, med, days)


if __name__ == "__main__":
    main()
