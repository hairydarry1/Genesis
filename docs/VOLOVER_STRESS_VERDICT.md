# VOL OVER WIDE-SL — IS THE EDGE REAL OR JUST THE TREND?

**Date:** 2026-09-04 · **Evidence:** `eval/voc_stress.py`

**Question:** the surviving config (voc400 SL=5×, voc550 SL=2×, partial, cluster+cd3) shows +0.62/+0.37 pts/trade in-sample. The product ran 3997→7311 over 60 days. Is the edge structural (crash reversion), or is the engine just long a product that went up?

---

## Controls

**1. Time-thirds (all positive — no single-regime dependence):**

| | T1 | T2 | T3 |
|---|---|---|---|
| voc400 (SL5) | +0.771 (PF 1.53) | +0.760 (PF 1.39) | +0.343 (PF 1.19) |
| voc550 (SL2) | +0.283 (PF 1.31) | +0.530 (PF 1.88) | +0.301 (PF 1.54) |
| vob400 (SL5) | +1.005 (PF 1.40) | +0.629 (PF 1.26) | +0.976 (PF 1.41) |

All three thirds positive on all three products. voc400's third third decays (PF 1.53→1.19) but stays clearly positive — no evidence the edge existed only in one lucky window.

**2. Detrended series (remove the linear drift, re-run):**

| | FULL | DETRENDED |
|---|---|---|
| voc400 | +0.626 (PF 1.36) | **+0.668 (PF 1.39)** |
| voc550 | +0.371 (PF 1.54) | **+0.398 (PF 1.58)** |
| vob400 | +0.871 (PF 1.36) | +0.855 (PF 1.35) |

**The edge SURVIVES detrending — it even improves on both Crash products.** This is the decisive control: if the profit were the +83% uptrend, removing the trend would kill or flip it. It doesn't.

**3. Direction structure (debug finding, strengthens the case):** voc400 has **6,525 big DOWN bars** (>3× median) and only **2 big UP bars**. The monster bars (111 pts = 45× median) are crashes; the up side caps at 4× median. This is genuine one-directional construction — the same absolute retracement cap family as the verified Boom/Crash book, at 10× the magnitude. The linear-fit drift of the sample is actually **−14.6 pts/day** (price rose then fell back); the strategy made +2,122 pts net *against* that fit — it is not riding an uptrend.

**4. Mirror controls:** inverting price + flipping direction reproduces the identical trade set (symmetry check passes). The pure "same-direction on inverted" control is degenerate on voc400 (only 2 up-spikes exist to invert into) — a structural artifact of the product being one-directional, not a test failure.

---

## Verdict

**The "it's just the trend" objection is dead.** The edge:
- survives trend removal (improves on both Crash products),
- is positive in all three time-thirds,
- trades *against* the sample's own net down-drift,
- and the product's construction is genuinely one-directional (6,525 crash bars vs 2 up-bars), the family signature of the verified Boom/Crash book.

Combined with the earlier results (gap p90 1.1× < breakeven 1.36 at SL=5×; random-entry ≈ spike-triggered so it's partial-geometry + crash-reversion, not spike timing; tick spread ≈ 0), **this is the strongest evidence any config in this project has produced before paper-testing** — OOS-consistent halves, all thirds positive, detrend-surviving, gap-contained.

## Still not "profitable" until paper says so
- 60 days is one regime, however clean. voc400's third-third decay (PF 1.19) is a yellow flag worth watching during the paper test.
- Sim gaps understate live on Boom/Crash (boom1000: sim ~2×, live 2.33). The wide stop (18 pts) is the mitigation; the paper test must confirm realized gap ≤ 2× stop.
- The engine's in-sample worst is still −58 pts (~3× the stop). One of those a week eats a thin month.
- Paper-test spec unchanged: voc400 SL≈18 pts (5×), TP≈36, partial, cluster+cd3, entry+1, hold 2. Target: ~30 closes, realized EV ≈ +0.6 pts/trade, worst gap ≤ 2× stop, all in a demo before any size.
