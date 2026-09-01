"""
eval/backtest_nosl.py — Spike Fade with NO stop losses.
Only exit via: safety exit (new spike detection) or timeout.
This matches real BOOM/CRASH behavior where SL gets slippaged through.
"""

import sys
import numpy as np
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def load_candles(symbol: str, timeframe: str = "1min") -> list[dict]:
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


def run_nosl_backtest(
    symbol: str,
    tp_pct: float = 0.005,
    max_hold: int = 8,
    risk_pct: float = 0.01,
    spike_mult: float = 3.0,
    entry_delay: int = 1,
) -> dict:
    """
    Spike Fade with NO stop loss.
    
    Exit logic:
      1. TP hit → close at TP level
      2. Safety exit: new spike detected against position → close immediately
      3. Timeout: after max_hold bars → close at market
    
    This is the REALISTIC approach for BOOM/CRASH.
    """
    candles = load_candles(symbol, "1min")
    n = len(candles)
    
    closes = np.array([c['close'] for c in candles], dtype=float)
    highs = np.array([c['high'] for c in candles], dtype=float)
    lows = np.array([c['low'] for c in candles], dtype=float)
    opens = np.array([c['open'] for c in candles], dtype=float)
    ranges = highs - lows
    
    is_crash = "crash" in symbol.lower()
    
    # Median range (100-bar lookback)
    lookback = 100
    median_range = np.full(n, 0.01)
    for i in range(lookback, n):
        window = ranges[max(0, i - lookback):i]
        median_range[i] = np.median(window)
    
    # State
    equity = 10000.0
    peak_equity = 10000.0
    in_position = False
    position_dir = 0
    entry_price = 0.0
    entry_bar = 0
    bars_held = 0
    position_size = 0
    requires_new_spike = True
    
    # Spike tracking
    last_spike_bar = -999
    bars_since_spike = 999
    spike_bars = []
    
    # Stats
    trades = []
    total_trades = 0
    wins = 0
    losses = 0
    total_pnl = 0
    safety_exits = 0
    tp_exits = 0
    timeout_exits = 0
    spikes_detected = 0
    
    slippage_pct = 0.00011
    
    for i in range(lookback + 1, n):
        bar_range = ranges[i - 1]
        med = median_range[i - 1]
        if med <= 0:
            med = 0.01
        
        # Detect spike on previous bar
        is_spike = bar_range > spike_mult * med
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
            
            # === SAFETY EXIT: New spike against our position ===
            if is_spike and med > 0:
                close_now = closes[i]
                against = False
                if position_dir == 1 and close_now < entry_price:
                    against = True  # Long, spike down
                if position_dir == -1 and close_now > entry_price:
                    against = True  # Short, spike up
                
                if against:
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
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    safety_exits += 1
                    
                    in_position = False
                    requires_new_spike = True
                    position_dir = 0
                    continue
            
            # === TP CHECK ===
            if position_dir == 1:  # Long
                if highs[i] - entry_price >= tp_pct * entry_price:
                    exit_price = entry_price * (1 + tp_pct)
                    pnl_pct = tp_pct
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "tp"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    tp_exits += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
            else:  # Short
                if entry_price - lows[i] >= tp_pct * entry_price:
                    exit_price = entry_price * (1 - tp_pct)
                    pnl_pct = tp_pct
                    pnl_dollars = position_size * pnl_pct
                    equity += pnl_dollars
                    peak_equity = max(peak_equity, equity)
                    trades.append({"entry": entry_price, "exit": exit_price, "pnl": pnl_dollars, "bars_held": bars_held, "reason": "tp"})
                    total_trades += 1; total_pnl += pnl_dollars
                    if pnl_dollars > 0: wins += 1
                    else: losses += 1
                    tp_exits += 1
                    in_position = False; requires_new_spike = True; position_dir = 0
                    continue
            
            # === TIMEOUT (no SL!) ===
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
                timeout_exits += 1
                in_position = False; requires_new_spike = True; position_dir = 0
                continue
        
        # === ENTRY ===
        if not in_position and bars_since_spike == entry_delay and last_spike_bar >= 0:
            # Cluster filter
            cluster_count = sum(1 for sb in spike_bars if i - sb <= 10 and sb != last_spike_bar)
            if cluster_count >= 2:
                continue
            
            if requires_new_spike:
                continue
            
            if median_range[i] <= 0:
                continue
            
            dir = 1 if is_crash else -1
            
            # Position sizing (no SL, so risk is based on TP distance)
            risk_dollars = equity * risk_pct
            tp_dist = tp_pct * closes[i]
            position_size = risk_dollars / tp_dist if tp_dist > 0 else 0
            if position_size <= 0:
                continue
            
            if dir == 1:
                entry_price = closes[i] * (1 - slippage_pct)
            else:
                entry_price = closes[i] * (1 + slippage_pct)
            
            in_position = True
            requires_new_spike = True
            position_dir = dir
            bars_held = 0
    
    # Close open position at end
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
    
    # Average win vs average loss ratio
    wl_ratio = abs(avg_win / avg_loss) if avg_loss != 0 else 99
    
    return {
        "symbol": symbol,
        "trades": total_trades,
        "win_rate": wr / 100,
        "profit_factor": pf,
        "total_return_pct": ret,
        "max_dd_pct": max_dd,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "wl_ratio": wl_ratio,
        "tp_exits": tp_exits,
        "timeout_exits": timeout_exits,
        "safety_exits": safety_exits,
        "spikes_detected": spikes_detected,
    }


if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  SPIKE FADE — NO STOP LOSS BACKTEST")
    print("  (Only safety exit + timeout)")
    print("=" * 72)
    
    configs = [
        {"tp": 0.004, "hold": 8, "label": "TP=0.4% Hold=8"},
        {"tp": 0.005, "hold": 8, "label": "TP=0.5% Hold=8"},
        {"tp": 0.006, "hold": 10, "label": "TP=0.6% Hold=10"},
        {"tp": 0.008, "hold": 12, "label": "TP=0.8% Hold=12"},
        {"tp": 0.010, "hold": 15, "label": "TP=1.0% Hold=15"},
    ]
    
    symbols = ["BOOM500", "CRASH500", "BOOM1000", "CRASH1000"]
    
    for sym in symbols:
        print(f"\n{'='*72}")
        print(f"  {sym} — NO STOP LOSS")
        print(f"{'='*72}")
        
        best_ret = -999
        best_cfg = None
        
        for cfg in configs:
            r = run_nosl_backtest(sym, tp_pct=cfg["tp"], max_hold=cfg["hold"])
            n = r['trades']
            wr = r['win_rate'] * 100
            ret = r['total_return_pct']
            pf = r['profit_factor']
            dd = r['max_dd_pct']
            tp = r['tp_exits']
            to = r['timeout_exits']
            se = r['safety_exits']
            wl = r['wl_ratio']
            
            marker = ""
            if ret > best_ret:
                best_ret = ret
                best_cfg = cfg["label"]
                marker = " <-- BEST"
            
            print(f"  {cfg['label']:20s}  {n:>4}T  WR={wr:>5.1f}%  Ret={ret:>+7.2f}%  PF={pf:>5.2f}  DD={dd:.2f}%  W/L={wl:.1f}x  [TP:{tp} TO:{to} SE:{se}]{marker}")
        
        # Best config details
        if best_cfg:
            print(f"\n  Best: {best_cfg}")
    
    # Also run SpikeCatcher v2 (no SL version)
    print(f"\n{'='*72}")
    print(f"  SPIKECATCHER v2 — NO STOP LOSS")
    print(f"{'='*72}")
    
    for sym in symbols:
        try:
            from eval.backtest_spikecatcher_v2 import run_spikecatcher_v2_backtest
            r = run_spikecatcher_v2_backtest(sym, use_safety_exit=True)
            n = r['trades']
            wr = r['win_rate'] * 100
            ret = r['total_return_pct']
            pf = r['profit_factor']
            dd = r['max_dd_pct']
            se = r['safety_exits']
            eb = r['exit_breakdown']
            print(f"  {sym:12s}  {n:>5}T  WR={wr:>5.1f}%  Ret={ret:>+7.2f}%  PF={pf:>5.2f}  DD={dd:.2f}%  Safety={se}  ExitBreakdown={eb}")
        except Exception as e:
            print(f"  {sym}: ERROR {e}")
