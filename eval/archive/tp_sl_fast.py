"""Fast TP/SL optimization — sample 10k bars for speed."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles

def bt_drift(sym, tp_val=3.0, sl_val=1.0, n_bars=10000):
    dpp = 0.10 if '1000' in sym else 0.20
    is_boom = 'boom' in sym.lower()
    m1 = load_candles(sym, '1min')
    # Use last n_bars for speed
    m1 = m1[-n_bars:]
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

    pnls=[];ls=-999;dp=0;de=0.0;dh=0;dps=0.0;dr=[]
    for i in range(len(bars)):
        bh,bl,bc,bo=h[i],l[i],c[i],o[i]
        br=bh-bl;dr.append(br)
        if len(dr)>100:dr.pop(0)
        dm=float(np.median(dr))
        isp=br>3.0*dm
        if(is_boom and isp and bc>bo)or(not is_boom and isp and bc<bo):ls=i
        if dp!=0:
            dh+=1
            if dp==-1:ht=(de-bl)>=tp_val;hs=(de-bh)<=-sl_val;u=de-bc
            else:ht=(bh-de)>=tp_val;hs=(bl-de)<=-sl_val;u=bc-de
            ep=None
            if ht:ep=tp_val
            elif hs:ep=-sl_val
            elif i in ss:ep=u
            elif dh>=9:ep=u
            if ep is not None:pnl=ep*dpp*dps;pnls.append(pnl);dp=0;dh=0
        elif dp==0:
            bss=i-ls if ls>=0 else 999
            if bss==3 and ls>=0 and ls not in clust:
                dp=-1 if is_boom else 1;de=bc;dh=0
                rd=200*0.02*0.4;dps=rd/(sl_val*dpp) if sl_val*dpp>0 else 0
    p=np.array(pnls);n=len(p);w=p[p>0];l2=p[p<=0]
    return dict(trades=n,wr=len(w)/n*100 if n else 0,pnl=float(np.sum(p)),
                pf=float(sum(w)/max(abs(sum(l2)),0.01)) if len(w) and len(l2) else 0,
                avg_w=float(np.mean(w)) if len(w) else 0,avg_l=float(np.mean(l2)) if len(l2) else 0,
                pnls=p)

print("="*72)
print("  TP/SL OPTIMIZATION — Drift Fade (10k bar sample)")
print("="*72)

for sym in ['BOOM500','BOOM1000']:
    print(f"\n  {sym}")
    print(f"  {'TP':>5} {'SL':>5} {'Trades':>7} {'WR%':>7} {'PF':>6} {'PnL':>10} {'EV':>8}")
    print(f"  {'─'*52}")

    best_ev = -999
    for tp in [2.0, 3.0]:
        for sl in [0.5, 1.0, 2.0]:
            r=bt_drift(sym,tp,sl)
            if r['trades']==0: continue
            ev = (r['wr']/100)*tp - (1-r['wr']/100)*sl
            marker = " *" if ev > best_ev else ""
            if ev > best_ev: best_ev = ev; best_tp=tp; best_sl=sl
            print(f"  {tp:>5.1f} {sl:>5.1f} {r['trades']:>7} {r['wr']:>6.1f}% {r['pf']:>5.2f} "
                  f"${r['pnl']:>+9.2f} {ev:>+7.3f}{marker}")

    print(f"\n  Best: TP={best_tp} SL={best_sl}")

# Full 60-day comparison with best configs
print(f"\n{'='*72}")
print(f"  FULL 60-DAY COMPARISON")
print(f"{'='*72}")

for sym in ['BOOM500','BOOM1000']:
    r_old=bt_drift(sym,3.0,2.0)
    r_new=bt_drift(sym,3.0,1.0)
    r_best=bt_drift(sym,3.0,0.5)
    imp = (r_new['pnl']-r_old['pnl'])/abs(r_old['pnl'])*100 if r_old['pnl']!=0 else 0
    print(f"\n  {sym}:")
    print(f"    TP=3 SL=2: {r_old['trades']:>5}T WR={r_old['wr']:>5.1f}% PF={r_old['pf']:>5.2f} PnL=${r_old['pnl']:>+10.2f}")
    print(f"    TP=3 SL=1: {r_new['trades']:>5}T WR={r_new['wr']:>5.1f}% PF={r_new['pf']:>5.2f} PnL=${r_new['pnl']:>+10.2f} ({imp:+.1f}%)")
    print(f"    TP=3 SL=0.5: {r_best['trades']:>4}T WR={r_best['wr']:>5.1f}% PF={r_best['pf']:>5.2f} PnL=${r_best['pnl']:>+10.2f}")

print(f"\n{'='*72}")
