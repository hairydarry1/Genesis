"""TP/SL optimization — find optimal TP/SL for drift fade."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles, detect_order_blocks, SMCSpikeHunter, SMCHunterConfig

def bt(sym, risk_pct=0.02, tp_val=3.0, sl_val=1.0):
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
    clust=set(); sa=np.array(spikes)
    for s in sa:
        if int(np.sum(np.abs(sa-s)<=10))-1>=2: clust.add(int(s))
    cfg=SMCHunterConfig(sl_pct=0.005,risk_per_trade=risk_pct*0.6,rsi_extreme_low=15,rsi_extreme_high=85)
    smc=SMCSpikeHunter(sym,config=cfg,initial_equity=200)
    smc.set_h1_zones(z); smc.precompute(m1)
    eq=200.0;pk=200.0;pnls=[];ls=-999;dp=0;de=0.0;dh=0;dps=0.0;dr=[];pend=None
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
            if dp==-1:ht=(de-bl)>=tp_val;hs=(de-bh)<=-sl_val;u=de-bc
            else:ht=(bh-de)>=tp_val;hs=(bl-de)<=-sl_val;u=bc-de
            ep=None;reason=None
            if ht:ep=tp_val;reason='tp'
            elif hs:ep=-sl_val;reason='sl'
            elif i in ss:ep=u;reason='spike'
            elif dh>=9:ep=u;reason='timeout'
            if ep is not None:pnl=ep*dpp*dps;eq+=pnl;pnls.append(pnl);dp=0;dh=0
        elif dp==0 and smc.position==0 and pend is None:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0 and ls not in clust:
                rd=200*risk_pct*0.4;ps=rd/(sl_val*dpp) if sl_val*dpp>0 else 0
                pend={'d':-1 if is_boom else 1,'p':bc,'s':ps,'at':i+1}
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


print("="*72)
print("  TP/SL OPTIMIZATION — 60-Day Data")
print("="*72)

for sym in ['BOOM500','BOOM1000']:
    print(f"\n  {sym}")
    print(f"  {'Config':>20} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>12} {'AvgW':>8} {'AvgL':>8}")
    print(f"  {'─'*70}")

    for tp,sl in [(3.0,2.0),(3.0,1.0),(2.0,1.0)]:
        r=bt(sym,0.02,tp,sl)
        print(f"  TP={tp:.1f} SL={sl:.1f}    {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
              f"${r['pnl']:>+11.2f} ${r['avg_w']:>+7.2f} ${r['avg_l']:>+7.2f}")

# Best config detailed
print(f"\n{'='*72}")
print(f"  OPTIMAL CONFIG: TP=3.0 SL=1.0")
print(f"{'='*72}")

r500=bt('BOOM500',0.02,3.0,1.0)
r1000=bt('BOOM1000',0.02,3.0,1.0)
comb=np.concatenate([r500['pnls'],r1000['pnls']])
wr_c=np.sum(comb>0)/len(comb)*100

print(f"\n  30-Day (FIXED $200, 2% risk):")
print(f"    BOOM500:  {r500['trades']}T  WR={r500['wr']:.1f}%  PF={r500['pf']:.2f}  PnL=${r500['pnl']:+.2f}")
print(f"    BOOM1000: {r1000['trades']}T  WR={r1000['wr']:.1f}%  PF={r1000['pf']:.2f}  PnL=${r1000['pnl']:+.2f}")
print(f"    COMBINED: {len(comb)}T  WR={wr_c:.1f}%  PnL=${np.sum(comb):+.2f}")

# Monte Carlo
for risk in [0.01,0.02]:
    s5=r500['pnls']*(risk/0.02);s10=r1000['pnls']*(risk/0.02)
    c=np.concatenate([s5,s10])
    sims=mc(c,12,100,400.0)
    print(f"\n  12-Month MC ({risk*100:.0f}% risk):")
    print(f"    Median: ${np.median(sims):.2f} ({(np.median(sims)/400-1)*100:+.1f}%)")
    print(f"    P5:     ${np.percentile(sims,5):.2f} ({(np.percentile(sims,5)/400-1)*100:+.1f}%)")
    print(f"    Profit: {(np.sum(sims>400)/len(sims)*100):.0f}%")

# Compare old vs new
print(f"\n{'='*72}")
print(f"  OLD vs NEW CONFIG")
print(f"{'='*72}")

for sym in ['BOOM500','BOOM1000']:
    r_old=bt(sym,0.02,3.0,2.0)
    r_new=bt(sym,0.02,3.0,1.0)
    imp = (r_new['pnl']-r_old['pnl'])/abs(r_old['pnl'])*100 if r_old['pnl']!=0 else 0
    print(f"\n  {sym}:")
    print(f"    OLD (TP=3 SL=2): {r_old['trades']}T WR={r_old['wr']:.1f}% PF={r_old['pf']:.2f} PnL=${r_old['pnl']:+.2f}")
    print(f"    NEW (TP=3 SL=1): {r_new['trades']}T WR={r_new['wr']:.1f}% PF={r_new['pf']:.2f} PnL=${r_new['pnl']:+.2f}")
    print(f"    Improvement:      {imp:+.1f}%")

print(f"\n{'='*72}")
