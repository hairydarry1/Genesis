"""Hybrid Strategy: SMC Spike Hunter + Drift Fade.

THESIS: SMC finds WHERE to trade (zones), Drift Fade finds WHEN (timing).
Combined: Only enter SMC trades when we're in the drift window (3-15 bars after spike).

This should IMPROVE SMC win rate by avoiding entries during the volatile spike window.
"""
import sys
sys.path.insert(0, '.')
import numpy as np
import pandas as pd
from pathlib import Path
from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, detect_order_blocks,
    load_candles, compute_rsi, compute_ema, compute_sma, price_in_zone
)


def detect_m1_spikes(bars_arr, direction='up', threshold=3.0):
    """Detect spike bars on M1 data."""
    ranges = bars_arr[:, 1] - bars_arr[:, 2]
    med = np.median(ranges)
    spikes = set()
    for i in range(len(bars_arr)):
        if ranges[i] > threshold * med:
            if direction == 'up' and bars_arr[i, 3] > bars_arr[i, 0]:
                spikes.add(i)
            elif direction == 'down' and bars_arr[i, 3] < bars_arr[i, 0]:
                spikes.add(i)
    return spikes


def run_hybrid_backtest(symbol, rsi_low=15, rsi_high=85, sl_pct=0.005,
                         risk=0.01, drift_entry_min=3, drift_entry_max=15,
                         use_drift_filter=True, initial_equity=10000):
    """Run hybrid SMC + Drift Fade backtest."""
    # Load data
    h1 = load_candles(symbol, "60min")
    m1 = load_candles(symbol, "1min")
    m1_arr = np.array([[b['open'], b['high'], b['low'], b['close']] for b in m1])
    
    # Detect spikes for drift tracking
    direction = 'up' if 'boom' in symbol.lower() else 'down'
    spikes = detect_m1_spikes(m1_arr, direction)
    
    # Compute bars-since-spike for each bar
    last_spike = -1
    bars_since_spike = np.full(len(m1), 999)
    for i in range(len(m1)):
        if i in spikes:
            last_spike = i
        if last_spike >= 0:
            bars_since_spike[i] = i - last_spike
    
    # Initialize SMC strategy
    cfg = SMCHunterConfig(
        sl_pct=sl_pct,
        risk_per_trade=risk,
        rsi_extreme_low=rsi_low,
        rsi_extreme_high=rsi_high,
        max_hold=60,
    )
    
    strat = SMCSpikeHunter(symbol, config=cfg, initial_equity=initial_equity)
    zones = detect_order_blocks(h1, threshold_mult=2.5 if 'boom' in symbol.lower() else 2.0)
    strat.set_h1_zones(zones)
    strat.precompute(m1)
    
    # Run backtest with optional drift filter
    hybrid_trades = []
    smc_only_trades = []
    
    for i in range(len(m1)):
        bar = m1[i]
        
        # Get SMC signal
        result = strat.on_bar(bar, i)
        
        # If SMC opened a trade, check drift filter
        if result and result.get('action') == 'open':
            bss = bars_since_spike[i]
            
            # Record what SMC would have done (no filter)
            smc_only_trades.append({
                'bar': i,
                'bss': bss,
                'zone': result.get('zone', 'none'),
                'rsi': result.get('rsi', 0),
            })
            
            # Apply drift filter: only keep if in drift window
            if use_drift_filter:
                if not (drift_entry_min <= bss <= drift_entry_max):
                    # Close immediately (reject trade)
                    close_result = strat._close_position(
                        bar['close'], 'drift_filter_reject', bar['close']
                    )
                    continue  # Skip recording this trade
        
        # Record exits
        if result and result.get('action') == 'close':
            trade = strat.trade_log[-1] if strat.trade_log else None
            if trade:
                hybrid_trades.append(trade)
    
    # Close any open position
    if strat.position != 0:
        strat._close_position(m1[-1]['close'], 'end_of_data', m1[-1]['close'])
        if strat.trade_log:
            hybrid_trades.append(strat.trade_log[-1])
    
    return {
        'symbol': symbol,
        'zones': len(zones),
        'smc_signals': len(smc_only_trades),
        'hybrid_trades': len(hybrid_trades),
        'trades': strat.trade_log,
        'equity': strat.equity,
        'initial_equity': strat.initial_equity,
        'equity_curve': strat.equity_curve,
    }


def analyze_results(result, label):
    """Analyze and print backtest results."""
    trades = result['trades']
    n = len(trades)
    if n == 0:
        print(f"  {label}: No trades")
        return
    
    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    
    wr = len(wins) / n * 100
    tw = sum(wins)
    tl = abs(sum(losses))
    pf = tw / max(tl, 0.01)
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0
    avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 99
    max_dd = max((e['dd'] for e in result['equity_curve']), default=0) * 100
    ret = (result['equity'] / result['initial_equity'] - 1) * 100
    
    # Exit reasons
    reasons = {}
    for t in trades:
        reasons[t['exit_reason']] = reasons.get(t['exit_reason'], 0) + 1
    
    # Bars held
    avg_hold = np.mean([t['bars_held'] for t in trades])
    
    print(f"  {label}")
    print(f"    Trades:    {n} ({n/30:.1f}/month)")
    print(f"    Win Rate:  {wr:.1f}%")
    print(f"    PF:        {pf:.2f}")
    print(f"    Avg R:R:   1:{avg_rr:.1f}")
    print(f"    Avg Win:   ${avg_win:.4f}")
    print(f"    Avg Loss:  ${avg_loss:.4f}")
    print(f"    Return:    {ret:+.2f}%")
    print(f"    Max DD:    {max_dd:.2f}%")
    print(f"    Avg Hold:  {avg_hold:.1f} bars")
    print(f"    Exits:     {reasons}")
    
    return {
        'trades': n, 'wr': wr, 'pf': pf, 'avg_rr': avg_rr,
        'avg_win': avg_win, 'avg_loss': avg_loss, 'ret': ret,
        'max_dd': max_dd, 'avg_hold': avg_hold, 'reasons': reasons,
    }


def main():
    print(f"\n{'='*80}")
    print(f"  HYBRID STRATEGY: SMC Spike Hunter + Drift Fade")
    print(f"  SMC finds WHERE (zones), Drift Fade finds WHEN (timing)")
    print(f"{'='*80}")
    
    for sym in ['BOOM500', 'BOOM1000']:
        print(f"\n  {'─'*70}")
        print(f"  {sym}")
        print(f"  {'─'*70}")
        
        # 1. SMC Only (no drift filter)
        result_smc = run_hybrid_backtest(sym, use_drift_filter=False)
        stats_smc = analyze_results(result_smc, "SMC Only (no drift filter)")
        
        # 2. SMC + Drift Filter (3-15 bars)
        result_hybrid = run_hybrid_backtest(sym, use_drift_filter=True,
                                              drift_entry_min=3, drift_entry_max=15)
        stats_hybrid = analyze_results(result_hybrid, "SMC + Drift Filter (3-15 bars)")
        
        # 3. SMC + Tight Drift Filter (5-12 bars)
        result_tight = run_hybrid_backtest(sym, use_drift_filter=True,
                                             drift_entry_min=5, drift_entry_max=12)
        stats_tight = analyze_results(result_tight, "SMC + Drift Filter (5-12 bars)")
        
        # 4. Drift Fade Only (for comparison)
        # Import and run
        from eval.drift_fade_final import detect_spikes as df_detect, backtest as df_backtest
        candles = pd.read_parquet(f'data/candles_{sym.lower()}_1min.parquet')
        candles.columns = [c.lower() for c in candles.columns]
        bars_arr = candles[['open', 'high', 'low', 'close']].values.astype(float)
        dpp = 0.10 if '1000' in sym else 0.20
        spikes_arr = df_detect(bars_arr, 'up' if 'boom' in sym.lower() else 'down', threshold=3.0)
        drift_result = df_backtest(bars_arr, spikes_arr, entry_bar=3, sl=2, tp=3,
                                    max_hold=9, direction='up' if 'boom' in sym.lower() else 'down',
                                    dollar_per_point=dpp)
        if drift_result:
            print(f"\n  Drift Fade Only (for reference)")
            print(f"    Trades: {drift_result['trades']} ({drift_result['trades']/30:.1f}/month)")
            print(f"    WR: {drift_result['win_rate']:.1f}% | PF: {drift_result['pf']:.2f}")
            print(f"    PnL: ${drift_result['total_pnl']:+.2f}")
        
        # Compare
        if stats_smc and stats_hybrid:
            print(f"\n  📊 COMPARISON:")
            print(f"  {'Metric':<20} {'SMC Only':>12} {'Hybrid':>12} {'Change':>12}")
            print(f"  {'─'*56}")
            print(f"  {'Trades':<20} {stats_smc['trades']:>12} {stats_hybrid['trades']:>12} {stats_hybrid['trades']-stats_smc['trades']:>+12}")
            print(f"  {'Win Rate':<20} {stats_smc['wr']:>11.1f}% {stats_hybrid['wr']:>11.1f}% {stats_hybrid['wr']-stats_smc['wr']:>+11.1f}%")
            print(f"  {'Profit Factor':<20} {stats_smc['pf']:>12.2f} {stats_hybrid['pf']:>12.2f} {stats_hybrid['pf']-stats_smc['pf']:>+12.2f}")
            print(f"  {'Max Drawdown':<20} {stats_smc['max_dd']:>11.2f}% {stats_hybrid['max_dd']:>11.2f}% {stats_hybrid['max_dd']-stats_smc['max_dd']:>+11.2f}%")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
