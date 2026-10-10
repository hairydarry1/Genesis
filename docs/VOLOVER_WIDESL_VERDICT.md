# VOL OVER — WIDER-SL LEVER TEST

**Date:** 2026-09-04 · **Evidence:** `eval/voc_widesl.py` (tick-level gap m vs SL distance + corrected-engine EV at each SL multiplier)

**Question from `docs/VOLOVER_VERDICT.md`:** the Vol over family's broker floors (2.5–3.6 pts) are tiny vs 40–53×-median monster bars, so stops gap wide open (the boom1000 disease at higher dose). Does moving the stop OUT — SL = 2–8× the floor/median — collapse the gap enough that the partial engine's edge survives?

---

## The mechanism (why this lever was worth testing)

Gap m collapses with stop distance **because the same market event costs less in multiples**. VOC400's worst engine trade is the same ~58-pt adverse bar at every SL: at SL=1× it's a **16× gap**; at SL=8× it's a **2× gap**. Wider SL converts catastrophic gap-throughs into ordinary stop hits. And unlike most products, engine EV *rises* as SL widens here (fewer giant losses dominate) — the tail-taming more than pays for the wider risk unit.

## Results

**VOC400 (Vol over Crash 400)** — broker floor 3.60, median bar 2.46:

| SL mult | SL pts | gap m med | p90 | max | engine EV* | PF | breakeven m | live-able? |
|---|---|---|---|---|---|---|---|---|
| 1× | 3.60 | 1.20 | 3.8 | 12.7 | +0.58 | 1.33 | 1.33 | ❌ p90 3.8 > 1.33 |
| 2× | 7.20 | 0.59 | 2.6 | 5.8 | +0.59 | 1.33 | 1.33 | ❌ |
| 3× | 10.80 | 0.48 | 1.8 | 3.9 | +0.60 | 1.35 | 1.35 | ❌ |
| **5×** | 18.00 | 0.27 | **1.1** | 2.0 | **+0.63** | 1.36 | 1.36 | ✅ p90 1.1 < 1.36 |
| **8×** | 28.80 | 0.21 | **0.6** | 1.6 | **+0.66** | 1.39 | 1.39 | ✅ p90 0.6 < 1.39 |

\* engine EV is in **points per trade at half-lot weighting** (avg +0.62 pts, avg +0.035R at the 18-pt stop), 56.5 trades/day. The edge is real but per-unit-risk it is ~1/10th of the verified crash300 book — it is carried by trade COUNT, not per-trade size. Win pts 7978 vs loss pts 5867 over 60d (breakeven m 1.36).

**VOC550 (Vol over Crash 550)** — broker floor 3.20, median bar 1.22:

| SL mult | SL pts | gap m med | p90 | max | engine EV | PF | breakeven m | live-able? |
|---|---|---|---|---|---|---|---|---|
| 1× | 3.20 | 0.98 | 3.0 | 7.9 | +0.36R | 1.51 | 1.51 | ❌ |
| **2×** | 6.40 | 0.42 | **1.5** | 3.4 | **+0.37R** | 1.54 | 1.54 | ✅ |
| **3×** | 9.60 | 0.26 | **1.0** | 2.0 | **+0.38R** | 1.56 | 1.56 | ✅ |
| 5× | 16.00 | 0.18 | 0.5 | 1.0 | +0.39R | 1.57 | 1.57 | ✅ |

**VOB400 / VOB550 (Boom side)** — still dead at every SL. Gap p90 stays above breakeven even at 8× (vob400 p90 1.4 vs be 1.37, marginal; the maxes stay 2.7–4.8×). The Boom direction on these products gaps structurally harder. **Excluded.**

## Verdict

- **Vol over Crash 400 is conditionally live-able at SL = 5–8× median (18–29 pts):** engine EV +0.63–0.66R/trade, PF 1.36–1.39, WR 75%, and the measured tick-level gap p90 (0.6–1.1×) sits *below* the breakeven multiplier. This is the first config on this family that clears both tests — positive engine edge AND gap tail contained.
- **Vol over Crash 550 clears at SL = 2–5× (6–16 pts):** EV +0.37–0.39R, PF 1.54–1.57, gap p90 0.5–1.5×.
- **The Boom side stays dead** — excluded from any book.

## What it means practically

- SL moves from the broker floor to **max(med, floor) × 5** (a "wide-stop partial" config — the opposite of the 1000s' thin-floor problem). Typical loss unit grows ~5×, so **lot size must shrink ~5×** to hold dollar-risk constant — the same $ risk buys the same EV with a contained tail.
- At 75% WR with TP = 2×SL, a 5×-wider SL means TP = 2× a 5×-bigger stop — the monster favorable bars (40–111 pts on voc400) reach it within the 2-bar hold, which is why WR holds at 75% instead of collapsing.
- The trigger is still decoration (random ≈ spike on these) — this is schedule-geometry + wide-stop protection, not spike timing.

## Honest caveats (unchanged from the family verdict)
- Same 60-day window, one regime. The tick gap sample is ~7 days.
- Random-entry tick-gap measurement is a first-order estimate — real fills must confirm (the Boom/Crash pattern is sim-understates-live).
- Paper-test the exact config on a demo chart first: VOC400 at SL≈18 pts / TP≈36 pts / partial / cluster+cd3, and VOC550 at SL≈6.4–9.6 pts. Targets: ~30 closes, realized EV ≈ +0.6R (voc400) / +0.37R (voc550), worst gap ≤ 2× stop.
