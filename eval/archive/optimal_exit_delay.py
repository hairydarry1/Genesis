#!/usr/bin/env python3
"""Find optimal exit delay after spike for safety exit PnL."""

import numpy as np
from collections import Counter
import os

def load(path):
    return np.load(path, allow_pickle=True)

def detect_spikes(prices, mult=3.0):
    diffs = np.abs(np.diff(prices))
    nz = diffs[diffs > 0]
    if len(nz) == 0:
        return np.array([], dtype=int)
    return np.where(diffs > mult * np.median(nz))[0] + 1

def analyze_spike_retracement(prices, spike_idx, is_crash, max_delay=20):
    """For each spike, measure price at various delays after the spike.
    
    If we're in a position and the spike hits:
    - Exit immediately (delay=0) → worst fill
    - Exit after N ticks → might get better fill if price retraces
    
    For BOOM: spike goes UP → we're SHORT → want to exit low
    For CRASH: spike goes DOWN → we're LONG → want to exit high
    """
    n = len(prices)
    results = {d: [] for d in range(max_delay + 1)}
    
    for si in spike_idx:
        # Price at spike (delay=0)
        spike_price = prices[si]
        
        for delay in range(max_delay + 1):
            if si + delay < n:
                exit_price = prices[si + delay]
                # PnL from the spike's perspective:
                # BOOM (short): we want exit_price as LOW as possible → pnl = spike_price - exit_price
                # CRASH (long): we want exit_price as HIGH as possible → pnl = exit_price - spike_price
                if is_crash:
                    pnl = exit_price - spike_price  # positive = good (price recovered)
                else:
                    pnl = spike_price - exit_price   # positive = good (price dropped back)
                results[delay].append(pnl)
    
    return results

def full_backtest_with_exit_delay(prices, is_crash, entry_delay, exit_delays, tp_pct, hold_bars, spike_mult):
    """Full backtest comparing different safety exit delays."""
    n = len(prices)
    spikes = detect_spikes(prices, spike_mult)
    
    diffs = np.abs(np.diff(prices))
    nz = diffs[diffs > 0]
    sthresh = spike_mult * np.median(nz) if len(nz) > 0 else 1
    
    all_results = {ed: {'trades': [], 'pnl': 0, 'wins': 0, 'reasons': Counter()} for ed in exit_delays}
    
    # For each entry (after spike + entry_delay), track until exit
    cooldown = 0
    
    for si in spikes:
        ei = si + entry_delay
        if ei >= n or ei < cooldown:
            continue
        
        entry_price = prices[ei]
        
        # Find all exit scenarios for different exit_delays
        # We scan forward once and record what happens at each delay
        max_delay = max(exit_delays)
        
        # Track: for each delay, when does the trade exit?
        # We need to simulate the trade forward and see what happens
        # at each exit_delay after the FIRST spike hit
        
        # First, find the first spike that hits after entry
        first_spike_hit = None
        for j in range(ei + 1, min(ei + hold_bars + 1, n)):
            if abs(prices[j] - prices[j-1]) > sthresh:
                first_spike_hit = j
                break
        
        if first_spike_hit is None:
            # No spike hit → timeout exit
            timeout_idx = min(ei + hold_bars, n - 1)
            timeout_pnl = (prices[timeout_idx] - entry_price) / entry_price
            if is_crash:
                timeout_pnl = -timeout_pnl
            
            for ed in exit_delays:
                all_results[ed]['trades'].append(('timeout', timeout_pnl))
                all_results[ed]['reasons']['timeout'] += 1
                if timeout_pnl > 0:
                    all_results[ed]['wins'] += 1
                all_results[ed]['pnl'] += timeout_pnl
            cooldown = timeout_idx + 5
            continue
        
        # Spike hit at first_spike_hit
        # For each exit_delay, exit at first_spike_hit + delay
        for ed in exit_delays:
            exit_idx = min(first_spike_hit + ed, n - 1)
            exit_price = prices[exit_idx]
            
            pnl = (exit_price - entry_price) / entry_price
            if is_crash:
                pnl = -pnl
            
            all_results[ed]['trades'].append(('safety_exit', pnl))
            all_results[ed]['reasons']['safety_exit'] += 1
            if pnl > 0:
                all_results[ed]['wins'] += 1
            all_results[ed]['pnl'] += pnl
        
        # Cooldown based on the latest possible exit
        cooldown = first_spike_hit + max(exit_delays) + 5
    
    return all_results

def main():
    ENTRY_DELAYS = [3, 5, 10]
    EXIT_DELAYS = [0, 1, 2, 3, 5, 7, 10, 15, 20]
    TP_PCT = 0.004
    HOLD_BARS = 200
    SMULT = 3.0
    
    configs = [
        ('BOOM 1000', 'data/ticks_boom1000_index.npy', False),
        ('CRASH 1000', 'data/ticks_crash1000_index.npy', True),
        ('BOOM 500', 'data/ticks_boom_500_index.npy', False),
        ('CRASH 500', 'data/ticks_crash_500_index.npy', True),
    ]
    
    for sym, path, is_crash in configs:
        if not os.path.exists(path):
            continue
        
        prices = load(path)
        spikes = detect_spikes(prices, SMULT)
        
        print(f"\n{'='*80}")
        print(f"  {sym} ({len(prices):,} ticks, {len(spikes)} spikes)")
        print(f"{'='*80}")
        
        # Part 1: Spike retracement analysis
        print(f"\n  SPIKE RETRACEMENT ANALYSIS (what happens after spike):")
        retrac = analyze_spike_retracement(prices, spikes, is_crash, max_delay=20)
        
        print(f"  {'Delay':>6} {'Mean PnL':>10} {'Median':>10} {'Win%':>8} {'P95 Loss':>10} {'P5 Win':>10}")
        print(f"  {'-'*6} {'-'*10} {'-'*10} {'-'*8} {'-'*10} {'-'*10}")
        
        for delay in range(0, 21):
            vals = np.array(retrac[delay])
            mean_pnl = vals.mean()
            median_pnl = np.median(vals)
            win_pct = (vals > 0).mean() * 100
            p5 = np.percentile(vals, 5)
            p95 = np.percentile(vals, 95)
            print(f"  {delay:6d} {mean_pnl:>+.6f}  {median_pnl:>+.6f}  {win_pct:>6.1f}%  {p5:>+.6f}  {p95:>+.6f}")
        
        # Part 2: Full backtest with different exit delays
        print(f"\n  FULL BACKTEST (entry_delay=3):")
        results = full_backtest_with_exit_delay(
            prices, is_crash, entry_delay=3, 
            exit_delays=EXIT_DELAYS, tp_pct=TP_PCT, 
            hold_bars=HOLD_BARS, spike_mult=SMULT
        )
        
        print(f"  {'ExitDelay':>10} {'Trades':>7} {'WR':>7} {'Total PnL':>12} {'Avg PnL':>10} {'PF':>7}")
        print(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*12} {'-'*10} {'-'*7}")
        
        best_pnl = -999
        best_delay = 0
        
        for ed in EXIT_DELAYS:
            r = results[ed]
            n = len(r['trades'])
            if n == 0:
                continue
            pnls = np.array([t[1] for t in r['trades']])
            wr = (pnls > 0).sum() / n * 100
            total = pnls.sum()
            avg = pnls.mean()
            gp = pnls[pnls > 0].sum()
            gl = abs(pnls[pnls <= 0].sum())
            pf = gp / gl if gl > 0 else float('inf')
            
            marker = ""
            if total > best_pnl:
                best_pnl = total
                best_delay = ed
                marker = " * BEST"
            
            print(f"  {ed:10d} {n:7d} {wr:6.1f}% {total:>+12.6f} {avg:>+10.6f} {pf:>7.2f}{marker}")
        
        print(f"\n  Best exit delay: {best_delay} ticks (PnL={best_pnl:+.6f}%)")
        
        # Part 3: Entry delay sensitivity
        print(f"\n  ENTRY DELAY SENSITIVITY (exit_delay=best={best_delay}):")
        print(f"  {'EntryDelay':>10} {'Trades':>7} {'WR':>7} {'Total PnL':>12} {'Avg PnL':>10}")
        print(f"  {'-'*10} {'-'*7} {'-'*7} {'-'*12} {'-'*10}")
        
        for edelay in ENTRY_DELAYS:
            r = full_backtest_with_exit_delay(
                prices, is_crash, entry_delay=edelay,
                exit_delays=[best_delay], tp_pct=TP_PCT,
                hold_bars=HOLD_BARS, spike_mult=SMULT
            )
            rr = r[best_delay]
            n = len(rr['trades'])
            if n == 0:
                continue
            pnls = np.array([t[1] for t in rr['trades']])
            wr = (pnls > 0).sum() / n * 100
            total = pnls.sum()
            avg = pnls.mean()
            print(f"  {edelay:10d} {n:7d} {wr:6.1f}% {total:>+12.6f} {avg:>+10.6f}")
    
    # Summary
    print(f"\n{'='*80}")
    print(f"  OPTIMAL EXIT DELAY SUMMARY")
    print(f"{'='*80}")
    print(f"  {'Symbol':<12} {'Optimal Delay':>14} {'vs Delay=0':>14} {'Improvement':>14}")
    print(f"  {'-'*12} {'-'*14} {'-'*14} {'-'*14}")
    
    for sym, path, is_crash in configs:
        if not os.path.exists(path):
            continue
        prices = load(path)
        
        r0 = full_backtest_with_exit_delay(
            prices, is_crash, 3, [0], TP_PCT, HOLD_BARS, SMULT
        )
        # Test a few delays
        test_delays = [0, 1, 2, 3, 5, 7, 10, 15, 20]
        r_all = full_backtest_with_exit_delay(
            prices, is_crash, 3, test_delays, TP_PCT, HOLD_BARS, SMULT
        )
        
        best_ed = 0
        best_p = -999
        for ed in test_delays:
            p = r_all[ed]['pnl']
            if p > best_p:
                best_p = p
                best_ed = ed
        
        base = r_all[0]['pnl']
        imp = best_p - base
        print(f"  {sym:<12} {best_ed:>14d} ticks {base:>+14.6f}% {imp:>+14.6f}%")

if __name__ == '__main__':
    main()
