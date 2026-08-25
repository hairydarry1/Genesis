# Synthetics Brain — World Model Validation Milestone (Build Brief)

**Scope:** This milestone builds and validates ONLY the world model for the synthetic indices brain. No policy, no execution, no autonomy. Success here is a strict gate — do not proceed to policy training until this passes.

**Goal:** Given a window of recent price/tick state, predict the distribution of the next N ticks (price movement + volatility), and prove that prediction is meaningfully better than naive statistical baselines, out-of-sample, across volatility regimes.

---

## 1. Instruments in scope for this milestone
Start with ONE synthetic index to prove the pipeline before multiplying:
- Volatility 75 Index (or your preferred starting synthetic — pick the one you have the most historical data for)
- Once this passes end-to-end, replicate for Boom/Crash and other Volatility indices as separate world models (not shared weights)

## 2. Data pipeline

**Session 0 (light, do this first): `data/deriv_connection.py`**
- Small `DerivConnection` class wrapping the official `python-deriv-api` library (deriv-com/python-deriv-api)
- Handles what the base library does NOT: active keepalive ping (every 15-20s, well under Deriv's 30s idle timeout), reconnect-with-backoff on drop, and re-subscription replay after reconnect
- On startup, call `website_status`/`server_status` to read current `api_call_limits` rather than hardcoding assumed rate limits
- Structured logging: every drop, reconnect attempt, and rate-limit hit gets a timestamped log line
- **Sanity check before moving on:** leave it running against a demo account overnight (or several hours minimum); confirm zero silent gaps in tick reception and that it survives at least one forced disconnect
- Reference: FinRL (AI4Finance-Foundation/FinRL) is worth having Freebuff inspect for its environment/data-processor patterns before writing custom pipeline code — don't copy wholesale (it's model-free-oriented, ours is a hybrid), but reuse what fits

**Module: `data/ingest.py`**
- Pull historical tick data from Deriv API using the `DerivConnection` wrapper from Session 0 (never talk to the raw websocket library directly)
- Store as raw tick log: timestamp, price, (bid/ask if available)
- Storage format: pick one explicitly (e.g. Parquet for efficient columnar storage/querying) rather than ad-hoc CSVs — this matters once you're accumulating months of tick data
- Target: as much history as Deriv exposes, minimum recommendation a few months of continuous tick data
- **Sanity check:** resampled bars have zero unexpected gaps; spot-check a sample against Deriv's own chart

**Module: `data/resample.py`**
- Resample raw ticks into fixed intervals (e.g. 1s, 5s, 1m bars) — synthetics tick fast, so pick a resolution that matches realistic decision frequency
- Output: OHLC(V) bars per resolution, cleanly indexed by time, no gaps

**Module: `data/splits.py`**
- Implement walk-forward splitting: NOT random shuffle. E.g. rolling windows — train on months 1-3, validate on month 4, slide forward
- Output: a reusable split generator other modules call, so every experiment uses the same splits (avoids silent leakage bugs)

## 3. Feature/state representation
**Module: `features/state.py`**
- Multi-timeframe encoding: build parallel bar series at 2-3 resolutions (e.g. 1m, 15m, 1h) from the same resampled data
- Volatility features: rolling realized volatility, volatility-of-volatility, time-since-last-large-move
- Statistical fingerprint features (synthetic-specific): time-since-last-spike (for Boom/Crash types), streak counters, mean-reversion vs persistence indicators
- Structural features: FVG/iFVG detector as a separate deterministic module (see section 6) feeding in as additional context features

**Output:** a single function `build_state(timestamp) -> feature_vector` that all downstream modules call — keep this the single source of truth for "what the model sees."

## 4. World model
**Module: `model/world_model.py`**
- Input: feature_vector from `build_state`
- Output: predicted distribution (not point estimate) of price movement over next N ticks/bars — e.g. predicted mean + variance, or a small set of quantiles
- Architecture: start simple — a multi-branch network (one small encoder per timeframe → concatenate → fusion layer → distributional output head). Resist the urge to make this complex before the simple version is validated.
- Training objective: negative log-likelihood or quantile loss against actual next-N-tick outcomes (NOT plain MSE — you want calibrated uncertainty, not just a point guess)

## 5. Baselines (must be implemented BEFORE judging the world model)
**Module: `eval/baselines.py`**
- Naive persistence: "next price = current price"
- Mean reversion: "next price reverts toward recent rolling mean"
- Random walk with historical volatility: "next price = current price + noise sampled from recent realized volatility"
- These are the bar the world model must clear. If it can't beat these out-of-sample, stop and rework features/architecture before continuing.

## 6. Structural detection layer (two sub-layers, both in `features/structures.py`)

**6a. Shared foundational utility (build first, everything else depends on it)**
- Swing-point detector: identifies local swing highs/lows given a lookback window; requires a minimum number of bars on each side to count as a genuine extreme (not chop noise)
- Every detector below that needs swing points consumes THIS single implementation — do not let each structure re-implement its own swing logic

**6b. Deterministic quality filter (still non-learned — this is housekeeping, not the brain's relevance judgment)**
Before ANY structure instance is logged or passed downstream, it must pass hard-coded quality thresholds:
- Minimum size relative to recent ATR (a gap/block smaller than this is noise, discard entirely — don't log it as "low quality," just drop it)
- Momentum/impulse check where relevant (e.g. the candle that created an FVG should show real range/momentum, not just drift)
- Retest/failure invalidation (e.g. an order block that's been retested and failed N times is no longer structurally valid — configurable N)
- These thresholds must be calibrated PER INSTRUMENT (Volatility 10 vs Volatility 100 have very different baseline volatility) — do not hardcode one global threshold across all synthetics
- **Sanity check:** manually verify against a plotted chart that filtered-out instances really do look like noise, and instances that pass really do look like genuine structures

**6c. Structure detectors (each consumes swing points + quality filter from above)**
- **FVG**: standard 3-candle gap — bullish: candle 1 high < candle 3 low; bearish: candle 1 low > candle 3 high
- **iFVG**: FVG that transitions unmitigated → fully mitigated, then shows reversal/rejection at the same zone
- **Order Blocks**: last opposing candle before a strong directional move; zone = that candle's high/low
- **Liquidity Sweeps/Grabs**: price wicks beyond a swing high/low then closes back inside within N bars
- **Break of Structure (BOS)**: close beyond a prior swing high/low in the direction of the existing trend
- **Change of Character (CHoCH)**: first break of structure AGAINST the prevailing trend sequence
- **Equal Highs/Lows (EQH/EQL)**: two or more swing points within a tolerance band (e.g. fraction of ATR)
- **Premium/Discount Zones**: midpoint of range between a recent swing high and swing low — simple derived context feature, not its own detector
- **Mitigation Blocks**: failed-breakout zones price is expected to revisit; can likely share order-block detection logic with a different trigger condition

**Output per structure instance** (feeds into `build_state` as raw context features): type, zone/location, size relative to ATR, age, mitigation status, distance from current price, multi-timeframe confluence flag (does this zone align with the same structure on a higher timeframe)

**Division of labor, restated:** 6b (quality filter) decides whether something is structurally REAL and gets logged at all — deterministic, no learning. The world model / policy (later milestones) decide whether a structurally-real instance is actually RELEVANT to a trading decision — this is learned, not hardcoded. Do not conflate the two.

## 7. Evaluation — what "passing" means
**Module: `eval/validate.py`**
- Run world model predictions across ALL walk-forward splits (not just one)
- Score against each baseline from section 5, using:
  - Predictive accuracy (e.g. quantile/interval coverage — are 80% confidence intervals actually right ~80% of the time?)
  - Performance broken out BY volatility regime (calm vs choppy vs spike periods) — report separately, don't just average
- Produce a report: per-split, per-regime accuracy vs baseline, plus overall calibration plot (predicted vs actual outcome frequency)

**Pass criteria (lock these in before running, not after seeing results):**
- Beats all three naive baselines out-of-sample, on average AND in at least the two most common volatility regimes
- Calibration is reasonable (not wildly overconfident) — define acceptable deviation before you look at results
- No single walk-forward split catastrophically fails (a model that's great on average but blows up in one regime is not passing)

## 8. Suggested Freebuff session breakdown
0. Session 0 (light): `data/deriv_connection.py` — build and stress-test the connection wrapper alone; confirm it survives an idle period and a forced disconnect before anything depends on it
1. Session 1: `data/ingest.py` + `data/resample.py` (using the Session 0 wrapper) — get clean historical bars flowing, verify no gaps
2. Session 2: `data/splits.py` + `eval/baselines.py` — get walk-forward splitting and baselines working and sanity-checked FIRST, independent of any model
3. Session 3: `features/state.py` + `features/structures.py` — build the feature pipeline, unit-test that `build_state` produces sane, consistent vectors (no NaNs, consistent shape)
4. Session 4: `model/world_model.py` — build and train the model
5. Session 5: `eval/validate.py` — run full evaluation, generate the calibration/regime report
6. Session 6: review results together against the pass criteria from section 7 — go/no-go decision before touching policy code

**After each session:** run the listed sanity check before starting the next one — catching a bug immediately is far cheaper than finding it during Session 5's evaluation.

## 10. Stricter validation pass (before anything touches real capital, later milestone)
Plain walk-forward (section 3, 7) is the working validation method for this milestone, but research shows it can still be fooled by testing against just one sequence of market conditions. Before any brain is trusted with live capital (not this milestone — flagging for later), add a stricter pass:
- **Combinatorial Purged Cross-Validation (CPCV)** — tests across combinatorially many train/test arrangements from the same data instead of one forward slice
- **Deflated Sharpe Ratio** — corrects for the fact that testing many strategy variations makes some look good by chance alone
This is a "final exam," not a replacement for the walk-forward checks already in section 7.

## 9. Explicitly out of scope for this milestone
- Policy/action selection
- Reward shaping
- Any live/paper trading
- Gold or forex — synthetics only, for now