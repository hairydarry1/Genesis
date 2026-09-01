"""Optimized stress test — key slippage levels only."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles, detect_order_blocks, SMCSpikeHunter, SMCHunterConfig

def bt(sym, risk_pct=0.02, slip=1.0):
    dpp = 0.10 if '1000' in sym else 0.20
    is_boom = 'boom' in sym.lower()
    m1 = load_candles(sym, '1min')
    try:
        h1 = load_candles(sym, '60min')
        z = detect_order_blocks(h1, threshold_mult=2.0 if not is_boom else 2.5)
    except: z = []
    bars = np.array([[b['open'],b['high'],b['low'],b['close']] for b in m1], dtype=float)
    c,h,l,o = bars[:,3],bars[:,1],bars[:,2],bars[:,0]
    rng=h-l; med=float(np.median(rng))
    sm=rng>3.0*med
    if is_boom: sm &= c>o
    else: sm &= c<o
    spikes=np.where(sm)[0]; ss=set(spikes.tolist())
    clust=set()
    sa=np.array(spikes)
    for s in sa:
        if int(np.sum(np.abs(sa-s)<=10))-1>=2: clust.add(int(s))
    sbar=set(spikes.tolist())
    cfg=SMCHunterConfig(sl_pct=0.005,risk_per_trade=risk_pct*0.6,rsi_extreme_low=15,rsi_extreme_high=85)
    smc=SMCSpikeHunter(sym,config=cfg,initial_equity=200)
    smc.set_h1_zones(z); smc.precompute(m1)
    eq=200.0;pk=200.0;pnls=[];ls=-999;dp=0;de=0.0;dh=0;dps=0.0;dr=[];pend=None
    es=0.00011*slip; xsp=0.00005*slip
    delay=1 if slip<=2 else(2 if slip<=5 else 3)
    for i,bar in enumerate(m1):
        bh,bl,bc,bo=bar['high'],bar['low'],bar['close'],bar['open']
        br=bh-bl;dr.append(br)
        if len(dr)>100:dr.pop(0)
        dm=float(np.median(dr))
        isp=br>3.0*dm
        if(is_boom and isp and bc>bo)or(not is_boom and isp and bc<bo):ls=i
        if pend and i>=pend['at']:
            ep=pend['p'];sl=ep*es
            if i in sbar:sl*=3.0
            if pend['d']==-1:ep+=sl
            else:ep-=sl
            dp=pend['d'];de=ep;dh=0;dps=pend['s'];pend=None
        if dp!=0:
            dh+=1
            if dp==-1:ht=(de-bl)>=3;hs=(de-bh)<=-1;u=de-bc
            else:ht=(bh-de)>=3;hs=(bl-de)<=-1;u=bc-de
            ep2=None;reason=None
            if ht:ep2=3;reason='tp'
            elif hs:ep2=-2;reason='sl'
            elif i in ss:ep2=u;reason='spike'
            elif dh>=9:ep2=u;reason='timeout'
            if ep2 is not None:
                if reason!='sl':ep2-=abs(ep2)*xsp
                pnl=ep2*dpp*dps;eq+=pnl;pnls.append(pnl);dp=0;dh=0
        elif dp==0 and smc.position==0 and pend is None:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0 and ls not in clust:
                rd=200*risk_pct*0.4;ps=rd/(2*dpp) if 2*dpp>0 else 0
                pend={'d':-1 if is_boom else 1,'p':bc,'s':ps,'at':i+delay}
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
                pnls=p)

def mc(pnls,months=12,n=200,init=400.0):
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

print("="*78)
print("  STRESS TEST — Slippage Sensitivity (60-day data)")
print("="*78)

for sym in ['BOOM500','BOOM1000']:
    print(f"\n  {sym}")
    print(f"  {'Mult':>5} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>12} {'AvgW':>8} {'AvgL':>8}")
    print(f"  {'─'*58}")
    for m in [1.0, 2.0, 5.0, 10.0]:
        r=bt(sym,0.02,m)
        print(f"  {m:>4.1f}x {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
              f"${r['pnl']:>+11.2f} ${r['avg_w']:>+7.2f} ${r['avg_l']:>+7.2f}")

print(f"\n{'='*78}")
print(f"  COMBINED PORTFOLIO — Stress Test ($400, 2% risk)")
print(f"{'='*78}")
print(f"\n  {'Mult':>5} {'30d PnL':>12} {'12-Mo Med':>14} {'12-Mo P5':>14} {'Profit%':>8}")
print(f"  {'─'*58}")
for m in [1.0, 2.0, 5.0, 10.0]:
    r5=bt('BOOM500',0.02,m);r10=bt('BOOM1000',0.02,m)
    c=np.concatenate([r5['pnls'],r10['pnls']])
    s=mc(c,12,200,400.0)
    print(f"  {m:>4.1f}x ${np.sum(c):>+11.2f} ${np.median(s):>13.2f} "
          f"${np.percentile(s,5):>13.2f} {(np.sum(s>400)/len(s)*100):>7.0f}%")

print(f"\n  WORST CASE — 5x slippage, 1% risk, $400:")
r5=bt('BOOM500',0.01,5.0);r10=bt('BOOM1000',0.01,5.0)
c=np.concatenate([r5['pnls'],r10['pnls']])
for mo in [6,12,24]:
    s=mc(c,mo,200,400.0)
    print(f"    {mo:>2}-mo: Med=${np.median(s):.2f} ({(np.median(s)/400-1)*100:+.1f}%) "
          f"P5=${np.percentile(s,5):.2f} Profit={(np.sum(s>400)/len(s)*100):.0f}%")

print(f"\n  Growth Table (5x slippage, compounding):")
print(f"  {'Risk':>6} {'3-Mo':>10} {'6-Mo':>10} {'12-Mo':>10} {'24-Mo':>10}")
print(f"  {'─'*50}")
for risk in [0.01,0.02]:
    r5=bt('BOOM500',risk,5.0);r10=bt('BOOM1000',risk,5.0)
    c=np.concatenate([r5['pnls'],r10['pnls']])
    vals=[]
    for mo in [3,6,12,24]:
        s=mc(c,mo,200,400.0);vals.append(np.median(s))
    print(f"  {risk*100:.0f}%   ${vals[0]:>9.2f}  ${vals[1]:>9.2f}  ${vals[2]:>9.2f}  ${vals[3]:>9.2f}")

print(f"\n{'='*78}")
print(f"  1x=Normal  2x=Moderate  5x=Severe  10x=Extreme")
print(f"{'='*78}")
