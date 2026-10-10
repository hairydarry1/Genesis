# Replay vs Backtest PnL Gap — Diagnosis (Sep 8, 2026)

**Question:** why did the replay executor lose money while the Phase 1 backtest
(cold-hours, Sep 1–7) showed WR 57–70% and positive expectancy?

**Tool:** `eval/diagnose_replay_gap.py` — a variant ladder that toggles one layer
at a time over the same Sep 1–7 tick data, same sizing basis, halts on/off.

## TL;DR — three compounding misreads, not one

| # | Cause | Cost (7d, $200, 4 symbols) |
|---|-------|---------------------------|
| 1 | **Phantom SL**: `safety_multiplier = 3.0x median range` was treated as a stop-loss. It is the *spike-detection* threshold (same 3.0x as `SPIKE_MULT` and the EA's `SpikeThreshold`). At 0.02–0.04% of price, the stop sat inside single-tick noise; spike ticks blew through it, realizing $17–60 losses vs $4 intended (10–15x overshoot). | dominated the original bleed |
| 2 | **Wrong timeout**: 6 bars vs Phase 1's 8. | −$173 → −$42 (+$131/wk when fixed) |
| 3 | **Halt cage x sizing**: the 5% daily halt never existed in the Phase 1 backtest. With 2% risk per trade, the halt trips after ~2.5 losses — at 57% WR, most days lock after 3 trades, starving a thin-edge strategy of volume (n=36–84 vs n=191–200 unhaltered). | −$158 → +$768 (+$926/wk when excluded) |

Non-causes (verified): tick-vs-bar TP fills (B→C in earlier ladder: identical),
timestamp synthesis (fixed bug found during diagnosis), sizing basis (normalized
via the 1.5x-median risk distance).

## Variant ladder (final, Sep 1–7, $200 book per symbol, fresh book each)

| Variant | Config | n (all syms) | WR | PnL |
|---------|--------|--------------|----|----|
| A | EA geometry (SL 1.5x med, TP 2x med, to8, tick safety exit, halts) | 315 | ~85% | **−$164.15** |
| B | Same, SL 1.0x med (no attach) | — | — | −$164.15 (≡A: all SLs blow through) |
| C | Phase 1 semantics + halts (no SL, TP 0.4%, to8) | — | — | −$417.03 |
| D | Phase 1 semantics, NO halts | 491 | 61.3% | **+$851.78** |

Reference (Phase 1 bar backtest, no halts, executor sizing): boom500 +$363,
crash500 +$223, boom1000 −$5, crash1000 −$69 → net +$513/week.

## Executor parity (achieved after fixing 4 more bugs)

After the fixes below, the official executor's trade set is **bit-for-bit
identical** to ladder variant D (491 trades; same reasons, holds, and
favorable-move percentages to 4 decimals — verified by multiset diff).
Remaining USD differences are sizing-basis only:

| Executor run (same 491 trades) | 7-day PnL |
|--------------------------------|-----------|
| Per-symbol fresh books, compounding (variant D) | +$851.78 |
| Shared book, sizing FIXED on starting equity (`--fixed-sizing`) | **+$595.99** |
| Shared book, compounding (live-like default) | **−$174.16** |
| Shared book, compounding + halts (locked config) | −$145.94 (n=48, PF 0.65) |

**Compounding-path risk:** at 2% risk on a PF ≈ 1.1 edge, the same trade
sequence swings ~$770/week purely from sizing path. crash1000's loss cluster
lands on a grown book (−$526 vs −$199 isolated) while the 500s' wins land on a
shrunk one — the edge flips sign. Sizing basis is a material strategy
decision, not an implementation detail.

### Bugs found in the executor during parity work (all fixed)

1. **No bar-close TP** — the 0.4% TP existed only as an intrabar tick check
   (unreachable: TP distance is 30–138× the median M1 range). Phase 1's exit
   is a bar-close check; without it 100% of exits were timeout. Added to
   `_close_bar` in reference order (TP → timeout → spike_against).
2. **`spike_dir` sign inverted** — `_open` stored −1 for a short, but a short
   faded an UP spike (+1); `spike_against` (Phase 1: exit on same-direction
   double spike) could never fire. Now 188 fire (38.3% exit mix, matching
   Phase 1).
3. **Sequential replay starved symbols** — `run_replay` fed all of symbol 1's
   week, then symbol 2 … into one shared book, so later symbols traded a dead
   book. Now interleaves all symbols' ticks by real timestamps (live-like).
4. **Re-entry on exit bar skipped** — an `elif` blocked queuing a new entry on
   the bar that closed a position; Phase 1 allows re-entry when the exit bar is
   itself a spike.

Replay also now uses **real candle timestamps** (uniform synthesis reshuffles
hour-of-day and corrupts the cold-hours filter) via a shared `load_ticks`.

## Tick-level safety exit (EA v2.15 `safety_spike`)

One adverse tick > 2.5x spikeMult x median closes the position instantly.
On 1-min fades this *always* realizes the spike-tick fill (price reverts after
spikes; the EA's own v2.07 notes say double spikes cluster 1–5 **bars** apart).
Measured with the halt cage removed conceptually: variant A vs D differ by
−$931/week. The bar-close `spike_against` exit prices nearly identically
without paying the spike tick.

**Executor default is now `--no-safety-tick-exit` semantics** (opt in with
`--safety-tick-exit` for A/B). EA live trading should re-validate
`UseSafetyExit` separately — the EA runs the scalp geometry (SL 1x / TP 2x
median, attached 1.5x), which is a *different strategy* from the Phase 1
validated fade (0.4% TP, no SL).

## Consecutive-loss halt semantics

The 5-loss halt was "disabled until restart" — a session-kill over a 14-day
paper phase. Locked stop conditions are daily risk rails; both halts now reset
each UTC day (`EquityBook.note_ts` rolls the day on every tick — must be in the
tick path, else a halted engine never closes a trade and never unhalts).

## Strategic conclusion

The Phase 1 edge (WR 57–70%, ~+$500/wk on $200 at executor sizing) **does not
survive translation into the EA's tight-stop scalp geometry** (WR 82–90%,
negative PnL — wins pay ~1 median range while spike-through losses pay ~1.5x
attached distance and cluster). Phase 1 semantics and EA semantics are two
different strategies. Paper trading (Phase 2) must run the *validated* Phase 1
semantics, which is what `eval/paper_trading_protected.py` now implements:

- entries: fade spike-candle direction, cold hours only, next-bar fill
- exits: TP 0.4% on M1 bar close (Phase 1 check), timeout 8 bars, spike-against at bar close
- no intrabar SL
- risk rails: 2%/trade sized on 1.5x-median distance, 5% daily halt,
  5 consecutive-loss halt, both reset daily

## Known residual vs reference

With `--no-halts --fixed-sizing` the executor reproduces the Phase 1 reference
within sizing-path noise (+$596 vs +$513 bar-backtest equivalent; per-symbol
differences reflect tick-vs-bar-close fill approximation). The replay is the
*more* realistic estimate of what live paper trading will experience.

## DECISION (Sep 8, 2026): Phase 2 config = measurement mode (option A)

The executor default is now **measurement mode**: pure Phase 1 semantics —
halts OFF, sizing FIXED on starting equity. Legacy flags kept for
compatibility: `--halts` enables the rails, `--compounding` restores
live-like sizing, `--no-halts` is a no-op kept so old commands still run.

**Why A, and why the hybrid (C) was tried and rejected.** Paper trading risks
nothing real; its job is to measure whether the live edge matches Phase 1
(+$513–596/wk on $200, WR 55–70%). The halt cage answers a Phase 3 question —
and on Sep 1–7 it is not merely binding but *path-toxic* at this sizing:

| Halts (fixed sizing unless noted) | Trades | 7d PnL |
|-----------------------------------|--------|--------|
| daily 5% / 5 consec, compounding (old locked) | 48 | −$146 |
| daily 15% / 5 consec (hybrid option C) | 62 | **−$257 (equity NEGATIVE)** |
| daily 25% / 5 consec | 62 | −$257 (identical — halt path converges) |
| daily 50% / 10 consec (opt-in default) | 432 | +$483 (1 halt event) |
| none (**Phase 2 default**) | 491 | **+$596** |

Mechanism: with no intrabar SL, realized losses (~$17–36 via `spike_against`
and timeout tails) dwarf the nominal $4 risk, and losses **cluster** on double
spikes. A halt-after-loss-cluster then skips exactly the trades following each
cluster — which include the recoveries — selecting an unrepresentative ~12%
of a validated edge. Meanwhile fixed sizing never de-levers, so the 15%
dollar cap shrinks below one realized loss as equity falls (halt → shrink →
halt spiral; equity went negative). Between the 25% and 50% thresholds there
is a cliff with no robust middle — the hybrid's premise ("raise the threshold
so it stops catastrophic days without capping participation") does not hold
for this edge.

**Consequence for Phase 3 (live):** do NOT reuse these rails. Live execution
needs a real per-trade SL (the no-SL Phase 1 semantics have unbounded intrabar
risk) and de-levering compounding before halt thresholds can mean anything.
That redesign is Phase 3 work and should be informed by what Phase 2 records
(esp. realized loss distribution and loss clustering).

## SYMBOL SCOPE (Sep 8, 2026): all 4 symbols for Phase 2

Question raised by crash1000's negative OOS week (−$653 on 92 trades in tick
replay; the only bleeding symbol). Decision: **exclude nothing**.

- crash1000's WR held across months (70.5% Aug → 69.5% Sep on the Phase 1
  bar basis) — the *pattern* is intact; what flipped is per-trade magnitude
  (avg +$2.11 → −$0.53). Its spike_against losses are ~3× the 500-series'
  (−$51 vs −$15–19) on identical 2% risk — a 1.5x-median sizing-basis
  artifact of the 1000-series' smaller median range, not a broken pattern.
- Per-symbol monthly signs are regime noise, demonstrated by the mirror:
  crash500 was Aug's WORST symbol (WR 49.2%, −$1099 USD-equiv) and Sep's
  runner-up (+$223), the exact inverse of crash1000 (+$1166 → −$69). An
  exclusion rule applied in August would have wrongly dropped crash500.
- The only stable object is the portfolio: positive in BOTH months
  (Aug ≈ +$1705, Sep ≈ +$512, bar-basis USD-equiv).
- Phase 2 is a measurement phase at zero real risk: per-symbol OOS data IS
  the product. Dropping a symbol because last week's sign was negative throws
  away a quarter of the evidence.

Pre-registered suspension criterion (Sep 23–24 review): suspend a symbol for
general go-live only if, over the full 14 paper days, per-symbol paper PnL < 0
**AND** WR_cold < 55%. Single-week signs are explicitly not a basis.

(caveat: replay timestamps are synthesized from candle timestamps — the
tracker CSV stamps wall-clock time, so day-level replay analysis must be done
in-process. Live trading is unaffected.)

## Reproduce

```bash
python eval/diagnose_replay_gap.py                 # variant ladder + asymmetry
# measurement mode = DEFAULT (Phase 1 parity, +$596):
python eval/paper_trading_protected.py --replay \
    --symbols boom1000,crash1000,boom500,crash500 --equity 200
# halt rails opt-in (least-harmful tested: +$483):
python eval/paper_trading_protected.py --replay \
    --symbols boom1000,crash1000,boom500,crash500 --equity 200 --halts
# old locked config reproduction (halts + compounding, −$146, n=48):
python eval/paper_trading_protected.py --replay \
    --symbols boom1000,crash1000,boom500,crash500 --equity 200 \
    --halts --compounding --daily-loss 0.05
python eval/verify_sept.py                         # Phase 1 re-verification
```
