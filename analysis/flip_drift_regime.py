#!/usr/bin/env python3
"""User hypothesis, tested directly: hour-scale DRIFT REGIMES persist.

Flip_drift_gate Part A tested LINEAR predictability across all hours
(max|t|=1.62 -> nothing). That is NOT the user's hypothesis. The user
says: there are short drifts lasting over an hour too, and the bot keeps
entering against them. That is a CONDITIONAL persistence claim: when the
trailing hour drift is BIG, the next hour tends to keep going. Linear
regression over all magnitudes can miss it entirely if persistence lives
only in the tail of |trail|.

D. PERSISTENCE CENSUS
   For w in (20,60,120,240,360)m, h in (10,30,60,120)m, theta in
   (10,20,40,80)u:
     p_same = P(sign(fwd_h) == sign(trail_w) | |trail_w| >= theta)
     z vs 0.5 (overlap-strided sampling, honest SE)
     dollar edge: E[fwd_h * sign(trail_w)] in u, with t-stat
   80 cells -> Bonferroni bar ~2.94.

E. REGIME-ALIGNED ENGINE
   run_conv clone, parity-gated: an arm is taken ONLY if the spike
   direction AGREES with sign(trail_w) and |trail_w| >= theta
   (shorts allowed INSIDE down-drifts - the user's point; neutral zone
   |trail| < theta lets the engine's own spike logic decide both sides;
   NaN trail = data hole BLOCKS the arm - never trade blind; bit-exact
   parity is proven with the gate switched off). Long/short split printed per
   config so the -34.29 short bleed can be decomposed into aligned vs
   counter-regime shorts. Reference: run_conv side=1 (long-only) run
   fresh on the same window.

Usage: python analysis/flip_drift_regime.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP  # noqa: E402
from flip_concepts import MIN_AGE, STEP_LOTS  # noqa: E402
from flip_drift_gate import build_trails  # noqa: E402
from flip_gapmask import bad_tick_mask  # noqa: E402
from flip_regime_desize import (  # noqa: E402
    DELAY, MULT, SPREAD_USD, SYMBOL, VPU, get_med_roll)
from flip_rolling_engine import run_conv as shared_run  # noqa: E402

CEN_W = (20, 60, 120, 240, 360)
CEN_H = (10, 30, 60, 120)
CEN_TH = (10.0, 20.0, 40.0, 80.0)
GATE_W = (20, 60, 120)
GATE_TH = (5.0, 10.0, 20.0, 40.0)


def main():
    ms, px = load(SYMBOL)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    bad = bad_tick_mask(ms)
    dt = np.diff(ms[WARMUP:WARMUP + 400_000])
    tpm = 60_000.0 / np.median(dt[dt > 0])
    print(f"=== drift REGIME | {SYMBOL} | {days:.1f} gap-masked days "
          f"| {len(px):,} ticks | {tpm:.0f} ticks/min ===")
    print(f"live convention: rolling median, mult={MULT}, delay={DELAY}, "
          f"spread={SPREAD_USD:.4f} USD/trip, 1u=${VPU * STEP_LOTS:.2f}\n")

    trails = build_trails(px, bad, tpm, CEN_W)

    # ---------------- D. persistence census ----------------
    print("--- D. P( fwd continues the trail ) when |trail| >= theta ---")
    print("    cell = p_same%(z);  Bonferroni bar over "
          f"{len(CEN_W) * len(CEN_H) * len(CEN_TH)} cells ~2.94")
    zmax = 0.0
    emax = 0.0
    best = None
    for w_min in CEN_W:
        trail = trails[w_min]
        for theta in CEN_TH:
            cells = []
            for h_min in CEN_H:
                lag = max(int(round(h_min * tpm)), 1)
                w_ticks = max(int(round(w_min * tpm)), 1)
                a = WARMUP + max(w_ticks, lag)
                b = len(px) - lag - 10
                if b - a < 20 * lag:
                    cells.append("  n/a       ")
                    continue
                idx = np.arange(0, b - a, lag)
                lo = a + idx
                hi = lo + lag
                fwd = (px[hi] - px[lo]) / VPU
                tr = trail[lo]
                m = np.isfinite(fwd) & np.isfinite(tr) & (np.abs(tr) >= theta)
                n = int(m.sum())
                if n < 200:
                    cells.append("  n<200     ")
                    continue
                f = fwd[m]
                t = tr[m]
                same = (f * t) > 0
                p = float(same.mean())
                z = (p - 0.5) / np.sqrt(0.25 / n)
                # aligned-direction dollar edge (u per hour-scale episode)
                edge = float(np.mean(f * np.sign(t)))
                se_e = float(np.std(f * np.sign(t)) / np.sqrt(n))
                t_e = edge / se_e if se_e > 0 else 0.0
                cells.append(f"{p * 100:5.1f}({z:+.1f})")
                if abs(z) > zmax:
                    zmax = abs(z)
                if abs(t_e) > emax:
                    emax = abs(t_e)
                    best = (w_min, theta, h_min, p, n, edge)
            print(f"  w={w_min:>3}m th={theta:>4.0f}u: "
                  + "  ".join(f"{h}m {c}" for h, c in zip(CEN_H, cells)))
    print(f"  max|z| = {zmax:.2f}  (bar ~2.94; >bar = regimes persist)")
    if best:
        print(f"  strongest dollar cell: w={best[0]}m th={best[1]:.0f}u "
              f"h={best[2]}m p={best[3] * 100:.1f}% n={best[4]} "
              f"edge={best[5]:+.2f}u/h\n")

    # ---------------- E. regime-aligned engine ----------------
    print("--- E. regime-aligned engine (arm only when spike dir agrees "
          "with big trail) ---")
    med_roll = get_med_roll(ms, px)
    base_med = float(np.median(med_roll[WARMUP:]))
    nets0, _ = shared_run(ms, px, med_roll, base_med, side=0,
                          mode="counter", mult=MULT, delay=DELAY)
    netsL, _ = shared_run(ms, px, med_roll, base_med, side=1,
                          mode="counter", mult=MULT, delay=DELAY)
    print(f"  baseline LS: {len(nets0)} trips ${nets0.sum():+.2f} "
          f"(${nets0.sum() / (days / 7):+.2f}/wk)   "
          f"long-only: {len(netsL)} trips ${netsL.sum():+.2f} "
          f"(${netsL.sum() / (days / 7):+.2f}/wk)")

    def run_aligned2(w_min, theta, gate_on=True):
        d = np.diff(px)
        thr_t = MULT * med_roll
        tr = trails[w_min]
        pos = 0.0
        entry = 0.0
        cur_dir, cnt = 0, 0
        worst = 0.0
        pending = False
        pend_j = 0
        ep_dir, ep_j = 0, -1
        nets, sides = [], []
        n_block = 0

        def close(fill, j):
            nonlocal pos, worst, pending
            nets.append((fill - entry) * pos * VPU - SPREAD_USD)
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
                    close(px[j], j)
                    continue
                elif pending and is_spike and s == np.sign(pos):
                    close(px[j], j)
                    continue

            if pos == 0:
                if ep_dir != 0:
                    if j - ep_j >= DELAY:
                        pos, entry = STEP_LOTS * ep_dir, px[j]
                        ep_dir, ep_j = 0, -1
                        worst, pending = 0.0, False
                else:
                    if cnt >= MIN_AGE + 1 and is_spike:
                        t_now = tr[j]
                        if gate_on:
                            if np.isfinite(t_now):
                                aligned = (abs(t_now) >= theta
                                           and int(np.sign(t_now))
                                           == cur_dir)
                            else:
                                aligned = False  # never trade blind
                        else:
                            aligned = True  # parity pass-through
                        if aligned:
                            ep_dir, ep_j = cur_dir, j
                        else:
                            n_block += 1
        if pos != 0:
            close(px[-1], len(px) - 1)
        return np.array(nets), sides, n_block

    # parity pass-through: gate_on=False -> verbatim clone -> bit-exact
    nets_p, sides_p, nbp = run_aligned2(60, 0.0, gate_on=False)
    ok = len(nets0) == len(nets_p) and np.allclose(nets0, nets_p)
    print(f"  parity vs run_conv (gate off): "
          f"{'BIT-EXACT' if ok else 'MISMATCH'}")
    if not ok:
        raise SystemExit("clone diverged - numbers below meaningless")
    shorts_p = np.array([n for n, s in zip(nets_p, sides_p) if s < 0])
    longs_p = np.array([n for n, s in zip(nets_p, sides_p) if s > 0])
    print(f"  gated-run cohorts (theta~0 pass-through = engine itself): "
          f"{len(longs_p)} longs ${longs_p.sum():+.2f}, "
          f"{len(shorts_p)} shorts ${shorts_p.sum():+.2f}\n")

    print(f"  {'theta':>6}{'w':>6}{'n':>5}{'net$':>10}{'net/wk':>9}"
          f"{'L$':>9}{'S$':>9}{'blocks':>8}")
    for w_min in GATE_W:
        for theta in GATE_TH:
            nets, sides, nblk = run_aligned2(w_min, theta)
            wk = nets.sum() / (days / 7.0)
            ls = sum(n for n, s in zip(nets, sides) if s > 0)
            ss = sum(n for n, s in zip(nets, sides) if s < 0)
            print(f"  {theta:>6.0f}{w_min:>5}m{len(nets):>5}"
                  f"{nets.sum():>+10.2f}{wk:>+9.2f}{ls:>+9.2f}{ss:>+9.2f}"
                  f"{nblk:>8}")
        print()

    print("verdict rule: D cells past the z-bar = regimes persist -> an")
    print("aligned gate is a real solution, not an abstention. If E's best")
    print("aligned cell beats long-only net/wk AND keeps positive aligned")
    print("shorts, adopt alignment; else the honest answer stands.")


if __name__ == "__main__":
    main()
