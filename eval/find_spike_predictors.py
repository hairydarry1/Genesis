#!/usr/bin/env python3
"""Fast spike predictor analysis on first 500K ticks only."""

import numpy as np
from collections import defaultdict
import os, time

def load(path):
    return np.load(path, allow_pickle=True)[:500000]

def detect_spikes(prices, mult=3.0):
    d = np.abs(np.diff(prices))
    nz = d[d > 0]
    if len(nz) == 0: return np.array([], dtype=int)
    return np.where(d > mult * np.median(nz))[0] + 1

def rmean(arr, w):
    cs = np.concatenate([[0], np.cumsum(arr)])
    out = np.empty(len(arr))
    out[:w] = cs[1:w+1] / np.arange(1, w+1)
    out[w:] = (cs[w+1:] - cs[:len(arr)-w]) / w
    return out

def compute_features(prices):
    n = len(prices)
    d = np.diff(prices)
    ad = np.abs(d)
    feat = {}
    feat['tick_mag'] = np.concatenate([[0], ad])
    feat['tick_dir'] = np.concatenate([[0], np.sign(d)])
    
    for w in [5, 10, 20, 50]:
        feat[f'avg_{w}'] = np.concatenate([[0], rmean(ad, w)])
    
    for w in [10, 20]:
        rm = rmean(ad, w)
        rm2 = rmean(ad**2, w)
        std = np.sqrt(np.maximum(rm2 - rm**2, 0))
        z = np.zeros(n); z[1:] = np.where(std > 0, (ad - rm) / std, 0)
        feat[f'zscore_{w}'] = z
    
    for w in [5, 10, 20]:
        rm = rmean(ad, w)
        feat[f'ratio_{w}'] = np.concatenate([[1], np.where(rm > 0, ad / rm, 1)])
    
    for w in [10, 20, 50]:
        rm = rmean(ad, w)
        rm2 = rmean(ad**2, w)
        feat[f'vol_{w}'] = np.concatenate([[0], np.sqrt(np.maximum(rm2 - rm**2, 0))])
    
    accel = np.zeros(n); accel[2:] = ad[1:] - ad[:-1]
    feat['accel'] = accel
    
    for w in [3, 5]:
        a_sub = accel[2:]
        am = rmean(a_sub, w)
        out = np.zeros(n); out[2:2+len(am)] = am
        feat[f'accel_avg_{w}'] = out
    
    # Rolling max using simple shift approach (no stride_tricks)
    for w in [5, 10]:
        rmax_arr = np.empty(len(ad))
        for i in range(len(ad)):
            s = max(0, i - w + 1)
            rmax_arr[i] = ad[s:i+1].max()
        feat[f'max_{w}'] = np.concatenate([[0], rmax_arr])
    
    # Consecutive runs
    sign_d = np.sign(d)
    up_run = np.zeros(n)
    dn_run = np.zeros(n)
    run_u, run_d = 0, 0
    for i in range(n):
        if i > 0 and d[i-1] > 0: run_u += 1
        elif i > 0: run_u = 0
        if i > 0 and d[i-1] < 0: run_d += 1
        elif i > 0: run_d = 0
        up_run[i] = run_u
        dn_run[i] = run_d
    feat['consec_up'] = up_run
    feat['consec_dn'] = dn_run
    
    # Cluster ratio
    for w in [10, 20]:
        rm_arr = np.empty(len(ad))
        for i in range(len(ad)):
            s = max(0, i - w + 1)
            rm_arr[i] = ad[s:i+1].max()
        rm_mean = rmean(ad, w)
        feat[f'cluster_{w}'] = np.concatenate([[1], np.where(rm_mean > 0, rm_arr / rm_mean, 1)])
    
    return feat

def analyze(smask, nmask, vals, name):
    sv, nv = vals[smask], vals[nmask]
    if len(sv) == 0 or len(nv) == 0: return None
    sm, nm = sv.mean(), nv.mean()
    ns = nv.std()
    ratio = abs(sm - nm) / ns if ns > 0 else 0
    above = (sv > np.median(nv)).mean() * 100
    return (name, sm, nm, np.median(sv), np.median(nv), ratio, above)

def main():
    np.random.seed(42)
    configs = [
        ('BOOM 500', 'data/ticks_boom_500_index.npy', False),
        ('CRASH 500', 'data/ticks_crash_500_index.npy', True),
        ('BOOM 1000', 'data/ticks_boom1000_index.npy', False),
        ('CRASH 1000', 'data/ticks_crash1000_index.npy', True),
    ]
    all_results = defaultdict(list)
    
    for sym, path, is_crash in configs:
        if not os.path.exists(path): continue
        t0 = time.time()
        prices = load(path)
        spike_idx = detect_spikes(prices, 3.0)
        
        print(f"\n{'='*70}")
        print(f"  {sym} ({len(prices):,} ticks, {len(spike_idx)} spikes) [{time.time()-t0:.1f}s load]")
        print(f"{'='*70}")
        
        smask = np.zeros(len(prices), dtype=bool)
        for si in spike_idx:
            if si < len(prices): smask[si] = True
            if si-1 >= 0: smask[si-1] = True
        smask[:100] = False; smask[-100:] = False
        
        ns_idx = np.random.choice(np.where(~smask)[0], size=min(len(spike_idx)*2, (~smask).sum()), replace=False)
        nmask = np.zeros(len(prices), dtype=bool)
        nmask[ns_idx] = True; nmask[:100] = False; nmask[-100:] = False
        
        print(f"  Spike: {smask.sum()}, Non-spike: {nmask.sum()}")
        
        t1 = time.time()
        features = compute_features(prices)
        print(f"  Features computed in {time.time()-t1:.1f}s")
        
        results = []
        for fname, fvals in features.items():
            r = analyze(smask, nmask, fvals, fname)
            if r: results.append(r); all_results[r[0]].append(r)
        
        results.sort(key=lambda x: x[5], reverse=True)
        
        print(f"\n  {'Feature':<22} {'Spike':>10} {'Non-Spike':>10} {'Ratio':>7} {'Above%':>7}")
        print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*7} {'-'*7}")
        for r in results[:25]:
            tag = " ***" if r[5] > 0.5 else " **" if r[5] > 0.3 else " *" if r[5] > 0.15 else ""
            print(f"  {r[0]:<22} {r[1]:>10.4f} {r[2]:>10.4f} {r[5]:>7.3f} {r[6]:>6.1f}%{tag}")
        
        print(f"  Total: {time.time()-t0:.1f}s")
    
    print(f"\n{'='*70}")
    print(f"  CROSS-SYMBOL: Consistent predictors across ALL symbols")
    print(f"{'='*70}")
    summary = []
    for fname, rlist in all_results.items():
        if len(rlist) < 2: continue
        avg_r = np.mean([r[5] for r in rlist])
        min_r = min(r[5] for r in rlist)
        avg_a = np.mean([r[6] for r in rlist])
        summary.append((fname, avg_r, min_r, avg_a, len(rlist)))
    summary.sort(key=lambda x: x[1], reverse=True)
    
    print(f"  {'Feature':<22} {'Avg Ratio':>10} {'Min Ratio':>10} {'Avg Above%':>10}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10}")
    for fname, ar, mr, aa, ns in summary:
        tag = " ***" if mr > 0.3 else " **" if mr > 0.15 else " *" if mr > 0.05 else ""
        print(f"  {fname:<22} {ar:>10.3f} {mr:>10.3f} {aa:>9.1f}%{tag}")

if __name__ == '__main__':
    main()
