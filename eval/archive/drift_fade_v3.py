"""Drift Fade Strategy — Fixed backtest (no compounding distortion).

KEY INSIGHT from data analysis:
  After every spike, price drifts LINEARLY:
  BOOM500: -0.057% per bar (price drops before next spike)
  CRASH500: +0.057% per bar (price rises before next spike)

STRATEGY: Enter counter-drift, exit at TP/SL/spike/timeout.
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


def backtest(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction):
    """Fixed-risk backtest (no compounding)."""
    n = len(bars_arr)
    RISK_PER_TRADE = 100  # Fixed $100 risk per trade for clean stats
    
    in_trade = False
    entry_price = 0
    entry_idx = 0
    
    trades_pnl = []
    last_spike = -1
    spike_set = set(spikes.tolist())
    
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
                unrealized = c - entry_price
            else:
                hit_tp = (entry_price - l) >= tp
                hit_sl = (entry_price - h) <= -sl
                unrealized = entry_price - c
            
            exit_price = None
            exit_reason = None
            
            if hit_tp:
                exit_price = tp
                exit_reason = 'tp'
            elif hit_sl:
                exit_price = -sl
                exit_reason = 'sl'
            elif i in spike_set:
                exit_price = unrealized
                exit_reason = 'spike'
            elif bars_held >= max_hold:
                exit_price = unrealized
                exit_reason = 'timeout'
            
            if exit_price is not None:
                pnl = RISK_PER_TRADE * (exit_price / sl)
                trades_pnl.append({'pnl': pnl, 'reason': exit_reason, 'bars': bars_held})
                in_trade = False
        
        elif not in_trade and bars_since == entry_bar and last_spike >= 0:
            entry_price = bars_arr[i, 3]
            entry_idx = i
            in_trade = True
    
    if not trades_pnl:
        return None
    
    pnls = [t['pnl'] for t in trades_pnl]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    
    reasons = {}
    for t in trades_pnl:
        reasons[t['reason']] = reasons.get(t['reason'], 0) + 1
    
    total_wins = sum(wins)
    total_losses = abs(sum(losses))
    
    # Equity curve for max DD
    eq = [10000]
    for p in pnls:
        eq.append(eq[-1] + p)
    eq = np.array(eq)
    peak = np.maximum.accumulate(eq)
    dd = (peak - eq) / peak * 100
    
    return {
        'trades': len(pnls),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': len(wins) / len(pnls) * 100,
        'total_pnl': sum(pnls),
        'pnl_pct': sum(pnls) / 10000 * 100,
        'pf': total_wins / max(total_losses, 0.01),
        'avg_win': np.mean(wins) if wins else 0,
        'avg_loss': np.mean(losses) if losses else 0,
        'avg_rr': (np.mean(wins) / abs(np.mean(losses))) if losses and np.mean(losses) != 0 else 99,
        'max_dd': dd.max(),
        'median_pnl': np.median(pnls),
        'avg_hold': np.mean([t['bars'] for t in trades_pnl]),
        'reasons': reasons,
    }


def main():
    import os
    
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'up'),
        'CRASH500': ('data/candles_crash500_1min.parquet', 'down'),
        'BOOM1000': ('data/candles_boom1000_1min.parquet', 'up'),
        'CRASH1000': ('data/candles_crash1000_1min.parquet', 'down'),
    }
    
    print(f"\n{'='*80}")
    print(f"  DRIFT FADE STRATEGY — BACKTEST (FIXED RISK)")
    print(f"{'='*80}")
    
    for sym, (path, direction) in configs.items():
        if not os.path.exists(path):
            continue
        
        candles = pd.read_parquet(path)
        candles.columns = [c.lower() for c in candles.columns]
        bars_arr = candles[['open', 'high', 'low', 'close']].values.astype(float)
        spikes = detect_spikes(bars_arr, direction, threshold=3.0)
        
        print(f"\n  {'─'*70}")
        print(f"  {sym}: {len(bars_arr)} bars, {len(spikes)} spikes")
        print(f"  {'─'*70}")
        
        results = []
        for entry_bar in [5, 8, 10, 12, 15, 20]:
            for sl in [3, 5, 7, 10]:
                for tp in [5, 8, 10, 15, 20]:
                    if tp <= sl:
                        continue
                    for max_hold in [entry_bar * 2, entry_bar * 3]:
                        r = backtest(bars_arr, spikes, entry_bar, sl, tp, max_hold, direction)
                        if r and r['trades'] >= 10:
                            r['config'] = f"e={entry_bar} SL={sl} TP={tp} h={max_hold}"
                            r['score'] = r['pf'] * (r['win_rate']/100) * np.log1p(r['trades'])
                            results.append(r)
        
        results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n  TOP 10:")
        print(f"  {'Config':<25} {'Trades':>6} {'WR%':>6} {'PF':>7} {'PnL$':>10} {'AvgWin':>8} {'AvgLoss':>8} {'DD%':>5} {'Reasons'}")
        print(f"  {'─'*95}")
        
        for i, r in enumerate(results[:10]):
            print(f"  {r['config']:<25} {r['trades']:>6} {r['win_rate']:>5.1f}% "
                  f"{r['pf']:>6.2f} {r['total_pnl']:>+9.0f} "
                  f"{r['avg_win']:>+7.0f} {r['avg_loss']:>+7.0f} "
                  f"{r['max_dd']:>4.1f}% {r['reasons']}")
        
        if results:
            best = results[0]
            print(f"\n  BEST: {best['config']}")
            print(f"    Trades: {best['trades']} ({best['trades']/30:.1f}/month)")
            print(f"    Avg R:R: 1:{best['avg_rr']:.1f}")
            print(f"    Avg hold: {best['avg_hold']:.1f} bars")
            
            # Scale to actual lot size
            # BOOM500/CRASH500 min lot 0.20, BOOM1000/CRASH1000 min lot 0.10
            if '1000' in sym:
                lot_value_per_point = 0.10  # $0.10 per point at 0.10 lot
            else:
                lot_value_per_point = 0.20  # $0.20 per point at 0.20 lot
            
            # For the best SL, what's the actual risk per trade?
            sl_val = int(best['config'].split('SL=')[1].split(' ')[0])
            actual_risk = sl_val * lot_value_per_point
            
            # Monthly return at 2% risk
            risk_per_trade = 0.02  # 2% of account
            avg_pnl_per_trade = best['total_pnl'] / best['trades']
            trades_per_month = best['trades'] / 30
            
            print(f"\n  SCALING TO REAL TRADING:")
            print(f"    Min lot: {0.10 if '1000' in sym else 0.20}")
            print(f"    Risk per point: ${lot_value_per_point}")
            print(f"    SL points: {sl_val}")
            print(f"    Actual risk per trade: ${actual_risk}")
            print(f"    Account needed for 2% risk: ${actual_risk / 0.02:.0f}")
            
            # Expected monthly at different account sizes
            for acct in [100, 200, 500, 1000]:
                risk_dollars = acct * risk_per_trade
                scale = risk_dollars / (sl_val * lot_value_per_point)
                monthly = trades_per_month * avg_pnl_per_trade * scale
                print(f"    ${acct:>5} account: ${monthly:>+8.2f}/month (${monthly*12:>+8.0f}/year)")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
