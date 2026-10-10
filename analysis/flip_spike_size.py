#!/usr/bin/env python3
"""Entry-spike size + exit-timing variants (user hypotheses, masked re-test).

User observations, formalized (Sep 14):
  A. "Tiny spikes take trades that lose" -> does ENTRY-spike magnitude
     predict outcome? (MAG tombstone said no, but that predates gap-masking.)
     Tercile table + filter ladder in x-multiples of the frozen median
     (the smallest detectable spike is 5.0x, so "tiny" = 5.0-5.5x band).
  B. "If the counter-spike leaves us at a loss, wait for a retrace; if the
     market keeps going against us, close" -> RETRACE-HOLD exit family:
       - pure hold: after an adverse counter-spike, hold until the first
         favorable spike (the retrace), exit there.
       - bounded hold: additionally close if ANOTHER adverse spike arrives
         while holding (market continuing through us).
     Both run standalone and stacked on the 25u drift stop (the current
     deployment candidate), LS and long-only.

Conventions identical to flip_drift_stop/flip_concepts: frozen median at
WARMUP, |d| > 5x median detector, min_age=1, live spread per episode,
$0.10/price-unit, gap-masked (bad_tick_mask). Incumbent rows must reproduce
the fleet references (LS +$19.81/wk, L +$14.79/wk) - that is the validity gate.

Usage: python analysis/flip_spike_size.py
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
X_FILTERS = [None, 5.25, 5.5, 6.0]     # entry-spike minimum size (x median)


def run(ms, px, med, side=0, exit_mode="counter", stop_u=None, x_min=None):
    """Phase-follower with pluggable exit.

    exit_mode:
      counter    - incumbent: close on any counter-spike
      retrace    - pure hold: adverse counter-spike with net<0 holds until
                   the first favorable spike closes (retrace captured)
      bounded    - retrace + close on a second adverse spike while holding
    stop_u: optional entry-anchored MAE cap (25u deployment candidate)
    x_min: optional entry filter on spike size (x median)
    Returns (nets, entry_x) arrays.
    """
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False          # holding through an adverse spike
    nets = []
    entry_x = []

    def close(fill):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        pos, worst, pending = 0.0, 0.0, False

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            continue
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))

        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1

            if pos != 0 and s != np.sign(pos):
                pnl_now = (px[j] - entry) * pos * VPU - SPREAD_USD
                if exit_mode == "counter" or pnl_now >= 0:
                    close(px[j])
                    continue
                if not pending:
                    pending = True       # adverse & losing: hold for retrace
                    continue
                if exit_mode == "bounded":
                    close(px[j])         # another adverse spike: market kept going
                    continue
                # pure retrace: keep holding through further adverse spikes
            elif pos != 0 and pending and s == np.sign(pos):
                close(px[j])             # retrace arrived: take it
                continue
        elif pos != 0:
            # catastrophe cap fires on quiet ticks even while holding a
            # retrace (bounded+stop config); pure retrace stays uncapped.
            if stop_u is not None and -worst >= stop_u:
                close(px[j])
                continue

        if pos == 0:
            want = (side == 0) or (cur_dir == side)
            if want and cnt >= MIN_AGE + 1 and big[j - 1]:
                if x_min is not None and abs(d[j - 1]) / med < x_min:
                    continue
                pos, entry = STEP_LOTS * cur_dir, px[j]
                worst, pending = 0.0, False
                entry_x.append(abs(d[j - 1]) / med)
    return np.array(nets), np.array(entry_x)


def stats(nets, days, label):
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    eq = np.cumsum(nets)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = (peak - eq).max() if len(eq) else 0.0
    wr = len(wins) / len(nets) * 100 if len(nets) else 0.0
    print(f"  {label:30s} trips={len(nets):4d}  WR={wr:4.1f}%  "
          f"net=${nets.sum()/days*7:+7.2f}/wk  "
          f"avgW=${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL=${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst=${losses.min() if len(losses) else 0:+6.2f}  maxDD=${dd:5.2f}")


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[0]) / 86400e3
    print(f"archive: {days:.1f} days (gap-masked), med={med:.5f}\n")

    print("=== A. entry-spike size vs outcome (terciles of entry x) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        nets, ex = run(ms, px, med, side=side)
        if len(ex) == 0:
            continue
        qs = np.quantile(ex, [1 / 3, 2 / 3])
        lab = np.digitize(ex, qs)
        rows = []
        for t in (0, 1, 2):
            sel = nets[lab == t]
            wr = (sel > 0).mean() * 100 if len(sel) else 0
            rows.append(f"x<{qs[0]:.2f}: n={len(sel)} wr={wr:.0f}% "
                        f"mean=${sel.mean() if len(sel) else 0:+.2f}"
                        ) if t == 0 else None
            rows.append(f"{qs[0]:.2f}-{qs[1]:.2f}: n={len(sel)} wr={wr:.0f}% "
                        f"mean=${sel.mean() if len(sel) else 0:+.2f}"
                        ) if t == 1 else None
            rows.append(f"x>{qs[1]:.2f}: n={len(sel)} wr={wr:.0f}% "
                        f"mean=${sel.mean() if len(sel) else 0:+.2f}"
                        ) if t == 2 else None
        corr = np.corrcoef(ex, nets)[0, 1] if len(ex) > 2 else float("nan")
        print(f"  {sname}: corr(x, net) = {corr:+.3f}")
        for r in rows:
            print(f"    {r}")
    print()

    print("=== A2. entry-size filter ladder (net/wk) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        for x in X_FILTERS:
            nets, _ = run(ms, px, med, side=side, x_min=x)
            stats(nets, days, f"{sname} x_min={x or 'none'}")
        print()

    print("=== B. exit variants (same entries as incumbent) ===")
    for side, sname in ((0, "LS"), (1, "L")):
        for mode, stop, label in (
                ("counter", None, "incumbent (counter-spike)"),
                ("retrace", None, "retrace-hold (pure)"),
                ("bounded", None, "retrace-hold (bounded)"),
                ("counter", 25.0, "incumbent + 25u stop"),
                ("bounded", 25.0, "retrace-bounded + 25u stop")):
            nets, _ = run(ms, px, med, side=side, exit_mode=mode, stop_u=stop)
            stats(nets, days, f"{sname} {label}")
        print()


if __name__ == "__main__":
    main()
