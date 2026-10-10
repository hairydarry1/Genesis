# ICT Swing-TP Test — Verdict

Video claim: take profit at the **prior swing high** (buys) / low (sells) instead of
2×SL — "don't take it any further."
Evidence: `eval/ict_swingtp_test.py` + M1-swing variant. Identical setup to the gated
pullback test (M15 BOS gate + ≥50% retrace entry + partial engine, gap-through fills)
— **only the TP level changed**.

## Result: the video's TP rule is dead on these products

Two interpretations of "prior swing", both measured, both lose to 2×SL:

### 1. M15 swing (the video's literal rule) — catastrophic

The M15 swing high/low is **58–337 points away** from entry (median ≈ 77 on boom500,
337 on crash300 — i.e. the top/bottom of an entire multi-hour leg). A TP that far
away is never reached inside any hold; the trade resolves as SL-hit or timeout-close
instead. Every symbol, every hold (3/5/7):

| Symbol | 2×SL avg | M15-swing TP avg | PF |
|---|---|---|---|
| crash300 | +0.79 | **−3.99** | 0.26 |
| boom500 | +0.81 | **−8.61** | 0.11 |
| crash500 | +0.21 | **−17.5** | 0.02 |
| boom1000 | +1.73 | **−27.3** | 0.04 |
| crash1000 | +0.42 | **−45.9** | 0.01 |

Mechanism: with no reachable TP, trades that would have banked +2×SL early now stay
open and hit SL instead — WR collapses (73%→58% crash300) and the averages go
deeply negative. Random controls confirm it's the TP rule, not the entries
(random+M15-swing: −5.1 to −44.7 avg).

### 2. M1 swing (most charitable interpretation) — equal to slightly worse than 2×SL

The most recent confirmed 1-min pivot (3-bar swing) before entry sits at a
**median 3.4–4.2 pts** — the same order of magnitude as 2×SL (≈4.9 pts on crash300).
So this version is just 2×SL with extra steps:

| Symbol | 2×SL avg (PF) | M1-swing avg (PF) | OOS |
|---|---|---|---|
| crash300 | +0.79 (1.72) | +0.66 (1.63) | +0.43 / +0.95 |
| boom500 | +0.81 (2.59) | +0.60 (2.03) | +0.75 / +0.42 |

Positive, OOS-consistent — but **strictly worse than 2×SL on both**. The swing TP
is tighter on crash300 (4.19 vs 4.86 → smaller wins) and farther on boom500
(3.37 vs ~2.0 → exposes the trade longer).

## Verdict

The video's "TP at the prior swing" rule is **dead on Boom/Crash**, in both the
literal (M15) and charitable (M1) readings:

- The **M15 swing is the top of a multi-hour leg** — on these products a drift leg
  between discharges rarely survives that long, so the TP acts as "no TP at all"
  and every win turns into an SL. It only worked in the video because of cherry-picked
  examples where the whole leg happened to play out.
- The **M1 swing is just 2×SL with rounding**, and slightly worse at it.

**The video's regime gate and pullback entry survive; its TP rule does not.** The
2×SL + partial-engine exit stays — it's already the better version of the video's
idea. If anything, the sweep says the *opposite* of the video's advice on exits:
tighter is better (2×SL > M1-swing > M15-swing), consistent with everything else
measured on this book (hold=3 > hold=5 > hold=7 on the gated pullback config).