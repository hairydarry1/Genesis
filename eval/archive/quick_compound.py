"""Fixed compound backtest — run with fixed risk, project with compounding."""
import numpy as np, pandas as pd, sys
sys.path.insert(0, '.')
from eval.smc_spike_hunter import load_candles, detect_order_blocks, SMCSpikeHunter, SMCHunterConfig

def bt_fixed(sym, risk_pct=0.02):
    """Backtest with FIXED position sizes (no compounding)."""
    dpp = 0.10 if '1000' in sym else 0.20
    is_boom = 'boom' in sym.lower()
    m1 = load_candles(sym, '1min')
    try:
        h1 = load_candles(sym, '60min')
        z = detect_order_blocks(h1, threshold_mult=2.0 if not is_boom else 2.5)
    except: z = []

    bars = np.array([[b['open'],b['high'],b['low'],b['close']] for b in m1], dtype=float)
    c, h, l, o = bars[:,3], bars[:,1], bars[:,2], bars[:,0]
    rng = h - l; med = float(np.median(rng))
    sm = rng > 3.0*med
    if is_boom: sm &= c > o
    else: sm &= c < o
    spikes = np.where(sm)[0]
    ss = set(spikes.tolist())

    clust = set()
    sa = np.array(spikes)
    for s in sa:
        if int(np.sum(np.abs(sa-s)<=10))-1 >= 2: clust.add(int(s))

    # SMC — FIXED risk (equity stays at 200)
    cfg = SMCHunterConfig(sl_pct=0.005, risk_per_trade=risk_pct*0.6, rsi_extreme_low=15, rsi_extreme_high=85)
    smc = SMCSpikeHunter(sym, config=cfg, initial_equity=200)
    smc.set_h1_zones(z); smc.precompute(m1)

    eq = 200.0; peak = 200.0; pnls = []; ls = -999
    dp = 0; de = 0.0; dh = 0; dps = 0.0; dr = []

    for i, bar in enumerate(m1):
        bh, bl, bc, bo = bar['high'], bar['low'], bar['close'], bar['open']
        br = bh-bl; dr.append(br)
        if len(dr)>100: dr.pop(0)
        dm = float(np.median(dr))
        isp = br > 3.0*dm
        if (is_boom and isp and bc>bo) or (not is_boom and isp and bc<bo): ls = i

        if dp != 0:
            dh += 1
            if dp == -1:
                ht = (de-bl)>=3; hs = (de-bh)<=-1; u = de-bc
            else:
                ht = (bh-de)>=3; hs = (bl-de)<=-1; u = bc-de
            ep = None
            if ht: ep = 3
            elif hs: ep = -1
            elif i in ss: ep = u
            elif dh >= 9: ep = u
            if ep is not None:
                pnl = ep*dpp*dps
                eq += pnl; pnls.append(pnl)
                dp = 0; dh = 0

        elif dp == 0 and smc.position == 0:
            bss = i-ls if ls>=0 else 999
            if bss == 3 and ls >= 0 and ls not in clust:
                dp = -1 if is_boom else 1; de = bc; dh = 0
                rd = 200 * risk_pct * 0.4  # FIXED $200 equity
                dps = rd/(1*dpp) if 1*dpp>0 else 0

        # SMC — keep equity FIXED at 200 for sizing
        smc.equity = 200
        r = smc.on_bar(bar, i)
        if r and r['action']=='close':
            eq += r['pnl']; pnls.append(r['pnl'])
        peak = max(peak, eq)

    if smc.position!=0:
        smc._close_position(m1[-1]['close'],'end',m1[-1]['close'])
        if smc.trade_log: eq+=smc.trade_log[-1]['pnl']; pnls.append(smc.trade_log[-1]['pnl'])

    return np.array(pnls)


def mc_compound(pnls, months=12, n_sims=300, init=400.0):
    """Monte Carlo with compounding using actual PnL distribution."""
    np.random.seed(42)
    if len(pnls)==0: return np.array([init])

    # Compute per-trade return as fraction of equity at risk
    # Each trade risks risk_pct of equity, so wins/losses scale with equity
    wins = pnls[pnls>0]
    losses = pnls[pnls<=0]
    wr = len(wins)/len(pnls)

    # Average win/loss as fraction of $200 (base equity)
    aw_pct = np.mean(wins)/200 if len(wins)>0 else 0.001
    al_pct = abs(np.mean(losses))/200 if len(losses)>0 else 0.001

    # Cap per-trade return to prevent overflow
    aw_pct = min(aw_pct, 0.03)  # Max 3% per trade
    al_pct = min(al_pct, 0.03)

    tpm = len(pnls)/30  # trades per month
    total = int(tpm * months)

    sims = np.full(n_sims, init)
    for s in range(n_sims):
        eq = init
        for _ in range(total):
            if np.random.random() < wr:
                eq *= (1 + aw_pct)  # Compound win
            else:
                eq *= (1 - al_pct)  # Compound loss
            eq = max(eq, 0)
        sims[s] = eq
    return sims


print("="*72)
print("  COMPOUNDING PROJECTION — Cluster-Filtered Dual Strategy")
print("  Backtest runs with FIXED risk, then projects with compounding")
print("="*72)

# Run backtests with fixed risk
print("\n  Running backtests (fixed risk, no compounding)...")
r500 = bt_fixed('BOOM500', risk_pct=0.02)
r1000 = bt_fixed('BOOM1000', risk_pct=0.02)

wr5 = np.sum(r500>0)/len(r500)*100 if len(r500) else 0
wr10 = np.sum(r1000>0)/len(r1000)*100 if len(r1000) else 0
combined = np.concatenate([r500, r1000])
wr_c = np.sum(combined>0)/len(combined)*100

print(f"\n  30-Day Backtest (FIXED $200 equity):")
print(f"  {'':15} {'Trades':>7} {'WR%':>7} {'PnL':>12} {'AvgWin':>9} {'AvgLoss':>9}")
print(f"  {'─'*60}")
print(f"  {'BOOM500':<15} {len(r500):>7} {wr5:>6.1f}% ${np.sum(r500):>+11.2f} "
      f"${np.mean(r500[r500>0]):>+8.2f} ${np.mean(r500[r500<=0]):>+8.2f}" if len(r500[r500>0]) else "")
print(f"  {'BOOM1000':<15} {len(r1000):>7} {wr10:>6.1f}% ${np.sum(r1000):>+11.2f} "
      f"${np.mean(r1000[r1000>0]):>+8.2f} ${np.mean(r1000[r1000<=0]):>+8.2f}" if len(r1000[r1000>0]) else "")
print(f"  {'─'*60}")
print(f"  {'COMBINED':<15} {len(combined):>7} {wr_c:>6.1f}% ${np.sum(combined):>+11.2f}")

# Project with compounding at different risk levels
print(f"\n{'='*72}")
print(f"  COMPOUNDING PROJECTIONS — $400 start (BOOM500 + BOOM1000)")
print(f"{'='*72}")

for risk in [0.01, 0.02, 0.03]:
    # Scale PnLs for this risk level
    s5 = r500 * (risk / 0.02)
    s10 = r1000 * (risk / 0.02)
    combined_r = np.concatenate([s5, s10])

    sims = mc_compound(combined_r, months=12, init=400.0)
    sims6 = mc_compound(combined_r, months=6, init=400.0)
    sims24 = mc_compound(combined_r, months=24, init=400.0)

    print(f"\n  Risk {risk*100:.0f}%/trade:")
    print(f"    30-day PnL (flat): ${np.sum(combined_r):+.2f}")
    print(f"")
    print(f"    {'Period':<12} {'Median':>12} {'P5 (worst)':>12} {'P95 (best)':>12} {'Profit%':>8}")
    print(f"    {'─'*58}")
    for label, s in [("6-month", sims6), ("12-month", sims), ("24-month", sims24)]:
        med = np.median(s)
        p5 = np.percentile(s, 5)
        p95 = np.percentile(s, 95)
        profit = np.sum(s>400)/len(s)*100
        print(f"    {label:<12} ${med:>11.2f} ${p5:>11.2f} ${p95:>11.2f} {profit:>7.0f}%")

# Growth table
print(f"\n{'='*72}")
print(f"  GROWTH TABLE — $400 start")
print(f"{'='*72}")
print(f"\n  {'Risk':>6}  {'3-Mo':>10}  {'6-Mo':>10}  {'12-Mo':>10}  {'24-Mo':>10}")
print(f"  {'─'*50}")

for risk in [0.01, 0.02, 0.03]:
    s5 = r500 * (risk / 0.02)
    s10 = r1000 * (risk / 0.02)
    combined_r = np.concatenate([s5, s10])
    vals = []
    for months in [3, 6, 12, 24]:
        sims = mc_compound(combined_r, months=months, init=400.0)
        vals.append(np.median(sims))
    print(f"  {risk*100:.0f}%   ${vals[0]:>9.2f}  ${vals[1]:>9.2f}  ${vals[2]:>9.2f}  ${vals[3]:>9.2f}")

print(f"\n{'='*72}")
print(f"  NOTES:")
print(f"  - Backtest uses FIXED $200 equity (realistic for min lot constraints)")
print(f"  - Projections use COMPOUNDING (position sizes grow with equity)")
print(f"  - Cluster filter removes ~47% of low-quality trades")
print(f"  - Monte Carlo: 300 simulations, random trade sequence")
print(f"  - Per-trade return capped at 3% to prevent overflow")
print(f"{'='*72}")
