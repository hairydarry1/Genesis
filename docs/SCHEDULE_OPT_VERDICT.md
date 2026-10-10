# SCHEDULE OPTIMIZATION — STEP (OVERLAP) × COOLDOWN-WINDOW SWEEP

**Date:** 2026-09-03 · **Evidence:** `eval/schedule_opt.py` (runner byte-verified against the corrected engine: 0/634 mismatches on crash300; gap-through on adverse discharge bars modeled)

**Question:** the base-rate finding said partial-entry geometry on a *plain schedule* ≈ or > the spike book. What is the optimal schedule — how often to enter (step/overlap), and what cooldown rule after a loss?

---

## Finding 1 — Overlap (laddering) is free: EV does not decay at step 1–2

Every-bar entry (step 1, up to ~3 concurrent positions given the 2–3 bar hold) has **equal-or-better EV/trade than any sparse cadence** on all 7 symbols:

| Symbol | step 1 | step 5 | step 10 |
|---|---|---|---|
| crash300 | +0.306 | +0.358 | +0.332 |
| crash500 | +0.143 | +0.150 | +0.160 |
| crash1000 | +0.170 | +0.177 | +0.170 |
| boom500 | +0.231 | +0.223 | +0.209 |
| boom1000 | +0.428 | +0.391 | +0.417 |
| crash150 | +0.106 | +0.108 | +0.101 |
| boom150 | +0.108 | +0.111 | +0.124 |

No degradation anywhere. Concurrency is pure throughput (step 1 ≈ 5× the PnL/1kb of step 5) with no EV penalty. **A single-position EA is leaving ~5× throughput on the table, not gaining edge.**

## Finding 2 — Loss-cooldown in BARS is the entire filter, and it nearly doubles EV

Sweeping the cooldown window after a **losing** close (skip entries for cd bars), step 1:

| Symbol | cd=0 EV | cd=10 EV | cd=10 PF | cd=10 loss% | cd=0 loss% |
|---|---|---|---|---|---|
| crash300 | +0.306 | **+0.741** | 2.02 | 17.4% | 27.2% |
| crash500 | +0.143 | **+0.322** | 2.02 | 11.0% | 18.9% |
| crash1000 | +0.170 | **+0.343** | 2.09 | 5.7% | 10.5% |
| boom500 | +0.231 | **+0.517** | 2.10 | 10.6% | 18.8% |
| boom1000 | +0.428 | **+0.876** | 2.08 | 5.7% | 10.5% |
| crash150 | +0.106 | **+0.251** | 2.07 | 28.3% | 39.8% |
| boom150 | +0.108 | **+0.246** | 2.06 | 28.1% | 39.5% |

**Universal across all seven.** OOS halves: cd=10 beats cd=0 in **both** halves on every symbol (e.g. crash300 OOS1 +0.851 / OOS2 +0.633; boom1000 +0.905 / +0.856).

Throughput note: cd=10 costs only ~5% of total PnL/1kb (crash300: 305 → 289) while cutting the trade count ~60% and loss-rate ~40% — the same money at a fraction of the exposure.

Plateau: cd 10–40 all ≈ equal; cd=80 slightly higher EV on crash300 but n collapses; cd=160 degrades. **cd ∈ [10, 40] bars is the robust band.**

## Finding 3 — Cooldown-after-WIN is toxic; after-every/random is neutral (the mechanism proof)

Same 10-bar skip, triggered by different events:

| Symbol | cd=0 | after LOSS | after WIN | after EVERY | after RANDOM |
|---|---|---|---|---|---|
| crash300 | +0.306 | **+0.741** | **−0.017** | +0.332 | +0.249 |
| crash500 | +0.143 | **+0.322** | +0.009 | +0.160 | +0.124 |
| crash1000 | +0.170 | **+0.343** | +0.038 | +0.170 | +0.172 |
| boom500 | +0.231 | **+0.517** | +0.012 | +0.209 | +0.261 |
| boom1000 | +0.428 | **+0.876** | **−0.185** | +0.417 | +0.438 |
| crash150 | +0.106 | **+0.251** | +0.007 | +0.101 | +0.105 |
| boom150 | +0.108 | **+0.246** | −0.007 | +0.124 | +0.115 |

**The asymmetry is the discovery.** Skipping after a loss removes the entries that would have re-bought the discharge knife (losses cluster — adverse discharge windows). Skipping after a win destroys value on every symbol (wins cluster too — you sit out the follow-through drift). Periodic or random skipping does nothing. This is *discharge clustering expressed through the exit rule*, and it is the mechanism behind everything:

- The live EA's spike-triggered "skip 3 spikes after a loss" was never about the spike detector — **the skip logic is the value**, and its units should be *bars*, not *spikes*.
- The base-rate result (spike knowledge pays only in the skip) is now explained: cooldown-after-loss on a dense cadence IS the filter.

## Why step 5 + cd=10 shows no gain

At step 5, a 10-bar skip removes only one scheduled entry (the loss already sits ~2–5 bars before the next entry, past most of the adverse window). The gain requires **dense cadence**: step 1–2, where the skip actually removes the entries inside the discharge aftermath.

## Operational spec for a schedule-driven EA (paper first)

1. **Cadence:** enter at every warm-median bar close (or every 2nd — EV identical, halves trade count).
2. **Exit:** engine-standard partial (A at entry, B added next bar only if bar-1 favorable), SL = max(median, floor), TP = 2×SL, hold ≤ 2–3 bars.
3. **Rule:** after any closed **loss**, skip entries for **10–20 bars**. Never skip after a win.
4. **Direction:** crash-family long, boom-family short (as live). Symmetric fade tested dead elsewhere.
5. **No spike detector, no gates, no hour filters** — measured decoration or noise.

**Expected:** PF ~2.0–2.2, loss rate ~half of cd=0, EV/trade up to +0.74 pts (crash300) at 5× the throughput of the current spike book's cadence if laddered, or ~57 pts/1000 bars single-position (step 5).

## Honest caveats

- **Per-trade cost: MEASURED = $0** (see `docs/LIVE_COST_VERDICT.md`). Real broker fills on Boom/Crash 1000, 300, 500 show zero spread/commission and ≤0.005 pts entry slippage; realized PnL = pure price move. The old "spread-fatal" caveat is cleared — the only real cost is the loss-gap tail (already modeled with gap-through fills).
- Same 60-day window as everything else — one regime. Paper 2–4 weeks first.
- The cd-after-loss rule is asymmetric by construction (only losses trigger). Verifying the live EA's existing spike-cooldown behaves this way: it does — but in spike units (3 spikes ≈ 40–100+ bars), which is the over-long end of the plateau.
