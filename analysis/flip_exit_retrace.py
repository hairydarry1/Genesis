#!/usr/bin/env python3
"""Exit-side test: close on the counter-spike, or HOLD through it?

WHY (2026-10-10)
----------------
The user's observation after the blow-up: "in the losing trades there is
always a drift in the opposite direction, and there are tiny/small spikes
inside those drifts that do nothing - the drift just continues until it
hits the SL, and the bot usually took a trade in the opposite direction of
the drift". flip_spike_size.py (Sep 14, first run 2026-10-10) put numbers on
that on the NO-DELAY engine: identical entries, only the exit changed.

  L  incumbent (close on counter-spike)   n=605  WR=45.8%  -$1.52/wk  maxDD $64.39
  L  retrace-hold (pure)                  n=604  WR=55.8%  +$6.88/wk  maxDD $38.19
  LS incumbent                            n=898  WR=45.9%  -$7.51/wk  maxDD $105.90
  LS retrace-hold (bounded)               n=845  WR=54.0%  +$0.37/wk  maxDD $72.25

So the money in that study is in the EXIT, not the entry. But that study ran
the no-delay engine, and the live executor is the DELAY-300 (Menu C) engine.
This study re-runs the same exit question on the deployed engine:
flip_rolling_engine.run_conv conventions, rolling 5000-tick median,
mult=5, min_age=1, delay=300 ticks, live spread, 0.1 lot.

  EXITS (entries identical in every row)
    counter  - incumbent: close on the first counter-spike.
    retrace  - adverse counter-spike while LOSING: hold; exit on the first
               favourable spike (the retrace). Further adverse spikes are
               also held through (uncapped).
    bounded  - retrace, but a SECOND adverse spike while holding closes
               (the drift really is continuing through us).

  STOPS: the live server-side 60u backstop (~$6.00), and none. A stop fills
  AT the stop level (broker SL behaviour) and is checked on every tick, like
  a server-side SL - so a gap through it books no slippage here. That makes
  the stop rows, if anything, optimistic; analysis/flip_sl_backstop.py remains
  the authority on stop cost.

DISCIPLINE
----------
run_variant is run_conv verbatim with the exit branch parameterised. With
exit_mode="counter" and stop_u=None it must reproduce run_conv("counter")
BIT-FOR-BIT (nets AND close timestamps) before any table is printed.

Usage: python analysis/flip_exit_retrace.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP  # noqa: E402
from flip_concepts import MIN_AGE, STEP_LOTS  # noqa: E402
from flip_gapmask import bad_tick_mask  # noqa: E402
from flip_regime_desize import (  # noqa: E402
    DELAY, MULT, SPREAD_USD, SYMBOL, VPU, get_med_roll, show, stats)
from flip_rolling_engine import run_conv as shared_run  # noqa: E402

STOP_CHOICES = (None, 60.0)          # 60u = the live server-side backstop
EXIT_MODES = ("counter", "retrace", "bounded")


def run_variant(ms, px, med_t, side=0, exit_mode="counter", delay=0,
                stop_u=None, mult=MULT):
    """run_conv clone: identical entries, parameterised exit + optional stop.

    Returns (nets, close_ms, n_stop_fills).
    """
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr_t = (mult * med_t if np.ndim(med_t)
             else mult * med_t * np.ones(len(d)))

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    ep_dir, ep_j = 0, -1
    n_stop = 0
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
            ep_dir, ep_j = 0, -1
            continue
        is_spike = bool(d[j - 1] > thr_t[j - 1] or -d[j - 1] > thr_t[j - 1])
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
            if stop_u is not None and -worst >= stop_u:
                # server-side SL: fills at the stop level, fires on any tick
                close(entry - stop_u * np.sign(pos), j)
                n_stop += 1
                continue
            if is_spike and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if exit_mode == "counter" or pnl_now >= 0:
                    close(px[j], j)
                    continue
                if not pending:
                    pending = True
                    continue
                if exit_mode == "bounded":
                    close(px[j], j)
                    continue
                # exit_mode == "retrace": keep holding through further
                # adverse spikes - only a favourable spike closes it.
            elif pending and is_spike and s == np.sign(pos):
                close(px[j], j)
                continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    ep_dir, ep_j = 0, -1
                    worst, pending = 0.0, False
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= MIN_AGE + 1 and is_spike:
                    if delay == 0:
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        worst, pending = 0.0, False
                    else:
                        ep_dir, ep_j = cur_dir, j
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms), n_stop


def trips_of(nets, cms):
    return [(float(n), i, float(c))
            for i, (n, c) in enumerate(zip(nets, cms))]


def main():
    ms, px = load(SYMBOL)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"=== exit family | {SYMBOL} | {days:.1f} gap-masked days "
          f"| {len(px):,} ticks ===")
    print(f"deployed convention: rolling 5000-tick median, mult={MULT}, "
          f"min_age={MIN_AGE}, delay={DELAY}, spread={SPREAD_USD:.4f} USD/trip"
          f"  (1u = ${VPU * STEP_LOTS:.2f} at 0.1 lot)\n")

    med_roll = get_med_roll(ms, px)
    base_med = float(np.median(med_roll[WARMUP:]))

    # ---------------- parity gate ----------------
    print("--- 0. parity gate (run_variant must equal run_conv) ---")
    ok = True
    for side, sname in ((0, "LS"), (1, "L")):
        r_nets, r_cms = shared_run(ms, px, med_roll, base_med, side=side,
                                   mode="counter", mult=MULT, delay=DELAY)
        v_nets, v_cms, _ = run_variant(ms, px, med_roll, side=side,
                                       exit_mode="counter", delay=DELAY)
        same = (len(r_nets) == len(v_nets)
                and np.array_equal(r_nets, v_nets)
                and np.array_equal(r_cms, v_cms))
        ok &= same
        print(f"  {sname}: run_conv n={len(r_nets)} net=${r_nets.sum():+.4f} "
              f"| run_variant n={len(v_nets)} net=${v_nets.sum():+.4f} "
              f"-> {'BIT-EXACT' if same else '*** MISMATCH ***'}")
    if not ok:
        print("\nPARITY FAILED - numbers below would be meaningless. Stop.")
        return
    print()

    # ---------------- exit sweep ----------------
    for stop_u in STOP_CHOICES:
        stop_lbl = "no stop" if stop_u is None else f"{stop_u:.0f}u SL"
        print(f"--- 1. exits with {stop_lbl} "
              f"{'' if stop_u is None else '(~$%.2f, fills at the stop level)' % (stop_u * VPU * STEP_LOTS)} ---")
        for side, sname in ((0, "LS"), (1, "L")):
            for exit_mode in EXIT_MODES:
                nets, cms, n_stop = run_variant(
                    ms, px, med_roll, side=side, exit_mode=exit_mode,
                    delay=DELAY, stop_u=stop_u)
                s = stats(trips_of(nets, cms), days)
                tag = f"{sname} {exit_mode}"
                if s is None:
                    print(f"  {tag:<26} n=   0 (no trips)")
                    continue
                show(tag, s)
                if stop_u is not None:
                    print(f"  {'':<26}   SL fills: {n_stop} "
                          f"({n_stop / max(s['n'], 1) * 100:.0f}% of trips)")
            print()


if __name__ == "__main__":
    main()
