#!/usr/bin/env python3
"""
EA LOG ANALYZER
Parse MQ5 logs and find errors, patterns, issues.
"""
import re
from collections import Counter

# The actual logs from user's MQ5 Expert tab
LOGS = """
[BUY FAIL] Error: 10030 Unsupported filling mode
[BUY FAIL] Error: 10030 Unsupported filling mode
[BUY FAIL] Error: 10030 Unsupported filling mode
[BUY FAIL] Error: 10016 Invalid stops
[BUY FAIL] Error: 10016 Invalid stops
[BUY FAIL] Error: 10016 Invalid stops
[BUY FAIL] Error: 10016 Invalid stops
[BUY FAIL] Error: 10016 Invalid stops
[CLOSE FAIL] safety_spike Error: 10029
[CLOSE FAIL] sl Error: 10039
[SLTP FAIL] attempt 1 Error: 10016 Invalid stops
[SLTP FAIL] attempt 1 Error: 10016 Invalid stops
[SLTP] Attached SL=1873.556 TP=1880.832 open=1877.194 (attempt 2, 1.5x)
[SLTP] Attached SL=1871.475 TP=1878.751 open=1875.113 (attempt 2, 1.5x)
[SLTP] Attached SL=6121.1361 TP=6126.2796 open=6122.8506 (attempt 2, 1.5x)
[ENTRY] LONG @ 1877.194 TP=2.426 SL=2.426 Lots=5.00
[ENTRY] LONG @ 1875.113 TP=2.426 SL=2.426 Lots=5.00
[ENTRY] LONG @ 6122.8506 TP=2.2860 SL=1.1430 Lots=5.00
[sl] PnL=$-5.3700 | 1T WR=0.0% PnL=$-5.3700 Safety=0
[sl] PnL=$-5.1600 | 2T WR=0.0% PnL=$-10.5300 Safety=0
[sl] PnL=$-1.8450 | 1T WR=0.0% PnL=$-1.8450 Safety=0
"""

def analyze():
    print("="*60)
    print("EA LOG ANALYSIS")
    print("="*60)
    
    lines = LOGS.strip().split('\n')
    
    # Count errors
    errors = Counter()
    for line in lines:
        if 'Error:' in line:
            match = re.search(r'Error: (\d+)', line)
            if match:
                errors[match.group(1)] += 1
    
    print("\n--- ERROR COUNTS ---")
    error_names = {
        '10030': 'Unsupported filling mode',
        '10016': 'Invalid stops',
        '10029': 'Invalid fill (close rejected)',
        '10039': 'Trade timeout',
    }
    for code, count in errors.most_common():
        name = error_names.get(code, 'Unknown')
        print(f"  Error {code} ({name}): {count} times")
    
    # Count events
    events = Counter()
    for line in lines:
        if '[ENTRY]' in line:
            events['entries'] += 1
        elif '[BUY FAIL]' in line or '[SELL FAIL]' in line:
            events['entry_fails'] += 1
        elif '[SLTP] Attached' in line:
            events['sltp_attached'] += 1
        elif '[SLTP FAIL]' in line:
            events['sltp_fails'] += 1
        elif '[CLOSE FAIL]' in line:
            events['close_fails'] += 1
        elif '[sl]' in line or '[tp]' in line or '[timeout]' in line:
            events['exits'] += 1
    
    print("\n--- EVENT COUNTS ---")
    for event, count in events.items():
        print(f"  {event}: {count}")
    
    # Analyze SLTP attempts
    print("\n--- SLTP ATTACHMENT ---")
    attempt1_fails = sum(1 for l in lines if 'SLTP FAIL] attempt 1' in l)
    attempt2_wins = sum(1 for l in lines if 'attempt 2' in l and 'Attached' in l)
    print(f"  Attempt 1 fails: {attempt1_fails}")
    print(f"  Attempt 2 succeeds: {attempt2_wins}")
    print(f"  SLTP success rate: {attempt2_wins/max(attempt1_fails+attempt2_wins,1)*100:.0f}% (always needs retry)")
    
    # Analyze trades
    print("\n--- TRADE ANALYSIS ---")
    trades = []
    for line in lines:
        if '[sl]' in line or '[tp]' in line or '[timeout]' in line:
            match = re.search(r'PnL=\$([-\d.]+)', line)
            if match:
                trades.append(float(match.group(1)))
    
    if trades:
        wins = sum(1 for t in trades if t > 0)
        losses = sum(1 for t in trades if t <= 0)
        print(f"  Total closed: {len(trades)}")
        print(f"  Wins: {wins}")
        print(f"  Losses: {losses}")
        print(f"  Total PnL: ${sum(trades):.2f}")
        print(f"  Avg loss: ${sum(t for t in trades if t <= 0)/max(losses,1):.2f}")
    
    # Identify bugs
    print("\n--- BUGS IDENTIFIED ---")
    print("""
  1. Error 10030 (filling mode): FIXED in v2.04
     - EA now auto-detects FOK/IOC
     - No more filling mode errors on new versions
  
  2. Error 10016 (invalid stops): PARTIALLY FIXED
     - EA opens with sl=0, tp=0
     - Attaches SL separately
     - But SLTP attempt 1 STILL fails every time
     - Always needs retry at 1.5x
  
  3. Error 10029 (close rejected): NOT FIXED
     - Safety exit failed
     - Position stayed open during spike
     - EA retried but may have failed
  
  4. Error 10039 (timeout): NOT FIXED
     - SL close timed out
     - Server was slow to respond
  
  5. Duplicate entries: FIXED in v2.05
     - Added position check before entry
  
  6. Close retry: FIXED in v2.05
     - Retries 3 times with 100ms delay
""")
    
    # Recommendations
    print("\n--- RECOMMENDATIONS ---")
    print("""
  1. INCREASE SL BUFFER
     - Current: 1.5x minimum (still fails on attempt 1)
     - Try: 2.0x minimum (should succeed first try)
  
  2. INCREASE ORDER DEVIATION
     - Current: 500 points
     - Try: 1000 points (wider tolerance)
  
  3. ADD SL ACKNOWLEDGMENT CHECK
     - After opening position, verify SL is attached
     - If not, close immediately
  
  4. MONITOR CLOSE FAILURES
     - Log when close fails
     - Alert user if position stuck open
""")

if __name__ == "__main__":
    analyze()
