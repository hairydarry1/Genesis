"""Optimized drift filter research — fast numpy, fewer tests."""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles, compute_rsi, compute_ema, compute_sma


def run(closes, highs, lows, opens, spikes, direction, dpp, filt):
    n = len(closes)
    spike_set = set(spikes.tolist())
    entry_bar = filt.get("eb", 3)
    sl, tp, mh = filt.get("sl", 1.0), filt.get("tp", 3.0), filt.get("mh", 9)
    
    ranges = highs - lows
    med_r = float(np.median(ranges))
    rsi = compute_rsi(closes, 14)
    ema9 = compute_ema(closes, 9)
    ema18 = compute_sma(closes, 18)
    
    vol_std = np.std(np.diff(closes) / np.maximum(closes[:-1], 0.001))
    # Rolling 20-bar vol
    rets = np.concatenate([[0], np.diff(closes) / np.maximum(closes[:-1], 0.001)])
    vol20 = np.full(n, np.nan)
    for i in range(20, n):
        vol20[i] = np.std(rets[i-20:i])
    vol_med = np.nanmedian(vol20)
    
    # Cluster detection
    spike_cluster = set()
    spikes_arr = np.array(spikes)
    for s in spikes_arr:
        nearby = np.sum(np.abs(spikes_arr - s) <= 10) - 1
        if nearby >= 2:
            spike_cluster.add(int(s))
    
    last_spike = -1
    in_trade = False
    entry_p = 0.0
    entry_idx = 0
    trades = wins = losses = 0
    
    for i in range(n):
        if i in spike_set:
            last_spike = i
        bs = i - last_spike if last_spike >= 0 else 999
        
        if in_trade:
            h, l, c = highs[i], lows[i], closes[i]
            held = i - entry_idx
            if direction == 'short':
                hit_tp = (entry_p - l) >= tp
                hit_sl = (entry_p - h) <= -sl
                unreal = entry_p - c
            else:
                hit_tp = (h - entry_p) >= tp
                hit_sl = (l - entry_p) <= -sl
                unreal = c - entry_p
            
            ep = None
            if hit_tp: ep = tp
            elif hit_sl: ep = -sl
            elif i in spike_set: ep = unreal
            elif held >= mh: ep = unreal
            if ep is not None:
                pnl = ep * dpp
                trades += 1
                if pnl > 0: wins += 1
                else: losses += 1
                in_trade = False
        
        elif not in_trade and bs == entry_bar and last_spike >= 0:
            skip = False
            
            if "vol" in filt and not np.isnan(vol20[i]):
                if vol20[i] > vol_med * filt["vol"]: skip = True
            if "spk" in filt and ranges[last_spike] < filt["spk"] * med_r: skip = True
            if "rsi" in filt:
                if direction == 'short' and rsi[i] > filt["rsi"]: skip = True
                elif direction != 'short' and rsi[i] < (100 - filt["rsi"]): skip = True
            if "ema" in filt and not np.isnan(ema9[i]) and not np.isnan(ema18[i]):
                if direction == 'short' and ema9[i] > ema18[i]: skip = True
                elif direction != 'short' and ema9[i] < ema18[i]: skip = True
            if "clust" in filt and last_spike in spike_cluster: skip = True
            if "drift" in filt:
                if direction == 'short':
                    d = entry_p - closes[last_spike]
                else:
                    d = closes[last_spike] - entry_p
                if d < filt["drift"] * med_r: skip = True
            
            if skip: continue
            entry_p = closes[i]
            entry_idx = i
            in_trade = True
    
    wr = wins / trades * 100 if trades else 0
    tw = wins * tp * dpp
    tl = losses * sl * dpp * 0.6  # approx
    pf = tw / max(tl, 0.01)
    return trades, wr, pf


def main():
    for sym, direction, dpp in [("BOOM500", "up", 0.20), ("BOOM1000", "up", 0.10)]:
        path = f"data/candles_{sym.lower()}_1min.parquet"
        if not Path(path).exists(): continue
        df = pd.read_parquet(path)
        df.columns = [c.lower() for c in df.columns]
        ba = df[['open','high','low','close']].values.astype(float)
        closes, highs, lows, opens = ba[:,3], ba[:,1], ba[:,2], ba[:,0]
        ranges = highs - lows
        med_r = float(np.median(ranges))
        spikes = np.array([i for i in range(len(ba)) if ranges[i] > 3.0*med_r and 
                          (ba[i,3] > ba[i,0] if direction=='up' else ba[i,3] < ba[i,0])])
        
        print(f"\n{'='*70}")
        print(f"  {sym}: {len(ba)} bars, {len(spikes)} spikes")
        print(f"{'='*70}")
        
        base = run(closes, highs, lows, opens, spikes, direction, dpp, {})
        print(f"  BASELINE: {base[0]}T  WR={base[1]:.1f}%  PF={base[2]:.2f}")
        
        tests = [
            ("Vol<1.0x", {"vol": 1.0}), ("Vol<0.8x", {"vol": 0.8}), ("Vol<0.6x", {"vol": 0.6}),
            ("Spk>3.5x", {"spk": 3.5}), ("Spk>4.0x", {"spk": 4.0}), ("Spk>5.0x", {"spk": 5.0}),
            ("RSI<40", {"rsi": 40}), ("RSI<30", {"rsi": 30}),
            ("EMA align", {"ema": 1}), ("Skip cluster", {"clust": 1}),
            ("MinDrift 1.5x", {"drift": 1.5}), ("MinDrift 2.0x", {"drift": 2.0}),
            ("Vol+RSI", {"vol": 0.8, "rsi": 30}),
            ("Vol+EMA", {"vol": 0.8, "ema": 1}),
            ("Vol+RSI+EMA", {"vol": 0.8, "rsi": 30, "ema": 1}),
            ("Vol+Clust", {"vol": 0.8, "clust": 1}),
            ("All best", {"vol": 0.8, "rsi": 30, "ema": 1, "clust": 1}),
            ("All+Drift", {"vol": 0.8, "rsi": 30, "ema": 1, "clust": 1, "drift": 1.5}),
            ("Tight", {"vol": 0.6, "rsi": 40, "ema": 1}),
            ("Loose", {"vol": 1.0, "rsi": 20}),
        ]
        
        print(f"\n  {'Filter':<18} {'Trades':>6} {'WR%':>7} {'PF':>6} {'Score':>7}")
        print(f"  {'─'*48}")
        
        best = base[1] * base[2] / 100
        for name, f in tests:
            t, w, p = run(closes, highs, lows, opens, spikes, direction, dpp, f)
            score = w * p / 100 if t > 10 else 0
            m = " <" if score > best else ""
            print(f"  {name:<18} {t:>6} {w:>6.1f}% {p:>5.2f} {score:>6.3f}{m}")
            if score > best: best = score
        
    print(f"\n{'='*70}")


if __name__ == "__main__":
    main()
