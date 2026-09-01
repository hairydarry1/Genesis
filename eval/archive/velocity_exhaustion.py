"""
eval/velocity_exhaustion.py — Velocity Exhaustion Engine for Crash 300

The core insight:
  - Crash 300 spikes are ALL DOWN (driven by the algorithm)
  - Before a spike, price climbs steadily upward (the "buildup")
  - The climb ACCELERATES near the end (velocity exhaustion)
  - When velocity exceeds a threshold → SPIKE IS IMMINENT
  - We can use this to:
    1. EXIT LONG before the spike hits (protect profits)
    2. ENTER SHORT to catch the spike (if confident)

Architecture:
  Tick Stream → Velocity Calc → Exhaustion Detection → Signal

Entry Logic (Buy The Dip):
  1. M5 EMA 10 > EMA 30 (uptrend)
  2. M1 spike DOWN detected (3x median range)
  3. Wait 2 bars after spike
  4. Enter LONG (buy the retrace)
  5. TP: 2x median range | SL: 1x median range

Exit Logic (Velocity Protection):
  1. Track tick velocity (price change rate)
  2. When downward velocity exceeds 5x normal → SPIKE WARNING
  3. Exit LONG immediately (before spike hits)
  4. After spike → re-enter LONG (buy the retrace again)

Run:
    python eval/velocity_exhaustion.py
    python eval/velocity_exhaustion.py --symbol CRASH300 --backtest
    python eval/velocity_exhaustion.py --live
"""

import sys
import time
import json
import argparse
import numpy as np
from pathlib import Path
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

# ─── Configuration ──────────────────────────────────────────────────────────

@dataclass
class VelocityConfig:
    """Configuration for the velocity exhaustion engine."""
    
    # Spike detection (M1 bar level)
    spike_threshold: float = 3.0     # Range > 3x median = spike
    spike_cooldown_bars: int = 2     # Wait N bars after spike before entry
    spike_cluster_window: int = 10   # Bars to consider as cluster
    spike_cluster_min: int = 3       # Min spikes in window to be a cluster
    
    # Velocity detection (tick level)
    velocity_window: int = 5         # Ticks to calculate velocity
    velocity_spike_mult: float = 5.0 # Velocity > Nx normal = spike warning (relaxed from 3)
    velocity_down_threshold: float = -0.01  # Minimum downward velocity to trigger
    
    # Entry
    entry_bars_after_spike: int = 2  # Bars to wait after spike before entry
    min_momentum_bars: int = 3       # Min bars of upward momentum before entry
    
    # Exit
    tp_range_mult: float = 2.0       # TP = Nx median range
    sl_range_mult: float = 1.0       # SL = Nx median range
    max_hold_bars: int = 9           # Max bars to hold position
    velocity_exit_enabled: bool = True  # Use velocity to exit early
    
    # Risk
    risk_pct: float = 0.02           # 2% risk per trade
    dollar_per_point: float = 0.20   # $0.20 per point (Crash 300)
    
    # Timeframe
    symbol: str = "Crash 300 Index"
    symbol_code: str = "CRASH300"


@dataclass
class VelocityState:
    """Current velocity tracking state."""
    tick_history: list = field(default_factory=list)  # Recent tick prices
    velocities: list = field(default_factory=list)    # Recent velocities
    avg_velocity: float = 0.0                         # Average upward velocity
    max_velocity: float = 0.0                         # Max recent velocity
    velocity_trend: str = "normal"                    # normal, accelerating, exhausted
    spike_warning: bool = False                       # Spike detected via velocity
    ticks_since_spike: int = 0                        # Ticks since last spike
    last_spike_tick: int = 0                          # Last spike tick index


@dataclass
class TradeState:
    """Current trade state."""
    in_position: bool = False
    direction: int = 0  # 1=long, -1=short
    entry_price: float = 0.0
    entry_tick: int = 0
    bars_held: int = 0
    tp_price: float = 0.0
    sl_price: float = 0.0
    position_size: float = 0.0
    unrealized_pnl: float = 0.0


# ─── Velocity Exhaustion Detector ──────────────────────────────────────────

class VelocityExhaustionEngine:
    """
    Detects spike probability by analyzing tick velocity patterns.
    
    Key insight: Before a Crash 300 spike (down), price accelerates upward.
    The acceleration is detectable at the tick level 2-5 ticks before the spike.
    
    When velocity exceeds threshold → spike is imminent → exit LONG.
    """
    
    def __init__(self, config: VelocityConfig = None):
        self.cfg = config or VelocityConfig()
        self.velocity = VelocityState()
        self.trade = TradeState()
        self.equity = 0.0
        self.initial_equity = 0.0
        self.peak_equity = 0.0
        
        # Bar-level state
        self._bars: list = []
        self._ranges: list = []
        self._median_range: float = 1.0
        self._spike_indices: list = []
        self._last_spike_bar: int = -999
        self._bars_since_spike: int = 999
        
        # Tick-level state
        self._tick_count: int = 0
        self._last_tick_price: float = 0.0
        self._tick_prices: list = []
        self._tick_velocities: list = []
        self._consecutive_upticks: int = 0
        self._consecutive_downticks: int = 0
        
        # EMA state for M5 trend
        self._m5_ema_fast: float = 0.0
        self._m5_ema_slow: float = 0.0
        self._m5_ema_fast_prev: float = 0.0
        self._m5_ema_slow_prev: float = 0.0
        
        # Performance tracking
        self.trades: list = []
        self.equity_curve: list = []
        self.velocity_warnings: int = 0
        self.velocity_exits: int = 0
        self.spikes_detected: int = 0
        self.spikes_caught: int = 0
        
    def update_ema(self, price: float, period: int, current_ema: float) -> float:
        """Update EMA with new price."""
        k = 2.0 / (period + 1)
        return price * k + current_ema * (1 - k)
    
    def detect_spike(self, bar: dict) -> bool:
        """Detect if bar is a spike."""
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        bar_range = h - l
        
        # Track range
        self._ranges.append(bar_range)
        if len(self._ranges) > 100:
            self._ranges.pop(0)
        self._median_range = float(np.median(self._ranges)) if self._ranges else 1.0
        
        is_spike = bar_range > self.cfg.spike_threshold * self._median_range
        # Crash 300: spikes are DOWN (c < o)
        is_crash_spike = is_spike and c < o
        
        return is_crash_spike
    
    def simulate_tick_velocity(self, bar: dict) -> dict:
        """
        Simulate tick velocity from bar OHLC data.
        
        In real tick data, we'd see individual price changes.
        From bars, we simulate ~10 ticks by interpolating OHLC.
        The key insight: before a spike, the bar shows rapid upward
        movement (long upper wick, or strong close above open).
        """
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        
        # Simulate tick prices: open → high → close (crash spike = open → high → crash down)
        # For Crash 300: normal bars climb, spike bars crash
        tick_prices = []
        if c >= o:  # Normal bar (or small spike up)
            # Climb up: open → close
            for frac in np.linspace(0, 1, 5):
                tick_prices.append(o + (c - o) * frac)
        else:  # Spike down
            # First climb (the exhaustion), then crash
            peak = h if h > o else o
            tick_prices.append(o)
            tick_prices.append(peak)  # Exhaustion peak
            tick_prices.append(c)     # Crash
        
        # Feed ticks to velocity engine
        result = {}
        for tp in tick_prices:
            result = self.update_velocity(tp)
        
        return result
    
    def update_velocity(self, tick_price: float) -> dict:
        """
        Update velocity tracking and detect exhaustion.
        
        Returns dict with:
          - velocity: current velocity
          - avg_velocity: average recent velocity
          - warning: bool, spike imminent
          - acceleration: rate of velocity change
        """
        self._tick_count += 1
        
        if self._last_tick_price == 0:
            self._last_tick_price = tick_price
            return {"velocity": 0, "avg_velocity": 0, "warning": False, "acceleration": 0}
        
        # Calculate velocity (price change per tick)
        velocity = tick_price - self._last_tick_price
        self._last_tick_price = tick_price
        
        # Track velocity history
        self._tick_velocities.append(velocity)
        if len(self._tick_velocities) > 50:
            self._tick_velocities.pop(0)
        
        # Track tick prices
        self._tick_prices.append(tick_price)
        if len(self._tick_prices) > 100:
            self._tick_prices.pop(0)
        
        # Track consecutive ticks
        if velocity > 0:
            self._consecutive_upticks += 1
            self._consecutive_downticks = 0
        elif velocity < 0:
            self._consecutive_downticks += 1
            self._consecutive_upticks = 0
        else:
            self._consecutive_upticks = 0
            self._consecutive_downticks = 0
        
        # Calculate rolling velocity stats
        window = min(self.cfg.velocity_window, len(self._tick_velocities))
        if window < 3:
            return {"velocity": velocity, "avg_velocity": 0, "warning": False, "acceleration": 0}
        
        recent_vels = self._tick_velocities[-window:]
        avg_velocity = np.mean(recent_vels)
        std_velocity = np.std(recent_vels) if len(recent_vels) > 1 else 1.0
        max_velocity = np.max(np.abs(recent_vels))
        
        # Calculate acceleration (change in velocity)
        if len(self._tick_velocities) >= 2 * window:
            prev_vels = self._tick_velocities[-2*window:-window]
            prev_avg = np.mean(prev_vels)
            acceleration = avg_velocity - prev_avg
        else:
            acceleration = 0
        
        # Store state
        self.velocity.avg_velocity = avg_velocity
        self.velocity.max_velocity = max_velocity
        
        # Detect velocity exhaustion (spike imminent)
        # Conditions:
        # 1. Strong upward climb (consecutive upticks)
        # 2. Velocity accelerating (positive acceleration)
        # 3. Velocity exceeds normal (high magnitude)
        spike_warning = False
        self.velocity.velocity_trend = "normal"
        
        if self._consecutive_upticks >= self.cfg.velocity_window * 2:
            # Extended uptrend — check for exhaustion
            if abs(acceleration) > abs(avg_velocity) * 1.5 and avg_velocity > 0:
                # Strong acceleration (not just normal fluctuation)
                spike_warning = True
                self.velocity.velocity_trend = "accelerated"
            
            if max_velocity > abs(avg_velocity) * self.cfg.velocity_spike_mult:
                # Velocity spike detected
                spike_warning = True
                self.velocity.velocity_trend = "exhausted"
            
            if self._consecutive_upticks >= self.cfg.velocity_window * 4:
                # Very extended uptrend — exhaustion very likely
                spike_warning = True
                self.velocity.velocity_trend = "exhausted"
        
        self.velocity.spike_warning = spike_warning
        if spike_warning:
            self.velocity_warnings += 1
        
        # Track ticks since spike
        self.velocity.ticks_since_spike += 1
        
        return {
            "velocity": velocity,
            "avg_velocity": avg_velocity,
            "max_velocity": max_velocity,
            "warning": spike_warning,
            "trend": self.velocity.velocity_trend,
            "consecutive_upticks": self._consecutive_upticks,
            "consecutive_downticks": self._consecutive_downticks,
            "acceleration": acceleration,
            "ticks_since_spike": self.velocity.ticks_since_spike,
        }
    
    def on_bar(self, bar: dict, bar_idx: int, equity: float) -> Optional[dict]:
        """
        Process a new M1 bar.
        Returns trade signal or None.
        """
        self._bars.append(bar)
        if len(self._bars) > 200:
            self._bars.pop(0)
        
        # Detect spike
        is_spike = self.detect_spike(bar)
        
        # Simulate tick velocity from bar data
        vel_data = self.simulate_tick_velocity(bar)
        
        if is_spike:
            self._last_spike_bar = bar_idx
            self._bars_since_spike = 0
            self._spike_indices.append(bar_idx)
            self.spikes_detected += 1
            self.velocity.ticks_since_spike = 0
            self.velocity.last_spike_tick = self._tick_count
        else:
            self._bars_since_spike += 1
        
        # Update EMA for trend detection
        c = bar['close']
        self._m5_ema_fast = self.update_ema(c, 10, self._m5_ema_fast if self._m5_ema_fast else c)
        self._m5_ema_slow = self.update_ema(c, 30, self._m5_ema_slow if self._m5_ema_slow else c)
        
        # Position management
        if self.trade.in_position:
            self.trade.bars_held += 1
            return self._check_exit(bar, bar_idx, equity)
        
        # ── ENTRY LOGIC ──
        # Only enter if:
        # 1. We just detected a spike (bars_since_spike == entry_bars_after_spike)
        # 2. M5 trend is UP (EMA fast > slow)
        # 3. Not in a cluster (3+ spikes within 10 bars)
        # 4. No velocity warning (spike not imminent)
        
        if self._bars_since_spike != self.cfg.entry_bars_after_spike:
            return None
        
        if self._last_spike_bar < 0:
            return None
        
        # Check M5 trend
        if self._m5_ema_fast <= self._m5_ema_slow:
            return None  # Downtrend — don't buy
        
        # Check cluster
        recent_spikes = sum(1 for s in self._spike_indices 
                           if bar_idx - s <= self.cfg.spike_cluster_window and s != self._last_spike_bar)
        if recent_spikes >= self.cfg.spike_cluster_min - 1:
            return None  # Cluster — too many spikes
        
        # Check velocity warning
        if self.cfg.velocity_exit_enabled and self.velocity.spike_warning:
            return None  # Spike imminent — don't enter
        
        # Check momentum (price should be recovering)
        if len(self._bars) >= self.cfg.min_momentum_bars:
            recent_bars = self._bars[-self.cfg.min_momentum_bars:]
            recovering = all(recent_bars[j]['close'] >= recent_bars[j-1]['close'] 
                           for j in range(1, len(recent_bars)))
            if not recovering:
                return None
        
        # ── ENTER LONG (buy the dip) ──
        entry_price = c
        tp = self.cfg.tp_range_mult * self._median_range
        sl = self.cfg.sl_range_mult * self._median_range
        
        risk_dollars = equity * self.cfg.risk_pct
        position_size = risk_dollars / (sl * self.cfg.dollar_per_point) if sl > 0 else 0
        
        self.trade.in_position = True
        self.trade.direction = 1  # LONG
        self.trade.entry_price = entry_price
        self.trade.entry_tick = self._tick_count
        self.trade.bars_held = 0
        self.trade.tp_price = entry_price + tp
        self.trade.sl_price = entry_price - sl
        self.trade.position_size = position_size
        
        return {
            "action": "open",
            "strategy": "velocity_dip",
            "side": "long",
            "entry_price": entry_price,
            "tp": tp,
            "sl": sl,
            "position_size": position_size,
            "velocity_trend": self.velocity.velocity_trend,
        }
    
    def _check_exit(self, bar: dict, bar_idx: int, equity: float) -> Optional[dict]:
        """Check exit conditions."""
        h, l, c = bar['high'], bar['low'], bar['close']
        entry = self.trade.entry_price
        
        # Calculate unrealized PnL
        unrealized_pts = (c - entry) if self.trade.direction == 1 else (entry - c)
        self.trade.unrealized_pnl = unrealized_pts * self.cfg.dollar_per_point * self.trade.position_size
        
        exit_pts = None
        reason = None
        
        # 1. Take Profit
        if self.trade.direction == 1 and h >= self.trade.tp_price:
            exit_pts = self.trade.tp_price - entry
            reason = "tp"
        elif self.trade.direction == -1 and l <= self.trade.tp_price:
            exit_pts = entry - self.trade.tp_price
            reason = "tp"
        
        # 2. Stop Loss
        if exit_pts is None:
            if self.trade.direction == 1 and l <= self.trade.sl_price:
                exit_pts = self.trade.sl_price - entry
                reason = "sl"
            elif self.trade.direction == -1 and h >= self.trade.sl_price:
                exit_pts = entry - self.trade.sl_price
                reason = "sl"
        
        # 3. Velocity exit (spike warning while in position)
        if exit_pts is None and self.cfg.velocity_exit_enabled:
            if self.velocity.spike_warning and self.trade.direction == 1:
                # Velocity says spike is imminent — exit before it hits
                exit_pts = unrealized_pts
                reason = "velocity_exit"
                self.velocity_exits += 1
            elif self._consecutive_downticks >= 3 and self.trade.direction == 1:
                # Downward acceleration detected
                exit_pts = unrealized_pts
                reason = "velocity_exit"
                self.velocity_exits += 1
        
        # 4. Spike against us
        if exit_pts is None:
            is_spike = self.detect_spike(bar)
            if is_spike and self.trade.direction == 1:
                # Spike down while long — exit
                exit_pts = unrealized_pts
                reason = "spike_against"
                self.spikes_caught += 1
        
        # 5. Timeout
        if exit_pts is None and self.trade.bars_held >= self.cfg.max_hold_bars:
            exit_pts = unrealized_pts
            reason = "timeout"
        
        if exit_pts is None:
            return None
        
        # Record trade
        pnl_dollars = exit_pts * self.cfg.dollar_per_point * self.trade.position_size
        self.equity += pnl_dollars
        
        trade_record = {
            "entry": entry,
            "exit": c,
            "pnl_pts": exit_pts,
            "pnl_dollars": pnl_dollars,
            "reason": reason,
            "bars_held": self.trade.bars_held,
            "velocity_trend": self.velocity.velocity_trend,
            "velocity_warnings": self.velocity_warnings,
        }
        self.trades.append(trade_record)
        self.equity_curve.append(self.equity)
        
        if self.equity > self.peak_equity:
            self.peak_equity = self.equity
        
        # Reset trade state
        self.trade.in_position = False
        self.trade.direction = 0
        self.trade.entry_price = 0.0
        self.trade.bars_held = 0
        self.trade.unrealized_pnl = 0.0
        
        return {
            "action": "close",
            "strategy": "velocity_dip",
            "exit_price": c,
            "exit_reason": reason,
            "pnl_pts": exit_pts,
            "pnl_dollars": pnl_dollars,
            "equity": self.equity,
        }
    
    def get_stats(self) -> dict:
        """Get performance statistics."""
        if not self.trades:
            return {"trades": 0}
        
        wins = [t for t in self.trades if t['pnl_dollars'] > 0]
        losses = [t for t in self.trades if t['pnl_dollars'] <= 0]
        
        total_pnl = sum(t['pnl_dollars'] for t in self.trades)
        avg_win = np.mean([t['pnl_dollars'] for t in wins]) if wins else 0
        avg_loss = abs(np.mean([t['pnl_dollars'] for t in losses])) if losses else 0
        
        # Profit factor
        gross_profit = sum(t['pnl_dollars'] for t in wins)
        gross_loss = abs(sum(t['pnl_dollars'] for t in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        # Max drawdown
        peak = self.initial_equity
        max_dd = 0
        for eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0
            max_dd = max(max_dd, dd)
        
        # Exit reason breakdown
        reasons = {}
        for t in self.trades:
            r = t['reason']
            if r not in reasons:
                reasons[r] = {"count": 0, "pnl": 0, "wins": 0}
            reasons[r]["count"] += 1
            reasons[r]["pnl"] += t['pnl_dollars']
            if t['pnl_dollars'] > 0:
                reasons[r]["wins"] += 1
        
        return {
            "trades": len(self.trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": len(wins) / len(self.trades),
            "total_pnl": total_pnl,
            "return_pct": total_pnl / self.initial_equity * 100,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "profit_factor": pf,
            "max_drawdown": max_dd,
            "equity_curve": self.equity_curve,
            "exit_reasons": reasons,
            "velocity_warnings": self.velocity_warnings,
            "velocity_exits": self.velocity_exits,
            "spikes_detected": self.spikes_detected,
            "spikes_caught": self.spikes_caught,
        }


# ─── Backtest ──────────────────────────────────────────────────────────────

def run_backtest(symbol: str = "CRASH300", max_bars: int = 50000, equity: float = 200.0,
                 velocity_exit: bool = True, use_tick_data: bool = True):
    """
    Backtest the velocity exhaustion strategy.
    
    If tick data is available, uses it for velocity detection.
    Otherwise, simulates velocity from bar data.
    """
    from eval.smc_spike_hunter import load_candles
    
    print(f"\n{'='*72}")
    print(f"  VELOCITY EXHAUSTION BACKTEST — {symbol}")
    print(f"{'='*72}")
    
    # Load M1 data
    m1 = load_candles(symbol, "1min")[-max_bars:]
    print(f"  Loaded {len(m1)} M1 bars")
    
    # Load tick data if available
    tick_data = None
    if use_tick_data:
        try:
            tick_path = Path("data") / f"ticks_{symbol.lower()}.json"
            if tick_path.exists():
                tick_data = json.loads(tick_path.read_text())
                print(f"  Loaded {len(tick_data)} ticks for velocity analysis")
        except Exception:
            pass
    
    # Strategy configs
    configs = [
        ("With Velocity Exit", VelocityConfig(
            symbol_code=symbol, velocity_exit_enabled=True,
            entry_bars_after_spike=2, tp_range_mult=2.0, sl_range_mult=1.0)),
        ("Without Velocity Exit", VelocityConfig(
            symbol_code=symbol, velocity_exit_enabled=False,
            entry_bars_after_spike=2, tp_range_mult=2.0, sl_range_mult=1.0)),
        ("TP=3 SL=1 With Velocity", VelocityConfig(
            symbol_code=symbol, velocity_exit_enabled=True,
            entry_bars_after_spike=2, tp_range_mult=3.0, sl_range_mult=1.0)),
    ]
    
    results = {}
    
    for name, cfg in configs:
        print(f"\n  --- {name} ---")
        engine = VelocityExhaustionEngine(cfg)
        engine.equity = equity
        engine.initial_equity = equity
        engine.peak_equity = equity
        
        for i, bar in enumerate(m1):
            result = engine.on_bar(bar, i, engine.equity)
            if result and result['action'] == 'close':
                pass  # Trade recorded internally
        
        stats = engine.get_stats()
        results[name] = stats
        
        if stats['trades'] > 0:
            print(f"    Trades:     {stats['trades']}")
            print(f"    WR:         {stats['win_rate']:.1%}")
            print(f"    PF:         {stats['profit_factor']:.2f}")
            print(f"    PnL:        ${stats['total_pnl']:+.2f} ({stats['return_pct']:+.1f}%)")
            print(f"    MaxDD:      {stats['max_drawdown']:.1%}")
            print(f"    Avg Win:    ${stats['avg_win']:.2f}")
            print(f"    Avg Loss:   ${stats['avg_loss']:.2f}")
            print(f"    Vel Warns:  {stats['velocity_warnings']}")
            print(f"    Vel Exits:  {stats['velocity_exits']}")
            print(f"    Spikes:     {stats['spikes_detected']}")
            print(f"    Spike Hits: {stats['spikes_caught']}")
            
            # Exit reason breakdown
            print(f"    --- Exit Reasons ---")
            for reason, data in sorted(stats['exit_reasons'].items(), 
                                       key=lambda x: -x[1]['count']):
                wr = data['wins'] / data['count'] if data['count'] > 0 else 0
                print(f"      {reason:20s}: {data['count']:4d} trades, "
                      f"WR={wr:.0%}, PnL=${data['pnl']:+.2f}")
    
    # Comparison table
    print(f"\n  {'='*72}")
    print(f"  COMPARISON")
    print(f"  {'='*72}")
    print(f"  {'Config':<30s} {'Trades':>7s} {'WR%':>7s} {'PF':>7s} {'PnL':>12s} {'MaxDD':>7s}")
    print(f"  {'-'*70}")
    
    for name, stats in results.items():
        if stats['trades'] > 0:
            print(f"  {name:<30s} {stats['trades']:>7d} "
                  f"{stats['win_rate']:>6.1%} {stats['profit_factor']:>7.2f} "
                  f"${stats['total_pnl']:>+10.2f} {stats['max_drawdown']:>6.1%}")
    
    return results


# ─── Live Tick Stream ──────────────────────────────────────────────────────

def run_live_tick_stream(symbol: str = "Crash 300 Index", duration_seconds: int = 300):
    """
    Stream live ticks and demonstrate velocity detection.
    """
    try:
        import MetaTrader5 as mt5
    except ImportError:
        print("  MetaTrader5 not installed. Run: pip install MetaTrader5")
        return
    
    print(f"\n{'='*72}")
    print(f"  LIVE VELOCITY EXHAUSTION MONITOR — {symbol}")
    print(f"{'='*72}")
    
    if not mt5.initialize():
        print(f"  MT5 init failed: {mt5.last_error()}")
        return
    
    info = mt5.account_info()
    if info:
        print(f"  Connected: {info.login} @ {info.server}")
    
    engine = VelocityExhaustionEngine(VelocityConfig(symbol=symbol))
    
    print(f"  Monitoring tick velocity for {duration_seconds}s...")
    print(f"  Looking for velocity exhaustion patterns...")
    print(f"  Press Ctrl+C to stop\n")
    
    start_time = time.time()
    last_tick_time = 0
    tick_count = 0
    warning_count = 0
    
    try:
        while time.time() - start_time < duration_seconds:
            tick = mt5.symbol_info_tick(symbol)
            if tick is None:
                time.sleep(0.1)
                continue
            
            current_time = time.time()
            if current_time - last_tick_time < 0.3:  # 300ms between ticks
                continue
            last_tick_time = current_time
            tick_count += 1
            
            # Update velocity
            vel_data = engine.update_velocity(tick.bid)
            
            # Status line
            trend = vel_data['trend']
            velocity = vel_data['velocity']
            avg_vel = vel_data['avg_velocity']
            consecutive = vel_data['consecutive_upticks']
            since_spike = vel_data['ticks_since_spike']
            
            # Color-coded output
            if vel_data['warning']:
                warning_count += 1
                print(f"  ⚠️  SPIKE WARNING  | vel={velocity:+.4f} avg={avg_vel:+.4f} "
                      f"| upticks={consecutive} since_spike={since_spike} "
                      f"| TOTAL WARNINGS: {warning_count}")
            elif trend == "accelerated":
                print(f"  ⚡ ACCELERATING   | vel={velocity:+.4f} avg={avg_vel:+.4f} "
                      f"| upticks={consecutive} since_spike={since_spike}")
            elif tick_count % 10 == 0:  # Print status every 10 ticks
                print(f"  📊 Normal         | vel={velocity:+.4f} avg={avg_vel:+.4f} "
                      f"| upticks={consecutive} since_spike={since_spike}")
    
    except KeyboardInterrupt:
        print(f"\n  Stopped after {tick_count} ticks")
    
    finally:
        mt5.shutdown()
    
    # Summary
    print(f"\n  {'='*72}")
    print(f"  VELOCITY MONITOR SUMMARY")
    print(f"  {'='*72}")
    print(f"  Ticks monitored:   {tick_count}")
    print(f"  Spike warnings:    {warning_count}")
    print(f"  Warning rate:      {warning_count/tick_count*100:.1f}%" if tick_count > 0 else "  N/A")


# ─── Main ──────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Velocity Exhaustion Engine for Crash 300")
    parser.add_argument("--symbol", default="CRASH300", help="Symbol code (CRASH300, CRASH500)")
    parser.add_argument("--backtest", action="store_true", help="Run backtest")
    parser.add_argument("--live", action="store_true", help="Run live tick stream")
    parser.add_argument("--max-bars", type=int, default=50000, help="Max bars for backtest")
    parser.add_argument("--equity", type=float, default=200.0, help="Starting equity")
    parser.add_argument("--no-tick-data", action="store_true", help="Skip tick data loading")
    parser.add_argument("--duration", type=int, default=300, help="Live stream duration (seconds)")
    args = parser.parse_args()
    
    if args.live:
        symbol_map = {
            "CRASH300": "Crash 300 Index",
            "CRASH500": "Crash 500 Index",
            "BOOM300": "Boom 300 Index",
            "BOOM500": "Boom 500 Index",
        }
        symbol = symbol_map.get(args.symbol, args.symbol)
        run_live_tick_stream(symbol, duration_seconds=args.duration)
    else:
        run_backtest(
            symbol=args.symbol,
            max_bars=args.max_bars,
            equity=args.equity,
            use_tick_data=not args.no_tick_data,
        )


if __name__ == "__main__":
    main()
