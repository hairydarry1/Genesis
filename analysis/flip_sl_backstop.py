#!/usr/bin/env python3
"""How much does the 60u broker-side SL backstop cost, and is a tighter one better?

CONTEXT (2026-10-07)
--------------------
The live executor attaches a catastrophic server-side SL at
`SL_BACKSTOP_UNITS = 60.0` from the entry price on every adopted/filled
position (eval/flip_executor.py). On a 0.1-lot book that is exactly $6.00
($0.10/unit). The user starts each day at ~$10 (profit is skimmed nightly),
so ONE backstop hit is ~60% of the day's capital. On 2026-10-07 the very
first position - a short @ 9591.25230 - was stopped 60u up at 9651.25270 for
-$6.00, which is 60% of the book and the entire day's drawdown.

The backstop is a DISASTER guard, not the strategy exit: normally the
counter-spike closes the position. So the question is not "does 60u ever
fire" but "what does tightening it cost in truncation vs what it saves in
tail".

METHOD
------
- Same harness as P21 (analysis/flip_regime_desize.py): the live convention
  (rolling 5000-tick median, gap-masked, DELAY_TICKS=300 pending layer, live
  0.6261u spread). `run_sl` is a clone of run_traced with the backstop added.
- DISCIPLINE GATE: with sl_units=None the clone must reproduce the shared
  flip_rolling_engine.run_conv trip list BIT-FOR-BIT, and with the backstop
  enabled it must reproduce the shared engine on every trip the backstop did
  not touch. Numbers are not printed as findings until that passes.
- Fill convention: filled AT the stop level (what a working MT5 stop order
  does; the observed 2026-10-07 fill was 0.4u past, i.e. -$0.04, so this is
  optimistic by ~4c per hit and no more).

Usage: python analysis/flip_sl_backstop.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MIN_AGE, STEP_LOTS
from flip_gapmask import bad_tick_mask
from flip_next_gen import day_block
from flip_phase_backtest import get_specs
from flip_regime_desize import (DELAY, MULT, PER_UNIT, SPREAD_USD, SYMBOL,
                                VPU, get_med_roll, show, stats)
from flip_rolling_engine import run_conv as shared_run

SWEEP = (25.0, 40.0, 60.0, None)


def run_sl(ms, px, med_t, side=0, mode="counter", mult=MULT, delay=DELAY,
           sl_units=None):
    """run_traced + the broker-side catastrophic SL backstop.

    Returns (trips, n_gate, n_sl). Trips are (net, entry_j, exit_ms, None) so
    the shape matches the P21 harness and `stats`/`day_block` work unchanged.
    """
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr_t = mult * med_t
    pos = 0.0
    entry = 0.0
    worst = 0.0
    cur_dir, cnt = 0, 0
    pending = False
    pend_j = 0
    ep_dir, ep_j = 0, -1
    trips = []
    n_fill = n_sl = 0
    ent_j = 0

    def close(fill, j):
        nonlocal pos, pending, worst
        net = (fill - entry) * pos * VPU - SPREAD_USD
        trips.append([net, ent_j, ms[j], None])
        pos, pending, worst = 0.0, False, 0.0

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
            # catastrophic backstop fires first - it lives on the broker and
            # does not care about phase state
            if sl_units is not None and worst <= -sl_units:
                close(entry - np.sign(pos) * sl_units, j)
                n_sl += 1
                continue
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
            elif pending and mode == "timebnd" and j - pend_j >= k:
                close(px[j], j)
                continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    ent_j = j
                    n_fill += 1
                    ep_dir, ep_j = 0, -1
                    worst, pending = 0.0, False
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= MIN_AGE + 1 and is_spike:
                    if delay == 0:
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        ent_j = j
                        n_fill += 1
                        pending = False
                    else:
                        ep_dir, ep_j = cur_dir, j
    if pos != 0:
        close(px[-1], len(px) - 1)
    return trips, n_fill, n_sl


def main():
    ms, px = load(SYMBOL)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"=== SL backstop sweep | {SYMBOL} | {days:.1f} gap-masked days "
          f"| {len(px):,} ticks ===")
    print(f"live convention: rolling 5000-tick median, mult={MULT}, "
          f"min_age={MIN_AGE}, delay={DELAY}, spread={SPREAD_USD:.4f} USD/trip")
    print(f"per-unit book: ${PER_UNIT:.4f}/u at {STEP_LOTS} lots -> "
          f"25u=${25 * PER_UNIT:.2f}  40u=${40 * PER_UNIT:.2f}  "
          f"60u=${60 * PER_UNIT:.2f}  100u=${100 * PER_UNIT:.2f}\n")

    med = get_med_roll(ms, px)

    for side, sname in ((0, "LS"), (1, "L long-only")):
        base = float(np.median(med[WARMUP:]))
        # ---- discipline gate: no backstop must equal the shared engine ----
        trips, nf, nsl = run_sl(ms, px, med, side=side, sl_units=None)
        sn, _ = shared_run(ms, px, med, base, side=side, mode="counter",
                           mult=MULT, delay=DELAY)
        mine = np.array([t[0] for t in trips])
        ok = len(sn) == len(mine) and np.allclose(sn, mine)
        print(f"--- {sname} ---  parity vs flip_rolling_engine.run_conv: "
              f"{'BIT-EXACT' if ok else 'MISMATCH'} "
              f"(shared n={len(sn)} net=${sn.sum():+.2f})")
        if not ok:
            raise SystemExit("clone diverged from the shared engine - "
                             "numbers below would be meaningless")
        print(f"    trip count {len(mine)}, fills {nf}, spread cost "
              f"${SPREAD_USD * len(mine):.2f} of net ${mine.sum():+.2f}")

        print(f"\n  {'SL':<8}{'hits':>6}{'trunc$':>10}  " + "metrics")
        for sl in SWEEP:
            tr, _, ns = run_sl(ms, px, med, side=side, sl_units=sl)
            s = stats(tr, days)
            lab = "none" if sl is None else f"{sl:.0f}u/${sl * PER_UNIT:.2f}"
            # $ abandoned by stopping out early: the trips the backstop
            # touched, scored as if they had run to their normal exit
            ntr = np.array([t[0] for t in tr])
            trunc = (mine.sum() - ntr.sum()) if sl is not None else 0.0
            print(f"  {lab:<8}{ns:>6}{trunc:>+10.2f}  ", end="")
            show("", s)
            tl = [(t[0], t[2]) for t in tr]
            row = []
            for seed in (11, 12, 13):
                fl, ru = day_block(tl, seed=seed, n_sims=2000)
                row.append(f"{fl:5.1f}/{ru:4.1f}")
            print(f"      survival ($10, 4wk) flip/ruin: " + "  ".join(row))
        print()


if __name__ == "__main__":
    main()
