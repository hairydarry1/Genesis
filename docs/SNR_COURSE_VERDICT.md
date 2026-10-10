# SNR Course Battery — Verdict

Source: 6-transcript S/R course ("market makers only care about levels", classic /
breakout / head-and-shoulders / gap SNR types). Three claims were measurable.
Evidence: `eval/snr_battery.py` + sparse-level retest. Corrected engine throughout.

## Claim 1: "Spikes originate at S/R levels" — statistically true, practically empty

Permutation test (spike extremes vs time-matched random bars, distance to nearest
reaction level from ≥2x-median-range pivots):

| Map | crash300 | boom500 | crash500 |
|---|---|---|---|
| Dense (9k levels) | p=0.001 | p=0.001 | p=0.001 |
| Sparse (700 levels, deduped) | p=0.005 | p=0.27 | p=0.003 |
| Effect size (sparse) | 0.27 vs 0.24 med-rng | 0.27 vs 0.15 (wrong dir) | 0.27 vs 0.10 (wrong dir) |

With ~700 levels on the chart, *everything* is within a quarter of a median-range of
a "level" — spike extremes are a hair closer than random bars, but the discriminating
power is ~zero. **This is the classic "level-drawing always looks prophetic" trap:
with enough levels, every extreme confirms the map. Not tradeable.**

## Claim 2: breakout/flip-retest entries — DEAD, worse than control

Requiring the discharge to close through a prior level and enter on the retest:

| Symbol | flip-retest | matched-lag ctrl |
|---|---|---|
| crash300 | +0.26 | **+0.58** |
| boom500 | +0.16 | **+0.43** |
| crash500 | +0.13 | **+0.32** |

The level condition *actively selects worse entries* on all three (the through-levels
that get retested are disproportionately in exhausted legs). Dead.

## Claim 3: "double top at retest" = DOUBLE-TOUCH filter — REAL, the actual find

Gated pullback entries split by whether the bounce extreme was touched ≥2× before
the 50% retrace (the video's double-top-at-level observation):

| Symbol | DOUBLE-touch | single-touch | dbl OOS |
|---|---|---|---|
| crash300 | **+1.02 (PF 2.12, n=109)** | +0.61 | +0.54 / +1.75 ✅ |
| boom500 | **+0.97 (PF 3.60, n=33)** | +0.72 | +1.07 / +0.77 ✅ |
| crash500 | +0.19 | +0.22 | −0.04 / +0.62 ❌ |

**+66% per-trade on crash300 with real n, OOS-consistent both halves; same direction
on boom500 (thin n); dead on crash500.** Mechanism is coherent with everything
measured: a bounce that holds its extreme twice has absorbed the retry pressure —
the retrace that follows is more likely to be the continuation leg, not a failed
knee-jerk. It's the same "let the structure settle" family as the freshness edge,
expressed in price instead of time.

## Bottom line

The course's core metaphysics ("market makers trade levels") produced one dead claim
and one empty claim — but its *observed* pattern (double touch before the move) is a
genuine entry-quality filter worth +0.4/trade on crash300 and stacks directly onto the
verified spec: **gate → freshness 3-8 → pullback → double-touch (crash300, boom500
tentative) → 2×SL partial.** Full catalog of the course's types (head-and-shoulders,
gap SNR) remains discretionary decoration — no rule stated in the transcripts was
algorithmic enough to test.

## Addendum: 2×2 stack test (crash300) — freshness and double-touch do NOT compose

| Filter stack | n | avg | PF | OOS |
|---|---|---|---|---|
| gate only | 245 | +0.79 | 1.72 | +0.55 / +1.10 |
| gate + freshness 3-8 | 61 | **+1.86** | **4.72** | **+1.82 / +1.94** |
| gate + double-touch | 109 | +1.02 | 2.12 | +0.54 / +1.75 |
| gate + fresh + dbl-touch | 23 | +1.47 | 2.81 | +1.10 / +2.51 |

Adding double-touch on top of freshness **shrinks n (61→23) and lowers the average
(+1.86→+1.47)** — the two filters select the same "settled bounce" trades, so the
second one is subsumed (same pattern as cd being subsumed by the gate earlier).

Sharper spec that fell out: gate∩freshness is effectively **age 3-5 M15 bars** (the
persist=6 gate already expires ages ≥6, so the 3-8 window truncates) — and that slice
is the best crash300 config measured: +1.86/trade, OOS halves nearly identical.

**Honest caveat:** this is the third cut of the same data (bucket sweep found 3-6,
this found 3-5). Every age-restricted variant beats gate-only, so the *direction* is
robust — but the +1.86 is the best of several looks. Expect mean reversion toward
~+1.0-1.2 live. Practical spec: **use freshness as the primary settle-filter on
crash300; keep double-touch as the alternative where freshness isn't computed (boom500
was its win) — do not stack both.**

## Addendum 2: level-TOUCH as a spike predictor — NULL (the artifact autopsy)

The user's observation: "spikes happen at the same moment as a previous low or high."
Tested as conditional hazard — P(discharge within 1-5 bars of touching a prior
significant swing level) vs base rate.

**First pass: spectacular and WRONG.** P(spike|touch) looked 3.4-9.4× the base rate
(crash1000: 94% vs 10%, p=0.003). Two fatal flaws: (1) the pivot map included pivots
confirmed with *future* bars, and (2) discharge extremes are themselves pivots —
spike creates level, price touches it, spike counted: circularity.

**Lookahead-free retest (pivots confirmed incrementally, touches evaluated only
against already-confirmed levels):**

| Symbol | lift h=1 | lift h=3 | lift h=5 | verdict |
|---|---|---|---|---|
| crash300 | 0.98× | 0.98× | 0.99× | noise |
| crash500 | 0.97× | 0.99× | 0.99× | noise |
| crash1000 | 1.02× | 1.03× | 0.97× | noise |
| boom500 | 1.04× | 1.05× (p=0.013) | 1.02× | multiple-comparison artifact (15 tests) |
| boom1000 | 0.98× | 1.06× | 1.04× | noise |

Corroborating evidence: bounce *trades* at touches show no edge either (crash300
+0.260 vs random +0.252; crash1000/boom500/boom1000 all WORSE than random) — if
touches really preceded spikes at 5-9×, bounce entries would print money. They don't.

**Why the observation feels so real on the chart:** (1) every discharge extreme
*becomes* a level, so the level map is self-seeding; (2) ~700 significant levels
survive on 30 days of data — coincidence coverage is near-total; (3) memory selects
hits and discards the hundreds of spikes away from levels. Same visual trap as the
spike-origin test, now with a lookahead artifact on top.

**The pattern across everything tested**: claims of the form "confirm-then-react"
(partial-add, gate, freshness, double-touch) survive; claims of the form
"this event predicts the discharge" (spike origin, level touch, hazard state) never
do. The discharge remains unpredictable. That asymmetry IS the market's structure.
