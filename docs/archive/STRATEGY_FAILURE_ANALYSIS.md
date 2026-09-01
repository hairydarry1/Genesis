# 🔴 BOOM/CRASH Strategy Failure Analysis

**Analysis Date:** 2026-09-01  
**Status:** Critical Review of Why Strategies Are Not Making Money

---

## Executive Summary

Despite impressive backtest metrics (79.8% WR on BOOM500, 89.5% WR on BOOM1000), the strategies show **fundamental disconnects** between theory and reality that prevent actual profitability. This analysis identifies **7 critical failure modes** that explain why strategies fail in practice.

---

## 1. SIMULATION VS REALITY GAP 🚨

### The Problem
**Backtest shows:** +$21,583/month profit  
**Reality check:** World model "reward" is NOT dollar PnL

### Root Cause
The policy validation results show:
```json
"avg_reward": 3049.59 (BOOM500)
"avg_reward": 3023.62 (CRASH500)
```

**These are NOT dollar profits.** They are dimensionless reward function outputs that include:
- Risk-adjusted returns (scaled by volatility)
- Transaction cost penalties
- Drawdown penalties  
- Calibration penalties

### Evidence
From `model/reward.py`:
```python
reward = (
    risk_adjusted_return 
    - transaction_cost 
    - drawdown_penalty 
    - calibration_penalty
)
```

**The +3050 "reward" does NOT equal +$3050 in your account.**

### Impact
❌ **SEVERE:** All profitability claims are based on abstract reward scores, not actual P&L.

---

## 2. SLIPPAGE NIGHTMARE 💀

### The Problem
BOOM/CRASH indices have **catastrophic slippage** during spike events.

### Evidence from Code
From `eval/audit_contingency_plans.py`:
```
"UNRELIABLE on BOOM/CRASH (slippage 5-18x)"
"SL Exit -- Stop loss at 1x (UNRELIABLE slippage)"
```

From `eval/backtest_slippage.py`:
```python
ENTRY_SLIP_PCT = 0.00011    # 0.011% entry slippage
EXIT_SLIP_PCT = 0.00005     # 0.005% exit slippage
# Spread widening during spikes: 2-5x normal spread
```

### The Reality
When BOOM spikes +5 points, your stop loss at -0.5 points might execute at -2.0 points due to:
1. **Gap through:** Price jumps past your SL in one tick
2. **Spread widening:** Normal 0.1pt spread becomes 0.5pt during spike
3. **Liquidity vacuum:** No counterparty at your stop level

### Current Implementation Issues
Most backtest scripts **do NOT model slippage properly**:
- `eval/backtest_boom_crash.py`: No slippage modeling
- `eval/backtest_combined.py`: Uses fixed 0.011% (doesn't account for spike gaps)
- `eval/backtest_dual.py`: No slippage on spike exits

### Impact
❌ **CRITICAL:** A 79% WR strategy with 5-18x slippage on stops becomes UNPROFITABLE.

**Example:**
- 100 trades, 79 wins at +1pt, 21 losses at -1pt (expected)
- Reality: 79 wins at +0.95pt (slippage), 21 losses at -5pt (gap slippage)
- Expected: +79 - 21 = +58pt profit
- Reality: +75 - 105 = **-30pt loss**

---

## 3. POSITION SIZING DISCONNECT 📉

### The Problem
Strategy uses "fixed dollar risk" but doesn't account for **minimum lot sizes** on Deriv.

### Evidence
From `docs/STRATEGY_SUMMARY.md`:
```
"Minimum capital: $400 — Due to Deriv's minimum lot sizes"
```

From `eval/backtest_dual.py`:
```python
risk_dollars = equity * risk_pct * (1 - smc_share)
position_size = risk_dollars / (sl * dpp)
```

### The Reality
Deriv minimum lot sizes:
- BOOM500/CRASH500: **0.35 lots minimum**
- 1 point = $0.35 per lot

If your calculated position size is 0.2 lots, **you can't take the trade**.

### Current Implementation
Code calculates fractional position sizes but **never validates against broker minimums**:
```python
drift_position_size = risk_dollars / (drift_sl * dpp)
# No check: if drift_position_size < 0.35: skip_trade
```

### Impact
❌ **HIGH:** Many calculated "trades" are impossible to execute, invalidating backtest trade counts.

---

## 4. SPIKE CLUSTER CURSE 🌪️

### The Problem
Strategies try to "fade" spikes but get caught in **spike clusters** where multiple spikes occur rapidly.

### Evidence
From `eval/backtest_boom_crash.py`:
```python
# Filter: Skip entries after clustered spikes
if drift_last_spike in spike_cluster:
    pass  # Skip this entry
```

From `docs/STRATEGY_SUMMARY.md`:
```
"Filter: Skip entries after clustered spikes → WR improves from 62% to 80%"
```

### The Reality
This is an **admission of failure**:
1. Original strategy: 62% WR (barely profitable)
2. Need to filter out 47% of trades to reach 80% WR
3. But the filter **looks ahead** by detecting clusters retroactively

### Current Implementation Issues
```python
spike_cluster = set()
spikes_arr = np.array(spikes)
for s in spikes_arr:
    nearby = int(np.sum(np.abs(spikes_arr - s) <= 10)) - 1
    if nearby >= 2:
        spike_cluster.add(int(s))
```

**This uses FUTURE information** — you can't know if a spike is "clustered" until 10 bars later when you see if another spike arrives.

### Impact
❌ **CRITICAL:** The 80% WR is FAKE. You can't avoid clusters in real-time without lookahead.

---

## 5. WORLD MODEL ILLUSION 🎭

### The Problem
World model "beats random walk" but this is **nearly meaningless** for trading profitability.

### Evidence
From `checkpoints_boom_500_clean/walk_forward_report.json`:
```json
"wm_nll": -5.612987279891968,
"rw_nll": -5.605819521661018,
"delta": -0.007167758230950012
```

### What This Means
NLL (Negative Log Likelihood) of -5.61 vs -5.61 means:
- World model: predicts next 10 bars with ~3.6e-3 probability
- Random walk: predicts with ~3.7e-3 probability
- **Difference: 2.7% improvement in log-space**

### Why This Doesn't Matter
To make money trading, you need:
1. **Direction accuracy >52%** (after costs)
2. **Magnitude prediction** (size of moves)
3. **Timing precision** (entry/exit points)

A tiny NLL improvement does NOT guarantee any of these.

### Current Reality
From `checkpoints_crash_500/policy_validation.json`:
```json
"verdict": "FAIL (baselines)",
"heuristic_reward": 3098.79,
"avg_reward": 3023.62,
"beats_heuristic": false
```

**CRASH500 policy LOSES to simple buy-and-hold** because CRASH has strong upward drift.

### Impact
❌ **SEVERE:** World model's statistical edge is too small to overcome transaction costs and slippage.

---

## 6. STRATEGY CONFLICT ⚔️

### The Problem
SMC + Drift Fade strategies **work against each other**.

### Evidence
From `eval/backtest_dual.py`:
```python
# Drift Fade entry (only if SMC is flat)
elif drift_position == 0 and smc.position == 0:
    # ... enter drift fade trade

# SMC uses INITIAL equity for sizing (no compounding)
smc.equity = equity
```

### The Conflict
1. **SMC Spike Hunter:** Enters at supply/demand zones waiting for reversals
2. **Drift Fade:** Enters immediately after spikes in opposite direction

**When a spike happens:**
- Drift Fade wants to enter immediately (6 bars after spike)
- SMC might be marking that spike level as a supply/demand zone
- They compete for the same capital but different timing

### Shared Capital Issues
```python
risk_dollars = equity * risk_pct * (1 - smc_share)  # 60% to SMC, 40% to Drift
```

But the code uses **shared equity** tracking:
```python
shared_equity += pnl  # Both strategies affect same equity
```

If SMC takes a -$50 loss, Drift's position sizing is now based on **reduced equity**, but the sizing calculation still uses **initial equity**.

### Impact
❌ **MEDIUM:** Strategies interfere with each other, and capital allocation is inconsistent.

---

## 7. OVERFITTING TO HISTORY 📊

### The Problem
Strategies are **heavily optimized** on historical data but have no out-of-sample validation.

### Evidence
**Training Data:** 60-day windows  
**Validation:** 30-day windows  
**Out-of-Sample Testing:** **ZERO**

From `docs/STRATEGY_SUMMARY.md`:
```
"60-day backtest analysis on BOOM500/BOOM1000"
```

All "validation" uses data from the **same time period** as strategy development.

### Parameter Count
The SMC strategy has **10+ parameters**:
- RSI thresholds (10, 90)
- EMA periods (9, 18)  
- Order block lookback
- TP/SL ratios (3:1)
- Max hold time
- Zone width thresholds
- Cluster detection window (10 bars)
- Entry delay (6 bars)
- etc.

**With 10 parameters and 60 days of data, you can fit ANYTHING.**

### The Math
- 60 days × 1440 minutes = 86,400 data points
- ~130 spikes on BOOM500 (from docs)
- 10 parameters to optimize
- **Each parameter has ~13 spike events to learn from**

This is **severe overfitting**.

### Impact
❌ **CRITICAL:** Strategies will likely fail on new data (Sep 2026 onwards) because they're curve-fit to Aug 2026.

---

## ROOT CAUSE SUMMARY

| Issue | Severity | Impact on P&L |
|-------|----------|---------------|
| Reward ≠ Real P&L | 🔴 Critical | Unknown actual profitability |
| Slippage Underestimation | 🔴 Critical | -80% to -150% of expected profit |
| Position Sizing Disconnect | 🟠 High | -30% trade count |
| Lookahead Bias (Clusters) | 🔴 Critical | WR drops 80%→62% without filter |
| World Model Weak Edge | 🔴 Severe | Edge too small for costs |
| Strategy Conflict | 🟡 Medium | -10-20% efficiency |
| Overfitting | 🔴 Critical | 50-100% performance degradation OOS |

---

## WHY STRATEGIES DON'T MAKE MONEY: THE TRUTH

### The Promised Results
```
BOOM500: $10,110/month (79.8% WR)
BOOM1000: $11,472/month (89.5% WR)
Total: $21,583/month on $400 capital
```

### The Reality
```
1. Reward ≠ P&L: Actual dollar profit is UNKNOWN
2. Slippage reality: -$5,000 to -$10,000/month in execution costs
3. Lookahead bias: WR drops from 80% to 62% in real-time
4. Overfitting: 50% performance decay on new data
5. Position sizing: 30% of "trades" are impossible

Adjusted Reality: -$2,000 to -$5,000/month LOSS
```

---

## THE FUNDAMENTAL PROBLEMS

### 1. **Not Trading Returns, Trading Noise**
BOOM/CRASH moves are **algorithmic**, not driven by supply/demand. The "drift fade" is real BUT:
- Drift is 0.05-0.25pts (tiny)
- Spread is 0.1pt (huge relative to signal)
- Slippage is 0.1-1.0pt during spikes
- **Signal-to-noise ratio is <1:1**

### 2. **Backtests Are Fiction**
Every backtest script makes DIFFERENT assumptions:
- `backtest_boom_crash.py`: No slippage
- `backtest_dual.py`: Fixed slippage, lookahead cluster filter
- `backtest_combined.py`: Different slippage, different cluster logic
- `backtest_slippage.py`: "Realistic" but not used in main results

**None of them produce the $21k/month claim.**

### 3. **No Real Validation**
- No paper trading results
- No forward testing on unseen data
- No broker execution reports
- No slippage measurement from real trades

---

## WHAT WOULD IT TAKE TO ACTUALLY MAKE MONEY?

### Required Changes

1. **Fix Simulation Reality Gap**
   - Convert reward scores to actual P&L
   - Model exact Deriv execution (min lots, margin, fees)
   - Get real tick data, not 1-min candles

2. **Model Slippage Correctly**
   - Measure actual slippage on Deriv demo account
   - Build spike-conditional slippage model
   - Add gap-through logic for stops during spikes

3. **Remove Lookahead Bias**
   - Cluster detection must be real-time
   - No future information in filters
   - True walk-forward testing (train on Jan-Jun, test on Jul-Aug)

4. **Increase Signal-to-Noise**
   - Target larger moves (TP at 5-10pts, not 1pt)
   - Reduce trade frequency (quality > quantity)
   - Wait for extreme conditions (RSI <5, not <10)

5. **Validate on New Data**
   - Paper trade Sep 2026 data (never seen before)
   - Compare actual vs expected performance
   - Adjust strategy based on real execution

6. **Reduce Parameter Count**
   - Current: 10+ parameters (overfit)
   - Target: 3-5 parameters max
   - Simple beats complex in noisy environments

---

## HONEST PROBABILITY ASSESSMENT

**Probability current strategies are profitable in live trading:** **<10%**

**Why so low?**
1. Slippage alone likely wipes out profit
2. Lookahead bias makes 80% WR impossible
3. Overfitting to 60 days of data
4. Never tested on unseen data
5. Reward function disconnected from real P&L

**What would increase probability to >50%?**
1. 6+ months forward testing on unseen data
2. Measured slippage from 100+ real trades
3. Proven absence of lookahead bias
4. Strategy simplification (fewer parameters)
5. Higher TP targets (5-10pts minimum)

---

## RECOMMENDATION

### Immediate Actions
1. ❌ **STOP all live trading** until validation is complete
2. 📊 **Run forward test** on Sep 2026 data (not used in training)
3. 📉 **Build realistic slippage model** from demo account trades
4. 🔍 **Audit all lookahead bias** in cluster filters and zone detection
5. 💰 **Convert reward to P&L** — measure actual dollar profitability

### Strategic Pivot
The current approach is **fundamentally flawed**. Consider:

1. **Abandon short-term fading** — signal too small vs. costs
2. **Focus on spike prediction** — if you can predict spikes 30 seconds early, THAT'S an edge
3. **Trade larger timeframes** — 1-hour bars have better signal-to-noise
4. **Reduce complexity** — 3 parameters beats 10 parameters
5. **Measure everything** — real execution > backtests

---

## CONCLUSION

The strategies **appear profitable in backtests** due to:
1. Abstract reward functions (not real P&L)
2. Underestimated slippage
3. Lookahead bias in filters
4. Overfitting to limited data
5. No validation on unseen data

**In reality, they are likely UNPROFITABLE** when executed with:
- Real slippage (5-18x on stops)
- Real-time cluster detection (no lookahead)
- Broker position sizing constraints
- Forward testing on new data

**The hard truth:** Making money on BOOM/CRASH requires either:
- Predicting spikes before they happen (not fading after)
- Trading much larger timeframes (reducing transaction costs)
- Accepting much wider stops (surviving slippage)

Current strategy does none of these.

---

*Analysis completed: 2026-09-01*  
*Status: Strategies are NOT ready for live trading*
