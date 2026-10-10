# The true base rate — partial-entry on a schedule vs the spike book

**Date:** 2026-09-03 · corrected M1 engine, 60d per symbol
**Companion:** `docs/BOOM150_CRASH150_VERDICT.md` (the decomposition that led here)

**Question:** the spike-fade framing assumes the entry (after a detected
discharge) is the edge. What is the true base rate of the same partial-entry
mechanics WITHOUT a spike detector — on a plain repeating schedule?

**Method:** identical SL/TP/partial/hold mechanics on (a) EA-style spike-
triggered entries (3× median, cluster skip, partial, entry+1, hold 2, with the
EA's cooldown-3) vs (b) non-overlapping count-matched schedules (enter every
~12–24th warm-median bar), on all seven symbols. All numbers pts/trade.

---

## 1. Without cooldown — the schedule matches or beats the trigger everywhere

| Symbol | trigger | schedule (count-matched) | delta |
|---|---|---|---|
| crash300 | +0.295 | +0.291 | +0.004 |
| crash500 | +0.159 | +0.173 | −0.014 |
| crash1000 | +0.099 | +0.222 | **−0.122** |
| boom500 | +0.174 | +0.248 | **−0.074** |
| boom1000 | +0.452 | +0.608 | **−0.157** |
| crash150 | +0.108 | +0.110 | −0.002 |
| boom150 | +0.101 | +0.098 | +0.003 |

The base rate of partial-entry-without-spike-detection equals or exceeds the
spike book on **6 of 7 symbols**. On boom1000/boom500/crash1000 the spike
trigger actively *loses* to a blind schedule. Offsets are stable (5 phases,
±0.05).

## 2. With the EA's loss-cooldown (time-equivalent comparison)

Cooldown-3 on triggers = skip 3 *spikes*; schedule equivalent = skip
3×mean-gap *bars* (~37–65 bars).

| Symbol | trigger cd3 | schedule cd-eq | delta | verdict |
|---|---|---|---|---|
| **crash300** | +0.353 | +0.232 | **+0.121** | spike-context REAL |
| crash500 | +0.168 | +0.160 | +0.009 | neutral |
| boom500 | +0.159 | +0.231 | −0.072 | schedule better |
| crash1000 | +0.126 | +0.158 | −0.033 | schedule better |
| **boom1000** | +0.470 | +0.371 | **+0.099** | spike-context real (sim-only caveat: live gap m=2.33) |

## 3. The three-layer anatomy of the book's edge

1. **Base layer — the partial add-rule is the entire positive EV.** Full-lot
   fades are negative on every symbol (−0.04 to −0.15); the add-B-if-
   profitable rule converts them to +0.11 to +0.61/trade *even on random or
   scheduled entries*. This is the profit engine. It is already ON in the live
   EA and requires no spike detector.
2. **Timing layer — the discharge-triggered ENTRY adds ≈ zero.** Fading
   spikes as an entry technique is neutral-to-harmful vs a blind schedule;
   the door it was believed to open (post-discharge drift) is open to any
   entry on these drift-carrying charts.
3. **Risk layer — spike context earns its keep only through the loss
   cooldown, and only on crash300 (+0.12) and sim-boom1000 (+0.10).** The
   post-loss skip (don't re-enter the discharge window) is where spike
   knowledge pays. Every entry-quality filter ever tested (gate thresholds,
   quiet regime, finals-vs-mids, hazard timing) was optimizing the layer that
   does not exist.

## 4. Why this was never seen before

Every prior test compared spike-config-vs-spike-config (gate on/off, hold
sweeps, entry offsets) — never spike-vs-no-spike. The controls run this week
(Flips, 150s, random entries) finally isolated the mechanics from the trigger.

## 5. Operational conclusions

- **Keep the crash300 (and sim-boom1000) config exactly as-is** — the
  spike+cooldown combination there beats any schedule by +0.10–0.12/trade;
  that is the project's most defensible live edge.
- **boom500/crash1000/boom1000's spike triggers should be questioned:** a
  blind schedule makes more in the sim. Consider schedule-driven partial on
  these (same risk sizing, no detector) — or at minimum stop tuning their
  spike-entry parameters, since that surface is noise.
- **The 150s are pure base-rate canvases:** floor-free, absolutely capped,
  +0.11/trade on a plain schedule with DD 14–20 pts. They are the cleanest
  testbed for the schedule-driven design and the strongest argument for
  dropping the spike detector where it adds nothing.
- **Future work should target the skip logic, not entry quality:** the
  measurable spike-context value lives in *when not to trade* (after losses,
  in discharge windows) — the mirror of every entry-side filter tested to
  date.
