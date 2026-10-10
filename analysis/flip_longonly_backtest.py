#!/usr/bin/env python3
"""Backtest the final synthesis: long-only + daily-loss stop (flip1000).

The concepts study measured long-only WITHOUT the -9 USD daily stop; the
executor's incumbent config INCLUDES it. This script makes them comparable:
both variants, stop ladder, day-block bootstrap (whole UTC days resampled,
preserving within-day clustering), fixed 0.1 lot, $10 -> $100, 4-week horizon.

Also validates the stop window for the long-only intraday profile:
  design rule = stop must sit below the deepest dip of any positive day and
  above the worst full-day total (so it clips catastrophe days only).

Usage: python analysis/flip_longonly_backtest.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_concepts import load, simulate, SYMBOL, WARMUP, HORIZON_DAYS, N_SIMS

THRESHOLDS = [None, 10.0, 9.0, 8.0, 7.0]
VARIANTS = [("baseline (L+S)", dict()), ("long-only", dict(side=1))]


def day_blocks(trades):
    days = {}
    for pnl, tms in trades:
        day = datetime.fromtimestamp(tms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        days.setdefault(day, []).append(pnl)
    return [(d, np.array(v)) for d, v in sorted(days.items())]


def sim(days, thresh, rng, start_eq=10.0, target=100.0):
    """One 4-week horizon; stop = skip rest of day once day_pnl <= -thresh."""
    eq = start_eq
    taken = skipped = 0
    for _ in range(HORIZON_DAYS):
        day = days[rng.integers(len(days))][1]
        day_pnl = 0.0
        for t in day:
            if thresh is not None and day_pnl <= -thresh:
                skipped += 1
                continue
            eq += t
            day_pnl += t
            taken += 1
            if eq <= 0:
                return eq, True, False, taken, skipped
        if eq >= target:
            return eq, False, True, taken, skipped
    return eq, False, False, taken, skipped


def stop_window(days):
    """Deepest intraday dip of any positive day, and worst full-day total."""
    dips, totals = [], []
    for d, v in days:
        cum = np.cumsum(v)
        dip = float(np.min(cum)) if len(cum) else 0.0
        total = float(v.sum())
        if total > 0:
            dips.append(dip)
        totals.append(total)
    return min(dips), min(totals)


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    dur_days = (ms[-1] - ms[WARMUP]) / 86_400_000

    for name, kw in VARIANTS:
        trades, _ = simulate(ms, px, med, **kw)
        days = day_blocks(trades)
        worst_dip, worst_day = stop_window(days)
        print(f"\n=== {name} | {len(trades)} trips, {dur_days:.1f}d | "
              f"deepest dip of any positive day: {worst_dip:+.2f} | "
              f"worst day total: {worst_day:+.2f} ===")
        rng = np.random.default_rng(7)
        print(f"{'stop':>6s} {'flip%':>7s} {'ruin%':>7s} {'alive%':>7s} "
              f"{'trips/4wk':>9s} {'skipped':>8s} {'medFinal':>9s}")
        for thresh in THRESHOLDS:
            fl = ru = 0
            tt = ts = 0
            finals = []
            for _ in range(N_SIMS):
                eq, ruined, flipped, taken, skipped = sim(days, thresh, rng)
                tt += taken
                ts += skipped
                if ruined:
                    ru += 1
                elif flipped:
                    fl += 1
                    finals.append(eq)
            med_f = float(np.median(finals)) if finals else 0.0
            lab = "none" if thresh is None else f"-${thresh:.0f}"
            print(f"{lab:>6s} {fl / N_SIMS * 100:6.1f}% {ru / N_SIMS * 100:6.1f}% "
                  f"{(N_SIMS - fl - ru) / N_SIMS * 100:6.1f}% {tt / N_SIMS:9.0f} "
                  f"{ts / N_SIMS:8.0f} {med_f:9.2f}")


if __name__ == "__main__":
    main()
