# 🔍 CHASE-SPIKE VERDICT — entering WITH the spike direction

**Date:** 2026-09-03
**Engine:** corrected M1 engine (`partial_m1_corrected.py`), no lookahead, broker-floor SL, gap-through fills on adverse spike bars, full-sample sweep + OOS halves.
**Question:** what if we trade the spike itself — CRASH: SHORT the dump, BOOM: LONG the pump — instead of fading into the updrift? (User's hypothesis: "stoplosses work normally there" because the SL sits behind the spike.)

## Verdict: DEAD on all five symbols. The fade is correct everywhere.

Best chase config per symbol (full sample → OOS halves). Fade baseline = current verified partial-entry hold=2.

| Symbol | Chase best PF (config) | Chase WR | Chase OOS 1st / 2nd half PF | Fade baseline PF | Chase PnL vs Fade PnL |
|---|---|---|---|---|---|
| crash300 | 0.74 (off=3, hold=1) | 16–31% | 0.72 / 0.76 | **1.30** | −694 vs +1,019 |
| crash500 | 0.49 (off=3, hold=1) | 10–21% | 0.56 / 0.43 | **1.38** | −494 vs +480 |
| crash1000 | 0.41 (off=1, hold=1) | 5–15% | 0.43 / 0.39 | **1.19** | −387 vs +200 |
| boom500 | 0.36 (off=1, hold=1) | 10–16% | 0.34 / 0.38 | **1.24** | −925 vs +507 |
| boom1000 | 0.15 (off=1, hold=2) | 5–7% | 0.17 / 0.14 | **1.39** | −1,396 vs +878 |

Every chase config of the 80 swept (entry offset 0–3 × hold 1–5 × 5 symbols) has PF < 0.75; most are < 0.5. No OOS half rescues any of them. The fade wins 2–9× on PnL on every symbol.

## Why (what the chart actually does after a spike)

- **The spike is a discharge, and the follow-through is the OPPOSITE drift — always.** The dump/pump does not continue; it stalls or retraces. That is the mean-reverting construction of BOOM/CRASH, and it is exactly why the fade system exists.
- **CRASH chase (shorting the dump):** WR 5–30%, and most exits are SL hits — the retracement bounces up through the stop above entry (68–92% of trades). On crash1000 the dump doesn't even bounce; it just *stalls* (95% of 1-bar holds end in timeout at ~ −0.2 avg — no continuation to ride).
- **BOOM chase (longing the pump):** WR 5–16%, SL hit on ~92% of trades. The pump retraces immediately and violently — boom1000 averages **−0.92 pts/trade** at a 1.14-pt stop, i.e. the pump is fully retraced within 1–2 bars.
- **Asymmetry:** boom pumps retrace harder than crash dumps (boom1000 chase avg −0.92 vs crash1000 chase avg −0.21). This independently re-confirms the fade-side ranking: boom1000 is the *strongest* fade (PF 1.39, hardest retracement) and crash1000 the *weakest* (PF 1.19, stalls rather than retraces).

## Implications

1. **User's suspicion confirmed on the 1000s:** crash/boom1000 are indeed the *worst* for chasing (PF 0.41 / 0.15) — but the whole idea dies on the 300/500s too.
2. **"SLs work normally on the chase" is irrelevant** — you don't get run over by the spike, you get run over by the retracement instead, at the same SL distance. The damage just moves to the other side of the trade.
3. **No config change.** The verified fade setup (crash long / boom short, partial entry, gate, per-chart cooldown, Kelly-corrected risk) is the correct side of every spike on every symbol.
4. This closes the last open direction question from the session: **fading is not a preference, it is the structure.**

## Evidence trail

- `eval/chase_spike_test.py` — full sweep (80 configs) + OOS halves, corrected-engine accounting, direction-correct signs.
- Note: an initial sign bug (SHORT PnL computed as exit−entry) briefly made crash-chase look like a monster winner; caught and corrected before any conclusion — the boom side was unaffected and already showed the true picture.

---

# 🔬 PART 2 — What about chasing the OPPOSITE-direction (retracement) bars?

**Question:** the chart also prints opposite-direction bars — on CRASH big UP bars (the bounces), on BOOM big DOWN bars (the pullbacks). Do THEY behave differently when chased (crash: LONG the bounce, boom: SHORT the pullback)?

## Structural finding: retracement bars are SIZE-CAPPED — they never spike

Measured across all five symbols (30 days of M1):

| Symbol | median opposite-bar range | median main-direction bar range | opposite bars > 1.5× ALL-bar median | opposite bars > 1.5× their OWN median |
|---|---|---|---|---|
| crash300 | 0.875 | 4.89 | **0** | 215 |
| crash500 | 0.358 | 3.08 | **0** | 0 |
| crash1000 | 0.343 | 5.64 | **0** | 0 |
| boom500 | 0.551 | 4.64 | **0** | 0 |
| boom1000 | 0.882 | 14.28 | **0** | 0 |

**The spike direction is the only direction that prints large single bars. The retracement is ALWAYS a grind of small bars — recovery is diffusion, never a discharge.** There are no "retracement spikes" to chase; the opposite direction is structurally event-free at bar scale. (Only crash300 has any tail at all — 215 up bars past 1.5× their own class median.)

## Executable test: chase the LARGEST opposite bars (top 2–50% of their class)

Because discrete opposite spikes don't exist, the closest executable analog is entering WITH the strongest opposite bars. Results (corrected engine, SL=floor, TP=2×, OOS halves):

| Symbol | Best config | Full PF | OOS 1st / 2nd half | Verdict |
|---|---|---|---|---|
| crash300 | top-2% bar, +1 bar, hold 1 | 1.04 | 0.76 / 1.24 | dead — inconsistent halves |
| crash500 | median bar, +1, hold 1 | 1.13 | (gate-only 1.28/1.11 was a 275-trade cherry-pick; full sample 0.92–0.97) | dead |
| crash1000 | top-2% bar, +1, hold 1 | 1.44 | 1.57 / **1.03** | dead — 2nd half is break-even |
| boom500 | top-2% bar, 0, hold 1 | 1.33 | 1.31 / 1.43 | **killed on stress tests** |
| boom1000 | median bar, +1, hold 1 | 1.52 | 2.64 / **0.89** | dead — wildly inconsistent |

**The one apparent survivor (boom500, shorting top-2% pullback bars, 1-bar hold) died under proper stress:**
- Time-thirds: PF 1.65 / **1.00** / 1.29 — the middle third is flat dead.
- Cost sensitivity: edge is +0.13 pts/trade and PF collapses with cost — 1.08 at 0.1 pts, **0.95 at 0.15 pts**. Thinner than any realistic round-trip cost.

## Verdict: opposite bars do NOT behave like main spikes — because they are not events

1. **They cannot be chased profitably on any symbol** — every config either dies OOS or is thinner than costs. The behavioral difference from main spikes is not "more continuation", it is *no event structure at all*.
2. **Defensive corollary (genuinely useful):** because the retracement can never arrive as a single large bar, an opposite-direction "spike" alarm can never fire and never gap through the fade's SL. **The fade's stop is only ever at risk from the MAIN direction — which is exactly what the tick-level safety exit watches.** This is structural confirmation that the verified system's protection design (safety exit on adverse main-direction moves) covers the only real gap-through vector.
3. No config change. The verified fade system remains the only executable edge on every symbol.

## Evidence trail

- `eval/chase_opposite_test.py` — percentile-based opposite-bar selection, 32-config sweep × 5 symbols + OOS halves (gate on/off).
- Stress tests for the boom500 candidate (time-thirds, cost sweep) in the session's verification probe.

---

# 🔄 PART 3 — The hybrid "flip" (ride the drift, then flip and wait for the spike)

**Idea (user, CRASH framing):** ride the post-spike updrift for D bars (the existing fade), then FLIP SHORT and hold up to H bars waiting for the next crash spike, SL above so a no-spike outcome is a capped small loss. Rationale: on the spike side the SL "works" (adverse moves are the size-capped drift), and on the 300/500s spikes are frequent.

**Test:** enter at close of bar i+D after every 3× spike, SL = max(median, broker floor) on the adverse side, TP = 2× SL toward the spike, hold ≤ H bars. Sweep D ∈ {1,2,3,5,7} × H ∈ {5,10,20} × drift-OK filter on/off. (Boom mirrored: LONG after the down-drift.)

## Result: dead on all five — and the gap statistics explain exactly why

| Symbol | Median bars to next spike | % of gaps where the drift exceeds the SL distance BEFORE the next spike | Best flip PF (any config) |
|---|---|---|---|
| crash300 | 6 | **63%** | 0.67 |
| crash500 | 8 | **75%** | 0.45 |
| crash1000 | 14 | **82%** | 0.32 |
| boom500 | 8 | **81%** | 0.34 |
| boom1000 | 14 | **90%** | 0.15 |

**The flip dies because the drift outruns the stop before the spike arrives — 63–90% of the time.** You are short while the market grinds up against you for 6–14 bars (median), and the grind exceeds the stop distance in the large majority of gaps. TP hits land only ~20–25% of trades, so even a 2× payoff can't cover 3–4× more SL hits. Raising TP makes it worse (fewer hits). The only entry timing that could fix this is "wait for drift exhaustion" — and every drift-exhaustion predictor (velocity, acceleration, gap-length) was measured and died as lookahead/no-signal. The drift between discharges is a biased random walk that pays the fade and murders the flip.

**"Can we do both?"** Per spike the sequence is fade-then-flip. The fade is verified positive; the flip loses −2,000 to −3,800 pts per symbol over the sample — combining them nets *below* the fade alone. Don't add it.

## Fill mechanics — does the spike register at the level? (answered by the user's own live log)

**No. Spikes gap through levels, and the slip is direction-dependent:**
- Boom 1000, 19:32–19:34 (log): SHORT entry 14464.91, SL 5.23 above. Boom spike fired; the safety exit failed twice (10029, then 10036 — ~0.35 s per retry) and finally filled **22.34 pts through** the stop → **−$55.85** instead of the nominal −$13. The retry latency alone cost ~17 pts.
- Meanwhile the same chart's win an hour earlier (+7.33) was a **timeout**, not a TP — the fade's TP (10.46) never filled because the drift doesn't reach it in the hold window. Crash 1000's +3.98 win was likewise a timeout below its 4.58 TP.

So the fill asymmetry is exactly as feared: **the fade's SL lives in the spike direction (gaps 4–8× against you), and its TP lives in the drift direction (fills at level, often never reached).** That asymmetry is the real mechanism behind "stop loss doesn't work" on fades — and it is also why the flip's SL genuinely fills at level (its adverse direction is the size-capped drift). The flip's SL works as advertised; the flip still loses on EV because the drift kills it before the spike pays.

## The 1-win-1-loss problem (boom1000 +7.33 then −55.85 → −48.52)

One losing trade erased 7.6 wins — the exact MC/Kelly finding from this session: boom1000's average loss is 5.3× its average win, 2% risk there is full Kelly (overbet), and at the measured live gap multiplier (~4.3×: 22.34/5.23) the chart's EV is negative. Two concrete actions:
1. **Risk 1% on the 1000s, not 2%** — halving the boom1000 loss to ~−28 still hurts, but the MC showed sizing cannot rescue a chart whose losses gap 4×; the honest fix is smaller size AND treating the 1000s as unproven until ~30 v2.12 closes per chart are measured by `eval/gap_measure.py`.
2. **The two failed close attempts added ~17 pts** — worth improving close-retry latency (the v2.11+ path already retries; a 5.23 stop on a symbol that spikes 20–40 pts in seconds can't contain the spike regardless — that is the argument for not fading the 1000s at all, which is what the chase-side size-cap finding independently supports).

## Verdict: the money is always in the retracement, never the discharge

Chase-immediate (Part 1): PF 0.15–0.74. Chase-opposite-bars (Part 2): dead / thinner than cost. **Flip-after-drift (Part 3): dead, killed by the 63–90% drift-outruns-stop statistic.** Every entry that bets on the spike direction loses, on every symbol, under every timing — because between discharges the drift grinds against you longer and further than your stop, and nothing predicts the discharge's arrival.

## Evidence trail

- `eval/flip_spike_test.py` — gap-length/drift-over-SL statistics + 90-config flip sweep per symbol.

---

# 🛡️ PART 4 — The hedged fade (keep the long, add a small spike-side hedge when deep)

**Idea (user):** don't flip out of the fade — KEEP the fade position and ADD a small opposite hedge (crash long → add 0.25–0.75 short) only once the drift has run beyond the stop distance (long ≥ +1R). Rationale: the fade's real damage is the single-tick spike gapping the SL 4–8×; a hedge bought at +1R would capture s × the spike drop and cap the tail at (1−s)×D, costing only a bleed while the drift continues (when the long is winning anyway).

**Test:** identical verified selection per symbol (config.py), full-lot main leg with corrected-engine gap fills, hedge opened at the first bar close ≥ +1R (or +1.5R) drift, closed at the main leg's exit bar. Hedge size s ∈ {0.25, 0.5, 0.75}.

## Result: no measurable value on any symbol

| Symbol | Hedge trigger rate | Effect on PnL (s=0.5/0.75) | Effect on PF / DD / worst trade |
|---|---|---|---|
| crash300 | 0–3% | none | none — never fires |
| crash500 | 2% | none | none — never fires |
| crash1000 | 82% | **−3% / −9%** (worse) | DD 74 → 80–83 (worse); worst −22.8 unchanged |
| boom500 | 80% | ~0.0% | unchanged |
| boom1000 | 90% | +1% / +3% | PF 1.23 → 1.24; DD, worst unchanged |

## Why it fails (three independent reasons, all measured)

1. **On the short-hold 300/500s the hedge never fires** — the drift cannot cover 1×SL within the 2-bar hold (0–3% trigger). Dead by construction there.
2. **Where it fires (1000s, 80–90%), it doesn't help because the disasters strike BEFORE the trigger.** The worst trades (crash1000 −22.8) are early spikes — the long is stopped out before the drift ever reaches +1R, so no hedge is in place on exactly the trades that need it. This is the mirror of the flip result: the trigger condition (deep drift) does not select the trades that blow up.
3. **When it does fire, the bleed cancels the capture.** The deep-drift state carries NO extra spike risk (tick hazard is flat — TICK_HAZARD_VERDICT), so the hedge is not buying a higher-probability event; it is paying continuous premium for an event that is no closer. boom500 nets exactly zero; crash1000 nets negative (the continued drift after +1R bleeds more than the eventual spike capture).

boom1000's +3% at s=0.75 is within noise, costs 0.75 extra lots of exposure for +15 pts over 1,587 trades, and would shrink further under the live wider floor (5.23, not the sim's 1.14). Not deployable.

## The accumulating truth (Parts 1–4 of this file)

Every attempt to bet on the spike side or hedge the spike fails for the same three measured reasons, in order:
1. **You cannot know when** — the tick-level hazard is flat (TICK_HAZARD_VERDICT). There is no "imminent".
2. **You cannot wait for it** — the drift outruns any stop before the spike in 63–90% of gaps (Part 3).
3. **You cannot insure only when deep** — the disasters are the *early* spikes, and a deep position carries no extra spike risk to insure against (Part 4).

The only honest mitigations for the one-tick discharge are the ones already in the system: size (Kelly-corrected 1–1.5% on the 1000s), partial entry, gate + cooldown, and live measurement of the real gap multiplier (`eval/gap_measure.py`). No further edge exists on the spike side of this market.

## Evidence trail

- `eval/hedge_flip_test.py` — per-symbol verified-config hedge sweep (size × trigger R), main leg with corrected engine fills.

---

# 🎰 PART 5 — The lottery: lose 1R, win K·R, catch a few spikes

**Idea (user):** don't try to win 100 small trades — risk a small fixed stop per attempt (SL = 1R at the broker floor) and set the TP far out (K·R, e.g. lose $1 / gain $8). A spike is a huge move, so catching two pays for many small stops.

**Test:** chase entries at D bars after every 3× spike, SL = 1R (fills at level — adverse drift bars are size-capped), TP at K·R for K ∈ {2…50}, wait up to 180 bars, per symbol.

## Result: the payoff curve asymptotes to zero from BELOW on every symbol

EV per attempt in R units (negative = losing money per attempt):

| Symbol | K=2 | K=6 | K=10 | K=15 | K=30 | K=50 |
|---|---|---|---|---|---|---|
| crash300 | −0.30 | −0.12 | −0.03 | **±0.00** | +0.08* | +0.09* |
| crash500 | −0.47 | −0.24 | −0.14 | −0.08 | −0.03 | −0.01 |
| boom500 | −0.62 | −0.37 | −0.28 | −0.26 | −0.22 | −0.25 |
| boom1000 | −0.81 | −0.64 | −0.50 | −0.45 | −0.35 | −0.27 |

P(win) at K=15 is only 4–6%; at K=30 it is 1–2.5%. The **reason EV flattens near zero instead of turning positive**: P(reaching K·R) decays as ~c/K with c ≈ 0.9–1.0 on every symbol — *just short* of the 1/K needed to break even. The algorithm's drift-vs-stop calibration sits exactly on the knife edge where the fade is positive and the spike lottery is a hair negative.

## *The apparent +EV at K=30–50 on crash300 is a data artifact — killed by control

Those cells are carried by "timeout" profits averaging +12.7 to +15.8R (3-hour holds drifting in the short's favor). A **random-entry control** (same side, same rules, entries at random bars) makes the same money: crash300 random K=50 = +0.053R vs spike-relative +0.086R; crash500 random K=50 = +0.018R vs spike-relative −0.011R. The residual is the 30-day sample's secular downtrend (crash300 fell ~1900→1850), which leaks into any long-hold short. Not a spike edge — it reverses with the trend.

## The user's own frame, quantified

At K=8–15 you need ~17–33 attempts per two wins (P(win) 6–12%), and the stops cost ~1R each: two 15R wins can't cover ~30 × 1R stops. The measured P(win) ≈ c/K with c < 1 makes the "catch two and quit" plan net-negative on every symbol — before spreads and the fact that live fills during the favorable move still carry slippage.

**Verdict:** the spike-side lottery is structurally just-out-of-reach. No K, D, or hold rescues it; the only positive-looking cells are secular drift. This closes the fifth and final spike-side family: immediate chase (P1), opposite bars (P2), delayed flip (P3), hedged fade (P4), and the wide-TP lottery (P5). The fade remains the only positive-EV side, and its tail is managed by size, partial entry, gate, and cooldown — not by any spike-side addition.

## Evidence trail

- `eval/lottery_chase.py` — K × D lottery sweep, reason splits, and the random-entry control (in the session probe).

---

# ⚖️ PART 6 — The c-curve: exactly how thin the lottery's knife edge is

**Question:** c = P(first-touch of −K·R before +1R) × K. The lottery is +EV iff c > K/(K+1) ≈ 1. Full-history c never exceeded breakeven — but could c ever exceed it in a rolling window (a real favorable-bias regime = the lottery's only chance)? Clean race only (timeouts excluded — no secular-drift artifact).

## Full-sample c-curves (never cross breakeven)

| Symbol | c peak (at K) | peak EV | breakeven c at that K |
|---|---|---|---|
| crash300 | 0.897 (K=12) | −0.028R | 0.923 |
| crash500 | 0.858 (K=20) | −0.099R | 0.952 |
| boom500 | 0.749 (K=30) | −0.226R | 0.968 |
| crash1000 | 0.680 (K=20) | −0.286R | 0.952 |
| boom1000 | 0.642 (K=30) | −0.337R | 0.968 |

**The c-curve is not a constant — it rises to a peak around K=12–20 then collapses** (crash300: 0.59 at K=3 → 0.90 at K=12 → 0.41 at K=30). That's the signature of a discharge with a *characteristic size* (~8–12R on the 500s/300s), not a pure 1/K power law: races that try to catch beyond the typical discharge die fast. The wide-K lottery (K=30–50) is far worse than the K≈12 sweet spot, and even the sweet spot sits below breakeven on every symbol.

## Rolling windows (6 × ~5 days): one genuine c > 1 window — not credible yet

| Symbol | Windows with EV>0 (K=12) | Best c (window) | Adjacent positive pairs |
|---|---|---|---|
| **crash300** | **3/6 (w1 +0.22R, w2 +0.05, w4 +0.07)** | **1.12 (w1)** | 1 (w1–w2) |
| crash500 | 2/6 (w0, w2, ≈+0.06–0.08R) | 1.00 | 0 |
| boom500 | 2/6 (w0, w3, ≈+0.02–0.04R) | 0.96 | 0 |
| crash1000 | 0/6 | 0.86 | 0 |
| boom1000 | 0/6 | 0.71 | 0 |

- **crash300 w1 hit c = 1.12 (EV +0.22R at K=12)** — the first and only measured c > 1. But: the K=8 CI on the best crash300 window spans zero ([−0.125, +0.249]), the positive windows are mostly isolated, and only ~5 days of data back each window. Consistent with noise around a negative mean across the 60 window-cells tested — though the fact that the positives cluster on crash300 (the highest-c symbol) is mild supporting evidence.
- **crash1000 and boom1000 never come close** in any window — the lottery is dead there in every regime observed.

## Operational takeaway: a live monitor is the only way to ever trade this

Because c CAN cross 1 transiently (crash300 w1), the lottery deserves exactly one thing: a **daily rolling monitor**, not a live position. Implemented:
- `brain/c_lottery.py` — measures c, EV, breakeven at K=8 and K=12 over the recent third of history per symbol, verdict: `LOTTERY OPEN` (c > breakeven with ≥100 resolved races) / `WATCH` (c > 0.85) / `DEAD`.
- Wired into the daily brain run: every `*_ai_report.md` now carries the lottery-c line per symbol. Today's state: **all five DEAD** (crash300 c=0.82 at K=12, below its 0.923 breakeven).
- If the monitor ever shows `LOTTERY OPEN` on two consecutive daily runs for crash300/500, THAT is the only condition under which a paper-test of the wide-TP chase is justified — and only then.

## Evidence trail

- `eval/c_analysis.py` — full-sample c-curves + rolling-window c/EV/CIs per symbol.
- `brain/c_lottery.py` — the live daily monitor.