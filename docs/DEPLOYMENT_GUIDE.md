# SpikeCatcher v3 — Demo Deployment Guide

## Step 1: Set Up Deriv Demo Account

If you don't have a demo account:
1. Go to https://www.deriv.com/demo/
2. Click "Create free demo account"
3. Choose $10,000 virtual balance
4. Select MetaTrader 5 platform
5. Note your login credentials

## Step 2: Connect MT5 to Deriv

1. Open MetaTrader 5
2. File → Login to Trade Account
3. Enter demo credentials
4. Server: Deriv-Demo

## Step 3: Install the EA

1. Open MetaEditor (F4)
2. File → Open Data Folder
3. Navigate to: MQL5/Experts/
4. Copy `mt5/SpikeCatcher_v2.mq5` to this folder
5. In MetaEditor: Refresh (F5)
6. Double-click SpikeCatcher_v2.mq5
7. Compile (F7) — should show "0 errors"

## Step 4: Attach to Chart

1. In MT5, open a M1 chart (e.g., Crash 500 Index)
2. In Navigator panel (Ctrl+N), find Experts → SpikeCatcher_v2
3. Drag EA onto chart
4. In the dialog:
   - Check "Allow Algo Trading"
   - Click OK

## Step 5: Verify It's Working

Check the Experts tab (bottom of MT5) for these messages:

```
=== SpikeCatcher v2 ===
Entry: Bar 2 | TP: 2.0x | SL: 1.0x
Safety: ON | Threshold: 2.5x
Velocity: OFF | Threshold: 12 ticks
COLD Hours: ON | Hours: 0,6,7,9,22,23
MaxHold: 6 bars
Cluster: skip if 3+ in 10 bars
Filling: ORDER_FILLING_FOK
[COLD] Trading allowed during hours: 0,6,7,9,22,23
```

If you see `[FILL] Auto-detected filling mode: ORDER_FILLING_FOK` — the filling bug is fixed!

## Step 6: Watch for First Trade

The EA will:
1. Detect spikes (print `[SPIKE #1] Bar X Range=...`)
2. Check if COLD hour (print `[COLD BLOCK]` if not)
3. Enter trade after 2 bars (print `[ENTRY] LONG/SHORT @ ...`)
4. Exit on safety exit, timeout, or tick exit (print `[safety_spike]` or `[timeout]`)

## Step 7: Monitor Performance

After 24 hours, check:

```bash
python eval/paper_trading_tracker.py report
```

Expected results:
- Trades: 15-30 per day
- Win Rate: 55-70%
- Cold Hour Trades: >80%
- Safety Exits: 10-20% of trades

## Step 8: Red Flags to Watch

| Signal | Meaning | Action |
|--------|---------|--------|
| `[BUY FAIL] Error: 10030` | Filling mode issue | Recompile EA |
| `[COLD BLOCK]` not appearing | COLD filter not working | Check symbol name |
| 0 trades after 24 hours | No spikes detected | Check chart timeframe (M1) |
| All trades losing | Strategy broken | Stop, investigate |

## Quick Checklist

- [ ] Demo account created
- [ ] MT5 connected to Deriv
- [ ] EA compiled (0 errors)
- [ ] EA attached to M1 chart
- [ ] Experts tab shows correct settings
- [ ] First spike detected
- [ ] First trade executed
- [ ] No error messages

## Supported Symbols

| Symbol | COLD Hours | Status |
|--------|------------|--------|
| Crash 300 Index | 0,6,7,18,22,23 | ✅ Ready |
| Crash 500 Index | 0,6,7,9,22,23 | ✅ Ready |
| Crash 1000 Index | 3,6,7,18,22,23 | ✅ Ready |
| Boom 500 Index | 1,5,12,14,15,19 | ✅ Ready |
| Boom 1000 Index | 6,13,18,20,21,22 | ✅ Ready |

## Troubleshooting

### "Filling mode error" still appears
→ Make sure you recompiled after the fix
→ Remove EA from chart, re-attach

### No trades happening
→ Check if "Algo Trading" button is enabled (top of MT5)
→ Check chart is M1 timeframe
→ Check symbol name matches (e.g., "Crash 500 Index" not "CRASH500")

### EA removed from chart
→ This happens if you switch timeframes
→ Re-attach to M1 chart
→ State resets (no position tracking)

---
*Guide for SpikeCatcher v3 demo deployment*
*Last updated: September 1, 2026*
