"""
eval/optimized_strategy.py — Optimized strategy with ALL deep research findings.

Improvements:
1. TP=2 SL=1 (instead of TP=3 SL=1) — +9% EV
2. Time-of-day filter (BOOM500: skip 3am, 1pm)
3. Volatility filter (BOOM500: skip extreme vol)
4. Consecutive spike filter (BOOM500: skip 5+ consecutive)
"""

import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles, detect_order_blocks, SMCSpikeHunter, SMCHunterConfig

def bt_optimized(sym, risk_pct=0.02, tp=2.0, sl=1.0, use_time_filter=True,
                 use_vol_filter=True, use_consec_filter=True):
    dpp = 0.10 if '1000' in sym else 0.20
    is_boom = 'boom' in sym.lower()
    m1 = load_candles(sym, '1min')
    try:
        h1 = load_candles(sym, '60min')
        z = detect_order_blocks(h1, threshold_mult=2.0 if not is_boom else 2.5)
    except: z = []

    bars = np.array([[b['open'],b['high'],b['low'],b['close']] for b in m1], dtype=float)
    epochs = np.array([b['epoch'] for b in m1])
    c,h,l,o = bars[:,3],bars[:,1],bars[:,2],bars[:,0]
    rng=h-l; med=float(np.median(rng))
    sm=rng>3.0*med
    if is_boom: sm &= c>o
    else: sm &= c<o
    spikes=np.where(sm)[0]; ss=set(spikes.tolist())
    clust=set(); sa=np.array(spikes)
    for s in sa:
        if int(np.sum(np.abs(sa-s)<=10))-1>=2: clust.add(int(s))

    # Pre-compute volatility
    rets = np.zeros(len(bars))
    rets[1:] = np.diff(c) / np.maximum(c[:-1], 0.001)
    vol20 = np.full(len(bars), np.nan)
    for i in range(20, len(bars)):
        vol20[i] = np.std(rets[i-20:i])
    vol_med = float(np.nanmedian(vol20))

    # Pre-compute consecutive spike counts
    consec = np.zeros(len(bars), dtype=int)
    for s in spikes:
        count = 0
        for j in range(1, 21):
            if s-j >= 0 and (s-j) in ss:
                count += 1
            else:
                break
        consec[s] = count

    cfg=SMCHunterConfig(sl_pct=0.005,risk_per_trade=risk_pct*0.6,rsi_extreme_low=15,rsi_extreme_high=85)
    smc=SMCSpikeHunter(sym,config=cfg,initial_equity=200)
    smc.set_h1_zones(z); smc.precompute(m1)

    eq=200.0;pk=200.0;pnls=[];ls=-999;dp=0;de=0.0;dh=0;dps=0.0;dr=[];pend=None
    filtered = {"time":0, "vol":0, "consec":0, "cluster":0}

    for i,bar in enumerate(m1):
        bh,bl,bc,bo=bar['high'],bar['low'],bar['close'],bar['open']
        br=bh-bl;dr.append(br)
        if len(dr)>100:dr.pop(0)
        dm=float(np.median(dr))
        isp=br>3.0*dm
        if(is_boom and isp and bc>bo)or(not is_boom and isp and bc<bo):ls=i
        if pend and i>=pend['at']:
            dp=pend['d'];de=pend['p'];dh=0;dps=pend['s'];pend=None
        if dp!=0:
            dh+=1
            if dp==-1:ht=(de-bl)>=tp;hs=(de-bh)<=-sl;u=de-bc
            else:ht=(bh-de)>=tp;hs=(bl-de)<=-sl;u=bc-de
            ep=None;reason=None
            if ht:ep=tp;reason='tp'
            elif hs:ep=-sl;reason='sl'
            elif i in ss:ep=u;reason='spike'
            elif dh>=9:ep=u;reason='timeout'
            if ep is not None:pnl=ep*dpp*dps;eq+=pnl;pnls.append(pnl);dp=0;dh=0
        elif dp==0 and smc.position==0 and pend is None:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0:
                skip = False

                # Cluster filter
                if ls in clust:
                    skip = True; filtered["cluster"] += 1

                # Time filter (BOOM500: skip hours 3, 13)
                if use_time_filter and is_boom and not skip:
                    hour = (int(epochs[ls]) % 86400) // 3600
                    if hour in [3, 13]:
                        skip = True; filtered["time"] += 1

                # Volatility filter (BOOM500: skip extreme vol)
                if use_vol_filter and is_boom and not skip:
                    if not np.isnan(vol20[ls]):
                        if vol20[ls] > vol_med * 2.0:
                            skip = True; filtered["vol"] += 1

                # Consecutive spike filter (BOOM500: skip 5+ consecutive)
                if use_consec_filter and is_boom and not skip:
                    if consec[ls] >= 5:
                        skip = True; filtered["consec"] += 1

                if not skip:
                    dp=-1 if is_boom else 1;de=bc;dh=0
                    rd=200*risk_pct*0.4;dps=rd/(sl*dpp) if sl*dpp>0 else 0
                    pend={'d':dp,'p':bc,'s':dps,'at':i+1}

        smc.equity=200;r=smc.on_bar(bar,i)
        if r and r['action']=='close':eq+=r['pnl'];pnls.append(r['pnl'])
        pk=max(pk,eq)

    if smc.position!=0:
        smc._close_position(m1[-1]['close'],'end',m1[-1]['close'])
        if smc.trade_log:eq+=smc.trade_log[-1]['pnl'];pnls.append(smc.trade_log[-1]['pnl'])

    p=np.array(pnls);n=len(p);w=p[p>0];l2=p[p<=0]
    return dict(trades=n,wr=len(w)/n*100 if n else 0,pnl=float(np.sum(p)),
                pf=float(sum(w)/max(abs(sum(l2)),0.01)) if len(w) and len(l2) else 0,
                avg_w=float(np.mean(w)) if len(w) else 0,avg_l=float(np.mean(l2)) if len(l2) else 0,
                pnls=p, filtered=filtered)


def mc(pnls,months=12,n=100,init=400.0):
    np.random.seed(42)
    if len(pnls)==0:return np.array([init])
    wr=np.sum(pnls>0)/len(pnls)
    w=pnls[pnls>0];l=pnls[pnls<=0]
    aw=min(np.mean(w)/200,0.03) if len(w) else 0.001
    al=min(abs(np.mean(l))/200,0.03) if len(l) else 0.001
    tpm=len(pnls)/30;total=int(tpm*months)
    sims=np.full(n,init)
    for s in range(n):
        eq=init
        for _ in range(total):
            if np.random.random()<wr:eq*=(1+aw)
            else:eq*=(1-al)
            eq=max(eq,0)
        sims[s]=eq
    return sims


print("="*72)
print("  OPTIMIZED STRATEGY — All Deep Research Findings Applied")
print("="*72)

# Test different configs
configs = [
    ("TP=3 SL=1 (old)", 3.0, 1.0, False, False, False),
    ("TP=2 SL=1 (new)", 2.0, 1.0, False, False, False),
    ("TP=2 + filters", 2.0, 1.0, True, True, True),
]

for sym in ['BOOM500', 'BOOM1000']:
    print(f"\n  {sym}")
    print(f"  {'Config':>25} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>12} {'EV':>8}")
    print(f"  {'─'*70}")

    for label, tp, sl, time_f, vol_f, consec_f in configs:
        r = bt_optimized(sym, 0.02, tp, sl, time_f, vol_f, consec_f)
        ev = (r['wr']/100)*tp - (1-r['wr']/100)*sl if r['trades'] > 0 else 0
        f_str = ""
        if r['filtered']['time'] > 0: f_str += f"T={r['filtered']['time']} "
        if r['filtered']['vol'] > 0: f_str += f"V={r['filtered']['vol']} "
        if r['filtered']['consec'] > 0: f_str += f"C={r['filtered']['consec']} "
        print(f"  {label:>25} {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
              f"${r['pnl']:>+11.2f} {ev:>+7.3f}  {f_str}")

# Best config detailed
print(f"\n{'='*72}")
print(f"  BEST CONFIG: TP=2 SL=1 + All Filters")
print(f"{'='*72}")

r500 = bt_optimized('BOOM500', 0.02, 2.0, 1.0, True, True, True)
r1000 = bt_optimized('BOOM1000', 0.02, 2.0, 1.0, True, True, True)
comb = np.concatenate([r500['pnls'], r1000['pnls']])
wr_c = np.sum(comb>0)/len(comb)*100

print(f"\n  30-Day (FIXED $200, 2% risk):")
print(f"    BOOM500:  {r500['trades']}T  WR={r500['wr']:.1f}%  PF={r500['pf']:.2f}  PnL=${r500['pnl']:+.2f}")
print(f"    BOOM1000: {r1000['trades']}T  WR={r1000['wr']:.1f}%  PF={r1000['pf']:.2f}  PnL=${r1000['pnl']:+.2f}")
print(f"    COMBINED: {len(comb)}T  WR={wr_c:.1f}%  PnL=${np.sum(comb):+.2f}")
print(f"\n  Filters applied:")
print(f"    Time: {r500['filtered']['time']+r1000['filtered']['time']} trades skipped")
print(f"    Vol: {r500['filtered']['vol']+r1000['filtered']['vol']} trades skipped")
print(f"    Consec: {r500['filtered']['consec']+r1000['filtered']['consec']} trades skipped")
print(f"    Cluster: {r500['filtered']['cluster']+r1000['filtered']['cluster']} trades skipped")

# Monte Carlo
for risk in [0.01, 0.02]:
    s5=r500['pnls']*(risk/0.02);s10=r1000['pnls']*(risk/0.02)
    c=np.concatenate([s5,s10])
    sims=mc(c,12,50,400.0)
    print(f"\n  12-Month MC ({risk*100:.0f}% risk):")
    print(f"    Median: ${np.median(sims):.2f} ({(np.median(sims)/400-1)*100:+.1f}%)")
    print(f"    P5:     ${np.percentile(sims,5):.2f} ({(np.percentile(sims,5)/400-1)*100:+.1f}%)")
    print(f"    Profit: {(np.sum(sims>400)/len(sims)*100):.0f}%")

# Compare old vs new
print(f"\n{'='*72}")
print(f"  OLD (TP=3 SL=1) vs NEW (TP=2 SL=1 + filters)")
print(f"{'='*72}")

r_old_500 = bt_optimized('BOOM500', 0.02, 3.0, 1.0, False, False, False)
r_new_500 = bt_optimized('BOOM500', 0.02, 2.0, 1.0, True, True, True)
r_old_1000 = bt_optimized('BOOM1000', 0.02, 3.0, 1.0, False, False, False)
r_new_1000 = bt_optimized('BOOM1000', 0.02, 2.0, 1.0, True, True, True)

old_comb = np.concatenate([r_old_500['pnls'], r_old_1000['pnls']])
new_comb = np.concatenate([r_new_500['pnls'], r_new_1000['pnls']])

print(f"\n  {'':>15} {'OLD':>12} {'NEW':>12} {'Change':>12}")
print(f"  {'─'*55}")
print(f"  {'BOOM500 PnL':>15} ${r_old_500['pnl']:>+11.2f} ${r_new_500['pnl']:>+11.2f} "
      f"{(r_new_500['pnl']-r_old_500['pnl'])/abs(r_old_500['pnl'])*100:>+11.1f}%")
print(f"  {'BOOM500 WR':>15} {r_old_500['wr']:>11.1f}% {r_new_500['wr']:>11.1f}% "
      f"{r_new_500['wr']-r_old_500['wr']:>+11.1f}%")
print(f"  {'BOOM1000 PnL':>15} ${r_old_1000['pnl']:>+11.2f} ${r_new_1000['pnl']:>+11.2f} "
      f"{(r_new_1000['pnl']-r_old_1000['pnl'])/abs(r_old_1000['pnl'])*100:>+11.1f}%")
print(f"  {'BOOM1000 WR':>15} {r_old_1000['wr']:>11.1f}% {r_new_1000['wr']:>11.1f}% "
      f"{r_new_1000['wr']-r_old_1000['wr']:>+11.1f}%")
print(f"  {'Combined PnL':>15} ${np.sum(old_comb):>+11.2f} ${np.sum(new_comb):>+11.2f} "
      f"{(np.sum(new_comb)-np.sum(old_comb))/abs(np.sum(old_comb))*100:>+11.1f}%")

print(f"\n{'='*72}")
