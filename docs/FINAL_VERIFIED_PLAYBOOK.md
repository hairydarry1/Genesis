# ✅ FINAL VERIFIED PLAYBOOK
## The artifact-checked truth about Boom/Crash trading (Sep 2026)

**Role of this document:** Everything in here survived *corrected* backtests — real gap-through fills, no lookahead, matched controls, out-of-sample halves. Every earlier doc in this folder with PF 2.0+ claims was written before the two simulation bugs below were found. Treat those as historical, not actionable.

---

## 1. The two bugs that invalidated earlier docs

1. **Entry-bar lookahead.** Backtests gave the position credit for the *entry bar's own high/low* — prices that printed *before* the position existed. This manufactured fake PF 2.5–12 edges for "continuation-bar entry", "cluster entry", and "recovery-state" filters. All three concepts are **dead** once the exit walk starts the bar after entry. Related files that still contain this bug and must not be trusted: `eval/cluster_entry_backtest.py`, `eval/recovery_state_backtest.py`, `eval/bar1_validation.py`.
2. **Breakeven-stop gap exemption.** A simulated BE stop was allowed to fill *exactly at entry* when a spike gapped through it. Real BE stops gap through exactly like SL stops (there is no tick at your level). This manufactured fake PF 4.4 / DD÷6 for a breakeven layer. Fixed; BE is genuinely harmful.

`eval/cont_full_backtest.py` and `eval/partial_m1_corrected.py` contain the corrected exit model and are the trustworthy engines.

---

## 2. Measured market reality (30 days, real tick + M1 data)

| Fact | Measurement | Consequence |
|---|---|---|
| Spike timing is **memoryless** | Hazard flat at every wait; E[next gap] ≈ 19.5 min on CRASH1000 regardless of how long you've waited | Nothing predicts *when* — no timer, no rubber band, no regime |
| Indicators don't predict spikes | RSI/Stoch/EMA/MACD/entropy/acceleration ≈ 1.0× lift | No spike-prediction EA exists |
| Price level/position predicts nothing | corr ≈ 0 after controlling recency; 3.1 future spikes from bottom and top alike | Price is the *output* of the timer |
| Hour/weekday are flat | Spikes/hr within ±10% all hours, all days | No "quiet session" to wait for |
| **SL gets gapped through in one tick** | Median adverse gap: CRASH1000 5.94 pts (4–9× the 1.14 SL), BOOM1000 14.2 pts (11–25×) | The loss is set at ~1 tick. No exit mechanism beats the server SL |
| Losses cluster | P(loss after a loss) = 1.28–1.41× baseline | This is the *only* real predictive signal → cooldown |
| Fade wins are timeout-driven | At hold 2–3 most exits are timeouts riding the drift, not TP hits | TP geometry barely matters; time-in-market is the risk driver |

---

## 3. The verified playbook

**Full-size fades lose everywhere** (PF 0.81–1.00, all five symbols) — this is the live experience reproduced. The edge is *entirely* in loss management:

| Layer | Rule | Effect (corrected engine) |
|---|---|---|
| **L1 Partial entry** | 50% now; 2nd 50% joins after 1 bar **only if the first half is profitable** | PF 0.9 → **1.2–1.4**; maxDD ÷4–5. The whole edge. Applies to BOOM **and** CRASH equally |
| **L2 Loss cooldown** | After any losing close, skip the next 3 spike entries | PF +0.04–0.14 more, DD ÷1.5–2, max-loss streaks cut ~half. Costs ~30% of trades |
| **L3 Short hold** | 2–3 M1 bars | Least time under gap exposure. Longer holds only add loss probability |
| **L4 Risk 2%/trade** | Quarter-Kelly under real slippage | Survives the 7–8 loss streaks; raising size amplifies the slipped tail faster than wins |

**Final per-symbol config (partial entry; cooldown 1 on gated charts, 3 on the 1000s; corrected engine, OOS-verified):**

| Symbol | Config | PF | PnL/trade | OOS halves (PF) | Trades/day |
|---|---|---|---|---|---|
| CRASH300 | hold 2 **+ big-cluster gate (8×, 3 bars)** | 2.17 | +0.55 | 2.63 / 1.76 | ~6 |
| CRASH500 | hold 2 **+ big-cluster gate (8×, 3 bars)** | 1.69 | +0.23 | 1.62 / 1.77 | ~11 |
| BOOM500 | hold 2 **+ big-cluster gate (8×, 3 bars)** | 2.00 | +0.46 | 2.06 / 1.96 | ~11 |
| CRASH1000 | hold 5, **no gate** | 1.29 | +0.31 | 1.29 / 1.30 | ~44 |
| BOOM1000 | hold 2, **no gate** | 1.48 | +0.52 | 1.34 / 1.65 | ~53 |

Positive on both halves on all five symbols. Modest but real — and consistent with every live observation.

**Entry-side quality gate (verified 2026-09-03):** the only entry filter to survive the corrected engine + OOS. Enter a fade only when the spike is **≥8× median AND another ≥8× spike occurred within the prior 3 bars** (a "big discharge" — the algorithm then needs time to recharge, so the fade has room to mature). Window 3 beats window 5 on 5 of 6 OOS half-cells across the trio (the calm after a big cluster is a *tight* phenomenon). The 8× cutoff is calibrated to the 300/500 series; on the 1000s the gate is **harmful** (their median range is tiny, so ≥8× fires on ~80% of spikes — retested at 15×/25× and still fails OOS). The gate trades ~85% fewer trades for substantially higher PF (crash300 1.69→2.17, boom500 1.74→2.00 full-sample at cd=1) and roughly half the drawdown.

---

## 4. Things that were tried and are definitively dead

| Idea | Why it died (verified) |
|---|---|
| Continuation-bar entry ("buy the 2nd down bar") | Entry-bar lookahead artifact |
| Cluster/big-spike entry filters | Same artifact; corrected cluster PF ≈ 1.0 |
| Breakeven stop after 2nd half | Gaps go straight through it — converts wins to scratches while keeping gap losses |
| Trailing stop | Trailing adds stop-out points, every one of which can be gap-filled. Catastrophic on BOOM |
| TP 1.25–1.5× geometry | Exits are timeout-dominated at hold 3; TP rarely binds. Boom worse |
| Profit-lock (+10 pts → stop day) | Matched-count control: byte-identical. Pure trade-count illusion |
| Monster-spike gate / wider cluster / escalation / tight day-caps | All just remove trades |
| COLD-hours, M5-trend, double-spike-skip filters | Validated only on the buggy model or as thin WR nudges; don't rebuild on them |

**Why stacking stops at 2 layers:** every further layer re-removes the *same* gap losses (nothing left after partial+cooldown), adds new gap-fillable stop points, or only reduces trade count.

---

---

## 5b. Trailing Max-Ratio Gate (solo-core filter, v2.15)

**What it does:** skips an entry if the largest adverse spike in the last N bars
(excluding the current spike) had a range ≥ Threshold × median. Cluster starts are
preceded by big spikes; solos happen in quiet periods where even the biggest recent
spike was modest.

**Verified result (corrected M1 engine, OOS halves):**

| Symbol | Threshold | Window | full_diff | H1_diff | H2_diff | n kept | % kept |
|--------|-----------|--------|-----------|---------|---------|--------|--------|
| CRASH300 | 8.0× | 20 bars | **+0.181** | +0.233 | +0.130 | 2220 | 32% |

Both halves positive — the filter survives OOS. The H2 is smaller than H1 but still
clearly positive (+0.130 vs random).

**EA settings:**

- `UseTrailingMaxRatioGate` — **auto-ON for crash300**, manual override via input
- `TrailingMaxRatioThreshold = 8.0` (crash300 verified value)
- `TrailingMaxRatioWindow = 20` bars
- **Auto-OFF for all other symbols** (not verified). Set `UseTrailingMaxRatioGate=true`
  to test on other symbols.

**Mechanism:** the gate removes 68% of entries (cluster members + starts) while keeping
43% of solos — clean selectivity. Average PnL of kept entries: +0.411 vs +0.230 random.

**Why 8.0× and not higher/lower:** the fine sweep (th=4,5,6,7,8,10,12,15) shows a
non-monotonic curve — th=4/5 are enormous but too thin (n=44/73), th=6 collapses in H1,
th=8 is the sweet spot with practical n. th=10/15 survive OOS but are weaker.

---

## 5. EA deployment state (mt5/SpikeCatcher_v2.mq5 — v2.15)

- `UsePartialEntry = true` — applies to **all symbols** (was CRASH-only; that restriction came from the old no-SL backtest and was refuted)
- `LossCooldown = 3`
- `UseBigClusterGate = true`, `BigClusterMult = 8.0`, `BigClusterWindow = 5` — **auto-disabled on 1000 Index symbols** (verified: the gate only holds on 300/500). Startup log prints the gate state so you can confirm
- `UseTrailingMaxRatioGate` — **auto-ON for crash300** (th=8.0×, 20-bar window). **Auto-OFF for all other symbols.** Set `UseTrailingMaxRatioGate=true` to test on other symbols. Startup log prints the gate state.
- The old all-size cluster filter (skip 3+ spikes in 10 bars) is neutral-to-slightly-harmful on the corrected engine — the big-cluster gate is its better-motivated replacement
- Safety exit (tick spike detection → close) stays on: it cannot beat a single-tick gap, but it limits damage on multi-tick cascades
- SL/TP handled broker-realistically: open with no stops, attach after fill against the real open price, floor SL to broker minimum and scale TP to preserve the 2:1 ratio, close immediately if stops can't attach

**Recommended per-chart settings (v2.09):**

| Chart | MaxHoldBars | Gate | LossCooldown |
|---|---|---|---|
| CRASH300 | 2 | auto-ON (8×, **3** bars) + trailing max-ratio (8×, 20 bars) | **1** (skip the single post-loss spike — verified 2026-09-03) |
| CRASH500 / BOOM500 | 2 | auto-ON (8×, **3** bars) | **1** (skip the single post-loss spike — verified 2026-09-03) |
| CRASH1000 | 5–7 | auto-OFF | 3 |
| BOOM1000 | 2 | auto-OFF | 3 |

Loss clustering survives the gate (P(loss\|loss) = 1.26–1.52× baseline on gated trades), so a cooldown is still justified — but the gate already removes most weak entries, so cd=1 beats cd=3 on the gated charts (OOS PF 1.69–1.79 vs 1.44–1.75). The 1000s still want cd=3 for its second-half robustness (crash1000: PF 1.30 vs 1.09 at cd=0).

---

## 6. Trustworthy tooling

- `eval/partial_m1_corrected.py` — corrected M1 engine (partial/full, OOS) — **the reference backtester**
- `eval/cont_full_backtest.py` — corrected engine with cluster/cooldown and daily view
- `eval/defence_layers.py`, `eval/defence_axes.py` — layer stacking + controls (BE gap rule and matched-count control are implemented correctly here)
- `eval/hold_time_tick_sim.py`, `eval/hold_time_levers.py` — tick-level sims (no lookahead possible) — partial ≈ PF 2.0 on CRASH at tick level
- `eval/sl_gap_analysis.py` — measured gap-through magnitudes per symbol
- `eval/state_edges.py` — the four state-dependence probes (recharge/drift/squeeze/up-streak: only drift-stretch showed a correlation, and it wasn't tradeable)

**Buggy / superseded scripts (do not reuse):** `eval/cluster_entry_backtest.py`, `eval/recovery_state_backtest.py`, `eval/bar1_validation.py` (entry-bar lookahead); `eval/m1_partial_fast.py` (no SL at all — source of the old PF 2.26 claim).

---

## 7. Monthly dollar projection (5 lots, $1/point/lot) — 2026-09-03

**Assumptions (state them every time this is quoted):**
- All charts run at the `MaxLots = 5` cap — this is what actually happens on a ~$1,000–2,000 account because the broker SL floors make 2%-risk sizing exceed 5 lots. At $1,000 the real risk/trade is 0.6% (crash1000) to 1.2% (crash300), NOT 2%.
- **$1 per point per 1.0 lot** — cross-checked exactly against live `[sl]` PnL lines (Crash 300: median 1.074 × 5 lots = −$5.37; Crash 1000: median 0.369 × 5 = −$1.845). **Verify per-symbol $/pt/lot against a real filled trade before trusting dollars.**
- Win sizes from the corrected sim (they match live timeout wins: ≈ +1.4 pts ≈ $7 at 5 lots on Crash 1000). Loss sizes from **tick-measured gaps**, NOT the sim (M1 bars can't see single-tick gaps; sim losses are ~2× too gentle).

| Chart (config) | Trades/mo | Win $ | Loss $ (gap) | EV/trade | Monthly $ | Sim worst-DD $ |
|---|---|---|---|---|---|---|
| CRASH300 (gate 8×/3, cd1, h2) | ~190 | +9.5 | −25 | +0.85 | +160 | −135 |
| CRASH500 (gate, cd1, h2) | ~350 | +3.7 | −17 | ≈0.05 | +15 | −40 |
| BOOM500 (gate, cd1, h2) | ~330 | +5.7 | −24 | +0.25 | +80 | −65 |
| CRASH1000 (no gate, cd3, h5) | ~1,360 | +8.6 | −30 | +0.65 | +900 | −280 |
| BOOM1000 (no gate, cd3, h2) | ~1,640 | +9.4 | −45 | +0.35 | +570 | −480 |

**Two ways to run the book:**

| Profile | Monthly $ (mid) | Swing (monthly σ) | Worst month |
|---|---|---|---|
| All 5 charts at 5 lots | **+1,700** | ±1,000 | can be negative |
| 1000s at **2.5 lots**, 300/500 at 5 | **+1,000** | ±500 | rarely negative |

**What the gate is for:** the three gated charts have the shape that survives anything (PF 1.7–2.2, worst 31-day DD −$40 to −$135) but make modest dollars because they trade ~10×/day combined. The dollar volume lives on the ungated 1000s (80+ trades/day) where EV is thin (+$0.35–0.65/trade) against −$30–45 tails — two boom1000 gaps can wipe two weeks of gated profit. **This is the quantified version of your live experience.** The half-size-on-1000s variant is the honest "reduced-swing" middle ground, since the gate provably fails there.

**Paper-trade before believing any dollar number.** The win sizes and trade counts are sim/live-anchored; the loss sizes carry the most uncertainty and drive everything.

---

*Written 2026-09-03. Corrected-engine results only. When in doubt, re-run `eval/partial_m1_corrected.py` before believing any new claim.*
