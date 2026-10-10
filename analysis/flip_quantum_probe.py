#!/usr/bin/env python3
"""Tick-quantum microstructure probes (Sep 16, round 3).

Perspective shift: stop reading the market as bars/spikes and read it at
the generator's ATOM level - the individual tick quantum - then zoom back
out with that microscope and re-ask "where is the perfect entry?"

P10  QUANTUM ANATOMY: what are the atoms? point size, zero-tick share,
     the full magnitude distribution, and where the 5x/8x fences sit in it.
P11  LEVEL MAGNETISM: is there pull toward round levels (0.05/0.25/1.0)?
     Conditional drift by residual decile -> would explain WHY absorption
     favors one direction. (The "different angle" probe.)
P12  SPIKE MICROANATOMY: is a "major" one generator decision or several
     ticks? Cluster analysis of same-sign spikes within 500ms - and what
     that does to phase-age semantics (the engine's cnt++).
P13  MIRROR ASYMMETRY: post-spike forward drift by sign and horizon - the
     absorption asymmetry at the tick level, quantified as drift per 1k
     ticks. The entry rule already trades this; here is its exact shape.

Each probe ends with its verdict vs the $0.05/trip gate or a mechanism
note. No strategy changes here - measurement only.

Usage: python analysis/flip_quantum_probe.py
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP
from flip_concepts import MULT
from flip_gapmask import bad_tick_mask
from flip_next_gen import SYMBOL, day_block, ladder_fn

GATE_USD = 0.05        # $/trip at 0.1 lot needed to matter
VPU01 = 0.10           # $ per price unit at 0.1 lot
MAJOR_X = 8.0


def ok_mask(ms, px):
    bad = bad_tick_mask(ms)
    return ~bad[1:len(px)]


def p10_quantum_anatomy(ms, px, med):
    d = np.diff(px)
    ok = ok_mask(ms, px)
    dq = d[ok]
    pt = 0.0001   # spec tick_size (verified 2026-09-12 quote probe); the
                  # archive carries float dust below this, so min|delta|
                  # is not a reliable point estimate
    print("=== P10 quantum anatomy: the generator's atoms ===")
    print(f"  ticks={len(dq):,}  point={pt:.5f}u  med|delta|={med:.5f}u "
          f"= {med / pt:.1f} points")
    zeros = (dq == 0).mean() * 100
    print(f"  zero-delta (no price change) share: {zeros:.1f}%")
    nz = np.abs(dq[dq != 0])
    print(f"  min nonzero |delta|: {nz.min():.8f}u  "
          f"sub-point moves: {(nz < pt).mean() * 100:.2f}% of nonzero "
          "(float dust from storage, not generator states)")
    a = np.abs(dq)
    print("  magnitude distribution (multiples of point):")
    for q in (50, 75, 90, 99, 99.9):
        print(f"    p{q:5.1f}: {np.percentile(a, q) / pt:8.1f} points")
    print("  fences and tails:")
    for x in (5.0, 8.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 2000.0):
        n = int((a > x * med).sum())
        print(f"    >{x:6.1f}x med: n={n:6d}  {n / len(a) * 100:8.4f}%  "
              f"(${x * med * VPU01:.4f}/tick at 0.1 lot)")
    print(f"  max |delta| observed: {a.max():.3f}u = {a.max() / pt:.0f} points "
          f"(${a.max() * VPU01:.2f} at 0.1 lot)\n")
    return pt


def p11_level_magnetism(ms, px, med):
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    cb = np.cumsum(bad)
    print("=== P11 level magnetism: pull toward round levels ===")
    for L in (0.05, 0.25, 1.0):
        line = [f"  L={L:4.2f}u"]
        for H in (100, 1000, 5000):
            n = len(px) - H
            if n <= 0:
                continue
            f = px[H:] - px[:-H]                     # forward return at tick k
            resid = px[:-H] - L * np.floor(px[:-H] / L)
            frac = resid / L
            valid = (cb[H:] - cb[:-H]) == 0          # no masked tick inside
            dec = np.clip((frac * 10).astype(int), 0, 9)
            means = np.array([f[(dec == k) & valid].mean() if
                              ((dec == k) & valid).sum() > 500 else np.nan
                              for k in range(10)])
            spread = np.nanmax(means) - np.nanmin(means)
            line.append(f"H={H:5d}t drift-spread {spread:+.5f}u "
                        f"(${spread * VPU01:+.4f})")
        print("  |  ".join(line))
    print("  (if levels magnetize: low-residual deciles drift DOWN, high "
          "drift UP.\n   spread >> gate/L-scale => exploitable level-anchored "
          "entry filter)\n")


def p12_spike_microanatomy(ms, px, med):
    d = np.diff(px)
    ok = ok_mask(ms, px)
    ms_d = ms[1:len(px)]
    thr = MULT * med
    big = (np.abs(d) > thr) & ok
    idx = np.where(big)[0]
    sgn = np.sign(d[idx])
    # cluster: same-sign spike within 500ms of the previous one
    clusters = []          # list of lists of indices into idx
    cur = [idx[0]]
    for a, b, sa, sb in zip(idx[:-1], idx[1:], sgn[:-1], sgn[1:]):
        if sb == sa and (ms_d[b] - ms_d[a]) < 500:
            cur.append(b)
        else:
            clusters.append(cur)
            cur = [b]
    clusters.append(cur)
    sizes = np.array([len(c) for c in clusters])
    big8 = (np.abs(d) > MAJOR_X * med) & ok
    big8_idx = set(idx[big8[idx]].tolist())
    in_multi = sizes > 1
    multi_major = sum(1 for c, m in zip(clusters, in_multi)
                      if m and any(i in big8_idx for i in c))
    n_major_events = sum(1 for c in clusters
                         if any(i in big8_idx for i in c))
    gap_ticks = []
    for c in clusters:
        if len(c) > 1:
            gap_ticks.extend(np.diff(c).tolist())
    print("=== P12 spike microanatomy: one decision or several ticks? ===")
    print(f"  spikes >=5x: {len(idx)}  clustered (same-sign, <500ms): "
          f"{int(in_multi.sum())} clusters")
    print(f"  size distribution: 1t {int((sizes == 1).sum())}  "
          f"2t {int((sizes == 2).sum())}  3t+ {int((sizes >= 3).sum())}")
    if len(gap_ticks):
        gt = np.array(gap_ticks)
        print(f"  intra-cluster tick gap: med {np.median(gt):.0f}  "
              f"p90 {np.percentile(gt, 90):.0f}")
    if n_major_events:
        print(f"  >=8x majors: {int(big8.sum())} raw spikes -> "
              f"{n_major_events} events; {multi_major} events are multi-tick "
              f"({multi_major / n_major_events * 100:.1f}%)")
    # does a multi-tick cluster look like ONE spec jump split?
    spans = []
    for c in clusters:
        if len(c) > 1 and any(i in big8_idx for i in c):
            spans.append(abs(d[c].sum()))
    if spans:
        sp = np.array(spans)
        singles = np.abs(d[idx & ~np.isin(idx, np.concatenate(
            [c for c in clusters if len(c) > 1]))]) if False else None
        print(f"  multi-tick major event span: med {np.median(sp):.3f}u "
              f"(single 8x+ spike med "
              f"{np.median(np.abs(d[sorted(big8_idx)])):.3f}u)")
    print("  ENGINE SEMANTICS: each cluster part is its own tick-delta spike,\n"
          "  so a 2-part major advances phase age by 2 and can complete the\n"
          "  entry gate by itself - 'age' partially counts cluster parts, not\n"
          "  independent decisions.\n")


def p13_mirror_asymmetry(ms, px, med):
    d = np.diff(px)
    bad = bad_tick_mask(ms)
    cb = np.cumsum(bad)
    cs = np.concatenate([[0.0], np.cumsum(d)])
    thr = MULT * med
    ok = ~bad[1:len(px)]
    up = np.where((d > thr) & ok)[0]
    dn = np.where((d < -thr) & ok)[0]
    print("=== P13 mirror asymmetry: post-spike drift by sign (u per 1k ticks) ===")
    print(f"    {'N ticks':>9s} {'after UP major':>15s} {'after DOWN major':>17s}")
    for N in (10, 100, 500, 2000, 10000):
        row = [f"    {N:9d}"]
        for arr in (up, dn):
            v = arr[arr + N < len(cs)]
            f = (cs[v + N] - cs[v]) / N * 1000
            valid = (cb[v + N] - cb[v]) == 0
            row.append(f"{f[valid].mean():+15.4f}")
        print("  ".join(row))
    print("  (positive after UP = absorption retrace - why longs win;\n"
          "   negative after DOWN = continuation - why shorts win. The entry\n"
          "   rule harvests exactly this; shape here, not a new rule.)\n")


def perspective(ms, px, med):
    d = np.diff(px)
    ok = ok_mask(ms, px)
    thr = MULT * med
    days = (ms[-1] - ms[0]) / 86_400_000
    minors = int(((np.abs(d) > thr) & (np.abs(d) <= MAJOR_X * med) & ok).sum())
    majors = int(((np.abs(d) > MAJOR_X * med) & ok).sum())
    print("=== PERSPECTIVE: the market as a market-maker business ===")
    print(f"  generator decisions visible per day: {(minors + majors) / days:.0f} "
          f"({minors / days:.0f} minors + {majors / days:.0f} majors)")
    print(f"  decisions our engine acts on: ~15-30/day - we READ ~3% of the\n"
          f"  stream and harvest persistence, not information.")
    print(f"  friction per round trip: $0.0626 spread + slippage vs measured\n"
          f"  edge $0.18-0.26: the ONLY product being sold is phase persistence\n"
          f"  (P62.8% at 8x, probes P4). Everything else - S/R, MTF, flow -\n"
          f"  is decoration on a coin with a 62.8/37.2 weight.")
    print(f"  'perfect entry' in a memoryless-arrival world cannot mean timing\n"
          f"  the clock (T-addendum: memoryless) or the size (MAG: closed).\n"
          f"  It can only mean: enter when the ODDS of persistence are\n"
          f"  highest = quiet regimes (Appendix Y) and the freshly-voted side\n"
          f"  (P4), and let the EXIT do the profit-taking. That is the angle\n"
          f"  this whole probe series keeps converging to.\n")


def p14_event_class(ms, px, med):
    """P10 found a two-population structure: ordinary ticks end near 8x med,
    true jumps start near 20x med (empty 8-20x gap), and the jump rate
    matches the spec clock (1/1000 ticks). The engine votes on 5x+ - i.e.
    partly on ordinary-tick tails. Test: move the vote fence up.
    Patching fence via module attrs (from-imports bind names locally)."""
    import flip_exit_families as fef
    import flip_next_gen as fng
    d = np.diff(px)
    ok = ok_mask(ms, px)
    days = (ms[-1] - ms[WARMUP]) / 86_400_000
    print("=== P14 event-class ladder: vote on TRUE jumps ===")
    for x in (8.0, 12.0, 20.0):
        n = int(((np.abs(d) > x * med) & ok).sum())
        print(f"    {x:4.0f}x med: {n:5d} events = {n / days:.1f}/day "
              f"(spec clock: 86.4/day)")
    base_ref = {"incumbent": (198, 15.24), "TB5000": (198, 25.78)}
    for mx in (5.0, 8.0, 12.0, 20.0):
        fef.MULT = mx
        fng.MULT = mx
        for fam, param, lab in (("counter", None, "incumbent"),
                                ("timebnd", 5000, "TB5000")):
            nets, cms = fef.run(ms, px, med, side=1, family=fam, param=param)
            note = ""
            if mx == 5.0:
                ref = base_ref[lab]
                note = ("  (fleet ref " +
                        f"{ref[0]}t/${ref[1]:+.2f}" +
                        ")")
            print(f"  fence {mx:4.1f}x {lab:9s} trips={len(nets):4d}  "
                  f"net ${nets.sum() / days * 7:+7.2f}/wk  "
                  f"WR {(nets > 0).mean() * 100:4.1f}%  "
                  f"worst ${nets.min():+6.2f}{note}")
            if mx in (12.0, 20.0):
                trades = list(zip(nets.tolist(), cms.tolist()))
                fl, ru = day_block(trades, lots_mult=ladder_fn, seed=11)
                print(f"        ladder flip/ruin (seed 11): {fl:5.1f}/{ru:4.1f}")
    fef.MULT = 5.0
    fng.MULT = 5.0
    print()


def main():
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px)[:WARMUP])))
    print(f"archive {(ms[-1] - ms[0]) / 86_400_000:.1f} days, {len(px):,} ticks, "
          f"med={med:.5f}u\n")
    p10_quantum_anatomy(ms, px, med)
    p11_level_magnetism(ms, px, med)
    p12_spike_microanatomy(ms, px, med)
    p13_mirror_asymmetry(ms, px, med)
    p14_event_class(ms, px, med)
    perspective(ms, px, med)


if __name__ == "__main__":
    main()
