"""Drift Fade Strategy — Optimized backtest.

EDGE: After every spike, price drifts LINEARLY in the opposite direction.
  BOOM500: drifts DOWN ~0.057% per bar for 25+ bars
  CRASH500: drifts UP ~0.057% per bar for 25+ bars

STRATEGY: Enter COUNTER-TRADE after N bars, exit at TP/SL/timeout.
"""
import sys
sys.path.insert(0, '.')

import numpy as np
import pandas as pd


def detect_spikes(bars_arr, direction, threshold=3.0):
    """Detect spike bars. bars_arr: Nx4 array [open, high, low, close]"""
    ranges = bars_arr[:, 1] - bars_arr[:, 2]  # high - low
    med = np.median(ranges)
    spikes = []
    for i in range(len(bars_arr)):
        rng = ranges[i]
        if rng > threshold * med:
            if direction == 'up' and bars_arr[i, 3] > bars_arr[i, 0]:
                spikes.append(i)
            elif direction == 'down' and bars_arr[i, 3] < bars_arr[i, 0]:
                spikes.append(i)
    return np.array(spikes)


def backtest_drift_fade(bars_arr, spikes, entry_bar, sl, tp, max_hold, 
                         direction, risk_pct=0.02, equity=10000.0):
    """Vectorized drift fade backtest."""
    n = len(bars_arr)
    equity_curve = np.full(n, equity)
    
    in_trade = False
    entry_price = 0
    entry_idx = 0
    trade_pnl = 0
    
    wins = 0
    losses = 0
    total_pnl = 0
    win_pnl = 0
    loss_pnl = 0
    max_dd = 0
    peak = equity
    hold_bars_list = []
    
    # Create spike lookup
    spike_set = set(spikes.tolist())
    
    # Track bars since spike
    last_spike = -1
    
    for i in range(n):
        if i in spike_set:
            last_spike = i
        
        bars_since = i - last_spike if last_spike >= 0 else 999
        
        if in_trade:
            o, h, l, c = bars_arr[i]
            
            if direction == 'long':
                unrealized = c - entry_price
                hit_high = h - entry_price
                hit_low = l - entry_price
            else:
                unrealized = entry_price - c
                hit_high = entry_price - l
                hit_low = entry_price - h
            
            bars_held = i - entry_idx
            
            # TP
            if hit_high >= tp:
                trade_pnl = tp
                in_trade = False
            # SL
            elif hit_low <= -sl:
                trade_pnl = -sl
                in_trade = False
            # Timeout
            elif bars_held >= max_hold:
                trade_pnl = unrealized
                in_trade = False
            # Spike exit
            elif i in spike_set:
                trade_pnl = unrealized
                in_trade = False
            
            if not in_trade:
                risk_dollars = equity * risk_pct
                pnl_dollars = risk_dollars * (trade_pnl / sl)
                total_pnl += pnl_dollars
                
                if trade_pnl > 0:
                    wins += 1
                    win_pnl += pnl_dollars
                else:
                    losses += 1
                    loss_pnl += pnl_dollars
                
                hold_bars_list.append(bars_held)
                equity += pnl_dollars
                peak = max(peak, equity)
                dd = (peak - equity) / peak * 100
                max_dd = max(max_dd, dd)
        
        elif not in_trade and bars_since == entry_bar:
            # Enter trade
            entry_price = bars_arr[i, 3]  # close
            entry_idx = i
            in_trade = True
    
    # Force close
    if in_trade:
        c = bars_arr[-1, 3]
        if direction == 'long':
            trade_pnl = c - entry_price
        else:
            trade_pnl = entry_price - c
        risk_dollars = equity * risk_pct
        pnl_dollars = risk_dollars * (trade_pnl / sl)
        total_pnl += pnl_dollars
        if trade_pnl > 0:
            wins += 1
            win_pnl += pnl_dollars
        else:
            losses += 1
            loss_pnl += pnl_dollars
    
    total_trades = wins + losses
    if total_trades == 0:
        return None
    
    return {
        'trades': total_trades,
        'wins': wins,
        'losses': losses,
        'win_rate': wins / total_trades * 100,
        'pnl': total_pnl,
        'pnl_pct': total_pnl / equity * 100,
        'pf': win_pnl / max(loss_pnl, 0.01),
        'avg_win': win_pnl / max(wins, 1),
        'avg_loss': loss_pnl / max(losses, 1),
        'avg_rr': (win_pnl/wins) / max(loss_pnl/losses, 0.01) if losses > 0 else 99,
        'max_dd': max_dd,
        'final_equity': equity,
        'avg_hold': np.mean(hold_bars_list) if hold_bars_list else 0,
        'trades_per_month': total_trades / 30,
    }


def main():
    import os
    
    configs = {
        'BOOM500': ('data/candles_boom500_1min.parquet', 'up'),
        'CRASH500': ('data/candles_crash500_1min.parquet', 'down'),
    }
    
    print(f"\n{'='*80}")
    print(f"  DRIFT FADE STRATEGY — OPTIMIZED BACKTEST")
    print(f"  Exploit linear price drift between spikes")
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
        for entry_bar in [5, 8, 10, 12, 15, 20, 25]:
            for sl in [3, 5, 7, 10]:
                for tp in [5, 8, 10, 15, 20]:
                    if tp <= sl:
                        continue
                    for max_hold in [entry_bar * 2, entry_bar * 3]:
                        r = backtest_drift_fade(
                            bars_arr, spikes, entry_bar, sl, tp, max_hold,
                            direction, risk_pct=0.02, equity=10000
                        )
                        if r and r['trades'] >= 10:
                            r['config'] = f"entry={entry_bar} SL={sl} TP={tp} hold={max_hold}"
                            r['score'] = r['pf'] * np.sqrt(r['trades']) * (1 - r['max_dd']/100)
                            results.append(r)
        
        results.sort(key=lambda x: x['score'], reverse=True)
        
        print(f"\n  TOP 10 CONFIGS:")
        print(f"  {'Config':<35} {'Trades':>7} {'WR%':>6} {'PF':>6} {'PnL$':>10} {'PnL%':>8} {'DD%':>6}")
        print(f"  {'─'*80}")
        
        for i, r in enumerate(results[:10]):
            print(f"  {r['config']:<35} {r['trades']:>7} {r['win_rate']:>5.1f}% "
                  f"{r['pf']:>5.2f} {r['pnl']:>+9.2f} {r['pnl_pct']:>+7.2f}% {r['max_dd']:>5.2f}%")
        
        # Show details for best
        if results:
            best = results[0]
            print(f"\n  BEST: {best['config']}")
            print(f"    Trades/month: {best['trades_per_month']:.1f}")
            print(f"    Avg hold:     {best['avg_hold']:.1f} bars")
            print(f"    Avg R:R:      1:{best['avg_rr']:.1f}")
            print(f"    Final equity: ${best['final_equity']:,.2f}")
            
            # Monte Carlo projection
            trades_per_month = best['trades_per_month']
            win_rate = best['win_rate'] / 100
            avg_win = best['avg_win']
            avg_loss = best['avg_loss']
            
            monthly_exp = trades_per_month * (win_rate * avg_win - (1-win_rate) * avg_loss)
            annual_exp = monthly_exp * 12
            
            print(f"\n  PROJECTIONS ($10,000 account, 2% risk):")
            print(f"    Monthly expected: ${monthly_exp:+,.2f}")
            print(f"    Annual expected:  ${annual_exp:+,.2f} ({annual_exp/100:.1f}%)")
            
            # Conservative (50% of backtest)
            print(f"    Conservative (50%): ${annual_exp*0.5:+,.2f}/year")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
