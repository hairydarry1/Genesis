# SpikeCatcher v3 — Quick Cheat Sheet

## Deploy in 5 Steps

```
1. F4 (MetaEditor) → F7 (Compile) → 0 errors
2. Drag EA to M1 chart
3. Check "Allow Algo Trading" → OK
4. Verify Experts tab shows:
   [COLD] Trading allowed during hours: ...
   [FILL] Auto-detected filling mode: ORDER_FILLING_FOK
5. Wait for first spike → EA enters automatically
```

## What You Should See

```
=== SpikeCatcher v2 ===
Entry: Bar 2 | TP: 2.0x | SL: 1.0x
Safety: ON | Threshold: 2.5x
Velocity: OFF
COLD Hours: ON | Hours: 0,6,7,9,22,23
MaxHold: 6 bars
Filling: ORDER_FILLING_FOK
```

## COLD Hours by Symbol

| Symbol | Safe Hours (UTC) |
|--------|-----------------|
| Crash 300 | 0,6,7,18,22,23 |
| Crash 500 | 0,6,7,9,22,23 |
| Crash 1000 | 3,6,7,18,22,23 |
| Boom 500 | 1,5,12,14,15,19 |
| Boom 1000 | 6,13,18,20,21,22 |

## Expected Performance

| Metric | Target | Red Flag |
|--------|--------|----------|
| Win Rate | 55-70% | <50% |
| Trades/Day | 15-30 | <5 |
| Safety Exits | 10-20% | >30% |
| Cold Blocked | 20-30% | <10% |

## Quick Commands

```bash
# Check performance
python eval/paper_trading_tracker.py report

# Check specific symbol
python eval/paper_trading_tracker.py report crash500
```

## If Something Goes Wrong

| Error | Fix |
|-------|-----|
| Error 10030 | Recompile EA (F7) |
| No trades | Enable Algo Trading button |
| EA removed | Re-attach to M1 chart |
| All losing | Stop, investigate |

## Timeline

| Day | Action |
|-----|--------|
| 1-3 | Verify EA works, no errors |
| 4-7 | Check performance vs targets |
| 8-14 | Decide: go live or investigate |
| 15+ | If good: live with $200/symbol |

---
*Quick reference for SpikeCatcher v3*
