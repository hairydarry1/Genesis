# Oct 5–6 Flip1000 funding / cutover runbook

Single source of truth for this window. Data references: `eval/flip_executor.py`, `scripts/flip_checkpoint.py`, `scripts/flip_manual_counterfactual.py`, `data/flip_exec/`.

## The facts the runbook is built on (read directly from the executor source)

| Setting | Value | Where |
|---|---|---|
| Sizing | fixed **0.1 lot**, `$0.10/unit` | `LOTS = 0.1`, `VPU = 0.10` |
| Daily entry halt | day PnL `<= -$9.00` per UTC day (raw units; active threshold prints `stop=$N.NN` on the heartbeat) | `DAILY_STOP_RAW_USD = 9.0`, `daily_stop_usd()` |
| Equity floor | model equity `<= $1.50` → permanent halt (`"floored": true`). Lowered from `$4.00` on 2026-10-06 — see *Book ≠ balance* below | `EQUITY_FLOOR_USD = 1.5` |
| Catastrophic backstop | broker-side 60u server-side SL on every entry/adopt (≈ worst-case $6) | `SL_BACKSTOP_UNITS = 60.0` |
| Live entry | `FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY` **at launch** (flag dies with the shell; 02:30 task restart cannot inherit it) | `ALLOW_LIVE` |
| Live account gate | default: demo only; with flag, accepts live but only if the launching shell carries the flag | `assert_demo_account()` |

## Book ≠ balance (read this before trusting the floor)

An equity floor is only as honest as the book it reads. Until 2026-10-06 the
book was `10.0 + realised PnL from the trades ledger`, which is **structurally
blind to `DEAL_TYPE_BALANCE` deals** — deposits, withdrawals, transfers and
charges move the real balance without writing any ENTRY/EXIT row.

On Oct 5 at 20:58 a **−$4.00 balance op** (deal `20709033588`, comment
`R-d2932223-5662-49cc-bbaa-743c5`, `vol=0.0 px=0.0`) took the real account to
**$3.70** while the book still read **$7.63**. The $4.00 floor therefore sat
**$3.93 above reality** and could not have fired before a wipeout. The trades
themselves were fine: real trade PnL was −$2.30 against the book's −$2.37.

Now fixed in `eval/flip_executor.py`:

- a **live** run anchors the book to `account_info().balance` at startup
  (`is_live`, i.e. `ACCOUNT_TRADE_MODE_REAL`). The ledger trade-book survives
  only as a cross-check: any gap between the two is by definition a balance op
- `report_balance_ops()` lists recent `DEAL_TYPE_BALANCE` deals at launch and
  records them to `data/flip_exec/balance_ops.csv` (deduped across restarts)
- `reconcile_book()` re-checks every 60s while live, books any drift, and
  **re-runs the floor**, so a mid-session charge can trip it immediately
- `EQUITY_FLOOR_USD` lowered **4.0 → 1.50**: on a $3.70 book an honest $4.00
  floor halts everything on sight. 1.50 leaves room to trade and still fires
  **above the broker's stop-out**, so the floor — not a margin call — ends the run.

**The 60u backstop is not the worst case on a small book.** At 1:1000 leverage
0.1 lot needs only `$1.60` of margin, so a stop-out lands at ~`$0.80` equity =
roughly `$2.90` of floating loss — far inside the ~$6 the backstop implies.
Never quote the backstop as the tail risk without checking the balance first.

## Committed constraints (yours)

1. **SL stays as-is.** No reduction. The program's own ladder/stop study (Sep 10: `$9` optimal; `$5` → 0% flip / 94.5% ruin) does not support a lower SL, and the Phase-3 design is tail-based sizing, not a smaller stop.
2. **Oct 5–6 = clean flat, then no executor left running into a power loss with an open position.** Machine is on battery at 52%; Oct 5–6 power is not constant, so the executor only runs with the machine on AC and the runbook's flat-exit checkpoint completed first.
3. **Funded entry = $10; VPS trigger = funded P/L >= $15.** You deposit **$10** (the $15 VPS price is what the account must *earn*, not what you add). Fund $10 and launch the executor with `FLIP_ALLOW_LIVE`; when funded P/L reaches $15 you buy the VPS and pivot the account to it.
4. **Host plan:** the $10 account trades on the PC first. When funded P/L reaches **$15**, buy the VPS and **migrate the whole executor to it** — `eval/`, `scripts/`, and `data/flip_exec/` — so trading continues while the PC is off. The VPS becomes the permanent host; this is not an account-side-only pivot. The spare-machine plan is abandoned.

## Why no smaller SL is being evaluated

- The Sep 10 ladder and the 2000-sim stop study show any stop `<= -$5` collapses (e.g., −$5 → 0% flip / 94.5% ruin; −$9 → optimal).
- The failure mode is multi-bar adverse carry (spikes at 9–73× the median bar range blow through stops). The design response is **tail-based sizing / account-level floor**, which is Phase-3 architecture, not yet built. A reduced SL is not supported by the program's own history and would break the pre-registered config.

## Window order

- **Oct 5:** clean flat exit (read below) + reconcile the books, then run the power guard (`docs/runbooks/power_guard.md`). Fund **$10** and launch the funded executor on the PC with `FLIP_ALLOW_LIVE` in the same shell.
- **Oct 5 -> VPS trigger:** the $10 account trades live on the PC. When funded P/L reaches **$15**, buy the VPS, **migrate the executor + scripts + `data/flip_exec/` to it**, and run the executor there so trading survives the PC being off. If the account runs dry before $15, the cycle fails — stop, record the final balance, do **not** re-fund from savings.

## Oct 5 – flat exit checklist

1. Confirm the machine is on AC (plugged in), not hibernating/leaving the executor in an unknown state.
2. Run the flat-exit checklist script:
   ```bat
   python scripts\flip_flat_exit.bat
   ```
   Then run the counterfactual on the real file:
   ```bat
   python analysis\flip_manual_counterfactual.py
   ```
   Expect 10W/2L (the established manual-counterfactual baseline, n=15); do not force a close that isn't a real close.
3. Verify the ledger is whole before you touch the executor:
   ```bat
   python scripts\heal_ledger_slfail.py
   ```
   (if it refuses because the Sep 16 backup dir exists, confirm the producer already emits MANEXIT + SL-backstop rows and proceed to step 4.)
   Confirm the walk prints no phantom/slfail rows remaining, then also confirm `data/flip_exec/trades.csv` + `slippage_ledger.csv` are consistent:
   - `slippage_ledger.csv` **must include** MANEXIT rows + SL-backstop rows, otherwise `flip_checkpoint.py` rebuilds a daily digest that understates the book (the Sep 17 bug — confirm the producer already adds them; they are present in this file).
   - `day_state.json` must show `day_pnl`, `day_halted`, `floored` consistent with the reconciled book.
4. Stand the executor down **flat**: close any broker position by hand in MT5 (captured as MANEXIT by the heartbeat), then go through the checklist in `scripts\flip_flat_exit.bat` end to end (\[7\] day_state, `[9]` sync, `[10] STOP`). Verify `data/flip_exec/day_state.json` is `day_halted: true, floored: false` and no open position exists in MT5 before powering off. Do **NOT** run the executor overnight/power-loss with an open position.
   - Do **NOT** run the executor overnight/power-loss with an open position.
5. Record the exit: timestamp, reason, final day-state.

## Oct 5 – fund $10, then trade to the $15 VPS trigger

Do this **after** the flat exit and the VPS cost floor is reached:

1. Place the VPS with a real-money account when its cost reaches the floor (or before), then re-quote the funded account:
   ```bat
   python scripts\quote_live_spread.py
   ```
   Must be `<= 0.63u`; otherwise halt entries and re-measure 24h.
2. Fund **exactly $10**.
3. Launch the executor on your PC with the live arm **in the same shell**;
   ```bat
   $env:FLIP_ALLOW_LIVE = "I_UNDERSTAND_THIS_IS_REAL_MONEY"
   python eval\flip_executor.py
   ```
   The flag takes effect only on that launch (it dies with the shell; the 02:30 task restart cannot inherit it).
4. Verify: first order prints the 60u backstop (or `ADOPT` re-attaches it), `[ACCOUNT GUARD]` did not refuse the launch, and the first trade prints `pnl` + `eq`.
5. Power rule after funding: the executor only runs while AC is present; never leave it running into a power loss with an open position (60u backstop caps the worst case, but a power loss with an open position invalidates the measurement day).

## VPS migration (the whole executor moves)

- Until P/L reaches $15, everything stays on the PC; the flat-exit checkpoint and power-guard rules govern Oct 5–6.
- At **funded P/L >= $15**: buy the VPS, then migrate `eval/flip_executor.py`, `scripts/`, and the entire `data/flip_exec/` book.
- **Do the copy with the PC executor stopped.** Never let two hosts write the same book: the single-instance lock and `data/flip_exec/executor.pid` guard against double-launch, but the file copy itself must happen while the PC instance is down.
- Verify on the VPS (banner, first heartbeat, ledger append), then treat the VPS as the permanent host. This is the only way trading is genuinely uninterrupted, because the PC at the new location has no reliable power.

## VPS cost-floor gating (repeat every time the runbook is used)

- **Pre-condition for funding:** the **$15 VPS must be payable out of the funded account's own P/L**. Deposit **$10**, trade live, and the trigger to buy the VPS is **funded P/L >= $15** (balance >= $25). Until then the VPS is not purchased.
- **Gate:** before any real-money funding, re-quote the live spread on the account you will fund (`scripts/quote_live_spread.py`): must be `<= 0.63u`, otherwise halt entries and re-measure 24h.
- **Funding**
  1. Confirm the halt-scaling fix is live in the executor:
     - OPEN-day heartbeat prints `stop=$9.00` (raw units at 0.1 lot).
     - The flag `ALLOW_LIVE` is only honored at launch, so there is no stale build from an old PID still running.
  2. Fund **exactly $10**.
  3. Launch deliberately with the flag in the **same shell**:
     ```bat
     $env:FLIP_ALLOW_LIVE = "I_UNDERSTAND_THIS_IS_REAL_MONEY"
     python eval\flip_executor.py
     ```
     No `>>` redirect on the script's own log files (seen cause of PermissionError earlier) — the executor writes `archive/flip_exec.log`, `archive/flip_exec.err.log`, `day_state.json`, `engine_state.json`, `uptime.json`, `executor.pid`; run it from a clean shell, not through a redirect.
  4. Verify first order in the log carries the 60u backstop (or `ADOPT` re-attaches it), `[ACCOUNT GUARD]` did not hold the launch, and the first trade prints `pnl` with `eq`.
- **Power rule in the funded period:** the executor only runs when AC is present. Never leave it running into a power loss with an open position (60u backstop caps the worst case, but a power loss with an open position invalidates the measurement day).

## Hard stops / manual review

| Symptom | Action |
|---|---|
| `ACCOUNT GUARD REFUSED` | login back to demo; reconcile; restart the executor |
| `[EQUITY FLOOR]` / `floored: true` | STOP; do not flip `"floored": false` without joint review |
| `[BALANCE-OP]` in the log | a deposit/withdrawal/charge moved the real balance; the book has been re-anchored and the floor re-checked. Confirm the op was yours |
| Book ≠ MT5 balance | the anchor failed or an op landed mid-run; `python scripts/quote_live_spread.py` prints the real balance. Do not trust the floor until they agree |
| `[DAILY STOP]` | entries halt; review before re-arming |
| Executor dead + position open | close manually in MT5, then reconcile; ping for review |
| `[AUTOTRADING-OFF!]` | click MT5 AutoTrading green (human action, not directed) |
| `DRIFT SUSPECTED` | stop; re-derive the edge before funding/continuation |
| Post-funding spread > 0.70u | halt entries; re-measure 24h before resuming |

## Oct 8 branch marker (unchanged)

No new gate is registered for Oct 6. The 28-day confirmation is read from `archive/flip_confirm_longonly.log` on Oct 8 (it fires on the 28-day archive crossing, not on a new day). The Oct 5 cutoff is: flat, reconciled, and no open position left running into power loss. The VPS migration happens when **funded P/L reaches $15** — at that point `eval/`, `scripts/`, and `data/flip_exec/` all move to the VPS.

## Verification commands

```bat
powershell -File scripts\flip_status.ps1
python scripts\flip_manual_counterfactual.py
python scripts\heal_ledger_slfail.py
python scripts\quote_live_spread.py
python analysis\flip_drift_monitor.py
python scripts\flip_flat_restart.py --dry-run
```

`quote_live_spread.py` is the only read-only way to see the **real** balance and
re-quote the spread gate; run it after any `[BALANCE-OP]`. `flip_flat_restart.py
--dry-run` prints the planned flat-moment restart without touching anything.
