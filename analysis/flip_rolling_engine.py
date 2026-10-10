#!/usr/bin/env python3
"""P17: rolling-median (live) vs frozen-median (backtest) engine recon.

DISCOVERY (Sep 16): the executor computes the spike fence against a
ROLLING median of the last 5000 tick deltas (eval/flip_executor.py
WINDOW=5000, np.median per tick). Every backtest engine freezes the
median from the warmup segment. Under the Sep 16 vol-regime discovery
(ordinary-tick scale ran 2.98x normal on Sep 14-15), the two detectors
diverge wildly in regimes: the frozen engine floods with false minors
(27/1k ticks on those days), the rolling engine self-corrects within
~83 minutes. The live book has therefore ALWAYS run a different
detector than the archive numbers it is meant to confirm.

This script quantifies it:

  1. Rolling-median series (exact live semantics: sorted 5000-tick
     window, append current delta, median = mean of two middle ranks;
     bad-tick holes freeze the window, matching the executor's GAP skip).
  2. CENSUS by day: spike votes per 1k good ticks at 5x rolling vs
     5x frozen - does the flood vanish?
  3. Oct-8 menu cells re-run under BOTH conventions (LS):
     fence5 counter, fence5 TB5000, fence8 counter, fence8 TB5000.
  4. Filter interactions under rolling: quiet r0=3 (hypothesis: no-op,
     like at fence8 - rolling median IS the root-cause fix) and the
     delay600 candidate (does its fence5 edge survive?).
  5. Day-table frozen vs rolling on the headline cell + survival.

Usage: python analysis/flip_rolling_engine.py
"""
import sys
from bisect import bisect_left, insort
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, STEP_LOTS
from flip_gapmask import bad_tick_mask
from flip_next_gen import day_block
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT
WINDOW = 5000   # executor WINDOW


def rolling_medians(ms, px):
    """Per-tick rolling median of |delta| over the last WINDOW deltas.

    Matches the executor exactly: window is a deque(maxlen=5000) that
    the executor appends abs(delta) to BEFORE computing np.median.
    Bad ticks: the executor never sees them as deltas (GAP logic skips
    hole deltas and resets phase), so the window freezes across holes.
    Returns med array aligned to d-space (len(px)-1).
    """
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    n = len(d)
    med = np.zeros(n)
    ss = []                     # sorted list of window |deltas|
    raw = []                    # arrival order for eviction
    for j in range(n):
        if bad[j + 1]:
            continue            # hole: delta never enters the window
        x = abs(d[j])
        if len(raw) == WINDOW:
            old = raw.pop(0)
            ss.pop(bisect_left(ss, old))
        insort(ss, x)
        raw.append(x)
        # executor semantics: np.median of the current deque contents,
        # even while the window is still filling at startup
        m = len(ss)
        med[j] = ss[m // 2] if m % 2 else (ss[m // 2 - 1] + ss[m // 2]) / 2.0
    return med


def run_conv(ms, px, med_t, frozen_med, side=0, mode="counter", k=None,
             mult=5.0, r0=None, delay=0, major_x=8.0):
    """Fleet phase-follower where the fence uses a per-tick median.

    med_t is the per-tick median (rolling if from rolling_medians,
    constant if frozen). Strategy logic is verbatim flip_next_gen.run
    (plus the quiet r0 gate and pending-entry delay layer from
    flip_entry_delay, both bit-exact-validated in their studies).
    """
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    thr_t = mult * med_t if np.ndim(med_t) else mult * med_t * np.ones(len(d))
    minor_thr = major_x * med_t if np.ndim(med_t) else major_x * med_t * np.ones(len(d))

    pos = 0.0
    entry = 0.0
    cur_dir, cnt = 0, 0
    worst = 0.0
    pending = False
    pend_j = 0
    ep_dir, ep_j = 0, -1
    minor_ts = []
    nets = []
    cms = []
    n_cancel = n_fill = 0

    def close(fill, j):
        nonlocal pos, worst, pending
        nets.append((fill - entry) * pos * VPU - SPREAD_USD)
        cms.append(ms[j])
        pos, worst, pending = 0.0, 0.0, False

    def minors_recent(j):
        return sum(1 for i in reversed(minor_ts) if j - i <= 2000)

    for j in range(WARMUP, len(px)):
        if bad[j]:
            pos, cur_dir, cnt, worst, pending = 0.0, 0, 0, 0.0, False
            ep_dir, ep_j = 0, -1
            minor_ts = []
            continue
        is_spike = bool(d[j - 1] > thr_t[j - 1] or -d[j - 1] > thr_t[j - 1])
        s = int(np.sign(d[j - 1])) if is_spike else 0
        if is_spike:
            if r0 is not None and abs(d[j - 1]) <= minor_thr[j - 1]:
                minor_ts.append(j - 1)
                if len(minor_ts) > 4096:
                    del minor_ts[:-2048]
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if ep_dir != 0 and s != 0 and s != ep_dir:
                n_cancel += 1
                ep_dir, ep_j = 0, -1

        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
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
            elif pending:
                if mode == "timebnd" and j - pend_j >= k:
                    close(px[j], j)
                    continue

        if pos == 0:
            if ep_dir != 0:
                if j - ep_j >= delay:
                    pos, entry = STEP_LOTS * ep_dir, px[j]
                    n_fill += 1
                    ep_dir, ep_j = 0, -1
                    worst, pending = 0.0, False
            else:
                want = (side == 0) or (cur_dir == side)
                if want and cnt >= MIN_AGE + 1 and is_spike:
                    if r0 is not None and minors_recent(j) >= r0:
                        pass
                    elif delay == 0:
                        pos, entry = STEP_LOTS * cur_dir, px[j]
                        worst, pending = 0.0, False
                    else:
                        ep_dir, ep_j = cur_dir, j
    if pos != 0:
        close(px[-1], len(px) - 1)
    return np.array(nets), np.array(cms)


def day_of(t):
    return datetime.fromtimestamp(t / 1000, tz=timezone.utc).date()


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    frozen_med = float(np.median(np.abs(d[:WARMUP])))
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print(f"flip1000: {days:.1f} days, frozen med={frozen_med:.5f}, "
          f"live convention: rolling median over {WINDOW} ticks\n")

    print("computing rolling-median series (bisect window)...")
    import time as _t
    t0 = _t.time()
    med_roll = rolling_medians(ms, px)
    print(f"  done in {_t.time()-t0:.1f}s  "
          f"(median of med_roll={np.median(med_roll[WARMUP:]):.5f}, "
          f"p95={np.percentile(med_roll[WARMUP:], 95):.5f})\n")

    print("=== 1. CENSUS by day: spike votes /1k good ticks (fence5) ===")
    ok = ~bad_tick_mask(ms)[1:len(px)]
    vote_f = (np.abs(d) > 5.0 * frozen_med) & ok
    vote_r = (np.abs(d) > 5.0 * med_roll) & ok
    jms = ms[1:len(px)]
    print(f"{'day':10s} {'frozen':>7s} {'rolling':>8s}")
    for dd in sorted({day_of(t) for t in jms[ok]}):
        m = ok & np.array([day_of(t) == dd for t in jms])
        n_ok = int(m.sum())
        print(f"{dd}  {int(vote_f[m].sum())/n_ok*1000:7.2f} "
              f"{int(vote_r[m].sum())/n_ok*1000:8.2f}")
    print()

    print("=== 2. Oct-8 menu cells: FROZEN vs ROLLING (LS) ===")
    cells = (("fence5 counter", 5.0, "counter", {}),
             ("fence5 TB5000 ", 5.0, "timebnd", dict(k=5000)),
             ("fence8 counter", 8.0, "counter", {}),
             ("fence8 TB5000 ", 8.0, "timebnd", dict(k=5000)))
    keep = {}
    for label, mult, mode, kw in cells:
        nf, cf = run_conv(ms, px, frozen_med, frozen_med, side=0,
                          mode=mode, mult=mult, **kw)
        nr, cr = run_conv(ms, px, med_roll, frozen_med, side=0,
                          mode=mode, mult=mult, **kw)
        keep[label] = (nr, cr)
        print(f"  {label} frozen: trips={len(nf):4d}  "
              f"net=${nf.sum()/days*7:+7.2f}/wk   |  "
              f"rolling: trips={len(nr):4d}  net=${nr.sum()/days*7:+7.2f}/wk")
    print()

    print("=== 3. filter interactions under ROLLING (fence5, LS) ===")
    for label, kw in (("plain            ", dict()),
                      ("quiet r0=3       ", dict(r0=3)),
                      ("quiet r0=3+dly600", dict(r0=3, delay=600))):
        nr, cr = run_conv(ms, px, med_roll, frozen_med, side=0,
                          mode="counter", mult=5.0, **kw)
        print(f"  rolling fence5 counter {label}: trips={len(nr):4d}  "
              f"net=${nr.sum()/days*7:+7.2f}/wk")
    print()

    print("=== 4. day-table: headline cell frozen vs rolling ===")
    nf, cf = run_conv(ms, px, frozen_med, frozen_med, side=0,
                      mode="timebnd", mult=8.0, k=5000)
    nr, cr = keep["fence8 TB5000 "]
    df_ = {}
    dr_ = {}
    for n_, t_ in zip(nf, cf):
        df_[day_of(t_)] = df_.get(day_of(t_), 0.0) + n_
    for n_, t_ in zip(nr, cr):
        dr_[day_of(t_)] = dr_.get(day_of(t_), 0.0) + n_
    alldays = sorted(set(df_) | set(dr_))
    print(f"{'day':10s} {'frozen$':>9s} {'rolling$':>9s}")
    for dd in alldays:
        print(f"{dd}  {df_.get(dd, 0.0):+9.2f} {dr_.get(dd, 0.0):+9.2f}")
    print()

    print("=== 5. survival under ROLLING (LS TB5000 cells + dly600, fixed 0.1) ===")
    for label, mode, kw in (("fence5 TB5000   ", "timebnd", dict(k=5000)),
                            ("fence5 TB+r0=3  ", "timebnd",
                             dict(k=5000, r0=3)),
                            ("fence8 TB5000   ", "timebnd", dict(k=5000)),
                            ("f5 ctn r0=3 d600", "counter",
                             dict(r0=3, delay=600))):
        nr, cr = run_conv(ms, px, med_roll, frozen_med, side=0,
                          mode=mode, mult=5.0 if "fence5" in label else 8.0,
                          **kw)
        trades = list(zip(nr.tolist(), cr.tolist()))
        row = []
        for seed in (11, 12, 13):
            fl, ru = day_block(trades, seed=seed)
            row.append(f"{fl:5.1f}/{ru:4.1f}")
        print(f"  {label} net=${nr.sum()/days*7:+7.2f}/wk  flip/ruin: "
              + "  ".join(row))


if __name__ == "__main__":
    main()
