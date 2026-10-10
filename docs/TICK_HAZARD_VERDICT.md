# ⏱️ TICK-HAZARD VERDICT — a spike is NOT more likely the longer you wait

**Date:** 2026-09-03
**Data:** raw tick series — crash500 (2.51M), crash1000 (2.51M), boom500 (2.54M), boom1000 (2.56M) consecutive prices.
**Question:** if you chase/flip and keep getting stopped by the drift before the spike fires, why not *enter later, when the spike is imminent*? The "tick counter / overdue" theory says: the longer since the last spike, the higher the chance of one now.

## Method (no assumptions, native resolution)

- Spike tick = a single tick moving in the adverse direction more than a threshold (crash: down, boom: up). Thresholds chosen to capture the true discharges (crash500 > 1 pt: 4,252 events; crash1000 > 3 pt: 1,775; boom500 > 1 pt: 4,528; boom1000 > 5 pt: 2,067).
- For every tick, compute g = ticks since the previous spike. Then the **discrete hazard** per 100-tick gap bucket:
  hazard(g) = (# spikes firing at gap g) ÷ (# ticks that reached gap g).
- A Poisson (memoryless) process has FLAT hazard. A "due" discharge — like your tick-counter docs claim, or a rubber band that must snap — shows hazard RISING with g.

## Result: FLAT on all four symbols

Rates as a multiple of the base (g = 1–100) rate, by 100-tick buckets from g=1 up to g=1,600+:

| Symbol | Base rate (%/tick) | Lowest bucket | Highest bucket | Max |z| across 17 buckets |
|---|---|---|---|---|---|
| crash500 | 0.172 | 0.87× | 1.19× | 1.76 | (noise) |
| crash1000 | 0.069 | 0.85× | 1.22× | 1.51 | (noise) |
| boom500 | 0.184 | 0.84× | 1.10× | ~1 | (noise) |
| boom1000 | 0.087 | 0.78× | 1.05× | 3.93* | (*a *dip* mid-gap, not a rise) |

There is **no rising trend on any symbol**. Spike probability per tick is statistically the same at gap 50 and gap 1,500. The gap distribution is broad and exponential-ish (crash500: median 405, p25 172, p75 824) — the signature of a Poisson discharge, **not** a forced ~500-tick clock with an "overdue" state.

## What this means

1. **"Imminent" is undetectable — entering later does not move you closer in probability.** The "enter at 700+ ticks" idea (from the earlier BOOM_CRASH_FUNDAMENTALS doc) has no measurable basis: the discharge rate is identical at tick 700 and tick 100. This is the definitive, native-resolution version of the earlier deaths (velocity, acceleration, gap-length, self-excitation — all null).
2. **It explains why the flip loses:** even a *perfect* later entry can't select high-probability windows, because there are none. And the drift-outruns-stop statistic (63–90% of gaps — see CHASE_SPIKE_VERDICT Part 3) means a short/long held waiting for the spike is stopped out before the event the large majority of the time, no matter when it enters.
3. **The one real structure in the tick data** is the directional one: crash indices creep up in ~2.5M micro-ticks between rare single-tick discharges (crash500: 5,236 down-moves vs 2.47M up-moves; crashes arrive as ONE tick of −6 to −11 pts). The money is in the creep (the fade) and the damage is in the single-tick discharge (why the fade SL gaps). There is no exploitable *timing* structure — only direction and size.

## Evidence trail

- `eval/tick_hazard.py` — reusable hazard estimator (run: `python eval/tick_hazard.py [symbol] [threshold]`).
- Raw data: `data/ticks_{crash,boom}{500,1000}_index.npy`.