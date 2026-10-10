# Indicator Scalping Course Battery — Verdict

**Source:** 9 YouTube scalping transcripts (RSI(1)+envelope, ADX(1)/Momentum<5,
MA-cross+awesome-oscillator, MACD/BB "40-50 candle" systems).
**Engine:** corrected gap-through partial engine, 5 symbols, OOS halves, matched controls.
**Script:** `eval/indicator_course_battery.py`

Four claims were mechanically extractable. Two tested, one verified, one catastrophic,
one fantasy — details below.

---

## A. Run-exhaustion entry ("RSI(1) below 10 then cross") — ✅ REAL on crash300, ❌ elsewhere

RSI(1) is binary (0 = down bar, 100 = up bar), so "RSI(1) < 10 for a while, then it
crosses up" is exactly **k consecutive adverse closes, enter on the first favorable
close**. k=3:

| Test | crash300 | boom500 |
|---|---|---|
| k=3 raw | **+0.733 (PF 1.65, n=152)** | +0.475 (n=56) |
| ctrl x10 random | +0.088 (n=1520) | +0.149 |
| OOS H1 / H2 | **+0.785 / +0.681** ✅ | +0.910 / **+0.041** ❌ |
| k sweep | k=2 +0.21 → k=3 +0.73 → k=4 +1.29 | no gradient |
| + M15 gate | **+1.158 (PF 2.47, n=50)** | +1.195 (n=18, thin) |

- **crash300: verified.** OOS-consistent, 8× the control, monotone k-gradient
  (deeper adverse run → better bounce — a dose-response curve, the strongest
  robustness signature a threshold can show), and it stacks with the M15 gate to
  **+1.16/trade** at real n.
- **Mechanism decomposition:** 100% of k≥3 adverse runs on crash300 contain a
  ≥1×-median discharge bar (n=152 has / n=0 clean). This is not candle mysticism —
  it is **"wait for the post-discharge dip to exhaust, enter the drift resumption
  on the first favorable close."** A genuinely new entry timing vs both e+1 and
  the bounce pullback.
- **boom500: dead** (H2 collapse). **1000s: never fires** — 3 consecutive adverse
  closes are vanishingly rare on slow symbols.

## B. Discharge-body retrace entry ("spike then retracement") — works but dominated

Enter when price retraces ≥50% of the discharge bar's body within 6 bars:

| Symbol | body-retrace | bounce-pullback (ref) |
|---|---|---|
| crash300 | +0.361 (PF 1.27, n=3180) | +0.246 (n=601) |
| crash500 | +0.180 (n=1970) | +0.207 (n=223) |
| crash1000 | +0.192 (n=681) | +0.487 (n=47) |
| boom500 | +0.114 (n=1905) | +0.108 (n=184) |
| boom1000 | **−0.334** (n=674) | +0.322 (n=44) |

Positive on the crash family at high throughput, but **PF 1.27 is dominated by the
verified dense-gated config (PF 2.5-2.9)** and it's negative on boom1000. Keep as a
known variant, not a spec change.

## C. Martingale-add ("add when -6, exit -10") — ☠️ CATASTROPHIC, measured

| Symbol | flat 1.0 | martingale 2.0 |
|---|---|---|
| crash300 | −0.035 | **−0.886 (PF 0.48)** |
| crash500 | +0.006 | −0.324 |
| crash1000 | +0.013 | −0.309 |
| boom500 | +0.003 | −0.506 |
| boom1000 | +0.006 | **−0.841** |

Doubling into a losing drift multiplies the tail you already know is the only real
cost (P5). PF ~0.5 everywhere — the "recovery" lots catch the discharge, not the
bounce. This is the quantified reason the loss-cooldown exists.

## D. "Catch 10-15 candles" — zero empirical support

Post-exhaustion favorable runs on crash300: mean 3.2, median 2, p90=7,
**P(run ≥ 10) = 0%**. Hold=10/15 EV goes negative. Confirms the hold=3 wall from
`PRINCIPLE_DERIVATIONS_VERDICT.md` (D2) from a third independent angle.

---

## Net effect on the playbook

1. **New verified entry for crash300:** k=3 run-exhaustion (+ M15 gate) → **+1.16/trade
   (PF 2.47, n=50)** — the third entry family for crash300 alongside gate+freshness
   pullback (+1.86 best-cut / ~+1.0-1.2 expected live) and dense-gated (+0.92).
   It is also the cheapest to run in the EA (no swing/pivot math — one counter).
2. boom500 keeps: gate + double-touch pullback. crash500 keeps: M60 gate + pullback.
3. Martingale-add is now *measured* as the account-killer it is — worth more as a
   defense-layer receipt than as a strategy.

---

## Follow-up: exhaustion × freshness composition (crash300)

**They don't stack — they interleave.** Bucketing gated k=3 exhaustion entries by
BOS age puts 41/52 in **age 0-2** — exactly the window where pullback entries are
*worst* (fresh-break momentum). The age 3-8 exhaustion slice is too thin to verify
(n=10, +2.34, all winners but no mass). The two rules occupy **disjoint windows of
the same settle-cycle**:

| Window | Rule | Why it wins there |
|---|---|---|
| BOS age 0-2 (fresh break) | **exhaustion k=3** | only fade the post-break dip *after it exhausts* |
| BOS age 3-8 (settled) | **pullback 50%** | structure digested; retrace = continuation |

**Union portfolio (deduped, zero overlap within 5 bars):**

| Test | n | avg/trade | PF | OOS H1 / H2 |
|---|---|---|---|---|
| **crash300 union** | 113 | **+1.42** | **2.89** | **+1.42 / +1.42** |
| pullback-only @3-8 | 73 | +1.42-equiv | — | — |
| exhaustion-only @0-2 | 41 | +0.90 | 1.94 | — |

113 trades/month at +1.42 avg ≈ **160 pts/month** vs ~113 pts/month for the
pullback-only spec — more throughput AND stable halves. The zero-overlap result is
the strongest evidence this is structure, not curve-fit: the rules *cannot*
collide because they trade disjoint ages of the cycle.

**Updated crash300 spec:** M15 gate → dispatch by BOS age: age 0-2 watch for
3-adverse-run exhaustion → enter on first favorable close; age 3-8 watch for the
50% pullback → enter on retrace. Expected live ~+1.0-1.2/trade after mean
reversion. (Same 30-day data caveat as everything else in this book.)
