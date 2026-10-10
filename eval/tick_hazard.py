#!/usr/bin/env python3
"""
TICK-LEVEL SPIKE HAZARD — is a spike more likely the longer we wait?
====================================================================
Tests the "tick counter / overdue / imminent" theory at its native
resolution: does P(spike on the next tick) rise as the tick-gap since
the last spike grows?

Raw tick files: data/ticks_{crash,boom}{500,1000}_index.npy (price series).
A spike tick = a single-tick move in the adverse direction exceeding T.

Method: discrete hazard estimate. For each tick compute g = ticks since
the previous spike. Bucket by g. hazard(g-bucket) = (# spikes firing in
bucket) / (# ticks that reached that bucket). Flat hazard => Poisson /
memoryless => "imminent" is undetectable. Rising hazard => exploitable.

Run: python eval/tick_hazard.py [symbol] [threshold]
     symbols: crash500 crash1000 boom500 boom1000 (default all)
     threshold: absolute pts (default = auto: >99.5th pct of |moves|)
"""
import numpy as np
import sys

FILES = {"crash500": ("data/ticks_crash_500_index.npy", -1),
         "crash1000": ("data/ticks_crash1000_index.npy", -1),
         "boom500": ("data/ticks_boom_500_index.npy", 1),
         "boom1000": ("data/ticks_boom1000_index.npy", 1)}


def hazard(name, T=None):
    f, direction = FILES[name]
    px = np.load(f)
    d = np.diff(px)
    adv = direction * d
    n = len(adv)
    if T is None:
        # auto: capture the discharge tail (~0.4% of adverse moves)
        adv_pos = adv[adv > 0]
        T = np.percentile(adv_pos, 99.6)
    spike = adv > T
    sidx = np.where(spike)[0]
    if len(sidx) < 50:
        print(f"{name}: too few spikes ({len(sidx)}) at T={T:.3f}")
        return None
    gaps = np.diff(sidx)
    print(f"\n=== {name} | threshold={T:.3f} pts | {len(sidx)} spikes | "
          f"gap ticks: med={np.median(gaps):.0f} p25={np.percentile(gaps,25):.0f} "
          f"p75={np.percentile(gaps,75):.0f} p95={np.percentile(gaps,95):.0f} "
          f"max={gaps.max():.0f} ===")

    last = np.full(n, -1)
    last[sidx] = sidx
    last = np.maximum.accumulate(last)
    gsince = np.arange(n) - last
    sp_prev = np.concatenate([[-1], sidx[:-1]])
    gsince[sidx] = sidx - sp_prev
    gsince[last == -1] = 0
    is_spike = np.zeros(n, bool)
    is_spike[sidx] = True

    edges = list(range(0, 1601, 100)) + [10 ** 9]
    rows = []
    for a, b in zip(edges[:-1], edges[1:]):
        m = (gsince > a) & (gsince <= b)
        tot = m.sum()
        hit = (m & is_spike).sum()
        if tot > 300:
            rows.append((a + 1, b, 100.0 * hit / tot, tot, hit))
    base = rows[0][2]
    print(f"    {'gap bucket':<15}{'rate%/tick':>12}{'x_base':>8}{'n':>9}{'fire':>7}")
    for a, b, r, tot, hit in rows:
        print(f"    g={a:>4}-{b:<6}{r:11.4f}{r / base:8.2f}{tot:9.0f}{hit:7.0f}")
    zm = []
    for rw in rows:
        p = rw[2] / 100
        z = (p - base / 100) / np.sqrt(max(p * (1 - p), 1e-12) / rw[3])
        zm.append(abs(z))
    print(f"    max |z| across buckets: {max(zm):.2f} "
          f"(>2.5 would suggest a real rise; base rate {base:.4f}%/tick)")
    return {"spikes": len(sidx), "max_z": max(zm), "base": base}


if __name__ == "__main__":
    args = sys.argv[1:]
    syms = [args[0]] if args and args[0] in FILES else list(FILES)
    T = float(args[1]) if len(args) > 1 else None
    for s in syms:
        hazard(s, T)
