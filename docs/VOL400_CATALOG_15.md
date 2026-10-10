# VOL400 CATALOG — 15 concrete edges, concepts & contingencies

**Date:** 2026-09-03 · **Market:** Spot Up - Volatility Up Index (over-400 vol product)
**Data:** 86,392 M1 candles (60d) + 860,911 ticks · floor 10.00 · spread 0
**Evidence:** `eval/vol400_hunt.py`, `eval/vol400_arsenal.py`, `eval/vol400_deep.py`, `eval/vol400_battery.py`
**Statuses:** ✅ VERIFIED-PROMISING (measured + OOS) · 🔧 STRUCTURAL (mechanism, executable) · 🛡 CONTINGENCY

---

## The core play (read this first)

> **SHORT a ≥2.5× up-discharge at entry+2 (skip the aftershock bar), partial
> entry (half A at close[e+2], add half B at close[e+3] only if A is open &
> profitable), SL = max(median range, 10.00), TP = 2×SL, hold ≤2 bars.**
> Optionally: skip entries when ≥2 bars >2× median fired in the last 20 bars
> (quiet-regime filter), and skip 2 discharges after any loss (cooldown).

Stacked config: **PF 2.90, WR 57.5%, EV +3.44 pts (+0.34R), worst −10.6**.
Trades ≈ 2–3/day. Paper-test before live (see #14).

---

## ✅ VERIFIED-PROMISING (measured + OOS both halves)

**1. The core fade short.** Short ≥2.5× discharge at e+2, partial, hold 2.
n=153 · PF 2.37 · EV +2.94 pts (+0.29R) · WR 56.9% · OOS 2.62 / 2.24 ·
worst −13.4. Strongest per-trade edge in the project (~6× the boom/crash fade).
*Hook:* EA demo mode; entry bar = close of spike+2; skip bar spike+1 entirely.

**2. Discharge-size scaling — the single strongest filter.** ≥3.0× discharges:
n=18 · **PF 6.41 · EV +5.22 pts · WR 72.2% · worst −6.1** (vs 2.5–3.0×: PF 2.15,
worst −13.4). Reversion quality scales with discharge size — big discharges
revert hard and rarely run your stop. *Hook:* tiered sizing — 1× size at
2.5–3.0×, 2× size at ≥3.0× (or run the ≥3.0× subset alone for a slow,
high-quality book).

**3. Quiet-regime filter (executable decaying-hazard).** Skip the short if
≥2 bars >2× median fired in the trailing 20 bars. n=146 · PF 2.57 ·
EV +3.22 · **removes every one of the 7 worst trades** (they all live in the
active regime) · OOS 2.89 / 2.41 — better than baseline in BOTH halves.
Robust to window 10–40 (PF 2.41–2.59). *Hook:* simple rolling count of
`range > 2×median` in the last 20 bars before the signal bar.

**4. Loss cooldown 2.** After any losing trade, skip the next 2 discharges.
PF 2.57, worst −13.4 → **−9.9**. Transfers from boom/crash. *Hook:* same
counter logic already in the EA.

**5. Partial entry = the risk engine (non-negotiable).** Add-B-rule cuts
losers to half size: losses average **−0.50R** (half a stop) vs −1R full,
wins average +0.89R, worst −13.4 vs −19.7 full-lot. This single mechanism is
why the worst trade is only 1.34× the stop. *Hook:* already implemented for
boom/crash — reuse unchanged.

**6. The stacked config (paper-test target).** Quiet filter + cooldown 2 +
partial: n=80 · **PF 2.90 · EV +3.44 · worst −10.6**. With the ≥3.0× tier at
double size: PF 10.90 on n=12 (illustrative only). This is the exact spec for
#14's paper test.

---

## 🔧 STRUCTURAL (mechanism measured, executable)

**7. Entry timing is a knife-edge: e+2 and nothing else.** e+1 fails OOS
(0.54 / 2.36 — the aftershock runs the stop), e+2 works (2.62 / 2.24),
e+3/e+4/e+5 decay (EV +1.25 / +0.82 / +1.81). The decaying tick hazard
(24.7× base at gap 0–50 ticks) defines a ~1–2 bar no-trade window after a
discharge; e+2 sits just past it. *Contingency:* never "improve" the entry
offset — it's already at the optimum.

**8. Reversion is threshold-shaped, not linear.** After ≥2× big bars the
2-bar aftermath is a coin flip (50.8% down, mean −0.09); after ≥3× bars it's
**61% down, mean −7.4 pts**. This is why 2.5×+ thresholds have edge and 2.0×
(and 1.5×: EV +0.10 with n=9,433) is flat. The trade lives at the threshold
where reversion actually bites.

**9. Loss-gap multiplier is structurally bounded here.** Worst measured gap =
1.34× the stop (13.4 vs 10) — versus boom1000's live 2.33×. The symmetric
structure means gaps come from BOTH directions, but the partial halves
exposure before they hit. *Contingency:* the m-breakeven discipline applies —
if live m exceeds ~1.5 (measured by `eval/gap_measure.py`), cut risk.

**10. Zero spread = zero cost drag.** The +0.29R survives intact; contrast
boom500's +0.13/trade edge which died at 0.15 pts of cost. This is the one
Deriv class where a micro-edge can't be killed by friction. *Contingency:*
recheck spread when the symbol relists — if it ever goes nonzero, recompute.

---

## 🛡 CONTINGENCIES (protection & correct behavior)

**11. The long side is dead everywhere — never buy this index for the
"grind".** Long after down-discharge: −12.1 pts. Long after up: −12.0.
Random-entry longs: PF 1.03, worst −67 (the +83% drift is a regime bet, not
an edge — control-verified). Small bars are a literal coin flip (+0.037
pts/bar, P(up)=0.501, AC1 −0.006). The ONLY tradeable side is the short.

**12. Do not add the failed families.** Cluster gates (no effect — 2.5×
discharges are ~384 bars apart), entry-hour filters (noise, sd 4.85 vs
pooled 2.55), lottery/wide-TP (c peaks 0.745 vs 0.750 breakeven, random-
controls identical), chase (0% WR), breakeven stop (PF 1.58), two-tier TP
(PF 1.32), wide stops (PF 1.42–1.53, worst grows to −29.6), anchored TP
(PF 1.58), aftershock-confirmation filter (no gain), M5 (n=20, noise).
Every one measured; none helps.

**13. Risk ceiling: 1% live, 3.4% Kelly cap.** Variance-Kelly ≈ 3.4%; MC at
1% risk: +16.2%/mo mean, worst month −2.4%, P(neg)=0.01. But both-sided gap
risk is structural (unlike boom/crash), and n=153 is thin — 1% until
validated, never more than ~2%.

**14. The only path to live: the paper-test protocol.** ~15–30 trades over
2–4 weeks on a demo chart with the stacked config; targets — realized EV
≥ +2.5 pts/trade, WR ≥ 55%, worst trade ≥ −11, m < 1.5. Pass → live at 1%.
Fail → kill it. (Same 30-close protocol as the boom/crash book.)

**15. Diversification contingency — mirror twin UNAVAILABLE, but the real
"vol over" family was found.** The Down twin (Spot Up - Volatility Down
Index) is **not obtainable from any source on this account** (verified
2026-09-03): MT5 lists it but cannot load it (symbol_info None, 0 candles/ticks
at every depth, visible=False) and it does not exist on the Deriv public API
(89 symbols × 5 landing companies — no match). The literal "Vol over Boom /
Crash 400/550/750 Index" line is equally unavailable on this account
(symbol_select returns −4 "Terminal: Not found") and on the public API.
Those live on Deriv's newer MT5 server group / DTrader — obtainable only via
account entitlement. **What IS obtainable on this account** (all loadable
with full history): the **Crash Boom Flip 150/300/500/1000** family (new
construction, Flip 150 floor 0.517 / Flip 1000 floor 1.878) and Boom/Crash
50/99/100/150/200/600/900 denominations (150s floor only 0.100; 99/100/200
floor ≈22.5–23.0). The Flip line is the closest obtainable proxy for the
mirror/regime test — fetch and battery it before the single-symbol short
book is treated as the whole opportunity.

---

## One-line summary

The over-400 vol product has ONE edge family — the partial e+2 short after
≥2.5× discharges, sharpened by the quiet-regime filter and loss cooldown —
with the strongest per-trade EV in the project, a structurally bounded tail,
and zero spread. Everything else (longs, lottery, timing, gates, exits)
measured dead. Paper-test #6's exact config for 2–4 weeks, then decide.