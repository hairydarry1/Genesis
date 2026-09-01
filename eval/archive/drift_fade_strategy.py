"""Drift Fade Strategy — Exploit the linear price drift after spikes.

EDGE: After every spike, price drifts LINEARLY in the opposite direction.
  BOOM500: drifts DOWN ~0.057% per bar for 25+ bars
  CRASH500: drifts UP ~0.057% per bar for 25+ bars

Then the next spike RESETS price back.

STRATEGY:
  1. Wait for a spike (detect it)
  2. Count bars since spike
  3. At N bars: enter COUNTER-TRADE (buy BOOM drift, sell CRASH drift)
  4. Exit when next spike occurs OR at fixed TP/SL
  5. The drift is PREDICTABLE and REPEATABLE

This is NOT about predicting the spike — it's about HARVESTING the
predictable drift between spikes.
"""
import sys
sys.path.insert(0, '.')

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class DriftFadeConfig:
    entry_bar: int = 10          # Enter N bars after last spike
    sl_points: float = 5.0       # Stop loss in points
    tp_points: float = 8.0       # Take profit in points
    exit_timeout: int = 20       # Exit after N bars max
    risk_pct: float = 0.02      # Risk per trade (% of equity)
    entry_slippage_pct: float = 0.00011  # 0.011%
    max_hold_bars: int = 30      # Maximum hold time


@dataclass
class Trade:
    entry_bar: int
    entry_price: float
    direction: str  # 'long' or 'short'
    exit_bar: int = 0
    exit_price: float = 0.0
    exit_reason: str = ''
    pnl: float = 0.0
    pnl_pct: float = 0.0
    bars_held: int = 0


class DriftFadeStrategy:
    """Exploit linear price drift between spikes."""
    
    def __init__(self, symbol: str, config: DriftFadeConfig, initial_equity: float = 10000):
        self.symbol = symbol
        self.cfg = config
        self.equity = initial_equity
        self.initial_equity = initial_equity
        
        # Direction: BOOM drifts down (buy), CRASH drifts up (sell)
        self.trade_direction = 'long' if 'BOOM' in symbol else 'short'
        
        # State
        self.last_spike_bar = -1
        self.in_trade = False
        self.current_trade = None
        self.trades = []
        self.bars_since_spike = 0
        
    def detect_spike(self, bar: dict, prev_bar: dict, all_ranges: list) -> bool:
        """Detect if current bar is a spike."""
        if len(all_ranges) < 50:
            return False
        med_range = np.median(all_ranges[-500:]) if len(all_ranges) >= 500 else np.median(all_ranges)
        rng = bar['high'] - bar['low']
        
        if rng <= 3.0 * med_range:
            return False
        
        direction = 'up' if 'BOOM' in self.symbol else 'down'
        if direction == 'up':
            return bar['close'] > bar['open']
        else:
            return bar['close'] < bar['open']
    
    def on_bar(self, bar_idx: int, bar: dict, all_ranges: list):
        """Process each bar."""
        # Detect spike
        is_spike = self.detect_spike(bar, {}, all_ranges)
        
        if is_spike:
            self.last_spike_bar = bar_idx
            self.bars_since_spike = 0
            
            # If in trade, the spike is our EXIT signal
            if self.in_trade and self.current_trade:
                self._close_position(bar_idx, bar['close'], 'spike_exit')
        else:
            self.bars_since_spike += 1
        
        # Check if should enter
        if not self.in_trade and self.last_spike_bar >= 0:
            if self.bars_since_spike == self.cfg.entry_bar:
                self._open_position(bar_idx, bar)
        
        # Check exit conditions for open trade
        if self.in_trade and self.current_trade:
            self._check_exit(bar_idx, bar)
    
    def _open_position(self, bar_idx: int, bar: dict):
        """Open a drift fade position."""
        # Entry price with slippage
        if self.trade_direction == 'long':
            entry_price = bar['close'] * (1 + self.cfg.entry_slippage_pct)
        else:
            entry_price = bar['close'] * (1 - self.cfg.entry_slippage_pct)
        
        # Position size
        risk_dollars = self.equity * self.cfg.risk_pct
        
        self.current_trade = Trade(
            entry_bar=bar_idx,
            entry_price=entry_price,
            direction=self.trade_direction,
        )
        self.in_trade = True
        
        self._risk_dollars = risk_dollars
    
    def _check_exit(self, bar_idx: int, bar: dict):
        """Check TP, SL, and timeout."""
        trade = self.current_trade
        if not trade:
            return
        
        bars_held = bar_idx - trade.entry_bar
        
        if trade.direction == 'long':
            current_pnl = (bar['close'] - trade.entry_price)
            high_pnl = (bar['high'] - trade.entry_price)
            low_pnl = (bar['low'] - trade.entry_price)
            
            # TP hit
            if high_pnl >= self.cfg.tp_points:
                self._close_position(bar_idx, trade.entry_price + self.cfg.tp_points, 'tp')
                return
            
            # SL hit
            if low_pnl <= -self.cfg.sl_points:
                self._close_position(bar_idx, trade.entry_price - self.cfg.sl_points, 'sl')
                return
        else:  # short
            current_pnl = (trade.entry_price - bar['close'])
            high_pnl = (trade.entry_price - bar['low'])
            low_pnl = (trade.entry_price - bar['high'])
            
            # TP hit
            if high_pnl >= self.cfg.tp_points:
                self._close_position(bar_idx, trade.entry_price - self.cfg.tp_points, 'tp')
                return
            
            # SL hit
            if low_pnl <= -self.cfg.sl_points:
                self._close_position(bar_idx, trade.entry_price + self.cfg.sl_points, 'sl')
                return
        
        # Timeout
        if bars_held >= self.cfg.max_hold_bars:
            self._close_position(bar_idx, bar['close'], 'timeout')
            return
    
    def _close_position(self, bar_idx: int, exit_price: float, reason: str):
        """Close current position and record trade."""
        trade = self.current_trade
        if not trade:
            return
        
        if trade.direction == 'long':
            pnl_points = exit_price - trade.entry_price
        else:
            pnl_points = trade.entry_price - exit_price
        
        # Convert points to dollars
        pnl_dollars = (pnl_points / trade.entry_price) * self._risk_dollars * (self.cfg.tp_points / self.cfg.sl_points)
        
        # Simplified: risk_dollars * (pnl_points / sl_points)
        pnl_dollars = self._risk_dollars * (pnl_points / self.cfg.sl_points)
        
        trade.exit_bar = bar_idx
        trade.exit_price = exit_price
        trade.exit_reason = reason
        trade.pnl = pnl_dollars
        trade.pnl_pct = pnl_dollars / self.equity * 100
        trade.bars_held = bar_idx - trade.entry_bar
        
        self.equity += pnl_dollars
        self.trades.append(trade)
        self.in_trade = False
        self.current_trade = None


def run_backtest(symbol: str, candles: pd.DataFrame, config: DriftFadeConfig, 
                 initial_equity: float = 10000) -> dict:
    """Run backtest on M1 candles."""
    strategy = DriftFadeStrategy(symbol, config, initial_equity)
    
    # Precompute ranges
    ranges = (candles['high'] - candles['low']).values
    
    for i in range(len(candles)):
        bar = {
            'open': candles['open'].iloc[i],
            'high': candles['high'].iloc[i],
            'low': candles['low'].iloc[i],
            'close': candles['close'].iloc[i],
        }
        strategy.on_bar(i, bar, ranges[:i+1].tolist())
    
    # Force close any open position
    if strategy.in_trade and strategy.current_trade:
        last_bar = candles.iloc[-1]
        strategy._close_position(len(candles)-1, last_bar['close'], 'end')
    
    # Stats
    trades = strategy.trades
    if not trades:
        return {'symbol': symbol, 'trades': 0, 'pnl': 0, 'win_rate': 0, 'pf': 0}
    
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    
    total_wins = sum(t.pnl for t in wins)
    total_losses = abs(sum(t.pnl for t in losses))
    
    win_rate = len(wins) / len(trades) * 100
    profit_factor = total_wins / max(total_losses, 0.01)
    avg_win = np.mean([t.pnl for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl for t in losses]) if losses else 0
    avg_rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0
    
    # Exit reason breakdown
    exit_reasons = {}
    for t in trades:
        exit_reasons[t.exit_reason] = exit_reasons.get(t.exit_reason, 0) + 1
    
    # Max drawdown
    equity_curve = [initial_equity]
    for t in trades:
        equity_curve.append(equity_curve[-1] + t.pnl)
    equity_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(equity_arr)
    dd = (peak - equity_arr) / peak * 100
    max_dd = dd.max()
    
    return {
        'symbol': symbol,
        'trades': len(trades),
        'wins': len(wins),
        'losses': len(losses),
        'win_rate': win_rate,
        'pnl': sum(t.pnl for t in trades),
        'pnl_pct': (strategy.equity - initial_equity) / initial_equity * 100,
        'profit_factor': profit_factor,
        'avg_win': avg_win,
        'avg_loss': avg_loss,
        'avg_rr': avg_rr,
        'max_drawdown': max_dd,
        'final_equity': strategy.equity,
        'exit_reasons': exit_reasons,
        'avg_bars_held': np.mean([t.bars_held for t in trades]),
        'trades_per_month': len(trades) / 30,
    }


def main():
    import os
    
    configs = {
        'BOOM500': 'data/candles_boom500_1min.parquet',
        'CRASH500': 'data/candles_crash500_1min.parquet',
    }
    
    print(f"\n{'='*80}")
    print(f"  DRIFT FADE STRATEGY — BACKTEST")
    print(f"  Exploit linear price drift between spikes")
    print(f"{'='*80}")
    
    for sym, path in configs.items():
        if not os.path.exists(path):
            print(f"  [SKIP] {sym}: data not found")
            continue
        
        candles = pd.read_parquet(path)
        candles.columns = [c.lower() for c in candles.columns]
        
        print(f"\n  --- {sym} ({len(candles)} bars) ---")
        
        # Test different entry offsets
        best_result = None
        best_config = None
        
        for entry_bar in [5, 8, 10, 12, 15, 20]:
            for sl in [3, 5, 7, 10]:
                for tp in [5, 8, 10, 15]:
                    if tp <= sl:
                        continue
                    
                    cfg = DriftFadeConfig(
                        entry_bar=entry_bar,
                        sl_points=sl,
                        tp_points=tp,
                        max_hold_bars=entry_bar * 3,
                    )
                    
                    result = run_backtest(sym, candles, cfg, initial_equity=10000)
                    
                    if result['trades'] < 10:
                        continue
                    
                    # Score: PF * sqrt(trades) * (1 - max_dd/100)
                    score = result['profit_factor'] * np.sqrt(result['trades']) * (1 - result['max_drawdown']/100)
                    
                    if best_result is None or score > best_result.get('_score', 0):
                        result['_score'] = score
                        best_result = result
                        best_config = cfg
        
        if best_result:
            print(f"\n  BEST CONFIG: entry_bar={best_config.entry_bar}, SL={best_config.sl_points}, TP={best_config.tp_points}")
            print(f"    Trades:     {best_result['trades']} ({best_result['trades_per_month']:.0f}/month)")
            print(f"    Win Rate:   {best_result['win_rate']:.1f}%")
            print(f"    PnL:        ${best_result['pnl']:+.2f} ({best_result['pnl_pct']:+.2f}%)")
            print(f"    PF:         {best_result['profit_factor']:.2f}")
            print(f"    Avg Win:    ${best_result['avg_win']:.4f}")
            print(f"    Avg Loss:   ${best_result['avg_loss']:.4f}")
            print(f"    Avg R:R:    1:{best_result['avg_rr']:.1f}")
            print(f"    Max DD:     {best_result['max_drawdown']:.2f}%")
            print(f"    Avg Hold:   {best_result['avg_bars_held']:.1f} bars")
            print(f"    Exit Reasons: {best_result['exit_reasons']}")
        
        # Also show top 5 configs
        print(f"\n  TOP 5 CONFIGS:")
        all_results = []
        for entry_bar in [5, 8, 10, 12, 15, 20]:
            for sl in [3, 5, 7, 10]:
                for tp in [5, 8, 10, 15]:
                    if tp <= sl:
                        continue
                    cfg = DriftFadeConfig(entry_bar=entry_bar, sl_points=sl, tp_points=tp, max_hold_bars=entry_bar*3)
                    result = run_backtest(sym, candles, cfg, initial_equity=10000)
                    if result['trades'] >= 10:
                        result['_score'] = result['profit_factor'] * np.sqrt(result['trades']) * (1 - result['max_drawdown']/100)
                        all_results.append((cfg, result))
        
        all_results.sort(key=lambda x: x[1]['_score'], reverse=True)
        for i, (cfg, r) in enumerate(all_results[:5]):
            print(f"    #{i+1}: entry={cfg.entry_bar} SL={cfg.sl_points} TP={cfg.tp_points} → "
                  f"WR={r['win_rate']:.0f}% PF={r['profit_factor']:.2f} PnL=${r['pnl']:+.2f} "
                  f"DD={r['max_drawdown']:.2f}% T={r['trades']}")
    
    print(f"\n{'='*80}")


if __name__ == '__main__':
    main()
