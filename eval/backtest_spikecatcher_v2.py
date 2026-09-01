"""
eval/backtest_spikecatcher_v2.py — Python backtest of SpikeCatcher_v2 EA logic.

Replicates the MQL5 EA strategy:
  - Spike detection: bar range > Nx median range
  - Entry: EntryBar bars after spike
  - Direction: Crash=LONG (buy dip), Boom=SHORT (fade spike)
  - TP: TPMultiplier × median range
  - SL: SLMultiplier × median range
  - Timeout: MaxHoldBars
  - Cluster filter: skip if ClusterMin+ spikes in ClusterWindow bars
  - Safety exit: close on spike detection (simplified for backtest)

For fair comparison with SMC, we also model:
  - Entry slippage (0.011%)
  - Position sizing at 1% risk
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_candles(symbol: str, timeframe: str = "1min") -> list[dict]:
    """Load parquet candle data."""
    import pandas as pd
    f = Path(__file__).parent.parent / "data" / f"candles_{symbol.lower()}_{timeframe}.parquet"
    if not f.exists():
        raise FileNotFoundError(f"Missing: {f}")
    df = pd.read_parquet(f)
    eps = df['epoch'].values.astype(int) if 'epoch' in df.columns else np.zeros(len(df), dtype=int)
    return [
        {"epoch": int(eps[i]), "open": float(df['open'].values[i]),
         "high": float(df['high'].values[i]), "low": float(df['low'].values[i]),
         "close": float(df['close'].values[i])}
        for i in range(len(df))
    ]


def run_spikecatcher_v2_backtest(
    symbol: str,
    spike_threshold: float = 3.0,
    entry_bar: int = 2,
    tp_mult: float = 2.0,
    sl_mult: float = 1.0,
    max_hold: int = 9,
    cluster_window: int = 10,
    cluster_min: int = 3,
    risk_pct: float = 0.01,
    safety_threshold: float = 2.5,
    use_safety_exit: bool = True,
) -> dict:
    """
    Run SpikeCatcher_v2 backtest on M1 data.
    
    Note: True tick-level safety exit can't be replicated on bar data.
    We approximate by checking if the NEXT bar's range exceeds safety threshold
    and closing at the open of that bar (worst case fill).
    """
    candles = load_candles(symbol, "1min")
    n = len(candles)
    
    if n < 200:
        return {"symbol": symbol, "trades": 0, "error": "insufficient data"}
    
    closes = np.array([c['close'] for c in candles], dtype=float)
    highs = np.array([c['high'] for c in candles], dtype=float)
    lows = np.array([c['low'] for c in candles], dtype=float)
    opens = np.array([c['open'] for c in candles], dtype=float)
    
    # Median range (circular buffer, 100 bars)
    lookback = 100
    ranges = highs - lows
    median_range = np.full(n, 0.01)
    for i in range(lookback, n):
        window = ranges[max(0, i - lookback):i]
        median_range[i] = np.median(window)
    
    # Determine direction
    is_crash = "crash" in symbol.lower()
    
    # Position sizing helper
    def calc_lots(sl_dist, equity):
        if sl_dist <= 0:
            return 0
        risk_dollars = equity * risk_pct
        # Simplified: assume 1 tick = 0.01 price move, tick value varies
        # Use price-based sizing for fair comparison
        lots = risk_dollars / sl_dist
        return max(lots, 0.01)
    
    # Backtest state
    equity = 10000.0
    peak_equity = 10000.0
    in_position = False
    position_dir = 0  # 1=long, -1=short
    entry_price = 0.0
    entry_bar = 0
    bars_held = 0
    requires_new_spike = True
    
    # Spike tracking
    spike_bars = []  # list of bar indices where spikes occurred
    last_spike_bar = -999
    bars_since_spike = 999
    
    # Stats
    trades = []
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0
    safety_exits = 0
    spikes_detected = 0
    
    # Entry slippage
    slippage_pct = 0.00011  # 0.011%
    
    # Run through bars
    for i in range(lookback + 1, n):
        bar_range = ranges[i - 1]
        med = median_range[i - 1]
        
        if med <= 0:
            med = 0.01
        
        # Check if previous bar was a spike
        is_spike = bar_range > spike_threshold * med
        if is_spike and med > 0:
            close_prev = closes[i - 1]
            open_prev = opens[i - 1]
            is_crash_spike = close_prev < open_prev
            is_boom_spike = close_prev > open_prev
            is_my_spike = is_crash_spike if is_crash else is_boom_spike
            
            if is_my_spike:
                last_spike_bar = i - 1
                bars_since_spike = 0
                requires_new_spike = False
                spike_bars.append(i - 1)
                spikes_detected += 1
            else:
                bars_since_spike += 1
        else:
            bars_since_spike += 1
        
        # Position management
        if in_position:
            bars_held += 1
            
            # Safety exit check: if current bar range > safety_threshold * median
            if use_safety_exit and bar_range > safety_threshold * med:
                # Check if spike is against position
                close_now = closes[i]
                against = False
                if position_dir == 1 and close_now < entry_price:
                    against = True
                if position_dir == -1 and close_now > entry_price:
                    against = True
                
                if against:
                    # Close at current bar close (conservative)
                    exit_price = close_now
                    if position_dir == 1:
                        pnl_pct = (exit_price - entry_price) / entry_price
                    else:
                        pnl_pct = (entry_price - exit_price) / entry_price
                    
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    
                    trades.append({
                        "entry": entry_price, "exit": exit_price,
                        "pnl": pnl_dollars, "bars_held": bars_held,
                        "reason": "safety_exit"
                    })
                    total_trades += 1
                    total_pnl += pnl_dollars
                    if pnl_dollars > 0:
                        wins += 1
                    else:
                        losses += 1
                    safety_exits += 1
                    
                    in_position = False
                    requires_new_spike = True
                    position_dir = 0
                    continue
            
            # TP check
            tp = tp_mult * med
            sl = sl_mult * med
            
            if position_dir == 1:  # Long
                if highs[i] - entry_price >= tp:
                    exit_price = entry_price + tp
                    pnl_pct = tp / entry_price
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "tp"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
                
                if entry_price - lows[i] >= sl:
                    exit_price = entry_price - sl
                    pnl_pct = -sl / entry_price
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "sl"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
            else:  # Short
                if entry_price - lows[i] >= tp:
                    exit_price = entry_price - tp
                    pnl_pct = tp / entry_price
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "tp"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
                
                if highs[i] - entry_price >= sl:
                    exit_price = entry_price + sl
                    pnl_pct = -sl / entry_price
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "sl"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
            
            # Timeout
            if bars_held >= max_hold:
                exit_price = closes[i]
                if position_dir == 1:
                    pnl_pct = (exit_price - entry_price) / entry_price
                else:
                    pnl_pct = (entry_price - exit_price) / entry_price
                pnl_dollars = position_size * pnl_pct
                equity += pnl_dollars
                peak_equity = max(peak_equity, equity)
                trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "timeout"})
                total_trades += 1; total_pnl += pnl_dollars
                if pnl_dollars > 0: wins += 1
                else: losses += 1
                in_position = False; requires_new_spike = True; position_dir = 0
                continue
        
        # Entry logic
        if not in_position and bars_since_spike == entry_bar and last_spike_bar >= 0:
            # Cluster filter
            cluster_count = 0
            for sb in spike_bars:
                if i - sb <= cluster_window and sb != last_spike_bar:
                    cluster_count += 1
            if cluster_count >= cluster_min - 1:
                continue
            
            if requires_new_spike:
                continue
            
            if median_range[i] <= 0:
                continue
            
            # Direction: Crash=LONG, Boom=SHORT
            dir = 1 if is_crash else -1
            
            # Calculate lot size
            sl_dist = sl_mult * median_range[i]
            position_size = calc_lots(sl_dist, equity)
            if position_size <= 0:
                continue
            
            # Entry price with slippage
            if dir == 1:
                entry_price = closes[i] * (1 - slippage_pct)
            else:
                entry_price = closes[i] * (1 + slippage_pct)
            
            in_position = True
            requires_new_spike = True
            position_dir = dir
            entry_bar_idx = i
            bars_held = 0
    
    # Close any open position at end
    if in_position:
        exit_price = closes[-1]
        if position_dir == 1:
            pnl_pct = (exit_price - entry_price) / entry_price
        else:
            pnl_pct = (entry_price - exit_price) / entry_price
        pnl_dollars = position_size * pnl_pct
        equity += pnl_dollars
        trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "end_of_data"})
        total_trades += 1; total_pnl += pnl_dollars
        if pnl_dollars > 0: wins += 1
        else: losses += 1
    
    # Calculate metrics
    wr = (wins / total_trades * 100) if total_trades > 0 else 0
    max_dd = ((peak_equity - equity) / peak_equity * 100) if peak_equity > 0 else 0
    ret = ((equity / 10000.0) - 1) * 100
    
    win_pnls = [t['pnl'] for t in trades if t['pnl'] > 0]
    loss_pnls = [t['pnl'] for t in trades if t['pnl'] <= 0]
    avg_win = np.mean(win_pnls) if win_pnls else 0
    avg_loss = np.mean(loss_pnls) if loss_pnls else 0
    
    tw = sum(win_pnls) if win_pnls else 0
    tl = abs(sum(loss_pnls)) if loss_pnls else 0
    pf = tw / tl if tl > 0 else 99
    
    exit_breakdown = {}
    for t in trades:
        r = t['reason']
        exit_breakdown[r] = exit_breakdown.get(r, 0) + 1
    
    return {
        "symbol": symbol,
        "trades": total_trades,
        "win_rate": wr / 100,
        "profit_factor": pf,
        "total_return_pct": ret,
        "max_dd_pct": max_dd,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "exit_breakdown": exit_breakdown,
        "spikes_detected": spikes_detected,
        "safety_exits": safety_exits,
    }


if __name__ == "__main__":
    symbols = ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]
    
    print("\n" + "=" * 72)
    print("  SPIKECATCHER v2 — BACKTEST")
    print("=" * 72)
    
    for sym in symbols:
        print(f"\n{'='*60}")
        print(f"  SPIKECATCHER v2 — {sym}")
        print(f"{'='*60}")
        try:
            r = run_spikecatcher_v2_backtest(sym)
            n = r['trades']
            if n == 0:
                print(f"  No trades. Spikes: {r.get('spikes_detected', 0)}")
            else:
                print(f"  Trades: {n}")
                print(f"  Win Rate: {r['win_rate']*100:.1f}%")
                print(f"  Profit Factor: {r['profit_factor']:.2f}")
                print(f"  Total Return: {r['total_return_pct']:+.2f}%")
                print(f"  Max Drawdown: {r['max_dd_pct']:.2f}%")
                print(f"  Avg Win: ${r['avg_win']:.4f}")
                print(f"  Avg Loss: ${r['avg_loss']:.4f}")
                print(f"  Exit Breakdown: {r['exit_breakdown']}")
                print(f"  Spikes Detected: {r['spikes_detected']}")
                print(f"  Safety Exits: {r['safety_exits']}")
        except Exception as e:
            print(f"  ERROR: {e}")
