# Boom 150 / Crash 150 — geometry check + the decomposition that reframes the book

**Date:** 2026-09-03 · **Data:** 60d M1 per symbol (86,393 bars), fetched live MT5
**Files:** `data/candles_Boom_150_Index_1min.parquet`, `data/candles_Crash_150_Index_1min.parquet`
**Evidence:** engine run in-session (corrected M1 engine + gate + partial), random-entry controls

---

## 1. The geometry check (what was asked)

| Metric | Boom 150 | Crash 150 | vs Vol Up twin | vs Flip 150 |
|---|---|---|---|---|
| floor | 0.100 | 0.100 | 10.00 | 0.517 |
| median bar | 0.393 | 0.400 | 10.6 | 0.412 |
| **floor/median** | **0.25** | **0.25** | 0.94 | 1.25 |
| direction bias (up/dn bar size) | 3.19 | 0.31 | 1.00 (sym) | 1.00 (sym) |
| opposite bars >1.5× median | **0** | **0** | present | present |
| discharge reversion (2-bar) | 63–64% down | 63% up | 61% (3×+) | coin flip |

The 150s are **classic directional Boom/Crash construction** — the retracement
cap is absolute (ZERO opposite-direction bars over 1.5× median), which is the
structural protection the symmetric products (Vol Up, Flips) lack, and
discharge reversion is strong (63–64%) at every threshold. floor/median =
0.25 means the floor never binds: SL = natural median, geometry is floor-free.

**Corrected-engine fade (partial + gate, hold 2, floor 0.1):**

| | n | WR | PF | avg/trade | avg (R) | OOS 1st/2nd |
|---|---|---|---|---|---|---|
| crash150 | 6,919 | 59.9% | 1.36 | +0.108 | **+0.27R** | 1.39 / 1.33 |
| boom150 | 6,962 | 59.9% | 1.34 | +0.102 | **+0.26R** | 1.33 / 1.35 |

PF ≈ the verified crash300 (1.32) / boom500 (1.21) book; per-trade EV in R is
~2× crash300's (+0.27R vs +0.13R); drawdown in points is far lower (14–20 vs
57–70). On the surface: the best new directional candidates yet.

**But the random-entry control kills the spike-timing claim on them:**
trigger EV +0.108 vs random-bar entries +0.107 (z = +0.23, crash150) — the
discharge trigger adds nothing; the +EV is the partial geometry paying out on
this drift structure.

---

## 2. The decomposition (the finding that reframes everything)

Full-lot vs partial × spike-trigger vs random-bar entry, same SL/TP mechanics,
hold 2, on the five book symbols + the two 150s (pts/trade):

| Symbol | FULL trigger | FULL random | PARTIAL trigger | PARTIAL random | timing delta (partial) | partial-add value |
|---|---|---|---|---|---|---|
| crash300 | −0.089 | −0.154 | **+0.295** | +0.281 | +0.013 | **+0.38** |
| crash500 | −0.001 | −0.082 | **+0.159** | +0.093 | **+0.066** | +0.16 |
| boom500 | −0.106 | −0.009 | **+0.174** | +0.233 | −0.059 | **+0.28** |
| boom1000 | — | — | +0.481 | +0.413 | +0.069 | — |
| crash1000 | — | — | +0.107 | +0.150 | −0.043 | — |
| crash150 | −0.037 | −0.031 | +0.110 | +0.109 | +0.001 | **+0.15** |
| boom150 | −0.041 | −0.031 | +0.105 | +0.118 | −0.013 | **+0.15** |

**Three robust conclusions:**

1. **The raw fade (full-lot) is NEGATIVE on every symbol.** Entering after a
   spike with SL = max(med, floor), TP = 2×SL, 2-bar hold and no partial loses
   (or ~breakeven) in this 60-day sample. The "fade the spike" trade by itself
   does not exist.

2. **The partial-entry add-rule is the entire positive edge** (+0.15 to
   +0.44 pts/trade on random entries alone). The rule — enter half, add the
   second half only when the first is open and profitable — converts a losing
   base trade into the project's positive book. The 2R-TP × half-loss-mean
   structure of the add rule is what has been paying, on every chart, in
   every regime tested.

3. **Spike timing adds little or nothing on most symbols.** Real positive
   timing only on crash500 (+0.066 ≈ 4σ vs controls) and boom1000 (+0.069);
   crash300 barely (+0.013, not significant); **negative** on boom500 (−0.059,
   shorting after pumps is worse than random shorts), crash1000, boom150,
   ~zero on crash150. The years of spike-detection work (gate, thresholds,
   hazard, quiet-regime filters) were optimizing the secondary term; the
   primary term was always the exit/management rule.

---

## 3. What this means operationally

- **The EA's live profits were real but mis-attributed.** The v2.1x book
  (partial ON) collects the partial-add geometry. The spike detector supplies
  exposure cadence; on crashes (300/500) it also adds a mild real timing
  benefit, on booms it slightly *hurts* — yet the booms still net positive
  through partial.
- **The 150s are not an edge over random timing either** — but neither are
  most of the book's symbols. Their value is the same as the book's: a
  floor-free, fully capped, high-frequency canvas where the partial geometry
  pays +0.10–0.11 pts (~+0.27R) per trade with very low DD. They are
  *comparable or better* than the currently-traded 300/500s on the metrics
  that matter (EV/R, DD), at ~2× the trade frequency.
- **The honest next experiment:** since the partial-add is the edge and timing
  is mostly decoration, the maximally-simple test is *partial-entry on a
  schedule* (e.g., every bar where the median is warm, skip when SL would be
  floor-bound) vs partial-after-spikes — and whether the mild crash500 timing
  benefit survives real fills. That is the true base rate for the book, and
  it is higher than the spike-fade framing implied.
- **Live implication:** do NOT turn off the spike detector (it sets a sane
  exposure cadence and holds real timing value on crash500/300), but stop
  treating spike-quality filters as the profit engine. The profit engine is
  the partial add-rule, and it is already ON in the live EA.

Verdict on the asked question: the 150s are the cleanest directional canvases
in the family (absolute cap, floor-free, 63% reversion, PF 1.34–1.36
OOS-stable) but derive their profit from the same partial geometry as the
rest of the book — they are a valid *replacement/extension* candidate for the
300/500 charts, not a new kind of edge.
