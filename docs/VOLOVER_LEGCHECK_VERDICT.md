# VOL OVER CRASH 400 — LEG-CHECK: DOES THE EDGE SURVIVE THE DOWN-LEG?

**Date:** 2026-09-04 · **Evidence:** `eval/voc_legcheck.py` (clean, masks verified)

**Context:** the sample is ~1.5 macro cycles (up +64% then down −39%). The 12h-gate claim in `docs/VOLOVER_REGIME_VERDICT.md` was computed with the up-leg dominant. This test splits the config every honest way to see what survives.

---

## A) Chronological thirds (the direct survival test)

**Gated (12h-up, SL5×):** T1 +0.853 (PF 1.60) · T2 +0.980 (PF 1.54) · **T3 +0.452 (PF 1.27)**

**Ungated (SL5×):** T1 +0.771 (PF 1.53) · T2 +0.760 (PF 1.39) · **T3 +0.343 (PF 1.19)**

**The edge survives every third of the sample — including the −39% down-leg that dominates the final third.** T3 (days 40–59, the deep drawdown leg) is the weakest but stays clearly positive (PF 1.27 gated). No third is negative or flat. This is the core answer: **the config does not die in the down-leg.**

## B) Macro-leg at entry — the gate is real but modest, and my earlier framing was inflated

| Entries | n | avg | PF |
|---|---|---|---|
| **UP-leg only** (gate) | 1731 | **+0.773** | **1.48** |
| DOWN-leg only | 1722 | +0.593 | 1.33 |
| Ungated (both) | 3392 | +0.626 | 1.36 |

**Correction to the previous doc:** the earlier "no gate +0.640 → 12h-gate +0.856" comparison was *subset noise*, not gate power. Restricting to UP-leg bars gives +0.773, not +0.856 (the previous run's number came from a different internal config — this clean run is the truth). The gate's real contribution: **+0.773 vs +0.626 = +0.15 pts/trade (~24%)**, from taking fewer, better entries (1731 vs 3392). Real, but a refinement — not a different strategy.

**Important nuance:** DOWN-leg entries are also positive (+0.593). Fading crashes works in BOTH legs — better in up-legs (the crash is a dip in a grind) but still profitable in down-legs (short grinds exist inside down-legs too, and the wide-stop partial still harvests them). The oscillator doesn't have a "never trade this side" rule; it has a "prefer this side" tilt.

## C) Gate-window sweep — "12h" is NOT a knife-edge, but it's not magic either

Up-gated: 3h +0.711 · 6h +0.580 · **12h +0.773** · 24h +0.633 · 48h +0.758
Down-gated: 6h +0.706 · 12h +0.593 · 24h +0.549

12h and 48h up-gates are the best; everything is within +0.55 to +0.77. **No window collapses the edge** — robust to the parameter. The up-gate beats the down-gate at 12h/24h but loses at 6h, so the direction-tilt is mild and window-dependent. Treat the gate as "skip the entries right after the macro turns down for a few hours," not as a precise instrument.

## D) SL robustness

SL3× +0.756 · **SL5× +0.773** · SL8× +0.815 (all 12h-up gated). Monotonic improvement with width — the wider the stop, the better, because the crash tail is what hurts and width absorbs it. No knife-edge on SL either.

---

## Verdict

**The config survives every split the sample allows:**
- Positive in all three chronological thirds, including the −39% down-leg third (T3 PF 1.27–1.19).
- Positive in both macro legs (up +0.773, down +0.593).
- Robust to gate window (no collapse from 3h–48h) and SL width (3×–8× monotone).

**Corrected claims vs the previous doc:**
1. The gate adds **+0.15 pts/trade (~24%)**, not +0.22 — still worth having, less heroic than first reported.
2. The oscillator does not require a hard side-filter; the wide-stop partial works in both legs. The gate is a tilt, not a switch.

**Paper-test spec (final):** LONG after >3×-median crash bars, 12h-slope-up gate (tilt, not hard requirement — DOWN-leg entries are also profitable so a soft gate or no gate is fine), SL = max(median,floor)×5 (~18 pts), TP = 2×SL, partial add-B, cluster+cd3. Expected avg ≈ **+0.6 to +0.8 pts/trade**, PF ~1.4–1.5, worst single trade ~−58 pts (3× stop — size for it).

**Honest limits:** 59 days is still one-and-a-half cycles; the tail (−58 pts worst) is real and a few of those define the month; sim gaps understate live. Paper-test before any size, exactly as before — but the structural objection ("it dies in a down-leg") is now answered: it doesn't.
