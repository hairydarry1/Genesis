"""Classification filter backtest — two-pass approach."""
import sys, numpy as np, tempfile, shutil
from pathlib import Path
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles
from model.strategy_brain import StrategyBrain
from model.trade_thinker import MarketContext, TradeOutcome

def run_classify(symbol, max_bars=20000):
    """Pass 1: Run without filtering, collect classifications."""
    is_boom = 'boom' in symbol.lower()
    dpp = 0.10 if '1000' in symbol else 0.20
    m1 = load_candles(symbol, '1min')[-max_bars:]
    tp, sl, mh, st = 3.0, 1.0, 9, 3.0
    pos, ep, bh, ls = 0, 0.0, 0, -999
    ranges, spikes, clust = [], [], set()
    eq, ps, tid, trades = 200.0, 0.0, 0, []

    for i, bar in enumerate(m1):
        o,h,l,c = bar['open'],bar['high'],bar['low'],bar['close']
        br = h-l; ranges.append(br)
        if len(ranges)>100: ranges.pop(0)
        med = float(np.median(ranges))
        if br > st*med and c > o:
            ls = i; spikes.append(i)
            near = sum(1 for s in spikes if abs(s-i)<=10 and s!=i)
            if near >= 2:
                clust.add(i)
                for s in spikes:
                    if abs(s-i)<=10 and s!=i: clust.add(s)

        if pos != 0:
            bh += 1
            if pos == 1: ht=(h-ep)>=tp; hs=(l-ep)<=-sl; u=c-ep
            else: ht=(ep-l)>=tp; hs=(ep-h)<=-sl; u=ep-c
            sa = i in spikes and ((pos==1 and c<ep) or (pos==-1 and c>ep))
            x,reason = None,None
            if ht: x,reason=tp,'tp'
            elif hs: x,reason=-sl,'sl'
            elif sa: x,reason=u,'spike'
            elif bh>=mh: x,reason=u,'timeout'
            if x is not None:
                pnl=x*dpp*ps; eq+=pnl; tid+=1
                # Classify
                cl_size = sum(1 for s in spikes if abs(s-i)<=10)
                is_algo = (br/med if med>0 else 0) > 15
                is_late = (i-ls if ls>=0 else 999) <= 4 and cl_size >= 2
                is_cluster = cl_size >= 3
                
                if is_late: cat = 'late_entry'
                elif is_cluster: cat = 'cluster_continuation'
                elif is_algo and pnl <= 0: cat = 'algo_spike_trap'
                elif pnl > 0 and cl_size <= 1: cat = 'good_fade'
                elif pnl > 0: cat = 'noise_fade_win'
                else: cat = 'other_loss'
                
                trades.append({'pnl':pnl,'cat':cat,'bars_since_spike':i-ls if ls>=0 else 999,
                    'cluster_size':cl_size,'is_algo':is_algo,'spike_size':br/med if med>0 else 0})
                pos,bh=0,0
                continue

        if pos==0:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0 and ls not in clust:
                rd=eq*0.008; ps=rd/(sl*dpp) if sl*dpp>0 else 0
                pos=-1; ep=c; bh=0
    return trades, m1

def build_filters(trades):
    """Analyze classifications and build optimal filters."""
    cats = {}
    for t in trades:
        c = t['cat']
        if c not in cats: cats[c]={'n':0,'w':0,'pnl':0}
        cats[c]['n']+=1
        if t['pnl']>0: cats[c]['w']+=1
        cats[c]['pnl']+=t['pnl']
    
    # Build filter rules from losing categories
    filters = []
    for cat, s in cats.items():
        if s['n'] == 0: continue
        wr = s['w']/s['n']*100
        if wr < 20 and s['n'] >= 3:  # Low WR, enough samples
            filters.append({'category': cat, 'trades': s['n'], 'wr': wr, 'pnl': s['pnl']})
    
    return cats, filters

def run_filtered(symbol, filters, max_bars=20000):
    """Pass 2: Run with learned filters applied."""
    is_boom = 'boom' in symbol.lower()
    dpp = 0.10 if '1000' in symbol else 0.20
    m1 = load_candles(symbol, '1min')[-max_bars:]
    tp, sl, mh, st = 3.0, 1.0, 9, 3.0
    pos, ep, bh, ls = 0, 0.0, 0, -999
    ranges, spikes, clust = [], [], set()
    eq, ps, tid, trades, skipped = 200.0, 0.0, 0, [], 0

    for i, bar in enumerate(m1):
        o,h,l,c = bar['open'],bar['high'],bar['low'],bar['close']
        br = h-l; ranges.append(br)
        if len(ranges)>100: ranges.pop(0)
        med = float(np.median(ranges))
        if br > st*med and c > o:
            ls = i; spikes.append(i)
            near = sum(1 for s in spikes if abs(s-i)<=10 and s!=i)
            if near >= 2:
                clust.add(i)
                for s in spikes:
                    if abs(s-i)<=10 and s!=i: clust.add(s)

        if pos != 0:
            bh += 1
            if pos == 1: ht=(h-ep)>=tp; hs=(l-ep)<=-sl; u=c-ep
            else: ht=(ep-l)>=tp; hs=(ep-h)<=-sl; u=ep-c
            sa = i in spikes and ((pos==1 and c<ep) or (pos==-1 and c>ep))
            x,reason = None,None
            if ht: x,reason=tp,'tp'
            elif hs: x,reason=-sl,'sl'
            elif sa: x,reason=u,'spike'
            elif bh>=mh: x,reason=u,'timeout'
            if x is not None:
                pnl=x*dpp*ps; eq+=pnl; tid+=1
                trades.append({'pnl':pnl})
                pos,bh=0,0
                continue

        if pos==0:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0 and ls not in clust:
                # Apply learned filters
                cl_size = sum(1 for s in spikes if abs(s-i)<=10)
                is_algo = (br/med if med>0 else 0) > 15
                is_late = bss <= 4 and cl_size >= 2
                is_cluster = cl_size >= 3

                skip = False
                skip_reason = ''
                for f in filters:
                    if f['category'] == 'late_entry' and is_late:
                        skip, skip_reason = True, 'late_entry'
                    elif f['category'] == 'cluster_continuation' and is_cluster:
                        skip, skip_reason = True, 'cluster'
                    elif f['category'] == 'algo_spike_trap' and is_algo:
                        skip, skip_reason = True, 'algo_spike'
                
                if skip:
                    skipped += 1
                    continue

                rd=eq*0.008; ps=rd/(sl*dpp) if sl*dpp>0 else 0
                pos=-1; ep=c; bh=0
    return trades, skipped

# ── Run ──
for sym in ['BOOM500', 'BOOM1000']:
    print(f'\n{"="*60}')
    print(f'  {sym} — Classification Filter Results')
    print(f'{"="*60}')
    
    # Pass 1: classify
    trades_raw, m1 = run_classify(sym)
    cats, filters = build_filters(trades_raw)
    
    n = len(trades_raw)
    wr_raw = sum(1 for t in trades_raw if t['pnl']>0)/n*100
    
    print(f'\n  Pass 1 (no filter): {n}T  WR={wr_raw:.1f}%')
    print(f'\n  Loss categories found:')
    for c, s in sorted(cats.items(), key=lambda x: -x[1]['n']):
        wr2 = s['w']/s['n']*100 if s['n']>0 else 0
        emoji = '✅' if wr2>70 else '⚠️' if wr2>40 else '❌'
        print(f'    {emoji} {c:30s}: {s["n"]:4d}T  WR={wr2:>5.1f}%  PnL=${s["pnl"]:>+10.2f}')
    
    print(f'\n  Filters to apply (WR < 20%, 3+ trades):')
    for f in filters:
        print(f'    SKIP: {f["category"]:30s} ({f["trades"]}T, WR={f["wr"]:.0f}%, PnL=${f["pnl"]:+.2f})')
    
    if not filters:
        print(f'    (no filters needed — all categories have acceptable WR)')
        continue
    
    # Pass 2: apply filters
    trades_filt, skipped = run_filtered(sym, filters)
    nf = len(trades_filt)
    wr_filt = sum(1 for t in trades_filt if t['pnl']>0)/nf*100 if nf > 0 else 0
    
    print(f'\n  Pass 2 (with filters): {nf}T  WR={wr_filt:.1f}%  (skipped {skipped})')
    print(f'\n  ─── Improvement ───')
    print(f'  Trades: {n} → {nf} ({nf-n:+d})')
    print(f'  WR:     {wr_raw:.1f}% → {wr_filt:.1f}% ({wr_filt-wr_raw:+.1f}%)')
    
    # PnL comparison
    pnl_raw = sum(t['pnl'] for t in trades_raw)
    pnl_filt = sum(t['pnl'] for t in trades_filt)
    pnl_lost = sum(t['pnl'] for t in trades_raw if t['cat'] in [f['category'] for f in filters])
    print(f'  PnL:    ${pnl_raw:+.2f} → ${pnl_filt:+.2f}')
    print(f'  Losses avoided: ${-pnl_lost:+.2f}')
    
    # Show what was filtered
    filtered_out = [t for t in trades_raw if t['cat'] in [f['category'] for f in filters]]
    if filtered_out:
        filt_wr = sum(1 for t in filtered_out if t['pnl']>0)/len(filtered_out)*100
        filt_pnl = sum(t['pnl'] for t in filtered_out)
        print(f'\n  Filtered trades: {len(filtered_out)}T  WR={filt_wr:.1f}%  PnL=${filt_pnl:+.2f}')
        print(f'  These were the WORST trades — removing them improves everything.')
    
    print(f'  {"="*60}')
