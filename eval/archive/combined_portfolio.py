"""Combined Portfolio: SMC Spike Hunter + Drift Fade running together.

Both strategies trade independently on the same account.
SMC captures zone-level entries (high R:R, lower frequency).
Drift Fade captures counter-drift entries (high WR, high frequency).
Diversified edges reduce overall risk.
"""
import sys
sys.path.insert(0, '.')
import numpy as np
import pandas as pd


def detect_spikes(bars_arr, direction, threshold=3.0):
    ranges = bars_arr[:, 1] - bars_arr[:, 2]
    med = np.median(ranges)
    spikes = []
    for i in range(len(bars_arr)):
        if ranges[i] > threshold * med:
            if direction == 'up' and bars_arr[i, 3] > bars_arr[i, 0]:
                spikes.append(i)
            elif direction == 'down' and bars_arr[i, 3] < bars_arr[i, 0]:
                spikes.append(i)
    return np.array(spikes)


def backtest_drift(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction, dpp):
    """Drift Fade backtest."""
    n = len(bars_arr)
    spike_set = set(spikes.tolist())
    last_spike = -1
    in_trade = False
    entry_price = 0
    entry_idx = 0
    pnls = []
    
    for i in range(n):
        if i in spike_set:
            last_spike = i
        bars_since = i - last_spike if last_spike >= 0 else 999
        
        if in_trade:
            h, l, c = bars_arr[i, 1], bars_arr[i, 2], bars_arr[i, 3]
            bars_held = i - entry_idx
            
            if direction == 'long':
                hit_tp = (h - entry_price) >= tp
                hit_sl = (l - entry_price) <= -sl
                unrealized = c - entry_price
            else:
                hit_tp = (entry_price - l) >= tp
                hit_sl = (entry_price - h) <= -sl
                unrealized = entry_price - c
            
            exit_pts = None
            if hit_tp:
                exit_pts = tp
            elif hit_sl:
                exit_pts = -sl
            elif i in spike_set:
                exit_pts = unrealized
            elif bars_held >= max_hold:
                exit_pts = unrealized
            
            if exit_pts is not None:
                pnls.append(exit_pts * dpp)
                in_trade = False
        
        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = bars_arr[i, 3]
            entry_idx = i
            in_trade = True
    
    if in_trade:
        c = bars_arr[-1, 3]
        pnl = (c - entry_price if direction == 'long' else entry_price - c) * dpp
        pnls.append(pnl)
    
    return pnls


def get_smc_trades(sym):
    """Get SMC trade results from existing backtest."""
    from eval.smc_spike_hunter import run_smc_backtest
    
    # Use the best config from our research
    if 'boom500' in sym.lower():
        r = run_smc_backtest(sym, sl_pct=0.005, risk=0.01, rsi_low=15, rsi_high=85, max_hold=60)
    elif 'boom1000' in sym.lower():
        r = run_smc_backtest(sym, sl_pct=0.005, risk=0.01, rsi_low=15, rsi_high=85, max_hold=60)
    else:
        r = run_smc_backtest(sym, sl_pct=0.005, risk=0.01, rsi_low=10, rsi_high=90, max_hold=60)
    
    return r


def monte_carlo_combined(smc_monthly, drift_monthly, months=12, n_sims=1000, risk_pct=0.02):
    """Monte Carlo projection for combined portfolio."""
    np.random.seed(42)
    
    # Build trade distribution from actual backtest data
    # SMC: ~24% WR, high R:R
    # Drift: ~83% WR, moderate R:R
    
    results = np.zeros(n_sims)
    
    for sim in range(n_sims):
        equity = 10000
        for month in range(months):
            # SMC trades this month
            smc_trades = int(np.random.poisson(max(smc_monthly['trades_per_month'], 0.5)))
            for _ in range(smc_trades):
                risk_dollars = equity * risk_pct
                if np.random.random() < smc_monthly['win_rate']:
                    pnl = risk_dollars * smc_monthly['avg_rr']
                else:
                    pnl = -risk_dollars
                equity += pnl
            
            # Drift trades this month
            drift_trades = int(np.random.poisson(max(drift_monthly['trades_per_month'], 0.5)))
            for _ in range(drift_trades):
                risk_dollars = equity * risk_pct
                if np.random.random() < drift_monthly['win_rate']:
                    pnl = risk_dollars * drift_monthly['avg_rr']
                else:
                    pnl = -risk_dollars
                equity += pnl
        
        results[sim] = equity
    
    return results


def main():
    import os
    
    print(f"\n{'='*80}")
    print(f"  COMBINED PORTFOLIO: SMC + Drift Fade")
    print(f"  Running both strategies independently on same account")
    print(f"{'='*80}")
    
    for sym in ['BOOM500', 'BOOM1000']:
        print(f"\n  {'─'*70}")
        print(f"  {sym}")
        print(f"  {'─'*70}")
        
        # Get SMC results
        smc = get_smc_trades(sym)
        if smc['trades'] == 0:
            print(f"  No SMC trades")
            continue
        
        # Get Drift Fade results
        path = f'data/candles_{sym.lower()}_1min.parquet'
        if not os.path.exists(path):
            continue
        
        candles = pd.read_parquet(path)
        candles.columns = [c.lower() for c in candles.columns]
        bars_arr = candles[['open', 'high', 'low', 'close']].values.astype(float)
        dpp = 0.10 if '1000' in sym else 0.20
        direction = 'up' if 'boom' in sym.lower() else 'down'
        spikes = detect_spikes(bars_arr, direction, threshold=3.0)
        drift_pnls = backtest_drift(bars_arr, spikes, entry_bar=3, sl=2, tp=3,
                                     max_hold=9, direction=direction if direction == 'long' else 'short',
                                     dpp=dpp)
        
        drift_wr = sum(1 for p in drift_pnls if p > 0) / max(len(drift_pnls), 1)
        drift_avg_win = np.mean([p for p in drift_pnls if p > 0]) if any(p > 0 for p in drift_pnls) else 0
        drift_avg_loss = np.mean([p for p in drift_pnls if p <= 0]) if any(p <= 0 for p in drift_pnls) else 0
        drift_avg_rr = abs(drift_avg_win / drift_avg_loss) if drift_avg_loss != 0 else 99
        drift_trades_per_month = len(drift_pnls) / 30
        
        # SMC stats
        smc_trades = smc['trades']
        smc_wr = smc['win_rate']
        smc_pf = smc['profit_factor']
        smc_trades_per_month = smc_trades / 30
        
        print(f"\n  STRATEGY BREAKDOWN:")
        print(f"  {'':20} {'SMC':>12} {'Drift Fade':>12} {'Combined':>12}")
        print(f"  {'─'*56}")
        print(f"  {'Trades/month':<20} {smc_trades_per_month:>12.1f} {drift_trades_per_month:>12.1f} {smc_trades_per_month+drift_trades_per_month:>12.1f}")
        print(f"  {'Win Rate':<20} {smc_wr*100:>11.1f}% {drift_wr*100:>11.1f}% {'':>12}")
        print(f"  {'Profit Factor':<20} {smc_pf:>12.2f} {'':>12} {'':>12}")
        
        # Project returns at different account sizes
        print(f"\n  PROJECTED RETURNS (2% risk per trade):")
        print(f"  {'Account':>10} {'SMC/mo':>10} {'Drift/mo':>10} {'Total/mo':>10} {'Annual':>12} {'Status'}")
        print(f"  {'─'*65}")
        
        for acct in [100, 200, 500, 1000]:
            risk_per_trade = acct * 0.02
            
            # SMC monthly
            smc_win_pnl = risk_per_trade * smc['avg_win'] / max(abs(smc['avg_loss']), 0.001) if smc.get('avg_loss', 0) != 0 else risk_per_trade * 7.8
            smc_loss_pnl = -risk_per_trade
            smc_monthly = smc_trades_per_month * (smc_wr * smc_win_pnl + (1-smc_wr) * smc_loss_pnl)
            
            # Drift monthly
            drift_win_pnl = risk_per_trade * drift_avg_rr
            drift_loss_pnl = -risk_per_trade
            drift_monthly = drift_trades_per_month * (drift_wr * drift_win_pnl + (1-drift_wr) * drift_loss_pnl)
            
            total_monthly = smc_monthly + drift_monthly
            total_annual = total_monthly * 12
            
            status = "✅" if total_annual > 0 else "❌"
            print(f"  ${acct:>8}  ${smc_monthly:>+9.2f} ${drift_monthly:>+9.2f} ${total_monthly:>+9.2f} ${total_annual:>+11.0f}  {status}")
        
        # Monte Carlo
        smc_stats = {
            'trades_per_month': smc_trades_per_month,
            'win_rate': smc_wr,
            'avg_rr': smc['avg_win'] / max(abs(smc['avg_loss']), 0.001) if smc.get('avg_loss', 0) != 0 else 7.8,
        }
        drift_stats = {
            'trades_per_month': drift_trades_per_month,
            'win_rate': drift_wr,
            'avg_rr': drift_avg_rr,
        }
        
        mc_results = monte_carlo_combined(smc_stats, drift_stats, months=12, n_sims=1000)
        
        print(f"\n  MONTE CARLO (12-month, 1000 sims, $10,000 start, 2% risk):")
        print(f"    Median:  ${np.median(mc_results):>+10.2f} ({(np.median(mc_results)/10000-1)*100:+.1f}%)")
        print(f"    P5:      ${np.percentile(mc_results, 5):>+10.2f} ({(np.percentile(mc_results, 5)/10000-1)*100:+.1f}%)")
        print(f"    P95:     ${np.percentile(mc_results, 95):>+10.2f} ({(np.percentile(mc_results, 95)/10000-1)*100:+.1f}%)")
        print(f"    Blowup:  {(np.sum(mc_results < 5000)/1000*100):.1f}%")
        print(f"    Profit:  {(np.sum(mc_results > 10000)/1000*100):.1f}% chance")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
