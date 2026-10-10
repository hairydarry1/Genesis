# Boom/Crash Strategy Development Guide

**Last Updated:** 2026-09-02  
**Project:** Genesis Trading System

---

## Overview

This guide shows you how to develop profitable Boom/Crash strategies from scratch using the Genesis framework. You'll learn to analyze patterns, build predictive models, backtest strategies, and deploy live.

---

## Table of Contents

1. [Getting Started](#1-getting-started)
2. [Understanding Your Tools](#2-understanding-your-tools)
3. [Strategy Development Workflow](#3-strategy-development-workflow)
4. [Pattern Analysis](#4-pattern-analysis)
5. [Building Predictive Models](#5-building-predictive-models)
6. [Backtesting & Optimization](#6-backtesting--optimization)
7. [Live Trading](#7-live-trading)
8. [Example: Building a Complete Strategy](#8-example-building-a-complete-strategy)

---

## 1. Getting Started

### Prerequisites

Read these documents first:
- `docs/BOOM_CRASH_FUNDAMENTALS.md` - Core concepts
- `README.md` - Project overview

### Your Toolkit

The Genesis framework provides these tools:

1. **Pattern Recognition** (`model/pattern_recognition.py`)
   - Detects velocity exhaustion, spike patterns, clusters, retracements
   
2. **Tick Predictor** (`model/tick_predictor.py`)
   - Predicts next 10-50 tick movements with confidence bounds
   
3. **Timeframe Analyzer** (`model/timeframe_analyzer.py`)
   - Analyzes tick, 1s, 1m, 5m timeframes simultaneously
   
4. **Strategy Backtester** (`model/strategy_backtester.py`)
   - Comprehensive backtesting with walk-forward optimization

---

## 2. Understanding Your Tools

### Pattern Recognition System

Identifies patterns in real-time:

```python
from model.pattern_recognition import PatternRecognizer

# Create recognizer
recognizer = PatternRecognizer(
    symbol='CRASH500',
    velocity_threshold=12,
    tick_count_threshold=700
)

# Update with new data
recognizer.update(tick_prices)

# Detect patterns
patterns = recognizer.detect_patterns(tick_prices)

# Predict spike
prediction = recognizer.predict_spike(tick_prices, current_idx)
print(f"Spike probability: {prediction.probability:.1%}")
print(f"Direction: {prediction.expected_direction}")
print(f"Expected in: {prediction.expected_ticks} ticks")
```

**Key Patterns Detected:**
- `VELOCITY_EXHAUSTION` - Consecutive same-direction ticks (12-20+)
- `TICK_EXHAUSTION` - Long time since last spike (700+ ticks)
- `SPIKE_CLUSTER` - Multiple spikes within 5 bars
- `RETRACEMENT` - Post-spike mean reversion

### Tick Prediction Model

Predicts short-term price movements:

```python
from model.tick_predictor import TickPredictor

# Train on historical data
predictor = TickPredictor(symbol='CRASH500')
predictor.train(historical_tick_prices)

# Predict next 20 ticks
prediction = predictor.predict_next_ticks(
    current_prices,
    n_ticks=20,
    include_spike_scenarios=True
)

print(f"Predicted prices: {prediction.predicted_prices}")
print(f"Spike probability: {prediction.spike_probability:.1%}")
print(f"Confidence: {prediction.confidence:.1%}")
```

### Multi-Timeframe Analysis

Analyzes multiple timeframes for confluence:

```python
from model.timeframe_analyzer import TimeframeAnalyzer

# Create analyzer
analyzer = TimeframeAnalyzer(tick_prices)

# Analyze current state
analysis = analyzer.analyze_current_state()

print(f"Signal: {analysis.signal}")
print(f"Confidence: {analysis.confidence:.1%}")
print(f"Trend alignment: {analysis.trend_alignment:.1%}")
print(f"Stop loss: {analysis.stop_loss_level:.2f}")
print(f"Take profit: {analysis.take_profit_level:.2f}")
```

### Strategy Backtester

Tests and optimizes strategies:

```python
from model.strategy_backtester import Backtester, Strategy

# Create backtester
backtester = Backtester(
    prices=tick_prices,
    symbol='CRASH500',
    spread=0.0001,
    slippage=0.00005
)

# Run backtest
result = backtester.run(
    strategy=my_strategy,
    initial_equity=10000.0,
    position_size_pct=1.0
)

print(result.to_dict())
```

---

## 3. Strategy Development Workflow

### Step 1: Define Your Hypothesis

Start with a clear hypothesis:

**Example:**
> "CRASH spikes are preceded by 12+ consecutive upticks 79% of the time. By exiting positions when we detect 12+ upticks, we can avoid most spike damage."

### Step 2: Analyze Historical Data

Use pattern recognition to validate:

```python
import numpy as np
from model.pattern_recognition import PatternRecognizer

# Load historical data
prices = np.load('data/ticks_crash_500_index.npy')

# Analyze patterns
recognizer = PatternRecognizer('CRASH500', velocity_threshold=12)
recognizer.update(prices)

# Check how many spikes were preceded by velocity
spike_mask = recognizer.detect_spikes(prices)
spike_indices = np.where(spike_mask)[0]

velocity_before_spike = []
for spike_idx in spike_indices:
    if spike_idx >= 30:
        velocity, direction = recognizer.analyze_velocity_buildup(
            prices[spike_idx-30:spike_idx]
        )
        velocity_before_spike.append(velocity)

# Calculate success rate
caught = sum(1 for v in velocity_before_spike if v >= 12)
success_rate = caught / len(velocity_before_spike)
print(f"Velocity threshold 12 catches {success_rate:.1%} of spikes")
```

### Step 3: Build Strategy Class

Implement your strategy:

```python
from model.strategy_backtester import Strategy, Position

class VelocityExitStrategy(Strategy):
    """Exit when velocity threshold reached."""
    
    def __init__(self, velocity_threshold=12, entry_signal='fade'):
        super().__init__('VelocityExit')
        self.velocity_threshold = velocity_threshold
        self.entry_signal = entry_signal
        
    def should_enter(self, prices, idx, state):
        # Implement entry logic
        if self.entry_signal == 'fade':
            # Enter after spike detected
            pass
        return False, None
        
    def should_exit(self, prices, idx, position, state):
        # Check velocity
        if idx >= 20:
            recent = prices[idx-20:idx+1]
            velocity, direction = self._calculate_velocity(recent)
            
            # For CRASH LONG positions
            if position.direction == 'LONG' and direction == 'UP':
                if velocity >= self.velocity_threshold:
                    return True, 'VELOCITY_EXIT'
                    
        return False, ''
        
    def _calculate_velocity(self, prices):
        changes = np.diff(prices)
        if len(changes) == 0:
            return 0, 'NEUTRAL'
            
        current_dir = 'UP' if changes[-1] > 0 else 'DOWN'
        count = 0
        for i in range(len(changes)-1, -1, -1):
            if (current_dir == 'UP' and changes[i] > 0) or \
               (current_dir == 'DOWN' and changes[i] < 0):
                count += 1
            else:
                break
        return count, current_dir
```

### Step 4: Backtest

Test your strategy:

```python
from model.strategy_backtester import Backtester

backtester = Backtester(prices, symbol='CRASH500')

# Test single configuration
strategy = VelocityExitStrategy(velocity_threshold=12)
result = backtester.run(strategy, initial_equity=10000)

print(f"Win Rate: {result.win_rate:.1%}")
print(f"Profit Factor: {result.profit_factor:.2f}")
print(f"Max Drawdown: {result.max_drawdown_pct:.2%}")
print(f"Total Return: {result.total_return_pct:.2%}")
```

### Step 5: Optimize Parameters

Use grid search:

```python
# Optimize velocity threshold
param_grid = {
    'velocity_threshold': [10, 12, 15, 18, 20],
    'entry_signal': ['fade', 'catcher']
}

best_params, best_result = backtester.optimize(
    VelocityExitStrategy,
    param_grid,
    metric='profit_factor'
)

print(f"Best params: {best_params}")
print(f"Best profit factor: {best_result.profit_factor:.2f}")
```

### Step 6: Walk-Forward Validation

Test on out-of-sample data:

```python
# Split data into train/test
train_size = int(len(prices) * 0.7)
train_prices = prices[:train_size]
test_prices = prices[train_size:]

# Train on first 70%
train_backtester = Backtester(train_prices, 'CRASH500')
best_params, _ = train_backtester.optimize(
    VelocityExitStrategy,
    param_grid
)

# Test on remaining 30%
test_backtester = Backtester(test_prices, 'CRASH500')
strategy = VelocityExitStrategy(**best_params)
test_result = test_backtester.run(strategy, 10000)

print(f"Out-of-sample profit factor: {test_result.profit_factor:.2f}")
```

### Step 7: Deploy Live

Once validated, deploy:

```python
# See live/trading_loop.py for live deployment
```

---

## 4. Pattern Analysis

### Analyzing Velocity Patterns

```python
from model.pattern_recognition import PatternRecognizer
import numpy as np

# Load data
prices = np.load('data/ticks_crash_500_index.npy')

recognizer = PatternRecognizer('CRASH500')
recognizer.update(prices)

# Scan for velocity exhaustion windows
for i in range(100, len(prices)):
    patterns = recognizer.detect_patterns(prices[:i], window_size=100)
    
    velocity_patterns = [p for p in patterns if p.pattern_type == 'VELOCITY_EXHAUSTION']
    
    if velocity_patterns:
        pattern = velocity_patterns[0]
        print(f"Tick {i}: Velocity {pattern.strength} ({pattern.direction})")
        print(f"  Confidence: {pattern.confidence:.1%}")
```

### Analyzing Tick Count Exhaustion

```python
# Track ticks since last spike
spike_mask = recognizer.detect_spikes(prices)
spike_indices = np.where(spike_mask)[0]

for i, spike_idx in enumerate(spike_indices[1:], 1):
    prev_spike_idx = spike_indices[i-1]
    interval = spike_idx - prev_spike_idx
    print(f"Spike {i}: {interval} ticks since previous")

# Calculate probabilities
intervals = np.diff(spike_indices)
print(f"Mean interval: {np.mean(intervals):.0f}")
print(f"Median interval: {np.median(intervals):.0f}")
print(f"P(spike) at 700 ticks: {(intervals >= 700).mean():.1%}")
print(f"P(spike) at 800 ticks: {(intervals >= 800).mean():.1%}")
```

### Analyzing Retracement Patterns

```python
# After each spike, measure retracement
for spike_idx in spike_indices:
    if spike_idx + 10 < len(prices):
        spike_price = prices[spike_idx]
        pre_spike_price = prices[spike_idx - 1]
        
        # Check retracement over next 10 bars
        for bars in [2, 5, 10]:
            future_price = prices[spike_idx + bars]
            
            spike_move = spike_price - pre_spike_price
            retracement = future_price - spike_price
            
            if abs(spike_move) > 0:
                retrace_pct = retracement / spike_move
                print(f"Spike {spike_idx}, +{bars} bars: {retrace_pct:.1%} retracement")
```

---

## 5. Building Predictive Models

### Training Tick Predictor

```python
from model.tick_predictor import TickPredictor
import numpy as np

# Load training data (at least 5000 ticks)
train_prices = np.load('data/ticks_crash_500_index.npy')

# Train predictor
predictor = TickPredictor(symbol='CRASH500')
predictor.train(train_prices, verbose=True)

# Save learned parameters
import pickle
with open('models/crash500_predictor.pkl', 'wb') as f:
    pickle.dump(predictor, f)
```

### Using Predictions in Strategies

```python
class PredictiveStrategy(Strategy):
    """Strategy that uses tick predictions."""
    
    def __init__(self, predictor, confidence_threshold=0.7):
        super().__init__('Predictive')
        self.predictor = predictor
        self.confidence_threshold = confidence_threshold
        
    def should_enter(self, prices, idx, state):
        if idx < 100:
            return False, None
            
        # Predict next 10 ticks
        current_state = prices[idx-100:idx]
        prediction = self.predictor.predict_next_ticks(
            current_state,
            n_ticks=10
        )
        
        # Enter if high-confidence spike prediction
        if prediction.spike_probability > 0.8:
            if prediction.spike_expected_direction == 'DOWN':
                # Prepare to enter SHORT to catch the spike
                return True, 'SHORT'
                
        return False, None
```

### Combining Multiple Models

```python
from model.pattern_recognition import PatternRecognizer
from model.tick_predictor import TickPredictor
from model.timeframe_analyzer import TimeframeAnalyzer

class EnsembleStrategy(Strategy):
    """Combines multiple prediction models."""
    
    def __init__(self):
        super().__init__('Ensemble')
        self.recognizer = PatternRecognizer('CRASH500')
        self.predictor = TickPredictor('CRASH500')
        self.analyzer = None  # Created dynamically
        
    def should_enter(self, prices, idx, state):
        if idx < 100:
            return False, None
            
        # Signal 1: Pattern recognition
        self.recognizer.update(prices[:idx])
        spike_pred = self.recognizer.predict_spike(prices[:idx], idx)
        pattern_signal = spike_pred.probability
        
        # Signal 2: Tick predictor
        tick_pred = self.predictor.predict_next_ticks(prices[idx-100:idx], 10)
        predictor_signal = tick_pred.spike_probability
        
        # Signal 3: Multi-timeframe
        self.analyzer = TimeframeAnalyzer(prices[:idx])
        mtf_analysis = self.analyzer.analyze_current_state()
        timeframe_signal = 1.0 if mtf_analysis.signal == 'SELL' else 0.0
        
        # Ensemble: weighted average
        ensemble_score = (
            0.4 * pattern_signal +
            0.4 * predictor_signal +
            0.2 * timeframe_signal
        )
        
        if ensemble_score > 0.7:
            return True, 'SHORT'  # Catch crash spike
            
        return False, None
```

---

## 6. Backtesting & Optimization

### Comprehensive Backtest

```python
from model.strategy_backtester import Backtester
import numpy as np

# Load full dataset
prices = np.load('data/ticks_crash_500_index.npy')

# Split into train/validation/test
n = len(prices)
train_end = int(n * 0.6)
val_end = int(n * 0.8)

train_prices = prices[:train_end]
val_prices = prices[train_end:val_end]
test_prices = prices[val_end:]

# 1. Optimize on training data
train_backtester = Backtester(train_prices, 'CRASH500')

param_grid = {
    'velocity_threshold': [10, 12, 15, 18, 20],
    'tick_threshold': [600, 700, 800, 900],
    'tp_multiplier': [1.5, 2.0, 2.5],
    'sl_multiplier': [0.8, 1.0, 1.2],
}

best_params, train_result = train_backtester.optimize(
    MyStrategy,
    param_grid,
    metric='profit_factor'
)

print(f"Training PF: {train_result.profit_factor:.2f}")

# 2. Validate on validation data
val_backtester = Backtester(val_prices, 'CRASH500')
strategy = MyStrategy(**best_params)
val_result = val_backtester.run(strategy, 10000)

print(f"Validation PF: {val_result.profit_factor:.2f}")

# 3. Final test on unseen data
if val_result.profit_factor > 1.5:  # Gate: only test if validation passes
    test_backtester = Backtester(test_prices, 'CRASH500')
    test_result = test_backtester.run(strategy, 10000)
    
    print(f"Test PF: {test_result.profit_factor:.2f}")
    print(f"Test Win Rate: {test_result.win_rate:.1%}")
    print(f"Test Max DD: {test_result.max_drawdown_pct:.2%}")
```

### Monte Carlo Simulation

```python
def monte_carlo_simulation(strategy, prices, n_simulations=1000):
    """Run Monte Carlo simulation by randomizing trade order."""
    results = []
    
    # Run initial backtest to get trades
    backtester = Backtester(prices, 'CRASH500')
    initial_result = backtester.run(strategy, 10000)
    trades = initial_result.trades
    
    if len(trades) == 0:
        return None
        
    # Simulate by randomizing trade order
    for _ in range(n_simulations):
        np.random.shuffle(trades)
        
        equity = 10000.0
        for trade in trades:
            equity += trade.pnl
            
        results.append(equity)
        
    results = np.array(results)
    
    print(f"Monte Carlo Results ({n_simulations} simulations):")
    print(f"  Mean final equity: ${np.mean(results):.2f}")
    print(f"  Median final equity: ${np.median(results):.2f}")
    print(f"  Std dev: ${np.std(results):.2f}")
    print(f"  5th percentile: ${np.percentile(results, 5):.2f}")
    print(f"  95th percentile: ${np.percentile(results, 95):.2f}")
    print(f"  Probability of profit: {(results > 10000).mean():.1%}")
    
    return results
```

---

## 7. Live Trading

### Paper Trading First

Before live deployment, paper trade:

```python
# See eval/paper_trading_tracker.py

from eval.paper_trading_tracker import PaperTrader

trader = PaperTrader(
    symbol='CRASH500',
    strategy=my_strategy,
    initial_equity=10000
)

# Run for 1 week
trader.run(duration_days=7)

# Review results
trader.print_summary()
```

### Live Deployment

```python
# See live/trading_loop.py

from live.trading_loop import TradingLoop
from live.config import LiveConfig

# Load configuration
config = LiveConfig.from_json('live/settings.json')

# Start trading loop
loop = TradingLoop(config)
loop.run()
```

### Monitoring

```python
# Real-time monitoring dashboard
# See dashboard/server.py

python dashboard/server.py
# Open http://localhost:8050
```

---

## 8. Example: Building a Complete Strategy

Let's build a complete "Velocity + Tick Count Catcher" strategy from scratch.

### Step 1: Hypothesis

> "By combining velocity exhaustion (12+ ticks) with tick count exhaustion (700+ ticks), we can catch 85%+ of spikes with 90%+ win rate."

### Step 2: Implementation

```python
from model.strategy_backtester import Strategy, Position
from model.pattern_recognition import PatternRecognizer
import numpy as np

class VelocityTickCatcher(Strategy):
    """
    Combined velocity + tick count spike catcher.
    
    Entry: When BOTH conditions met:
      1. Velocity >= threshold (12+ consecutive same-dir ticks)
      2. Tick count >= threshold (700+ ticks since spike)
      
    Exit: TP at spike magnitude, SL at small fixed amount
    """
    
    def __init__(
        self,
        velocity_threshold=12,
        tick_threshold=700,
        tp_points=200,
        sl_points=25,
        max_hold=50
    ):
        super().__init__('VelocityTickCatcher')
        self.velocity_threshold = velocity_threshold
        self.tick_threshold = tick_threshold
        self.tp_points = tp_points
        self.sl_points = sl_points
        self.max_hold = max_hold
        
        self.recognizer = PatternRecognizer(
            'CRASH500',
            velocity_threshold=velocity_threshold,
            tick_count_threshold=tick_threshold
        )
        
    def should_enter(self, prices, idx, state):
        if idx < 100:
            return False, None
            
        # Update recognizer
        self.recognizer.update(prices[:idx+1])
        
        # Get spike prediction
        prediction = self.recognizer.predict_spike(prices[:idx+1], idx)
        
        # Enter if BOTH signals strong
        if prediction.probability >= 0.8:
            # CRASH: spike goes DOWN, so we SHORT
            return True, 'SHORT'
            
        return False, None
        
    def should_exit(self, prices, idx, position, state):
        current_price = prices[idx]
        
        # Take profit
        profit = position.entry_price - current_price  # SHORT position
        if profit >= self.tp_points:
            return True, 'TP'
            
        # Stop loss
        loss = current_price - position.entry_price
        if loss >= self.sl_points:
            return True, 'SL'
            
        # Max hold
        if position.bars_held >= self.max_hold:
            return True, 'TIMEOUT'
            
        return False, ''
```

### Step 3: Backtest

```python
# Load CRASH500 tick data
prices = np.load('data/ticks_crash_500_index.npy')

# Create backtester
backtester = Backtester(prices, symbol='CRASH500')

# Test strategy
strategy = VelocityTickCatcher(
    velocity_threshold=12,
    tick_threshold=700,
    tp_points=200,
    sl_points=25
)

result = backtester.run(strategy, initial_equity=10000)

print("\n" + "="*60)
print("BACKTEST RESULTS")
print("="*60)
for key, value in result.to_dict().items():
    print(f"{key}: {value}")
    
print(f"\nExit Breakdown:")
for reason, count in result.exit_reasons.items():
    print(f"  {reason}: {count}")
```

### Step 4: Optimize

```python
# Optimize parameters
param_grid = {
    'velocity_threshold': [10, 12, 15],
    'tick_threshold': [650, 700, 750, 800],
    'tp_points': [150, 200, 250],
    'sl_points': [20, 25, 30],
}

best_params, best_result = backtester.optimize(
    VelocityTickCatcher,
    param_grid,
    metric='profit_factor'
)

print(f"\nBest Parameters: {best_params}")
print(f"Best Profit Factor: {best_result.profit_factor:.2f}")
print(f"Best Win Rate: {best_result.win_rate:.1%}")
```

### Step 5: Walk-Forward Validation

```python
# Split data
train_size = int(len(prices) * 0.7)
train_prices = prices[:train_size]
test_prices = prices[train_size:]

# Train
train_backtester = Backtester(train_prices, 'CRASH500')
best_params, _ = train_backtester.optimize(
    VelocityTickCatcher,
    param_grid
)

# Test
test_backtester = Backtester(test_prices, 'CRASH500')
final_strategy = VelocityTickCatcher(**best_params)
test_result = test_backtester.run(final_strategy, 10000)

print(f"\nOut-of-Sample Results:")
print(f"  Profit Factor: {test_result.profit_factor:.2f}")
print(f"  Win Rate: {test_result.win_rate:.1%}")
print(f"  Total Return: {test_result.total_return_pct:.2%}")
print(f"  Max Drawdown: {test_result.max_drawdown_pct:.2%}")
print(f"  Sharpe Ratio: {test_result.sharpe_ratio:.2f}")

# Gate: Only deploy if passes criteria
if (test_result.profit_factor > 2.0 and
    test_result.win_rate > 0.75 and
    test_result.max_drawdown_pct < 0.15):
    print("\n✅ PASSED - Ready for paper trading")
else:
    print("\n❌ FAILED - Needs improvement")
```

### Step 6: Paper Trade

```python
# Paper trade for 1 week
from eval.paper_trading_tracker import PaperTrader

paper_trader = PaperTrader(
    symbol='CRASH500',
    strategy=VelocityTickCatcher(**best_params),
    initial_equity=10000
)

paper_trader.run(duration_days=7)
paper_trader.print_summary()
```

### Step 7: Deploy Live

```python
# Add to live/settings.json
# Then run:
python -m live.trading_loop
```

---

## Summary

You now have everything needed to:

1. ✅ Understand Boom/Crash mechanics (`BOOM_CRASH_FUNDAMENTALS.md`)
2. ✅ Analyze patterns (`pattern_recognition.py`)
3. ✅ Build predictive models (`tick_predictor.py`)
4. ✅ Analyze multiple timeframes (`timeframe_analyzer.py`)
5. ✅ Backtest and optimize (`strategy_backtester.py`)
6. ✅ Deploy live (`live/trading_loop.py`)

**Next Steps:**

1. Study existing strategies in `eval/` directory
2. Run backtests on your data: `eval/final_backtest.py`
3. Experiment with pattern thresholds
4. Build your own strategy using the framework
5. Backtest rigorously with walk-forward validation
6. Paper trade before live deployment

**Remember:**
- Start simple, add complexity gradually
- Always validate out-of-sample
- Paper trade before going live
- Monitor drawdown continuously
- The market is always teaching - stay curious

---

**Good luck building profitable strategies!**
