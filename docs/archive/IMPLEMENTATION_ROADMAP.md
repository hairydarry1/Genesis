# 🚀 BOOM/CRASH IMPLEMENTATION ROADMAP
## From Analysis to Live Trading (Step-by-Step)

**Date:** 2026-09-01 22:14 UTC  
**Purpose:** Actionable plan with realistic expectations  
**Timeline:** 2-4 weeks before live trading

---

## Current Situation Summary

You have analyzed your BOOM/CRASH strategies and discovered:

✅ **The Good News:**
- Drift fade edge EXISTS (verified from data)
- Velocity pattern EXISTS (85.7% BOOM, 79.2% CRASH have 12+ buildup)
- COLD hours work (19-22% fewer spikes)
- Basic strategy shows 90% WR in clean backtest

❌ **The Bad News:**
- Profit claims are 30-70x inflated ($21k → $300-500 realistic)
- Velocity 85.7% is historical pattern, NOT live catch rate
- Expected live catch rate: 60-75% (accounting for latency)
- Slippage will reduce WR from 90% to 60-70%

⚠️ **The Unknown:**
- Does velocity pattern still exist in Sep 2026?
- What's the actual live catch rate?
- What's real slippage on your platform?
- Does strategy work on new data?

---

## The Roadmap (4 Phases)

### Phase 1: VERIFY (Days 1-2)
### Phase 2: PAPER TRADE (Days 3-16)
### Phase 3: ANALYZE (Days 17-18)
### Phase 4: GO LIVE (Day 19+)

---

## Phase 1: VERIFY PATTERNS (2 Days)

**Goal:** Confirm the edge still exists in Sep 2026 data

### Day 1 Morning: Fetch Fresh Data

```bash
# Fetch Sep 2026 tick/candle data
cd "C:\Users\hp\Desktop\code\Genesis"

# Fetch latest data
python data/fetch_candles.py --symbol BOOM500 --start 2026-09-01 --days 7
python data/fetch_candles.py --symbol CRASH500 --start 2026-09-01 --days 7

# If you have tick data access:
# python data/fetch_1000_ticks.py --symbol BOOM500 --start 2026-09-01
```

**Expected:** New data files in `data/` directory

---

### Day 1 Afternoon: Verify Velocity Pattern

```bash
# Test if 85.7%/79.2% pattern still holds
python eval/test_velocity_12.py --start-date 2026-09-01 --end-date 2026-09-07
```

**Success Criteria:**
- ✅ BOOM500: 75-90% of spikes have 12+ downtick buildup
- ✅ CRASH500: 70-85% of spikes have 12+ uptick buildup
- ✅ Mean buildup: 40-60 ticks

**Red Flags:**
- ❌ Pattern drops to <70% (pattern is decaying)
- ❌ Mean buildup drops to <30 ticks (spikes becoming more sudden)
- ❌ False trigger ratio increases >0.30

**Decision:**
- If pattern holds: PROCEED to Phase 2
- If pattern weak: STOP and investigate why
- If pattern gone: ABANDON velocity exit, use safety only

---

### Day 2: Verify COLD Hours

```bash
# Test if COLD hours still reduce spikes
python eval/backtest_cold_hours.py --start-date 2026-09-01 --days 7
```

**Success Criteria:**
- ✅ COLD hours show 15-25% fewer spikes
- ✅ WR improvement during COLD hours: 5-15%

**Decision:**
- If COLD hours work: USE them
- If no difference: SKIP this filter

---

### Day 2: Run Forward Test Backtest

```bash
# Backtest on Sep 2026 data (never seen during development)
python eval/backtest_combined.py --start-date 2026-09-01 --days 7 \
    --velocity-threshold 12 \
    --safety-multiplier 3.0 \
    --tick-exit 6
```

**Success Criteria:**
- ✅ Win Rate: 55-75%
- ✅ Profit Factor: >1.2
- ✅ Return: +2% to +8% (7 days)

**Red Flags:**
- ❌ WR drops to <50% (strategy overfit)
- ❌ Profit Factor <1.0 (not profitable)
- ❌ Large drawdown >15%

**Decision:**
- If backtest passes: PROCEED to Phase 2
- If backtest fails: STOP and debug

---

## Phase 2: PAPER TRADE (14 Days)

**Goal:** Test strategy with real execution (demo account)

### Setup (Day 3)

1. **Open Deriv Demo Account**
   - Go to Deriv.com → Demo account
   - Fund with $10,000 demo money
   - Get API token for trading

2. **Deploy Paper Trading System**
```bash
# Start paper trading
python eval/paper_trading_protected.py \
    --symbol BOOM500,CRASH500 \
    --equity 200 \
    --risk 0.02 \
    --velocity-threshold 12 \
    --cold-hours "1,5,12,14,15,19"
```

3. **Set Up Monitoring**
```bash
# Start dashboard (separate terminal)
python dashboard/spike_fade_server.py --port 8080

# Open browser: http://localhost:8080
```

---

### Daily Tasks (Days 4-16)

**Every Morning:**
1. Check dashboard for overnight results
2. Review trade log: `logs/trades/trades_YYYYMMDD.csv`
3. Calculate key metrics (see below)

**Every Evening:**
4. Review today's performance
5. Update tracking spreadsheet
6. Check for anomalies

---

### Tracking Spreadsheet (Critical!)

Create `paper_trading_tracker.xlsx` with these columns:

| Date | Trades | Wins | Losses | WR% | PnL | Velocity Exits | Safety Exits | Spikes Hit | Catch Rate | False Exits |
|------|--------|------|--------|-----|-----|---------------|--------------|------------|-----------|-------------|
| 9/3  | 12     | 8    | 4      | 66.7| $2.40| 3            | 1           | 4          | 75%       | 2           |
| 9/4  | 15     | 10   | 5      | 66.7| $3.10| 4            | 0           | 4          | 100%      | 3           |
| ...  | ...    | ...  | ...    | ... | ... | ...          | ...         | ...        | ...       | ...         |

**Key Formulas:**
```
Win Rate = Wins / Trades
Catch Rate = Velocity Exits / (Velocity + Safety Exits)
False Exit Rate = False Exits / Velocity Exits
Daily Return % = PnL / Starting Equity
```

---

### Week 1 Checkpoints (Days 4-9)

**After 3 Days (Day 6):**
- Trades: 30-50 expected
- Check: Is velocity exit working?
  - Target: 3-8 velocity exits
  - Target: 60-80% catch rate
  - Red flag: <50% catch rate

**After 7 Days (Day 10):**
- Trades: 70-120 expected
- Check: Overall performance
  - Target WR: 55-70%
  - Target Return: +2% to +6%
  - Red flag: WR <50% or loss

**Decision Point (Day 10):**
- ✅ If hitting targets: Continue to Week 2
- ⚠️ If slightly off: Adjust parameters (see Tuning section)
- ❌ If far off: STOP and investigate

---

### Week 2 Checkpoints (Days 11-16)

**After 14 Days Total (Day 17):**
- Trades: 150-250 expected
- Check: Consistency
  - WR stable 55-70%?
  - Return +5% to +12%?
  - Max drawdown <10%?

---

## Phase 3: ANALYZE RESULTS (2 Days)

**Goal:** Decide if strategy is ready for live trading

### Day 17: Calculate Final Metrics

```bash
# Run analysis script
python eval/analyze_paper_trading.py \
    --start-date 2026-09-03 \
    --end-date 2026-09-16 \
    --output paper_trading_report.json
```

**Analyze:**

1. **Overall Performance**
   - Total trades
   - Win rate
   - Profit factor
   - Total return %
   - Max drawdown

2. **Protection Effectiveness**
   - Velocity exits: count and catch rate
   - Safety exits: count and catch rate
   - Spikes missed: count
   - False exits: count and rate

3. **Compare to Expectations**

| Metric | Expected | Actual | Pass? |
|--------|----------|--------|-------|
| Win Rate | 55-70% | ___ | Y/N |
| Velocity Catch | 60-75% | ___ | Y/N |
| False Exits | <30% | ___ | Y/N |
| Monthly Return | 30-80% | ___ | Y/N |
| Max Drawdown | <12% | ___ | Y/N |

---

### Day 18: Make Go/No-Go Decision

**GO LIVE if ALL are true:**
- ✅ Win Rate: 55-70%
- ✅ Velocity Catch: 55-80%
- ✅ False Exits: <35%
- ✅ Monthly Return: 25-100%
- ✅ Max Drawdown: <15%
- ✅ No major anomalies (unexplained losses)
- ✅ Pattern stable across both weeks

**DO NOT GO LIVE if ANY are true:**
- ❌ Win Rate: <50%
- ❌ Velocity Catch: <50%
- ❌ False Exits: >40%
- ❌ Monthly Return: <20% or >150%
- ❌ Max Drawdown: >20%
- ❌ Unexplained large losses
- ❌ Performance degrading over time

---

## Phase 4: GO LIVE (Day 19+)

**If paper trading PASSED all checks**

### Day 19: Final Preparations

1. **Open Real Account**
   - Deposit $200-300 (not $1000+)
   - Start small, scale up later

2. **Deploy Live System**
```bash
# Use same parameters that worked in paper trading
python live/trading_loop.py \
    --symbol BOOM500,CRASH500 \
    --equity 200 \
    --risk 0.02 \
    --velocity-threshold 12 \
    --mode LIVE
```

3. **Double-Check Everything**
   - [ ] API credentials correct
   - [ ] Position sizing correct
   - [ ] Risk per trade = 2% (not 20%)
   - [ ] COLD hours enabled
   - [ ] All protection layers active
   - [ ] Dashboard running
   - [ ] Logging enabled
   - [ ] Alert system active

---

### Week 1 Live (Days 19-25)

**CRITICAL: Stay small**
- Equity: $200-300 only
- Risk: 2% per trade max
- Watch every trade
- Ready to STOP immediately

**Daily checklist:**
- Morning: Check overnight trades
- Evening: Review day's performance
- Compare: Live vs paper trading
  - Is WR similar? (±10%)
  - Is catch rate similar? (±15%)
  - Is slippage similar? (±0.2 pts)

**Red flags to STOP:**
- WR drops to <45%
- Catch rate drops to <40%
- Slippage >0.7 pts average
- Unexplained large loss
- System behaving differently than paper

---

### Month 1 Live (Days 26-49)

**If Week 1 went well:**
- Continue with $200-300
- Track cumulative metrics
- Compare to paper trading expectations

**Weekly review:**
- Calculate week's WR, return, drawdown
- Compare to paper trading baseline
- Adjust if needed (see Tuning section)

**Scale up only if:**
- ✅ 4 weeks profitable
- ✅ WR stable 55-70%
- ✅ Drawdown <10%
- ✅ No major issues

**Then:** Increase to $400-500 (not $1000+)

---

## Parameter Tuning Guide

If paper trading shows issues, adjust parameters:

### Problem: Velocity Catch Rate <50%

**Symptoms:**
- Too many spikes getting through
- Safety exit activating frequently

**Solutions:**
1. Lower threshold: 12 → 10 ticks
   - Pro: Catches more spikes
   - Con: More false exits
2. Check execution speed
   - Measure tick-to-order latency
   - If >100ms, platform is too slow
3. Add pre-exit warning
   - Exit at 10 ticks instead of waiting for 12

---

### Problem: False Exits >35%

**Symptoms:**
- Velocity exits triggering without spikes
- Win rate lower than expected

**Solutions:**
1. Raise threshold: 12 → 14 or 16 ticks
   - Pro: Fewer false exits
   - Con: Miss more spikes
2. Add trend filter
   - Only exit if against main trend
3. Add time filter
   - Only exit if held >3 bars

---

### Problem: Win Rate <50%

**Symptoms:**
- Too many losses
- Strategy not profitable

**Solutions:**
1. Tighten COLD hours
   - Only trade best hours: 1, 5, 19 UTC
2. Increase TP, decrease SL
   - TP: 2.0x → 2.5x median
   - Accept some will miss
3. Add cluster filter
   - Skip if 2+ spikes in last 5 bars
4. Reduce trade frequency
   - Entry delay: 2 bars → 4 bars

---

### Problem: Max Drawdown >15%

**Symptoms:**
- Large consecutive losses
- Spike clusters hitting hard

**Solutions:**
1. Reduce position size
   - Risk: 2% → 1.5%
2. Add daily loss limit
   - Stop if down -5% in one day
3. Avoid cluster periods
   - Stop trading after 3 spikes in 15 minutes
4. Increase tick exit speed
   - Max hold: 6 bars → 4 bars

---

## Risk Management Rules

**Position Sizing:**
```python
risk_per_trade = account_equity * 0.02  # 2% risk
position_size = risk_per_trade / (SL_points * point_value)

# Example:
# Equity: $200
# Risk: 2% = $4
# SL: 0.56 points
# Point value: $0.35 per lot
# Position: $4 / (0.56 * $0.35) = 20.4 lots (round to 20)
```

**Daily Limits:**
```python
max_trades_per_day = 30
max_daily_loss = -5%  # Stop if hit
max_consecutive_losses = 5  # Stop if hit
```

**Weekly Limits:**
```python
max_weekly_loss = -10%  # Stop trading for rest of week
target_weekly_return = +8%  # Good week, can reduce risk
```

---

## Emergency Stop Conditions

**STOP TRADING IMMEDIATELY if:**

1. **Technical Issues:**
   - Platform not responding
   - Orders not executing
   - Tick data delayed >5 seconds
   - Dashboard showing errors

2. **Performance Issues:**
   - 5 consecutive losses
   - Down -5% in one day
   - Down -10% in one week
   - WR drops to <40%

3. **Market Issues:**
   - Unusual spike frequency (>15/hour)
   - Spread widening >3x normal
   - Platform announces maintenance
   - Major news event

4. **Protection Issues:**
   - Velocity exit not triggering
   - Safety exit missing spikes
   - Slippage >1.0 pts on average

**When stopped:**
- Close all positions
- Don't trade for 24 hours
- Investigate root cause
- Fix issue before resuming

---

## Expected Timeline & Milestones

| Phase | Days | Key Milestone | Success Metric |
|-------|------|---------------|----------------|
| Verify | 1-2 | Pattern still exists | 75-90% buildup pattern |
| Paper Trade Week 1 | 3-9 | Protection working | 60-75% velocity catch |
| Paper Trade Week 2 | 10-16 | Consistency proven | 55-70% WR stable |
| Analysis | 17-18 | Go/No-Go decision | All metrics pass |
| Live Week 1 | 19-25 | Survive reality | Live ≈ Paper (±15%) |
| Live Month 1 | 26-49 | Prove profitability | Cumulative profit >0 |

---

## Realistic Expectations

### What You SHOULD Expect

**Month 1 (If everything works):**
- Starting capital: $200
- Win Rate: 55-65%
- Trades: 150-250
- Monthly return: +40-70%
- **Profit: $80-140**

**Month 2-3 (Scale up to $400):**
- Win Rate: stabilizes 58-68%
- Monthly return: +35-60%
- **Profit: $140-240/month**

**Year 1 Total:**
- Start: $200
- End: $400-700
- Total return: +100-250%
- **NOT $21,583 (forget that number)**

### What You Should NOT Expect

- ❌ $10k+/month profit (forget it)
- ❌ 80-90% win rate (60-70% is reality)
- ❌ 85.7% live velocity catch (70-75% realistic)
- ❌ No losses (you will have losing days/weeks)
- ❌ Perfect execution (slippage happens)

---

## Final Checklist Before Day 1

- [ ] I understand profit is $200-400/month, not $21k
- [ ] I understand 85.7% is historical pattern, not live rate
- [ ] I have Sep 2026 data to verify pattern
- [ ] I have demo account set up
- [ ] I have paper trading script ready
- [ ] I have tracking spreadsheet ready
- [ ] I have monitoring dashboard ready
- [ ] I know my go/no-go criteria
- [ ] I know my emergency stop conditions
- [ ] I will start with $200-300, not $1000+
- [ ] I will track every metric daily
- [ ] I will STOP if metrics fail
- [ ] I won't scale up until Month 2+
- [ ] I understand this might NOT work
- [ ] I'm prepared to lose my starting capital

**If you checked all boxes: BEGIN Phase 1 tomorrow.**

**If you missed any: DO NOT START until all are checked.**

---

## Summary: The Path Forward

```
Day 1-2:   Verify pattern on Sep 2026 data
           ↓ (if pattern holds)
Day 3:     Deploy paper trading
           ↓
Day 4-16:  Paper trade 14 days, track everything
           ↓ (if metrics pass)
Day 17-18: Analyze, make go/no-go decision
           ↓ (if GO)
Day 19:    Deploy live with $200-300
           ↓
Day 20-49: Trade carefully, track daily
           ↓ (after 1 month profitable)
Month 2:   Scale to $400-500
           ↓
Month 3+:  Grow slowly, stay disciplined
```

**Expected outcome after 3 months:**
- Capital: $200 → $400-700
- Profit: $200-500 total
- Learning: Priceless

**That's the honest, realistic path.**

**Good luck. Start with verification tomorrow.**

---

*Last updated: 2026-09-01 22:14 UTC*  
*Status: ACTIONABLE ROADMAP*  
*Next: Day 1 - Fetch Sep 2026 data and verify pattern*
