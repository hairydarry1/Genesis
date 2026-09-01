"""Ultra-fast drift filter research — vectorized numpy only."""
import sys, numpy as np, pandas as pd
from pathlib import Path
sys.path.insert(0, '.')


def compute_rsi_fast(closes, period=14):
    n = len(closes)
    rsi = np.full(n, 50.0)
    if n < period + 1: return rsi
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = np.mean(gains[:period])
    avg_l = np.mean(losses[:period])
    for i in range(period, len(deltas)):
        avg_g = (avg_g * (period-1) + gains[i]) / period
        avg_l = (avg_l * (period-1) + losses[i]) / period
        rsi[i+1] = 100 if avg_l == 0 else 100 - 100/(1 + avg_g/avg_l)
    return rsi


def run(closes, highs, lows, opens, spike_set, spikes_arr, direction, dpp, filt):
    n = len(closes)
    entry_bar = filt.get("eb", 3)
    sl, tp, mh = filt.get("sl", 1.0), filt.get("tp", 3.0), filt.get("mh", 9)
    # Normalize direction: 'up'/'down' → 'short'/'long' (counter-drift)
    is_short = (direction == 'up')  # BOOM spike UP → SHORT the counter-drift
    
    ranges = highs - lows
    med_r = float(np.median(ranges))
    
    # Fast indicators
    rsi = compute_rsi_fast(closes, 14) if "rsi" in filt else None
    vol20 = None
    vol_med = 0
    if "vol" in filt:
        rets = np.zeros(n)
        rets[1:] = np.diff(closes) / np.maximum(closes[:-1], 0.001)
        # Rolling std via cumsum trick
        r2 = rets * rets
        cs_r = np.cumsum(rets)
        cs_r2 = np.cumsum(r2)
        vol20 = np.full(n, np.nan)
        for i in range(20, n):
            vol20[i] = np.sqrt((cs_r2[i] - cs_r2[i-20])/20 - ((cs_r[i] - cs_r[i-20])/20)**2)
        vol_med = float(np.nanmedian(vol20))
    
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
            if is_short:
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
                trades += 1
                if ep * dpp > 0: wins += 1
                else: losses += 1
                in_trade = False
        
        elif not in_trade and bs == entry_bar and last_spike >= 0:
            skip = False
            if "vol" in filt and not np.isnan(vol20[i]) and vol20[i] > vol_med * filt["vol"]: skip = True
            if "spk" in filt and ranges[last_spike] < filt["spk"] * med_r: skip = True
            if "rsi" in filt:
                if is_short and rsi[i] > filt["rsi"]: skip = True
            if "clust" in filt:
                nearby = int(np.sum(np.abs(spikes_arr - last_spike) <= 10)) - 1
                if nearby >= 2: skip = True
            if "drift" in filt:
                if is_short:
                    d = entry_p - closes[last_spike]
                else:
                    d = closes[last_spike] - entry_p
                if d < filt["drift"] * med_r: skip = True
            if skip: continue
            entry_p = closes[i]
            entry_idx = i
            in_trade = True
    
    wr = wins / trades * 100 if trades else 0
    pf = wins / max(losses, 1)
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
        spike_set = set(spikes.tolist())
        
        print(f"\n{'='*60}")
        print(f"  {sym}: {len(ba)} bars, {len(spikes)} spikes")
        print(f"{'='*60}")
        
        base = run(closes, highs, lows, opens, spike_set, spikes, direction, dpp, {})
        print(f"  BASELINE: {base[0]}T  WR={base[1]:.1f}%  PF={base[2]:.2f}  Score={base[1]*base[2]/100:.3f}")
        
        tests = [
            ("Vol<1.0x", {"vol": 1.0}), ("Vol<0.8x", {"vol": 0.8}), ("Vol<0.6x", {"vol": 0.6}),
            ("Spk>3.5x", {"spk": 3.5}), ("Spk>4.0x", {"spk": 4.0}), ("Spk>5.0x", {"spk": 5.0}),
            ("RSI<40", {"rsi": 40}), ("RSI<30", {"rsi": 30}),
            ("Skip clust", {"clust": 1}),
            ("Drift>1.5x", {"drift": 1.5}), ("Drift>2.0x", {"drift": 2.0}),
            ("Vol+RSI", {"vol": 0.8, "rsi": 30}),
            ("Vol+Clust", {"vol": 0.8, "clust": 1}),
            ("Vol+RSI+Clust", {"vol": 0.8, "rsi": 30, "clust": 1}),
            ("All best", {"vol": 0.8, "rsi": 30, "clust": 1, "drift": 1.5}),
            ("Tight", {"vol": 0.6, "rsi": 40, "clust": 1}),
            ("Loose", {"vol": 1.0, "rsi": 20}),
        ]
        
        print(f"\n  {'Filter':<16} {'Trades':>6} {'WR%':>7} {'PF':>6} {'Score':>7}")
        print(f"  {'─'*45}")
        
        best = base[1] * base[2] / 100
        for name, f in tests:
            t, w, p = run(closes, highs, lows, opens, spike_set, spikes, direction, dpp, f)
            score = w * p / 100 if t > 10 else 0
            m = " <" if score > best else ""
            print(f"  {name:<16} {t:>6} {w:>6.1f}% {p:>5.2f} {score:>6.3f}{m}")
            if score > best: best = score
    
    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
