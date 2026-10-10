# VOL OVER CRASH 400 — IT'S AN OSCILLATOR, NOT A DRIFTER

**Date:** 2026-09-04 · **Evidence:** `eval/voc_whatis.py`, `eval/voc_regime.py`

**The user's correction is right and it changes the model.** I had been treating Vol over Crash 400 as a boom/crash product (one-directional discharge + guaranteed reversion + a drift to ride). It is none of those.

---

## What the product actually is

| Measure | Value | Meaning |
|---|---|---|
| Net change, 59 days | **−20 pts** (flat) | No drift at all |
| Total 1-min variation | 251,350 pts | **100% oscillation, 0% directional** |
| Close path | 4443 → 7310 (+64%) → 4423 (−39%) | One giant up-leg, one giant down-leg |
| Max drawdown from peak | **−45.3%** | Extreme macro swings |
| 24h-window slopes | 53% up / 47% down, mean −3 pts/24h | Macro alternates; no persistent direction |
| Median next-2-bar move | **+2.3 pts on 75% of ALL bars**, mean ≈ 0 | Slow grind up most of the time |
| Crash bars | 6,525 down-bars >3× median; **2** up-bars >3× | The down-crash is the only violent move; up side caps ~4× median |

**The structure: a macro mean-reverter.** It grinds up through small bars (75% of 2-bar windows net +2.3 pts), then violently crashes the accumulated gain in down-bar bursts (up to 45× median), then grinds again. Net over 59 days: zero. The "drift" is an illusion — there is no direction to ride, only the grind-vs-crash cycle.

**Why every "spike-fade" framing was wrong here:**
- Post-crash next-2 moves are ≈ baseline (mean −0.06 vs −0.003) — no guaranteed reversion like boom/crash.
- The up-leg/down-leg distinction matters MORE than the spike itself (below).
- The edge the engine found (+0.6 pts/trade) was never "fade the crash" — it was the partial-add geometry harvesting the 75%-grind while the wide stop absorbed crash hits. That's why random entries matched: the *grind* is available to any entry, and the crashes are what you pay.

## The real findings

**H1 — MACRO-LEG GATING WORKS (the strongest result on this product):**

| Config | n | avg pts | WR | PF |
|---|---|---|---|---|
| SL3× no gate | 3693 | +0.610 | 74% | 1.35 |
| **SL3× 12h-up gate** | 1846 | **+0.834** | 76% | **1.52** |
| SL5× no gate | 3693 | +0.640 | 74% | 1.37 |
| **SL5× 12h-up gate** | 1846 | **+0.856** | 76% | **1.54** |

Entering crash-fades ONLY when the 12-hour macro slope is up (the crash is a dip inside a grind) lifts avg from +0.61 to **+0.83–0.86 pts/trade** and PF to 1.52–1.54. The 6h and 24h windows are weaker — 12h is the sweet spot.

**H2 — no leg persistence after crashes.** Next 1h/6h/24h moves after a big crash are indistinguishable from baseline (−0.2/−2.2/−4.6 vs −0.6/−1.8/−5.7). A crash does not predict the macro leg. No "short the down-leg" edge.

**H3 — the grind-long is a trap (and the honest numbers matter).** A naive "long every bar, harvest the 75% grind" shows PF 1.63 and +82,000 pts — until gap-through fills are added. With honest fills (crashes fill at the bar low, not the stop): cd=0 **−7,365 pts, worst −111**. cd=30 barely reaches +0.04. **The grind and the crash are the same coin**: the wide-stop partial engine works because it harvests the grind AND stops out of crashes; pure grind-long pays the full crash price.

## Revised verdict

- **Vol over Crash 400 is tradeable only as an OSCILLATOR play, not a drift/fade play.** The boom/crash engine's edge was real but mislabeled — it's a grind-harvester with crash-stops.
- **The genuinely new, product-specific edge is the 12h macro-leg gate**: fade crashes only inside confirmed up-grinds. PF 1.54, avg +0.86 pts/trade, at SL=5× with the gap containment already verified (p90 1.1× < breakeven).
- **Paper-test config (updated):** LONG after >3×-median crash bars, ONLY when 12h macro slope > 0, SL = max(median, floor) × 5 (~18 pts), TP = 2×SL, partial add-B if bar-1 profitable, cluster skip + cd-after-loss 3. Target: ~30 closes, avg ≥ +0.8 pts/trade, realized gap ≤ 2× stop.

## Honest caveats (unchanged)
- 59 days is ~1.5 macro cycles (one up-leg, one down-leg). The 12h gate was tuned on the up-leg mostly — it MUST survive a full down-leg regime to be trusted (the sample's second half IS a −39% down-leg; check the gate's OOS behavior there specifically).
- Same gap caveat as everywhere: sim underestimates live; the wide stop is the mitigation.
- The 12h slope gate adds a parameter — risk of overfitting the sample's single oscillation. Paper-test before believing.
