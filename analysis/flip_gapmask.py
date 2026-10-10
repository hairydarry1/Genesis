#!/usr/bin/env python3
"""Shared data-gap guarding for flip backtests.

Why: the archive contains (and may always contain) unhealable tick holes
(e.g. Sep 11 20:05 -> Sep 12 16:28 after the sleep wedge; the broker serves
only a few hours of tick history). Any trade that "holds" across a hole
mints phantom PnL (a real +$22.69 phantom was found on Sep 12), and the
fake 200u+ delta across the hole corrupts spike detectors and phase state.

Three tools:
  hole_intervals(ms, gap_s)  -> (N,2) array of hole [start_ms, end_ms]
  bad_tick_mask(ms, gap_s)   -> bool per tick: True where the incoming delta
                                d[j-1] spans a hole (fake). Tick-loop sims:
                                  if bad[j]: drop open pos, reset phase
                                  state, continue (no detector updates,
                                  no entries on this tick).
  crosses_hole(holes, t0,t1) -> True if [t0, t1] spans any hole. Spike-index
                                sims: drop a trade whose (entry, close)
                                span contains a hole.

Every tick-loop sim in analysis/flip_*.py uses bad_tick_mask; every
spike-index sim uses hole_intervals + crosses_hole (or excludes fake spikes
from big_idx via the same mask on ms[i+1]-ms[i]).
"""
import numpy as np

GAP_S = 300.0            # holes are gaps > 300s (5 min); ticks are ~0.5-5s


def hole_intervals(ms, gap_s=GAP_S):
    """Return (N,2) array of hole [start_ms, end_ms] where dt > gap_s.
    Empty (0,2) array if no holes."""
    dts = np.diff(ms)
    idx = np.where(dts > gap_s * 1000)[0]
    if len(idx) == 0:
        return np.empty((0, 2))
    return np.column_stack([ms[idx], ms[idx + 1]])


def bad_tick_mask(ms, gap_s=GAP_S):
    """Boolean mask over ticks: bad[j] = True iff ms[j] - ms[j-1] > gap_s
    (the delta landing at tick j is fake and must not touch the detector).
    bad[0] is always False."""
    bad = np.zeros(len(ms), dtype=bool)
    if len(ms) > 1:
        bad[1:] = np.diff(ms) > gap_s * 1000
    return bad


def crosses_hole(holes, t0, t1):
    """True if the span [t0, t1] contains any hole (strict overlap)."""
    if len(holes) == 0:
        return False
    lo = min(t0, t1)
    hi = max(t0, t1)
    i = np.searchsorted(holes[:, 1], lo, side="left")
    return bool(i < len(holes) and holes[i, 0] < hi)
