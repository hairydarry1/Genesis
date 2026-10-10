# Crash Boom Flip Indices — Mechanics & the $10→$100 Question (Sep 10, 2026)

**Question:** can a $10 account be flipped to $100 on Deriv's Crash Boom Flip
indices? **Data:** 7 days of ticks (Sep 3–10), ~600K ticks per symbol × 4
symbols (`data/ticks_flip{150,300,500,1000}.npy`), fetched read-only via
`data/fetch_flip_ticks.py`. **Tools:** `analysis/flip_mechanics.py`
(mechanics), `analysis/flip_phase_backtest.py` (backtest + survival).

## 1. Mechanics: the indices are spike-phase machines

A "large move" = tick delta > k × median |tick delta| (per symbol). Findings:

| Property | flip150 | flip300 | flip500 | flip1000 |
|---|---|---|---|---|
| large moves / week | 11,990 | 11,591 | 10,594 | 9,482 |
| alternation rate (P(dir changes)) | 39.6% | 23.7% | 17.1% | **10.6%** |
| phase length p50 / p90 / max | 2 / 5 / 24 | 2 / 11 / 57 | 2 / 16 / 72 | **4 / 25 / 103** |
| inter-move grind matches phase | 77.4% | 87.4% | 90.7% | **94.4%** |
| weekly up/down size ratio | 0.997 | 1.012 | 0.991 | 0.994 |

- **Phases are real.** Alternation 10–40% vs 50% for a coin flip: large moves
  cluster into same-direction runs lasting up to 103 moves (flip1000). Longer
  nominal period ⇒ longer, more persistent phases.
- **The grind follows the phase.** Between large moves, small-tick drift
  points the same way as the active phase 77–94% of the time. Pre-flip
  segments still carry the OLD direction ~60% of the time (the flip arrives
  as a surprise spike, not a slowdown).
- **No weekly drift.** Up/down move-size ratios 0.99–1.01: nothing to exploit
  directionally. This is a **phase-following game, not a prediction game.**

## 2. Strategy: hold with the phase, reverse on a counter-spike

Phase := direction of last large move. Enter at the next tick after the first
large move; close+reverse at the next tick after a counter-spike. Median
calibrated on the first 5,000 ticks, then **frozen**. Spread (500 pts = 0.05
price units on all four) paid once per phase. Fills never use the spike tick
itself. Live contract specs from the terminal: tick_value/tick_size =
$0.0001 per 0.0001 at **0.1 min lot** ⇒ **$0.10 per 1.0 price unit**.

Net result per week at min lot (negative rows omitted from stability work):

| Symbol | mult | phases/wk | WR | NET $/wk | worst phase |
|---|---|---|---|---|---|
| flip150 | all 2.5–5.0 | 1,436–5,963 | 31–49% | **all negative** | — |
| flip300 | 3.0 | 2,850 | 33.3% | +$88.47 | −$5.30 |
| flip300 | 4.0 | 1,367 | 43.7% | +$97.32 | −$13.04 |
| flip500 | 3.5 | 1,358 | 37.1% | **+$51.40** | −$2.90 |
| flip500 | 4.0 | 719 | 44.9% | +$42.76 | −$5.63 |
| flip1000 | 5.0 | 246 | 47.2% | **+$25.73** | −$4.59 |

- **flip150: no edge survives friction.** Its per-phase amplitude (~1× median)
  is too small relative to the fixed 0.05-unit spread.
- **flip300: biggest gross edge, least trustworthy.** H1/H2 splits flip sign
  (mult 3.5: −$47 / +$96). One-week-only evidence of a knife-edge parameter.
- **flip500 mult 3.5–4.0: the balanced choice.** Both halves positive
  (+$17/+$35 and +$20/+$23), long+short both viable (short side carries).
- **flip1000 mult 5.0: smoothest survivor.** Fewest trades, lowest friction
  share, both halves positive (+$15.66 / +$10.07).

## 3. Survival: the edge is real, $10 is still too small

Exposure = one 0.1-lot step per $10 of equity. The **single historical path**
ruined the $10 account on every profitable configuration except flip1000@5.0.
Bootstrap (1,000 iid-resampled walks per start equity — **optimistic**, since
iid resampling erases loss clustering):

| Config | $10: ruin / flip | $20: ruin / flip | $50: ruin / flip |
|---|---|---|---|
| flip300 @3.0 | 100% / 0% | 100% / 0% | 100% / 0.1% |
| flip500 @3.5 | 75% / 8.2% | 64% / 13.9% | 54% / 17.5% |
| flip500 @4.0 | 83% / 6.0% | 70% / 9.5% | 60% / 12.2% |
| flip1000 @5.0 | **46% / 12.1%** | **31% / 18.2%** | **15% / 32.0%** |

Why $10 dies: at 1 step, a single worst phase = −23% (flip1000) to −130%
(flip300@4.0) of the book, and phases arrive every few minutes. The edge's
*expectancy* is positive but its *drawdown path* is incompatible with
capital below roughly 5 worst-phases.

## 4. Verdict on the $10→$100 flip

1. **The Flip indices are the right family** — the only instrument in the
   terminal with measurable structure (spike phases) at $10-compatible
   granularity ($0.41 margin, $0.005 spread). Vol indices: pure noise, no
   edge possible. Boom/Crash: minimum lot too big for $10.
2. **But the honest odds at $10 are a lottery with better odds than the
   casino:** best realistic configuration (flip1000 @ mult 5.0, fixed 0.1
   lot, no step-up until 2×) gives **~12% flip probability with ~46% ruin**
   — and that's the *optimistic* iid number; real clustering makes it worse.
   Raising to $20 nearly doubles the odds (18% flip / 31% ruin).
3. **If attempting anyway:** flip1000 @ 5.0 only (or flip500 @ 3.5),
   fixed 0.1 lot until equity ≥ $20, then steps; hard stop at 50% drawdown;
   expect the median outcome (~50%+) to be a partial loss, not a flip.
4. **Better use of $10:** treat it as tuition — run the phase-follower on
   paper/demo for 4+ weeks (n≥4 weeks of data — this study is n=1 week),
   then decide with $50+ where flip1000@5.0 already shows 32% flip / 15%
   ruin. The edge will still be there; the capital constraint is what
   kills it today.

## 5. Conditional flip hazard: does phase age predict flips? (Sep 10 addendum)

**Tool:** `analysis/flip_conditionality.py`. Question: is P(next move flips)
conditioned on observable state (phase age in moves, time in phase, cumulative
amplitude, previous-phase length)? Compared against the **memoryless null**
(geometric phase lengths → flat hazard at 1/mean-length).

### Hazard by phase age (moves since phase start)

| Config | move #0 | age 2–4 | age ≥5 | memoryless null | verdict |
|---|---|---|---|---|---|
| flip300 @3.0 | **41.4%** | ~14% | ~12% | 19.2% | **strongly age-dependent** |
| flip500 @3.5 | 47.9% | ~38% | ~32% | 44.2% | age-dependent |
| flip300 @4.0 | 48.3% | ~50% | 50% | 49.7% | memoryless |
| flip500 @4.0 | 46.9% | ~48% | 52% | 47.5% | memoryless (n small) |
| flip1000 @5.0 | 39.3% | ~34% | 35% | 37.1% | ≈memoryless (mild dip at age 1–3) |

- **Age carries information only at lower thresholds.** At 3.0–3.5×, a
  phase's first move is far likelier to flip (it's often noise crossing the
  low bar); once a phase survives, hazard drops below the null. At 4.0–5.0×
  every large move is a genuinely big spike and the process is close to
  memoryless — a coin that just re-tosses each time.
- **Cumulative amplitude: no signal.** Hazard is flat across amplitude bins
  (no overshoot/exhaustion effect). Previous-phase length: weak positive
  association with next-phase length (flip300/500), not exploitable at n=1wk.

### Min-age entry gate: backtest (enter only after phase survives k moves)

| Config | k | trades/wk | WR | NET $/wk | maxDD | $10 ruin/flip | hist path |
|---|---|---|---|---|---|---|---|
| flip300 @3.0 | 0 | 2,850 | 33.3% | **+88.47** | $79 | 100% / 0% | ruined |
| flip300 @3.0 | 1 | 1,670 | 42.5% | +32.65 | $75 | 100% / 0% | ruined |
| flip500 @3.5 | 0 | 1,358 | 37.1% | +51.40 | $49 | 75% / 8.2% | ruined |
| flip500 @3.5 | 2 | 389 | 45.2% | +28.57 | **$24** | 36% / 17.3% | ruined |
| flip1000 @5.0 | 0 | 246 | 47.2% | +25.73 | $19 | 46% / 12.1% | **$41, survived** |
| **flip1000 @5.0** | **1** | 148 | **50.0%** | **+34.75** | $15 | **19% / 28.5%** | **$41, survived** |
| flip1000 @5.0 | 2 | 99 | 50.5% | +20.87 | **$11** | 16% / 12.3% | $19, survived |
| flip300 @4.0 | 1–3 | 184–705 | 40–47% | all negative | — | — | — |

**Findings:**
1. **The age gate is a risk transformer, not a PnL multiplier** — except on
   flip1000, where it is both: min_age=1 raises NET (+$34.75 vs +$25.73),
   lifts WR to 50%, and nearly triples the $10 flip odds (28.5% vs 12.1%)
   while cutting ruin (19% vs 46%). The historical (clustering-preserving)
   path survives at $10 with final equity $41.
2. **flip300's edge lives in move #0** — gating entries destroys it
   (+$88 → +$33 → ~$0). Its hazard shape says young phases are riskiest, but
   its profit comes precisely from catching phases at birth. The gate and the
   edge are the same thing; you can't skip the risk and keep the pay.
3. **flip500@3.5 with k=2 is the defensive variant:** NET −44% vs baseline
   but maxDD halves ($24 vs $49) and $10 ruin drops 75%→36%. Still
   hist-path-ruined at $10 though — flip500 remains a $50+ instrument.
4. **k=3+ is too slow everywhere** — too few trades, flip odds collapse.

### Updated recommendation

**flip1000 @ 5.0, min_age=1** replaces the baseline as the primary config:
fixed 0.1 lot, enter only after the phase prints 2 same-direction large
moves, exit on counter-spike. At $10: ~19% ruin / ~28% flip (iid bootstrap,
optimistic) with the historical path surviving; at $50: ~62% flip / ~1% ruin.
Secondary: flip500 @3.5 k=2 for lower drawdown at higher capital.
All n=1-week caveats from §6 still apply — H1/H2 for flip1000@5.0 k=1 is
+$25.99 / +$8.75 (both positive, decelerating).

### Loss profile (Sep 10 forensics, `analysis/flip_loss_forensics.py`)

All net of spread; parity-checked against `simulate_ma`.

| Config | 1 loss = | BE WR → actual | median loss | p90 \|loss\| | worst loss | worst streak | worst day |
|---|---|---|---|---|---|---|---|
| flip1000 @5 k=1 | **0.67 wins** | 40.1% → 49.3% | −$0.80 | −$2.13 | −$4.13 | 2 tr = −$5.90 | −$9.52 |
| flip1000 @5 k=0 | 0.73 wins | 42.3% → 46.7% | −$0.74 | −$2.04 | −$4.59 | 8 tr = −$12.13 | −$2.94 |
| flip500 @3.5 k=2 | 0.70 wins | 41.1% → 45.2% | −$0.59 | −$1.47 | −$3.23 | 5 tr = −$6.83 | −$9.13 |
| flip500 @3.5 k=0 | 0.53 wins | 34.8% → 37.0% | −$0.44 | −$1.34 | −$2.90 | 13 tr = −$9.22 | −$33.90 |
| flip300 @3.0 k=0 | 0.47 wins | 32.0% → 33.2% | −$0.39 | −$2.16 | −$5.30 | 17 tr = −$17.52 | −$36.27 |

The loss *shape* is the opposite of Boom/Crash: no fat single tails (worst
loss = 2.7–4.6× avg win, vs 5–6× on Boom/Crash fades) because the
counter-spike IS the exit — a phase's loss is bounded by its adverse grind.
The danger is **streaks and bad days**: flip1000@k=1 lost $9.52 in one day
(95% of a $10 book) and dipped to $4.89 min equity before finishing at
$40.85. The min-age gate cuts the worst streak from 8 trades/−$12.13 to
2 trades/−$5.90. flip300's +$88/wk hides a 17-loss streak and a −$36 day.
At $10, one bad day — not one bad trade — is the ruin mechanism.

### Execution fragility (Sep 10, the "am I sure" test)

4-week-horizon $10 walks (5,000 sims, flip1000@5.0 k=1): ruined first 22.9%,
**flipped 73.1%**, alive<100 4.0% — *if backtest-exact execution*. Sensitivity
to extra per-phase cost (slippage/latency beyond the next-tick fill model):

| extra cost/trade | net $/wk | flip% | ruin% |
|---|---|---|---|
| $0.000 | +34.75 | 73.8% | 22.1% |
| $0.117 (=50% of edge) | +17.37 | 27.3% | 62.4% |
| **$0.235 (=100% of edge)** | **0.00** | 3.4% | 94.4% |
| $0.352 | −17.37 | 0.1% | 99.9% |

The net edge is only **$0.235/trade on a $1.53 average win (15% margin)**.
Live fills after spike ticks concentrate slippage exactly where this strategy
enters/exits; typical retail latency during spike cascades can move price
more than the entire edge. Conclusion: the $10 flip is robust to the *random*
path (73%) but knife-edge to *execution quality* — the one variable a
backtest cannot measure. Demo-forwarding the exact executor is the only way
to observe the realized slippage term before risking money.

### Challenge results (Sep 10, `analysis/flip_challenge.py`)

Adversarial re-test: spread audit, corrected economics, detection parity,
survival re-run. **Three findings materially changed the verdict.**

**C1. The friction model was wrong for every symbol.** All backtests used the
static `symbol_info.spread` (0.05u). Live tick spreads are fixed Deriv
spreads far higher: flip150 0.17u (3.4×), flip300 **2.88u (57.5×)**,
flip500 **1.02u (20.4×)**, flip1000 0.63u (12.5×).

**C2. Corrected economics — only flip1000 survives.**

| Config | net (model spread) | net (LIVE spread) | verdict |
|---|---|---|---|
| flip1000 @5 k=1 | +$35/wk | **+$27/wk** ($0.182/trade) | **alive, degraded** |
| flip1000 @5 k=0 | +$27/wk | +$13/wk | marginal |
| flip500 @3.5 k=2 | +$32/wk | **−$6/wk** | dead |
| flip500 @3.5 k=0 | +$61/wk | −$71/wk | dead |
| flip300 @3.0 k=0 | +$81/wk | **−$735/wk** | catastrophically dead |

flip300's "biggest edge" was an artifact: 2.9k trades/wk × a 2.88u spread.
Every flip500 config is negative at real friction. The earlier H1/H2
instability warnings were pointing at this.

**C3. Detection methodology validated.** Rolling (executor) vs frozen
(backtest) median: 99.2% spike agreement (658 vs 663), identical 148 trades,
net $26.93 vs $26.22/wk. The live detector matches the study's semantics.

**C4. Survival, corrected (flip1000@5 k=1, $10, 4wk):** ruined 38.1%,
flipped **53.1%**, alive 8.8% (iid, still optimistic); historical path
survives ($22.91 final, min $4.63). The 73% pre-challenge number is
downgraded to ~53% — and spike-time slippage is now measured against a
$0.182/trade remaining edge, so $0.06 of slip eats a third of it.

**Bottom line after the challenge:** one config (flip1000 @5 k=1) remains
tradeable, with roughly coin-flip-plus odds at $10 and a decisive dependence
on the slippage ledger the executor is now accumulating. flip500/flip300
phase edges do not survive real spreads at any account size.

### Daily-loss stop: tested, and the sweet spot is real (Sep 10, `analysis/flip_daily_stop.py`)

Day-block bootstrap (whole UTC days resampled — preserves within-day loss
clustering), flip1000@5 k=1, corrected spread, FIXED 1 step, 4-week horizon:

| daily stop | flip% | ruin% | trades/4wk |
|---|---|---|---|
| none | 52.9% | 33.4% | 302 |
| **−$9** | **57.3%** | **30.1%** | 280 |
| −$8 | 55.9% | 30.9% | 282 |
| −$7 | 43.1% | 39.7% | 256 |
| −$5 | 0.0% | 94.5% | 98 |
| −$3 | 0.1% | 87.1% | 126 |

**Findings:**
1. **Tight stops are poison — worse than no stop.** −$3/−$5 sit inside the
   strategy's normal intraday noise (Sep 3, a +$7 day, dipped −$5.37 before
   recovering): every day gets halted mid-recovery. flip% → 0.
2. **−$8/−$9 improves BOTH axes** (+4.4pp flip, −3.3pp ruin vs no stop).
   The design rule: set the daily stop *between* the deepest intraday dip of
   any positive day (−$5.4 here) and the worst full-day loss (−$10.94 here).
   Inside that window the stop caps catastrophe days without amputating
   recoveries. Historical path confirms: −$9 final $38.17 vs $36.96 no-stop.
3. **Caution:** the −$8/−$9 window is estimated from the same 8 days it's
   tested on (in-sample). The *mechanism* (stop outside positive-day noise,
   inside bad-day total) is the durable insight; the exact dollar figure
   needs more days. Re-estimate from the accumulating tick archive.
4. Fixed 1-step sizing remains mandatory (step-up was +11pp ruin for +1pp
   flip). With fixed sizing + −$9 daily stop: **~57% flip / ~30% ruin at 4
   weeks** — the best measured configuration. 1–3 consecutive losses from a
   fresh $10 still cannot stop-out the account (worst 3-loss streak −$6.76
   vs $0.79 stop-out line); ruin remains a drift phenomenon that the daily
   stop now clips.

## 6. Caveats

- **One week of data.** All edges are single-week; Sep-phase behavior may not
  persist. Half-splits are a weak stability check.
- Bootstrap assumes iid phases — **optimistic**. Historical single-path sim
  (preserves clustering) ruined $10 on all but flip1000@5.0.
- Spread fixed at 500 pts and fills at next-tick bid; real slippage on spike
  fills would reduce all numbers. Swap/commission not modeled.
- Median frozen at tick 5,000; a live executor must recalibrate continuously
  (rolling median) — untested here.
- Deriv can change Flip specs (period, spread) without notice.

## Concept development round (Sep 10, 2026) — analysis/flip_concepts.py

11 rule variants tested on flip1000 @5.0 min_age=1, identical detector/fill/spread
accounting (engine validated by reproducing the incumbent: worst trip -4.19 vs
known -4.18, survival 52.1/26.0 vs 52.9/33.4).

| Concept | trips/wk | WR% | net $/wk | maxDD$ | survival (flip/ruin) | Verdict |
|---|---|---|---|---|---|---|
| baseline (executor) | 147 | 46.9 | 27.65 | 18.00 | 52.1% / 26.0% | incumbent |
| P2/P3 pyramid | 147 | ~46 | 43.0 / 56.1 | 25.4 / 32.6 | 60/35, 57/42 | REJECT - leverage not edge; top3 = 91% of profit (L+P3: 101%) |
| A20/A30 amplitude exit | ~405 | 50.3 | 11.4 | 24.0 | - | REJECT - re-entries pay spread thrice, halves net |
| T10/T20 trail | ~405 | 15.7 | -21 | 31 | - | REJECT - stops inside spike noise, WR collapses |
| TM10/TM30 time stop | 306/212 | ~45 | -10.0 / +2.0 | 23-24 | - | REJECT - cuts the tail that pays |
| L long-only | 73 | 52.1 | 22.66 | **6.47** | **49.5% / 0.0%** | **ADOPT-candidate** |
| S short-only | 74 | 41.9 | 4.99 | 18.7 | 0.4% / 48.8% | REJECT - ruin lives in shorts |

Key findings:
1. Long/short asymmetry is structural this week: longs 52.1% WR vs shorts 41.9%,
   long maxDD 3x smaller, shorts carry the ruin tail (S-only ruin 48.8%).
2. Long-only: give up 2.6pp flip odds (49.5 vs 52.1) to DELETE the ruin tail
   (26.0 -> 0.0% in 2000 day-block sims). On a $10 book ruin is the irreversible
   state -> strict risk-axis improvement.
3. Caveat: n=1 week for the L/S split (73/74 episodes). Multi-week confirmation
   from the growing tick archive (~mid-Oct) before permanent adoption.

### Long-only backtest with daily-stop ladder + runway (Sep 10, 2026) — analysis/flip_longonly_backtest.py

Day-block bootstrap ($10 start, fixed 0.1 lot, $100 target), both variants, stop ladder:

| Variant | stop | flip% | ruin% | trips/4wk |
|---|---|---|---|---|
| baseline (L+S) | none | 49.9 | 31.9 | 286 |
| baseline (L+S) | -$9 | 53.9 | 28.2 | 268 |
| **long-only** | none | 51.2 | **0.0** | 269 |
| **long-only** | -$9 | 50.5 | **0.0** | 269 |

- The -$9 stop never binds for longs: worst long day total is **-$0.59** (baseline
  worst: -$11.08). The stop stays in the executor as a free net; it is short-side
  insurance for a book that no longer shorts.
- Long-only dip profile: deepest intraday dip of any positive long day = -$4.78
  (baseline: -$7.22) - the stop design window (between -5.4 and -10.9) was
  short-side geometry all along.
- Runway curve (long-only, no stop, 2000 sims each): 4wk 51.2%/0.0% ->
  **8wk 100.0%/0.0%** -> 12wk 100%/0%. Ruin 0.0% at every horizon; the 4-week
  "alive" half is slow compounding, not risk - it flips with more runway.
- Honesty: 100% rests on 7 unique historical days (no catastrophic long day in
  sample). A clustered bad long day (-$10 total, 3x the worst observed trip) from
  $10 would still ruin. The risk axis shifts from trading ruin to regime-change
  risk: does the long-phase edge persist? The 4+ week archive re-test (~mid-Oct)
  is the confirmation gate.

### Horizon matrix for deployment (Sep 10, 2026) — flip%/ruin%, day-block, 3000 sims, measured cost

| Variant | 4wk | 6wk | 8wk | 12wk |
|---|---|---|---|---|
| L+S, -$9 stop | 53.2/29.1 | 66.6/29.8 | 70.4/28.8 | 71.1/28.8 |
| long-only, -$9 stop | 51.1/0.0 | 96.1/0.0 | 99.9/0.0 | 100.0/0.0 |

L+S ruin is flat ~29% at every horizon (death happens early or never); its flip odds
saturate ~71%. Long-only ruin is 0% everywhere and flip climbs steeply with runway.
=> At ANY deadline >= 6 weeks, long-only strictly dominates L+S (more flips, no ruin);
L+S's only edge is ~1.5 weeks faster median arrival. Deadline is THE lever for
long-only; it barely matters for L+S.

## Deep probe: loopholes and edges (Sep 10, 2026) - analysis/flip_deep_probe.py

Motivated by Deriv's official Flip description ("each large move may end the phase
or be absorbed"; "large moves cluster or stretch"; "no tick-counting tells you when
the next arrives"). All on flip1000 @5.0, 7.1d, 658 large moves.

### A. ABSORPTION ASYMMETRY - the generator-level find (STRUCTURAL, adopt as belief)
Post-spike drift over H ticks classifies every counter-spike as flip-confirming or
absorbed (phase resumes):
| H ticks | up-spikes absorbed (dn-phase survives) | dn-spikes absorbed (up-phase survives) |
|---|---|---|
| 50 | 59.0% | 63.2% |
| 100 | 58.0% | 60.4% |
| 200 | 55.0% | 60.8% |
| 400 | 51.0% | 58.5% |
Down-spikes (the counter-spikes that attack UP-phases) are absorbed MORE at every
horizon, and the gap WIDENS with horizon (12pp at H=400). UP-PHASES ARE MECHANICALLY
MORE DURABLE THAN DOWN-PHASES. This is the first-principles explanation of the L/S
asymmetry (long WR 52.1% vs short 41.9%, long maxDD 3x smaller) - not a week's luck
of the draw. Long-only case upgraded from "this week's asymmetry" to "structural in
this sample". October pre-registration: absorption gap >= 5pp at H=200 on 4+ weeks.

### B. MICRO-DRIFT - dead
Post-spike continuation peaks at +$0.0086/0.1lot over 100 ticks vs $0.0626 round-trip
spread. Nothing tradeable. (Confirms spikes are NOT local extremes to fade.)

### C. SPIKE-SIZE CLUSTERING - none
Lag-1 Spearman -0.004: sizes independent, heavy tail (means ~1050-1190x tick median).
No size-prediction edge.

### D. TIME HAZARD - memoryless confirmed
Inter-spike CV = 1.01 (Poisson = 1.00), hazard flat across quiet-gates (18.4% -> 18.8%
-> 17.1%, no trend). Deriv's "no tick-counting" claim is TRUE in data. No timing edge
exists; the executor's event-driven entries are already optimal.

### E. HOUR-OF-DAY - suggestive, NOT actionable (pre-registered for October)
$/episode ranges -1.17 (h4, n=3) to +1.20 (h16, n=7): bucket noise swamps signal at
7 samples/hour. DO NOT patch an hour filter on one week. October test: hour-filtered
phase-follow on 4+ weeks (regime: if h1-5 stays negative and h12-20 positive across
weeks, an hour filter is worth ~$3-8/wk of the current edge).

### F. CONFIRMATION-EXIT - catastrophically worse (trap avoided)
Deriv's own advice ("watch whether the drift changes direction") backtested:
| rule | net/wk | WR |
|---|---|---|
| immediate reversal (current) | +$27.65 | 46.9% |
| confirm K=50 ticks | -$68.50 | 36.9% |
| confirm K=150 | -$67.86 | 36.2% |
| confirm K=300 | -$28.09 | 36.1% |
Waiting for drift confirmation surrenders the tick-precision exit: confirmed flips
cost 50-300 ticks of adverse grind before exit; absorbed cases save only the spread.
The current exit rule is optimal in this sample.

### Family-wide absorption probe (Sep 11, 2026) - analysis/flip_absorption_family.py

The absorption asymmetry is NOT a family law. Gap = dnAbsorbed - upAbsorbed
(+ means up-phases tougher to kill), at H=200 on matched 7.1d archives:

| symbol | gap H=50 | gap H=200 | n counter-spikes | significance |
|---|---|---|---|---|
| flip150 | +1.9pp | +2.3pp | ~14,500/side | SOLID pro-long tilt |
| flip300 | +0.1pp | -0.1pp | ~3,000/side | exactly symmetric |
| flip500 | -1.4pp | -2.5pp | ~440/side | weak anti-long (borderline) |
| flip1000 | +4.2pp | +5.8pp | ~206/side | pro-long, borderline at n |

Reading:
1. Each index carries its OWN generator bias, not a shared one. Our flip1000
   pro-long asymmetry is real in its sample but is a property of this symbol's
   parameterization - we are lucky it points the tradeable way, not guaranteed.
   This DOWNGRADES the "mechanistic certainty" of the long-only case back to
   "strong sample evidence + plausible generator bias"; the October flip1000
   confirmation (>=5pp at H=200) remains the gate. Do NOT assume flip150's
   pro-long tilt transfers to a flip150 strategy (its economics are dead:
   -43.55/wk long net of 0.17 spread at min_age=0).
2. Economics re-check (one-sided, min_age=0, live spreads): flip1000 is again
   the ONLY symbol where either side clears costs (long +12.65, short +1.57/wk
   ungated; longs win LESS often but 8x bigger - asymmetric payoff, consistent
   with the absorption gap). flip300/500/150 both sides dead at every gate.
3. Nuance for October: on flip1000 ungated, short WR (50.4%) exceeds long WR
   (39.8%) yet long nets 8x more - long edge lives in the tail (big phases),
   short edge in frequency. The min_age=1 gate (the real config) flips the WR
   ordering (long 52.1 > short 41.9). Tail-dependence argues for patience in
   long phases - another argument against any early exit.

### Slippage verdict SETTLED + reconnect hardening (Sep 11)

**Verdict: GO, settled once at n=30** (pre-registered rule, frozen in
`data/flip_exec/verdict.json`). Measured execution cost $0.0576/RT
(spread gap 0.5761u; slippage ~0 — mean −$0.0000/side over 30 sides,
worst +$0.0269u). Remaining edge $0.1774/trade → 4-week odds at $10:
**flip 50.9% / ruin 41.9%** for the L+S baseline. Long-only remains
gated on the 28-day confirmation (~Oct 8).

**Reconnect finding:** a ~10.7h wifi drop (PC never slept — all three
scheduled tasks fired on schedule) caused a 38K-tick reconnect flush on
Sep 11; the backlog replay drove 2-3 entries on hours-stale ticks
(7-trip whipsaw cluster, −$0.06..−0.09 each, mostly genuine overnight
chop). Fix: **backlog guard** — ticks older than 120s are processed for
exits/median/spread but never open entries; log notes `[BACKLOG]`.
Deployed as PID 24808; adopt-on-restart path verified live in production
(orphan short adopted and closed cleanly across the restart).

### Fixed take-profit ladder: TP rejected — the counter-spike exit IS the adaptive TP (Sep 11)

Question: would a classic fixed TP (close at +X, stand down until the phase
resets) beat the incumbent uncapped counter-spike exit? Tested on the
8-day/687K-tick archive (`analysis/flip_tp_ladder.py`), same detector,
fills, and live spread as flip_concepts:

| TP (price units) | WR% | net $/wk | avgWin | avgLoss | maxDD |
|---|---|---|---|---|---|
| **none (incumbent)** | 48.8 | **+23.69** | 1.53 | -1.13 | 18.00 |
| 0.5u | 44.4 | +5.28 | 0.73 | -0.52 | 16.46 |
| 1.0u | **84.0** | +4.11 | 0.41 | **-1.98** | 16.01 |
| 2.0u | 82.1 | +10.32 | 0.51 | -1.93 | 12.36 |
| 4.0u | 75.9 | +12.02 | 0.65 | -1.70 | 14.49 |
| 8.0u | 63.0 | +12.47 | 0.94 | -1.37 | 18.30 |
| 12.0u | 54.9 | +11.36 | 1.15 | -1.23 | 18.73 |
| 20.0u | 50.6 | +18.14 | 1.38 | -1.15 | 17.95 |
| 40.0u | 48.8 | +19.79 | 1.47 | -1.13 | 18.90 |

Verdict: **no TP beats the incumbent net**; even the loosest (40u) gives up
16%. The mechanism is visible in the ladder: tighter TPs raise WR (1u → 84%)
while avgLoss balloons (-$0.52 → -$1.98) because capped winners can no longer
pay for full-size adverse counter-spikes — the TP trades the fat right tail
for a fatter left tail. Incumbent win sizes: p50=1.6u, p75=2.8u, p90=4.3u,
max 7.4u — TPs below p75 directly amputate real winners. 2u TP cuts maxDD
(12.4 vs 18.0) but at 56% less net — wrong trade for the $10→100 objective,
which needs net velocity. TP joins confirmation-exit, trails, pyramiding,
and time-stops in the rejected-exit graveyard. Caveat recorded: the 8-day
window's baseline survival (flip 50.2 / ruin 27.4) differs from the frozen
7.1-day numbers (50.9/41.9) — day-block bootstrap results move with the
archive window; the settled n=30 verdict remains the frozen reference.

### Strategy batch 2 (Sep 11, 8-day archive) — candidates for post-Oct 8 adoption

Same conventions as all concept tests. Phase structure: up-phase mean run
2.57 spikes (n=138) vs down 2.75 (n=138), ratio 0.94 — run LENGTH is not
the pro-long asymmetry; the tail distribution is (long phases pay, both
directions die young).

| Variant | trips/wk | WR% | net $/wk | maxDD$ | flip%/ruin% ($10, 4wk) |
|---|---|---|---|---|---|
| LS (executor) | 143.1 | 48.8 | +23.69 | 18.00 | 50.2 / 27.4 |
| L long-only | 70.7 | 53.8 | +22.24 | 9.02 | 45.9 / **0.0** |
| S short-only | 72.4 | 43.9 | +1.45 | 18.71 | 0.2 / 61.4 |
| MAG3 / MAG5 strong-entry | 143.1 | 48.8 | +23.69 | 18.00 | identical — spike size carries no info (re-confirms lag-1 rho ≈ 0) |
| ABS absorption re-entry | 173.2 | 45.9 | +17.75 | 27.24 | 24.3 / 30.6 |
| SXC short-cap-2.0u | 195.2 | 52.0 | +14.00 | 20.05 | 21.9 / 38.0 |
| L+ABS combo | 81.3 | 48.9 | +21.91 | 8.94 | 28.3 / 0.1 |

Reads: (1) **MAG is a pure no-op** — spike magnitude is not predictive,
closing that door with data. (2) **ABS and SXC lose net** — the absorption
asymmetry is real but NOT monetizable via faster re-entry (spread tax eats
it), and capping shorts amputates their (rare but real) tail. (3) The
**long-only case strengthens**: same net as L+S at ~half the drawdown and
ZERO ruin in 2,000 sims. (4) L+ABS is interesting-but-fragile: +21.9/wk
with 0.1% ruin, but lower flip odds at $10 than plain L in this sample —
parked, not adopted. Nothing changes before Oct 8; long-only remains the
presumed adoption, ABS re-tested on 28 days.

### Appendix H — The grinddown regime (Sep 12 live loss, forensics)

Trigger: live trade opened long on +1 age-1, phase counter rose to +1 x6 while
price drifted down 52u over 106 min on 6,303 down ticks vs 79 up ticks; exit
on a real -11.26u counter-spike for -$5.37 (model book $13.63 -> $8.26).
Exit logic worked exactly as designed; there was nothing to exit on.

Measured (analysis/flip_grind_forensics.py, 9.7-day archive, live config):
  - One-sided drift windows are COMMON: 24.9% of sliding 60-min windows have
    >=90% of small ticks in one direction. Drift is a regime, not a glitch.
  - Inside the worst drift windows, up-spikes still fire at 0.97 up-share
    (252 up vs 7 down) — the generator prints bounce-spikes against the
    drift, and the phase counter reads them as votes. Detector tracks spike
    direction; PnL tracks drift; they anti-correlate exactly when it hurts.
  - The loss is in-sample: backtest worst = -$5.43 (Sep 10, MAE 63u),
    -$5.37-class occurs ~1/176 trades (~every 10 days at live frequency).
  - Every big backtest loss is drift-dominated: drift share of loss p50=1.01
    (n=37 losses >$1); exit spikes add only -$0.5..-$2.4 more.
  - MAE dist: p50=7.6u, p90=24.7u, p99=51.8u, worst=63.3u. Live 56u = 2nd
    worst of 176. The tail is a different regime from the median trade.

Drift-stop ladder (analysis/flip_drift_stop.py, entry-anchored MAE cap —
distinct from rejected best-price trails):
  LS none   +$39.2/wk  worst -$5.43  maxDD $18.00   (incumbent)
  LS 25u    +$37.5/wk  worst -$2.66  maxDD $16.54   (-4.5% net, tail halved)
  L  none   +$35.2/wk  worst -$3.45  maxDD  $9.02   (Oct 8 presumed config)
  L  25u    +$35.7/wk  worst -$2.56  maxDD  $8.67   (+$0.5/wk, tail cut)
  Winner-damage census @25u: 17/176 trades touched; only 3 would-be wins
  killed; net effect of the rule +$13.71 over 9.7d vs naive expectation.

Status: NOT adopted (frozen for measurement integrity). Any change is a
post-archive decision with the 28-day re-test. If adopted, adopt on the
long-only branch where it is free (+net, lower DD); on LS it costs ~$1.7/wk
for tail insurance — a risk-preference call, not an edge call.

### Appendix I — Can grinddowns be predicted at entry? (Sep 12 study, pre-28d)

Question: is there observable pre-entry state that flags the entries that
become grind losses (like Sep 12 -$5.37)? Method
(analysis/flip_grind_prediction.py): per-trade feature battery at the fill
(60-min one-sided drift share, net drift, 5-min spike divergence/amplitude,
60-min vol regime, time since direction flip), tercile-vs-outcome tables,
biggest-loss signatures, filter backtests (skip bleeding entries), day-block
survival. Structural note: at min_age=1 every entry is at phase age 1, so
only drift/regime features can matter.

Result: NO - grinddowns are not predictable at entry.
  - Terciles are non-monotonic (pct_dn_60m on longs: low -$0.08, mid +$1.08,
    high -$0.28 mean net): mid-bleed entries are the BEST, both extremes
    worse. No threshold exists.
  - The six biggest losses share no signature: two entered during strong
    UP drift (shorts, +24u/+14.5u bleed against them came later), others in
    down-drifts; pct_dn_60m spans 0.15-0.87; all entered 5-min-calm windows,
    which is the NORM for every entry (winners too) because the entry spike
    is the first activity by construction.
  - Every filter destroys value: L skip pct>0.60 +$14.70 -> +$11.71/wk;
    drift filters worse; LS skip pct>0.80 +$35.85 -> +$21.13/wk with HIGHER
    maxDD; L skip 0.70 survival ruin 4.3% -> 14.4% (fewer trips = more
    fragile at $10).
  - Interpretation: the edge is post-spike momentum; the grind reveals
    itself only over the NEXT hour, which no entry-time feature can see.
    Correct defenses are the exit (counter-spike, already optimal), sizing,
    and the entry-anchored drift stop (Appendix H) - not entry filters.

Audit byproduct (important): today's archive contains an unhealable ~20h
hole (Sep 11 20:05 -> Sep 12 16:28; broker tick history depth is only a few
hours). Unmasked backtests produced a PHANTOM +$22.69 trade that "held"
across the hole. All archive numbers quoted after this note are gap-masked
(trades crossing >300s gaps dropped). Masked reference at 9.8d (includes
the Sep 12 loss day; day-block bootstrap on ~10 blocks is volatile):
  LS masked +$19.69/wk  flip 36.7% ruin 29.3%
  L  masked +$14.70/wk  flip  9.1% ruin  4.3%
  L  + 25u stop (masked) +$15.56/wk  flip 10.6% ruin 2.8%  <- stop still free
Pre-registered for Oct 8: re-run flip_grind_prediction.py + drift-stop on
the 28-day archive; adopt the drift stop on long-only if ruin reduction
persists; adopt an entry filter only if terciles turn monotonic (they did
not on 10 days).

### Appendix J — Gap-masking institutionalized (Sep 12)

After the phantom +$22.69 hole-trade was found (Appendix I), gap-masking was
built into every backtest sim (shared helper: analysis/flip_gapmask.py):
  - tick-loop sims: per-tick bad guard — on a fake hole-edge delta, drop any
    open position, reset phase state, skip the tick (concepts, tp_ladder,
    strategy_batch2, drift_stop, grind_forensics, grind_prediction,
    deep_probe)
  - spike-index sims: fake edge-spikes removed from the spike list + any
    position whose entry->close span crosses a hole is force-closed and
    dropped (phase_backtest, conditionality/simulate_ma (opt-in ms=),
    loss_forensics, daily_stop, absorption_family, challenge)
Hole definition: dt > 300s (normal tick cadence 0.5-5s).

Verification (masked vs the independent post-hoc references):
  concepts baseline: 178 trips +$19.81/wk worst -$5.43, survival 36.7/29.3
  concepts long-only: 87 trips +$14.79/wk, survival 9.1/4.3
  drift-stop L+25u: +$15.56/wk worst -$2.56  (matches manual masked run)
  tp ladder / batch2 / daily-stop / challenge verdicts unchanged.
In-sim masking is stricter than post-hoc by one hole-edge entry
(+$0.12/wk) — conservative and correct.

Known engine discrepancy to reconcile at Oct 8: flip_challenge's
simulate_ma path reports ~+$26-27/wk on flip1000@5 k=1 vs flip_concepts'
+$19.81/wk (pre-dates masking; same rankings, both positive). All decision
numbers come from flip_concepts unless stated.

### Appendix J addendum — engine reconciliation RESOLVED (Sep 12)

The flip_challenge vs flip_concepts "~+$27 vs +$19.81" difference was fully
reconciled (analysis/flip_engine_recon.py, kept as a regression check):
  1. Stale /wk label: challenge A2/A3 printed TOTALS over ~1.40wk under a
     "/wk" heading written when the archive was exactly 7 days old. Real
     weekly denominators now applied (post-warmup duration).
  2. Warmup scope: simulate_ma scanned spikes from index 0, taking one
     pre-warmup trade concepts never sees. Opt-in start=WARMUP added.
  3. Phantom double-count: challenge added back a 0.05u model spread that
     simulate_ma never subtracted (+$0.89 total overstatement). Removed.
Result: simulate_ma(masked, start=WARMUP) vs concepts mode=base ->
  n=178 == 178, net $27.64 == $27.64, residual $0.0000 (CONVERGED).
Challenge A2 now prints flip1000@5.0 k=1 = +$19.81/wk, identical to
concepts. The unmasked row in the recon also reconfirms the Appendix I
phantom independently: +$22.69 across exactly +1 hole-spanning trade.
Oct 8: either engine is valid with warmup + masking; flip_concepts stays
the decision engine; recon script should be run after any sim edit.

### Appendix K - "Is the -$9 daily stop too much?" (Sep 12, user question)

Grid: daily halt {none,9,7,6,5,4,3,2.5} x drift stop {none,25u}, LS and
long-only, day-block bootstrap (28d, 2000 sims, $10 fixed lot) +
historical path. Script: analysis/flip_daily_x_drift.py (raw trade lists
verified identical to flip_concepts before trusting).

Results (flip%/ruin%):
  LS   no stop: none 35/36 | -9: 38/32 | -7: 27/42 | -6: 28/41 | -5: 1/74
  LS  +25u stop: none 31/36 | -9: 34/32 | -7: 39/26 | -5: 0/82
  L    no stop: none  9/ 8 | -9..-5: 9/ 8 (halt NEVER fires) | -4: 1/43
  L   +25u stop: none 12/ 5 | -9..-5: 12/ 5 (halt NEVER fires) | -4: 2/37

Conclusions:
  1. Tightening the halt is poison, again: -5 and below amputates recovery
     days (LS ruin 32->74%+; L 8->43%). The old ladder verdict holds even
     with the drift stop truncating per-trade losses.
  2. On long-only the -9 halt is dead weight: it never fired historically
     and almost never in sims (worst L day: -5.37 raw, -4.86 with stop).
     What actually caps the day is the per-trade 25u drift stop.
  3. The halt fires too late by design: a day can still reach ~-$13 on LS
     (two big losses stacking before the halt trips). It is a freak-sequence
     circuit breaker, not a day-loss cap.
  4. Verdict: KEEP halt at -$9 (costs nothing, identical cells) and adopt
     the drift stop - the drift stop is the instrument that actually answers
     "one bad day cannot kill the $10" (per-trade worst $2.56 vs $6 SL).

### Appendix L: The Deriv WebSocket API as an alternative venue (Sep 12, 2026)

**Question:** can the flip strategy run via Deriv's WebSocket API (e.g. on
non-Windows hosts like PythonAnywhere) instead of MT5?

**Tool:** `analysis/deriv_api_recon.py` — read-only probe (ping,
active_symbols, contracts_for, ticks_history, tick stream; optional token for
catalog endpoints). No trade-capable call exists in its vocabulary.

**Findings:**

1. **The public catalog is empty unauthenticated.** `active_symbols` and
   `asset_index` return OK-but-empty to anonymous sessions, so "Flip absent
   from the catalog" is NOT yet categorical. Guessed codes (FLIP1000,
   CRASHBOOMF1000, BOOMF...) are InvalidSymbol. Definitive check needs a Deriv
   API token (read scope) — `python analysis/deriv_api_recon.py <TOKEN>`.
2. **Deriv's own guidance is MT5-only for Flips.** The experts.deriv.com guide
   (Aug 14, 2026) walks through MT5 Market Watch / Market Execution exclusively;
   Deriv Trader / API venues are never mentioned. Contrast: Boom/Crash classics
   (BOOM1000, CRASH500, R_100) all serve public ticks through the API.
3. **The API venue's contract model is fundamentally different:** MULTUP /
   MULTDOWN stakes (loss capped at stake, server-side TP/SL in currency,
   deal-cancellation 5-60m, close-anytime via sell). No lot-based position, no
   bid/short book as we know it — a short is a separate MULTDOWN stake.
4. **Feed parity measured on BOOM1000:** 255 shared epochs between API
   ticks_history and MT5 copy_ticks, price offset 0.0760 with std 0.00026
   (one tick-size). **Same generator process, two quote levels.** The API feed
   is mechanically equivalent to the MT5 feed — deltas, spikes, and phases
   would be identical.

**Verdict:** Flip indices are *effectively MT5-only today* (pending one
token-authenticated catalog check). The silver lining: if Deriv ever lists
Flips on the API venue, the tick process — the thing our entire edge is built
on — is verifiably the same one.

**Re-validation checklist if an API-venue Flip migration is ever attempted:**
  1. Feed: 24h API tick capture -> rerun drift monitor against frozen baselines.
  2. Cost: commission + spread structure per MULTUP/MULTDOWN vs our 0.6261u MT5
     spread — recompute the -$0.5761u/trade live-spread correction from scratch.
  3. Fills: stake PnL vs our per-tick PnL model; stop-out level vs our 60u SL;
     whether the counter-spike exit maps to `sell` at market.
  4. Risk stack: 60u SL -> server-side stop_loss (currency); daily halt port;
     short = separate stake (phase flip = two orders, not one reversal).
  5. Measurement reset: all archive numbers are MT5-venue numbers; the demo
     slippage ledger must start over on the new venue.

### M. User-hypothesis re-test: entry-spike size + retrace-hold exit (Sep 14, analysis/flip_spike_size.py)

Both user observations formalized and tested on the healed 11.4-day masked
archive, engine validated against flip_concepts to 0.5% on identical trips.

**A. "Tiny spikes take losing trades" -> entry-spike magnitude filter.**
corr(entry_x, net): LS -0.058, L +0.066. Terciles non-monotonic (LS mid
tercile is the BEST: +$0.52 vs +$0.17/-0.13). Filter ladder: x_min=5.5 gives
+$27.84/wk vs incumbent +$27.11 (+2.7%, from 3 skipped trades) but the ladder
is non-monotonic (5.25 -> +27.60, 6.0 -> +27.73) = overfit shape. The MAG
tombstone is re-confirmed under masking: **spike size carries no tradeable
information.** The perception is availability bias: at ~50% WR half of all
entries lose, and memory tags the losers that followed unimpressive spikes.

**B. "If the counter-spike leaves us at a loss, hold for a retrace; close if
it keeps going" -> retrace-hold exit family (same entries as incumbent).**

| variant | LS net/wk | L net/wk | notes |
|---|---|---|---|
| incumbent counter-spike | +27.11 | +23.87 | reference |
| retrace-hold pure | +16.95 | **+25.83** | LS: -37%, new -$7.99 tail; L: +8.2% |
| retrace-hold bounded | +25.55 | +24.54 | still <= incumbent both sides |
| incumbent + 25u stop | +25.22 | +23.70 | known drift-stop economics |
| retrace-bounded + 25u stop | +27.15 | +21.96 | no gain, more DD |

Reads: (1) On shorts the rule is **poison** - holding through adverse spikes
into a market that keeps running minted a -$7.99 loss, worse than anything in
the archive (grind-amplification, same mechanism as Appendix F). (2) On
longs the pure retrace-hold is +8.2% - mechanically consistent with the
absorption bounce (down-spikes get faded on this symbol, up-spikes don't),
i.e. the same asymmetry that already justifies long-only. (3) But: avgL
worsens (-1.22 -> -1.63), maxDD +15%, and the entire edge sits in one 11.4-day
sample - not distinguishable from one good week. (4) "Close if it keeps going"
is precisely the 25u entry-anchored drift stop, already the Oct 8 candidate;
the combos with it are strictly worse than either alone.

**Verdict:** hypothesis A closed (second tombstone, now mask-clean).
Hypothesis B: **pre-registered as candidate #2 for the Oct 8 28-day gate**
(long-only, pure retrace-hold, evaluated head-to-head vs incumbent + 25u
stop on 28 clean days). Executor stays frozen for measurement integrity;
no live changes from a one-sample +8%.

### N. Exit-family sweep: TIMEBND found (Sep 14, analysis/flip_exit_families.py)

Five untested exit families motivated by the absorption asymmetry (M-rule's
neighborhood), identical entries/conventions, engine validated to the fleet.
Outcome table (LS net/wk | L net/wk):

| family | result | verdict |
|---|---|---|
| STAGED half-now/next | +22.69 / +21.64 | loses (double spread cost) |
| THICK size-of-attack | +23.63 / +26.67 | long-specific, non-monotonic - park |
| TIMEBND retrace+patience | **+36.49@2000** / **+27.53@2000** | **candidate #3** |
| RATCHET entry-energy trail | negative (fixed mid-run) | tombstone #13 |
| ABSORB bounce re-entry | +25.66 / +25.66 | below incumbent, spread tax |

TIMEBND = user's M-rule refined: hold an adverse counter-spike for a retrace
BUT take the market if no favorable spike fills within K ticks. Patience
surface: 500 -> 1000 -> 2000 -> 3000 -> 5000 ticks = LS +21.7/+28.8/+36.5/
+25.4/+29.7 (2000-peak flagged; 3000 dip shows parameter sensitivity).
Robustness: time-split BOTH halves beat incumbent both halves (LS 2000:
H1 +36.72 / H2 +36.26). Survival ($10, 4wk, day-block):

| config | flip% | ruin% |
|---|---|---|
| LS incumbent | 58.5 | 19.7 |
| LS TIMEBND 2000 | 78.4 | 16.9 |
| L incumbent | 50.6 | 3.4 |
| **L TIMEBND 2000** | **70.7** | **0.2** |
| L TIMEBND 5000 | 73.6 | 2.5 |

Caveats: ~11 daily blocks (volatile bootstrap); 2000/5000 chosen post-hoc
from the grid (selection bias); patience = ~33min (2000) / ~83min (5000) at
60 ticks/min, trivially executor-implementable (tick counter since adverse
spike). Untested at gate: TIMEBND x 25u-stop combo. Executor stays FROZEN.
**Oct 8 menu now: {incumbent+25u stop, TIMEBND 2000, TIMEBND 5000} x
long-only**, decided head-to-head on 28 clean days.

### O. Next-gen: adaptive patience, sessions, and the compounding ladder (Sep 14, analysis/flip_next_gen.py)

**1. TIMEBND x 25u stop combo (Appendix N's open cell): NEGATIVE.**
Stop inside TIMEBND costs -$3.46/wk LS and -$3.65/wk L (34.77->31.31,
27.53->23.88) with NO worst-loss benefit (TIMEBND already amputates the
left tail by taking the market on patience expiry). Two exits, one job -
the drift stop joins the graveyard FOR THE TIMEBND CONFIG (stays valid for
the incumbent config).

**2. Adaptive patience (shrink K as MAE approaches cap): tombstone #14.**
LS +28.46/+24.73 vs flat 34.77; L +21.47/+21.69 vs flat 27.53. Shortening
patience underwater exits exactly into the weakness the retrace was waiting
for. **The edge IS the patience.** Flat K wins; keep it simple.

**3. Session structure (long-only, entry-hour, day-concentration-checked):**
the 12-16Z bucket holds +$21.19 of +$37.72 total (n=19, WR 73.7%, top day
only 30% of bucket - spread across days, not one lucky day). 04-08Z is
negative (-$3.53, WR 35%). BUT: synthetics have no human order flow, so a
session edge has no obvious mechanism (generator schedule? archive
artifact? n=19?). PRE-REGISTERED OBSERVATION ONLY - no session filter goes
live (grind_prediction precedent: entry filters destroy value). 28-day
window will confirm or kill it.

**4. THE MISSION NUMBER - compounding lot ladder vs fixed 0.1 (survival,
$10->100, 4wk, day-block, same trades):**

| config | fixed 0.1 | ladder 0.1->0.4 (eq 15/30/50) |
|---|---|---|
| L incumbent | 50.6 / 3.4 | 92.0 / 5.1 |
| **L TIMEBND 2000** | 70.7 / 0.2 | **99.2 / 0.2** |
| L TIMEBND 5000 | 73.6 / 2.5 | 95.5 / 3.6 |
| LS TIMEBND 2000 | 76.1 / 17.9 | 61.7 / 38.3 (ladder HURTS) |

The ladder multiplies whatever the daily distribution is: on a consistent,
skew-positive book (long-only + TIMEBND) it compounds nearly risk-free;
on a fat-tailed book (LS) it compounds the ruin. Long-only was already the
plan; the ladder is the reason it stays one.

**Deployment plan update (pending Oct 8 gate):** long-only + TIMEBND2000 +
lot ladder 0.1 -> 0.2 (eq 15) -> 0.3 (eq 30) -> 0.4 (eq 50). Live notes:
rescale the -$9 daily halt and equity floor with the lot rung (they are
model-dollar quantities); 60u server SL stays (dollar risk at 0.4 lots =
~$24, acceptable only above eq 50 by construction). Same caveats as ever:
11 daily blocks, in-sample sizing schedule, the 28-day read decides.

### P. Live-vs-backtest divergence watch (Sep 14, after 26 ledger round trips)

Live anatomy (slippage ledger, 26 RT, book per digest chain $11.50 -> $8.94
after the two Sep 14 evening losses): WR 38.5% (backtest 50%), avgW +$1.50
(bt +1.57), avgL -$0.93 (bt -1.20), worst -$5.37 (the known grinddown).
Shortfall vs backtest EV at same n: -$4.71. Decomposition: WR miss ~-$7.3,
win-size ~-$0.7, loss-size +$4.3 (live losses are SMALLER than modeled -
the exit stack works). Noise check: 10 wins in 26 at true p=0.5 is ~-1.2
sigma (~1-in-7 by chance) - underwhelming but not diagnostic.

Divergence to WATCH (too small to act on, n=12/14): live sides are INVERTED
vs backtest - longs -$2.09 (WR 33%) vs shorts +$2.19 (WR 43%), where the
backtest has longs carrying the book. If the inversion persists at n>=50,
the long-only deployment branch needs re-examination BEFORE adoption.
Loss-size hypothesis (user): rejected by data - avgL improved vs model;
the pain is the WR dip and its concentration on the long side. Re-check at
the 28-day gate with the same decomposition.

### Q. The profit-cap question: "bank $3-5 before the exit spike guts it" (Sep 14, analysis/flip_profit_cap.py)

**The motivating case is real and verified tick-by-tick:** live short
@9550.24410 (Sep 14 13:34->14:20Z) realized +$0.58; its open profit peaked
at +$2.65 at 14:20:54Z - ONE SECOND before the exit up-spike landed and cut
it to +$0.58. Giveback $2.08 = 78% of peak, in a single tick. Exactly the
mechanism the user described (this was the only adverse spike in the whole
47-min hold).

**Census (how often the feared scenario happens):** of 121 LS winners, 29
peaked >=$3; of those, only 1 gave back >=70% (the case above) and 0 gave
back >=90%. Givebacks >=50% happen in ~30% of winners (pain is real), but
the >=70% gut-punch is ~1% of ALL trades. Median winner: peak $1.90,
realized $1.11.

**TP ladder verdict: NEGATIVE at every dose, monotonic.** LS: incumbent
+$25.80/wk vs TP$1 +$16.81, TP$3 +$21.26, TP$5 +$23.34 (best). L: incumbent
+$23.20 vs TP$3 +$19.12, TP$6.5 +$22.34 (best). No dollar cap beats the
incumbent on either side. Why: a $3 cap banks every winner that would have
run (29 LS winners peaked >=$3, max $9.12) to protect against the ~1% gut-
punch - it truncates the right tail that pays for the whole book. The
counter-spike exit IS a profit-taking rule, just indexed to market violence
instead of a dollar number.

**Re-run Sep 16 (current 13.4-day archive incl. Sep 15's bleed day) - same
verdict, tighter:** LS incumbent +$13.67/wk vs TP$2 +$6.03, TP$3 +$10.78,
TP$5 +$11.55 (best). Long-only: incumbent +$7.99/wk vs TP$2 −$0.79 (kills
the edge entirely), TP$3 +$5.26, TP$5 +$6.25. The user's $2.00-2.50 band is
the WORST part of the ladder on the long side. Long-only giveback census:
21 winners peaked >=$3, only 1 cut >=70% - the gut-punch is ~0.5% of long
trades, and today's +$2.74→+$0.81 case (peak 1s before the spike) is the
second instance ever, the first being Sep 14's +$2.65→+$0.58. The user's
pattern-recognition is genuinely good (both gut-punches in the archive were
theirs first) - but the cure costs more than the disease: banking at $2
gives up the winners that run to +$8-9 to dodge a sub-1% tail.

Consistency note: this is the same message as TIMEBND but mirrored - the
edge lives in PATIENCE (hold through violence longer), not in banking
early. User's observation accepted; the proposed remedy rejected by data.
Tombstone #15 (TP family), now with the give-back census attached.

### Q2. Profit-cap clarification (user: "not a TP order - close when it REACHES the threshold")

Semantics tested were exactly that: tick-loop close at the crossing tick
(optimistic vs a resting order, if anything). Re-run on TIMEBND 2000 as
well - still negative in all six cells (LS: 35.97 -> 32.39@TP3 / 33.67@TP5;
L: 26.65 -> 23.65 / 24.64). The patient exit does not rescue the cap.

The decisive detail: the user's TWO live gut-punches, measured exactly -
  gp1 short@9579.023 (closed +$1.06): peak +$2.03, giveback 46% of peak
  gp2 short@9550.244 (closed +$0.58): peak +$2.65, giveback 78% of peak
BOTH PEAKED BELOW $3. A $3-5 close-threshold would NOT have fired on either
trade that motivated it. The threshold that would catch them ($2) is the
cell costing -$7.6/wk (LS) / -$9.2/wk (L) by banking the $4-$9 runners.
14% of archive winners give back >=70% of peak - the pain is real and
recurrent - but every measured remedy (dollar TP, trails, staging) pays
more in truncated right tail than it saves in giveback. Tombstone #15
stands, now with the user's own cases as exhibit A for why $3-5 misses.

### Appendix R — Entry-filter sweep: the four untested "filter bad trades" families (Sep 14)

Question: can any pre-entry state refuse the bad trades? Four families never
measured, tested on the masked 11.5-day archive (engine fleet-validated;
two implementation bugs fixed pre-read: CHURN quiet-counter reset, LSTREAK
cooldown re-arm). Entries identical to incumbent; only the filter varies.

| filter | LS net/wk (inc +25.80) | L net/wk (inc +23.20) | verdict |
|---|---|---|---|
| CHURN >=10/30/60 quiet ticks | +25.78 / +22.96 / +23.80 | +23.18 / +23.17 / +22.45 | tombstone #16 — spike clusters carry no edge; waiting after a spike only pays more spread later |
| LSTREAK skip after 2/3 losses | +24.52 / +24.43 | +21.67 / +22.60 | tombstone #17 — losses don't cluster beyond chance; skipping trades forfeits recovery wins (halts-poison, per-trade edition) |
| ACTREG 4-60 spikes/2h band | **+30.74** | **+26.68** | passes split (LS H1 +4.09 vs +3.49, H2 +3.14 vs +2.58) BUT day-table: 89-99% of gain from 2 of 12 days (09-04, 09-12), only 4-8 trades filtered |
| LIVE-SPREAD ceiling | n/a | n/a | structurally dead — live spread is a constant 0.6261u (no variance to filter on); corr(spread,pnl)=nan |

ACTREG disposition: **pre-registered observation, not a candidate.** Never
worse, tiny worst-loss improvement on L (-$3.45 vs -$5.37), but the effect
lives on two days out of twelve and n(filtered)=4-8. The 28-day window
will measure it for free (log activity at entry; decide later).

Entry-filter seam now declared **exhaustive**: MAG (M), grind features (I),
THICK (N), halts (K), churn, loss-streaks, activity regime, live spread.
Eight families, all measured, none deployment-grade. The information that
separates winners from losers is not in the entry state — it accumulates
during the hold, which is why every surviving edge is exit-side (TIMEBND).

### Appendix S — Structural concepts: S/R, order blocks, MTF, round numbers (Sep 14)

User question: do order blocks / S&R / multi-timeframe (M5, H1) analysis
apply here? General evidence first: SMC/order-block material is almost
entirely unvalidated marketing; S/R has limited academic support in HUMAN
markets (Osler 2000/2003 — levels as self-fulfilling order concentrations);
MTF is mostly a discipline tool, weak lead-lag evidence. On a synthetic
index the mechanism is absent BY CONSTRUCTION: no institutions leave order
blocks, no humans cluster orders at levels, and higher timeframes are
re-slicings of the same tick stream (zero information the tick studies
have not already searched). Tested anyway (analysis/flip_structural.py,
126 long-only incumbent entries, features strictly pre-entry):

| concept (operationalized) | result |
|---|---|
| S/R = prior spike extremes (last 10 highs/lows) | near-level n=15 mean +$0.48 vs far n=111 +$0.28 — opposite sign of S/R logic, both within noise |
| OB = entry inside the pre-spike calm zone | inside n=11 mean -$0.03 vs outside n=115 +$0.33 — right direction, n too small, noise |
| MTF trend (M5 x6/x12/x24 lookback) | x6: means IDENTICAL (+0.32/+0.30). x12: identical (+0.27/+0.31). x24: UP +$0.60 vs DOWN +$0.18 — 1 of 6 comparisons, skewed-mean fragile, overlaps the 12-16Z session pocket (Appendix O) |
| round numbers (nearest 100.0) | n=2 entries within 0.50u of a round level — no test possible |

Verdict: no deployment-grade signal in any family. The deeper answer: the
phase-follower already IS this symbol's order-block strategy — it enters at
the base of the actual violence event (the generative spike) rather than
inferring the zone from candle shadows on higher timeframes. And the
spike-base absorption asymmetry (section "absorbed vs survived") is the
mechanical analog of S/R that the exit logic already exploits. The single
hint worth watching: long 2h-momentum entries out-averaged — same trades
that drive the session pocket; log t24 trend at entry alongside activity
count so the 28-day gate scores both for free.

### Appendix T — Generator anatomy: the algorithm IS the market (Sep 14)

Thesis tested: a synthetic index has no human order flow, so the edge (if
new ones are wanted) must come from the GENERATOR's own structure.
analysis/flip_generator_anatomy.py, all four flip symbols, healed 12-day
archive, fleet spike definition. Four findings:

1. DIRECTION MEMORY CONFIRMED FAMILY-WIDE (the phase edge's root cause,
   now at generator level): P(next spike same direction) = 0.631 / 0.609 /
   0.651 / 0.758 for flip150/300/500/1000 (0.5 = independent). flip1000
   run lengths: mean 4.13, max 258 consecutive same-direction spikes.
   This is an autoregressive directional process, not a fair coin.

2. ASYMMETRIC POST-SPIKE DRIFT ON flip1000 (the long-only thesis quantified):
   median per-tick drift after spikes: after UP-spikes +4.6e-3 (continuation!),
   after DOWN-spikes +8.8e-3 (revert upward) at H=60..300 ticks. Both signs
   POSITIVE — the symbol structurally climbs after violence, down-spikes
   1.9x harder than up-spikes continue. flip150/300/500 revert both ways;
   only flip1000 has the climb-after-everything profile that long-only
   exploits. The absorption asymmetry is now a measured generator constant.

3. NEW STRUCTURE: flip1000 HAS TWO SPIKE POPULATIONS. Threshold sweep:
   thr 5x -> 1645 spikes, mean gap 612 (spec: 1000), gap-variance 2.22x
   geometric. thr 8x/12x/20x -> ~1030/1027/1024 spikes, gap ~985 (spec
   N=1000 EXACT), variance 1.06x. Interpretation: the spec'd major spike
   (>=8x median) is memoryless as advertised; a MINOR class (5-8x median,
   ~600 events/11.5d) rides on top AND CLUSTERS (overdispersed 2.22x) —
   minor spikes beget minor spikes. flip150/300/500 show no such class.
   Timing structure exists for the minor class. Pre-registered hypothesis
   (not traded): in minor-burst regimes the next-spike wait shortens, so
   TIMEBND's tick-patience could be regime-adaptive. Needs its own study.

4. DEAD ENDS RULED OUT: cross-symbol spike co-occurrence — all six pairs
   independent (permutation p 0.19-1.0, |r| <= 0.006). No shared seed
   scheduling between variants; each symbol's clock is its own. Major-spike
   timing is memoryless (1.06x) — no "spike is due" timer exists.

Where this leaves the "create the edge" program: the generator's exploitable
structure is already largely harvested by the phase-follower (findings 1+2
are the edge we trade). The one genuinely new seam is the minor-spike
cluster regime (finding 3) — queued behind the Oct 8 gate.

### Appendix U — Minor-spike regime: real structure, zero tradeable overlap (Sep 14)

Follow-up to Appendix T finding 3. Engine extension validated EXACTLY vs
fleet (counter / TIMEBND 2000 / TIMEBND 5000: 126 trips each, totals to
the cent). analysis/flip_minor_regime.py.

STRUCTURE CONFIRMED AND SHARPENED: 615 minors, median gap between
consecutive minors = 10 ticks (mean 1586) — minors arrive in ultra-tight
bursts separated by long droughts. Only 4% of majors have any minor within
+/-2000 ticks. The two populations are nearly disjoint in time, not just
in size.

Q1 (does the regime predict entry outcomes?): structurally NO — and the
reason is better than a small-n shrug. Entries fire on first-spike-after-
calm events, which are majors; minors avoid major neighborhoods (4%
co-occurrence), so 94% of entries have R=0 (zero minors in the prior 2000
ticks). Minor-class entries n=6 (incumbent mean -$0.12, TIMEBND -$0.37),
burst-regime entries n=3 (WR 0%) — real directions in the data but far
below any readable n. The regime cannot select trades because it almost
never co-occurs with them.

Q2 (adaptive patience K_low/K_high by regime): tombstone #18. All six
adaptive cells land between flat-K references; best adaptive (+$26.97/wk,
K=2000/5000 r0=1 or 2) barely clears flat K=2000 (+$26.65) and is beaten
by flat K=5000 (+$27.87) — with a day-table delta of +$0.52 on ONE day.
The regime switch is mechanically near-inert because so few entries carry
R>=1. The flat, simpler rule dominates.

Bonus mechanism worth recording: the engine's pending-exit counts any >5x
spike as a resolution, so minor BURSTS during a TIMEBND hold already
shorten pending waits — the minor class's main interaction with our P&L
is baked into TIMEBND's mechanics, which is why extra flat patience
(K=5000) is affordable. No new rule needed.

Gate menu unchanged: incumbent+25u stop / TIMEBND 2000 / TIMEBND 5000,
long-only, head-to-head on 28 clean days.

### Appendix P — interim re-check (Sep 15, n=38 of 50)

Side-split study: `analysis/flip_side_split.py` (fleet engine, matched window,
live ledger paired trips). Live: longs 19 avg −$0.05 (WR 42%), shorts 19 avg
+$0.15 (WR 53%) — inversion direction persists but sign test p=0.32, NOT
significant. Key context: the matched-window backtest shows the SAME ordering
this week (long +$0.11 n=83 vs short +$0.37 n=35) — the Sep 10–15 tick data
itself was short-friendly; this is a market-regime artifact, not a live
execution defect. Full archive: long +$0.19 vs short +$0.16 per trade —
long-only's case was always tail shape (survival odds), not per-trade average.
Settle gate n>=50 pre-registered (~2 more days); checkpoint re-captures
nightly via the [SIDE-SPLIT] block. No deployment action before settle.

### Appendix V — survival refresh before the Oct 8 gate (Sep 15, 12.7-day archive)

Runner: `analysis/flip_survival_refresh.py` (fleet engine `flip_next_gen.run`,
long-only, day-block bootstrap $10->$100 28d 2000 sims, fixed 0.1 lot +
ladder 0.1->0.4). Reconciliation: engine both-sides edge +$0.1698/trade vs
live review +$0.1776 -> drift -0.008 (engine validated, live numbers not
flattering themselves). Engine reproduction check: flip_next_gen section 4
on the same archive reproduces every cell exactly (seed=11).

| long-only config | $/trade | fixed flip/ruin | ladder flip/ruin |
|---|---|---|---|
| incumbent | +$0.185 | 21.6% / 11.7% | 70.3% / 16.2% |
| incumbent +25u stop | +$0.161 | 17.0% / 11.9% | 69.0% / 17.0% |
| TIMEBND 2000 | +$0.218 | 32.8% / 4.9% | 85.8% / 5.8% |
| TIMEBND 5000 | +$0.258 | 50.8% / 4.0% | 90.1% / 5.9% |

**The moving-odds lesson:** vs the Sep 10 run (11.4d archive) incumbent
ladder fell 92%->70%, TIMEBND2000 99%->86% — two weak days (Sep 14-15, the
short-friendly week, Appendix P) moved everything. With ~13 daily blocks each
day swings the bootstrap by points; the 28-day gate exists to average this.
Menu leader unchanged in shape: TIMEBND 5000 (best fixed AND ladder odds,
ruin <=6%). LS TIMEBND2000 this window: 71% fixed flip but 39.9% ladder ruin —
the ladder multiplies whatever tail it is given; long-only remains the
tail-safe backbone. No config change before the gate.

### Appendix T addendum — the x-scale saturates on majors (Sep 15)

First telemetry-stamped live trade (19:12Z, x=1269.87) prompted an anomaly
check that resolved into a scale clarification: the delta distribution is
bimodal — drift ticks (median ~0.0086u) and MAJOR spikes (10-30u single-tick
jumps, ~70-95/day, broker- and archive-confirmed). entry_x = delta/median is
therefore ~100-3000 for majors and 5-8 for minors; both exceed the 5.0
detector gate. MAG/THICK tercile studies were computed on this same mixed
scale, so no prior result is invalidated — but Oct 8 scorers reading
entry_x should know majors dominate the upper scale. Broker verification:
19:15:30Z jump 9463.97 -> 9451.74 was a single 1001ms-cadence tick (no hole),
price stayed — a genuine major, correctly traded as the exit event.

### Appendix W — sizing-ladder steepness sweep (Sep 15, archive ~1.18M ticks)

Question (user): "what if lot size increases with revenue at stages?" — that is
the deployment ladder itself; the untested parameter was STEEPNESS. Sweep of
six schedules on the fleet engine (long-only, $10->$100, 28d, 2000 day-block
sims, marks read from live equity so losses auto-de-size):

| schedule | incumbent flip/ruin | TIMEBND 5000 flip/ruin |
|---|---|---|
| fixed 0.1 | 19.7 / 13.2 | 51.1 / 3.9 |
| **std 0.1->0.4 @15/30/50** | **67.0 / 18.6** | **90.2 / 5.9** |
| mild 0.1->0.5 @12/20/35 | 65.3 / 26.6 | 89.5 / 7.8 |
| steep 0.1->0.8 @12/18/30 | 53.8 / 44.6 | 77.1 / 21.9 |
| x2 stages 0.1->0.8 @15/30/60 | 65.7 / 20.4 | 90.0 / 6.1 |
| v.steep 0.1->1.2 @12/20/35 | 46.4 / 53.3 | 69.8 / 30.1 |

Findings: (1) steepness has an optimum and the planned 0.1->0.4 schedule sits on
it — every steeper schedule strictly worsens BOTH flip and ruin odds (more
aggression buys speed, pays ruin). (2) The x2-doubling variant ties std
(90.0/6.1) and buys nothing -> std stays for simplicity. (3) The exit lever
(TIMEBND 5000) dominates any sizing choice: fixed-lot TIMEBND (51.1%) beats
most incumbent ladders. Validation anchor: std rung reproduces
flip_next_gen.ladder_fn exactly. Caveats: 14 daily blocks -> wide CIs; menu
in-sample; deployment itself gated on Oct 8 live-edge confirmation.

### Appendix X — daily profit aim (user hypothesis, Sep 15): "lock the day so we don't lose progress"

Mirror of the deployed −$9 daily halt: halt NEW entries once day PnL >= aim
(same day_state machinery, UTC reset). Sim: fleet engine day-block bootstrap
with intra-day truncation, anchor-validated against flip_next_gen.day_block
(all 4 cells within 2xSE). Long-only, $10->$100, 28d, 2000 sims.

Giveback census first (13 archive days): 12/13 days peaked >= +$1 intraday;
total giveback $41.05, worst three: peak +$4.73 -> close −$4.79 (−$9.51),
peak +$1.91 -> close −$6.30 (−$8.22), peak +$22.00 -> close +$16.04 (−$5.96).
The pain the user describes is real and large at day scale.

| rule (long-only, ladder 0.1->0.4) | incumbent flip/ruin | TIMEBND 5000 flip/ruin |
|---|---|---|
| no aim (reference) | 65.2 / 19.1 | 90.4 / 5.8 |
| aim $3 | **97.5 / 2.3** | 91.8 / 2.2 |
| aim $4 | 92.0 / 5.2 | 88.9 / 3.4 |
| aim $5 | 85.1 / 12.8 | 93.0 / 4.1 |
| aim $5 + stop $9 | 55.8 / 14.5 | 81.4 / 6.9 |

Mechanism (decomposed, not hand-waved): the aim truncates a day at +$3 once
crossed. (a) Giveback-to-loss days (+$4.73->−$4.79) become locked +$3 wins —
bad-day sum improves −$13.81 -> −$9.02. (b) Cost: big clean runner days
(+$11.64, +$6.81) are ALSO truncated — mean day barely moves ($2.19 -> $2.30,
within noise). The flip improvement is SD compression ($5.98 -> $3.92) plus
giveback savings, amplified by the ladder (higher multipliers held more of the
horizon). Seed-robust across 11/23/47. EV-neutral-to-positive; the win is
variance, not edge.

**Unplanned finding — the existing −$9 daily halt turns poisonous under the
ladder.** The halt reads SCALED day PnL: at 0.3 lots, −$9 = raw −$3, and the
aim$5+stop$9 cell (55.8%) vs aim$5 alone (85.1%) shows it halting ordinary
recoverable dips at size — "halts skip recovery wins," the Sep-9 lesson,
re-discovered at ladder scale. flip_next_gen.day_block models NO halt, so the
90%+ refresh numbers assume the halt does no damage; the sweep says at 0.2+ lots
it does. BEFORE funding: scale the halt threshold with lot size (e.g. −$9 x
multiplier) or re-express in raw units. Frozen diagnostic book unaffected
(0.1 lot, halt = raw −$9).

Caveats: 13 daily blocks; menu/aims in-sample; bootstrap robustness only;
realization requires the same day_state reset the executor already runs.
Status: pre-registered as a deployment-menu candidate (aim $3 on the confirmed
branch), NOT deployed before Oct 8. Graveyard count unchanged (not a tombstone —
it survived, narrowly).

### Appendix X2 — aim-$3 seed stress test (Sep 16): the result did not survive

Tool: `analysis/flip_aim_stress.py` — 7 seeds (11/23/29/37/47/53/61), N=2000
each, PAIRED design (same seed drives the identical day-draw sequence in the
no-aim and aim walks). Anchor 1: no-aim cells reproduce
flip_next_gen.day_block exactly (0.00pp delta, both engines). Anchor 2:
scaled-halt bug cell qualitatively reproduced (11.1 vs 55.8 recorded;
approximate intra-day semantics, band only — recorded in the caveat, not
used for conclusions).

**Trigger for the re-test: the archive itself changed.** Sep 15 completed as
a clean 24h day (164,677 ticks, 00:00:01→23:59:59 UTC, zero >5min holes,
biggest gap 3s) and it is the worst long-only daily block ever recorded:
incumbent **−$15.13** (44 trips), TIMEBND 5000 **−$19.80**, worst trips
−$2.88/−$4.84 — no monster trade, a sustained bleed. Two weak days
(Sep 14–15) previously halved the odds; this is worse than both combined.

Per-seed mean (7 seeds, flip%/ruin%):
| cell | Sep 15 recorded | 7-seed mean now | delta |
|---|---|---|---|
| incumbent ladder no-aim | 70.3/16.2 | **28.2/68.5** ±0.9 | −42pp flip |
| incumbent ladder aim $3 | 97.5/2.3 | **35.8/61.1** ±1.2 | −62pp flip |
| TIMEBND ladder no-aim | 90.1/5.9 | **43.0/56.5** ±1.5 | −47pp flip |
| TIMEBND ladder aim $3 | 91.8/2.2 | **37.3/61.6** ±1.2 | −55pp flip |

Paired aim-$3 minus no-aim (same seed): incumbent ladder **+7.5pp flip,
−7.3pp ruin — STABLE across all 7 seeds** (the direction held). But
TIMEBND ladder **−5.7pp flip, +5.0pp ruin — also STABLE (the aim HURTS
TIMEBND)**, and TIMEBND fixed collapses −17.0pp flip. The Appendix-X
pattern ("aim helps both engines") was an artifact of the pre-Sep-15
archive; the truth is engine-dependent.

**Why: the aim's protection is giveback-only.** Intraday peak census
(long-only): the aim locks +$3 only on days that PEAK ≥ $3 — Sep 15 peaked
at **+$1.39** (incumbent) / **+$1.20** (TIMEBND) and bled to −$15.13/−$19.80
passing straight through untouched. Sep 13 same shape (peak +$1.91). The
aim converts giveback days (Sep 14: +$4.73→−$4.79 becomes locked +$3) but
cannot touch bleed days — and bleed days are precisely the worst blocks.
Day-sum SD compression is likewise weaker than recorded: $7.52→$6.03
(incumbent), $8.43→$6.99 (TIMEBND) — was $5.98→$3.92.

**Verdict: aim $3 is DOWNGRADED from deployment candidate to
rejected-for-now.** The 97.5%/2.3% headline was an artifact of a 13-block
archive that happened to contain giveback-shaped bad days; one real
bleed-shaped block collapsed it. With 14 blocks the whole menu sits at
28–43% flip / 56–69% ruin — and the REAL Oct 8 gate (live executor book,
not this archive) is the decision instrument. The aim concept retains one
narrow honest form: it helps only if bad days keep being giveback-shaped;
that is a market-regime assumption, not an edge. FLIP_PLAN.md aim bullet
updated to match; halt-scaling fix remains mandatory and unaffected.

Caveats: 14 daily blocks (one bleed block moves everything — that is the
finding); menu still in-sample; anchor-2 approximation noted; the archive
grows daily so all figures are a snapshot of Sep 16 state.

### Appendix Y — QUIET-ENTRY exploit: suppress entries in minor-spike bursts (Sep 16)

Chain of evidence: exploit probes (Sep 16) showed minor (5-8x) spike density
varies **17.7x by UTC hour** while majors stay flat (the spec clock) — and
minor_regime (Appendix U) had shown burst-state entries lose (>=3 minors in
trailing 2000 ticks: −$0.49/trip vs +$0.28 quiet) but only ever tested
*patience* adaptation, never plain entry suppression. This appendix runs the
suppression cell. Tool: `analysis/flip_quiet_entry.py` (fleet engine +
`minor_hist` deque; bad-tick mask aligned; r0=off reproduces the fleet rows
bit-exact: LS counter 310/$+23.15, L counter 198/$+15.24, L TB5000
198/$+25.78, LS TB5000 275/$+18.36).

Grid (13.4-day archive, net $/wk):

| config | r0=off | r0>=2 | r0>=3 | r0>=4 |
|---|---|---|---|---|
| LS incumbent | +13.67 (310t, WR 47%) | +25.84 (254t, WR 52%) | same | same |
| LS TIMEBND 5000 | +9.63 (275t) | +19.96 (219t, WR 64%) | same | same |
| L incumbent | +7.99 (198t, WR 47%) | **+20.95** (143t, WR 55%) | same | same |
| L TIMEBND 5000 | +13.52 (198t) | **+24.64** (143t, WR 63%) | same | same |

r0 2/3/4 are identical -> the burst regime is binary (a flood, not a ramp) —
coherent with P2's clustering. Day-block survival (std ladder): L incumbent
27.0/69.5 -> **86.7/5.5** flip/ruin; L TIMEBND 41.4/58.1 -> **89.6/7.6**.

**The ACTREG-ghost referee (day concentration) passes.** The filter's entire
delta lands on the two bleed days: Sep 14 +9.64 (35 trips -> 12, −4.79 ->
+4.85), Sep 15 +13.49 (44 -> 15, −15.13 -> −1.64); all other 12 days exactly
+0.00. Top-2-positive-day share of gains: 65% -> 60% (ACTREG's poisoned
signature was 89-99%). Total L book +15.24 -> +39.96.

Mechanism (fits Sep 15's anatomy): a minor flood IS the absorption storm —
4,545 up-minors vs 46 down-minors while price ground −193.44u. The phase
detector votes on spikes; in a flood the votes lie, and every entry bought
into the lie. Suppressing entries while the flood is on removes exactly the
trips that die, without touching any quiet-day trip (delta exactly 0.00).

**Status: pre-registered as the second menu candidate for Oct 8** (alongside
exit family; candidate parameter r0=3, the Q1b convention — indistinguishable
from 2). NOT deployed: the executor's strategy stays frozen for a clean gate.
To make the gate able to score it live, the executor now stamps **min_2k**
(minors in trailing 2000 tick deltas, incl. the entry spike, window seeded
from the tick store at startup) on every ledger row — telemetry only. Scoring
lives in flip_telemetry_score P3 (quiet <=2 vs burst >2, n-gate 8).

Caveats: in-sample menu on 14 daily blocks; suppression validated on archive
only — the live check is P3 at the gate; act_2h-style warmup does not apply
(minor_hist is seeded, so min_2k is valid from tick 1).

### Appendix Z — strategy probe round 2 (Sep 16): P6/P7/P8/P9

Tool: `analysis/flip_new_probes.py`. Also: `flip_quiet_entry.run_quiet` exit
semantics aligned to the fleet (no `continue` past the entry block -> same-tick
re-entry after TIMEBND retrace-closes, as in flip_exit_families). With this,
all FOUR fleet cells validate bit-exact (LS counter 310/$+23.15, L counter
198/$+15.24, L TB5000 198/$+25.78, LS TB5000 278/$+32.09-32.20 trip-identical,
residual = archive growth between snapshots). All Appendix Y L-numbers
unchanged; LS/TB5000 references improve to 278t/$+16.82 -> 222t/$+27.15/wk.

**P6 instant-retrace exit: DEAD.** The 67-69% "opposite tick" after a spike
is symmetric ~one-tick noise: E[1-tick move after an ADVERSE spike] is
−$0.0012/trip (long side, n=577) and −$0.0245 (short side, n=9368 — down
majors continue). Nothing near the $0.06 friction; the executor's at-spike
fill is already the right timing. The absorption asymmetry is a phase-
length property, not a 1-tick property.

**P7 echo decay: real, clean, monotone.** Minor density vs time-since-major:
7.73/1k (0-30s) -> 8.88 (30-60s, peak) -> 8.00 -> 7.36 -> 7.06 -> 6.83
(10-30m) -> 4.52 (>30m). Echo half-life is ~tens of minutes. Confirms minor
floods are major-echoes (exploit-probes P2) and supplies a second quiet-gate
form (time-since-major < T) — held in reserve; trailing-count gate (Y) is
already live-scored via min_2k.

**P8 quiet-entry seed stress: STABLE (X2 discipline passed).** Paired
3-seed day-block survival, L-only, std ladder: r0=3 vs r0=off =
+59.7/+60.4/+60.3pp flip, −64.0/−63.8/−64.3pp ruin across seeds
11/23/47 (r0=off: 27-28% flip / 68-70% ruin; r0=3: 87-88% / 4.7-5.5%).
Unlike Appendix X's aim, no single seed moves the sign; the Y verdict does
not live on one random draw.

**P9 STAGED-T50 re-check: still dominated.** Current archive: LS +$8.55/wk
(incumbent +$13.67, TB5000 +$16.82); L +$4.70/wk, ladder 21.2/75.4 —
worst of the three. The 1-tick gap between halves keeps paying full spread
for no protection (worst trip −$2.72 vs incumbent −$5.37, but the WR loss
costs more). Verdict unchanged; STAGED stays retired.

Net: quiet-entry remains the strongest menu candidate (now 4-cell validated
and seed-stable); no new edge this round; two more tombstones (P6, P9).

### Appendix AA — tick-quantum round + the event-class fence (Sep 16): P10-P15

Tool: `analysis/flip_quantum_probe.py` (+ `analysis/flip_fence_stress.py`).
Perspective: read the market at the generator's atom level, then re-ask
where the perfect entry is.

**P10 quantum anatomy — the two-population discovery.** Point=0.0001u
(84 points = 1 median); no zero-delta ticks (every tick is a state change);
0.29% float dust. The magnitude distribution is TWO populations, not one:
an ordinary band ending near ~8x median, an EMPTY 8-20x gap (1,180 ticks
>8x vs 1,173 >20x), and a true-jump class 20x-2000x (max 32.7u observed).
Jump rate 1,173/13.4d = 87.9/day = the spec clock (1/1000 ticks). The
generator makes ~88 real decisions/day; everything else is drift dust.

**P11 level magnetism: weak, non-actionable.** Round-level (0.05/0.25/1.0u)
conditional drift spreads are +$0.009-0.023 at H=5000t — real but ~10x
below per-trip friction. No level filter.

**P12 spike microanatomy: majors are atomic.** ZERO same-sign spike
clusters within 500ms (n=9,945). Every >=5x spike is one generator event;
phase age counts independent decisions. No cluster semantics needed.

**P13 mirror asymmetry quantified.** Post-major drift: after UP, +62.5u/1k
ticks at N=10 flipping to −0.87 by N=2000 (absorption); after DOWN,
−967u/1k at N=10 (the jump itself) then sustained negative drift −5.1 at
N=2000 (continuation). The entry rule already harvests this exact shape.

**P14 event-class fence — THE finding.** Move the phase-vote fence from
5x to 8x+ median (true jumps only): L incumbent +$7.99 -> +$17.98/wk;
L TB5000 +$13.52 -> +$22.42/wk; ladder 27/70 -> 78.5/9.4 and 41/58 ->
84.5/10.8. Fence-robust: 8x/12x/20x identical trip sets (the empty gap
makes placement inside [8,20] moot). The 5x fence was voting partly on
ordinary-tail noise; at 8x every vote is a spec-clock decision.

**P15 stress + combination.** Seed table (paired 3 seeds): fence8
incumbent 78.1-80.1 flip / 7.3-9.6 ruin; fence8 TB5000 85.3-86.6 / 8.7-10.4
— stable. **Combination: quiet-entry r0=3 is a NO-OP at fence8** (rows
identical) — once minors cannot vote, floods cannot manufacture phase-age,
so the bad entries never occur. The fence is the root-cause fix; quiet-entry
was the symptom patch. LS fence8: +$28.83/+$31.42 per wk. Worst trip
slightly larger (−6.51 TB5000).

**Status: fence8 (MULT 5.0 -> 8.0) pre-registered as the third Oct 8 menu
candidate** — arguably the front-runner on mechanism simplicity (one
change, no second gate). NOT deployed: the diagnostic executor keeps
voting at 5x so the live measurement stays clean. Live scoring path:
NONE yet (min_2k/act_2h score the 5x world; a fence decision at the gate
must lean on the Oct 8 archive re-run + live trip count comparison).

Menu summary for Oct 8 (archive, std ladder, seed-stable):
  fence5 + r0=3 + TB5000:  89.6-90.5 flip / 5.9-7.6 ruin   (two mechanisms)
  fence8 + TB5000:         85.3-86.6 flip / 8.7-10.4 ruin  (one mechanism)
  fence8 + incumbent:      78.1-80.1 flip / 7.3-9.6 ruin

### Appendix AB — fence generality across the Flip family (Sep 16): P16

Tool: `analysis/flip_fence_generality.py`. Question: is Appendix AA's
two-population structure (ordinary band vs true-jump class at the spec
clock) family-wide? Census at 1.0/1.6/2.4/4.0x each symbol's frozen
baseline fence + fence ladder (long-only, per-symbol live spread and VPU).

Spec clocks (86400/N ticks per day: flip150 576, flip300 288, flip500
172.8, flip1000 86.4 — the script's header line printed these wrong as
N*2; the census numbers below are the truth):

| symbol | base fence -> true class | census gap | spec rate vs measured |
|---|---|---|---|
| flip1000 | 5x -> 8x | 744.8/day -> 88.4/day | 86.4 vs 87.9 ✓ |
| flip500 | 3.5x -> 5.6x | 999.3 -> 177.4/day | 172.8 vs 177.4 ✓ |
| flip300 | 3.0x -> ~5-7x | 3255 -> 325.9 -> 288.1/day | 288 vs 288.1 ✓ |
| flip150 | 2.5x -> (noisy, ~7-10x) | 21853 -> 14628 -> 1299 -> 555/day | 576 vs 555 ✓ (at 10x) |

**The persistence unmasking (the deepest cross-family result).** At the
LOW fence, P(next spike same direction) is inflated by drift
autocorrelation: flip1000 94.5%, flip300 86.9%, flip500 80.8% — fake
persistence from ordinary-tick runs. At the TRUE-class fence it
converges: **62.9% / 58.6% / 64.8%** — the generator's decision
persistence is ~60% family-wide. The 5x-fence stream was never "more
persistent"; it was mixing two populations.

Economics (L-only, $/wk at 0.1 lot):
| symbol | base fence | true-class fence |
|---|---|---|
| flip1000 | +7.99 (5x) | **+17.97** (8x); TB5000 +13.52 -> **+22.42** |
| flip500 | +1.02 (3.5x) | **+18.64** (5.6x); TB5000 -14.99 -> **+18.94** |
| flip300 | -68.97 (3x) | **+66.39** (7.2x); TB5000 -41.93 -> +19.07 |
| flip150 | -97.55 (2.5x) | -27.09 (4x) — **untradeable** |

flip150's verdict is structural: live spread 0.17u = **27.9x its median
tick** — friction is an order of magnitude past any fence's reach
(consistent with the Sep 13 family scan).

**Verdict: GENERATILITY CONFIRMED for flip300/flip500/flip1000.** The
two-population structure, the ~60% true-jump persistence, and the
fence-improvement all replicate. Fence placement inside the empty gap is
unimportant everywhere (flip500 5.6x ≈ 8.4x; flip300 4.8x ≈ 7.2x counts).

Caveats: 12.9-12.9-13.4-day archives, same calendar era (one regime
sample); per-symbol baselines frozen from the earlier study; TB5000's
k=5000 was tuned on flip1000 and does NOT transfer (flip300 incumbent
beats TB at true-class fence — exit family may be per-symbol); all
figures in-sample. Cross-symbol diversification is a NEW deployment
question — deliberately not opened; flip1000 remains the mission vehicle
until Oct 8.

### Appendix AC — flip300 second-book SKETCH (post-$100 diversification, Sep 16)

Not scheduled. Activates only after the flip1000 mission completes (or the
user explicitly re-orders priorities after Oct 8). Numbers from the 12.9-day
archive at the true-class fence 7.2x, per-symbol spread 2.88u, VPU 0.10:

  incumbent (counter) exit: 443 trips (34/day), +$66.39/wk at 0.1 lot,
    WR 53.0%, avgW +$2.62, avgL -$2.37, worst trip -$14.66,
    daily mean +$8.76 / SD $21.30 / worst day -$25.79 / best +$55.15
  fixed-lot seed-book odds (3 seeds stable):
    $20 -> $40 double-or-bust: 77.5-78.3% flip / 21.6-22.6% ruin
    $30 -> $60:                86.9% / 13.1%
  TIMEBND does NOT transfer (k=5000/10000 both +$19.07/wk, worst day
    -$72.59) - the patience constant is flip1000-tuned; use the incumbent
    counter exit on flip300.

Playbook (if/when activated):
  1. Gate: flip300 gets its OWN 28-day live confirmation (diagnostic
     executor, demo, 0.1 lot fixed, fence 7.2x, counter exit) before any
     funding - same discipline as flip1000. Archive will be 35+ days by
     Oct 8; the collector already covers flip300.
  2. Capital: seed-book $50-60, NOT $20-30 - at 0.1 min lot the worst
     archive day (-$25.79) is half a $50 book. RESOLVED Sep 16: broker
     volume_min for flip300 = 0.1 (volume_step 0.01, but lots below 0.1
     are rejected), so the $30 book is NOT viable; $50-60 stands. Never
     ladder before the live edge is confirmed on flip300's own ledger.
  3. Ops: separate executor instance (own MAGIC, pid, log, day_state),
     same account-guard/checkpoint pattern. Tick collection already runs.
  4. Correlation honesty: all Flips share one generator; flip300's bad
     days will correlate with flip1000's. This is diversification of
     friction/schedule, not of mechanism.
  5. Known ops risks: trade-link 10027 class failures will recur; spread
     is 46% of avg win - a spread widening to 3.5u kills roughly a third
     of the edge.

Open questions: per-symbol halt thresholds; whether the
session-pocket structure (P1) exists on flip300; fence placement inside
its 4.8-12x empty gap is insensitive (census Appendix AB).

## Appendix AD — Entry-delay confirmation (P15, Sep 16)

USER HYPOTHESIS: entering immediately in the spike's direction can be
caught by an immediate reversal; "what if it waits for ~3 bars to be
sure". Formalized: qualifying spike starts a PENDING entry (cancel if
an opposite spike arrives); fill at the then-current price after DELAY
ticks. 1 tick/s, so 3 one-minute bars ~ 180 ticks.

Engine: fleet clone + pending layer, validated BIT-EXACT on all 8
cells (LS/L x counter/TB5000 x fence5/fence8) at delay=0. Sweep ran
delay in {0, 60, 180, 600}.

Results (net/wk, 13.4-day gap-masked archive):

  fence5 L counter:   d0 +7.99  d60 +7.91  d180 +8.54  d600 +10.02
  fence5 LS counter:  d0 +13.67 d60 +12.11 d180 +14.66 d600 +19.44
  fence5 L TB5000:    d0 +13.52 d60 +11.40 d180 +4.49  d600 +11.50
  fence5 LS TB5000:   d0 +9.63  d60 +7.20  d180 -4.54  d600 +19.86
  fence8 L counter:   d0 +17.97 d60 +18.64 d180 +18.46 d600 +16.70
  fence8 LS counter:  d0 +28.83 d60 +29.36 d180 +30.84 d600 +28.78
  fence8 L TB5000:    d0 +22.42 d60 +18.50 d180 +10.72 d600 +15.17
  fence8 LS TB5000:   d0 +23.26 d60 +17.13 d180 +4.72  d600 +27.99

READING:
  1. P13's drift-decay prediction HOLDS: delay hurts longs on the
     patience-family exits (TB5000 L: d180 costs $9-12/wk - it gives
     up the freshest decayed drift) and helps shorts (S counter d0
     +5.68 -> d600 +9.42; down-drift persists, so late short entries
     still land inside it).
  2. At fence8 delay is a NO-OP on counter (+/-0.5) and strictly
     harmful on TB5000 except d600. The true-jump fence already
     removed the entries delay was avoiding.
  3. At fence5, delay STACKS on quiet-entry (not redundant):
     LS counter r0=3 alone +25.84 -> +r0=3+delay600 +29.20/wk.
     X2 stress (3 seeds, FIXED 0.1 lot): flip 73.5-75.4%, ruin
     1.8-2.4% - lowest ruin of any LS config on record. L row
     +20.25/wk, ruin ~0.1%, ladder 96.1% flip.
  4. Caveat: the delay response is NON-MONOTONIC in K (d180 dips,
     d600 peaks on several cells). Direction is coherent with
     mechanism; the specific 600 value may be window luck. Also the
     fence5 gain concentrates on the short side, and this window had
     short-friendly drift days.
  5. Census: cancels-by-flip are rare (fence5 L d600: 38 of 198
     entries = 19%) - the wait rarely fires its gun.

VERDICT: user's instinct is HALF right. Waiting helps shorts and the
fence5 LS book, does nothing at fence8 (counter), and actively hurts
longs on patient exits. ONE menu addition pre-registered for Oct 8:
  C) fence5 counter + r0=3 + delay=600 (LS)
     +$29.20/wk, fixed-sizing survival 74%/2% (3-seed range 73.5-75.4
     / 1.8-2.4) - no ladder dependency. Deployment needs a pending-
     entry state machine in the executor (~15s cancel-check loop).
Menu stays: A) fence5+r0=3+TB5000, B) fence8+TB5000, C) as above.
NOT deployed; diagnostic book unchanged.

## Appendix AE — Official spec recon, vol regimes, and the engine split (P16/P17, Sep 16)

Trigger: Deriv Academy's official Flip article (user-supplied). Three
threads came out of reconciling it against 13.4 days of ticks.

1. OFFICIAL SPEC vs MEASURED MODEL - agreement everywhere it matters:
   clock "1 large move / N ticks on average" (= corrected 86,400/N
   rate), "average is not a countdown" (= memoryless arrivals, no
   clock edge), "no counting of ticks can tell you when the next move
   or turn arrives", both directions on one symbol, 24/7 RNG. The
   article's only structural claim we had not tested: "you can see
   which [flip] happened by whether the GRIND changes direction".
2. P16 GRIND-DIRECTION DETECTOR (the article's oracle): phase =
   sign of a W-tick drift sum with hysteresis. BEST cell +$10.58/wk
   (fence8 W=2000 theta=10, counter) vs fleet fence8 +$28.83/wk -
   the official oracle LOSES ~3x to the spike-vote engine at every
   setting tried; with TB5000 patience it goes NEGATIVE (-$1.30,
   ruin 72-78%). Tombstone: the grind oracle is slower AND noisier;
   drift-sums are dominated by ordinary-tick autocorrelation, which
   is exactly the noise fence8 removed. (Also independently
   confirms: there is no better phase oracle hiding in the drift.)
3. RNG STATIONARITY -> VOL-REGIME DISCOVERY: the P2 hour-of-day
   minor-density swing (17.7x, "suspect" vs the article's uniform
   RNG) does NOT replicate across archive halves (rank corr -0.23).
   Root cause found: ordinary-tick scale runs in REGIMES - dead at
   0.0085u for 9 days, 1.39x on Sep 12, normal Sep 13, then 2.98x
   for exactly Sep 14-15 (the two bleed days), normal since. Against
   each regime's OWN median the 5-8x band is EMPTY (7 and 1 minors).
   The "minor floods" (27/1k votes on Sep 14-15, 8,757 half-minors)
   were ordinary high-vol ticks misclassified by the FROZEN warmup
   median. Quiet-entry's mechanism is vol-regime filtering, not
   absorption; the hour-of-day table was regime drift x frozen fence.
   Sep 15's live-vs-archive contrast (-$0.06 vs -$15.13) is now
   largely explained - see (4).
4. ENGINE SPLIT (P17, deployment-critical): the live executor's
   spike fence uses a ROLLING median of the last 5000 deltas
   (WINDOW=5000 deque, np.median per tick, ~83-min memory); every
   backtest froze the median at warmup. The live machine has ALWAYS
   run a different detector than the archive numbers meant to
   confirm it. Re-run (exact live semantics, bisect window):

   census fence5 votes/1k:   frozen -> rolling
     Sep 12:  6.39 -> 0.78    Sep 14: 27.15 -> 0.74    Sep 15: 27.88 -> 0.56
     normal days unchanged (~1.0-1.2). Flood eliminated.
   Oct-8 cells, frozen vs rolling (LS net/wk):
     fence5 counter: +13.67 -> +28.54  (= fence8's +28.83, 273 vs 270 trips)
     fence5 TB5000:  +9.63  -> +19.10
     fence8 counter: +28.83 -> +28.64  (unchanged)
     fence8 TB5000:  +23.26 -> +23.07  (unchanged; day-tables identical
                                       except Sep 14: +0.49 vs +0.12)
   Filter interactions under rolling: quiet r0=3 is an EXACT no-op
   (273 trips, same net) - the rolling median IS the root-cause fix
   and the live machine never had the disease. delay600 still stacks
   a little (+29.35 vs +28.54). Survival (LS, fixed 0.1, 3 seeds):
   f5-rolling counter +d600: 68.5-69.5% flip / 5.4-5.5% ruin.

CONSEQUENCES:
  - fence8 is CONVENTION-ROBUST: identical under frozen and rolling
    (jumps are jumps in any scale). Its archive numbers are
    live-valid as measured. This is why it should anchor the menu.
  - rolling-fence5 ~= fence8 in all conditions measured; the MULT=5
    executor is already closer to menu B than frozen backtests
    implied. Any Oct 8 comparison of live trips against frozen
    fence5 references would have mis-scored trip counts and WR -
    resolved now, before the gate.
  - The Oct 8 menu is re-based on the rolling (live) convention:
    B) fence8+TB5000 (+$23.07 LS rolling; ladder numbers from
       Appendix AA stand, convention-robust) - FRONT-RUNNER.
    C) rolling-fence5 counter + delay600 (+$29.35 LS; fixed-sizing
       survival ~69%/5.5%) - alive but weaker than the frozen
       reading; needs the pending-entry state machine.
    A) fence5+r0=3+TB5000 - DISSOLVED: r0=3 is a frozen-engine
       artifact patch; the live detector never sees the flood it
       filtered. Retired from the menu.
  - Frozen-median backtests remain valid ONLY on days where the
    regime matched warmup (Sep 3-11, 13); any future study must
    state its median convention. The fleet engine stays frozen
    (historical comparability); rolling cells are reproduced via
    analysis/flip_rolling_engine.py.

## Appendix AF — Menu-C delay stress under the rolling convention (P18, Sep 16)

AE's candidate C quoted DELAY600 from a single frozen-era reading.
Full stress (analysis/flip_delay_rolling_stress.py, rolling engine):

S1. K-SWEEP (LS deltas vs d0, net/wk):
    d60 +0.14  d120 +1.44  d180 +2.48  d300 +4.06  d600 +0.81
    d1200 -13.25  d2400 -13.19. Coherent HUMP peaking in the
    d180-d300 band, cliff beyond d600. Not an isolated spike; and
    d600 specifically sits on the decline - the frozen-era d600 peak
    was window luck.
S2. SIDE SPLIT: the hump exists on L too (+4.08 at d300) but the
    level is carried by S (S is where most of the +$/wk lives).
S3. DAY CONCENTRATION of the d300-vs-d0 DELTA: total +$7.74,
    7/14 days positive, top-2 = 125% - the ADD-ON is thin and
    noisy. But the ABSOLUTE d300 book is broad (worst day -7.59 vs
    d0's -10.53; the delay is risk-shaping more than profit-boosting:
    ruin 15% -> 4% at fixed sizing).
S4. HALF REPLICATION: d300 delta reproduces in BOTH halves
    (half1 +3.83, half2 +4.29/wk). d600 flips sign (-2.65 vs
    +4.28) - d300 is the mechanism; d600 was half-sample luck.
S5. SURVIVAL, fixed 0.1, 7 seeds (LS):
    d0   62.4-64.9 flip / 14.1-16.3 ruin
    d180 78.8-80.2 / 2.7-3.1
    d300 81.3-83.3 / 3.9-4.7
    d600 71.7-73.8 / 5.2-6.0

MENU RE-POINT (rolling convention, LS - the book the gate scores):
  C) fence5(counter, rolling) + DELAY300:
     +$32.60/wk | fixed 0.1: 81-83% flip / ~4% ruin (7 seeds) |
     ladder 0.1->0.4: 92-93% flip / 6.3-7.2% ruin (3 seeds).
     NEW FRONT-RUNNER. Caveats recorded: add-on delta thin (+$4/wk,
     top-2 days 125% of it); K inside the 180-300 plateau is not
     pinned (300 chosen as mid-plateau max-survival, both-half
     replicated); needs a pending-entry state machine in the
     executor (cancel on opposite spike, ~5s check loop).
  B) fence8 + TB5000: fixed 47.5% flip / 16.5% ruin; LS ladder
     65.5-66.5% / 32.6-33.8% - DEMOTED. Its earlier "~86% ladder"
     (Appendix AA) was a long-side, frozen-era framing; under the
     LS live-book framing it loses to C on net, fixed-survival,
     AND ladder-survival. Fence8 remains valuable as the
     convention-robust detector fact (AE), not as the deployment.
  A) retired (AE).

### Appendix P addendum — settle fired Sep 17, n=50: escalation NO

`flip_side_split.py` hit its pre-registered n>=50 gate (26 longs / 24 shorts).
Live: longs −$4.26 total (WR 38%, avg −$0.16) vs shorts +$1.58 (WR 50%,
avg +$0.07). Verdict per the pre-committed two-prong rule: **NO escalation** —
(a) long weakness is not significant (10/26 wins, sign-test p=0.163 > 0.05),
and (b) the matched-window backtest shows the SAME ordering (long avg −$0.01
vs short avg +$0.02), i.e. the window itself was short-friendly. Consistent
with the Sep 15 interim read: regime artifact, not an execution defect. The
Sep 10–15 archive's short-friendliness persists into the live window; the
Oct 8 gate's 28-day averaging is the remedy, not a code change. Consequence:
decision-rule item 3 is discharged; the long-only branch is NOT blocked
before funding. Scorer convention note (P17): the settle's backtest reference
uses the frozen warmup median; under the rolling (live) convention the
differences shrink further (rolling removes false-minor noise), so the
verdict is conservative as stated.

### Appendix Y addendum — first live P3 scoring (Sep 17): quiet-entry evidence is real but starved

First scoring run of the stamped book (`flip_telemetry_score.py`): 12 stamped
trips since 2026-09-15 18:49Z, of which **4 carry min_2k** (P3's accumulator
went live Sep 16) — below the pre-registered n-gate of 8, so P3 is not
scoreable yet. What the 4 rows say so far: all quiet (min_2k=0), avg −$0.15;
no burst rows exist to compare. Two structural findings from the first
scoring week:

1. **AE's burst-rate prediction is confirmed on the full archive under the
   live convention.** Rolling-median census (executor-exact semantics,
   `rolling_medians` + cache `data/flip_exec/_rolling_med_flip1000.npz`):
   1,332 spikes, 80 minors = **6.01% minor share** vs 27/1k-tick flood rates
   under the frozen convention on Sep 14-15. Burst days (>=10% minor share)
   are rare and isolated: Sep 11 (22.8%), Sep 14 (14.7%), Sep 17 (50.8%) —
   3 of 15 archive days. The live machine's detector naturally suppresses
   the flood disease quiet-entry was built for, exactly as AE said.

2. **Today (Sep 17) is the biggest burst day on record under the live
   convention (50.8% minor share) — yet both live entries stamped min_2k=0.**
   Entry-level quiet (trailing-2000-tick window ~33 min) is much rarer than
   day-level burst: floods cluster, entries land in the gaps. Implication
   for the gate: P3's burst bucket will fill slowly (maybe a handful of
   rows by Oct 8); the min_2k x entry-outcome table may never reach n>=8
   per bucket by the gate. If so, the honest fallback is the pre-registered
   one — decide quiet-entry's fate on the frozen-engine archive record
   (Appendices Y/Z: real but a frozen-median artifact patch, and a no-op
   under rolling per AE) rather than on live P3 evidence that never
   accumulated. No action now; watch bucket counts at the gate.

### Appendix Y addendum 2 — P3 scoring recipe (Sep 17)

Baseline for future scoring runs (avoids the 9-min rolling-median recompute):
cached medians `data/flip_exec/_rolling_med_flip1000.npz` (key: med, n must
equal len(px)); census under executor-exact semantics: 1,332 spikes / 80
minors / 6.01% share, burst days Sep 11/14/17 (22.8/14.7/50.8%). If the
archive grew since, recompute the cache (n mismatch triggers automatically).

### Appendix AD addendum — P19: the "tiniest spike" entries, re-checked on the live convention (Sep 17)

User complaint (recurring): the executor enters immediately on the tiniest
spikes and those lose. MAG's tombstone predates the two-population finding
and the rolling re-base, so it was re-asked under executor-exact semantics
(`analysis/flip_mag_rolling.py`, clone validated BIT-EXACT vs run_conv:
n=290, net +$46.04 = +$22.50/wk LS counter).

**Census + per-band EV (rolling fence5, LS counter, 14.3d):**

| entry-x band | n | WR | net | /wk |
|---|---|---|---|---|
| x<40 (<0.32u) | 6 | 67% | +$3.27 | +$1.60 |
| 40-60 | 6 | 50% | −$3.32 | −$1.62 |
| 60-100 | 7 | 57% | −$0.11 | −$0.05 |
| 100-300 | 36 | 61% | +$8.72 | +$4.26 |
| 300-800 | 92 | 53% | +$18.78 | +$9.17 |
| x>=800 | 143 | 46% | +$18.70 | +$9.14 |

**Verdict: the small-jump filter does not pay.** Skipping x<T at any
threshold T in {40,60,80,120} REDUCES net/wk (−$0.47 to −$4.27); no
threshold clears even +$0.50/wk, so the referee never ran. The x<40 band is
actually +EV (WR 67%); the only weak band (40-60) is 6 trips. MAG's
tombstone survives the convention change: entry size still does not predict
outcome under rolling — the edge is in the vote, not the jump.

**Why the eye disagrees — two real effects on record, not denied:**
1. **Live trips lose more than backtest trips on average** (−$0.33 stamped
   avg vs +$0.08 backtest) — the live x-distribution is bottom-heavy vs the
   archive's (backtest bands hold 92+143 = 235 trips at x>=300 = 81%, live
   stamped book holds ~5/14 = 36%): a small-n snapshot in a short-friendly,
   burst-heavy week, not proof of a filterable defect.
2. **"Tiny" is regime-relative under rolling** — fence8's min entry_x is 12:
   a ~0.1u absolute jump CAN be a true class-8x vote when the rolling median
   is low. The detector votes on class, not on visual size. The immediate
   entry (phase age 1) is the design — P15/P17 proved delay300 only as menu
   C, and the frozen book stays instant-entry for a clean gate.

**Status:** filter not adopted; telemetry keeps stamping entry_x so P19's
live verdict re-arms automatically as the stamped book grows. Menu unchanged.

## Appendix AG — LI-1: book-integrity incident, the SL loss the book never recorded (Sep 16–17)

Found Sep 17 from a user report: "my pc was asleep when the stoploss got hit
so it didn't record it." The report was exactly right.

**Anatomy.** Sep 16 14:48:53Z the executor opened a short @ 9400.31430
(ticket 8770365839). The PC slept shortly after (heartbeat gap 195m → 388m).
The broker-side 60u backstop SL fired at 18:10:06Z (deal 8652044438, buy 0.1
@ 9460.29910, pnl −$6.00) while the process was frozen. On wake, the tick
flush hit a counter-spike, positions_get found nothing, and the spike-close
path's else-branch did `pos_model = None` — silently. No booking, no row, no
print. The GAP path had the same hole. Result: the book overstated equity by
exactly $6.00 and every number after 18:10Z Sep 16 was wrong. The 15-second
man-sync never caught it because the flush processed the whole backlog in
seconds, before the next heartbeat.

**Repair (scripts/heal_ledger_slfail.py, idempotent, backup-first).**
v1 bug, caught by the verifier within a minute: appending the EXIT row at
file end let chronological pairing mispair the live 11:44 ENTRY with the
Sep-16 EXIT (a +$9.59 phantom trip). Backups restored; v2 inserts the EXIT
at its chronological slot. Healed book: Sep 16 day PnL −$6.97 (was −$1.70).
Side-split settle re-run at n=52: verdict unchanged, NO escalation (p=0.163).

**Executor fix (eval/flip_executor.py), three changes:**
1. `book_broker_close()`: when pos_model exists but the broker no longer
   holds the position (now handled on ALL paths — spike close, GAP path,
   man-sync), the matching OUT deal is pulled from `history_deals_get` by
   position ticket (fallback: price match ±0.05u) and the REAL pnl is booked
   with a full EXIT row + equity state save. Unit-tested with a synthetic
   MT5 module (ticket match, price fallback, graceful False on no deal).
2. Equity continuity: `model_eq` restores from the ledger at startup
   ([BOOK] equity restored from ledger: $X over N trips) instead of resetting
   to $10.00 every session — which had masked the damage for a full day.
3. Honest startup floor: if restored equity ≤ $4 (EQUITY_FLOOR), the
   executor starts halted ("floored": true in day_state.json) and refuses
   entries until manual review clears it. Previously the floor could never
   fire because equity was session-local.

**Full reconciliation (Sep 17, post-heal).** `history_deals_get` over the
whole archive range, MAGIC 620001: 64 in / 63 out deals, broker pnl −$12.06.
Out-deal reasons: 62 EA-closes, **exactly 1 SL** (the healed deal), nothing
else — the heal is complete and no further unbooked closes exist. Arithmetic
closes exactly on every independent path: side-split trips −$11.44; +probe
close −$0.06 (flip_probe manual, probe_slip.csv) + pre-ledger Sep-10
exploration deals −$0.56 = −$12.06 broker truth.

**State after LI-1.** Model-book equity **−$0.18 over 52 trips — the $10
book is dead by its own accounting**, killed Sep 17 13:39Z by a −$2.76 close
inside the 2.9× vol regime that began Sep 14 (the same regime behind the
frozen-engine flood artifacts, AE). The executor is running with all three
guards live and correctly staying halted (FLOOR + DAILY-HALT). The demo
account itself is untouched ($1,540 balance — the model book is a ledger,
not funded money).

**Lessons on record.** (a) The 60u backstop SL is load-bearing and fired
exactly once — a book that doesn't watch broker truth loses its mind the
first time it does. (b) Broker-truth reconciliation (deals vs ledger) should
be a startup ritual, not incident response. (c) Session-local equity made
every guard lie; continuity is now structural. (d) Chronological ledgers
take INSERTS, not appends — the verifier earned its keep.

## Appendix AH (Sep 17): Out-of-sample test of menu C — first true holdout, and it failed

**Setup.** The Oct 8 menu (AE/AF) was frozen when `flip_delay_rolling_stress.py`
was last written: **2026-09-16 16:14:30 UTC**. Ticks archived after that line
are data the menu never saw: **Sep 16 16:14Z → Sep 17 09:45Z, 0.73d, 97,883
ticks at 1.55× spec-clock activity** — the tail of the elevated-vol regime
plus Sep 17, the biggest minor-burst day on record (50.8% minor share).
Soft-peek disclosure: P19 (Sep 17) saw these ticks, but never touched delay K
or the menu (its verdict was "no filter"); selection remains clean. Engine:
C-clone validated **bit-exact** vs `run_conv` at d0 and d300 before any number
was trusted (n=290/$+46.04 and n=264/$+55.38 full-archive).

**S2 — holdout P&L (rolling, LS).** Every cell lost. d0: **−$6.86** over 14
trips; C d300: **−$5.87** over 12; B fence8+TB5000: **−$4.88** over 12.
Against IS rates of +$22–32/wk, the holdout ran at −$47 to −$66/wk-equivalent.
Per side: the damage is short-side (C shorts −$4.92, worst trip −$3.85; d0
shorts −$6.50, worst −$4.13); longs roughly flat (C −$0.95, d0 −$0.36).
Not one-trade-owned: C's top-2 losses are 114% of the net (profits existed —
best trip +$3.26); d0's are 103%.

**S3 — burst attribution.** Only 1 of C's 12 holdout entries was minor-class
(x<8): −$1.25. The other 11 were true-jump entries (x≥8, net −$4.63, WR 36%).
**The burst did not poison the detector — the phase entries themselves lost.**
The AE story ("rolling self-corrects, true jumps carry the signal") held
mechanically; the *edge* did not show up in this window.

**S4 — stitched survival (day-block, IS days + OOS cluster days per 28d sim).**
The holdout contains only 2 partial days, so the OOS days are a *regime
assumption*, swept honestly:

| f(cluster) | C fixed | C ladder | d0 fixed |
|---|---|---|---|
| 0% (pure IS) | 80–81% / 4.2–4.8% | 91–92% / 6.9–8.3% | 60–61% / 14.9–15.6% |
| 7% | 70–71% / 5.8–7.0% | 88–89% / 9.0–10.3% | 48–49% / 15.8–18.9% |
| 14% | 53–55% / 9.1–10.3% | 82% / 14.1–14.6% | 36–38% / 19.2–21.9% |
| 25% | 28–30% / 16.6–17.6% | 66% / 24.8–25.6% | 16–19% / 29.6–31.1% |
| 50% | 1% / 50.2% | 16–17% / 67.6–68.3% | 0% / 67.4–69.2% |

**Interpretation (pre-committed discipline applied).**
- The holdout is a **regime stress test, not a verdict**: 2 partial days
  (n=12 trips) cannot overturn 14 IS days at n=264. A −$5.87 stretch is
  inside the IS worst-day distribution (Sep 15 lost more than that in the
  frozen replay). No K re-pointing, no menu change from this alone.
- But it **kills the point estimates as promises**. AF's "81–83% flip / 4%
  ruin" assumed *zero* days like Sep 16–17. The honest statement is now a
  function of cluster frequency: C-ladder survives ~82–89% only if
  regime-cluster days run ≤~14% of the calendar; at 25% it's ~66%; the pure
  deterministic-path worst case (50%) is ruinous for every config including
  the incumbent. The book that just died (LI-1) is the realized 50%-column
  trajectory — three of its last four days were cluster days.
- **C dominates d0 in every cell of the table** — the delay's risk-shaping
  claim (AF) replicated OOS at the survival level even while the raw edge
  went negative. That is the part of AF that survives contact with the
  holdout.
- **B's OOS cell was the mildest** (−$4.88, WR 58%) — consistent with its
  fence being convention-robust (AE), but n=12; nothing to re-rank on.

**Consequences.** (1) Menu point estimates must be quoted with the
cluster-frequency row attached, never alone. (2) The LI-1 three-way decision
gains a data point: a reseeded diagnostic on d0 or C should *expect* weeks
like this one, and the $4 floor + ladder de-sizing is the only defense —
which is exactly what the architecture provides. (3) If cluster frequency
is even ~25%, the $10→$100 mission has no positive-expectation sizing
mechanism on record; the 28-day gate must measure cluster frequency itself
(count regime-cluster days as a first-class statistic) before any funding
decision. (4) The Vol-Regime Monitor idea graduates from nice-to-have to
**pre-registration candidate**: if a vol/activity heartbeat can flag cluster
days in real time, the response is de-sizing, not entry suppression (entry
filters were tombstoned; de-sizing was never tested — that is the one new
study this appendix licenses, P21, pre-registered before any run).

## Appendix AI (Sep 17): Live-vs-engine PnL bridge — where the $0.02/trip went

Question after LI-1: is the live edge really ~$0.02/trip (signal dead live)
or was the week an infrastructure failure? The collector archived the SAME
tick stream the executor traded, so the validated rolling engine (bit-exact
d0 clone) can be run over the exact live window (Sep 10 17:04Z → Sep 17
13:39Z) and the difference attributed trip-by-trip. Script:
`analysis/flip_live_gap_audit.py`. Bridge closes to the cent.

| book | n | net | WR | avgW/avgL |
|---|---|---|---|---|
| engine, live window (0.1 lot) | 133 | **+$21.32** (+$0.16/trip) | 56% | +$1.46 / −$1.47 |
| live actual | 53 | **−$10.18** (−$0.19/trip) | 43% | +$1.13 / −$1.21 |

**Bridge decomposition (gap = −$31.50):**
1. **Availability: −$21.08.** The engine found 133 trips; live took 53 —
   it missed 92 (69%), and the missed set nets +$21.08, i.e. essentially
   the ENTIRE engine edge. Concentration: Sep 12 (+$19.59, n=15 — sleep
   wedge started 23:10 + 4+ executor restarts that day), Sep 13 (n=18,
   mostly the 20h sleep), Sep 15/16 (+$13.05, halts + wake gaps). Some
   Sep-13/14/17 missed trips were negative — being offline sometimes
   helped — but the net is the edge, unclaimed.
2. **Stale/extra live trades: −$4.83.** 12 trips live took that the engine
   wouldn't: post-wake ADOPT/stale-phase entries and the post-gap
   re-entries the backtests' gap-mask refuses. Worst: Sep 17 11:44 short
   −$2.76 (entered after a 41h data gap, "phase age 7" class).
3. **Same-trade execution: −$5.59 over 41 matched pairs (−$0.14/trip).**
   Even on shared entries live underperformed: exit-timing divergences
   (live closed at tiny −$0.06 counter-moves where the engine held for
   +$1.2–1.5 — restart-reseeded median/phase state), and the Sep 16 SL
   trip (live −$6.00 backstop vs engine's −$4.13 counter-spike exit).

**Fill friction is a non-issue (confirms the GO verdict):** total adverse
slippage on 106 fills = −$0.0092 (entries −$0.0133, exits +$0.0041). The
executor filled at tick-quality prices all week. The engine's flat
$0.0626/trip spread model slightly OVERSTATES live costs.

**Shape finding:** the celebrated 17:1 win/loss ratio is a frozen-era
long-only framing. In the live window under rolling LS, even the ENGINE
runs ~1:1 (+$1.46/−$1.47) with the edge carried by WR (56%). Live's 43% WR
with the same 1:1 shape = negative EV. The mechanism to monitor in a reseed
is win-rate and hold-time on winners, not loss sizes.

**Verdict.** The week's death is ~2/3 infrastructure (the executor was
out of the game or state-corrupted for the majority of engine opportunities)
and ~1/3 regime (the engine's own window edge, +$0.16/trip, is below the
full-archive +$0.235 — the regime cost ~1/3 of the expectation too). The
signal survived contact with the live stream; the executor did not capture
it. The fixes already deployed for LI-1 (broker-truth booking, gap guard,
stale-tick suppression, equity continuity, floor) target exactly buckets 1
and 2; bucket 3's restart-state divergence argues for a no-restart discipline
on any reseeded diagnostic. This appendix licenses NO config change — it
licenses the reseed decision with eyes open.

## Appendix AJ (Sep 17 evening): Reseed — book v2 on Menu C, no-restart discipline

Decision (user-approved): reseed. v1 book (−$0.18, 52 trips) archived at
`archive/book_v1_dead/` with its reconciliation intact. What v2 runs and why:

- **Config C** (AF): rolling-fence5 detector (executor-native), pending-entry
  state machine — a qualifying spike arms a 300-tick pending; an opposite
  qualifying spike cancels it; at delay it fills at market. Live-safety
  deltas vs the backtest engine, all documented in-code: arm suppressed while
  stale or day-halted; a pending blocked past DELAY+600 ticks expires
  (never fills on stale context); same-dir cancel/refresh handled per
  run_conv (no refresh; cancel only on opposite).
- **State persistence** (AI bucket 3): phase (cur_dir/cnt) and the tick
  cursor + median save to `engine_state.json` on every heartbeat and restore
  on startup ONLY if freshness (saved cursor overlaps the archive's newest
  tick within 5k) and calibration (saved median ≤ 3× warmup median) pass;
  otherwise flat start. A restored pending is ALWAYS dropped — it would act
  on stale context. Restarts can no longer manufacture "phase age 7" entries.
- **Uptime instrumentation** (AI bucket 1): every heartbeat counts
  stream-currency (last tick < 120s old) into `hb_current ok/total` and
  writes `uptime.json` with tick/spike counts and the pending ledger
  (armed age, cancels, fills, expires). Availability is now a measured
  number; the AI audit's 69%-blind figure becomes the baseline to beat.
- **No-restart discipline**: mid-flight restarts corrupt state (AI bucket 3).
  Policy: restart only on a verified fault (STALL/RECONNECT loops, dead
  process via checkpoint), and note every restart in the log — restarts are
  measurement events now, not silent resets.
- **Verification at deploy:** banner prints the Menu C line; fresh book
  restores $10.00; `[STATE] no prior state - flat start` (correct for a
  reseed); first heartbeat shows `cur=1/1`; median 0.025 confirms the
  elevated regime is still priced into the detector at startup.

**Gate:** Oct 15 readout. Score: (a) live $/trip at full stream-currency,
(b) cluster-day frequency (AH), (c) pending fate mix (fills/cancels/expires
vs run_conv's archive rates). Decision rule unchanged in structure: confirm
only if live $/trip is positive and materially consistent with the rolling
backtest at the gate's uptime; otherwise do not fund.

### Appendix AK (Sep 18): v2 first checkpoint — scoring n=15 vs the config-C engine (same window)

First gate-template scoring of book v2, run at n=15 (script:
`analysis/flip_v2_score.py`, C-clone validated BIT-EXACT vs run_conv at
d300 before any number was trusted). Window: Sep 17 19:38Z → Sep 18 19:27Z,
~1.0d, mean rolling median 0.0106u (mild elevation, not the Sep 14–17
0.025u crisis regime).

| book | n | net | $/trip | WR | avgW/avgL | worst |
|---|---|---|---|---|---|---|
| live v2, all | 15 | **+$10.35** | +$0.69 | 73% | +1.09/−0.42 | −$1.23 |
| live v2, auto exits only | 11 | +$2.51 | **+$0.23** | 64% | +0.60/−0.42 | −$1.23 |
| live v2, manual closes | 4 | +$7.83 | +$1.96 | 100% | — | — |
| engine C-clone, same window | 22 | +$8.86 | +$0.40 | 68% | +0.89/−0.65 | −$1.53 |
| engine C-clone, full archive (AF expectation) | 291 | +$56.88 | +$0.195 | — | — | — |

**Bridge:** 15/22 engine fills matched live (tol 240s), engine-only 7
(net **−$2.03** — the missed trades LOST money this window), live-only **0**.
No stale adopts, no post-gap junk entries: the AI bucket-2 defect is gone
in production. Matched execution delta −$0.76 total (−$0.05/trip),
concentrated in the very first v2 trip (live rode a long to a −$1.23
counter-exit vs the engine's −$0.47); the other 14 matched trips within
±$0.01 except the manual closes (+$0.22, +$1.02 — user discretion, booked
correctly). Book arithmetic closes to the cent: $10 + Σpnl = $20.35 = the
executor's equity note.

**Gate-stat readings (AJ template):** uptime 211/213 = **99.1%**
stream-current (the AI-audit 69%-blind availability hole is closed — live
captured 68% of engine fills, and the 7 misses were signal divergence, not
downtime: the log shows no guards/halts/gaps in the window, the executor
simply armed 18 pendings vs the engine's 22 entries on marginal ticks).
Pending fates: 18 armed / 15 fills / 3 cancels / **0 expires**; 13/15 fills
at phase age 1; **0/15 minor-class entries** — the "tiniest-spike instant
entry" complaint is structurally absent under C (a pending fills only if
the phase vote survives 300 ticks).

**Honest read.** The headline ($+0.69/trip, 73% WR) is inflated by manual
closes (76% of net); the machine-only number is +$0.23/trip — above the AF
full-archive expectation (+$0.195) at n=11. Nothing here is proof: v1 was
+$3.30 at n=13 before dying. What IS established at this checkpoint:
availability fixed, book arithmetic closes, the delay machine behaves as
designed, and missed trades cost −$2.03 rather than the v1 pattern of
missed trades costing the whole edge. No config change licensed.

**Infrastructure incident found by this study:** the tick collector
(`data/flip_tick_store.py`) had been dead since Sep 18 02:30 — 44h of
tick gaps that would have made the engine benchmark unbuildable at the
gate. The store's 72h broker-rebuild window self-healed all four symbols
(~124k ticks each) when re-run, but broker history depth is finite: the
collector is now keepalive-covered (`FlipTickCollector-Keepalive`, every
30 min, freshness-gated, verified by force-run; wrapper
`scripts/flip_store_keepalive.cmd`). Lesson: the gate's benchmark is a
live dependency, not an archive — it needs its own watchdog like the
executor has.

### Appendix AK addendum (Sep 18 night): the 7 missed fills were SLEEP, not signal divergence

Follow-up probe (`analysis/flip_missed_fills_probe.py`): the 7 engine-only
fills (all 07:28–12:41Z) sat on huge, unmissable spikes (x 406–2075 at
delta 3.5–18u vs threshold 0.043; the engine replay shows 27 qualifying
spikes 06:30–13:30Z, none marginal). The executor's session log contains
**zero lines between ~06:46 and ~13:40Z** — the single session carries a
**+434.6m heartbeat gap** (plus an earlier harmless +17.2m overnight gap).
The PC slept from shortly after the 06:13Z close until the user woke it
(~13:40Z; battery playbook was active — on battery, DC idle sleep fired
as designed). The engine's 06:30–13:30 window netted −$2.03 across those
7 fills, so missing the night was, this once, +$2.03 BETTER than trading
it — but that is luck, not a property to rely on.

**Two durable findings:**
1. **`hb_current` (stream-currency) is blind to freezes.** A suspended
   process emits no heartbeats, so a 7h sleep scores as 99% currency.
   `flip_v2_score.py` now also computes **wall-clock coverage** from HB
   gaps: 25.5h span, 2 freezes, ~7.36h frozen → **real uptime ~71.1%**, not
   99.1%. The Oct 15 gate's "full stream-currency" criterion uses the
   wall-clock number; hb_current remains the when-awake network-health
   metric. Both are reported from now on.
2. **No executor or config defect is licensed.** Post-wake behavior was
   textbook: backlog suppressed entries, no stale adopt, first post-wake
   fill at 13:44Z on a fresh arm (+$3.45 when manually closed). Overnight
   coverage on battery is a power-physics constraint (AC = awake; battery
   = sleep by design); if the user wants overnight fills, the machine must
   be on charge — there is no software fix that both keeps a battery
   laptop awake all night and protects the battery.

**Side-split settle - auto-recorded by the daily checkpoint 2026-09-27 20:34 UTC (first run at n>=50):**

```
--- settle ---
  SETTLE (n=51): live long-avg $+0.27 vs bt long-avg $-0.22; live short-avg $-0.30 vs bt short-avg $-0.38
  inversion still present: NO (live long sign-test p=0.932, not significant)
  Appendix P escalation (defect, not regime): NO
```
