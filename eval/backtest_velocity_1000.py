#!/usr/bin/env python3
"""Fixed velocity exit backtest — correct buildup direction."""

import numpy as np
from collections import Counter
import time, os

def load(path):
    return np.load(path, allow_pickle=True)

def detect_spikes(prices, mult=3.0):
    diffs = np.abs(np.diff(prices))
    nz = diffs[diffs > 0]
    if len(nz) == 0:
        return np.array([], dtype=int)
    return np.where(diffs > mult * np.median(nz))[0] + 1

def precompute(prices):
    """Precompute consecutive uptick/downtick runs."""
    n = len(prices)
    diffs = np.diff(prices)
    
    up = np.zeros(n, dtype=np.int32)
    dn = np.zeros(n, dtype=np.int32)
    
    is_up = np.concatenate([[False], diffs > 0])
    run = 0
    for i in range(n):
        if is_up[i]:
            run += 1
        else:
            run = 0
        up[i] = run
    
    is_dn = np.concatenate([[False], diffs < 0])
    run = 0
    for i in range(n):
        if is_dn[i]:
            run += 1
        else:
            run = 0
        dn[i] = run
    
    return up, dn

def backtest(prices, is_crash, threshold, tp_pct, hold_bars, entry_delay, spike_mult):
    """Velocity exit backtest with CORRECT buildup direction.
    
    BOOM: price drifts DOWN → spike is UP. Buildup = downticks (dn_run).
    CRASH: price drifts UP → spike is DOWN. Buildup = upticks (up_run).
    """
    n = len(prices)
    
    spikes = detect_spikes(prices, spike_mult)
    
    up_run, dn_run = precompute(prices)
    
    # CORRECT: BOOM buildup=downticks, CRASH buildup=upticks
    if is_crash:
        buildup = up_run  # Price drifting up before crash = uptick buildup
    else:
        buildup = dn_run  # Price drifting down before boom = downtick buildup
    
    # Spike ahead map
    W = threshold + 30
    spike_ahead = np.zeros(n, dtype=bool)
    for si in spikes:
        s = max(0, si - W)
        spike_ahead[s:si] = True
    
    # Median tick for spike detection
    diffs = np.abs(np.diff(prices))
    nz = diffs[diffs > 0]
    sthresh = spike_mult * np.median(nz) if len(nz) > 0 else 1
    
    results = []
    cooldown = 0
    
    for si in spikes:
        ei = si + entry_delay
        if ei >= n or ei < cooldown:
            continue
        
        ep = prices[ei]
        exit_idx = min(ei + hold_bars, n - 1)
        reason = "timeout"
        
        end = min(ei + hold_bars + 1, n)
        
        for j in range(ei + 1, end):
            # Spike hit?
            if abs(prices[j] - prices[j-1]) > sthresh:
                exit_idx, reason = j, "safety_exit"
                break
            
            # Velocity exit
            if buildup[j] >= threshold and spike_ahead[j]:
                exit_idx, reason = j, "velocity_exit"
                break
            
            # TP
            if is_crash and prices[j] <= ep * (1 - tp_pct):
                exit_idx, reason = j, "tp"
                break
            if not is_crash and prices[j] >= ep * (1 + tp_pct):
                exit_idx, reason = j, "tp"
                break
        
        pnl = (prices[exit_idx] - ep) / ep
        if is_crash:
            pnl = -pnl
        results.append((pnl, reason))
        cooldown = exit_idx + 5
    
    return results

def summarize(trades, name):
    if not trades:
        print(f"  {name}: No trades")
        return
    pnls = np.array([t[0] for t in trades])
    n = len(pnls)
    wr = (pnls > 0).sum() / n * 100
    tp = pnls.sum()
    gp = pnls[pnls > 0].sum()
    gl = abs(pnls[pnls <= 0].sum())
    pf = gp / gl if gl > 0 else float('inf')
    rc = Counter(t[1] for t in trades)
    print(f"  {name}: Trades={n} WR={wr:.1f}% PnL={tp:.4f}% PF={pf:.2f}")
    for r in sorted(rc):
        rp = np.array([t[0] for t in trades if t[1] == r])
        rw = (rp > 0).sum() / len(rp) * 100
        print(f"    {r}: {rc[r]} ({rw:.0f}% WR, PnL={rp.sum():.4f}%)")

def main():
    THRESHOLDS = [10, 15, 20, 30, 50, 75, 100]
    TP = 0.004
    HOLD = 200
    SMULT = 3.0
    EDELAY = 3
    
    configs = [
        ('BOOM 1000', 'data/ticks_boom1000_index.npy', False),
        ('CRASH 1000', 'data/ticks_crash1000_index.npy', True),
    ]
    
    for sym, path, is_crash in configs:
        if not os.path.exists(path):
            print(f"\n{sym}: No data")
            continue
        
        prices = load(path)
        print(f"\n{'='*70}")
        print(f"  {sym} ({len(prices):,} ticks)")
        print(f"{'='*70}")
        
        spikes = detect_spikes(prices, SMULT)
        up_run, dn_run = precompute(prices)
        
        # Buildup analysis with CORRECT direction
        if is_crash:
            buildup_arr = up_run  # upticks before crash
            dir_name = "upticks"
        else:
            buildup_arr = dn_run  # downticks before boom
            dir_name = "downticks"
        
        print(f"  Spikes: {len(spikes)}")
        print(f"  Buildup type: {dir_name}")
        print(f"  Overall buildup stats: mean={buildup_arr.mean():.1f} median={np.median(buildup_arr):.0f} max={buildup_arr.max()}")
        
        print(f"  Buildup distribution:")
        for t in [5, 10, 20, 50, 100, 200]:
            pct = (buildup_arr >= t).mean() * 100
            print(f"    >= {t:3d}: {pct:.1f}%")
        
        print(f"\n  Buildup BEFORE spikes (at various offsets):")
        for offset in [1, 3, 5, 10, 20, 50, 100]:
            vals = []
            for si in spikes:
                if 0 <= si - offset < len(buildup_arr):
                    vals.append(buildup_arr[si - offset])
            if vals:
                mean_b = np.mean(vals)
                pct_ge10 = sum(1 for v in vals if v >= 10) / len(vals) * 100
                pct_ge20 = sum(1 for v in vals if v >= 20) / len(vals) * 100
                pct_ge50 = sum(1 for v in vals if v >= 50) / len(vals) * 100
                pct_ge100 = sum(1 for v in vals if v >= 100) / len(vals) * 100
                print(f"    Offset -{offset:4d}: mean={mean_b:7.1f}  >=10: {pct_ge10:5.1f}%  >=20: {pct_ge20:5.1f}%  >=50: {pct_ge50:5.1f}%  >=100: {pct_ge100:5.1f}%")
        
        for thr in THRESHOLDS:
            trades = backtest(prices, is_crash, thr, TP, HOLD, EDELAY, SMULT)
            summarize(trades, f"Threshold={thr}")
    
    # Final comparison
    print(f"\n{'='*70}")
    print(f"  FINAL COMPARISON: All 4 symbols")
    print(f"{'='*70}")
    print(f"  {'Symbol':<12} {'NoVel PnL':>12} {'Thr=20 PnL':>14} {'Thr=50 PnL':>14} {'Thr=100 PnL':>14}")
    print(f"  {'-'*12} {'-'*12} {'-'*14} {'-'*14} {'-'*14}")
    
    for sym, path, is_crash in [
        ('BOOM 1000', 'data/ticks_boom1000_index.npy', False),
        ('CRASH 1000', 'data/ticks_crash1000_index.npy', True),
        ('BOOM 500', 'data/ticks_boom_500_index.npy', False),
        ('CRASH 500', 'data/ticks_crash_500_index.npy', True),
    ]:
        if not os.path.exists(path):
            continue
        prices = load(path)
        t_no = backtest(prices, is_crash, 99999, TP, HOLD, EDELAY, SMULT)
        t20 = backtest(prices, is_crash, 20, TP, HOLD, EDELAY, SMULT)
        t50 = backtest(prices, is_crash, 50, TP, HOLD, EDELAY, SMULT)
        t100 = backtest(prices, is_crash, 100, TP, HOLD, EDELAY, SMULT)
        
        p_no = sum(t[0] for t in t_no)
        p20 = sum(t[0] for t in t20)
        p50 = sum(t[0] for t in t50)
        p100 = sum(t[0] for t in t100)
        
        n_safety = sum(1 for t in t_no if t[1] == 'safety_exit')
        n20_vel = sum(1 for t in t20 if t[1] == 'velocity_exit')
        n50_vel = sum(1 for t in t50 if t[1] == 'velocity_exit')
        n100_vel = sum(1 for t in t100 if t[1] == 'velocity_exit')
        
        print(f"  {sym:<12} {p_no:>+.4f}%     {p20:>+.4f}%({n20_vel:3d}v)  {p50:>+.4f}%({n50_vel:3d}v)  {p100:>+.4f}%({n100_vel:3d}v)")

if __name__ == '__main__':
    main()
