"""Drift Fade Strategy — Final backtest with correct dollar PnL.

Actual lot values:
  BOOM500/CRASH500: $0.20 per point (0.20 lot min)
  BOOM1000/CRASH1000: $0.10 per point (0.10 lot min)
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


def backtest(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction, dollar_per_point):
    """Backtest with correct dollar PnL."""
    n = len(bars_arr)
    spike_set = set(spikes.tolist())
    last_spike = -1
    
    in_trade = False
    entry_price = 0
    entry_idx = 0
    
    pnls = []
    reasons = []
    
    for i in range(n):
        if i in spike_set:
            last_spike = i
        
        bars_since = i - last_spike if last_spike >= 0 else 999
        
        if in_trade:
            o, h, l, c = bars_arr[i]
            bars_held = i - entry_idx
            
            if direction == 'long':
                hit_tp = (h - entry_price) >= tp
                hit_sl = (l - entry_price) <= -sl
                unrealized_pts = c - entry_price
            else:
                hit_tp = (entry_price - l) >= tp
                hit_sl = (entry_price - h) <= -sl
                unrealized_pts = entry_price - c
            
            exit_pts = None
            reason = None
            
            if hit_tp:
                exit_pts = tp
                reason = 'tp'
            elif hit_sl:
                exit_pts = -sl
                reason = 'sl'
            elif i in spike_set:
                exit_pts = unrealized_pts
                reason = 'spike'
            elif bars_held >= max_hold:
                exit_pts = unrealized_pts
                reason = 'timeout'
            
            if exit_pts is not None:
                pnl_dollars = exit_pts * dollar_per_point
                pnls.append(pnl_dollars)
                reasons.append(reason)
                in_trade = False
        
        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = bars_arr[i, 3]
            entry_idx = i
            in_trade = True
    
    if not pnls:
        return None
    
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    reason_counts = {}
    for r in reasons:
        reason_counts[r] = reason_counts.get(r, 0) + 1
    
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    
    # Equity curve
    eq = np.cumsum([0] + pnls)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / np.maximum(peak, 1) * 100
    
    return {
        'trades': len(pnls),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(pnls) * 100,
        'total_pnl': sum(pnls),
        'pf': total_wins / max(total_losses, 0.01),
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'avg_rr': (np.mean(wins) / abs(np.mean(losses))) if losses and np.mean(losses) != 0 else 99,
        'max_dd': dd.max(),
        'avg_hold': entry_bar + np.mean([i - entry_idx for i, reason in zip(range(len(reasons)), reasons)]) if reasons else 0,
        'reasons': reason_counts,
        'pnls': pnls,
    }


def project_returns(sym, result, dollar_per_point, risk_pct=0.02):
    """Project monthly returns at different account sizes."""
    if not result:
        return
    
    trades_per_month = result['trades'] / 30
    avg_pnl = result['total_pnl'] / result['trades']
    
    # SL from best config
    sl_val = 3  # Will be passed in
    
    print(f"\n  PROJECTED RETURNS ({risk_pct*100:.0f}% risk per trade):")
    print(f"  {'Account':>10} {'Risk/Trade':>10} {'Monthly':>10} {'Annual':>12} {'Status'}")
    print(f"  {'─'*60}")
    
    for acct in [50, 100, 200, 500, 1000]:
        risk_dollars = acct * risk_pct
        
        # Check if risk_dollars can cover SL
        min_risk = sl_val * dollar_per_point
        if risk_dollars < min_risk:
            print(f"  ${acct:>8}  ${risk_dollars:>8.2f}  {'N/A (risk too low)':>20}")
            continue
        
        # Scale: how many "units" of risk can we trade?
        # Each unit risks min_risk dollars
        # We want to risk risk_dollars per trade
        # So we trade risk_dollars / min_risk units
        units = risk_dollars / min_risk
        
        monthly = trades_per_month * avg_pnl * units
        annual = monthly * 12
        
        status = "✅" if risk_pct <= 0.02 else "⚠️"
        print(f"  ${acct:>8}  ${risk_dollars:>8.2f}  ${monthly:>+9.2f}  ${annual:>+11.0f}  {status}")


def main():
    import os
    
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'up', 0.20),
        'CRASH500': ('data/candles_crash500_1min.parquet', 'down', 0.20),
        'BOOM1000': ('data/candles_boom1000_1min.parquet', 'up', 0.10),
        'CRASH1000': ('data/candles_crash1000_1min.parquet', 'down', 0.10),
    }
    
    print(f"\n{'='*80}")
    print(f"  DRIFT FADE STRATEGY — FINAL BACKTEST")
    print(f"  Edge: Linear price drift between spikes (~0.057%/bar)")
    print(f"  Strategy: Counter-drift entry after N bars, TP/SL exit")
    print(f"{'='*80}")
    
    all_best = {}
    
    for sym, (path, direction, dpp) in configs.items():
        if not os.path.exists(path):
            continue
        
        candles = pd.read_parquet(path)
        candles.columns = [c.lower() for c in candles.columns]
        bars_arr = candles[['open', 'high', 'low', 'close']].values.astype(float)
        spikes = detect_spikes(bars_arr, direction, threshold=3.0)
        
        print(f"\n  {'─'*70}")
        print(f"  {sym}: {len(bars_arr)} bars, {len(spikes)} spikes, ${dpp}/point")
        print(f"  {'─'*70}")
        
        best_score = -999
        best_result = None
        best_cfg = None
        
        all_results = []
        for entry_bar in [3, 5, 8, 10, 12, 15, 20]:
            for sl in [2, 3, 5, 7]:
                for tp in [3, 5, 7, 10]:
                    if tp <= sl:
                        continue
                    for max_hold in [entry_bar * 2, entry_bar * 3]:
                        r = backtest(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction, dpp)
                        if r and r['trades'] >= 20:
                            score = r['pf'] * (r['win_rate']/100) * np.log1p(r['trades']) * (1 - r['max_dd']/100)
                            r['score'] = score
                            r['cfg'] = f"e={entry_bar} SL={sl} TP={tp} h={max_hold}"
                            all_results.append(r)
                            if score > best_score:
                                best_score = score
                                best_result = r
                                best_cfg = {'entry_bar': entry_bar, 'sl': sl, 'tp': tp, 'max_hold': max_hold}
        
        all_results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n  TOP 5:")
        print(f"  {'Config':<25} {'Trades':>6} {'WR%':>6} {'PF':>6} {'PnL$':>10} {'AvgW':>7} {'AvgL':>7} {'DD%':>5}")
        print(f"  {'─'*85}")
        
        for i, r in enumerate(all_results[:5]):
            print(f"  {r['cfg']:<25} {r['trades']:>6} {r['win_rate']:>5.1f}% "
                  f"{r['pf']:>5.2f} {r['total_pnl']:>+9.2f} "
                  f"{r['avg_win']:>+6.2f} {r['avg_loss']:>+6.2f} {r['max_dd']:>4.1f}%")
        
        if best_result:
            print(f"\n  🏆 BEST: {best_result['cfg']}")
            print(f"    Trades: {best_result['trades']} ({best_result['trades']/30:.1f}/month)")
            print(f"    Win Rate: {best_result['win_rate']:.1f}%")
            print(f"    Profit Factor: {best_result['pf']:.2f}")
            print(f"    Avg R:R: 1:{best_result['avg_rr']:.1f}")
            print(f"    Max Drawdown: {best_result['max_dd']:.2f}%")
            print(f"    Exit Reasons: {best_result['reasons']}")
            
            project_returns(sym, best_result, dpp, risk_pct=0.02)
            project_returns(sym, best_result, dpp, risk_pct=0.03)
            project_returns(sym, best_result, dpp, risk_pct=0.05)
            
            all_best[sym] = best_result
    
    # Combined projection
    if all_best:
        print(f"\n{'='*80}")
        print(f"  COMBINED PORTFOLIO PROJECTION")
        print(f"{'='*80}")
        
        for risk_pct in [0.02, 0.03, 0.05]:
            total_monthly = 0
            print(f"\n  Risk: {risk_pct*100:.0f}% per trade")
            for sym, r in all_best.items():
                dpp = configs[sym][2]
                sl_val = 3  # Most configs use SL=3
                trades_per_month = r['trades'] / 30
                avg_pnl = r['total_pnl'] / r['trades']
                
                for acct in [100, 200, 500]:
                    risk_dollars = acct * risk_pct
                    min_risk = sl_val * dpp
                    if risk_dollars >= min_risk:
                        units = risk_dollars / min_risk
                        monthly = trades_per_month * avg_pnl * units
                        total_monthly += monthly
            
            print(f"  Total monthly (all symbols, $200 each): ${total_monthly:+.2f}")
            print(f"  Total annual: ${total_monthly*12:+.2f}")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
