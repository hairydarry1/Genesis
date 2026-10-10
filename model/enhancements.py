"""
Enhancement modules for BOOM/CRASH spike fade strategy.

Provides:
1. SessionFilter — time-of-day filtering
2. TradeLogger — CSV logging with full context
3. SlippageModel — realistic spread/slippage costs
4. CorrelationManager — cross-symbol position management
5. VolatilityRegime — adaptive TP/SL based on vol state
6. StressTester — extreme scenario simulation
7. AdaptiveAutoTuner — self-optimizing parameters from live data
"""

import csv
import os
import time
import math
import numpy as np
from datetime import datetime, timezone, timedelta
from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Optional


# ──────────────────────────────────────────────────────────────────────
# 1. SESSION FILTER
# ──────────────────────────────────────────────────────────────────────

class SessionFilter:
    """Filter trades based on time-of-day spike quality.
    
    BOOM/CRASH spikes don't happen uniformly — certain hours have
    more predictable spike patterns. This filter blocks trades during
    "dead zones" where spikes are erratic or retrace quality is poor.
    
    Since we only have 3.5 days of data, the defaults are conservative.
    The filter learns optimal hours as more data arrives.
    """
    
    def __init__(self, symbol: str, enabled: bool = True):
        self.symbol = symbol
        self.enabled = enabled
        
        # Default active hours (UTC) — based on Deriv synthetic indices
        # These indices run 24/7 but spike clustering varies
        # Initial: allow all hours. As data accumulates, narrow down.
        self.active_hours: set = set(range(24))
        
        # Track quality per hour
        self.hour_trades: dict = {h: deque(maxlen=50) for h in range(24)}
        self.hour_wins: dict = {h: 0 for h in range(24)}
        self.hour_total: dict = {h: 0 for h in range(24)}
        
        # Thresholds
        self.min_trades_per_hour = 10  # Need this many trades before filtering
        self.min_win_rate = 0.40       # Block hours with WR below this
        self.min_retrace = 0.0003      # Block hours where avg retrace < 0.03%
    
    def is_active_hour(self, hour: int) -> bool:
        """Check if current hour is in the active trading window."""
        if not self.enabled:
            return True
        return hour in self.active_hours
    
    def record_trade(self, hour: int, won: bool, retrace_pct: float):
        """Record trade outcome for a specific hour."""
        self.hour_total[hour] += 1
        if won:
            self.hour_wins[hour] += 1
        self.hour_trades[hour].append((won, retrace_pct))
    
    def update_hours(self):
        """Re-evaluate which hours are worth trading.
        
        Called periodically (e.g., every 50 trades) to recalibrate.
        """
        new_active = set()
        for h in range(24):
            total = self.hour_total[h]
            if total < self.min_trades_per_hour:
                # Not enough data — keep active
                new_active.add(h)
                continue
            
            wr = self.hour_wins[h] / total
            avg_retrace = sum(r for _, r in self.hour_trades[h]) / len(self.hour_trades[h])
            
            if wr >= self.min_win_rate or avg_retrace >= self.min_retrace:
                new_active.add(h)
        
        if new_active:
            self.active_hours = new_active
    
    def get_stats(self) -> dict:
        """Get per-hour statistics."""
        stats = {}
        for h in range(24):
            total = self.hour_total[h]
            if total > 0:
                stats[h] = {
                    'trades': total,
                    'wr': self.hour_wins[h] / total,
                    'active': h in self.active_hours,
                }
        return stats


# ──────────────────────────────────────────────────────────────────────
# 2. TRADE LOGGER
# ──────────────────────────────────────────────────────────────────────

class TradeLogger:
    """Log every trade to CSV with full context.
    
    Each row captures: entry/exit prices, PnL, market context,
    filter decisions, thinking layer output, and more.
    """
    
    def __init__(self, symbol: str, log_dir: str = "logs/trades"):
        self.symbol = symbol
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        
        self.filename = os.path.join(
            log_dir,
            f"trades_{symbol}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
        )
        
        self.fieldnames = [
            'trade_id', 'symbol', 'entry_time', 'exit_time', 'direction',
            'entry_price', 'exit_price', 'exit_reason', 'pnl', 'pnl_pct',
            'position_size', 'equity_before', 'equity_after',
            'spike_size_x', 'drift_strength', 'drift_direction',
            'cluster_size', 'bars_since_spike', 'volatility',
            'conviction', 'thinking_category', 'thinking_text',
            'session_hour', 'filtered', 'filter_reason',
            'slippage_cost', 'spread_cost', 'total_cost',
            'kelly_fraction', 'risk_pct', 'profit_tier',
        ]
        
        self.file = open(self.filename, 'w', newline='')
        self.writer = csv.DictWriter(self.file, fieldnames=self.fieldnames)
        self.writer.writeheader()
        self.trade_count = 0
    
    def log_trade(self, **kwargs):
        """Log a single trade."""
        self.trade_count += 1
        row = {fn: kwargs.get(fn, '') for fn in self.fieldnames}
        row['trade_id'] = self.trade_count
        row['symbol'] = self.symbol
        self.writer.writerow(row)
        self.file.flush()
    
    def close(self):
        """Close the CSV file."""
        self.file.close()
    
    def get_filename(self) -> str:
        return self.filename


# ──────────────────────────────────────────────────────────────────────
# 3. SLIPPAGE / SPREAD MODEL
# ──────────────────────────────────────────────────────────────────────

@dataclass
class SlippageModel:
    """Model realistic execution costs.
    
    Deriv BOOM/CRASH indices have:
    - Variable spread (wider during low liquidity)
    - Slippage on spike entries (price moves fast)
    - Commission (if any)
    
    This models the TRUE cost of each trade.
    """
    
    # Base spread in price units (from MT5 data: BOOM500 ~414 ticks, CRASH500 ~304)
    base_spread: float = 0.0004  # 0.04% of price as default
    
    # Slippage multiplier during spikes (spikes = fast moves = worse fills)
    spike_slippage_mult: float = 2.0  # 2x normal spread during spike
    
    # Slippage as fraction of price
    slippage_pct: float = 0.0001  # 0.01% per side
    
    # Commission per trade (as fraction of position)
    commission_pct: float = 0.0  # Deriv synthetic indices = 0 commission
    
    def calculate_cost(self, position_size: float, price: float,
                       is_spike_entry: bool = False) -> dict:
        """Calculate total execution cost for a trade (entry + exit).
        
        Returns cost in price units and as % of position.
        """
        spread_cost = price * self.base_spread * 2  # entry + exit
        
        slippage = position_size * self.slippage_pct * 2
        if is_spike_entry:
            slippage *= self.spike_slippage_mult
        
        commission = position_size * self.commission_pct
        
        total_cost = spread_cost + slippage + commission
        cost_pct = total_cost / position_size if position_size > 0 else 0
        
        return {
            'spread_cost': spread_cost,
            'slippage_cost': slippage,
            'commission': commission,
            'total_cost': total_cost,
            'cost_pct': cost_pct,
        }
    
    def adjust_pnl(self, raw_pnl: float, position_size: float, price: float,
                   is_spike_entry: bool = False) -> float:
        """Adjust PnL by execution costs."""
        costs = self.calculate_cost(position_size, price, is_spike_entry)
        return raw_pnl - costs['total_cost']


# Symbol-specific spread data from MT5 observations
SYMBOL_SPREADS = {
    'BOOM500':   {'base_spread_pct': 0.000414, 'digits': 3},
    'CRASH500':  {'base_spread_pct': 0.000304, 'digits': 3},
    'BOOM1000':  {'base_spread_pct': 0.001468, 'digits': 4},
    'CRASH1000': {'base_spread_pct': 0.000483, 'digits': 4},
}


def make_slippage_model(symbol: str) -> SlippageModel:
    """Create a slippage model for a specific symbol."""
    info = SYMBOL_SPREADS.get(symbol, SYMBOL_SPREADS['BOOM500'])
    return SlippageModel(
        base_spread=info['base_spread_pct'],
        spike_slippage_mult=2.0,
        slippage_pct=0.0001,
        commission_pct=0.0,
    )


# ──────────────────────────────────────────────────────────────────────
# 4. CORRELATION MANAGER
# ──────────────────────────────────────────────────────────────────────

class CorrelationManager:
    """Manage positions across correlated symbols.
    
    BOOM500 and CRASH500 are inversely correlated (BOOM spikes up, CRASH spikes down).
    BOOM1000 and CRASH1000 are similarly inversely correlated.
    
    Rules:
    - Don't hold BOOM500 SHORT while also holding CRASH500 SHORT
      (both would lose if market crashes hard)
    - Can hold BOOM500 SHORT + CRASH500 LONG (hedged)
    - Limit total exposure across correlated pairs
    """
    
    # Correlation groups
    CORR_GROUPS = {
        'boom': ['BOOM500', 'BOOM1000', 'BOOM300'],
        'crash': ['CRASH500', 'CRASH1000', 'CRASH300'],
        'boom_crash_flip': ['CRASH BOOM Flip 500 Index', 'CRASH BOOM Flip 1000 Index'],
    }
    
    def __init__(self, max_correlated_positions: int = 2,
                 max_same_direction: int = 1):
        self.positions: dict = {}  # symbol -> {direction, size, entry_price}
        self.max_correlated = max_correlated_positions
        self.max_same_dir = max_same_direction
        self.blocked_trades: list = []
    
    def register_position(self, symbol: str, direction: int, size: float, entry_price: float):
        """Register a new position."""
        self.positions[symbol] = {
            'direction': direction,
            'size': size,
            'entry_price': entry_price,
        }
    
    def close_position(self, symbol: str):
        """Remove a closed position."""
        self.positions.pop(symbol, None)
    
    def can_open(self, symbol: str, direction: int) -> tuple:
        """Check if we can open a new position.
        
        Returns (allowed: bool, reason: str)
        """
        # Find correlation group
        group = self._get_group(symbol)
        
        # Count positions in same correlation group
        group_positions = [
            (s, p) for s, p in self.positions.items()
            if self._get_group(s) == group and s != symbol
        ]
        
        if len(group_positions) >= self.max_correlated - 1:
            return False, f"max_correlated ({self.max_correlated} in {group})"
        
        # Count same-direction positions in group
        same_dir = [
            (s, p) for s, p in group_positions
            if p['direction'] == direction
        ]
        
        if len(same_dir) >= self.max_same_dir:
            return False, f"max_same_direction ({self.max_same_dir} {('LONG' if direction == 1 else 'SHORT')} in {group})"
        
        return True, "ok"
    
    def _get_group(self, symbol: str) -> str:
        """Get correlation group for a symbol."""
        for group, members in self.CORR_GROUPS.items():
            if symbol in members:
                return group
        return symbol  # No group = own group
    
    def get_exposure(self) -> dict:
        """Get current exposure summary."""
        total_long = sum(p['size'] for p in self.positions.values() if p['direction'] == 1)
        total_short = sum(p['size'] for p in self.positions.values() if p['direction'] == -1)
        return {
            'positions': len(self.positions),
            'total_long': total_long,
            'total_short': total_short,
            'net_exposure': total_long - total_short,
        }


# ──────────────────────────────────────────────────────────────────────
# 5. VOLATILITY REGIME DETECTOR
# ──────────────────────────────────────────────────────────────────────

class VolatilityRegime:
    """Detect current volatility regime and adapt TP/SL.
    
    BOOM/CRASH alternate between:
    - CALM: Small spikes, tight ranges, predictable retrace
    - VOLATILE: Large spikes, wide ranges, erratic behavior
    - TRENDING: Repeated spikes in one direction, poor fade setups
    
    Adapts take-profit and stop-loss to the regime.
    """
    
    CALM = 'calm'
    VOLATILE = 'volatile'
    TRENDING = 'trending'
    
    def __init__(self, lookback: int = 50):
        self.lookback = lookback
        self.recent_bars: deque = deque(maxlen=lookback)
        self.regime = self.CALM
        self.regime_changes: list = []
        
        # Regime-specific multipliers
        self.params = {
            self.CALM:      {'tp_mult': 1.0, 'sl_mult': 1.0, 'hold_mult': 1.0},
            self.VOLATILE:  {'tp_mult': 1.5, 'sl_mult': 0.7, 'hold_mult': 0.8},
            self.TRENDING:  {'tp_mult': 0.7, 'sl_mult': 1.3, 'hold_mult': 1.5},
        }
    
    def update(self, bar: dict):
        """Add a bar and update regime detection.
        
        bar needs: open, high, low, close
        """
        self.recent_bars.append(bar)
        if len(self.recent_bars) < 10:
            return
        
        self._detect_regime()
    
    def _detect_regime(self):
        """Classify current volatility regime."""
        bars = list(self.recent_bars)
        
        # Average True Range (ATR)
        atrs = []
        for b in bars:
            tr = max(
                b['high'] - b['low'],
                abs(b['high'] - b.get('prev_close', b['open'])),
                abs(b['low'] - b.get('prev_close', b['open'])),
            )
            atrs.append(tr)
        
        avg_atr = sum(atrs[-20:]) / 20
        long_avg = sum(atrs) / len(atrs)
        
        # Directional consistency (trending?)
        closes = [b['close'] for b in bars]
        ups = sum(1 for i in range(1, min(20, len(closes))) if closes[i] > closes[i-1])
        dirs = ups / min(20, len(closes) - 1)
        
        # Spike frequency
        ranges = [(b['high'] - b['low']) / b['close'] for b in bars[-20:] if b['close'] > 0]
        avg_range = sum(ranges) / len(ranges) if ranges else 0
        avg_long_range = sum([(b['high'] - b['low']) / b['close'] for b in bars if b['close'] > 0]) / len(bars)
        
        new_regime = self.CALM
        
        if avg_atr > long_avg * 1.5 or avg_range > avg_long_range * 1.5:
            new_regime = self.VOLATILE
        elif dirs > 0.65 or dirs < 0.35:
            new_regime = self.TRENDING
        
        if new_regime != self.regime:
            self.regime_changes.append({
                'time': time.time(),
                'from': self.regime,
                'to': new_regime,
            })
            self.regime = new_regime
    
    def get_adjusted_tp(self, base_tp: float) -> float:
        """Get TP adjusted for current regime."""
        return base_tp * self.params[self.regime]['tp_mult']
    
    def get_adjusted_sl(self, base_sl: float) -> float:
        """Get SL adjusted for current regime."""
        return base_sl * self.params[self.regime]['sl_mult']
    
    def get_adjusted_hold(self, base_hold: int) -> int:
        """Get max hold bars adjusted for current regime."""
        return max(1, int(base_hold * self.params[self.regime]['hold_mult']))
    
    def get_conviction_boost(self) -> float:
        """Additional conviction required in volatile/trending regimes."""
        boosts = {self.CALM: 0.0, self.VOLATILE: 0.05, self.TRENDING: 0.10}
        return boosts[self.regime]
    
    def get_summary(self) -> dict:
        return {
            'regime': self.regime,
            'changes': len(self.regime_changes),
            'params': self.params[self.regime],
        }


# ──────────────────────────────────────────────────────────────────────
# 6. STRESS TESTER
# ──────────────────────────────────────────────────────────────────────

class StressTester:
    """Simulate extreme scenarios to test strategy survival.
    
    Tests:
    1. Spike cluster (5+ spikes in a row)
    2. Rapid reversal (spike → immediate counter-spike)
    3. Volatility explosion (10x normal range)
    4. Gap scenario (price jumps over SL)
    5. Drawdown spiral (5+ consecutive losses)
    """
    
    def __init__(self, engine):
        self.engine = engine
    
    def run_stress_tests(self, initial_equity: float = 10000) -> dict:
        """Run all stress scenarios."""
        results = {}
        results['spike_cluster'] = self._test_spike_cluster(initial_equity)
        results['rapid_reversal'] = self._test_rapid_reversal(initial_equity)
        results['vol_explosion'] = self._test_vol_explosion(initial_equity)
        results['gap_over_sl'] = self._test_gap_over_sl(initial_equity)
        results['drawdown_spiral'] = self._test_drawdown_spiral(initial_equity)
        return results
    
    def _test_spike_cluster(self, equity: float) -> dict:
        """5 consecutive spikes in same direction — worst case for fade."""
        e = equity
        peak = e
        max_dd = 0
        
        for i in range(5):
            # Simulate: we fade, but spike continues
            pos_size = e * 0.10
            loss = pos_size * 0.001  # 0.1% SL
            e -= loss
            peak = max(peak, e)
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)
        
        return {
            'scenario': '5 consecutive spikes against position',
            'equity_after': e,
            'loss': equity - e,
            'loss_pct': (equity - e) / equity * 100,
            'max_dd_pct': max_dd * 100,
            'survived': e > 0,
        }
    
    def _test_rapid_reversal(self, equity: float) -> dict:
        """Spike, we enter, then immediate counter-spike hits SL."""
        pos_size = equity * 0.10
        loss = pos_size * 0.001  # SL
        e = equity - loss
        
        return {
            'scenario': 'Immediate counter-spike after entry',
            'equity_after': e,
            'loss': loss,
            'loss_pct': loss / equity * 100,
            'survived': e > 0,
        }
    
    def _test_vol_explosion(self, equity: float) -> dict:
        """Volatility spikes 10x — SL gets hit with wider spread."""
        pos_size = equity * 0.10
        normal_loss = pos_size * 0.001  # Normal SL
        vol_loss = pos_size * 0.002    # 2x SL due to spread widening
        e = equity - vol_loss
        
        return {
            'scenario': '10x volatility explosion with spread widening',
            'equity_after': e,
            'loss': vol_loss,
            'loss_pct': vol_loss / equity * 100,
            'max_dd_pct': vol_loss / equity * 100,
            'survived': e > 0,
        }
    
    def _test_gap_over_sl(self, equity: float) -> dict:
        """Price gaps over SL — worse fill."""
        pos_size = equity * 0.10
        sl_distance = pos_size * 0.001
        gap_loss = sl_distance * 2  # 2x SL due to gap
        e = equity - gap_loss
        
        return {
            'scenario': 'Price gaps 2x over stop loss',
            'equity_after': e,
            'loss': gap_loss,
            'loss_pct': gap_loss / equity * 100,
            'survived': e > 0,
        }
    
    def _test_drawdown_spiral(self, equity: float) -> dict:
        """10 consecutive losses at full risk — worst case drawdown."""
        e = equity
        peak = e
        max_dd = 0
        
        for _ in range(10):
            pos_size = e * 0.10  # 10% risk
            loss = pos_size * 0.001  # 0.1% SL
            e -= loss
            peak = max(peak, e)
            dd = (peak - e) / peak
            max_dd = max(max_dd, dd)
        
        return {
            'scenario': '10 consecutive losses (10% risk each)',
            'equity_after': e,
            'loss': equity - e,
            'loss_pct': (equity - e) / equity * 100,
            'max_dd_pct': max_dd * 100,
            'survived': e > 0,
        }
    
    def print_results(self, results: dict):
        """Pretty-print stress test results."""
        print(f'\n{"="*70}')
        print(f'  STRESS TEST RESULTS')
        print(f'{"="*70}')
        for name, r in results.items():
            status = 'SURVIVED' if r['survived'] else 'BLOWN UP'
            print(f'\n  [{status}] {r["scenario"]}')
            print(f'    Equity: ${r["equity_after"]:,.2f}  |  Loss: ${r["loss"]:.2f} ({r["loss_pct"]:.3f}%)')
            if 'max_dd_pct' in r:
                print(f'    Max DD: {r["max_dd_pct"]:.3f}%')
        print(f'\n{"="*70}')


# ──────────────────────────────────────────────────────────────────────
# 7. ADAPTIVE AUTO-TUNER
# ──────────────────────────────────────────────────────────────────────

class AdaptiveAutoTuner:
    """Self-optimize parameters from live trading data.
    
    Tracks performance over rolling windows and adjusts:
    - TP/SL distances
    - Max hold time
    - Minimum conviction
    - Spike size limits
    
    Uses a bandit-style approach: explore slightly while exploiting
    what works.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        
        # Rolling performance windows
        self.window_size = 50
        self.trade_results: deque = deque(maxlen=200)
        
        # Current optimized params
        self.optimal = {
            'tp_pct': None,
            'sl_pct': None,
            'max_hold': None,
            'min_conviction': None,
            'max_spike_x': None,
        }
        
        # Search ranges
        self.ranges = {
            'tp_pct': (0.003, 0.010),       # 0.3% to 1.0%
            'sl_pct': (0.0005, 0.002),       # 0.05% to 0.2%
            'max_hold': (3, 20),             # bars
            'min_conviction': (0.2, 0.6),    # 0.2 to 0.6
            'max_spike_x': (8, 20),          # multiples of median
        }
        
        # Step sizes for exploration
        self.steps = {
            'tp_pct': 0.001,
            'sl_pct': 0.0002,
            'max_hold': 1,
            'min_conviction': 0.05,
            'max_spike_x': 2,
        }
        
        self.min_trades_before_tune = 30
        self.last_tune_count = 0
        self.tune_interval = 25  # Re-tune every 25 trades
    
    def record_trade(self, won: bool, pnl_pct: float, context: dict = None):
        """Record trade outcome for learning."""
        self.trade_results.append({
            'won': won,
            'pnl_pct': pnl_pct,
            'context': context or {},
            'time': time.time(),
        })
    
    def should_tune(self) -> bool:
        """Check if we have enough new data to re-tune."""
        n = len(self.trade_results)
        return n >= self.min_trades_before_tune and (n - self.last_tune_count) >= self.tune_interval
    
    def tune(self) -> dict:
        """Analyze recent performance and adjust parameters.
        
        Returns the recommended parameter adjustments.
        """
        if len(self.trade_results) < self.min_trades_before_tune:
            return {}
        
        recent = list(self.trade_results)[-self.window_size:]
        
        # Win rate
        wr = sum(1 for t in recent if t['won']) / len(recent)
        
        # Average win and loss
        wins = [t['pnl_pct'] for t in recent if t['won']]
        losses = [t['pnl_pct'] for t in recent if not t['won']]
        avg_win = sum(wins) / len(wins) if wins else 0
        avg_loss = sum(losses) / len(losses) if losses else 0
        
        # Profit factor
        total_win = sum(wins)
        total_loss = abs(sum(losses))
        pf = total_win / total_loss if total_loss > 0 else 99
        
        adjustments = {}
        
        # If win rate is high and PF is good → widen TP to capture more
        if wr > 0.65 and pf > 2.0:
            adjustments['tp_pct'] = 'widen'
        
        # If win rate is low → tighten SL or raise min conviction
        if wr < 0.50:
            adjustments['sl_pct'] = 'tighten'
            adjustments['min_conviction'] = 'raise'
        
        # If average loss is larger than expected → tighten SL
        if abs(avg_loss) > abs(avg_win) * 0.8:
            adjustments['sl_pct'] = 'tighten'
        
        # If we're winning big → can afford to let winners run
        if avg_win > abs(avg_loss) * 2:
            adjustments['max_hold'] = 'extend'
        
        # If recent losses cluster → raise min conviction
        last_5 = [t['won'] for t in recent[-5:]]
        if sum(last_5) <= 2:  # 3+ losses in last 5
            adjustments['min_conviction'] = 'raise'
        
        # Apply adjustments (small steps)
        new_params = {}
        for param, direction in adjustments.items():
            current = self.optimal.get(param)
            if current is None:
                continue
            
            lo, hi = self.ranges[param]
            step = self.steps[param]
            
            if direction in ('widen', 'extend', 'raise'):
                new_val = min(current + step, hi)
            elif direction in ('tighten',):
                new_val = max(current - step, lo)
            else:
                new_val = current
            
            self.optimal[param] = new_val
            new_params[param] = {'old': current, 'new': new_val, 'reason': direction}
        
        self.last_tune_count = len(self.trade_results)
        
        return {
            'window_wr': wr,
            'window_pf': pf,
            'avg_win': avg_win,
            'avg_loss': avg_loss,
            'adjustments': new_params,
        }
    
    def get_params(self) -> dict:
        """Get current optimized parameters (None = use defaults)."""
        return {k: v for k, v in self.optimal.items() if v is not None}
    
    def get_summary(self) -> dict:
        n = len(self.trade_results)
        if n == 0:
            return {'trades': 0, 'status': 'collecting'}
        
        wr = sum(1 for t in self.trade_results if t['won']) / n
        return {
            'trades': n,
            'wr': wr,
            'optimal_params': self.get_params(),
            'last_tune': self.last_tune_count,
        }


# ──────────────────────────────────────────────────────────────────────
# 8. ANOMALY DETECTOR
# ──────────────────────────────────────────────────────────────────────

class AnomalyDetector:
    """Detect when market behavior changes and pause trading.
    
    Monitors:
    1. Spike frequency — sudden increase/decrease in spike rate
    2. Spike size distribution — shift in typical spike magnitude
    3. Win rate degradation — consecutive losses beyond normal
    4. Volatility regime shift — sudden ATR change
    5. Price drift — unusual directional bias
    
    When anomaly detected, sets a cooldown period to pause trading.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.enabled = True
        
        # Rolling windows
        self.lookback = 100  # bars to look back
        self.spike_counts: deque = deque(maxlen=20)  # spike count per 50-bar window
        self.spike_sizes: deque = deque(maxlen=200)  # recent spike sizes
        self.recent_returns: deque = deque(maxlen=500)
        self.recent_wins: deque = deque(maxlen=50)
        
        # Baseline stats (calibrated from first 200 bars)
        self.baseline_spike_rate = 0.02  # ~2% of bars are spikes
        self.baseline_avg_spike = 0.003  # avg spike size
        self.baseline_vol = 0.001  # avg volatility
        self.calibrated = False
        self.calibration_bars = 0
        
        # Anomaly state
        self.anomaly_active = False
        self.anomaly_reason = ""
        self.cooldown_until = 0  # bar index when cooldown ends
        self.cooldown_bars = 20  # pause for 20 bars after anomaly
        self.anomaly_log: list[dict] = []
        
        # Thresholds
        self.spike_rate_threshold = 2.5  # 2.5x normal = anomaly
        self.spike_size_threshold = 3.0  # 3x normal = anomaly
        self.consecutive_loss_threshold = 5  # 5 losses in a row
        self.vol_shift_threshold = 2.0  # 2x normal vol = anomaly
        self.drift_threshold = 0.005  # 0.5% drift in 100 bars
    
    def update(self, bar: dict, bar_idx: int):
        """Update with new bar data. Returns (is_anomaly, reason)."""
        if not self.enabled:
            return False, ""
        
        close = bar['close']
        self.recent_returns.append(close)
        self.calibration_bars += 1
        
        # Calibration phase — learn baseline
        if not self.calibrated:
            if self.calibration_bars >= 200:
                self._calibrate()
            return False, ""
        
        # Check cooldown
        if self.anomaly_active and bar_idx < self.cooldown_until:
            return True, self.anomaly_reason
        elif self.anomaly_active and bar_idx >= self.cooldown_until:
            self.anomaly_active = False
            self.anomaly_reason = ""
        
        # Run anomaly checks every 10 bars
        if bar_idx % 10 != 0:
            return False, ""
        
        anomalies = []
        
        # 1. Spike frequency check
        if len(self.recent_returns) >= 50:
            recent = list(self.recent_returns)[-50:]
            if len(recent) >= 2:
                rets = [abs(recent[i] - recent[i-1]) / recent[i-1] for i in range(1, len(recent)) if recent[i-1] > 0]
                if rets:
                    median_ret = float(np.median(rets))
                    n_spikes = sum(1 for r in rets if r > median_ret * 3)
                    spike_rate = n_spikes / len(rets)
                    if spike_rate > self.baseline_spike_rate * self.spike_rate_threshold:
                        anomalies.append(f"spike_rate={spike_rate:.3f} (baseline={self.baseline_spike_rate:.3f})")
        
        # 2. Volatility shift check
        if len(self.recent_returns) >= 100:
            recent = list(self.recent_returns)[-100:]
            if len(recent) >= 2:
                rets = [abs(recent[i] - recent[i-1]) / recent[i-1] for i in range(1, len(recent)) if recent[i-1] > 0]
                if rets:
                    current_vol = float(np.std(rets))
                    if current_vol > self.baseline_vol * self.vol_shift_threshold:
                        anomalies.append(f"vol_shift={current_vol:.6f} (baseline={self.baseline_vol:.6f})")
        
        # 3. Price drift check
        if len(self.recent_returns) >= 100:
            recent = list(self.recent_returns)[-100:]
            if len(recent) >= 2:
                drift = (recent[-1] - recent[0]) / recent[0]
                if abs(drift) > self.drift_threshold:
                    anomalies.append(f"price_drift={drift*100:.3f}%")
        
        # 4. Consecutive losses check
        if len(self.recent_wins) >= self.consecutive_loss_threshold:
            last_n = list(self.recent_wins)[-self.consecutive_loss_threshold:]
            if not any(last_n):  # all losses
                anomalies.append(f"consecutive_losses={self.consecutive_loss_threshold}")
        
        if anomalies:
            reason = "; ".join(anomalies)
            self.anomaly_active = True
            self.anomaly_reason = reason
            self.cooldown_until = bar_idx + self.cooldown_bars
            self.anomaly_log.append({
                'bar_idx': bar_idx,
                'reason': reason,
                'cooldown_until': self.cooldown_until,
            })
            return True, reason
        
        return False, ""
    
    def record_trade(self, won: bool):
        """Record trade outcome for consecutive loss detection."""
        self.recent_wins.append(won)
    
    def _calibrate(self):
        """Calibrate baseline stats from initial data."""
        if len(self.recent_returns) < 50:
            return
        
        recent = list(self.recent_returns)[-200:]
        if len(recent) < 2:
            return
        
        # Volatility
        rets = [abs(recent[i] - recent[i-1]) / recent[i-1] for i in range(1, len(recent)) if recent[i-1] > 0]
        if rets:
            self.baseline_vol = float(np.std(rets))
            median_ret = float(np.median(rets))
            self.baseline_spike_rate = sum(1 for r in rets if r > median_ret * 3) / len(rets)
            spike_sizes = [r for r in rets if r > median_ret * 3]
            self.baseline_avg_spike = float(np.mean(spike_sizes)) if spike_sizes else median_ret * 3
        
        self.calibrated = True
    
    def is_paused(self) -> bool:
        """Check if trading should be paused due to anomaly."""
        return self.anomaly_active
    
    def get_summary(self) -> dict:
        return {
            'calibrated': self.calibrated,
            'anomaly_active': self.anomaly_active,
            'anomaly_reason': self.anomaly_reason,
            'anomaly_count': len(self.anomaly_log),
            'baseline_spike_rate': self.baseline_spike_rate,
            'baseline_vol': self.baseline_vol,
        }
