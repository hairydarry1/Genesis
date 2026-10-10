# Flip1000 executor — shutdown, demo week, re-fund (written 2026-10-10)

State at hand-off: **live account 22031121 (DerivSVG-Server, REAL) is effectively blown
(book ~$1.40), the executor PID 22860 is alive but halted (`floored:true`), no open
position.** The PC is being shut down. Plan: **demo week → re-fund $10 the week after.**

Read `docs/FLIP1000_DIAGNOSIS.md` for *why* it blew and *what to change* before trading
this again. This file is only "how to operate it".

---

## 0. Paths and constants you will need

| thing | value |
|---|---|
| repo root | `C:\Users\hp\Desktop\code\Genesis` |
| python | `C:\Users\hp\AppData\Local\Python\bin\python.exe` |
| executor | `eval/flip_executor.py` |
| data dir (live) | `data/flip_exec/` |
| log the user tails | `archive/flip_exec.log` (stderr: `archive/flip_exec.err.log`) |
| live account | **22031121** — DerivSVG-Server (REAL) |
| demo account | **32341786** — DerivSVG-Server |
| symbol / key | `Crash Boom Flip 1000 Index` / `flip1000` |
| magic | 620001 |
| lot / VPU | 0.1 / $0.10 per price unit |
| backstop SL | 60.0 units (~$6.00 at 0.1 lot), server-side |
| daily stop | raw day PnL ≤ −$9.00 (UTC day) |
| equity floor | book ≤ **$1.50** → `floored:true`, permanent halt until manual review |
| entry delay | 300 ticks (~5.0 min), cancelled by an opposite spike |
| spike gate | `|delta| > 5 × median5000` and `≥ 0.15u` |

---

## 1. Before you power the PC off (do these in order)

1. **Confirm flat** — no open position on the symbol:
   `python scripts/flip_power_guard.py` → must print `RULING CLEAR_TO_POWER_OFF`.
   That script requires: on AC, no live executor PID, `day_state` halted=false floored=false.
   It will refuse now (executor alive + floored), so do steps 2–3 first.
2. **Kill the executor** (read the PID from `data/flip_exec/executor.pid`, currently 22860):
   ```bash
   taskkill //F //PID 22860
   python -c "import subprocess;r=subprocess.run(['tasklist','/FI','PID eq 22860','/NH'],capture_output=True,text=True);print('ALIVE' if 'python' in r.stdout.lower() else 'DEAD')"
   ```
3. **Disable the two keepalive tasks — THIS IS THE ONE THAT BITES.**
   `\FlipExecutor-Keepalive-Sweep` runs `scripts\flip_keepalive.cmd` every 30 min and that
   script **sets `FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY`**, so it will resurrect
   the executor in LIVE mode within 30 minutes of logon, on its own, signed in to whatever
   terminal is open:
   ```bash
   MSYS_NO_PATHCONV=1 schtasks /Change /TN "\FlipExecutor-Keepalive-Sweep" /DISABLE
   MSYS_NO_PATHCONV=1 schtasks /Change /TN "\FlipTickCollector-Keepalive" /DISABLE
   ```
   Verify: `MSYS_NO_PATHCONV=1 schtasks /query /fo LIST | grep -A2 -i "Flip"` → both `Disabled`.
4. **Power off freely.** The three daily read-only tasks (`\Genesis FlipCheckpoint Daily`
   02:30, `\Genesis FlipConfirm Daily` 02:45, `\Genesis FlipTick Daily` 01:05) are analysis
   only — safe to leave, and they are useful to keep.

Re-enable the keepalives only on the day you deliberately go live again (§3).

---

## 2. Demo week

**The demo/live switch is a single env var + which terminal MT5 is logged into.**

- `assert_demo_account()` (in `eval/flip_executor.py`) accepts a demo account always, and a
  live account **only** if `FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY` was set *in the
  shell that launched it*. So: never set it → live orders are refused; and the keepalive
  (which sets it) must stay disabled (§1.3).
- **Do not launch via `scripts/flip_keepalive.cmd` for demo** — it forces the live flag.

### 2.1 Keep demo results out of the live ledger (important)

`OUT_DIR` used to be a hardcoded `"data/flip_exec"` for **both** modes, so a demo session
would have appended demo trades into the live `trades.csv` / `slippage_ledger.csv` /
`day_state.json`. It is now overridable:

```bash
FLIP_OUT_DIR=data/flip_demo   # new: defaults to data/flip_exec when unset
```

Launch demo with that set (creates the dir), so the live book stays untouched. Belt and
braces: back up the live dir anyway before the first demo run:
`cp -r data/flip_exec data/flip_exec_backup_20261010`.

### 2.2 Demo launch

1. Open MT5, **log into the demo account** (32341786).
2. Confirm the terminal is demo *before* starting: `python scripts/quote_live_spread.py`
   (or any script printing `account_info()`), look for `trade_mode=0`.
3. Launch:
   ```bash
   cd /c/Users/hp/Desktop/code/Genesis
   FLIP_OUT_DIR=data/flip_demo /c/Users/hp/AppData/Local/Python/bin/python.exe -u eval/flip_executor.py
   ```
   (Detached, if you prefer: `start "flip-demo" /B ...python.exe -u eval\flip_executor.py 1>>archive\flip_demo.log 2>>archive\flip_demo.err.log`
   from `cmd.exe`, with `FLIP_OUT_DIR` set in that same shell.)
4. **Verify the banner.** You must see:
   - `account 32341786 (demo)` — **not** `(LIVE - FLIP_ALLOW_LIVE armed at launch)`;
   - `[BOOK] equity restored from ledger: $... (cumulative $10.00 model book; floor checks honest)`
     — demo uses the **$10 model book**, not the real balance (live anchors to the balance);
   - no `[EQUITY FLOOR]`, no `[HALT]` line.
5. Keep it running; watch `archive/flip_demo.log` (or stdout).

### 2.3 Demo stop

```bash
taskkill //F //PID $(cat data/flip_demo/executor.pid)
```

### 2.4 Demo decision rule (pre-register it now, before you look at the data)

Aggregate at ≥40 round trips, comparing against the 34-day archive baseline
(long-only ≈ +$7.64/wk, 37.9% ruin):

- **PASS** if net > $0 **and** no single UTC day worse than −25% of the book **and**
  the solvency gate never had to block more than ~50% of triggers (if it blocks more, the
  sizing is the problem, not the strategy).
- **FAIL** on any of: net ≤ $0, a single-day drawdown > 25%, or an unexplained ledger gap
  (book vs `account_info().balance` drifting by more than $0.02 without a `[BALANCE-OP]` row).
- n < 40 → **inconclusive**, run longer. Do **not** fund on fewer trips than that.
  (The last two live sessions were n=11 and n=10 and lost in *both* directions; that is noise.)

---

## 3. Going live again with the new $10

1. MT5 → log into **22031121** (live). Confirm `trade_mode=2`.
2. **Clear the halt** (the floored latch survives restarts by design; it only clears by hand).
   With the executor stopped, either delete or edit `data/flip_exec/day_state.json`:
   `{"date": "<today>", "day_pnl": 0.0, "day_halted": false, "floored": false}`
   (`rm data/flip_exec/day_state.json` also works — a missing file starts clean.)
3. Re-enable the keepalive(s): `schtasks /Change /TN "\FlipExecutor-Keepalive-Sweep" /ENABLE`
   (and `/TN "\FlipTickCollector-Keepalive" /ENABLE` — the collector feeds the engine's
   median benchmark; without it the gate loses its data).
4. Launch **in the same shell** that carries the live flag:
   ```bash
   cd /c/Users/hp/Desktop/code/Genesis
   FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY \
     /c/Users/hp/AppData/Local/Python/bin/python.exe -u eval/flip_executor.py
   ```
   (or `MSYS_NO_PATHCONV=1 cmd.exe /c "scripts\flip_keepalive.cmd"`, which does the same thing detached.)
5. **Verify:** `LIVE MODE ARMED`, `[BOOK] LIVE anchor: book = real balance $X`, the
   `[BALANCE-OPS]` lines, and **no** `[EQUITY FLOOR]` line. Then `scripts/quote_live_spread.py`
   to confirm book == balance.

### 3.1 Nightly routine (unchanged)

Withdraw the day's profit each night, restart each morning at ~$10. Those movements appear as
`DEAL_TYPE_BALANCE` ops; `reconcile_book()` books the delta every 60 s, prints `[BALANCE-OP]`,
re-checks the floor, and logs the row to `data/flip_exec/balance_ops.csv` (kept out of
`trades.csv`/`slippage_ledger.csv` so the scorers stay clean).

### 3.2 The VPS trigger, re-priced

Trigger = balance ≥ **$25** ($10 funded + $15 P/L). At the measured long-only rate
(+$7.64/wk on the 34-day archive) that is ~2 weeks **if** the edge holds — but the same
study puts long-only ruin at **37.9%**, so there is roughly a 1-in-3 chance the account dies
first. See `docs/FLIP1000_DIAGNOSIS.md` §5 for the sizing alternative that makes the trigger
reachable.

---

## 4. Ledgers, state files and what each one means

| file | meaning |
|---|---|
| `data/flip_exec/trades.csv` | every entry/close with the model book's equity after it |
| `data/flip_exec/slippage_ledger.csv` | ENTRY/EXIT pairs with slippage in units |
| `data/flip_exec/day_state.json` | `{date, day_pnl, day_halted, floored}` — the halt latch |
| `data/flip_exec/uptime.json` | ticks/spikes/heartbeats/pending counters (AI-audit) |
| `data/flip_exec/engine_state.json` | restart-neutral phase/pending state |
| `data/flip_exec/balance_ops.csv` | non-trade money movements (deposits/withdrawals) |
| `data/flip_exec/executor.pid` | single-instance lock; stale files are ignored |
| `data/flip_exec/_rolling_med_flip1000.npz` | cached rolling medians for the studies |

**Halt semantics (know these before you debug "why is it not trading"):**
`day_halted` clears at the UTC day roll; `floored` does **not** — it latches until a human
edits `day_state.json` (§3.2). Both only block **new entries** — neither can close an open
position. There is **no solvency gate** in the code: at a tiny book the executor will happily
arm entries it cannot afford, and the broker's margin stop-out fires long before the 60u SL
(that is how the Oct 7 trade died, not the SL).

---

## 5. Hazard list (each of these has already caused a real problem)

1. **Keepalive is live-armed.** `scripts/flip_keepalive.cmd` sets `FLIP_ALLOW_LIVE`; it will
   resurrect a LIVE executor 30 min after logon. Disable it (§1.3) for demo.
2. **Shared `OUT_DIR`.** Demo must use `FLIP_OUT_DIR=data/flip_demo` (§2.1).
3. **Model book vs real balance.** Only live mode anchors to `account_info().balance`; demo
   uses the $10 model book. Never compare a demo book to a live balance.
4. **Margin stop-out < SL.** With ~$10 and 0.1 lot, the stop-out (~$0.80 equity) is far inside
   the 60u/$6.00 backstop. The SL caps the *trade*, not the *account*.
5. **Template spread trap.** The tick archive reports a 0.0500u spread; the tradeable spread is
   **0.6261u round trip** (proven from 37 live deals). Never derive edge from the archive spread.
6. **Tick depth is ~72 h.** The tick store cannot reach further back; older windows silently
   return nothing. Anything older must come from `data/flip_ticks/`.
7. **Don't run scripts while the executor is live** unless read-only — it holds a single-instance
   lock and writes the same CSVs.
8. **Windows/MSYS quirks.** Bash here is Git Bash: use `//F` for `taskkill`, `MSYS_NO_PATHCONV=1`
   for `schtasks`; long commands sometimes hang — use short ones, one at a time.

---

## 6. Quick inventory of the tooling

**Launchers / ops**
- `scripts/flip_keepalive.cmd` — detached LIVE launch (sets the flag). *Disable for demo.*
- `scripts/flip_store_keepalive.cmd` — tick-collector keepalive.
- `scripts/flip_power_guard.py` — "is it safe to power off?" advisor.
- `scripts/quote_live_spread.py` — live spread / account probe.
- `scripts/flip_checkpoint.py`, `scripts/flip_flat_exit.bat`, `scripts/heal_ledger_slfail.py`.

**Studies (all in `analysis/`, outputs in `archive/`)**
- `flip_rolling_engine.py` — `run_conv`: the reference engine the executor mirrors.
- `flip_regime_desize.py` — P21 regime de-sizing study (owns VPU/SPREAD_USD/MULT/DELAY).
- `flip_drift_gate.py` → Parts A/B/C (drift predictability + counter-drift gate).
- `flip_drift_regime.py` → Parts D/E (regime persistence + alignment engine).
- `flip_spike_size.py` → entry-spike size vs outcome (the "tiny spikes" hypothesis).
- `flip_sl_backstop.py` — stop-loss sweep (60u is optimal).
- `flip_drift_monitor.py` — monthly spec-change alarm (92.5 spikes/day baseline).
- `flip_conditionality.py` (`load`, `WARMUP`), `flip_gapmask.py`, `flip_concepts.py`.

**Reading**
- `docs/FLIP1000_DIAGNOSIS.md` — this shutdown's findings + the fix.
- `docs/FLIP_INDEX_MECHANICS.md` — engine mechanics, Appendices T/U/AE.
- `docs/FLIP_DOSSIER_FOR_REVIEW.md`, `docs/EDGE_CATALOG_30.md` (#29: spike anticipation is impossible).
- `docs/runbooks/oct5-6_flip1000_funding_cutover.md`, `docs/runbooks/power_guard.md`.
- `.state/session_state.toml` — the season's decision log; read the tail for the latest state.
