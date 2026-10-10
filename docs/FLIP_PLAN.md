# Flip $10 → $100 — Canonical Plan

**Single source of truth.** If you read only one file, read this one.
Updated: 2026-09-17 · Strategy details: `docs/FLIP_INDEX_MECHANICS.md` ·
Incident history: `.state/session_state.toml`

> **STATUS Sep 17 (evening) — RESEEDED as book v2 on Menu C.** v1 (−$0.18,
> 52 trips) is archived at `archive/book_v1_dead/` with full reconciliation
> (Appendix AG). The PnL bridge (Appendix AI) proved the week's death was
> ~2/3 infrastructure (executor blind for 69% of engine opportunities) and
> ~1/3 regime — the signal was present in the live stream (+$21.32 engine
> on the same ticks) but uncaptured. v2 runs **config C** (fence5 rolling
> + DELAY300 pending-entry machine, LS, 0.1 lot) with: state persistence
> (phase restores across restarts, pending deliberately dropped),
> stream-currency + pending-fate instrumentation in every heartbeat
> (`uptime.json`), and a **no-restart discipline**: mid-flight restarts
> corrupt state (AI bucket 3) — restart only for a verified fault, and
> note it in the log. **New gate readout: Oct 15** (28 days from reseed;
> criteria: live $/trip at full uptime, cluster-day frequency per AH,
> stream-currency per AI). The C-vs-long-only-branch FLAG resolves at
> that readout (C's L-side cell is in AF).
> Re-decision needed before any redeploy: reseed, restart the 28-day gate
> from a fresh $10, or abandon — the Oct 8 menu science (AE–AF) stands, but
> it was never validated by the diagnostic book.
>
> **OOS STRESS (Sep 17, Appendix AH):** first true holdout (post-freeze
> ticks, 0.73d, worst burst day on record) — every menu cell LOST
> (C −$5.87, d0 −$6.86, B −$4.88). Burst did NOT poison the detector
> (11/12 C entries were true-jump class); the entries themselves lost.
> Menu survival numbers are now CLUSTER-FREQUENCY-CONDITIONAL — see the
> menu bullet below. Nothing re-pointed on this sample.
>
> **v2 FIRST CHECKPOINT (Sep 18, Appendix AK):** n=15, +$10.35 (+$0.69/trip;
> machine-only +$0.23/trip above the AF expectation, manual closes are 76%
> of the net). Bridge vs the bit-exact C engine on the same ticks: 15
> matched (delta −$0.05/trip), 7 engine-only that LOST −$2.03, **zero
> stale/junk entries** — AI buckets 2–3 fixed in production. Uptime: hb_current
> 99.1%, but **wall-clock ~71%** (7.4h PC sleep, 06:46–13:40Z — the 7
> "missed" fills were a frozen machine, not signal divergence; AK addendum).
> 0/15 minor-class entries, 0 pending expires. Nothing proven at n=15
> (v1 was +$3.30 at n=13 before dying); no config change licensed.
> Side finding: the tick collector died silently Sep 18 02:30 and was
> keepalive-covered after the store self-healed 44h of gaps — the gate's
> benchmark is a live dependency now.

---

## 1. Mission

Turn **$10 into $100** on Deriv's **Crash Boom Flip 1000 Index** with a
phase-following strategy at **fixed 0.1 lot**, inside a deadline, without
ruining the stake first.

The edge (measured on a 687K-tick archive): large moves (|delta| > 5.0×
rolling median |delta|) form persistent *phases*; after a phase prints one
same-direction spike (min_age=1), ride it and exit tick-precisely on the
first counter-spike. Wins are rare but large (avg +$1.52), losses frequent
but tiny (avg −$0.09) — a **17:1 win/loss size ratio**. Never interrupt the
wins: TP, trails, confirmation-exits, pyramiding, and time-stops were all
backtested and all lose to the incumbent (see the graveyard in
`docs/FLIP_INDEX_MECHANICS.md`).

## 2. What is running right now

- **Executor** `eval/flip_executor.py` — detached, PID in
  `data/flip_exec/executor.pid`, log `archive/flip_exec.log`.
  Config: 5.0× median, min_age=1, 0.1 lot, long+short, $0.10/unit.
- **Model book**: $10 virtual equity tracked in `data/flip_exec/trades.csv`
  and `archive/flip_daily_digest.csv` (one row per UTC day, rebuilt by the
  checkpoint).
- **Three scheduled tasks** (Windows Task Scheduler):
  - `Genesis Flip Tick Collector` 01:05 UTC — 72h self-healing tick archive
    → `data/flip_ticks/` (integrity: 100.00% coverage, audited 09-11,
    `analysis/flip_archive_audit.py`)
  - `Genesis FlipCheckpoint Daily` 02:30 UTC — liveness check + auto-restart
    + digest + review → `archive/flip_checkpoint.log`
  - `Genesis Flip Confirm LongOnly` 02:45 UTC — self-arms, reads out **Oct 8**
    → `archive/flip_confirm_longonly.log`
- **Guards live in the executor**: demo-only account guard (order +
  startup), 60u server-side SL backstop on every entry/adopt (worst case
  ≈ $6, verified accepted by broker), persistent daily stop −$9, $4 equity
  floor → permanent halt, stale-tick entry guard (>120s), AutoTrading-off
  detection, reconnect on real transport failures, orphan adopt.
- **Live trading is IMPOSSIBLE by accident**: it requires launching with
  `FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY` in the environment AND
  the terminal logged into the live account. The 02:30 auto-restart cannot
  inherit the flag (a dead live executor alerts a human instead).

## 3. Decisions already settled (do not relitigate without new data)

| Decision | Value | Evidence |
|---|---|---|
| Execution verdict | **GO** (frozen `data/flip_exec/verdict.json`) | n=30 sides: extra cost $0.0576/RT, slippage ≈ $0.00 |
| Live spread gate | **PASS** — 0.6261u, identical to demo | 30/30 samples on live acct 140691027, 09-11 |
| $-per-unit on live | $0.10/unit @ 0.1 lot, 1:1000 | identical to demo |
| Sizing (diagnostic phase, L+S) | **0.1 lot fixed** — the measurement lot, by design | every trip equal-weight keeps the edge estimate unbiased |
| Sizing (confirmed long-only branch) | **std ladder 0.1→0.4 @ $15/$30/$50** | Appendix W sweep (Sep 15): 90.2% flip / 5.9% ruin; NO steeper schedule improves both odds |
| TP / exits | No TP; counter-spike exit only | TP ladder: every rung ≤ +$12.5/wk vs +$23.7 incumbent |

## 4. The two branches (choose Oct 8)

**Patient (recommended — it's an everything-stake):** demo runs free until
**Oct 8**. If the 28-day confirmation passes → fund $10, lift the guard
deliberately, run **long-only** with the gate's winning config:
- Survival refresh (Sep 15, live edge embedded): menu leader **TIMEBND 5000** —
  50.8% flip / 4.0% ruin fixed lot, **90.1% flip / 5.9% ruin on the std ladder**
  (Appendices V/W; menu chosen in-sample, ~14 daily blocks → wide CIs).
- **Daily profit aim $3 — REJECTED for now** (Appendix X2, Sep 16, supersedes
  Appendix X): the 7-seed paired stress test collapsed the headline —
  incumbent ladder 97.5/2.3 → **35.8/61.1** flip/ruin, and the aim actively
  HURTS TIMEBND 5000 (−5.7pp flip, stable across all seeds). Cause: Sep 15
  completed as the worst long-only block on record (−$15.13/−$19.80) and it   was bleed-shaped (intraday peak only +$1.39) — the aim protects giveback
   days, never bleed days. Parked as a regime-dependent option; may only
   return with many more daily blocks and pre-registered shape evidence.
   Never in the diagnostic phase.
- **Quiet-entry filter (r0=3) — CANDIDATE for Oct 8** (Appendix Y, Sep 16):
  suppress entries while >=3 minors (5-8x median) sit in the trailing 2000
  ticks. Archive: long-only +$7.99 -> +$20.95/wk (incumbent) and ladder
  survival 27/70 -> 87/6 flip/ruin; the entire delta lands on the two bleed
  days (Sep 14 +9.64, Sep 15 +13.49) with every other day exactly untouched,
  so the ACTREG day-concentration ghost does NOT apply. Mechanism: a minor
  flood is the absorption storm — phase votes lie, entries die. The executor
  now stamps min_2k on every ledger row (telemetry only, strategy frozen) so
  the gate scores it live via flip_telemetry_score P3 before any deployment
  decision. Decide at Oct 8 alongside exit family — never in the diagnostic
  phase. Validated further in Appendix Z: fleet-exact engine (all 4 cells),
  seed-stable survival (+60pp flip / −64pp ruin across 3 seeds), echo-decay
  confirmation of the burst mechanism.
- **Event-class fence (MULT 5.0 → 8.0) — CANDIDATE for Oct 8, front-runner**
  (Appendix AA, Sep 16): the tick archive has TWO populations — ordinary
  ticks ending near 8x median and true generator jumps from ~20x (empty
  gap, jump rate = the spec clock). Voting only on true jumps doubles the
  long-only edge (+$17.98/wk incumbent, +$22.42 TB5000) and makes quiet-entry
  a NO-OP — the fence is the root-cause fix for burst poisoning, one mechanism
  instead of two. Seed-stable 78-80/85-87 flip. Generality CONFIRMED
  across the family (Appendix AB): flip300/flip500 show the same
  two-population structure and improve 2-9x at their true-class fences;
  the true-jump persistence is ~60% on every symbol; flip150 stays
  untradeable (spread = 27.9x its median tick). flip300 second-book
  SKETCH (post-$100 only): Appendix AC - own 28-day gate, $50-60 seed
  book (volume_min confirmed 0.1 on Sep 16, so $30 is not viable),
  counter exit (TB does not transfer), never before the mission
  completes. NOT deployed: diagnostic
  book keeps voting at 5x for a clean gate; decide at Oct 8 vs the r0=3
  combination on live trip counts.
- **Oct 8 menu (re-based Sep 16, Appendix AE + AF — live =
  rolling-median convention):**
  C) FRONT-RUNNER: rolling-fence5 counter + DELAY300 (pending entry,
  cancel on opposite spike) - +$32.60 LS net/wk; fixed-0.1 survival
  81-83% flip / ~4% ruin (7 seeds); ladder 92-93% / 6-7%. Both-half
  replicated at d300; K pinned to the 180-300 plateau, 600 was
  window luck. Needs a pending-entry state machine in the executor.
  B) fence8+TB5000 - demoted: fixed 47.5%/16.5%, LS ladder 66%/33%
  (its earlier ~86% was long-side frozen-era framing). Fence8 stays
  as the convention-robust detector fact (AE), not the deployment.
  A) fence5+quiet r0=3+TB5000 - RETIRED (AE): r0 was a frozen-median
  artifact patch; the live rolling median never had the disease.
  FLAG (Sep 17 audit): C is an LS config but the branch rule above says
  "run long-only." At the Oct 8 readout, either score C's long-only variant
  (its L-side delay response is in AF) or amend the branch text explicitly -
  the two pre-commitments must not collide silently.
- **OOS STRESS (Sep 17, Appendix AH — read before quoting any number
  above):** the first true holdout (archive ticks after the Sep 16 16:14Z
  freeze line: 0.73d, 97,883 ticks at 1.55× activity, the worst burst day
  on record) lost on EVERY menu cell — C −$5.87 (12 trips), d0 −$6.86
  (14), B −$4.88 (12). Damage was short-side; not one-trade-owned; the
  burst did NOT poison the detector (only 1 minor-class entry). Point
  estimates are therefore REGIME-CONDITIONAL: stitched survival for C
  ladder = 82–89% flip only if cluster days ≤~14% of calendar, ~66% at
  25%, ruinous at 50% (the LI-1 book realized the 50% trajectory). C
  still dominates d0 in every cell — the delay's risk-shaping replicated
  OOS. Consequences: (a) quote menu survival WITH the cluster-frequency
  row, never alone; (b) the 28-day gate must count regime-cluster days
  as a first-class statistic; (c) exactly one new study is licensed —
  P21 (pre-registered): vol-regime DE-SIZING response, since entry
  filters are tombstoned and de-sizing was never tested.
- **ENGINE NOTE (P17):** live fence = ROLLING 5000-tick median, all
  legacy backtests = frozen warmup median. Cells quoted as "rolling"
  are live-valid; frozen cells are historical comparability only.
  Ordinary-tick vol runs in regimes (2.98x on Sep 14-15); any future
  study must state its median convention.
- **Halt-scaling fix — MANDATORY before funding.** The executor's −$9 daily
  stop reads day PnL *after lot scaling*: at 0.3 lots that halts entries on a
  −$3 raw dip (the aim$5+stop$9 cell dropped 85.1%→55.8% — the Sep 9
  "halts are poison" lesson re-found at ladder scale). Fix = threshold in raw
  units or −$9 × lot multiplier. Today's 0.1-lot diagnostic book is unaffected
  (0.1 lot = raw −$9); no deployed config is harmed until the ladder fires.
  **IMPLEMENTED Sep 27:** `daily_stop_usd()` = raw −$9 × round(LOTS/0.1, 6);
  all 5 halt sites + skip-log + banner use it; HB now prints `stop=$N.NN`.
  Unit-verified (0.1→9, 0.2→18, 0.3→27, 0.4→36). Takes effect on the
  executor's next restart — the funding pre-flight (step 0) must verify the
  banner line and the OPEN-day HB `stop=` field before funding.
- The ladder is anti-martingale: rungs read from current equity each day, so
  losses auto-de-size. Wins step up, drawdowns step back down. Never hand-size.

**Impatient:** fund now, long+short: **flip 51.0% / ruin 41.9%** at 4 weeks
(Sep 15 interpolation). A 42% chance of losing the lot — only if the $10 is
genuinely "spent."

**Oct 8 NOT-CONFIRMED fallback:** re-decide with 4 more weeks of data; do
not fund long-only on an unconfirmed edge.

### Oct 8 decision rule (pre-committed Sep 15)

1. Gate **CONFIRMED** and live edge at the gate ≈ backtest (+$0.18–0.26/trade)
   → fund $10, run the gate-winning long-only config on the std ladder.
2. Gate **fails**, OR live edge still ≈ $0.02/trade at n≈300 → **DO NOT fund.**
   Re-decide with 4 more weeks of clean data.
3. Side-split settle (n≥50, fired Sep 17 at exactly n=50): verdict **NO
   escalation** — long weakness not significant (p=0.163) and matched-window
   backtest shows the same short-friendly ordering (regime, not defect).
   Item discharged; recorded in Appendix P addendum (FLIP_INDEX_MECHANICS.md).
4. No config changes between now and the gate — the measurement is the product.
   The ladder exists only for the confirmed branch, never for L+S.

## 5. Standing rules (non-negotiable once funded)

1. Diagnostic phase (L+S, pre-gate): 0.1 lot fixed. Confirmed long-only
   branch: std ladder 0.1→0.4 @ $15/$30/$50 (Appendix W). Either way:
   never "double to recover" — sizing follows equity marks, never losses.
2. Book prints **< $4** → stop everything, review together (floor is coded).
3. The deadline is the calendar, not your mood on a bad week.
4. Losing streaks are the rent: −$0.06 trips are normal; judge the book on
   net + phase capture, never win rate (38% WR is healthy here).
5. Watch, never launch: `powershell -File scripts\flip_status.ps1`.
   Only I launch the executor (the lock refuses duplicates anyway).

## 6. Funding-day runbook (the boring version)

0. **Pre-flight (gate aftermath):** confirm the halt-scaling fix is deployed
   and live (daily stop must be −$9 in RAW units, or −$9 × lot multiplier —
   verify the OPEN-day heartbeat prints the active threshold at the day's
   lot size). Lock the final config the gate decided: exit family (TIMEBND
   5000 vs incumbent). The daily aim is OFF — rejected (Appendix X2) unless
   re-registered with new evidence before the gate. No unfixed
   launches: funding a ladder without halt-scaling re-creates the −$3-raw-dip
   halt bug at size.
1. Re-run `scripts/quote_live_spread.py` on live (free double-check ≤0.63u).
2. Fund exactly $10.
3. Launch deliberately:
   ```powershell
   $env:FLIP_ALLOW_LIVE = "I_UNDERSTAND_THIS_IS_REAL_MONEY"
   python eval/flip_executor.py
   ```
   (in that same PowerShell — the flag dies with the shell, so a crash
   requires a conscious human relaunch, by design)
4. Verify first order in the log carries the 60u SL (line prints on OPEN).
5. Then hands off: tasks, digest, and checkpoint do the rest.

## 7. What to check when something looks wrong

| Symptom | Meaning | Fix |
|---|---|---|
| `AUTOTRADING-OFF!` in heartbeat | MT5 AutoTrading button off | click the button green |
| `[BACKLOG]` lines | wifi/sleep gap, entries suppressed until stream current | nothing — self-heals |
| `[ACCOUNT GUARD] REFUSED` | terminal on wrong account | login demo 32341786 |
| `[EQUITY FLOOR]` | model equity ≤ $4 | STOP — review together |
| executor dead + position open | checkpoint ALERTs, no auto-adopt restart | ping me |

## 8. Incident log (all fixed, all production-tested)

- **Sep 10** double-instance → pidfile+lock, single-instance invariant
- **Sep 11 am** 38k-tick backlog whipsaw after wifi drop → stale-tick guard
- **Sep 11 pm** live-account switch: guard refused orders (0 leakage) but
  revealed 3 bugs: refusal crashed `log_slip` (fixed: refuse ≠ crash),
  silent tracking drop on account switch (fixed: switch detection),
  AutoTrading button off → 10027 loop (my reconnect patch misdiagnosed it;
  fixed: correct classification + heartbeat flag)
- **Sep 11 pm** stale ledger header (9 cols vs 10-field rows) + 7 phantom
  −$943u rows from failed closes → header fixed, rows purged, `log_slip`
  now skips any unfilled order
- **Sep 16** manual MT5 closes were silently dropped (position vanished,
  book kept a ghost until the next counter-spike, no EXIT row, PnL
  unbooked) → heartbeat position-sync every ~15s: user close detected and
  booked as MANEXIT (side-split/telemetry/digest pair it normally), SL
  re-attached if stripped, manual positions adopted. Probe-verified
  end-to-end (`--probe-manual-close`) with probe-tainted rows kept out of
  the production book. User-facing rule: closing by hand is now SAFE and
  logged; opening manual positions while the executor holds one is not
  supported (no hedging logic).

## 9. Contingency decision tree (pre-committed — no improvising under stress)

**Dollar milestones (branch-dependent sizing):**
| Book | Action |
|---|---|
| $15 / $30 / $50 (confirmed branch) | sizing rung fires automatically — 0.2 / 0.3 / 0.4 lots (Appendix W). Diagnostic phase: nothing changes. |
| ≥ $100 | **SUCCESS**: withdraw everything, mission complete; any next cycle is a fresh decision |
| < $10 (any drawdown) | continue — measured drawdowns are part of the plan; sizing auto-steps DOWN with equity (anti-martingale); the floor below is the only hard trigger |
| < $4 | **FLOOR** (coded): executor halts itself; resume only after joint review |

**Time contingencies:**
- Week 3 or 6 with the book flat: continue — losing streaks are rent, the deadline is the calendar.
- Deadline reached below $100: stop, withdraw what's there, record, decide a next cycle with fresh eyes. **Never extend mid-flight.**

**System contingencies:**
- `python analysis/flip_drift_monitor.py` prints **DRIFT SUSPECTED** → stop, re-derive the edge before any funding/continuation.
- Deriv changes/renames/delists the symbol → stop immediately, no rebuild-on-the-fly.
- Post-funding spread re-quote > 0.70u → halt entries, re-measure 24h (transient vs real) before resuming.
- Executor dead + orphaned position (checkpoint ALERT) → close manually in MT5, ping me.
- Symbol offline > 2h with a position open → the 60u SL backstop protects; verify state on reconnect.

## 10. Open items

- **Oct 8**: read `archive/flip_confirm_longonly.log` together → pick branch
- Optional October pre-registrations (4+ weeks of ticks): absorption-gap
  ≥5pp on flip1000 at H=200; hour-of-day filter (~$3–8/wk if real)
- After funding: re-run spread quote once on the funded account

### Incident 2026-09-12: the 18-hour sleep wedge (found by user's log paste)
PC slept repeatedly (Modern Standby cycles in System log). Executor survived
in heartbeats but its MT5 session went deaf: tick feed frozen, ~18h idle.
Scheduled tasks caught up on wake (StartWhenAvailable=True proven); checkpoint
passed it as "alive" - process liveness could not see deafness.

Fixes deployed:
1. Executor deafness watchdog: empty batch + no tick > 300s -> [STALL] ->
   session rebuild (TICK_STALL_SECONDS, 60s cooldown).
2. Checkpoint session_deaf(): executor's own tick count frozen across two
   heartbeats > 3min apart + terminal still streaming -> restart (ADOPT path).
   Errors never trigger restarts; terminal-frozen is reported, not acted on.
3. flip_status.ps1: red DEAF WARN when heartbeat tick-rate < 10/min after 15m
   (healthy ~60/min).
4. Power: sleep/hibernate/disk = never on AC (monitor 10min). Root cause.
5. Tasks: WakeToRun=True added to all three (belt-and-braces).
6. Tick archive: 72h self-heal + manual backfill for the sleep hole.

### Incident 21 — Sep 12: first five-dollar loss (grinddown regime)
Long @9639.99, phase +1 x6 while price bled -52u/106min (6,303 down ticks vs
79 up; up-spikes kept voting the phase long). Exit fired correctly on a real
-11.26u counter-spike: -$5.37. Model book $13.63 -> $8.26 (-17.4% cum).
Verdict: in-sample tail (backtest worst -$5.43, ~1/176 trades), exit rule
blameless, mechanism documented (Appendix H). Decision tree check: $8.26 >
$4 floor -> no plan breach, continue. Drift-stop candidate measured and
parked pending 28-day archive. Lesson: phase age is spike-memory, not price-
memory — during one-sided drift windows the phase signal is actively wrong.

### Study 22 — Grinddown predictability: NO (Sep 12)
Entry-time features cannot flag grind losses (Appendix I): non-monotonic
terciles, no big-loss signature, all filters destroy net and raise ruin at
$10. Defenses stay: counter-spike exit + sizing + candidate 25u drift stop
(still free on long-only, ruin 4.3% -> 2.8% masked). Archive gained a ~20h
unhealable hole (broker keeps only hours of tick history); all backtest
numbers now gap-masked. Consider a second daily collector run to shrink the
exposure window.

### Incident 22 (2026-09-14): sleep relapse x2 + the stale-entry loophole — closed

**What happened.** Two more "Application API" sleeps (Sep 12 23:10->Sep 13
18:59 = 19.8h; Sep 14 01:49->10:37 = 8.8h local) plus a 21s lid sleep. The
Sep 10 power-policy fix had held (STANDBYIDLE AC=0x0) - these were *requested*
sleeps (bedtime presses), which no idle timeout can block. Consequences:
Sep 13 self-healed via the 01:05 store run, but Sep 14 had 5 minutes of ticks
and was expiring against broker history depth; and after the second wake the
executor replayed ~32k stale ticks, rebuilt the pre-sleep phase (+1 x9), and
entered long at "phase age 7" on a stale phase (-$0.96). The backtests have
refused exactly this trade since the gap-mask work; the live executor did not.

**Fixes deployed (three layers):**
1. `eval/flip_executor.py` GAP guard (ports the backtest hole-mask to live):
   consecutive-tick jump >5min = hole -> force-close any position across it,
   reset phase state, skip the fake delta, suppress entries until current.
   Exits and SL stay live as before. Deployed at 12:31Z (PID 696).
2. `scripts/heal_today.py`: same-day UTC rescue in 1h chunks for all four
   flip symbols, idempotent, run at any suspicion of a wedge. Sep 14 healed
   to 11:34Z, zero gaps, ~41.7k ticks/symbol - recovered before depth expired.
3. `scripts/flip_checkpoint.py` now runs heal_today on every 02:30 pass:
   worst-case unhealed window shrinks from 24h to ~25h after a *morning*
   wedge, and any wedge found at 02:30 is rescued same-day while the broker
   still serves it.

**Residual risk, stated honestly:** manual sleep/lid presses cannot be
blocked by policy; the defense is rescue + SL + gap-guard, not prevention.
Positions are dollar-capped by the 60u server SL in every case, so a wedge
costs measurement time, never uncapped risk. Sep 14 tick archive: complete.
