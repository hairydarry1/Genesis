"""
eval/combined_strategy.py — Three-Strategy Combined System

Strategy 1: SPIKE ANTICIPATION
  - Track time since last spike
  - After 50+ bars quiet → spike is imminent (2-3x bigger)
  - Enter BEFORE spike in expected direction
  - Ride the full spike (big wins, lower frequency)

Strategy 2: BUILD-UP TRADING
  - Enter during quiet periods after clusters
  - Wait for spike to happen
  - Ride the spike when it comes

Strategy 3: COUNTER-DRIFT FADE (existing)
  - After spike → fade the counter-drift
  - High frequency, smaller wins

Combined: Risk budget split across all three.

Run:
    python eval/combined_strategy.py
"""

import sys, numpy as np, tempfile, shutil
from pathlib import Path
sys.path.insert(0, '.')

from eval.smc_spike_hunter import load_candles
from model.strategy_brain import StrategyBrain
from model.trade_thinker import MarketContext, TradeOutcome


def run_combined(symbol, max_bars=20000):
    """Run all three strategies combined."""
    is_boom = 'boom' in symbol.lower()
    dpp = 0.10 if '1000' in symbol else 0.20
    m1 = load_candles(symbol, '1min')[-max_bars:]
    
    tp, sl, mh = 3.0, 1.0, 9
    spike_thresh = 3.0
    
    # State
    ranges = []
    spikes = []
    spike_times = []  # bar indices of spikes
    last_spike_idx = -999
    bars_since_spike = 999
    
    # Strategy states
    # 1. Spike anticipation
    anticipation_pos = 0  # 0=flat, 1=long, -1=short
    anticipation_entry = 0.0
    anticipation_bars = 0
    anticipation_active = False
    
    # 2. Build-up trading
    buildup_pos = 0
    buildup_entry = 0.0
    buildup_bars = 0
    buildup_active = False
    
    # 3. Counter-drift fade
    fade_pos = 0
    fade_entry = 0.0
    fade_bars = 0
    
    # Trade tracking
    trades = {'anticipation': [], 'buildup': [], 'fade': []}
    equity = 200.0
    
    for i, bar in enumerate(m1):
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        br = h - l
        ranges.append(br)
        if len(ranges) > 100:
            ranges.pop(0)
        med = float(np.median(ranges))
        
        # Detect spike
        is_spike = br > spike_thresh * med
        is_boom_spike = is_spike and c > o
        is_crash_spike = is_spike and c < o
        
        spike_happened = False
        if (is_boom and is_boom_spike) or (not is_boom and is_crash_spike):
            spike_happened = True
            last_spike_idx = i
            bars_since_spike = 0
            spikes.append(i)
            spike_times.append(i)
        else:
            bars_since_spike += 1
        
        # ── STRATEGY 1: SPIKE ANTICIPATION ──
        # After long quiet period, spike is imminent — enter BEFORE it
        if anticipation_active:
            anticipation_bars += 1
            if anticipation_pos == 1:
                hit_tp = (h - anticipation_entry) >= tp * 1.5  # Wider TP for anticipation
                hit_sl = (l - anticipation_entry) <= -sl
                hit_spike = spike_happened and c > anticipation_entry
            else:
                hit_tp = (anticipation_entry - l) >= tp * 1.5
                hit_sl = (anticipation_entry - h) <= -sl
                hit_spike = spike_happened and c < anticipation_entry
            
            exit_pts = None
            reason = None
            if hit_tp:
                exit_pts = tp * 1.5; reason = 'tp'
            elif hit_sl:
                exit_pts = -sl; reason = 'sl'
            elif hit_spike:
                # Spike happened in our direction — ride it
                u = (c - anticipation_entry) if anticipation_pos == 1 else (anticipation_entry - c)
                exit_pts = u; reason = 'spike_captured'
            elif anticipation_bars >= 15:
                u = (c - anticipation_entry) if anticipation_pos == 1 else (anticipation_entry - c)
                exit_pts = u; reason = 'timeout'
            
            if exit_pts is not None:
                pnl = exit_pts * dpp * 0.8  # Fixed position
                equity += pnl
                trades['anticipation'].append({'pnl': pnl, 'reason': reason, 'bars': anticipation_bars})
                anticipation_active = False
                anticipation_pos = 0
        
        if not anticipation_active and bars_since_spike > 0:
            # Check if we should anticipate a spike
            # Condition: 50+ bars since last spike → spike likely imminent
            if bars_since_spike >= 50:
                # Check if we're not in a cluster recovery period
                recent_spikes = sum(1 for s in spikes if i - s <= 10)
                if recent_spikes == 0:
                    # Spike anticipation: enter in DIRECTION of expected spike
                    anticipation_pos = 1 if is_boom else -1  # Boom goes UP, Crash goes DOWN
                    anticipation_entry = c
                    anticipation_bars = 0
                    anticipation_active = True
        
        # ── STRATEGY 2: BUILD-UP TRADING ──
        if buildup_active:
            buildup_bars += 1
            if buildup_pos == 1:
                hit_tp = (h - buildup_entry) >= tp
                hit_sl = (l - buildup_entry) <= -sl
            else:
                hit_tp = (buildup_entry - l) >= tp
                hit_sl = (buildup_entry - h) <= -sl
            
            exit_pts = None
            reason = None
            if hit_tp:
                exit_pts = tp; reason = 'tp'
            elif hit_sl:
                exit_pts = -sl; reason = 'sl'
            elif spike_happened:
                # Spike happened — ride it
                u = (c - buildup_entry) if buildup_pos == 1 else (buildup_entry - c)
                exit_pts = u; reason = 'spike_captured'
            elif buildup_bars >= 20:
                u = (c - buildup_entry) if buildup_pos == 1 else (buildup_entry - c)
                exit_pts = u; reason = 'timeout'
            
            if exit_pts is not None:
                pnl = exit_pts * dpp * 0.6
                equity += pnl
                trades['buildup'].append({'pnl': pnl, 'reason': reason, 'bars': buildup_bars})
                buildup_active = False
                buildup_pos = 0
        
        if not buildup_active and not anticipation_active:
            # Build-up: enter during quiet period, wait for spike
            if bars_since_spike >= 15 and bars_since_spike <= 40:
                # Quiet period — enter in direction of next spike
                recent_spikes = sum(1 for s in spikes if i - s <= 10)
                if recent_spikes == 0:
                    buildup_pos = 1 if is_boom else -1
                    buildup_entry = c
                    buildup_bars = 0
                    buildup_active = True
        
        # ── STRATEGY 3: COUNTER-DRIFT FADE ──
        if fade_pos != 0:
            fade_bars += 1
            if fade_pos == 1:
                hit_tp = (h - fade_entry) >= tp
                hit_sl = (l - fade_entry) <= -sl
            else:
                hit_tp = (fade_entry - l) >= tp
                hit_sl = (fade_entry - h) <= -sl
            
            spike_against = spike_happened and (
                (fade_pos == 1 and c < fade_entry) or (fade_pos == -1 and c > fade_entry))
            
            exit_pts = None
            reason = None
            if hit_tp:
                exit_pts = tp; reason = 'tp'
            elif hit_sl:
                exit_pts = -sl; reason = 'sl'
            elif spike_against:
                u = (c - fade_entry) if fade_pos == 1 else (fade_entry - c)
                exit_pts = u; reason = 'spike'
            elif fade_bars >= mh:
                u = (c - fade_entry) if fade_pos == 1 else (fade_entry - c)
                exit_pts = u; reason = 'timeout'
            
            if exit_pts is not None:
                pnl = exit_pts * dpp * 0.8
                equity += pnl
                trades['fade'].append({'pnl': pnl, 'reason': reason, 'bars': fade_bars})
                fade_pos = 0
                fade_bars = 0
        
        if fade_pos == 0 and not anticipation_active and not buildup_active:
            # Counter-drift fade: enter 2 bars after spike
            if bars_since_spike == 2 and last_spike_idx >= 0:
                # Skip clusters
                cluster_size = sum(1 for s in spikes if abs(s - i) <= 10)
                if cluster_size < 3:
                    fade_pos = -1 if is_boom else 1
                    fade_entry = c
                    fade_bars = 0
    
    return trades, equity


def print_results(symbol, trades, final_equity):
    """Print combined strategy results."""
    print(f'\n{"="*60}')
    print(f'  {symbol} — Combined Strategy Results')
    print(f'{"="*60}')
    
    total_pnl = 0
    total_trades = 0
    
    for name, label in [('anticipation', 'Spike Anticipation'), 
                         ('buildup', 'Build-Up Trading'),
                         ('fade', 'Counter-Drift Fade')]:
        t = trades[name]
        n = len(t)
        if n == 0:
            print(f'\n  {label}: No trades')
            continue
        
        pnls = [x['pnl'] for x in t]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        wr = len(wins)/n*100
        pnl = sum(pnls)
        avg = pnl/n
        
        reasons = {}
        for x in t:
            r = x['reason']
            if r not in reasons: reasons[r] = 0
            reasons[r] += 1
        
        print(f'\n  {label}:')
        print(f'    Trades: {n}  WR: {wr:.1f}%  PnL: ${pnl:+.2f}  Avg: ${avg:+.4f}')
        print(f'    Exit reasons: {reasons}')
        
        total_pnl += pnl
        total_trades += n
    
    print(f'\n  {"─"*50}')
    print(f'  COMBINED: {total_trades} trades  PnL: ${total_pnl:+.2f}')
    print(f'  Final equity: ${final_equity:.2f} (started $200)')
    print(f'  Return: {(final_equity/200-1)*100:+.1f}%')
    print(f'  {"="*60}')


if __name__ == '__main__':
    for sym in ['BOOM500', 'BOOM1000']:
        trades, equity = run_combined(sym)
        print_results(sym, trades, equity)
