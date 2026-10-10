# VOL OVER FAMILY — FULL BATTERY VERDICT

**Date:** 2026-09-04 · **Status change:** the "Vol over" line (Crash/Boom 400/550/750) was a catalog ghost when last checked — it is **now loadable with 60d M1 candles + ~7d bid/ask ticks** on this account (all 6 symbols, ~86k bars each, spread ≈ 0 at tick level).

**Evidence:** `eval/voc_battery.py` (structure), `eval/voc_engine.py` (corrected engine + OOS + random controls), `eval/voc_gap.py` (tick-level loss-gap), `data/fetch_voc.py` (fetcher).

---

## 1. Structure — asymmetric monster bars, massive secular drift

| Symbol | med bar | floor | floor/med | adverse max | favorable max | 60d price |
|---|---|---|---|---|---|---|
| Vol over Crash 400 | 2.46 | 3.60 | 1.46 | 9.9 (4.0x) | **111.6 (45x)** | 3997→7311 (+83%) |
| Vol over Crash 550 | 1.22 | 3.20 | 2.62 | 4.6 (3.8x) | **62.6 (51x)** | 1889→3582 |
| Vol over Crash 750 | 4.81 | 2.50 | 0.52 | 18.1 (3.8x) | **220.4 (46x)** | 9208→15151 |
| Vol over Boom 400 | 3.40 | 3.60 | 1.06 | 11.6 (3.4x) | **141.5 (42x)** | 5670→9226 |
| Vol over Boom 550 | 2.55 | 3.20 | 1.25 | 8.7 (3.4x) | **123.3 (48x)** | 5258→7026 |
| Vol over Boom 750 | 4.60 | 2.50 | 0.54 | 18.8 (4.1x) | **242.4 (53x)** | 8862→13954 |

**This is NOT classic Boom/Crash geometry.** Direction bias ≈ 1.00 (symmetric body sums) — but the *favorable* side carries 40-53x-median monster bars while the adverse side caps near 4x. These products run a strong secular drift punctuated by violent directional discharges in the drift direction (the +83-88% runs in 60 days dwarf anything on Boom/Crash).

## 2. Corrected engine (gap-through fills, partial, cluster+cd) — full vs partial

| Symbol | FULL hold2 | PARTIAL hold2 | +cluster+cd3 | OOS1/OOS2 | RANDOM control |
|---|---|---|---|---|---|
| voc400 | −0.14 | +0.49 | +0.58 | 1.34/1.32 | +0.57 (PF 1.31) |
| vob400 | −0.14 | +0.73 | +0.68 | 1.23/1.30 | +1.01 (PF 1.46) |
| voc550 | −0.01 | +0.34 | +0.36 | 1.41/1.66 | +0.28 (PF 1.37) |
| vob550 | +0.04 | +0.74 | +0.75 | 1.62/1.39 | +0.43 (PF 1.25) |
| voc750 | −0.15 | +0.87 | +0.96 | 1.31/1.46 | +0.96 (PF 1.39) |
| vob750 | −0.24 | +0.67 | +0.69 | 1.32/1.24 | +0.96 (PF 1.41) |

**UNIT CORRECTION (2026-09-04):** the engine returns PnL in **points at half-lot weighting** (0.5× for the single half), not R. The numbers above are **points per trade**, e.g. voc400 partial = +0.58 **points**/trade, NOT +0.58R. The gap-m and breakeven-m logic is unaffected (both are ratios). The per-R edge is +0.58/18 ≈ +0.03R at SL=18 — small per unit risk; the edge is carried by trade count (~56/day), not per-trade magnitude.

Consistent with **every** prior finding in this project:
- **Full-lot fade is negative** everywhere — no raw spike-fade exists on these either.
- **The partial add-B rule is the entire positive engine** (+0.34 to +0.96R).
- **The spike trigger adds NOTHING** — random entries with identical partial mechanics match or *beat* the spike book on 4 of 6 symbols (voc400, vob400, voc750, vob750). Only vob550 shows a real trigger component (+0.32R over random) and voc550 a marginal one (+0.08R). This is the base-rate verdict repeating exactly.

## 3. The loss-gap reality — this is the killer

Tick-level stop-through measurement (how far a stop gets blown past when hit):

| Symbol | m median @1x floor | p90 | max | m median @2x floor |
|---|---|---|---|---|
| voc400 | 1.20 | 3.78 | 12.7 | 0.59 |
| vob400 | **2.23** | 9.61 | 28.2 | 1.44 |
| voc550 | 0.98 | 3.00 | 7.9 | 0.40 |
| vob550 | 1.44 | 7.81 | 22.6 | 1.48 |
| voc750 | 0.11 | 5.45 | **32.3** | 0.14 |
| vob750 | 0.13 | 8.72 | 35.2 | 0.78 |

**Benchmark:** boom1000 measured m=2.33 live — and that single tail (−55.85, 22 pts through a 5.23 stop) is why the chart was just cut. The Vol over **Boom** side has median m 1.4-2.2 at 1x floor with p90 gaps of 8-10x; the **750s** reach maxes of 32-35x. Stops placed at the broker floor on a product whose drift bars run 40-53x median get annihilated — the sim's bar-level gap fill already models −58 to −235 pt worst trades, and the tick data says live is worse, not better.

## 4. Verdict

- **Not tradeable as a spike-fade book.** The partial engine shows attractive sim PF (1.3-1.5) but its edge is schedule geometry, not spike knowledge — random controls prove it — and the loss-gap structure (median m ~1-2 at 1x floor, tails 10-35x) is the worst measured on any Deriv product, *worse than the boom1000 chart that just had to be cut for exactly this reason*.
- **Vol over Crash 400 specifically (your ask):** sim PF 1.33-1.34 OOS-consistent, but spike-vs-random delta ≈ 0 (+0.58 vs +0.57) and live gap m median 1.20 / p90 3.78 / max 12.7 at the 3.60 floor. The EV breakeven multiplier for a 74%-WR partial book is ~1.5-1.7; the measured median m is below that but the p90 tail (3.8x) is above it, and one 12x gap erases ~50 wins. Same disease as boom1000, different face.
- **The only honest path to these products:** the same ladder they all require — a **schedule-driven partial** book (no spike detector) on the *Crash* side only, sized so that a 5x-gap loss costs <0.5% equity, paper-tested 2-4 weeks. The Boom side and both 750s are disqualified outright by gap structure.

## 5. What would change the verdict
1. Broker stop floors are the whole problem — if a *wider* manual SL (e.g. 3-5x floor) is allowed on these symbols, re-measure: gap m drops with distance (2x floor already halves most medians), and the engine's partial edge may survive at SL = 3-5x med. This is the one lever worth testing.
2. Tick-accurate engine walk on the vob550/voc550 spike-trigger component (the only two with real trigger value).
3. Real fills on a demo chart — the pattern on Boom/Crash is that sim gap underestimates live; verify before believing any of it.
