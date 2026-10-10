#!/usr/bin/env python3
"""New strategy probes, Sep 16 round 2 (P6-P9).

P6  INSTANT-RETRACE EXIT: exploit probes P1b showed ~67-69% of ticks right
    after a >=5x spike move opposite to it. If the mean 1-tick retrace after
    an ADVERSE spike exceeds friction, the executor could close one tick
    LATER than it does now (it fills a market order at the counter-spike
    tick). Measured here from ticks: E[d[j] | d[j-1] adverse], split by side
    and spike class (5-8x vs >8x), bad-tick masked. Gate: >= $0.05/trip at
    0.1 lot.
P7  ECHO DECAY: minor-spike density vs time-since-last-major. If minor
    floods echo majors, "time since major" is a second quiet-entry gate
    complementary to the trailing count (Appendix Y). Density = minors per
    1k ticks inside each band.
P8  QUIET-ENTRY STRESS: the X2 discipline applied to Appendix Y before it
    moves closer to deployment. r0=3 vs r0=off, long-only, fleet-validated
    engine, PAIRED 3-seed day-block survival (identical daily index means
    the same seed drives the same day-draw sequence in both walks).
P9  STAGED-T50 RE-CHECK: recorded verdict (+$7.29/wk) predates Sep 14-15.
    Rerun fleet STAGED (half off at the first counter-spike, half next tick)
    on the current archive, plus ladder survival vs incumbent/TIMEBND.

Usage: python analysis/flip_new_probes.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MULT, LIVE_SPREAD
from flip_entry_filters import VPU, STEP_LOTS
from flip_exit_families import run as fleet_run
from flip_gapmask import bad_tick_mask
from flip_next_gen import SYMBOL, day_block, ladder_fn
from flip_quiet_entry import run_quiet

MAJOR_X = 8.0
SPREAD_USD = LIVE_SPREAD * VPU * STEP_LOTS


def p6_instant_retrace(ms, px, med):
    """EV of closing 1 tick AFTER an adverse spike vs at the spike tick.
    Engine fills at px[j] when d[j-1] is the adverse spike; delaying to
    px[j+1] adds exactly d[j]. Positive E[d[j]] = the delay pays."""
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr = MULT * med
    # d[i] = px[i+1]-px[i]. Spike index i (engine's j-1); next-tick move d[i+1]
    # touches ticks i, i+1, i+2 -> all three must be unmasked.
    i = np.arange(len(d) - 1)
    ok = ~bad[i] & ~bad[i + 1] & ~bad[i + 2]
    print("=== P6 instant-retrace: EV of closing 1 tick AFTER an adverse spike ===")
    print(f"    (positive = delay captures the retrace; gate $0.05/trip; "
          f"spread {SPREAD_USD:.4f}/trip unchanged)")
    for side, adverse in (("long", -1), ("short", +1)):
        for cls, lo, hi in (("5-8x", thr, MAJOR_X * med),
                            (">8x", MAJOR_X * med, np.inf),
                            ("all>=5x", thr, np.inf)):
            m = ok & (np.sign(d[i]) == adverse) & \
                (np.abs(d[i]) >= lo) & (np.abs(d[i]) < hi)
            nxt = d[i[m] + 1]
            if len(nxt) == 0:
                print(f"  {side:5s} {cls:8s}  n=0")
                continue
            pnl = nxt * VPU
            se = pnl.std() / len(pnl) ** 0.5
            gate = "GATE PASS" if pnl.mean() >= 0.05 else "no edge"
            print(f"  {side:5s} {cls:8s}  n={len(nxt):5d}  "
                  f"mean {nxt.mean():+.5f}u (${pnl.mean():+.4f}/trip)  "
                  f"med ${np.median(pnl):+.4f}  "
                  f"{(pnl > 0).mean() * 100:4.1f}% opposite  "
                  f"SE ${se:.4f}  -> {gate}")
    print("  (live caveat: the delay holds the position one more poll-cycle;\n"
          "  archive EV is an upper bound on the live edge)\n")


def p7_echo_decay(ms, px, med):
    """Minor density vs time-since-last-major (>8x)."""
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr, big_thr = MULT * med, MAJOR_X * med
    ms_d = ms[1:len(px)]                    # timestamp of tick that ENDS delta i
    is_bad = bad[1:len(px)]
    is_major = (np.abs(d) > big_thr) & ~is_bad
    is_minor = (np.abs(d) > thr) & (np.abs(d) <= big_thr) & ~is_bad
    major_ms = ms_d[is_major]
    idx = np.searchsorted(major_ms, ms_d, side="right") - 1
    has_prev = idx >= 0
    tsm = (ms_d - major_ms[np.clip(idx, 0, None)]) / 1000.0
    bands = [(0, 30), (30, 60), (60, 120), (120, 300), (300, 600),
             (600, 1800), (1800, 10 ** 9)]
    names = ["0-30s", "30-60s", "1-2m", "2-5m", "5-10m", "10-30m", ">30m"]
    print("=== P7 echo decay: minor density vs time-since-last-major ===")
    print(f"    minors={int(is_minor.sum())}  majors={int(is_major.sum())}  "
          f"overall {is_minor.sum() / max(1, len(d)) * 1000:.2f}/1k ticks")
    for (lo, hi), name in zip(bands, names):
        sel = has_prev & (tsm >= lo) & (tsm < hi)
        n_band = int(sel.sum())
        n_min = int((is_minor & sel).sum())
        dens = n_min / n_band * 1000 if n_band else 0.0
        print(f"  {name:7s} ticks={n_band:8d}  minors={n_min:5d}  "
              f"density {dens:6.2f}/1k")
    print(f"  (before any major: {int((is_minor & ~has_prev).sum())} minors)\n")


def _fleet_trades(ms, px, med, side, fam):
    param = 0.5 if fam == "staged" else (5000 if fam == "timebnd" else None)
    nets, cms = fleet_run(ms, px, med, side=side, family=fam, param=param)
    return nets, list(zip(nets.tolist(), cms.tolist()))


def p8_quiet_stress(ms, px, med):
    print("=== P8 quiet-entry stress: paired 3-seed ladder survival (L-only) ===")
    out = {}
    for r0, lab in ((None, "r0=off"), (3, "r0=3")):
        nets, cms = run_quiet(ms, px, med, side=1, mode="counter", k=None, r0=r0)
        trades = list(zip(nets.tolist(), cms.tolist()))
        out[lab] = trades
        cells = []
        for seed in (11, 23, 47):
            fl, ru = day_block(trades, lots_mult=ladder_fn, seed=seed)
            cells.append(f"s{seed}: {fl:5.1f}/{ru:4.1f}")
        print(f"  {lab:7s} trips={len(trades):3d}  net ${nets.sum():+7.2f}  "
              f"flip/ruin  " + "  ".join(cells))
    for seed in (11, 23, 47):
        fl0, ru0 = day_block(out["r0=off"], lots_mult=ladder_fn, seed=seed)
        fl3, ru3 = day_block(out["r0=3"], lots_mult=ladder_fn, seed=seed)
        print(f"  paired delta seed {seed}: flip {fl3 - fl0:+6.1f}pp  "
              f"ruin {ru3 - ru0:+6.1f}pp")
    print()


def p9_staged_recheck(ms, px, med):
    print("=== P9 STAGED-T50 on the current archive (post Sep 14-15) ===")
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    for side, sname in ((0, "LS"), (1, "L")):
        for fam, lab in (("counter", "incumbent"), ("staged", "STAGED-T50"),
                         ("timebnd", "TIMEBND5000")):
            nets, trades = _fleet_trades(ms, px, med, side, fam)
            print(f"  {sname} {lab:12s} trips={len(nets):4d}  "
                  f"net ${nets.sum() / days * 7:+7.2f}/wk  "
                  f"worst ${nets.min():+6.2f}")
            if side == 1:
                fl, ru = day_block(trades, lots_mult=ladder_fn, seed=11)
                print(f"      L ladder flip/ruin (seed 11): {fl:5.1f}/{ru:4.1f}")
    print()


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"archive {days:.1f} days, {len(px):,} ticks, med={med:.5f}u\n")
    p6_instant_retrace(ms, px, med)
    p7_echo_decay(ms, px, med)
    p8_quiet_stress(ms, px, med)
    p9_staged_recheck(ms, px, med)


if __name__ == "__main__":
    main()
