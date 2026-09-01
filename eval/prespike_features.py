#!/usr/bin/env python3
"""Test if features at -1/-2/-3 ticks before spikes differ from non-spike ticks."""

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
    for w in [5, 10, 20]:
        feat[f'avg_{w}'] = np.concatenate([[0], rmean(ad, w)])
    for w in [5, 10, 20]:
        rm = rmean(ad, w)
        feat[f'ratio_{w}'] = np.concatenate([[1], np.where(rm > 0, ad / rm, 1)])
    for w in [10, 20]:
        rm = rmean(ad, w)
        rm2 = rmean(ad**2, w)
        std = np.sqrt(np.maximum(rm2 - rm**2, 0))
        z = np.zeros(n); z[1:] = np.where(std > 0, (ad - rm) / std, 0)
        feat[f'zscore_{w}'] = z
    accel = np.zeros(n); accel[2:] = ad[1:] - ad[:-1]
    feat['accel'] = accel
    for w in [3, 5]:
        a_sub = accel[2:]
        am = rmean(a_sub, w)
        out = np.zeros(n); out[2:2+len(am)] = am
        feat[f'accel_avg_{w}'] = out
    # Volatility
    for w in [10, 20]:
        rm = rmean(ad, w)
        rm2 = rmean(ad**2, w)
        feat[f'vol_{w}'] = np.concatenate([[0], np.sqrt(np.maximum(rm2 - rm**2, 0))])
    return feat

def main():
    np.random.seed(42)
    configs = [
        ('BOOM 500', 'data/ticks_boom_500_index.npy', False),
        ('CRASH 500', 'data/ticks_crash_500_index.npy', True),
        ('BOOM 1000', 'data/ticks_boom1000_index.npy', False),
        ('CRASH 1000', 'data/ticks_crash1000_index.npy', True),
    ]
    
    # Features to test at each offset
    test_features = ['tick_mag', 'ratio_5', 'ratio_10', 'ratio_20', 
                     'accel', 'accel_avg_3', 'accel_avg_5',
                     'zscore_10', 'zscore_20', 'vol_10', 'vol_20']
    
    offsets = [-3, -2, -1, 0, 1, 2, 3]
    
    all_symbol_results = {}
    
    for sym, path, is_crash in configs:
        if not os.path.exists(path): continue
        t0 = time.time()
        prices = load(path)
        spike_idx = detect_spikes(prices, 3.0)
        
        print(f"\n{'='*80}")
        print(f"  {sym} ({len(prices):,} ticks, {len(spike_idx)} spikes)")
        print(f"{'='*80}")
        
        features = compute_features(prices)
        n = len(prices)
        
        # Non-spike reference positions (far from any spike)
        is_spike = np.zeros(n, dtype=bool)
        for si in spike_idx:
            for offset in range(-10, 11):
                idx = si + offset
                if 0 <= idx < n:
                    is_spike[idx] = True
        
        non_spike_pool = np.where(~is_spike)[0]
        non_spike_pool = non_spike_pool[(non_spike_pool >= 50) & (non_spike_pool < n - 50)]
        
        results = defaultdict(dict)
        
        for feat_name in test_features:
            fvals = features[feat_name]
            
            # Non-spike reference
            ns_vals = fvals[non_spike_pool]
            ns_median = np.median(ns_vals)
            ns_mean = ns_vals.mean()
            ns_std = ns_vals.std()
            
            for offset in offsets:
                # Collect feature values at this offset before/after each spike
                vals = []
                for si in spike_idx:
                    idx = si + offset
                    if 0 <= idx < n:
                        vals.append(fvals[idx])
                
                if not vals: continue
                vals = np.array(vals)
                
                # Compare to non-spike
                mean_ratio = abs(vals.mean() - ns_mean) / ns_std if ns_std > 0 else 0
                above = (vals > ns_median).mean() * 100
                
                results[feat_name][offset] = {
                    'mean': vals.mean(),
                    'median': np.median(vals),
                    'ratio': mean_ratio,
                    'above': above,
                    'ns_mean': ns_mean,
                    'ns_median': ns_median,
                }
        
        # Print heatmap-style table
        print(f"\n  {'Feature':<15} | {'-3':>6} {'-2':>6} {'-1':>6} {'SPIKE':>6} {'+1':>6} {'+2':>6} {'+3':>6} | NS")
        print(f"  {'-'*15}-+-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-+{'-'*8}")
        
        for feat_name in test_features:
            row_mean = f"  {feat_name:<15} |"
            row_ratio = f"  {'(ratio)':<15} |"
            for offset in offsets:
                if offset in results[feat_name]:
                    r = results[feat_name][offset]
                    val = r['mean']
                    ratio = r['ratio']
                    # Color-code: high ratio = more different from non-spike
                    tag = "*" if ratio > 0.3 else "." if ratio > 0.1 else " "
                    row_mean += f" {val:>5.3f}{tag}"
                    row_ratio += f" {ratio:>5.2f}x "
                else:
                    row_mean += "     - "
                    row_ratio += "     - "
            ns_m = results[feat_name][0]['ns_mean'] if 0 in results[feat_name] else 0
            row_mean += f" {ns_m:>7.4f}"
            print(row_mean)
            print(row_ratio)
            print()
        
        all_symbol_results[sym] = results
        print(f"  [{time.time()-t0:.1f}s]")
    
    # Cross-symbol summary: which offsets have highest ratio?
    print(f"\n{'='*80}")
    print(f"  CROSS-SYMBOL SUMMARY: Mean ratio at each offset (higher = more predictive)")
    print(f"{'='*80}")
    
    print(f"\n  {'Feature':<15} | {'-3':>6} {'-2':>6} {'-1':>6} {'SPIKE':>6} {'+1':>6} {'+2':>6} {'+3':>6}")
    print(f"  {'-'*15}-+-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}-{'-'*6}")
    
    for feat_name in test_features:
        row = f"  {feat_name:<15} |"
        for offset in offsets:
            ratios = []
            for sym in all_symbol_results:
                if feat_name in all_symbol_results[sym] and offset in all_symbol_results[sym][feat_name]:
                    ratios.append(all_symbol_results[sym][feat_name][offset]['ratio'])
            if ratios:
                avg = np.mean(ratios)
                tag = "*" if avg > 0.3 else "." if avg > 0.1 else " "
                row += f" {avg:>5.2f}{tag}"
            else:
                row += "     - "
        print(row)
    
    # Key insight
    print(f"\n  Legend: * = ratio > 0.3 (strong signal), . = ratio > 0.1 (weak signal)")
    print(f"  Ratio = how many std devs the feature differs from non-spike reference")
    print(f"  SPIKE = offset 0 (the spike tick itself)")
    print(f"  -3/-2/-1 = ticks BEFORE spike (prediction window)")

if __name__ == '__main__':
    main()
