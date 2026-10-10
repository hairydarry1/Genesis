# Principle Derivations — Verdict (the theory's closed-loop test)

The five measured principles (P1 drift-is-edge/discharge-is-cost, P2 conditionality
beats prediction, P3 memoryless base + clustered tail, P4 settle-time ~45-90 min,
P5 stop-width-is-the-risk-model) were each pushed for untested derivations.
Evidence: `eval/principle_derivations.py`. Result: **all six derivations null or
marginal — the theory yields no more low-hanging fruit.** That failure pattern is
itself evidence the principle set is complete.

## D1 — Breakeven-raise after favorable bar (P2 on exits): NEUTRAL
crash300 +0.469 vs +0.473 base; boom500 0.686 vs 0.692. The partial add-B rule
already monetizes bar-1 confirmation; ratcheting the stop adds nothing inside a
3-bar hold. Dead.

## D2 — Conditional hold (extend +1 bar per favorable close): NEGATIVE
crash300 **−0.16** vs +0.47; boom500 0.54 vs 0.69. Letting winners run on
confirmation *destroys* value. This is the sharpest confirmation of P1 in the whole
project: **the grind does not persist beyond ~3 bars** — extending exposure only
re-arms the discharge tail. hold=3 is a wall, not a window.

## D3 — Rolling hazard state (dense, high vs low spike-rate windows): NULL
crash300 low +0.80 vs high +0.70 (small, unsplit); boom500 +0.52 vs +0.52. The M1
discharge rate carries no exploitable state. Base-rate memorylessness confirmed.

## D4 — Discharge-age gate (dense: enter only ≥N bars after last discharge): DEAD
Flat at 15 bars, worse at 30 (crash300 +0.34→+0.25), n collapses by 60. No
quiet-period structure at M1. Same conclusion as D3.

## D5 — Settle-break (bounce must break the pre-discharge swing): NULL
crash300: broke +0.85 (n=31) vs not-broke +0.78 (n=214) — indistinguishable;
boom500 n=6, degenerate. Price-space settle confirmation adds nothing beyond the
50% retrace itself.

## D6 — Wide-stop dense harvest (SL 1x/2x/3x median): MARGINAL
crash300: identical (broker floor already provides the wide stop); boom500:
+0.46→+0.48, PF 2.86→2.98 — monotone but tiny, the stop rarely binds in a 2-bar
hold. Not worth changing the spec.

## What this means

1. **The configuration space is exhausted on these axes.** Entry timing (null,
   repeatedly), conditional exits (neutral/negative), hazard/age states (null),
   price-settle confirmations (null), stop width (marginal). Every remaining lever
   that tested positive is already in the spec: partial add-B, hold=3, 2:1 exits,
   gate, freshness, double-touch, bar-cooldown, per-symbol TF.
2. **The residual edge is execution, not discovery.** The verified stack
   (dense-gated mode + pullback-precision mode, per-symbol) is the complete harvest
   of P1-P5 at this data resolution. Further gains live in: sizing discipline
   (P5), live gap re-measurement at ~30 closes/symbol, and regime monitoring
   (the gate/freshness decay checks).
3. **The next real unlock would be new information, not new strategy** — tick-level
   book data, longer history (regime variety), or a second uncorrelated product
   family (the Vol-over line failed gap tests; the Flip family remains
   schedule-compatible but thin).

This document closes the strategy-discovery phase. The playbook is final until
live data says otherwise.
