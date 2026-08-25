# Synthetics Brain — Full-Pipeline Backtesting Milestone (Build Brief)

**Precondition:** The policy/value training milestone (see `synthetics-policy-brief.md`) has passed its gate. This milestone validates the FULL pipeline acting together — world model + policy + confidence gate + governor — not any single component in isolation.

**Scope:** Simulate the complete decision pipeline over historical data and produce a trustworthy, scrutinizable simulated track record. No real money, no live connection required — this runs entirely against historical data. Paper trading (live data, no real execution) is the milestone AFTER this one.

---

## 1. Full pipeline simulation
**Module: `backtest/engine.py`**
- Replays historical data bar-by-bar (or tick-by-tick, matching the resolution used in training) through the entire stack in sequence:
  1. World model produces state prediction
  2. Policy produces action + confidence
  3. Confidence gate routes: high confidence within risk bounds → "would auto-execute"; medium/novel conditions → "would flag as advisory"; low confidence → discarded
  4. Governor (see section 3) checks the action against hard limits before it's recorded as a valid simulated trade
- Output: a full simulated trade log — every decision point, the action taken (or not), confidence score, and outcome
- **This must reuse the exact same feature pipeline, world model, and policy code used in training** — no reimplementation, no shortcuts, or the backtest isn't testing what will actually run live

## 2. Execution realism
**Module: `backtest/execution_sim.py`**
- Apply spread and slippage at every simulated trade (same cost model as training, for consistency)
- Optional stress mode: simulate degraded execution conditions during high-volatility periods (wider spread, partial fill probability) — run the backtest both with and without this to see how sensitive results are to execution quality assumptions

## 3. Governor simulation
**Module: `backtest/governor_sim.py`**
- Replay the governor's hard-coded rules (drawdown circuit breaker, position limits, per-brain autonomy status) against the simulated trade sequence
- Explicitly verify: during the worst historical drawdown period in the data, does the circuit breaker actually trigger and halt autonomous mode? If not, that's a governor logic bug to fix before proceeding — not something to note and move past
- Log every governor intervention (halted trade, size reduction, autonomy suspension) with the reason, same as a real audit trail would need

## 4. Metrics and reporting
**Module: `backtest/metrics.py`**
Beyond the Sharpe/Deflated Sharpe already computed in the policy milestone, compute:
- Max drawdown and drawdown duration (how long underwater, not just how deep)
- Win rate, profit factor
- Calmar ratio (return relative to max drawdown)
- Per-volatility-regime breakdown (reuse regime classification from the world-model milestone) — report separately, not just aggregated
- Confidence calibration on the full simulated run (does stated confidence match realized accuracy across the whole pipeline, not just the policy in isolation)

## 5. Monte Carlo / bootstrap resampling
**Module: `backtest/monte_carlo.py`**
- Take the sequence of simulated trades and reshuffle their order (or resample with replacement) thousands of times, recomputing key metrics (max drawdown, final return) each time
- Report the distribution, not just the point estimate — e.g. "5th percentile max drawdown across 10,000 resamples was X%"
- **Purpose:** reveals whether results depend on a lucky ordering of wins/losses. A strategy that looks fine in the original historical order but falls apart under reshuffling has fragile, order-dependent performance and should not be trusted

## 6. Lookahead bias audit (treat as a mandatory checklist, not an assumption)
**Module: `backtest/lookahead_audit.py`**
- Every feature — especially structural detectors (swing points, FVG, order blocks, etc.) — must only ever use data available at or before the decision timestamp
- Concrete check: run the full pipeline once normally, then run it again with all data beyond each decision point deliberately blacked out/masked, and confirm the two runs produce identical decisions up to that point
- Any divergence means a feature or detector is leaking future information — this must be fixed, not worked around, since it silently inflates every result upstream of it
- This audit is not optional and not a "nice to have" — a passing backtest built on lookahead-biased features is worse than no backtest at all, because it creates false confidence

## 7. Pass criteria (lock in before running, not after seeing results)
- Full pipeline metrics (section 4) meet or exceed the policy-milestone's standalone results (a well-functioning governor/gate shouldn't meaningfully hurt performance — if it does, investigate why before assuming the governor is just "being safe")
- Governor demonstrably intervenes correctly during the worst historical drawdown (section 3)
- Monte Carlo resampling (section 5) shows the strategy holds up reasonably across the distribution, not just in the original historical order — define an acceptable percentile bound before running (e.g. "5th percentile max drawdown must stay within an acceptable bound")
- Lookahead audit (section 6) passes cleanly with zero divergence
- Regime breakdown (section 4) shows no catastrophic regime where the system performs disastrously without the governor catching it

## 8. Explicitly out of scope for this milestone
- Live connection / paper trading (next milestone)
- Real capital in any form
- Gold or forex — synthetics only

## 9. Suggested Freebuff session breakdown
1. Session A: `backtest/engine.py` — wire the full pipeline together against historical data, reusing existing trained components
2. Session B: `backtest/execution_sim.py` + `backtest/governor_sim.py` — add execution realism and governor replay
3. Session C: `backtest/metrics.py` — full metrics suite, regime breakdown
4. Session D: `backtest/lookahead_audit.py` — run this BEFORE trusting any results from sessions A-C; if it fails, results from earlier sessions may need to be redone after the leak is fixed
5. Session E: `backtest/monte_carlo.py` — resampling analysis
6. Session F: review full results together against section 7 criteria — go/no-go decision before paper trading