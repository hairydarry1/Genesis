# VOL400 ARSENAL — the full Boom/Crash battery on the Vol Up index

**Date:** 2026-09-03 · **Data:** 86,392 M1 candles (60d) + 860,911 ticks
**Evidence:** `eval/vol400_arsenal.py`, `eval/vol400_battery.py`, `eval/vol400_deep.py`, `eval/vol400_stress.py`
**Companion:** `docs/VOL400_VERDICT.md` (structure, hazard, product facts)

Every concept family developed for Boom/Crash was re-run on **Spot Up -
Volatility Up Index** (floor 10.00 pts, spread 0, corrected engine accounting,
random-entry controls wherever a direction bias could fake an edge).

---

## Scorecard — what transfers, what dies

| # | Concept (from Boom/Crash) | Verdict on Vol Up | Evidence |
|---|---|---|---|
| 1 | **Partial entry** (half A, add B if profitable) | ✅ **IMPROVES the short** — PF 1.60→**2.37**, worst −19.7→**−13.4**, OOS 2.62 / 2.24 | §1 below |
| 2 | **Fade the discharge, entry+2** (2.5×+ threshold) | ✅ baseline edge — EV +2.1 pts, OOS-consistent (already established) | VOL400_VERDICT §4 |
| 3 | Lottery wide-TP (lose 1R win K·R) | ❌ dead — c peaks 0.745 vs 0.750 breakeven (K=3); random-entry c identical | §2 |
| 4 | Rolling-c regime monitor | ❌ dead — best window c=0.842 vs 0.889 needed; never above | §3 |
| 5 | Cluster gate | ❌ no effect — 2.5× discharges too rare (median gap 384 bars) for bar-level gates | §4 |
| 6 | Entry-hour filter | ❌ noise — hour EV sd 4.85 vs pooled 2.55, n≈6/hour | §5 |
| 7 | Grind / dip LONG (secular uptrend) | ❌ dead — random-entry longs identical (PF 1.03); worst gaps −67 pts | §6 |
| 8 | Drift momentum / AC1 | ❌ dead — AC1 ≈ 0 (small −0.006, big +0.004); small bars are coin flips (+0.037/bar drift) | §7 |
| 9 | Chase (with the spike) | ❌ dead — 0% WR, −12 pts EV (established) | VOL400_VERDICT §4 |
| 10 | Symmetric fade (both directions) | ❌ dead — long leg −12.1 pts | VOL400_VERDICT §4 |

---

## §1. Partial entry — the one transfer that IMPROVES the edge

Baseline (established): SHORT the ≥2.5× discharge at entry+2, SL =
max(median, 10.00), TP = 2×SL, hold ≤2. Adding the Boom/Crash partial
structure (half A at close[e+2], half B joins at close[e+3] only if A is
open and profitable):

| Version | n | WR | PF | EV | worst |
|---|---|---|---|---|---|
| Full-lot short | 153 | 56.9% | 1.60 | +2.08 pts (+0.21R) | −19.7 |
| **Partial short** | 153 | 56.9% | **2.37** | **+2.94 pts (+0.29R)** | **−13.4** |
| Partial OOS1 | 75 | 60.0% | 2.62 | +2.50 | — |
| Partial OOS2 | 78 | 53.8% | 2.24 | +3.37 | — |

**Why it works here even better than on Boom/Crash:** the add rule cuts
losers to half size. Measured trade anatomy (n=153):
- **Losses: mean −4.97 pts (−0.50R)** — the typical loss is half-A stopped,
  B never added (add rate 58%). Almost exactly half a stop.
- **Wins: mean +8.94 pts (+0.89R)** — full position riding to TP/timeout.
- Win/loss size ratio 1.80; WR 56.9% → **variance-Kelly ≈ 3.4% risk**.

The partial structure is the single most robust Boom/Crash invention, and it
transfers perfectly to this product.

### Risk / Monte Carlo (resampling the observed trades, ~55 trades/mo)

| Risk/trade | Monthly mean | sd | worst month | P(neg month) |
|---|---|---|---|---|
| 1% | +16.2% | 7.1% | −2.4% | 0.01 |
| 2% | +32.8% | 14.0% | −8.1% | 0.01 |
| 3% | +49.2% | 20.9% | −12.4% | 0.00 |

**Caveats that matter more than these numbers:** this is in-sample resampling
of the *same* 153 trades — it prices the tail structure, not the risk that the
edge itself vanishes. The measured loss-gap multiplier is **m = 1.34** (worst
13.4 pts vs 10-pt stop) — better than boom1000 (2.33 live) because partial
halves exposure, but the product's symmetric discharges mean both-sided gap
risk is structural.

---

## §2–§7. Killed items (with the receipts)

**§2 Lottery c-curve** (clean race: +1R stop vs −K·R TP, 30-bar limit):
- short side: c peaks **0.745 at K=3** (breakeven 0.750) — a hair short,
  the same razor's-edge as every boom/crash symbol.
- long side: identical peak 0.745.
- **Random-entry controls produce the SAME c** — on this product the
  discharge trigger adds nothing to the lottery; it's pure geometry.

**§3 Rolling c** (6 windows, K=8): best window c=0.842 vs 0.889 needed —
never above, unlike crash300's one w1 spike. No regime to monitor.

**§4 Cluster gate:** all four settings (2+/3+/4+ in 3/5/10 bars) change
nothing — 0 trades skipped. 2.5× discharges are spaced ~384 bars apart
median; the tick-level clustering never reaches bar level at this threshold.
(At 2.0× the gate WOULD matter, but 2.0× has no edge.)

**§5 Entry-hour:** 24 hourly buckets, n≈6 each, EV sd 4.85 vs pooled 2.55.
Same verdict as Boom/Crash: noise. No cold-hour analog exists here.

**§6 Grind/dip LONG** (secular +83% uptrend): LONG after 1 small down bar /
3+ consecutive small down bars, SL 2–3R, hold 5–20 → PF 1.02–1.04. **Random-
entry longs are identical (PF 1.03, EV +0.34–0.46)** — the "edge" is the
sample's drift, not a structure — and the tails are brutal (worst −55 to
−67 pts = 2× stop, both-sided gaps). Regime bet, not an edge. Dead.

**§7 AC1:** small-bar AC1 −0.006, big-bar +0.004 — zero momentum. Small bars
are a literal coin flip (P(up)=0.501) with +0.037 pts/bar drift (which sums
to the +83% secular move). The "grind" is not grindable.

---

## Bottom line for getting into this index

1. **One tradeable structure exists** (the only positive in the whole arsenal):
   **SHORT the ≥2.5× discharge at entry+2, partial entry, TP 2× floor,
   hold ≤2.** Do NOT add a long side, cluster gates, hour filters, or any
   lottery/wide-TP variant — all measured dead or noise.
2. **Strongest per-trade edge in the project** (EV +0.29R vs the fade's
   ~+0.05R) but **thinnest sample** (n=153, one product, one 60-day regime,
   shorts on a doubling index, Down twin untestable). The correct sequence:
   **paper-test first** (≈15–30 trades over 2–4 weeks on a demo chart),
   target realized EV ≈ +2.5–3.0 pts/trade; only then 1% risk live.
3. **Risk discipline:** variance-Kelly says 3.4%; live at **1%** until ~30
   closes confirm the EV and the loss-gap m stays <1.5 (reuse
   `eval/gap_measure.py`'s m framework — the m breakeven here is the ratio
   that keeps EV positive: at 1.34 worst it already is).
4. **Why the index deserves the effort:** unlike boom1000's uncontainable
   tail, the partial structure makes the *typical* loss half a stop, and the
   10-pt floor is only 1× the median bar — so TP at 2× is reachable in the
   reversion that follows big discharges, with the aftershock (bar e+1)
   explicitly avoided. That combination does not exist on any Boom/Crash
   chart.