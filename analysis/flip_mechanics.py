#!/usr/bin/env python3
"""Characterize Crash Boom Flip index mechanics from tick data.

Questions this answers (docs/FLIP_INDEX_MECHANICS.md):
  1. What is a "large move" here (tick-delta vs bar-range detection)?
  2. Do large-move directions form runs (phases) - crash-phase then
     boom-phase - and how long do phases last?
  3. Is the NEXT large move's direction predictable from observable state
     (transition probabilities, conditional on phase/age)?
  4. What does drift look like between large moves (the grind)?

Usage: python analysis/flip_mechanics.py
"""

import sys
from collections import Counter

import numpy as np

sys.path.insert(0, ".")

FLIPS = ["flip150", "flip300", "flip500", "flip1000"]


def load(sym):
    # prefer the accumulating daily store; fall back to the legacy 7-day fetch
    try:
        from data.flip_tick_store import load as store_load
        return store_load(sym)
    except (FileNotFoundError, ModuleNotFoundError):
        arr = np.load(f"data/ticks_{sym}.npy")
        return arr[:, 0], arr[:, 1]  # ms, price


def large_moves(ms, px, spike_mult=3.0):
    """Detect large moves as median-normalized tick deltas.

    Returns list of (tick_index, direction, size_in_medians).
    Median of ABS tick deltas; a large move = |delta| > mult x median.
    """
    d = np.diff(px)
    ad = np.abs(d)
    med = np.median(ad)
    big_idx = np.where(ad > spike_mult * med)[0]
    moves = []
    for i in big_idx:
        moves.append((i, int(np.sign(d[i])), float(ad[i] / med)))
    return moves, med


def runs_of_direction(moves):
    """Consecutive same-direction large moves = a phase run.

    Returns list of (direction, length).
    """
    runs = []
    cur_dir, cur_len = 0, 0
    for _, d, _ in moves:
        if d == cur_dir:
            cur_len += 1
        else:
            if cur_len:
                runs.append((cur_dir, cur_len))
            cur_dir, cur_len = d, 1
    if cur_len:
        runs.append((cur_dir, cur_len))
    return runs


def main():
    for sym in FLIPS:
        ms, px = load(sym)
        n = len(px)
        dur_h = (ms[-1] - ms[0]) / 3.6e6
        moves, med = large_moves(ms, px)
        dirs = [d for _, d, _ in moves]
        c = Counter(dirs)

        print("=" * 74)
        print(f"  {sym.upper()}  ticks={n:,}  span={dur_h:.1f}h  median_tick_delta={med:.5f}")
        print("=" * 74)

        if not moves:
            print("  no large moves detected")
            continue

        print(f"  large moves: n={len(moves)}  up={c[1]}  down={c[-1]}"
              f"  (up share {c[1]/len(moves)*100:.1f}%)")
        sizes = np.array([s for _, _, s in moves])
        print(f"  size (x median): p50 {np.median(sizes):.1f}  p95 {np.percentile(sizes,95):.1f}  max {sizes.max():.1f}")
        up_sz = sum(s for _, dd, s in moves if dd == 1)
        dn_sz = sum(s for _, dd, s in moves if dd == -1)
        print(f"  total up size {up_sz:.0f} vs down {dn_sz:.0f}  (ratio {up_sz/dn_sz:.3f}; 1.00 = no weekly drift)")

        runs = runs_of_direction(moves)
        rl = np.array([l for _, l in runs])
        print(f"  direction runs (phases): n={len(runs)}  len p50 {np.median(rl):.0f}"
              f"  p90 {np.percentile(rl,90):.0f}  max {rl.max()}  runs>=5: {(rl>=5).sum()}")
        up_runs = [l for d, l in runs if d == 1]
        dn_runs = [l for d, l in runs if d == -1]
        if up_runs and dn_runs:
            print(f"    up-run mean len {np.mean(up_runs):.2f} vs down-run {np.mean(dn_runs):.2f}"
                  f"  (asymmetry = structure)")

        # alternation: P(next dir != current dir) overall
        flips = sum(1 for a, b in zip(dirs, dirs[1:]) if b != a)
        print(f"  alternation rate: {flips/(len(dirs)-1)*100:.1f}% (50% = coin, high = mean-reverting spikes)")

        # gap (ticks) between consecutive large moves
        gaps = np.diff([i for i, _, _ in moves])
        print(f"  large-move gap (ticks): p50 {np.median(gaps):.0f}  p90 {np.percentile(gaps,90):.0f}"
              f"  max {gaps.max()}  (nominal period: {sym[4:]})")

        # drift: net move of all non-large ticks between consecutive large moves,
        # tested against the phase direction (observable state = previous move dir)
        d = np.diff(px)          # tick deltas (large_moves computes these internally)
        small = d.copy()
        big_set = np.zeros(len(d), dtype=bool)
        big_set[[i for i, _, _ in moves]] = True
        small[big_set] = 0.0
        idxs = [i for i, _, _ in moves]
        seg_signs = np.array([np.sign(small[a:b].sum()) for a, b in zip(idxs, idxs[1:])])
        seg_drift = np.array([small[a:b].sum() / med for a, b in zip(idxs, idxs[1:])])
        prev = np.array(dirs[:-1])
        nxt = np.array(dirs[1:])
        print(f"  inter-move drift: matches PREV move {np.mean(seg_signs == prev)*100:.1f}%"
              f"   matches NEXT move {np.mean(seg_signs == nxt)*100:.1f}%   (50% = neutral)")
        print(f"    drift magnitude per segment (x median): mean {np.mean(np.abs(seg_drift)):.1f}"
              f"  p50 {np.median(np.abs(seg_drift)):.1f}")
        isflip = prev != nxt
        if isflip.sum() > 20:
            print(f"    pre-flip segments (n={isflip.sum()}): still OLD dir {np.mean(seg_signs[isflip] == prev[isflip])*100:.1f}%"
                  f"   already NEW dir {np.mean(seg_signs[isflip] == nxt[isflip])*100:.1f}%")
        if (~isflip).sum() > 20:
            print(f"    continue segments (n={(~isflip).sum()}): matches phase {np.mean(seg_signs[~isflip] == prev[~isflip])*100:.1f}%")

        # conditional: P(next dir | current dir, gap small/large)
        for label, cond in [("gap<=p50", gaps <= np.median(gaps)), ("gap>p90", gaps > np.percentile(gaps, 90))]:
            sel = [(a == b) for a, b, keep in zip(dirs, dirs[1:], cond) if keep]
            if sel:
                print(f"  P(same dir | {label}): {np.mean(sel)*100:.1f}%  (n={len(sel)})")

        # price shape around a flip: mean small-tick cumulative move over
        # ticks -20..+20 around direction changes
        flips_idx = [k for k in range(1, len(moves)) if dirs[k] != dirs[k-1]]
        around = []
        for k in flips_idx[:400]:
            i0 = moves[k-1][0] if k > 0 else 0
            i1 = moves[k][0]
            seg = small[i0:i1]
            if len(seg) >= 10:
                around.append(seg.sum() / med)
        if around:
            a = np.array(around)
            print(f"  pre-flip segment drift (x median): mean {a.mean():+.2f}  p25 {np.percentile(a,25):+.2f}  p75 {np.percentile(a,75):+.2f}")
        print()


if __name__ == "__main__":
    main()
