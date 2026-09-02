# DEPLOYMENT GUIDE
## From Backtest to Live Trading

---

## STEP 1: COMPILE THE EA

1. Copy `mt5/SpikeCatcher_v2.mq5` to:
   ```
   C:\Users\hp\AppData\Roaming\MetaQuotes\Terminal\YOUR_ID\MQL5\Experts\
   ```

2. Open MetaEditor (F4 in MT5)
3. Find `SpikeCatcher_v2.mq5` in Navigator
4. Right-click → Compile
5. Check for 0 errors

---

## STEP 2: SET INPUTS

### For Maximum Profit (Recommended Start)
```
TradingSystem = 1
```

### For Maximum Safety
```
TradingSystem = 2
```

### Manual Configuration
```
RiskPercent = 1.0          // Start with 1% risk
MaxLots = 1.0              // Start small
EntryBar = 1               // Enter 1 bar after spike
MaxHoldBars = 3            // Exit after 3 bars
TPMultiplier = 1.0         // TP = 1x median range
SLMultiplier = 0.5         // SL = 0.5x median range
UseSafetyExit = true       // Close on new spike
SafetyMoveMult = 2.5       // Spike detection threshold
```

---

## STEP 3: ATTACH TO CHART

1. Open chart for your symbol (e.g., Crash 1000 Index, M1)
2. Drag `SpikeCatcher_v2` from Navigator to chart
3. Set inputs as above
4. Click OK
5. Enable AutoTrading (button turns green)

---

## STEP 4: MONITOR FIRST TRADES

Watch for:
- `[ENTRY]` messages — EA is taking trades
- `[SLTP] Attached` — SL/TP attached successfully
- `[BUY FAIL]` or `[SELL FAIL]` — Order rejected
- `[SLTP FAIL]` — SL attachment failed
- `[CLOSE FAIL]` — Close order rejected

---

## STEP 5: VERIFY SL IS WORKING

After first few trades, check:
1. Did SL trigger at expected level?
2. Was there slippage?
3. Did safety exit work?

If SL is NOT working (Error 10016):
- Increase `SLMultiplier` to 2.0
- Or increase `OrderDeviation` to 1000

---

## COMMON ISSUES

### Error 10030 (Unsupported Filling Mode)
Already fixed in v2.04+. EA auto-detects FOK/IOC.

### Error 10016 (Invalid Stops)
SL too close to price. EA now:
1. Opens with sl=0, tp=0
2. Attaches SL separately
3. Retries at 1.5x distance
4. Closes position if SL fails

### Error 10029 (Invalid Fill)
Server rejected close order. EA retries 3 times.

### No Trades
Check:
1. AutoTrading enabled?
2. TradingSystem=1 (not 2 with Cold Hours)?
3. Correct symbol and timeframe?

---

## EXPECTED PERFORMANCE

| Symbol | WR | PF | Monthly |
|--------|-----|-----|---------|
| BOOM1000 | 99.7% | 800+ | $15-50 |
| CRASH1000 | 99.9% | 2000+ | $5-20 |
| BOOM500 | 85% | 12+ | $50-100 |
| CRASH500 | 85% | 12+ | $30-80 |

---

## RISK MANAGEMENT

1. **Start with 1% risk per trade**
2. **Use Max Safety mode first** (TradingSystem=2)
3. **Monitor for 1 week** before increasing size
4. **Never risk more than 2%** per trade
5. **Take profits weekly** — withdraw 50%

---

## FILES

| File | Description |
|------|-------------|
| `mt5/SpikeCatcher_v2.mq5` | The EA |
| `docs/DEPLOYMENT_GUIDE.md` | This guide |
| `docs/EXPLOITATION_GUIDE.md` | Strategy details |
| `docs/CLUSTER_DEFENSE_SYSTEM.md` | Cluster defense |
| `docs/ALGORITHM_MECHANICS.md` | How the algorithm works |
