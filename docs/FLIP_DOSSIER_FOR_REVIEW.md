# Flip 1000 Trading System — Dossier for External Review

**Prepared:** 2026-09-17 · **Period covered:** 2026-08-25 → 2026-09-17
**Instrument:** Crash Boom Flip 1000 Index (Deriv synthetic, CFD on MT5)
**Evidence base:** ~1.3M archived ticks (coverage audited 100.00%), a live
demo-executor ledger (52 completed trips), and ~40 documented studies
(Appendices A–AG in `docs/FLIP_INDEX_MECHANICS.md`).
**Purpose of this document:** hand the whole project to an outside reviewer
for an independent rating. Failures are listed as prominently as successes.

---

## 0. The one-paragraph verdict

A phase-following tick strategy was built for a synthetic RNG index, with a
measured backtest edge of **+$0.18–0.26/trade** and a live *diagnostic* book
that ran 28 days' worth of infrastructure in 7 days — and the book went
**bankrupt ($10 → −$0.18 over 52 trips)** before the confirmation gate. The
losses came from documented, individually-addressable causes: a vol-regime
cluster, execution friction the backtest underestimated, a sleep-window SL
that the executor silently failed to book (a bookkeeping bug, since fixed),
and the deliberate choice to run the *diagnostic* config rather than the
improved one (to keep the Oct 8 measurement clean). The research output —
the edge's anatomy, the strategy graveyard, the execution measurements, the
integrity fixes — is real and reusable. The live P&L record is a failure.
Both statements are in scope for your rating.

---

## 1. Was real money at risk? The precise answer

Yes. If the $10 had been real:

- **With the guards working as designed** ($4 equity floor → halt), the
  account would likely have stopped near **$4** — a $6 loss, not $10.
- **With the code as it actually ran**, the floor never fired because a bug
  reset equity to $10.00 on every session restart (fixed Sep 17). The
  realistic outcome was **losing the entire $10**.
- The final blow chain: grinddown regime loss (−$5.37, Sep 12) → high-vol
  regime cluster Sep 14–17 → an unbooked −$6.00 SL while the PC slept
  (Sep 16) → a −$2.76 close inside the same regime (Sep 17) → **−$0.18**.

The demo account's own balance is a separate sink of broker play-money; the
$10 "model book" was the tracked, mission-relevant equity.

---

## 2. The market, down to the tick

**Official mechanics (Deriv Academy, confirmed against 1.3M measured ticks):**
- Two phases: Crash phase grinds **up**, Boom phase grinds **down**; any
  large move may **flip** the phase (~60% persistence, measured).
- A large move arrives on average **once every N ticks** (N=1000 here →
  ≈86.4/day at the 86,400-tick/day clock — measured, matches spec).
- 24/7, RNG-driven, no external liquidity, no news, no order flow.

**Measured structure the official docs don't tell you (the actual edge basis):**
- **Two tick populations.** Ordinary ticks end near **8× the rolling median**
  |delta|; true generator jumps start near **20×**, with an *empty gap*
  between. Voting on jumps only (the "fence8" discovery, App. AA) removed
  ~all detector noise. Family-wide confirmed on Flip 300/500 (App. AB).
- **Post-jump drift decay.** After a jump, favorable drift is *freshest in
  the first ticks* and decays to ~zero by ~2,000 ticks (P13). This is why
  instant entry harvests longs best, and why *patience on exits* pays but
  *patience on long-entries* costs.
- **Volatility runs in multi-day regimes.** Ordinary-tick scale sat at
  0.0085u for 9 days, then jumped **2.98×** for exactly Sep 14–15 (the two
  bleed days), back to normal, then elevated again Sep 16–17. Any detector
  keyed to a *frozen* scale estimate breaks in a new regime; the live
  executor's **rolling 5,000-tick median** adapts in ~83 minutes (P17/AE).
- **Memorylessness holds.** A striking 17.7× "hour-of-day" density swing was
  decomposed and shown to be an artifact of frozen fences × vol regimes —
  it did not replicate across archive halves (rank corr −0.23).

**Classic TA — orderblocks, support/resistance, MTF (M5/H1) analysis — was
evaluated and rejected.** A synthetic generator has no order flow, no
resting liquidity, no participant memory: S/R levels are artifacts of human
pattern-seeking, and timeframe aggregation cannot add information the tick
stream doesn't already contain. The generator's *own* event structure (jump
classes, phase votes, drift decay) is the only real signal source — which is
what the engine trades.

---

## 3. The edge, precisely

**Rule:** a "phase" is a persistent drift regime. Detect it by counting
same-direction **true jumps** (|delta| > fence × rolling median, fence at
5× live / 8× best-measured; `min_age=1` — one qualifying vote is enough).
Enter in the phase direction; **exit on the first counter-spike** (or the
TIMEBND 5000 patience variant).

**Backtest economics (frozen-era reference):** avg win **+$1.52** vs avg
loss **−$0.09** — a **17:1 win/loss size ratio** at ~38% win rate →
**+$0.235/trade**. Under the live rolling convention, long+short at fixed
0.1 lot: **+$28.5–32.6/wk** depending on config (below).

**Why a counterparty-less RNG market can still be "beaten":** the edge is
not against other traders — it is harvesting the generator's own structure
(regime-persistent drift + jump overshoot) against a fixed spread. The
product is a *phase-detection machine*, and its validity is conditional on
the generator's behavior staying put (contingency: drift monitor → stop).

**Honest caveats a reviewer should weigh first:**
1. The 17:1 ratio depends on **tick-precise counter-spike exits**. This was
   execution-tested (n=30 fills, slippage ≈ $0.00) but only at 0.1 lot on
   demo; it must be re-validated on the funded account.
2. The menu that "won" was chosen **in-sample** on ~14 daily blocks — wide
   confidence intervals. Only the delay-300 result replicated across both
   archive halves; other cells carry that weaker guarantee.
3. Everything is one symbol from one vendor. Vendor changes the RNG, the
   spread, or delists → edge dies (pre-committed: stop, no rebuild-on-the-fly).

---

## 4. Execution reality (measured, not assumed)

| Item | Model | Measured live |
|---|---|---|
| Spread | 0.05u assumed | **0.6261u** (12.5× worse; identical live vs demo) |
| Extra friction | — | **$0.0576/round-trip** (n=30) |
| Slippage | — | ≈ **$0.00** (n=30) |
| $-per-unit | $0.10/u @ 0.1 lot, 1:1000 | identical on live acct |

Execution verdict on record: **GO** — live edge ($0.18–0.26/trade) cleared
friction with margin, at 0.1 lot. At larger size this must be re-measured.
Flip 150, by contrast, is **untradeable**: spread = 27.9× its median tick.

---

## 5. The strategy graveyard (everything tried and killed)

| Idea | Verdict | Evidence |
|---|---|---|
| Take-profit ladder | Every rung ≤ +$12.5/wk vs +$23.7 incumbent | TP study |
| Trailing stops / time stops / confirmation-exits / pyramiding | All lose to counter-spike exit | same family |
| Daily profit aim $3 | **Toxic**: ladder survival 97.5/2.3 → **35.8/61.1** flip/ruin; protects giveback days, never bleed days | App. X2 |
| Quiet-entry r0=3 filter | Worked frozen-median (+$8→+$21/wk); **no-op under rolling** — patch for a disease the live machine never had | App. Y, AE |
| Skip small-spike entries ("it trades the tiniest spikes") | **Smallest band was the best** (WR 67%); skipping reduces net at every threshold | MAG tombstone + P19 |
| Delay-600 entry | Window luck (sign flips between archive halves) | App. AF |
| Drift-sum phase oracle (the doc's own "watch the grind") | **Loses 3×** (+$10.58 vs +$28.83/wk); negative with patience | P16 |
| Grinddown losses predicted from entry-time features | NO — non-monotonic terciles, all filters destroy net | App. I |
| Hour-of-day filter | Artifact (frozen-fence × vol regime) | P17/AE |
| Orderblocks / S&R / multi-timeframe | Rejected — no order flow exists in a synthetic generator | concept review |
| Fence8 *as deployment* | Demoted: LS ladder only 66% flip / 33% ruin (its ~86% fame was a long-side, frozen-era framing) | AE/AF |
| Flip 150 as a venue | Untradeable (spread) | App. AB |
| Manual "close at $2–3 profit" | Kept as a **logged manual override** (MANEXIT, probe-verified), never as an automated TP — TP was already tombstoned | Sep 16 incident log |

**What survived and why** — the live Oct 8 menu (App. AE–AF):

| | Config | LS net/wk | Fixed-0.1 survival | Ladder survival |
|---|---|---|---|---|
| **C** | rolling-fence5 counter + **DELAY300** (pending entry, cancel on opposite jump) | **+$32.60** | **81–83% flip / ~4% ruin** (7 seeds) | 92–93% / 6–7% |
| B | fence8 + TB5000 | +$23.07 | 47.5% / 16.5% | 66% / 33% |
| — | do-nothing incumbent d0 | +$28.54 | 62–65% / 14–16% | — |

DELAY300 is the formalized version of the "wait ~3 bars for confirmation"
idea: a qualifying jump opens a *pending* entry, canceled by an opposite
jump, otherwise filled after 300 ticks. K-response is a coherent hump
(d60 +0.14 → d300 +4.06 → d1200 −13.25 $/wk); d300 replicates in **both**
archive halves (+3.83 / +4.29); it is mainly a **stabilizer** (worst day
−$10.53 → −$7.59; ruin 15% → 4%), not a profit engine. Deployment needs a
pending-entry state machine (~5s cancel-check loop) — **never built**.

---

## 6. Risk management (what existed)

- **Sizing:** fixed 0.1 lot diagnostic (equal-weight keeps the edge estimate
  unbiased). Confirmed branch: anti-martingale ladder 0.1→0.4 lots at
  $15/$30/$50 equity marks — sizing follows *equity*, never losses; losses
  auto-de-size. Long-only ladder survival 90.1% flip / 5.9% ruin (App. W).
- **Hard stops:** 60u server-side SL on every entry (worst case ≈ $6,
  broker-verified); daily stop −$9 (halt-scaling fix flagged mandatory
  before funding); **$4 equity floor → permanent halt**.
- **Operational guards:** demo-only account guard, single-instance lock,
  stale-tick entry suppression (>120s), tick-gap guard (force-close across
  holes, reset phase), deafness watchdog + checkpoint auto-restart,
  AutoTrading-off detection, reconnect logic, orphan adopt, live trading
  gated behind `FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY` + manual
  relaunch (a crash can never silently resume live).
- **Telemetry:** every ledger row stamps entry jump size, activity, 2h
  trend, and minor-count (min_2k) — free evidence for the gate.

**What risk management missed:** the equity floor read a *session-reset*
equity (bug), and the spike-path position sync **silently dropped** any
position the broker had already closed (SL while asleep). Both fixed Sep 17
and production-verified; the incident is reconstructed to the cent
(App. AG). The lesson recorded: *a guard is only as good as the state it
reads at 3am.*

---

## 7. Engineering around the strategy

- **Tick collector** (72h self-healing, 100.00% coverage audited) +
  same-day rescue healer; **gap-masking in every backtest** after a phantom
  +$22.69 hole-trade was caught — no script may trade across a data hole.
- **Daily checkpoint**: liveness, deafness detection, auto-restart via
  ADOPT, digest, review log. Windows Task Scheduler, WakeToRun.
- **Book integrity:** broker deal history is the source of truth; any
  broker-side close books from history (SL/GAP/race paths); equity restores
  from the ledger at startup; manual user closes detected within ~15s and
  booked (MANEXIT).
- **Reconciliation standard:** book, ledger, and broker history must close
  to the cent. Final state: book −$11.44 + probe −$0.06 + pre-ledger
  exploration −$0.56 = **−$12.06 = broker truth, exactly**. Exactly one SL
  deal exists in the whole history (the healed one).

---

## 8. The live record (the part a reviewer will weight most)

| Date | Event |
|---|---|
| Sep 10 | Deploy; double-instance bug → single-instance lock |
| Sep 11 | Execution study n=30: **GO**; spread gate PASS; live-account guard refused correctly (0 leakage); 3 bugs found & fixed |
| Sep 12 | First big loss (−$5.37 grinddown regime, exit rule blameless); 18h sleep wedge → deafness watchdog, power policy, self-healing archive |
| Sep 13–14 | Sleep relapses; stale-entry loophole closed with the gap guard; heal_today pipeline |
| Sep 14–15 | **Vol regime 2.98×**: frozen-engine replay says −$15/−$20 days; live rolling engine lost −$0.06 on Sep 15 — the live machine was naturally immune to the artifact the backtests suffered |
| Sep 16 | Manual closes made safe (MANEXIT); **the −$6.00 SL fired at 18:10Z while the PC slept and was silently dropped** — book overstated by $6.00 from that moment |
| Sep 17 | User's "stoploss hit while asleep" report → incident LI-1: ledger healed (a first attempt created a phantom mispair; caught by verifier, rolled back, re-healed), executor patched at the root, full reconciliation to the cent. Today's −$2.76 close inside the still-elevated regime → **book −$0.18. Bankrupt.** Executor healthy and correctly staying halted. |

**Live vs backtest drift:** stamped live trips averaged **−$0.33** vs
backtest +$0.08 over a small n inside a short-friendly, burst-heavy week
(Sep 17 was the biggest minor-burst day on record: 50.8% of the day's
spikes). The pre-committed side-split settle fired at n=50: long weakness
**not significant** (p=0.163) and the matched-window backtest shows the
same ordering — regime signature, not execution defect. Verdict: no
escalation.

---

## 9. How to rate this — the questions that matter

1. **Is the edge real or curve-fit?** It has a mechanism (jump-class
   structure + drift decay, family-wide), survives a convention re-base
   (rolling), replicates across archive halves for its front-runner cell —
   but the menu was chosen in-sample on ~14 daily blocks. Demand out-of-sample
   confirmation before believing any survival number.
2. **Is the ledger trustworthy?** It wasn't (LI-1). It now reconciles to the
   cent against broker history, with exactly one SL deal in existence.
   Audit the reconciliation, not the narrative.
3. **Does execution survive funding?** n=30 fills said slippage ≈ 0 at
   0.1 lot. That is the *only* execution evidence. At scale, unknown.
4. **What kills it that isn't P&L?** Vendor risk (RNG/spread/symbol
   changes), single-machine operational risk (sleep caused the bankruptcy),
   regime clusters, and the fact that the improved config (C) has **zero
   live trips**.
5. **Was the diagnostic discipline sound?** Debatable by design: the book
   ran the *worse* config on purpose so Oct 8 would measure cleanly — and
   died before Oct 8. A reviewer can reasonably call that a process failure
   even while the measurement hygiene was good.
6. **Would you size this up?** The plan says no — $10 stake, ladder capped
   at 0.4 lots, hard floor, no martingale. The bankrupt book is the strongest
   argument *for* that conservatism.

**Known open defects / unproven items (full honesty):**
- Menu C never traded live; its pending-entry machine was never built.
- Halt-scaling fix (daily stop in raw units) not yet deployed — mandatory
  before any funded ladder.
- Live evidence starved: telemetry gates (n≥8 per bucket, n≥20 book) never
  reached; burst bucket may *never* fill because floods cluster between
  entries.
- Flip 300 diversification is a sketch only (post-$100; needs $50–60 seed;
  min lot 0.1 confirmed).
- The vol-regime *cause* is unknown — regimes are documented, not predicted.

---

## 10. Where this leaves the project

Three honest options, pre-committed to be decided *together*, not in the
codebase:

1. **Reseed** the book as-is and resume the diagnostic — cheapest, but it
   re-runs a config we already believe is inferior (d0 vs DELAY300).
2. **Restart the gate fresh**: build the pending-entry machine + halt-scaling
   fix, deploy the C config, restart a 28-day diagnostic from a new $10.
   Most information per dollar; delays any funding decision.
3. **Stop before real money.** The research record is the asset; the
   bankrupt book is the tuition. Nothing forces a next cycle.

The Oct 8 gate's premise (28 clean diagnostic days) is void either way —
the record now contains a bankruptcy. Whatever is chosen, the standard
stands: no config change without a pre-registered measurement, and no real
money without a gate that has actually run.
