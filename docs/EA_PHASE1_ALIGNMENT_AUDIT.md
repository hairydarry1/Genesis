# EA vs Phase 1 Semantics — Alignment Audit (Sep 9, 2026)

**Subject:** `mt5/SpikeCatcher_v2.mq5` v2.15 (1,606 lines)
**Reference:** the Phase 1-validated fade, as implemented in
`eval/paper_trading_protected.py` (trade-set parity with the Phase 1 backtest
proven in `docs/REPLAY_VS_BACKTEST_GAP.md`).

**Verdict: the EA cannot execute the validated strategy today.** One blocker
(cold-hours cannot be expressed), one fundamental mismatch (exit geometry),
and a default-ON filter stack that has never been tested *on top of* Phase 1
semantics. Every finding below is line-referenced.

## 1. Exit geometry — FUNDAMENTAL MISMATCH

| Behavior | EA (v2.15 defaults) | Phase 1 semantics | Ref |
|---|---|---|---|
| Take-profit | **2.0× median range, intrabar** (~0.05–0.09% of price) | **0.4% of entry, on M1 close** | `TPMultiplier=2.0` (L60); `CheckExitLevels` (L807–833) vs executor `_close_bar` |
| Stop-loss | **1.0× median range exists** (~0.02–0.09%) | **No SL at all** | `SLMultiplier=1.0` (L61); `hitSL` (L824–830) |
| Broker-min flooring | SL floored to `GetMinStopDistance()`, TP rescaled | n/a | L666–676 |
| Attach distance | stops attached at 1.5× distance, lots sized on it | n/a (no stops) | L681–688 |
| Timeout | **5 bars** | **8 bars** | `MaxHoldBars=5` (L58); L613–617 |
| Tick-level safety exit | **ON** (`UseSafetyExit=true`, 2.5×mult tick) | **OFF** (ladder: costs ~$900/wk here) | L74–76; OnTick L388–412 |
| Bar-close double-spike exit | ON — equivalent to executor's `spike_against` ✅ | ON | OnNewBar L473–484 |

The ladder in `docs/REPLAY_VS_BACKTEST_GAP.md` already showed this geometry is
a different (losing) strategy on the same entries: EA geometry −$164 vs Phase 1
semantics +$852 per week. **Alignment means: TP 0.4%-of-entry on bar close, no
SL, hold 8, tick safety exit off.** The attach-distance sizing machinery
(L681–688) can stay — it is how a real 2% risk is delivered and would carry the
0.4%-TP geometry's risk denominator (1.5×-median) unchanged.

## 2. Cold hours — BLOCKER (cannot express the validated list)

The EA gates cold hours as a **contiguous range** (`ColdHoursStart=0`,
`ColdHoursEnd=6`, wrap-allowed — L627–641). The validated edge trades the
**scattered list [1, 5, 12, 14, 15, 19] UTC**. As-is, the EA would trade hours
0–6 (3 of which are unvalidated hot hours: 0, 2, 3, 4) and miss hours
12/14/15/19 — i.e. it would trade a *different, unvalidated strategy*.

**Required change:** replace the range check with a per-symbol hour-list input
(e.g. `input string ColdHoursList = "1,5,12,14,15,19"` parsed in `OnInit`).
Note the EA reads `TimeCurrent()` (server time, L630) — must be normalized to
UTC, which is what the backtests use.

## 3. Entry-gate stack — default-ON, never validated on Phase 1 semantics

These fire *before* the EA ever enters, in `CheckEntry` (L592–720). Each was
individually sim-validated **against the EA's scalp geometry** — not against
the Phase 1 fade:

| Gate | Default | Note |
|---|---|---|
| Loss cooldown, bars (`LossCooldownBars=10`, L88–90; gate L616–621) | ON | Losses cluster — plausible on the fade too, but unproven there |
| Skip double spikes (`SkipDoubleSpikes=true`, window 3; L623–633) | ON | Overlaps with Phase 1's own spike_against exit |
| Cluster gate (`ClusterWindow=10, ClusterMin=3`; L643–650) | ON | |
| Trend filter, M5 EMA (`UseTrendFilter=true`; L652) | ON | |
| M15 BOS regime gate (`UseBOSGate=true`; L654–664) | ON | v2.14 evidence: lifts every symbol on EA geometry |
| Big-cluster gate (ON for 300/500, auto-off 1000s; L666–676) | ON | v2.14 evidence is 300/500-only |
| Trailing max-ratio gate (auto-on crash300 only; L678–688) | off* | crash300 not in the Phase 2 portfolio |
| **Partial entry** (`UsePartialEntry=true`; L690–713, OnNewBar L493–533) | ON | 50% now + 50% if bar-1 winning — changes the exposure path Phase 2 measures |

**Alignment rule: ship Phase 1 semantics with ALL of these OFF** (they are
`input`s, so this is configuration except where noted), then re-add one at a
time only with replay evidence on the fade geometry.

## 4. What already matches ✅

- Spike detection: bar range > 3.0× median, direction by candle body (L443–446)
- Entry timing: 1 bar after spike close (`EntryBar=1`, L636–637; executor
  queues on first tick of next bar — equivalent)
- Median warmup ≥60 bars before entries (L452–457) vs executor's 50
- Sizing philosophy: risk 2% on the *actual* attach distance (L945–966) —
  compatible; keep with the 1.5×-median denominator for parity
- Median window: 100 bars (L54) vs executor's 500 — minor variance source,
  not a semantic break

## 5. Punch-list to align (in order)

> **STATUS (Sep 9, late): items 1–3 IMPLEMENTED in v2.16 (compiles clean,
> 0 errors via MetaEditor). Item 4 (replay parity gate) is NOT yet run — do
> NOT deploy v2.16 to any chart until it passes.**

1. **[BLOCKER] Cold-hours list input** + UTC normalization (§2)
   → **DONE v2.16:** `ColdHoursList="1,5,12,14,15,19"` parsed in OnInit
   (legacy range kept as fallback when empty); gate now uses `TimeGMT()`
   (server `TimeCurrent()` is not UTC on Deriv). Presets no longer override
   cold hours (MAX PROFIT previously disabled them entirely).
2. **[GEOMETRY] Exit overhaul** (§1)
   → **DONE v2.16:** fade mode (`TPGradFactor=0`, default): TP = `TPPercent`
   (% of entry, default 0.4) checked on M1 close; `SLMultiplier=0` = no SL;
   optional `CatastropheSLMult` (default 80× median, attached at 1.0×,
   fill-through accepted — margin insurance per
   docs/TAIL_RISK_STOP_LOSSES.md, NOT the risk model); `MaxHoldBars` input
   (set 8); `UseSafetyExit=false`; bar-close `spike_against` is CORE
   (decoupled from the tick exit); sizing denominator fixed at 1.5× median
   (parity basis, decoupled from stop config); `CheckExitLevels` treats
   zero distance as "exit does not exist"; legacy scalp geometry intact
   behind `TPGradFactor>0`.
3. **[CONFIG] All entry gates OFF by default** (§3)
   → **DONE v2.16:** cluster gate now flag-gated (`UseClusterGate=false`),
   trend filter, double-spike skip, partial entry, bar cooldown all default
   OFF; `UsePartialEntry=true` + fade mode is an INIT error (validated only
   on the scalp).
4. **[VERIFY] Compile + 7-day replay parity:** port the aligned EA logic to
   the diag harness and require trade-set parity with
   `eval/paper_trading_protected.py` before any demo-chart deployment
   → **PENDING — the gate that blocks deployment.**
5. Only then: re-introduce EA filters one at a time on the fade geometry.

## 6. Validation (same-day, Sep tick data)

See `eval/diagnose_replay_gap.py` variant ladder — EA-defaults variant added
and decomposed stepwise back to Phase 1 semantics. Results recorded below
after the run.

## 6. Validation results (Sep 1–7 ticks, EA-defaults ladder)

Added to `eval/diagnose_replay_gap.py` section 3b: a ladder decomposing EA
v2.15 defaults (E0) stepwise into Phase 1 semantics (E5). Fresh $200 book per
symbol, no halt enforcement in E-variants (halt prints in the log are
book-level noise only).

| Variant | All-symbol PnL (7d) |
|---|---|
| E0 EA defaults: to5, SL 1.0×, TP 2.0× intrabar, tick safety ON, hours 0–5, cooldown 10 | **+$258.61** |
| E1 = E0 with hold 8 | +$258.61 (identical — scalp exits dominate before timeout) |
| E2 = E1 with Phase 1 TP (0.4% bar-close) | −$228.83 |
| E3 = E2 without tick safety exit | −$256.54 |
| E4 = E3 without SL (Phase 1 exits, EA hours 0–5) | +$111.99 |
| E5 = E4 with cold hours [1,5,12,14,15,19] (**≡ Phase 1 semantics**) | **+$851.78** |
| E6 = E5 + cooldown 10 bars (proposed EA-aligned default) | +$527.52 |

E5 reproduces ladder variant D exactly (per-symbol: boom500 +$406.34,
crash500 +$623.17, boom1000 +$21.52, crash1000 −$199) — harness parity
re-confirmed.

### Findings

1. **The EA as shipped is a working scalp — but a different strategy.** E0 is
   profitable this week (+$259) with the classic scalp profile (WR 75–93%,
   many tiny TP hits, fat SL/safety tails). But its per-symbol signature is
   the *opposite* of the fade's: E0's profit concentrates in crash1000
   (+$494.17) — the exact symbol that loses on the fade (−$199). The two
   geometries are not two settings of one strategy; they are two strategies
   with uncorrelated (here: inverted) symbol exposures. One week cannot rank
   them, but it kills the assumption that the EA 'carries' the Phase 1 edge.
2. **Hours are worth ~$740/week on the fade** (E4 +$112 vs E5 +$852): the
   scattered [1,5,12,14,15,19] list is a load-bearing component. The EA's
   0–5 window would silently trade a different edge. (Conversely, the scalp
   geometry happens to do *better* in 0–5 hours — quiet-night SL survival —
   which explains E0 vs the earlier −$164 'EA exits + fade hours' variant.)
3. **Cooldown-10 costs −$324/week on the fade** (E6 vs E5) despite the
   v2.14 'doubles EV' claim — that evidence was measured on the scalp
   geometry. Per-symbol: helps boom500 (+$68) and boom1000 (+$113), hurts
   crash500 (−$506). Do NOT ship it as a default on the fade; if wanted,
   validate per-symbol later.
4. **Exit geometry remains the dominant term**: E2/E3 (Phase 1 TP + EA SL
   and/or safety exit) are all negative — mixing geometries is worse than
   either pure configuration. Align wholesale (punch-list §5) or don't.

### Verdict

- Run the **validated fade** on the EA → execute punch-list §5 items 1–3,
  with `LossCooldownBars` default 0 (item 4's parity check will catch
  anything left). E6 shows what a 'fade + cooldown' EA would do (+$528):
  still positive, strictly worse than the pure fade this week.
- The **scalp as its own strategy** (E0) is interesting but unvalidated
  beyond one week and symbol-inverted vs the fade — do not deploy on the
  strength of this sample. If pursued, it needs its own multi-week OOS
  track, and its crash1000-heavy profit concentration would collide with
  the fade's portfolio rationale.
