# Crash Boom Flip family — full battery comparison

**Date:** 2026-09-03 · **Data:** ~48 days M1 + ~600k ticks per symbol (MT5, live)
**Evidence:** `eval/flip_battery.py` + inline controls · **Files:** `data/candles_Crash_Boom_Flip_{150,300,500,1000}_Index_1min.parquet`

The four Flip products are the closest OBTAINABLE proxies for the unavailable
"Vol over" line. Battery run: structure + reversion-by-threshold, directional
fade (partial entry) with OOS halves, quiet-regime + cooldown stack, lottery
c-curve, tick hazard, and **random-entry controls** for every survivor.

---

## The ranking (edge geometry)

| Product | floor | median bar | floor/med | Fade PF | Trigger vs random | Verdict |
|---|---|---|---|---|---|---|
| **Spot Up - Vol Up** (prior) | 10.00 | 10.6 | **0.94** | 2.37 | **z = +2.3** | ✅ real candidate |
| **Flip 150** | 0.517 | 0.412 | 1.25 | 1.14–1.23 | **z = −0.2 / −1.4** | ❌ artifact |
| Flip 300 | 8.63 | 4.20 | 2.05 | 1.00–1.10 | not tested (dead) | ❌ dead |
| Flip 500 | 3.06 | 1.30 | 2.35 | 0.91–1.08 | not tested (dead) | ❌ dead |
| Flip 1000 | 1.88 | 0.567 | 3.31 | 0.95–1.07 | not tested (dead) | ❌ dead |

**The geometry law (confirmed across 5 products):** edge quality on these
symmetric vol synthetics is predicted by **floor ÷ median-bar-range**. At
~0.9–1.0× (Vol Up) normal-bar reversion reaches the 2×-floor TP and the
discharge threshold carries real information. At 1.25× (Flip 150) the fade
geometry is *superficially* profitable but the discharge trigger carries no
information. At 2×+ (Flip 300/500/1000) the TP is unreachable by normal
moves — only discharges move price far enough, and the SL sits inside the
discharge noise.

---

## Per-product detail

**Flip 150** — the only superficially positive one, and the control kills it:
- Short-after-UP >2.0×, e+1, h2, partial: n=7,340, PF 1.17, EV +0.080R,
  OOS 1.13 / 1.20 (consistent)
- Long-after-DOWN (mirror): PF 1.14–1.17, OOS 1.17 / 1.12 (consistent)
- **30 random-entry controls: short EV +0.043 (trigger +0.041 → z=−0.23),
  long EV +0.045 (trigger +0.036 → z=−1.37)** — the discharge adds NOTHING
- Quiet + cooldown 2 stack: PF 1.23 but OOS halves flip across sides
  (1.11/1.37 vs 1.45/1.02) — noise
- Gap tail: worst partial −3.92 pts = **7.6× the 0.517 stop** (single-tick
  discharges of 1.7+ pts)
- Verdict: PF > 1 is the 2R-TP + partial-entry geometry working on *any*
  entry, exactly the boom500 pattern. **No discharge edge.**

**Flip 300** — PF 1.04–1.10 full sample but OOS1 **negative** (0.98/1.05);
worst −80 pts = **9× the 8.63 stop**. Dead.

**Flip 500** — PF 0.91–1.08, both OOS halves negative on the best config.
Dead.

**Flip 1000** — PF ~1.00 (OOS 0.94/1.04); worst −41 pts = **22× the 1.88
stop**. Lottery c>1 at K=3/5 (trig 1.31/1.72) is contradicted by the fade
(real fills, PF ~1.0) and matched by random controls (1.13/1.50) — a
gap-blind race riding the sample's downward drift. Dead.

**Shared family traits** (all four): perfectly symmetric bars (up/dn ratio
1.00, no retracement cap), **no threshold reversion** (2-bar aftermaths are
coin flips at every threshold — the Vol Up −7.4 pts/61% signature is absent),
and a **decaying tick hazard** (17–22× first bucket — clustering is a family
trait across all Deriv high-vol synthetics, but it only creates an edge where
reversion exists to trade).

---

## Bottom line

1. **None of the Flip products is worth trading on a discharge trigger.** The
   best (Flip 150) fails its random-entry control — its PF is geometry, not
   edge. The others are dead outright with 9–22× stop-gap tails.
2. **The Vol Up twin remains the only over-400-family edge** (floor/median
   0.94, threshold reversion, trigger z=+2.3, +0.29R candidate). The Flip
   family's purpose here is diagnostic: it *confirms* the geometry law —
   floor/median ≤ ~1 is the precondition for any edge on these products, and
   reversion-at-threshold is what Vol Up has and the Flips lack.
3. If the user wants another product beyond Vol Up, the search should target
   **floor/median near 1 with measurable reversion** — the earlier catalog
   scan shows Boom 150 / Crash 150 have floors of 0.100 (tiny) which may put
   their ratio in range — not the Flip line.
