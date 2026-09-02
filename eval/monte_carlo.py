#!/usr/bin/env python3
"""MONTE CARLO SIMULATION - Quick version"""
import numpy as np

def sim(name, wr, avg_w, avg_l, trades=200, sims=500):
    print(f"\n{'='*50}")
    print(f"{name} | WR={wr*100:.1f}% | Win={avg_w:+.4f} | Loss={avg_l:.4f} | RR={avg_w/abs(avg_l):.1f}")
    
    equities = np.full(sims, 1000.0)
    peaks = np.full(sims, 1000.0)
    max_dds = np.zeros(sims)
    max_ls = np.zeros(sims, dtype=int)
    cur_ls = np.zeros(sims, dtype=int)
    
    for _ in range(trades):
        wins = np.random.random(sims) < wr
        equities += np.where(wins, avg_w, avg_l)
        peaks = np.maximum(peaks, equities)
        dds = (peaks - equities) / peaks * 100
        max_dds = np.maximum(max_dds, dds)
        cur_ls = np.where(wins, 0, cur_ls + 1)
        max_ls = np.maximum(max_ls, cur_ls)
    
    prof = np.sum(equities > 1000) / sims * 100
    ruin = np.sum(equities < 500) / sims * 100
    
    print(f"  Mean equity: ${np.mean(equities):.2f} | Profit prob: {prof:.1f}%")
    print(f"  Mean DD: {np.mean(max_dds):.1f}% | P95 DD: {np.percentile(max_dds, 95):.1f}% | Worst DD: {np.max(max_dds):.1f}%")
    print(f"  Mean max loss streak: {np.mean(max_ls):.0f} | P95: {np.percentile(max_ls, 95):.0f} | Worst: {np.max(max_ls)}")
    print(f"  Ruin prob (below $500): {ruin:.1f}%")
    print(f"  Worst outcome: ${np.min(equities):.2f} | Best: ${np.max(equities):.2f}")

print("="*50)
print("MONTE CARLO: 500 sims x 200 trades each")
print("="*50)

sim("BOOM1000", 0.997, 0.014, -0.007)
sim("CRASH1000", 0.999, 0.005, -0.0025)
sim("BOOM500", 0.852, 0.502, -0.251)
sim("CRASH500", 0.855, 0.304, -0.152)

# Combined
print(f"\n{'='*50}")
print("COMBINED PORTFOLIO ($4000)")
print("="*50)
eq = np.full(500, 4000.0)
for _ in range(200):
    eq += np.where(np.random.random(500)<0.997, 0.014, -0.007)
    eq += np.where(np.random.random(500)<0.999, 0.005, -0.0025)
    eq += np.where(np.random.random(500)<0.852, 0.502, -0.251)
    eq += np.where(np.random.random(500)<0.855, 0.304, -0.152)

prof = np.sum(eq > 4000) / 500 * 100
print(f"  Mean: ${np.mean(eq):.2f} | Profit prob: {prof:.1f}%")
print(f"  Worst: ${np.min(eq):.2f} | Best: ${np.max(eq):.2f}")
print(f"  Mean profit: ${np.mean(eq)-4000:.2f}")
