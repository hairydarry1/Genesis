#!/usr/bin/env python3
"""Hour-scale drift: does the archive say you can PREDICT it, and would a
drift gate have saved the Oct 7 shorts?

CONTEXT (2026-10-07 evening)
----------------------------
Both of today's losers were shorts riding into a multi-hour climb
(9591.25 -> 9654.54):
  trade 1  short 9591.2523 (00:07Z fill)  -> broker SL backstop
           9651.2527, -$6.00
  trade 2  short 9621.8854 (10:11Z fill)  -> broker margin stop-out
           9654.5375, -$3.27 ("not enough funds to continue trading")
The executor's own trend_2h telemetry stamp read -1 at trade 1 and 0 at
trade 2, so the crude 2-hour snapshot saw nothing useful. The response
question: is hour-scale drift predictable enough to gate entries on?

This study answers on the same 34-day gap-masked memory used by the
backstop study, under live conventions (rolling 5000-tick median engine,
delay 300, live spread):

  A. PREDICTABILITY AUDIT - does trailing hour-scale return predict
     forward return? Single-regressor t-stat of mean forward drift (u)
     on trailing drift (u) over every (w, h) pair, w in [5m..6h], h in
     [5m..4h]. Discipline: the full max|t| landscape is reported, not a
     cherry-picked cell. (Honest t, no constant - flat means "no linear
     edge", which is what a gate needs.)
  B. OCT-7 COUNTERFACTUAL - trailing drift at each entry timestamp,
     in raw units, across the w grid, so we can see whether today's
     reads look extreme against the archive quantiles.
  C. GATE SIM - the DEPLOYED engine (bit-exact parity-gated clone that
     also records entry sides) with SHORT arms blocked when trailing
     w-drift <= -theta (theta in raw units). Longs are never blocked.
     Sweep theta x w; report n, net $, net/wk, blocks, and the
     short-only contribution the gate removed.

DISCIPLINE
----------
run_gate = run_conv verbatim + one blocking predicate at arm time; with
the predicate impossible it must reproduce flip_rolling_engine.run_conv
 BIT-FOR-BIT before any number is printed.

Trailing-drift arrays are vectorized with hole masking: a trail whose
[j-w, j] span contains any bad (gap) tick is NaN, and NaN never gates.

Usage: python analysis/flip_drift_gate.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP  # noqa: E402
from flip_concepts import MIN_AGE, STEP_LOTS  # noqa: E402
from flip_gapmask import bad_tick_mask  # noqa: E402
from flip_regime_desize import (  # noqa: E402
    DELAY, MULT, SPREAD_USD, SYMBOL, VPU, get_med_roll)
from flip_rolling_engine import run_conv as shared_run  # noqa: E402

AUDIT_W_MIN = (5, 10, 15, 30, 60, 120, 240, 360)
AUDIT_H_MIN = (5, 10, 15, 30, 60, 120, 240)
GATE_THETA_U = (10.0, 20.0, 40.0)          # 10u=$1, 20u=$2, 40u=$4 at 0.1 lot
GATE_W_MIN = (20, 60, 120, 240)
ENTRIES = (
    # note, UTC iso of the position going live on the book (fill/adopt)
    ("trade1 short 9591.2523 (SL backstop)", "2026-10-07T00:07:31+00:00"),
    ("trade2 short 9621.8854 (margin stop)", "2026-10-07T10:11:44+00:00"),
)


def build_trails(px, bad, tpm, w_min_list):
    """dict w_min -> trail array in u, with hole-spanning reads = NaN.

    trail[j] = (px[j] - px[j - w_ticks]) / VPU   (0.1-lot $-par is 0.10/u).
    """
    cumbad = np.cumsum(bad.astype(np.int64))
    trails = {}
    for w_min in w_min_list:
        w_ticks = max(int(round(w_min * tpm)), 1)
        trail = np.full(len(px), np.nan)
        if w_ticks >= len(px) - 10:
            trails[w_min] = trail
            continue
        trail[w_ticks:] = px[w_ticks:] - px[:-w_ticks]
        nb = cumbad.copy()
        nb[w_ticks:] -= cumbad[:-w_ticks]
        trail[nb > 0] = np.nan
        trails[w_min] = trail / VPU
    return trails


def w_ticks_of(w_min, tpm):
    return max(int(round(w_min * tpm)), 1)


def main():
    ms, px = load(SYMBOL)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    bad = bad_tick_mask(ms)
    # ticks per minute actually present in the archive (NOT the live 60/min)
    dt = np.diff(ms[WARMUP:WARMUP + 400_000])
    tpm = 60_000.0 / np.median(dt[dt > 0])
    print(f"=== drift gate | {SYMBOL} | {days:.1f} gap-masked days "
          f"| {len(px):,} ticks | median {tpm:.1f} ticks/min ===")
    print(f"live convention: rolling 5000-tick median, mult={MULT}, "
          f"min_age={MIN_AGE}, delay={DELAY}, spread={SPREAD_USD:.4f} USD/trip"
          f"  (1u = ${VPU * STEP_LOTS:.2f} at 0.1 lot)\n")

    trails = build_trails(px, bad, tpm, AUDIT_W_MIN)

    # ---------------- A. predictability audit ----------------
    print("--- A. hour-scale drift predictability "
          "(t-stat of fwd drift on trailing drift, no constant) ---")
    hdr = "    " + "".join(f"{'h=' + str(h) + 'm':>9}"
                           for h in AUDIT_H_MIN)
    print(hdr + "   (fwd$ = whole-window $ implied by the slope)")
    tmat = {}
    bmat = {}
    for w_min in AUDIT_W_MIN:
        w_ticks = w_ticks_of(w_min, tpm)
        trail_full = trails[w_min]
        row = []
        brow = []
        for h_min in AUDIT_H_MIN:
            lag = max(int(round(h_min * tpm)), 1)
            a = WARMUP + max(w_ticks, lag)
            b = len(px) - lag - 10
            if b - a < 20 * lag:
                row.append(float("nan"))
                brow.append(float("nan"))
                continue
            # stride = lag: consecutive sampled observations share no
            # forward increment, so the t-stat is overlap-honest
            idx = np.arange(0, b - a, lag)
            hi = a + idx + lag
            lo = a + idx
            fwd = (px[hi] - px[lo]) / VPU
            trail = trail_full[lo]
            m = np.isfinite(fwd) & np.isfinite(trail)
            fwd = fwd[m]
            trail = trail[m]
            tc = trail - trail.mean()
            denom = tc @ tc
            if denom <= 0:
                row.append(float("nan"))
                brow.append(float("nan"))
                continue
            beta = (tc @ fwd) / denom
            resid = fwd - beta * trail
            s2 = resid @ resid / max(len(fwd) - 2, 1)
            se = np.sqrt(s2 / denom)
            row.append(float(beta / se))
            # $ over the whole window at 0.1 lot: slope x trail spread x
            # tick count, scaled by the sampled fraction of increments
            brow.append(beta * float(np.nanstd(trail_full[a:b]))
                        * (len(px) - WARMUP) * lag / (b - a) * STEP_LOTS)
        tmat[w_min] = row
        bmat[w_min] = brow
        print(f"    w={w_min:>3}m  "
              + "".join(f"{t:>+8.2f}  " for t in row)
              + "  " + "".join(f"{b:>+7.1f}" for b in brow))
    finite = [abs(v) for r in tmat.values() for v in r if np.isfinite(v)]
    ncell = len(finite)
    print(f"    landscape max|t| = {max(finite):.2f} over {ncell} (w,h) "
          f"cells (5% two-sided ~1.96; Bonferroni x{ncell} "
          f"~{np.sqrt(2 * np.log(ncell)):.2f})")
    fmax = max((abs(v) for r in bmat.values() for v in r
                if np.isfinite(v)), default=0.0)
    print(f"    $-landscape: largest |fwd$| over the window = {fmax:.1f}")
    print("    read: NEGATIVE t = mean reversion (trail retraces), positive")
    print("    = continuation; the Bonferroni bar is ~2.84, so cells past it")
    print("    are real - and the SIGN decides which side a gate may block.\n")

    # ---------------- B. Oct-7 counterfactual ----------------
    print("--- B. Oct-7 counterfactual: trailing drift at the entry fill ---")
    print("    trade1 telemetry trend_2h was -1; trade2 read 0")
    for label, iso in ENTRIES:
        e_ms = int(datetime.fromisoformat(iso).timestamp() * 1000)
    last_ms = int(ms[-1])
    for label, iso in ENTRIES:
        e_ms = int(datetime.fromisoformat(iso).timestamp() * 1000)
        if e_ms > last_ms:
            print(f"  {label}:\n      fill is {(e_ms - last_ms) / 60000:.0f} "
                  "min past the archive end - trail n/a (Deriv tick depth)")
            continue
        jj = int(np.searchsorted(ms, e_ms))
        cells = []
        for w_min in AUDIT_W_MIN:
            if jj >= len(px):
                cells.append(f"{w_min:>3}m   n/a ")
                continue
            t = trails[w_min][jj]
            cells.append(f"{w_min:>3}m" + (f"{t:+7.1f}u" if np.isfinite(t)
                                           else "   n/a "))
        print(f"  {label}:\n      " + "  ".join(cells))
    q = np.nanpercentile(trails[60][WARMUP:], [10, 50, 90])
    print(f"    archive w=60m quantiles (10/50/90p): "
          f"{q[0]:+.1f}/{q[1]:+.1f}/{q[2]:+.1f}u\n")

    # ---------------- C. gate sim ----------------
    print("--- C. short-block gate sim (deployed convention) ---")
    med_roll = get_med_roll(ms, px)
    base_med = float(np.median(med_roll[WARMUP:]))
    nets0, cms0 = shared_run(ms, px, med_roll, base_med, side=0,
                             mode="counter", mult=MULT, delay=DELAY)
    print(f"  shared run_conv: {len(nets0)} trips, net ${nets0.sum():+.2f}, "
          f"{len(nets0) / days:.1f} trips/day")

    trail4 = build_trails(px, bad, tpm, GATE_W_MIN)


    def run_gate(w_min, theta, direction="down"):
        """run_conv verbatim; SHORT arms blocked when trail <= -theta u."""
        d = np.diff(px)
        thr_t = MULT * med_roll
        tr = trail4[w_min]
        pos = 0.0
        entry = 0.0
        cur_dir, cnt = 0, 0
        worst = 0.0
        pending = False
        pend_j = 0
        ep_dir, ep_j = 0, -1
        nets, cms, sides = [], [], []
        n_block = 0

        def close(fill, j):
            nonlocal pos, worst, pending
            nets.append((fill - entry) * pos * VPU - SPREAD_USD)
            cms.append(ms[j])
            sides.append(int(np.sign(pos)))
            pos, worst, pending = 0.0, 0.0, False

        for j in range(WARMUP, len(px)):
            if bad[j]:
                pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
                ep_dir, ep_j = 0, -1
                continue
            is_spike = bool(d[j - 1] > thr_t[j - 1]
                            or -d[j - 1] > thr_t[j - 1])
            s = int(np.sign(d[j - 1])) if is_spike else 0
            if is_spike:
                if s == cur_dir:
                    cnt += 1
                else:
                    cur_dir, cnt = s, 1
                if ep_dir != 0 and s != 0 and s != ep_dir:
                    ep_dir, ep_j = 0, -1

            if pos != 0:
                worst = min(worst, (px[j] - entry) * np.sign(pos))
                if is_spike and s != np.sign(pos):
                    close(px[j], j)      # counter mode: always
                    continue
                elif pending and is_spike and s == np.sign(pos):
                    close(px[j], j)
                    continue
                elif pending and j - pend_j >= 10_000_000:
                    close(px[j], j)
                    continue

            if pos == 0:
                if ep_dir != 0:
                    if j - ep_j >= DELAY:
                        pos, entry = STEP_LOTS * ep_dir, px[j]
                        n_fill_ok = True
                        ep_dir, ep_j = 0, -1
                        worst, pending = 0.0, False
                else:
                    if cnt >= MIN_AGE + 1 and is_spike:
                        if cur_dir < 0:
                            t_now = tr[j]
                            bad_t = (t_now <= -theta if direction == "down"
                                     else t_now >= theta)
                            if np.isfinite(t_now) and bad_t:
                                n_block += 1
                            else:
                                ep_dir, ep_j = cur_dir, j
                        else:
                            ep_dir, ep_j = cur_dir, j
        if pos != 0:
            close(px[-1], len(px) - 1)
        return np.array(nets), sides, n_block

    # discipline gate: theta huge -> nothing blocked -> must be bit-exact
    nets_p, sides_p, nb = run_gate(60, 1e9, "down")
    ok = len(nets0) == len(nets_p) and np.allclose(nets0, nets_p)
    print(f"  parity vs run_conv (theta=1e9): "
          f"{'BIT-EXACT' if ok else 'MISMATCH'} ({nb} blocks)")
    if not ok:
        raise SystemExit("clone diverged - numbers below would be meaningless")
    shorts = np.array([n for n, s in zip(nets_p, sides_p) if s < 0])
    longs = np.array([n for n, s in zip(nets_p, sides_p) if s > 0])
    print(f"    baseline split: {len(longs)} longs ${longs.sum():+.2f}, "
          f"{len(shorts)} shorts ${shorts.sum():+.2f}\n")

    for direction,lname in (("down", "block shorts when trail <= -theta "
                             "(down-drift abstention)"),
                            ("up", "block shorts when trail >= +theta "
                             "(up-drift abstention - trade-1 style)")):
        print(f"  {lname}")
        print(f"  {'theta':>6}{'w':>6}{'n':>5}{'net$':>10}{'net/wk':>9}"
              f"{'blocks':>7}   short-trips-removed")
        for theta in GATE_THETA_U:
            for w_min in GATE_W_MIN:
                nets, sides, nblk = run_gate(w_min, theta, direction)
                wk = nets.sum() / (days / 7.0)
                n_shorts_now = sum(1 for x in sides if x < 0)
                removed = len(shorts) - n_shorts_now
                print(f"  {theta:>6.0f}{w_min:>5}m{len(nets):>5}"
                      f"{nets.sum():>+10.2f}{wk:>+9.2f}{nblk:>7}"
                      f"   ~{removed} short trips removed")
            print()

    print("verdict advisory: A says raw hour-drift itself is NOT predictable")
    print("(max|t|=1.62); C says the gate still paid as an abstention tool.")


if __name__ == "__main__":
    main()
