# VOL OVER CRASH 400 — 15-ITEM CONCEPT CATALOG

**Date:** 2026-09-04 · **Evidence:** `eval/voc_hunt.py`, `eval/voc_legcheck.py`, `eval/voc_whatis.py`, `eval/voc_widesl.py`, `eval/voc_engine.py`, `eval/voc_stress.py`

**The product, one line:** a macro oscillator (net −20 pts / 59 days, 100% of variation is oscillation) that grinds up through small bars (75% of 2-bar windows net +2.3 pts) and violently crashes the gain back in down-bars up to 45× median. 6,525 crash bars >3× vs 2 up-bars. There is no drift to ride — the trade is the grind-vs-crash cycle.

**The verified engine baseline:** LONG after >3×-median crash bars, SL = max(median, floor) × 5 (~18 pts), TP = 2×SL, partial add-B if bar-1 profitable, cluster skip, cd-after-loss. **+0.64 pts/trade, PF 1.37, WR 74%**, OOS/third/leg-stable, tick spread ≈ 0, gap p90 1.1× < breakeven 1.36 at SL5.

---

## ✅ Verified and stacked (the money)

**1. The wide-stop partial crash-fade (the core).** SL5× + partial + cluster + cd3 = +0.64 pts/trade, PF 1.37, WR 74%, 63 trades/day. Survives all three time-thirds (T3 down-leg PF 1.27) and both macro legs (+0.77 up / +0.59 down). Full-lot is negative (−0.14) — partial is the entire engine, as everywhere in this project.

**2. Bar-based loss cooldown (cd=10) is the top refinement.** cd=10 lifts +0.64 → **+0.74 pts/trade, PF 1.45** at half the trade count (31.5/day). OOS halves both positive (OOS1 +0.95 PF 1.59 / OOS2 +0.51 PF 1.30). Same mechanism as the boom/crash schedule finding: losses cluster in adverse windows; skipping re-entry after a loss avoids the knife.

**3. Small crashes revert better than big ones.** Crash 3–5× band: +0.743 (PF 1.43) vs 5–8×: +0.496 (PF 1.27). Counter-intuitive — the 3–5× "dips inside a grind" are the cleanest buys; the 8–12×+ monsters are closer to leg-flips. Worth tilting size toward the 3–5× band (note: stacking cd=10 + 3–5× gives +0.70 — they overlap, no additive gain).

**4. SL width is monotone-good (3×→8×: +0.76→+0.82).** The wider the stop, the better — the crash tail is the only real cost and width absorbs it. No knife-edge. SL8× is fine; the constraint is per-trade dollar risk (wider SL = smaller lots).

**5. The 12h macro-up gate is a modest tilt (+0.15).** Up-leg entries +0.77 vs ungated +0.64. Real but small — and DOWN-leg entries are ALSO positive (+0.59), so this is a tilt, not a switch. Don't hard-gate; prefer.

## 🔧 Structural facts (design knowledge, not trades)

**6. The grind is the tradable asymmetry, not the spike.** 75% of ALL 2-bar windows net +2.3 pts (median). The mean ≈ 0 only because crashes erase it. Any long-with-stop structure harvests the grind; the entire art is dodging the 25% crash windows — which is exactly what the partial rule + wide stop + cooldown do.

**7. Post-crash reversion ≈ zero in raw terms.** Next-1h/6h/24h after crashes match baseline (−0.2/−2.2/−4.6 vs −0.6/−1.8/−5.7). No guaranteed bounce like boom/crash. The engine's profit is the grind-with-stops, NOT "fade the crash."

**8. No leg persistence → no short-side play.** Crashes don't predict the macro leg (H2). Shorting after crashes, fading up-bars (>3× only 2 exist; >2× n=376, WR 33%), reversal-timer entries — all dead or untradeable-thin.

**9. Crash bursts cluster in time, not predictably.** Skip-after-crash gates HURT (+0.64 → +0.46 at skip-10): the next crash often IS a good entry. The useful clustering fact is the loss-cooldown version: after a LOSS, skip; after a crash per se, don't.

**10. Vol-of-vol regime is weak.** SL5 engine by rolling-median quartile: 0.57/0.48/0.85/0.71 — higher-vol quartiles slightly better, nothing clean. Not a usable filter.

## 🛡 Contingencies and honest limits

**11. Grind-long is a trap — with honest gap-through it's −7,365 pts.** The naive "harvest the grind, long every bar" shows PF 1.63 until crash bars fill at their true lows; then it loses. The crashes ARE the cost of the grind. Any config that holds long through crash windows pays full price. This is the single most important negative result for this product.

**12. The worst trade is real and defines the month.** −58.6 pts (~3× the 18-pt stop) even in the best config, at 1×-floor it's worse. A few of those per month erase the +0.6-pt/trade edge's profit — sizing must treat the −60-pt tail as the risk unit, not the −18-pt stop. (cd=40's worst is only −24 but n collapses to 574.)

**13. Everything is 59 days / 1.5 macro cycles.** One up-leg (+64%) and one down-leg (−39%). The down-leg half is the honest OOS and it holds (T3 PF 1.27), but a second oscillation regime (sideways grind without crashes, or faster leg-flips) is untested. Paper 2–4 weeks before any size.

**14. Boom side and the 550/750 denominations are out.** VOB400/VOB550 gap structurally harder (median m 1.4–2.2 at 1× floor, p90 8–10×) — excluded by gap. The 750s' 32–35× max gaps disqualify them outright. Vol over Crash 400 is the only leg of this family that clears.

**15. The paper-test protocol is the product's only path to live.** Config: crash-fade >3× (tilt toward 3–5×), SL = max(med,floor)×5 (~18 pts), TP = 2×SL, partial add-B, cluster, **cd=10 bars after loss**, optional 12h-up tilt. Expected: avg ≥ +0.7 pts/trade, PF ~1.45, ~30 closes, realized worst-gap ≤ 2× stop, all in demo. Targets in `docs/VOLOVER_LEGCHECK_VERDICT.md`.

---

## Bottom line

Fifteen concepts tested on this oscillator. The verdict is unusually clean: **this product has ONE edge family** — the wide-stop partial crash-fade harvesting the 75%-grind — and it is **already near-optimal in its base form**. The only genuine refinements found are **cd=10 bar cooldown** (+0.64→+0.74) and **tilting toward 3–5× crashes**. Everything else — burst-skip gates, entry delay, up-bar fade, grind-long, vol-of-vol filters, reversal timers — measured dead, thin, or negative. The margin for "strategy discovery" on Vol over Crash 400 is small; the margin for **exit discipline and sizing** is where its profitability actually lives.
