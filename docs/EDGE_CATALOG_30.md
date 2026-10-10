# 🗂 EDGE CATALOG — 30 Solutions, Concepts & Contingencies

**Date:** 2026-09-03 (autonomous research session)
**Rule:** every empirical claim below was measured on the *corrected* M1 engine
(`eval/partial_m1_corrected.py` / `brain/engine.py` — no entry-bar lookahead,
real gap-through SL fills, OOS halves). Statuses are honest: `VERIFIED`,
`DEPLOYED`, `CONTINGENCY`, `PROMISING`, `KILLED`, `ANTI-IDEA`. KILLED does not
mean worthless — it means *measured and refuted*, which is itself the checks-
and-balances this project runs on.

Symbol keys: c3=crash300, c5=crash500, c1k=crash1000, b5=boom500,
b1k=boom1000. Baseline configs (the verified playbook): c3/c5/b5 = gate 8×/3
bars, hold 2, cd 1, partial; c1k = entry+3, hold 5, cd 3; b1k = entry+2,
hold 2, cd 3. All fades (long after crash spike, short after boom spike).

---

## A. THE VERIFIED CORE (run these; they are the book)

**1. Partial entry 50/50 with add-on-profit — VERIFIED (the whole edge)**
Half A enters at close of spike+1; half B joins 1 bar later only if A is
still open and profitable. Converts full-fade PF 0.81–1.00 into PF 1.2–2.6
per symbol with drawdowns ÷4–5. Engine control numbers today: c3 2.19,
c5 1.65, b5 1.86, c1k 1.43, b1k 1.69. Hook: `UsePartialEntry=true` (all
symbols) — already in the EA.

**2. Big-cluster gate 8×/3 bars on the 300/500s — VERIFIED**
Enter a fade only when the spike is ≥8× median AND another ≥8× spike fired
within the prior 3 bars. Window 3 beat window 5 on 5/6 OOS half-cells; the
8× cutoff is 300/500-series-specific (the gate is harmful on the 1000s).
Hook: `UseBigClusterGate`, auto-off on 1000 indices — already in the EA.

**3. Loss cooldown — VERIFIED, per-chart tuned**
P(loss | prior loss) = 1.26–1.52× on gated trades — losses cluster, and
clustering survives the gate. Gated charts: cd=1 (skip only the immediate
post-loss spike — best OOS). 1000s: cd=3 (needed for 2nd-half robustness,
c1k val PF 1.30 vs 1.09 at cd=0). Hook: `LossCooldown` per chart.

**4. Short time-in-market — VERIFIED**
c3/c5/b5/b1k hold 2 M1 bars; c1k hold 5. Wins are timeout-driven (riding
the recharge drift); TP geometry barely binds; every extra bar adds only
gap exposure. c3 gated hold sweep today re-confirmed: hold 2 OOS 2.38/1.99
beats hold 3 (2.09/1.75) and hold 4 (1.60/1.74); hold 1 has similar PF but
~1/3 less PnL (timeouts need a couple of bars to pay).

**5. Entry delay is a per-symbol structural fact, not a knob — VERIFIED**
c1k enters 3 bars after the spike (OOS PF 1.27/1.29 → 1.45/1.40) and b1k
2 bars (1.30/1.60 → 1.58/1.80). Measured mechanism: after a c1k spike the
market keeps drifting **down** for ~3 bars (mean −0.02/−0.11/−0.13 over the
next 3 bars) — entering at +1 means entering mid-dump. On the 300/500s the
same delay *hurts* (drift turns in your favor immediately), so they stay at
+1. Delay is symbol-specific; do not uniform it.

**6. EA v2.11–v2.13 hardening — DEPLOYED (compile pending on your side)**
Same-symbol instance lock (kills the 13:46 double-entry class), all-position
close with broker-realized PnL (no more orphaned partial halves), risk sized
on the distance stops actually attach at (2% is really ~2%), tick-safety
calibrated per symbol, 60-bar median warm-up, `EntryBar`/`MaxHoldBars`
inputs live (they were dead code before v2.13).

---

## B. NEW STRUCTURAL FINDINGS (measured today)

**7. Run-FINAL fades are PF 6–9; run-MID fades are PF 0.13 — the anatomy of the gate**
Engine-trusted buckets of the gate's own trades:
| | run-final entries | run-mid entries |
|---|---|---|
| c3 | n=154, PF **3.47**, WR 83%, DD 7.1 | n=13, PF **0.24**, WR 46%, DD 39.8 |
| c5 | n=275, PF **8.93**, WR 93%, DD 2.6 | n=49, PF **0.13**, WR 35%, DD 85.7 |
| b5 | n=258, PF **6.21**, WR 89%, DD 6.2 | n=39, PF **0.13**, WR 31%, DD 90.7 |
Mids are ~15% of trades yet they halve total PnL and triple DD. A fade
entered while a ≥8× cluster is still discharging gets slammed by the
continuation — the same damage that kills every mid-cluster idea.

**8. Mid-run damage is NOT avoidable without lookahead — KILLED (important)**
Tried every executable proxy for "cluster has ended": confirmation delays
of 2–3 quiet bars (V_C2x/V_C3x/V_C3B, enter at close[i+2]/[i+3]), calm
filters before entry (G1: ≤2 adverse spikes in 5 bars; G2: ≤1 in 3 bars),
and isolated-first-big entries (G4/G6). **Every one underperforms the
canonical gate on the 300/500s** (delays forfeit the fade's best drift bars
— c3 recovers +0.28/+0.44/+0.98 over the next 3 bars — and calm filters
move nothing OOS). Conclusion: the 6–9× PF of finals is a *quality ceiling
you cannot reach*; the gate's mix (finals + unavoidable mids) is the best
executable. Do not "improve" the gate with delays.

**9. T3 hindsight ladder → bounded ambition — KILLED as strategy, kept as map**
Run-size-2 finals at +1 (executable only because a 2-run's last member *is*
pos-2): c3 PF 3.46 (n=147), c5 PF 8.07 (n=251), b5 PF 6.81 (n=240) —
engine-trusted. Runs of size ≥3 are only ~2.3% of runs (c5: 46 of 2030),
so the dream (only ever enter the true final spike) is bounded: removing
the 2.3% of long runs would not change the book much; the mids you *can*
remove executably are not removable executably (see #8).

**10. Spike timing AND spike size are memoryless — ANTI-IDEA (final word)**
Size-vs-gap correlation ≈ 0.00 on every symbol (−0.003 to +0.031; gap
terciles flat: c3 7.27/7.20/7.23 ×). Long quiet stretches do NOT produce
bigger spikes. No size-escalation or "rubber-band" strategy has a basis.
Combined with flat hazard (playbook §2) this closes the anticipation door
formally: nothing observable before a spike predicts its timing *or* size.

**11. Drift-bar momentum is mild but real on crashes — MEASUREMENT**
Lag-1 autocorrelation of non-spike bar moves: c3 +0.042, c5 +0.073
(sustained recharge drift), c1k +0.003, b5 −0.003, b1k −0.001. Weak on
c3/c5 — enough to explain why "add while profitable" works on crashes and
why it should never be done into an adverse spike.

**12. Post-spike drift shapes are symbol DNA — MEASUREMENT (design input)**
Mean move over next 1/2/3 bars after an adverse spike:
c3 +0.28/+0.44/+0.98 (strong monotone recharge — fades win fast, hold 2
captures it); c5 ≈0/0/0 (weak — c5's edge is the rarest, highest-quality
gate, thin drift); c1k −0.02/−0.11/−0.13 (keeps falling → entry+3, hold 5);
b5 −0.20/−0.30/−0.30 (post-boom drift down favors the short fade);
b1k −0.41/−0.16/−0.39 (same, violent). Use these to sanity-check any
future entry/hold proposal per symbol.

**13. Third-add pyramiding is dead — KILLED (measured today)**
Adding a 25% third slice at bar +2 when still profitable: PF falls ~30–40%
on every symbol and every OOS half (c3 2.15 vs partial 3.20 twin-runner;
b1k 1.54 vs 2.46). The extra slice only re-adds SL exposure into a drift
that is about to end. The verified add-once architecture is the ceiling.

**14. Fade PnL by entry hour is noise — ANTI-IDEA (re-confirmed today)**
Best/worst hours flip between OOS halves on every symbol (c5 best hours
4/2 in half 1 → 17/22 in half 2). Any hour filter is curve-fitting 30 days
of noise. Run 24/7. (This is the third independent refutation of hour
structure: spike rate flat, cold-hours NOISE verdict, now fade-PnL noise.)

---

## C. RISK, SIZING & THE MONTE CARLO MACHINERY

**15. Kelly-corrected risk fractions — VERIFIED math**
Empirical Kelly f*: c3 7.0%, c5 8.0%, c1k 3.0%, b5 9.0%, b1k 2.0%. Running
2% everywhere = 29%/25%/67%/22%/**100%** of Kelly — b1k at 2% was a
full-Kelly overbet on a −9.6 pt average loss that gaps to ~8× SL. Corrected
fractions: **c3/c5/b5 2.0%, c1k 1.5%, b1k 1.0%** (per-chart `RiskPercent`).
300/500s could afford more but 2% is sane there.

**16. Portfolio Monte Carlo with loss-gap frames — TOOL (eval/portfolio_mc.py)**
Monthly $ distribution over 200k simulated months, day-bootstrap + measured
(shrunken) cross-symbol copula, Kelly-corrected risk. Portfolio EV
**breakeven loss multiplier m = 1.60** (live losses ≥1.60× sim losses → the
book is EV-negative). Per-symbol breakevens: c3 2.19, c5 1.65, c1k 1.43,
b5 1.86, b1k 1.69. Book at m=1.5: median **+$1,677/mo**, P(loss month) 11%,
worst month −$4.3k, intra-month DD p95 $1.8k. At m=2.0: −$6.3k/mo, 100%
losing months. The MC row the live book is on is decided by the measured m.

**17. Live gap-multiplier measurement — TOOL (eval/gap_measure.py)**
Reads the EA's trade CSVs (UTF-16, 12/13-col tolerant), computes realized
loss pts = |exit−entry| from broker fills per losing close — needs no
volume/$/pt/lot — and divides by the engine's sim mean loss to output m per
symbol vs its breakeven. Data status today: ~0 v2.12 losing closes exist;
the two restarted EAs (b1k, c5) began writing fresh files 2026-09-03 15:17.
Re-run after ~30 closes/chart accumulate. Interim live fact: manual EA
exits realize ~1.0× median (c3 1.07 pts, c1k 0.37, b1k ~0.36 at 5 lots) —
*smaller* than the sim's broker-floor losses — while broker-SL-through-
spike fills are the ≥2× tail. The mixture is the number to measure.

**18. Two book profiles — VERIFIED shape (docs/FINAL_VERIFIED_PLAYBOOK §7)**
All-5 @ 5 lots: mid **+$1,700/mo**, σ ±$1,000, worst month can be negative.
1000s @ 2.5 lots, 300/500 @ 5: mid **+$1,000/mo**, σ ±$500, rarely
negative. The 1000s carry the dollar volume AND the tail; halving them is
the honest reduced-swing configuration while their live m is unproven.

**19. Equity-scale reality — RULE**
At ~$1,000–2,000 equity the 5-lot cap binds on every chart (real
risk/trade 0.6–1.2%, not 2%). Risk% only bites once equity lets lots scale;
Kelly-corrected sizing only matters after the cap stops binding (~$600+
per chart at 5 lots it *always* binds). Raise `MaxLots` only when measured
m < the chart's breakeven (#16/#17).

**20. Kelly table as bankroll math — TOOL**
Keep the per-symbol f* in `brain/config.py`-adjacent docs; if equity ever
grows, quarter-Kelly on the 300/500s and half-Kelly on the 1000s is the
growth-optimal-but-safe band (the 2%→1–1.5% correction already implements
it today).

**21. Fade-value asymmetry check — TOOL/IDEA**
EV/trade (pts): c3 +0.79, c5 +0.24, c1k +0.41, b5 +0.42, b1k +0.66 (sim).
Dollar EV ordering differs after gap realism (c1k is the most fragile at
m=1.43). When cutting exposure, cut by *breakeven margin*, not by sim EV.

---

## D. LIVE EXECUTION & CALIBRATION

**22. $/pt/lot = 1.00 anchor — VERIFIED on c3/c1k, unverified on the rest**
Live `[sl]` math: c3 median 1.074 × 5 lots = −$5.37; c1k median 0.369 × 5 =
−$1.845 — exactly $1/pt/lot. The 500s/boom need real fills to confirm;
v2.12 volume logging + `[CALIB]` lines will pin them. Do not scale dollar
projections for b5/c5/b1k until confirmed.

**23. EA-managed closes beat broker SL as the primary loss path — EXECUTION**
Every measured manual exit has realized ≈1× median (no gap) because the EA
closes on its own bar check before the far broker stop (attached at
1.5×floor) can be gapped through. Keep `AttachStops` (it's the backstop for
EA death/10029) but treat the *manual* path as the loss engine. The gap
risk concentrates in the moments the EA cannot act — which is exactly what
the cooldown + instance lock + warm-up + all-position close harden.

**24. Wide-deviation closes during spikes — DEPLOYED**
Close requests use deviation 500 normally, **3000 on safety_spike** so the
close fills instead of 10029-rejecting mid-spike. Keep. Consider 3000 for
all reason codes when `median_range` is elevated (cheap robustness).

**25. Trade-CSV volume + realized-PnL columns (v2.12) — DEPLOYED**
`LogTrade` appends volume; `ClosePosition` logs broker-realized PnL per
position and prints `[CALIB] $/pt/lot`. This is the ground-truth feed for
#17 and #22. Note: existing log files keep their old 12-col header — rows
append a 13th value; gap_measure tolerates both.

---

## E. CONTINGENCIES & DEFENSE LAYERS

**26. Daily R-budget circuit breaker — CONTINGENCY (c1k only, K=8)**
Stop entering a chart for the rest of the day after its cumulative PnL
drops below −K R (R = the SL distance sized for). Full-history c1k:
K=8 keeps PF 1.44 vs 1.43 baseline while cutting max DD 56.1 → 38.7
(−31%) at ~20% PnL cost. On c3/c5/b5/b1k it is neutral-to-harmful (their
loss days end quickly anyway; the breaker also cuts the profitable end of
bad days). Deploy as a per-chart input, default OFF, ON for c1k if its
measured m stays >1.3.

**27. The 7-layer contingency stack (existing EA layers) — DEPLOYED, audited**
Volatility/gate filters, cluster skip, spike-size, entry timing, buildup
detection, time exit, safety exit — the playbook §4 measured that further
layers re-remove the same gap losses. Keep the current stack; do not stack
more on top of partial+cooldown (playbook §4: "nothing left after partial
+cooldown").

**28. Worst-case loss accounting — TOOL**
Per-symbol realistic loss sizes for planning: c3 −5 pts, c5 −3.3, c1k −6,
b5 −4.7, b1k −9.6 avg with tails to −32R (b1k, sim). Two b1k gaps ≈ two
weeks of gated-chart profit. Size the book so two same-day worst-case gaps
per 1000-chart do not exceed the daily R-budget (#26) when one is set.

---

## F. WHAT NOT TO CHASE (the reverse-engineered ledger, newest first)

**29. Anticipating the spike itself — ANTI-IDEA (proven impossible, cheaply)**
Hazard flat at every wait; size independent of gap; indicators ≈1.0× lift;
price level predicts nothing; hours flat; velocity/acceleration/micro-
patterns dead (lookahead). The algorithm's spike clock is memoryless — you
cannot be "in before the spike". What you *can* do: estimate the hazard for
position sizing and expectational math, and never hold through the
expected-loss tail (#16). Every hour spent hunting a spike predictor is
spent re-measuring noise.

**30. Strategy decay monitoring — DEPLOYED (brain/)**
The regime layer re-measures P(loss|loss), drift state, cluster ratios and
the OOS validity of the current configs every daily run, and refuses to
propose anything that fails train/validation on the corrected engine
(current verdict: HOLD everywhere). Its newest job: fold #17's measured m
into the daily report and flag when any chart crosses its breakeven. This
is the standing checks-and-balances so this catalog stays true.

---

## Appendix — graveyard ledger (everything prior, verdict, why)

| Concept | Verdict | Why (measured) |
|---|---|---|
| Velocity buildup / acceleration pre-spike | DEAD | lookahead artifact on entry bar |
| Continuation-bar entry / cluster entry filters | DEAD | entry-bar lookahead |
| Recovery-state entry | DEAD | lookahead artifact |
| Breakeven stop after 2nd half | DEAD | gaps fill straight through it |
| Trailing stops | DEAD | every trail level is a gap-fillable stop |
| TP 1.25–1.5× geometry | DEAD | exits are timeout-dominated |
| Profit-lock / day caps / monster-gate / escalation | DEAD | trade-count illusion or re-removes same losses |
| COLD hours filter | DEAD | NOISE verdict (stability.py, 5 symbols) |
| M5-trend filter | DEAD | thin WR nudge, unvalidated corrected |
| Double-spike-skip filter | DEAD | subsumed by gate+cooldown |
| Hawkes / spike self-excitation | DEAD | intensity 0.95–1.03× flat; doubles are Poisson |
| EWMA spike-rate gate | DEAD | symbol-inconsistent, no OOS |
| Conditional entry delay (bounce→+1, dump→+3) | DEAD | worse both halves on gated c3 |
| Spike-size compression (big→next smaller) | DEAD | lag-1 size corr ≈ 0.00 |
| Spike-bar body ratio | DEAD | inconsistent halves |
| Drift gate (fade only after favorable pre-spike hour) | WEAK | DD-reducer only, not additive with delay |
| Confirm/delay/calm/isolated entry variants (V_*/G_*) | DEAD | all ≤ canonical gate on 300/500 (today) |
| Third-add pyramiding | DEAD | PF −30–40% every symbol (today) |
| Entry-hour fade filters | DEAD | hours flip between OOS halves (today) |
| Size-vs-gap anticipation | DEAD | corr ≈ 0.00 (today) |
| Limit-entry at −0.25R | DEAD | dip-condition selects losers; fill delta mechanical |

*Corrected engine only. When in doubt, re-run before believing any new claim:
`python eval/catalog_hunt3.py`, `python eval/portfolio_mc.py`, or the playbook
engines.*
