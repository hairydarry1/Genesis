# HOW BOOM/CRASH ACTUALLY WORKS
## Deep Market Mechanics from 10M+ Ticks of Real Data

**Date:** 2026-09-02  
**Data:** 10.1 million ticks across 4 symbols  
**Status:** GROUND TRUTH - Not assumptions, MEASURED FACTS

---

## THE FUNDAMENTAL STRUCTURE

### How the Algorithm Actually Works

The Boom/Crash algorithm has ONE job: **create a one-directional drift with periodic counter-moves.**

```
CRASH indices: Price drifts UP slowly, then CRASHES down suddenly
BOOM indices:  Price drifts down slowly, then BOOMS up suddenly
```

### Measured Reality (NOT What We Assumed)

| What We Thought | What Actually Happens |
|-----------------|----------------------|
| Spike every 500/1000 ticks | Mean 72-129 ticks between spikes |
| Spikes are rare (0.2%) | Spikes happen 0.8-1.4% of ticks |
| Spikes alternate direction | 80-93% go in SAME direction |
| Price is random between spikes | Price has 98-99% directional consistency |

---

## THE TICK-LEVEL REALITY

### CRASH500 (2.5M ticks analyzed)

```
Up ticks:   2,466,416 (98.1%)  ← Price drifts UP
Down ticks:     5,236 ( 0.2%)  ← Crashes DOWN (rare but HUGE)
Zero ticks:    43,216 ( 1.7%)

Up tick size:   mean = 0.006  (tiny increments)
Down tick size: mean = 3.036  (500x larger!)
```

**The math:**
- Up ticks: 0.006 per tick × 50 ticks = 0.30 drift UP
- Down ticks: 3.036 per tick × 1 tick = 3.036 crash DOWN
- Net: -2.736 per crash cycle

### BOOM500 (2.5M ticks analyzed)

```
Up ticks:     5,182 ( 0.2%)  ← Booms UP (rare but HUGE)
Down ticks: 2,508,777 (98.8%) ← Price drifts DOWN
Zero ticks:    26,550 ( 1.0%)

Up tick size:   mean = 4.630  (500x larger!)
Down tick size: mean = 0.010  (tiny increments)
```

### CRASH1000 (2.5M ticks analyzed)

```
Up ticks:   2,463,456 (98.2%)  ← Drifts UP
Down ticks:     2,565 ( 0.1%)  ← Crashes DOWN (even rarer!)

Down tick size: mean = 5.797  (950x larger than up ticks!)
```

### BOOM1000 (2.6M ticks analyzed)

```
Up ticks:       2,555 ( 0.1%)  ← Booms UP (rarest!)
Down ticks: 2,540,090 (99.2%)  ← Drifts DOWN

Up tick size: mean = 15.028  (1000x larger than down ticks!)
```

---

## SPIKE TIMING PATTERNS

### How Often Do Spikes Happen?

| Symbol | Mean Interval | Median | Max | Spikes/Tick |
|--------|--------------|--------|-----|-------------|
| CRASH500 | 85 ticks | 58 | 1,184 | 1.2% |
| BOOM500 | 99 ticks | 69 | 1,237 | 1.0% |
| CRASH1000 | 129 ticks | 87 | 1,472 | 0.8% |
| BOOM1000 | 72 ticks | 50 | 1,093 | 1.4% |

### Interval Distribution

```
CRASH500:
  0-100 ticks:   69.2% of spikes
  100-200 ticks: 21.2%
  200-300 ticks:  6.4%
  300+ ticks:     3.2%

BOOM1000:
  0-100 ticks:   75.0% of spikes  ← Most frequent
  100-200 ticks: 18.7%
  200+ ticks:     6.3%
```

### Is There a Timer?

**NO.** The intervals are RANDOM/CHAOTIC:

| Symbol | Coefficient of Variation | Autocorrelation |
|--------|-------------------------|-----------------|
| CRASH500 | 1.028 | 0.033 |
| BOOM500 | 1.012 | 0.021 |
| CRASH1000 | 1.040 | 0.028 |
| BOOM1000 | 1.007 | 0.010 |

- CV > 0.5 = Random (not periodic)
- Autocorrelation near 0 = Intervals don't predict each other

**The algorithm does NOT have a fixed timer.** Spikes are injected randomly.

---

## SPIKE DIRECTION PATTERNS

### Which Direction Do Spikes Go?

| Symbol | UP Spikes | DOWN Spikes | Alternation Rate |
|--------|-----------|-------------|------------------|
| CRASH500 | 82.4% | 17.6% | 29.3% |
| BOOM500 | 20.2% | 79.8% | 32.0% |
| CRASH1000 | 86.8% | 13.2% | 22.8% |
| BOOM1000 | 7.2% | 92.8% | 13.4% |

### What This Means

1. **Spikes go in the DRIFT direction** (80-93% of the time)
   - CRASH: Most spikes are UP (continuing the drift)
   - BOOM: Most spikes are DOWN (continuing the drift)

2. **Counter-spikes are RARE** (7-18% of spikes)
   - These are the "crashes" in CRASH indices
   - These are the "booms" in BOOM indices

3. **Spikes CLUSTER in same direction** (alternation rate 13-32%)
   - After an UP spike, next spike is likely UP
   - Counter-spikes rarely alternate

---

## POST-SPIKE BEHAVIOR

### What Happens After a Spike?

| Symbol | +1 Bar | +2 Bars | +5 Bars | +10 Bars | +20 Bars |
|--------|--------|---------|---------|----------|----------|
| CRASH500 | +1.6% | +1.9% | +13.6% | +1.9% | -29.6% |
| BOOM500 | +4.8% | +2.3% | +0.7% | -12.2% | +0.5% |
| CRASH1000 | +7.7% | +24.4% | +16.8% | -1.0% | +20.3% |
| BOOM1000 | -0.2% | -8.3% | -4.1% | -48.7% | -55.4% |

### Key Insights

1. **Retracement is SMALL and SHORT-LIVED**
   - CRASH500: Only 13.6% retracement at +5 bars
   - BOOM500: Only 4.8% retracement at +1 bar
   - BOOM1000: NEGATIVE retracement (price continues!)

2. **Price CONTINUES in spike direction**
   - CRASH500 at +20 bars: -29.6% (continues DOWN)
   - BOOM1000 at +20 bars: -55.4% (continues DOWN strongly)

3. **The "retracement" we assumed is WRONG**
   - We thought: Price spikes, then reverts 50-80%
   - Reality: Price spikes, then continues in spike direction

---

## THE EXPLOITABLE EDGE

### What Actually Works

| Strategy | Why It Works | Evidence |
|----------|--------------|----------|
| **COLD Hours** | Reduces spike frequency | 19-22% fewer spikes in cold hours |
| **Tick Exit = 6** | Limits exposure time | 87-92% spike loss reduction |
| **Safety Exit** | Catches spikes early | 93-94% catch rate |
| **Position Sizing** | Manages risk | Half-Kelly optimal |

### What DOESN'T Work

| Strategy | Why It Fails | Evidence |
|----------|--------------|----------|
| **Velocity Exit** | Buildup is normal state | 0% WR at all thresholds |
| **Stop Loss** | Spikes too fast | 5-18x slippage |
| **Prediction** | Spikes are random | CV > 1.0, no autocorrelation |
| **Retracement Fade** | Retracement is small | Only 1-24% avg retracement |

---

## THE ALGORITHM'S DESIGN

### How Deriv Makes Money

```
1. Price drifts in one direction (slow, predictable)
   → Traders enter positions in drift direction
   → They feel safe because price is "trending"

2. Algorithm injects counter-spikes (fast, random)
   → Spikes are 500-1000x larger than normal ticks
   → They happen randomly (can't be predicted)
   → They wipe out drift-based positions

3. The math:
   - Up ticks: 0.006 × 50 = 0.30 profit per cycle
   - Down ticks: 3.036 × 1 = 3.036 loss per cycle
   - Net: -2.736 per cycle (house edge)
```

### Why Boom/Crash is Designed This Way

1. **Attract traders with "trending" price action**
   - 98% of ticks go in one direction
   - Looks like a normal trend
   - Traders enter with confidence

2. **Inject random counter-spikes**
   - Can't be predicted (CV > 1.0)
   - Can't be stopped (5-18x slippage)
   - Wipe out accumulated profits

3. **The house always wins**
   - Small gains from drift
   - Large losses from spikes
   - Net: Algorithm profits from trader losses

---

## CONTINGENCY PLANS

### Based on Actual Market Mechanics

| Contingency | Trigger | Action | Expected Result |
|-------------|---------|--------|-----------------|
| **COLD Hours** | Time-based | Don't trade in spike-heavy hours | 19-22% fewer spikes |
| **Tick Exit** | Bar count | Close after 6 bars | 87-92% less spike damage |
| **Safety Exit** | Spike detected | Close immediately | 93-94% catch rate |
| **Position Size** | Risk-based | Use Half-Kelly sizing | Optimal risk/reward |
| **No Retracement Fade** | Post-spike | Don't fade small retracements | Avoid bad entries |

### The REAL Contingency

**You cannot predict spikes. You can only MANAGE the damage.**

```
Without protection:
  100 trades × 70% WR = +$11.90
  30 losses × -$0.17 = -$5.10
  10 spikes × -$0.85 = -$8.50
  Net: -$1.70 (LOSS)

With protection:
  100 trades × 70% WR = +$11.90
  30 losses × -$0.17 = -$5.10
  10 spikes × -$0.20 = -$2.00 (safety exit)
  Net: +$4.80 (PROFIT)
```

---

## CONCLUSION

### The Truth About Boom/Crash

1. **The algorithm is random** (CV > 1.0, no autocorrelation)
2. **Spikes are NOT rare** (0.8-1.4% of all ticks)
3. **Retracement is SMALL** (1-24%, not 50-80%)
4. **The edge is RISK MANAGEMENT**, not prediction

### What to Focus On

1. **COLD hours** - Reduce spike exposure
2. **Tick exit** - Limit time in market
3. **Safety exit** - Catch spikes early
4. **Position sizing** - Survive the inevitable

### What NOT to Do

1. **Don't try to predict spikes** (they're random)
2. **Don't use stop losses** (slippage is too high)
3. **Don't fade retracements** (they're small)
4. **Don't use velocity exit** (buildup is normal)

---

**The market is designed to take your money. The only way to win is to play defense.**

*Analysis based on 10.1 million ticks across 4 symbols.*  
*All claims are MEASURED, not assumed.*
