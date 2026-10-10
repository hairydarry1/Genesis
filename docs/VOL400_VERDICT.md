# VOL400 (over-400 vol products) — Full Verdict

**Date:** 2026-09-03 · **Data:** 86,392 M1 candles (60d) + 860,911 ticks, live MT5 Deriv-Demo
**Evidence scripts:** `data/fetch_vol400.py`, `eval/vol400_battery.py`, `eval/vol400_deep.py`, `eval/vol400_stress.py`

---

## 1. What the product actually is

There is **no "Volatility 400 Index"** on the Deriv MT5 broker. The classic
volatility series tops out at **Volatility 250 (1s)**. The over-400 vol
products are the newer **directional** ones:

| Symbol | digits | spread | stop floor |
|---|---|---|---|
| Spot Up - Volatility Down Index | 2 | 0 | 1000 pts = **10.00** |
| Spot Up - Volatility Up Index | 2 | 0 | 1000 pts = **10.00** |

Notes:
- **Spread = 0** — no cost drag on these symbols. (Cost-sensitivity stress is moot.)
- **Stop floor = 10.00 pts** — the broker-mandated minimum SL/TP distance.
- The **Down** symbol has **no history** on this account (0 candles). Only the
  **Up** version is tradeable/testable here.
- 60-day price path: 5894 → 10879 (**+83%** — massive secular up-grind).

---

## 2. Structure — symmetric, NOT like Boom/Crash

| Metric | Vol Up | Boom/Crash |
|---|---|---|
| Median bar range, up vs down | 10.64 vs 10.63 (**1.0×**) | 5–16× directional asymmetry |
| Bars > 1.5× median, up vs down | 8,378 vs 8,317 (symmetric) | retracements size-**capped** (often ZERO big opposite bars) |
| Discharge direction | coin flip (P(same)=0.49) | forced (crash=down, boom=up) |
| Retracement can gap your SL? | **YES — no cap** | no |

**The two pillars of the Boom/Crash fade edge do not exist here:** there is no
guaranteed one-directional drift and no capped adverse side. SLs can gap from
BOTH directions (worst fills 1.5–2.0× the stop).

---

## 3. Tick hazard — DECAYING (the opposite of Boom/Crash)

P(discharge next tick) bucketed by ticks-since-last-discharge (p99.9 threshold,
867 discharges):

| Gap (ticks) | 0–50 | 100–150 | 300–350 | 800–850 | 1,500+ |
|---|---|---|---|---|---|
| Rate vs base | **24.7×** | 5.2× | 1.9× | 0.8× | 0.2–0.4× |

On Boom/Crash the hazard is **flat** (Poisson — "imminent" doesn't exist).
Here it **decays smoothly from 25× to 0.2×** — classic volatility clustering /
Hawkes-like self-excitation. Big moves come in **aftershocks**, then go quiet.

**But the direction of the aftershock is a coin flip** (P(same)=0.49 at 2×
median) — the clustering is time-only, not directional. And at the M1-bar
level the executable version is weak: P(next bar big) rises only 1.12–1.43×
after recent big bars.

---

## 4. Fade / Chase EV — measured

All configs: SL = max(median, 10.00 floor), TP = 2×SL, corrected engine
(gap-through fills), 60 days.

| Strategy | Result | Verdict |
|---|---|---|
| **Chase** (long after up-discharge) | WR 0%, EV −12.0 pts, every config | dead |
| **Symmetric fade** (short after up + long after down) | PF 0.41, EV −4.4 | dead — long leg is −12.1 |
| **Short after ANY discharge, entry+1** | OOS 0.54 / 2.36 — inconsistent | dead |
| **Short after discharge, entry+2, hold 1–2** | **OOS 1.60/1.52 & 1.53/1.51 — consistent** | **PROMISING** |

### The one survivor: SHORT at e+2 after a ≥2.5× discharge

- **n=153**, WR 57%, PF 1.5–1.6, **EV ≈ +2.1 pts (+0.21R)** per trade
- Time-thirds: +0.79 / +2.18 / +3.41 — all positive, monotonically rising
- Beats **50 random-entry controls** with identical geometry (controls: +0.26 ±
  0.79, max +1.98) → **z = 2.31**
- Bootstrap CI on trigger-vs-random: p5 = −0.11, p50 = +1.83, p95 = +3.88 —
  **marginal** (≈6% chance the edge is zero)
- Threshold response is *sharp*: >1.5× → +0.10 (n=9,433), >2.0× → +0.06
  (n=1,427), >2.5× → +2.08 (n=153), >3.0× → +5.32 (n=18 — too few to trust)

### Why it works (structural mechanism, not data-mining)

1. **Post-discharge reversion scales with size and is threshold-like:**
   after ≥3× discharges the next 2 bars net **−7.4 pts, 61% negative**; at 2×
   it's a coin flip (50.8%). The 2.5×+ threshold is where reversion actually
   bites.
2. **Entry+2 = skip the aftershock.** The decaying tick hazard says big ticks
   cluster in the first ~50–100 ticks after a discharge (≈ bar i+1). Entering
   at close of i+1 gets run over by the aftershock (e+1 configs fail on OOS1);
   entering at close of **i+2** enters after it, when reversion is cleanest.
3. **Spread = 0** — no cost drag to kill a +0.21R/trade edge.

### Why it is NOT verified yet

- n=153, bootstrap CI touches zero, and the sharp threshold jump (2.0×: +0.06
  vs 2.5×: +2.08) is consistent with small-sample multiple-testing.
- 60 days = one regime on one product. The Down twin is untestable (no data).
- The +83% secular up-grind means short-side results could be regime-specific
  (though thirds are positive across the whole window, including the rise).

---

## 5. Bottom line

- **Boom/Crash playbook does NOT transfer**: symmetric discharges, no retracement
  cap, both-sided gap risk. Fading the 1000s' tail problem would be *worse* here
  (10-pt floor, 2× stop gaps both directions).
- **One new genuine lead** (catalog-worthy): *"short the ≥2.5× discharge at
  entry+2, TP 2× floor, hold ≤2"* on the Vol Up index — EV +0.21R, OOS-
  consistent, mechanism explained (threshold reversion + aftershock skip),
  and cost-free. **Marked PROMISING — paper-test before any size.**
- The decaying tick hazard is the only place on Deriv where "right after a big
  move" carries more risk than usual — but direction is unpredictable, so it
  sharpens *exit timing* (don't hold through aftershocks), not entries.

**Suggested next step:** run the e+2 short on a Vol Up demo chart for 2–4 weeks
(≈ 15–30 trades at ~8/day) and compare realized EV vs the +2.1 pts target. If
it holds, it becomes the project's first non-Boom/Crash verified edge.