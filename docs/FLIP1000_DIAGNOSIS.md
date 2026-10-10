# Flip1000 — why the live account blew up, and what to change

Written 2026-10-10, after the second live blow-up (book $7.25 → $1.40 over Oct 8–9).
Operational hand-off (how to run it, demo week, re-fund) is in
`docs/runbooks/flip1000_demo_then_refund.md`.

Every number below is labelled with the study and the window it came from. The tick
archive has grown during the season, so **windows differ between studies and levels are
NOT comparable across them** — only compare rows within one table.

---

## 1. TL;DR

1. **The user's diagnosis is directionally right and the tests confirm the mechanism**:
   the entry trigger is a spike, the spike's direction is not the drift's direction, and the
   losing trades are the ones armed *against* the drift. Drift persistence is real and
   measurable (57–64.5% continuation at a 10-minute horizon, z up to 16.4).
2. **But the killer is not the entry — it is the EXIT and the SIZE.**
   - The incumbent exit closes on the *first* counter-spike. Inside a drift, that first
     counter-spike is the meaningless small spike the user described — so the bot
     **realises a loss on noise** and the position is gone before the drift resumes.
     Holding through it instead is worth more than every entry filter tested this season
     (see §4).
   - At 0.1 lot the 60u backstop is **$6.00 = 60% of a $10 book**. One loss removes more
     than half the account, and below ~$7.50 the broker's margin stop-out — not the SL —
     is what ends the trade. The account died of arithmetic, not of direction.
3. **Two integrity bugs were found in the live run** (§6): the executor follows
   `account_info()` wherever the terminal's login goes (booking a phantom ±$1,548 and
   adopting a foreign position), and there is **no solvency gate** — it will arm a trade it
   cannot afford.

---

## 2. What actually happened, trade by trade

Session Oct 8 11:57Z → Oct 9 17:24Z, live account 22031121, book anchored to the real
balance at start ($7.25). From `data/flip_exec/trades.csv`:

| # | side | entry | exit | u | $ |
|---|---|---|---|---|---|
| 1 | long | 9671.03130 | 9658.61580 | −12.42 | −1.24 |
| 2 | short | 9664.72490 | 9644.66080 | −20.06 | **+2.01** |
| 3 | short | 9678.15720 | 9698.18410 | +20.03 | −2.00 |
| 4 | — | (orphan manual CLOSE, foreign adoption — see §6) | | | 0.00 |
| 5 | long | 9697.89950 | 9690.40110 | −7.50 | −0.75 |
| 6 | short | 9681.97940 | 9676.54750 | −5.43 | +0.54 |
| 7 | long | 9682.17320 | 9673.24780 | −8.93 | −0.89 |
| 8 | long | 9674.36920 | 9674.61400 | +0.24 | +0.02 |
| 9 | short | 9669.31010 | 9685.14130 | +15.83 | −1.58 |
| 10 | short | 9653.28360 | 9659.56710 | +6.28 | −0.63 |
| 11 | long | 9630.49570 | 9612.47330 | −18.02 | −1.80 |

**Longs −$4.66 (5 trips), shorts −$1.67 (5 trips). Book $7.25 → $1.40.**

Read this honestly: **both directions lost.** n=10 round trips. This session says nothing
statistically about direction — it says a lot about size. (One $0.47 gap on trade 3 is a
manual-close fill difference, not a bug: the live anchor books the real balance, and a
human closing in the terminal fills at a different instant than the executor's polled quote.)

---

## 3. The user's hypothesis, tested

### 3.1 "Drifts last over an hour" — half right, and the useful half is 10 minutes

`analysis/flip_drift_regime.py` (Part D) — 34 gap-masked days, 80 cells, Bonferroni bar 2.94:
P(forward drift continues the trailing drift | |trailing drift| ≥ θ):

| horizon | continuation | verdict |
|---|---|---|
| 10 min | **57.0 – 64.5%**, max z = **16.45** | real, far past the bar (user is right) |
| 30 min | 50 – 55% | weak |
| 60 min | 46.9 – 54.8% | **coin flip** |
| 120 min | 45.7 – 52.5% | mean-reverting |

So regimes persist, but at the **10-minute** scale, not the hour. The engine's 300-tick
(~5 min) pending delay already aims at exactly that scale.

Companion result, `analysis/flip_drift_gate.py` Part A (same window, 56 cells): a *linear*
predictor of forward drift from trailing drift has max |t| = 1.62 — i.e. the persistence
lives in the **tail** (regime continuation), not in the slope. This is why an earlier
"drift is unpredictable" answer was misleading.

### 3.2 "Tiny spikes inside the drift do nothing" — as an ENTRY filter, false

`analysis/flip_spike_size.py` (Part A/A2, 36.5 gap-masked days, no-delay engine, first run
2026-10-10). Does the entry spike's size (x = multiple of the frozen median) predict the
outcome?

- corr(x, net) = **+0.020** (LS), **+0.060** (long-only) ≈ nothing.
- Terciles (LS): x < 140.87 → n=299, WR 37%, −$0.29; middle → n=299, WR 54%, +$0.18;
  top → n=300, WR 47%, −$0.02. No monotone structure.
- Filter ladder (minimum entry size): LS −$7.51 → −$7.40 → −$7.16 → −$7.95 /wk for
  x_min = none / 5.25 / 5.5 / 6.0. Long-only −$1.52 → −$1.46 → −$1.47 → −$2.22 /wk.

**Blocking small entry spikes changes nothing.** But the observation is *not* wasted: the
small spike is not a bad *entry* signal, it is a bad **exit** signal — which is where it
actually costs money (§4).

### 3.3 "The bot trades against the drift" — confirmed, and gating it does not pay

`analysis/flip_drift_gate.py` Part C (deployed delay-300 engine, bit-exact parity gate):
baseline LS = 634 trips, net **+$5.22** over 34 days = +$1.08/wk, of which
**longs +$39.51, shorts −$34.29**.

Twelve drift-alignment configurations (block shorts when trailing w-drift ≤ −θ, θ ∈
{10,20,40}u, w ∈ {20,60,120,240} min) — best cell θ=10/w=60m: **net +$38.71 (+$7.97/wk)**,
95 blocks. But: that is ≈ the same as simply **not shorting** (+$8.14/wk), and every
configuration leaves the short book between **−$15.90 and −$48.02**.
`analysis/flip_drift_regime.py` Part E then armed *only* trades aligned with the drift
(spike direction == sign(trailing drift), ≥ θ): 12 configs, best +$16.34 (+$3.37/wk) —
still worse than long-only, and the short leg was negative in 10 of 12.

**Conclusion: drift alignment is a real signal (10-min horizon) but a weak filter, and it
never rescues the short book under the incumbent exit.** See §4-bis — that conclusion is
conditioned on the incumbent exit.

---

## 4. The new finding: it is the EXIT, not the entry

`analysis/flip_spike_size.py` Part B — **identical entries**, only the exit changed
(36.5 days, no-delay engine, spread 0.0626 USD/trip, 0.1 lot):

| config | n | WR | net/wk | worst | maxDD |
|---|---|---|---|---|---|
| LS incumbent (close on first counter-spike) | 898 | 45.9% | **−$7.51** | −$10.04 | $105.90 |
| LS retrace-hold (pure) | 792 | 56.6% | −$7.66 | −$10.45 | $108.77 |
| LS retrace-hold (**bounded**) | 845 | 54.0% | **+$0.37** | −$10.45 | $72.25 |
| LS incumbent + 25u stop | 993 | 45.6% | −$6.76 | −$3.25 | $90.09 |
| LS retrace-bounded + 25u stop | 941 | 52.8% | −$2.44 | −$3.88 | $81.49 |
| L incumbent (close on first counter-spike) | 605 | 45.8% | **−$1.52** | −$10.04 | $64.39 |
| L retrace-hold (**pure**) | 604 | 55.8% | **+$6.88** | −$10.42 | $38.19 |
| L retrace-hold (bounded) | 604 | 53.6% | +$2.63 | −$9.95 | $47.61 |
| L incumbent + 25u stop | 660 | 45.6% | −$1.86 | −$3.25 | $59.14 |
| L retrace-bounded + 25u stop | 659 | 53.0% | **+$2.84** | −$3.44 | **$36.20** |

The exits mean:
- **counter** (incumbent) — close the moment a counter-spike prints.
- **retrace-hold (pure)** — if that counter-spike is *against* us and we are losing, don't
  close: wait for the first *favourable* spike (the retrace) and exit there. Further
  adverse spikes are held through.
- **retrace-hold (bounded)** — as pure, but if a *second* adverse spike arrives while
  holding (the drift really is continuing through us), close.

Why this matches the user's observation exactly: the incumbent treats the small
counter-spike inside a drift as an exit signal, so it **books the loss on noise** and is
flat when the drift resumes. Holding through it lifts the win rate from ~46% to ~54–56% and,
long-only, turns a −$1.52/wk book into +$6.88/wk with maxDD cut 41%. The 25u stop alone
does almost nothing to the net but caps the worst trade at −$3.25.

Validity note: that study's own incumbent does not reproduce its quoted fleet reference
(its docstring expects LS +$19.81/wk, it measures −$7.51/wk) — consistent with the season's
**edge-decay** finding (the family's early-window numbers were measured on 7–13 days and did
not survive the longer window). Same-engine, one-variable comparisons are still valid, and
they are what the table above is for.

### 4-bis. The same test on the DEPLOYED engine (delay-300)

`analysis/flip_exit_retrace.py` re-runs the exit family on the live convention
(`flip_rolling_engine.run_conv` clone, rolling 5000-tick median, mult 5, min_age 1,
delay 300, live spread, 0.1 lot), gated by a bit-exact parity assert against `run_conv`
before any table prints, and with the live **60u server-side backstop** as a second axis
(stop fills at the stop level and is checked every tick, i.e. broker SL behaviour).

[[DELAY300_TABLE]]

---

## 5. The real killer: size, and the missing solvency gate

Facts:
- 0.1 lot ⇒ 1 price unit = **$0.10**. The backstop is **60u = $6.00 per trade**.
- On a **$10** funded book that is **60% of equity per trade**: ~1.6 "lives".
- Margin on 0.1 lot at 1:1000 ≈ **$1.60**; a Deriv stop-out (~50% margin level) arrives at
  roughly **$0.80 equity**. So on a degraded book the broker closes the trade *long before*
  the 60u SL ever could — that is how the Oct 7 trade died (`not enough funds to continue
  trading`), and why the live book twice hit the floor.
- Ruin measurements (P21 regime study, 33-day window): LS **71.7% ruin** / +$0.63/wk;
  long-only **37.9% ruin** / +$7.64/wk. The Oct 8 gate's original `$4.00` floor and the
  `$1.50` floor both live *inside* one backstop loss on a small book.
- **There is no code path that refuses an entry because the account cannot absorb the
  stop.** The only entry blocks are the day-PnL halt and the `floored` latch, and neither
  fires until the money is already gone.

### The gate to add (spec — 3 lines, not yet implemented)

Before arming/keeping a pending entry, require that a **full backstop loss leaves the book
above the floor**:

```
backstop_usd = SL_BACKSTOP_UNITS * VPU * (LOTS / 0.1)      # $6.00 at 0.1 lot
margin_usd   = order_calc_margin(...) or 1.60              # 0.1 lot at 1:1000
if acct.equity - backstop_usd - margin_usd <= EQUITY_FLOOR_USD:
    block the entry  ->  print "[SOLVENCY] equity $X - backstop $6.00 <= floor $1.50"
```

Behaviour: a fresh $10 book passes ($10 − $6.00 − $1.60 = $2.40 > $1.50) and trades; a
**$7.25** book is refused outright ($7.25 − $7.60 = −$0.35). Applied on Oct 8, the entire
death spiral would not have happened — the account was already unable to carry its own stop.
Test it in the demo week before it ever sees live money again.

### Sizing options, honestly

| funding | lot | risk/trade | measured outcome | verdict |
|---|---|---|---|---|
| $10 | 0.1 | $6.00 (60%) | long-only +$7.64/wk, ruin 37.9% | the current plan; a coin flip |
| $10 | 0.01 | $0.60 (6%) | can't die, but edge/trip ≈ $0.0009 ⇒ ~$0.10/wk | measurement account, not growth |
| $50–100 | 0.1 | $6.00 (12%/6%) | long-only +$7.64/wk, ruin ≈ 0 | **the honest route to the $15 VPS trigger** |

The $15 VPS trigger needs **+150%** on a strategy whose measured ruin at that size is
**38%**. That is not a funding gap, it is a leverage gap. If the goal is the VPS, $50+ at
0.1 lot reaches it in ~2 weeks of measured edge; $10 at 0.1 lot mostly measures the ruin
process.

---

## 6. Integrity findings from the live run (new)

Both are in the executor as it stands. Neither is a "strategy" bug; both can corrupt
measurement, which is the entire point of the season.

**6.1 The executor follows the terminal's account.** `data/flip_exec/balance_ops.csv`
records two phantom ±$1,548 rows (`+1544.0057` on Oct 7 00:06Z, `+1548.5600`/`−1548.5600`
on Oct 9 12:04–12:07Z) and the log prints
`[BALANCE-OP] book $6.49 -> real $1555.05`. That is not money — it is `account_info()`
reporting a *different logged-in account* (`$1555.05`) because the terminal's login changed
under the running executor. `reconcile_book()` and the live anchor both trust
`account_info()` unconditionally.

**6.2 …and then adopts positions it finds there.** At the Oct 9 12:04 switch the log shows
`[MAN-SYNC] adopted manual position: long @ 9584.31110 (ticket 8789974209)` — a price ~90u
away from the flip1000 quotes at that moment (9673.6) and a ticket of a different shape from
the account's own. It produced the orphan `CLOSE` row on Oct 9 12:07:52 in `trades.csv` (a
close with no open). Net effect on the book was zero because the switch reversed, but for
those minutes the floor and day-stop logic were being evaluated against the wrong account.

**Hardening spec (not implemented):** capture `LAUNCH_LOGIN = acct.login` at startup; in
`reconcile_book()` and `sync_open_position()`, if `account_info().login != LAUNCH_LOGIN`,
print `[ACCOUNT-CHANGED] ...` and skip the update (never adopt, never re-anchor). Also:
**do not switch the MT5 terminal's account while the executor is running** — the demo week
must keep the demo login up and nothing else.

---

## 7. What to do next, in priority order

1. **Read the delay-300 exit verdict** (§4-bis) when `archive/flip_exit_retrace.out`
   finishes. If the retrace-hold direction holds on the deployed engine, it is the single
   biggest change available and it directly addresses the user's complaint.
2. **Implement the solvency gate** (§5) — small, testable on demo, and it is the guard whose
   absence cost both live accounts.
3. **Re-decide long-only vs LS *after* the exit change.** "Never short" was established
   under the incumbent exit (shorts −$34.29 / 634 trips; every gate config left them
   negative). With the retrace exit, the LS row improves by more than the L row in the
   no-delay table, so the short book deserves one more look rather than a blanket ban.
4. **Do not ship the entry-size filter** (x_min) — tested, dead.
5. **Do not ship a drift gate** as an entry filter — it approximates long-only at best and
   never fixes shorts.
6. Demo week → then re-fund, per the runbook. If the goal is the VPS trigger, fund more
   rather than leverage more.

---

## 8. Evidence index

| file | what it holds |
|---|---|
| `archive/flip_drift_gate.out` | Parts A/B/C — linear predictability, Oct 7 counterfactual, short-block gate sim |
| `archive/flip_drift_regime.out` | Parts D/E — regime persistence census, drift-aligned engine |
| `archive/flip_spike_size.out` | entry-spike size vs outcome; exit family (no-delay engine) |
| `archive/flip_exit_retrace.out` | exit family on the deployed delay-300 engine + 60u backstop |
| `analysis/flip_sl_backstop.py` | stop sweep: 60u is optimal on both sides |
| `analysis/flip_regime_desize.py` | P21 regime de-sizing; owns VPU/spread/MULT/DELAY |
| `data/flip_exec/trades.csv` | the Oct 8–9 live round trips in §2 |
| `archive/flip_exec.log` | `[BALANCE-OP]`, `[MAN-SYNC]`, `[BACKLOG]`, `[HB]` heartbeats |
| `data/flip_exec/balance_ops.csv` | the ±$1,548 account-switch artifacts |
