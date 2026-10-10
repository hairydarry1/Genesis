#!/usr/bin/env python3
"""Conditional flip-probability study for Crash Boom Flip indices.

Question: is the flip hazard conditioned on observable state — phase age
(# moves since phase start), time in phase, cumulative amplitude, previous
phase length? If yes, a better entry/exit rule exists than "hold to the
counter-spike."

Method: for each phase in the flip_phase_backtest simulation (same detector:
|delta| > mult x frozen median, WARMUP ticks), record features at each move
and whether the NEXT move flips. Logistic hazard via binned probabilities +
a rigorous permutation test (shuffles phase-start boundaries).

Usage: python analysis/flip_conditionality.py
"""

import sys
import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole

FLIPS = ["flip150", "flip300", "flip500", "flip1000"]
MT5_NAMES = {
    "flip150": "Crash Boom Flip 150 Index",
    "flip300": "Crash Boom Flip 300 Index",
    "flip500": "Crash Boom Flip 500 Index",
    "flip1000": "Crash Boom Flip 1000 Index",
}
# (symbol, mult) configurations from the baseline study worth conditioning
CONFIGS = [("flip300", 3.0), ("flip300", 4.0), ("flip500", 3.5),
           ("flip500", 4.0), ("flip1000", 5.0)]
WARMUP = 5000


def load(sym):
    # prefer the accumulating daily store; fall back to the legacy 7-day fetch
    try:
        from data.flip_tick_store import load as store_load
        return store_load(sym)
    except (FileNotFoundError, ModuleNotFoundError):
        arr = np.load(f"data/ticks_{sym}.npy")
        return arr[:, 0], arr[:, 1]


def get_phase_records(sym, mult):
    """Phase-segment the tick stream exactly like the baseline detector and
    emit one record per large move within each phase."""
    ms, px = load(sym)
    d = np.diff(px)
    ad = np.abs(d)
    med = float(np.median(ad[:WARMUP]))
    big_idx = np.where(ad > mult * med)[0]
    big_idx = big_idx[big_idx >= WARMUP]
    if len(big_idx) < 10:
        return []

    dirs = np.sign(d[big_idx]).astype(int)
    # phase boundaries: a flip ends the old phase
    phase_start = 0
    records = []
    for k in range(len(big_idx)):
        if k > 0 and dirs[k] != dirs[k - 1]:
            phase_start = k
        i0 = big_idx[phase_start]
        age = k - phase_start
        age_ms = ms[big_idx[k]] - ms[i0]
        cum = abs(px[big_idx[k]] - px[i0]) / med
        rec = dict(age=age, age_ms=age_ms, cum=cum,
                   flip=int(k < len(big_idx) - 1 and dirs[k + 1] != dirs[k]),
                   last=int(k == len(big_idx) - 1))
        records.append(rec)
    # phase lengths in moves (for the memoryless null)
    ends = np.where(np.diff(dirs) != 0)[0]
    lens = np.diff(np.concatenate(([-1], ends, [len(dirs) - 1])))
    return records, lens


def recs_to_arrays(recs):
    age = np.array([r["age"] for r in recs])
    age_ms = np.array([r["age_ms"] for r in recs], dtype=float)
    cum = np.array([r["cum"] for r in recs])
    flip = np.array([r["flip"] for r in recs])
    last = np.array([r["last"] for r in recs], dtype=bool)
    return age, age_ms, cum, flip, last


def simulate_ma(px, med, mult, min_age=0, ms=None, start=0):
    """Phase-follower with a min-age entry gate: enter a phase only after it
    has printed min_age+1 consecutive same-direction large moves (i.e., the
    phase survived min_age counter-spike tests). Exits stay counter-spike.
    min_age=0 reproduces the baseline strategy exactly."""
    d = np.diff(px)
    big_idx = np.where(np.abs(d) > mult * med)[0]
    if start:
        big_idx = big_idx[big_idx + 1 >= start]   # fills only from tick `start`
    if ms is not None:                     # gap-masking: drop fake edge-spikes
        bad = bad_tick_mask(ms)
        big_idx = big_idx[~bad[np.minimum(big_idx + 1, len(px) - 1)]]
        holes = hole_intervals(ms)
    else:
        holes = []
    pos, entry = 0, 0.0
    trades = []
    cur_dir, cnt = 0, 0          # current (unentered) phase direction + age+1
    last_t = None
    for i in big_idx:
        if ms is not None:
            t_fill = ms[i + 1] if i + 1 < len(ms) else ms[-1]
            if last_t is not None and crosses_hole(holes, last_t, t_fill):
                pos = 0                    # position would have spanned a hole
                cur_dir, cnt = 0, 0
            last_t = t_fill
        s = int(np.sign(d[i]))
        fill = px[i + 1] if i + 1 < len(px) else px[-1]
        if s == cur_dir:
            cnt += 1
        else:
            cur_dir, cnt = s, 1
        if pos == 0:
            if cnt >= min_age + 1:
                pos, entry = s, fill
        elif pos != s:
            trades.append(pos * (fill - entry))
            pos = 0
            if cnt >= min_age + 1:   # new phase: this spike is move #1
                pos, entry = s, fill
    if pos != 0:
        trades.append(pos * (px[-1] - entry))
    return trades


def binned_hazard(x, flip, bins, min_n=30):
    out = []
    for lo, hi in bins:
        m = (x >= lo) & (x < hi)
        n = int(m.sum())
        p = flip[m].mean() * 100 if n >= min_n else float("nan")
        out.append((lo, hi, n, p))
    return out


AGE_BINS = [(0, 1), (1, 2), (2, 3), (3, 4), (4, 6), (6, 8), (8, 12), (12, 20), (20, 10**9)]
CUM_BINS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, 30), (30, 45), (45, 70), (70, 10**9)]
TIME_BINS_MIN = [(0, 2), (2, 5), (5, 10), (10, 20), (20, 40), (40, 10**9)]


def main():
    print("=" * 78)
    print(" CONDITIONAL FLIP HAZARD — observable state vs P(next move flips)")
    print("=" * 78)
    for sym, mult in CONFIGS:
        recs, phase_lens = get_phase_records(sym, mult)
        if len(recs) < 300:
            print(f"\n{sym}@{mult}: too few moves ({len(recs)})")
            continue
        age, age_ms, cum, flip, last = recs_to_arrays(recs)
        hrs = age_ms / 3.6e6
        n = len(flip)
        print(f"\n{'=' * 78}\n {sym.upper()} @ {mult}x  moves={n}  base flip rate "
              f"{flip.mean() * 100:.1f}%  (last move excluded: {last.sum()})")
        print("=" * 78)

        for label, arr, bins in [("age (moves)", age, AGE_BINS),
                                 ("cum amplitude (x median)", cum, CUM_BINS),
                                 ("time in phase (min)", hrs * 60, TIME_BINS_MIN)]:
            rows = binned_hazard(arr[~last], flip[~last], bins)
            s = "   ".join(f"[{lo:g}{'-' if hi < 1e9 else '+'}{'' if hi < 1e9 else ''}"
                           f"{hi:g}{'':1s} {p:5.1f}% (n={nn})" if hi < 1e9 else
                           f"[{lo:g}+    {p:5.1f}% (n={nn})"
                           for lo, hi, nn, p in rows if not np.isnan(p))
            print(f"  {label:26s}: {s}")

        # Memoryless null: if phase lengths were geometric (no age info),
        # hazard would be flat at 1/mean_length regardless of age.
        mean_len = phase_lens.mean()
        null_hazard = 100.0 / mean_len
        # tradeable summary: late-phase survival vs base rate
        late = (age >= 5) & ~last
        early = (age <= 1) & ~last
        print(f"  memoryless null: mean phase len {mean_len:.1f} moves -> flat hazard "
              f"{null_hazard:.1f}%  (observed phase len p90 {np.percentile(phase_lens, 90):.0f}, max {phase_lens.max()})")
        print(f"  age<={1}: flip {flip[early].mean() * 100:.1f}%   age>=5: flip "
              f"{flip[late].mean() * 100:.1f}%   base {flip[~last].mean() * 100:.1f}%"
              f"   -> age {'CARRIES INFO' if abs(flip[late].mean() - flip[~last].mean()) > 0.02 else 'weak'}"
              f" (late-base {100 * (flip[late].mean() - flip[~last].mean()):+.1f}pp, n_late={int(late.sum())})")

    # previous-phase length conditioning: pooled over configs, per symbol
    print("\n" + "=" * 78)
    print(" PREVIOUS-PHASE LENGTH -> P(current phase is LONG) and P(flip next)")
    print("=" * 78)
    for sym in ["flip300", "flip500", "flip1000"]:
        mult = dict([("flip300", 3.5), ("flip500", 3.5), ("flip1000", 5.0)])[sym]
        recs, _ = get_phase_records(sym, mult)
        if len(recs) < 300:
            continue
        age, age_ms, cum, flip, last = recs_to_arrays(recs)
        # phase id = cumulative count of age==0 events
        phase_id = np.cumsum(age == 0)
        n_phases = phase_id[-1] + 1
        lens = np.bincount(phase_id)
        prev_len = np.array([lens[phase_id[k] - 1] if phase_id[k] > 0 else 0
                             for k in range(len(age))])
        rows = binned_hazard(prev_len[~last], flip[~last],
                             [(0, 2), (2, 4), (4, 8), (8, 15), (15, 10**9)])
        s = "   ".join(f"[{lo:g}{'+' if hi >= 1e9 else '-' + f'{hi:g}'} {p:5.1f}% (n={nn})"
                       for lo, hi, nn, p in rows if not np.isnan(p))
        print(f"  {sym:9s} prev-phase len -> flip: {s}")
        rows2 = binned_hazard(prev_len[~last], (age[~last] >= 5).astype(int),
                              [(0, 2), (2, 4), (4, 8), (8, 15), (15, 10**9)])
        s2 = "   ".join(f"{p:5.1f}% (n={nn})" for _, _, nn, p in rows2 if not np.isnan(p))
        print(f"  {sym:9s} prev-phase len -> this phase reaches age>=5: {s2}")

    # ---- rule variant: min-age entry gate --------------------------------
    print("\n" + "=" * 78)
    print(" RULE VARIANT: enter only after phase survives min_age moves")
    print("=" * 78)
    from flip_phase_backtest import get_specs, max_drawdown, bootstrap_odds, path_sim
    specs = get_specs()
    for sym, mult in CONFIGS:
        ms, px = load(sym)
        d0 = np.diff(px)
        med = float(np.median(np.abs(d0[:WARMUP])))
        sp = specs[sym]
        vpu = sp["tick_value"] / sp["tick_size"] * sp["vol_min"]
        spread_units = sp["spread_pts"] * sp["point"]
        dur_wk = (ms[-1] - ms[0]) / (3.6e6 * 168)
        print(f"\n  {sym.upper()} @ {mult}x  ({dur_wk:.2f}wk, spread {spread_units:g}u = ${spread_units * vpu:.3f})")
        for min_age in (0, 1, 2, 3):
            tr = np.array(simulate_ma(px, med, mult, min_age=min_age, ms=ms))
            n = len(tr)
            if n == 0:
                print(f"    min_age={min_age}: no trades")
                continue
            net = tr.sum() - n * spread_units
            net_usd = net * vpu
            wins = tr[tr > 0]
            cum = np.cumsum(tr - spread_units)
            line = (f"    min_age={min_age}: trades={n} ({n / dur_wk:.0f}/wk)  "
                    f"WR={len(wins) / n * 100:.1f}%  NET ${net_usd:+.2f}/wk  "
                    f"worst ${tr.min() * vpu:+.2f}  maxDD ${max_drawdown(cum) * vpu:+.2f}")
            if min_age == 0:
                line += "   <- baseline"
            print(line)
            if min_age > 0 and net_usd > 0:
                half = n // 2
                h1 = (tr[:half].sum() - half * spread_units) * vpu
                h2 = (tr[half:].sum() - (n - half) * spread_units) * vpu
                tr_usd = tr * vpu - spread_units * vpu
                odds = bootstrap_odds(tr_usd)
                r10, f10, _ = odds[10.0]
                r50, f50, _ = odds[50.0]
                ps = path_sim(tr_usd)
                print(f"        halves H1 ${h1:+.2f} / H2 ${h2:+.2f}   "
                      f"$10: ruin {r10:.0f}% / flip {f10:.1f}%   "
                      f"$50: ruin {r50:.0f}% / flip {f50:.1f}%   "
                      f"hist-path: final ${ps['final']:.0f} ruined={ps['ruined']}")


if __name__ == "__main__":
    main()
