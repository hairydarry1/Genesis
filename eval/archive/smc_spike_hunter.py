"""
eval/smc_spike_hunter.py — SMC-Based Spike Hunter Strategy for BOOM/CRASH.

Based on research from 12M+ tick analysis and professional strategies:

Entry Logic (3-factor confluence):
  1. H1 Order Block: Price must be in a supply/demand zone on H1
  2. M1 RSI Extreme: RSI 10 for Boom buys, RSI 90 for Crash sells
  3. M1 EMA Crossover: 9-EMA crosses 18-SMA for timing confirmation

Exit Logic:
  - TP: First or second spike (exit after spike)
  - SL: 0.5%+ beyond entry (accounts for slippage gaps)
  - Timeout: Exit after max_hold bars

Risk Management:
  - 0.5-1% risk per trade (NOT 10%)
  - Wide SL to survive spike slippage
  - Only 3-5 trades per session (quality over quantity)
"""

import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))


# ============================================================================
# Data Loading
# ============================================================================

def load_candles(symbol: str, timeframe: str) -> list[dict]:
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


# ============================================================================
# Indicator Calculations
# ============================================================================

def compute_rsi(closes_arr: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI using numpy for speed."""
    n = len(closes_arr)
    if n < period + 1:
        return np.full(n, 50.0)

    deltas = np.diff(closes_arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    rsi = np.full(n, 50.0)
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    for i in range(period, len(deltas)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi[i + 1] = 100.0
        else:
            rsi[i + 1] = 100 - (100 / (1 + avg_gain / avg_loss))

    return rsi


def compute_ema(data_arr: np.ndarray, period: int) -> np.ndarray:
    """Compute EMA using numpy."""
    n = len(data_arr)
    if n < period:
        return np.full(n, np.nan)
    multiplier = 2 / (period + 1)
    ema = np.full(n, np.nan)
    ema[period - 1] = np.mean(data_arr[:period])
    for i in range(period, n):
        ema[i] = data_arr[i] * multiplier + ema[i - 1] * (1 - multiplier)
    return ema


def compute_sma(data_arr: np.ndarray, period: int) -> np.ndarray:
    """Compute SMA using numpy cumsum."""
    n = len(data_arr)
    if n < period:
        return np.full(n, np.nan)
    cumsum = np.cumsum(data_arr)
    sma = np.full(n, np.nan)
    sma[period - 1:] = (cumsum[period - 1:] - np.concatenate([[0], cumsum[:-period]])) / period
    return sma


# ============================================================================
# H1 Order Block Detection
# ============================================================================

@dataclass
class OrderBlock:
    """Supply or demand zone on H1."""
    zone_type: str    # "demand" (support) or "supply" (resistance)
    high: float
    low: float
    mid: float
    origin_epoch: int
    strength: int     # number of touches
    valid: bool = True


def detect_order_blocks(h1_candles: list, threshold_mult: float = 2.5) -> list[OrderBlock]:
    """
    Detect supply/demand zones on H1 timeframe.

    Order Block = last opposing candle before a large move:
      - Demand: Last bearish candle before a bullish spike
      - Supply: Last bullish candle before a bearish crash
    """
    if len(h1_candles) < 5:
        return []

    zones = []
    closes = [c['close'] for c in h1_candles]
    median_move = np.median([abs(closes[i] - closes[i-1]) for i in range(1, len(closes))])
    large_move_threshold = median_move * threshold_mult

    for i in range(2, len(h1_candles) - 1):
        prev = h1_candles[i - 1]
        curr = h1_candles[i]
        nxt = h1_candles[i + 1]

        prev_range = prev['high'] - prev['low']
        if prev_range == 0:
            continue

        # Demand zone: bearish candle followed by large bullish move
        if curr['close'] < curr['open']:  # bearish candle
            next_move = nxt['close'] - curr['close']
            if next_move > large_move_threshold:
                zones.append(OrderBlock(
                    zone_type="demand",
                    high=curr['open'],   # top of bearish candle
                    low=curr['low'],     # bottom of bearish candle
                    mid=(curr['open'] + curr['low']) / 2,
                    origin_epoch=curr['epoch'],
                    strength=1,
                ))

        # Supply zone: bullish candle followed by large bearish move
        if curr['close'] > curr['open']:  # bullish candle
            next_move = curr['close'] - nxt['close']
            if next_move > large_move_threshold:
                zones.append(OrderBlock(
                    zone_type="supply",
                    high=curr['high'],   # top of bullish candle
                    low=curr['close'],   # bottom of bullish candle
                    mid=(curr['high'] + curr['close']) / 2,
                    origin_epoch=curr['epoch'],
                    strength=1,
                ))

    # Merge overlapping zones
    merged = []
    for z in zones:
        merged_flag = False
        for m in merged:
            if z.zone_type == m.zone_type:
                # Check overlap
                if z.low <= m.high and z.high >= m.low:
                    m.low = min(m.low, z.low)
                    m.high = max(m.high, z.high)
                    m.mid = (m.high + m.low) / 2
                    m.strength += 1
                    merged_flag = True
                    break
        if not merged_flag:
            merged.append(z)

    return merged


def price_in_zone(price: float, zones: list, margin: float = 0.001) -> OrderBlock | None:
    """Check if price is within any order block zone."""
    for z in zones:
        if z.valid and z.low - margin <= price <= z.high + margin:
            return z
    return None


# ============================================================================
# Spike Detection on M1
# ============================================================================

def detect_spikes(closes: list, median_window: int = 100, spike_mult: float = 3.0) -> list:
    """Detect spikes on M1 data. Returns list of (index, direction, size)."""
    spikes = []
    if len(closes) < median_window + 2:
        return spikes

    pct_changes = []
    for i in range(1, len(closes)):
        pct = (closes[i] - closes[i-1]) / closes[i-1] if closes[i-1] != 0 else 0
        pct_changes.append(abs(pct))

    for i in range(median_window, len(pct_changes)):
        recent = pct_changes[i - median_window: i]
        median_vol = max(float(np.median(recent)), 0.0001)
        current = pct_changes[i]
        if current / median_vol >= spike_mult:
            direction = 1 if closes[i + 1] > closes[i] else -1
            spikes.append((i + 1, direction, current / median_vol))

    return spikes


# ============================================================================
# SMC Spike Hunter Strategy
# ============================================================================

@dataclass
class SMCHunterConfig:
    """Configuration for SMC Spike Hunter."""
    # RSI
    rsi_period: int = 14
    rsi_extreme_high: float = 90.0   # Crash sell trigger
    rsi_extreme_low: float = 10.0    # Boom buy trigger

    # EMA crossover
    ema_fast: int = 9
    ema_slow: int = 18

    # Risk management
    sl_pct: float = 0.005        # 0.5% SL (wide to survive slippage)
    risk_per_trade: float = 0.01  # 1% of equity per trade
    max_hold: int = 60           # Max bars to hold (M1)
    max_trades_per_session: int = 5

    # Spike detection
    spike_mult: float = 3.0
    spike_exit_count: int = 2     # Exit after N spikes against us

    # Zone parameters
    zone_margin: float = 0.001    # Price tolerance for zone entry
    zone_threshold_mult: float = 2.5  # H1 move must be Nx median to create zone

    # Entry slippage (modeled from research: avg 0.011% per trade)
    entry_slippage_pct: float = 0.00011  # 0.011% slippage on entry

    # Trade size scaling (anti-martingale: increase after wins)
    use_scaling: bool = True
    scale_win_streak: int = 2     # Increase risk after N consecutive wins
    scale_up_factor: float = 1.5  # Multiply risk by this after streak
    scale_max_risk: float = 0.05  # Never risk more than 5% per trade
    scale_reset_on_loss: bool = True  # Reset to base risk after a loss

    # Trailing profit lock: reduce risk after equity gain to protect profits
    use_profit_lock: bool = True
    profit_lock_start: float = 0.03  # Start protecting after 3% gain
    profit_lock_factor: float = 0.5  # Risk multiplied by this per 3% above threshold
    profit_lock_min_risk: float = 0.002  # Never go below 0.2% risk


class SMCSpikeHunter:
    """
    SMC-based spike hunter for BOOM/CRASH.

    Enters at H1 Order Blocks with RSI extreme + EMA crossover confirmation.
    Exits on first spike in our favor.
    """

    def __init__(self, symbol: str, config: SMCHunterConfig = None,
                 initial_equity: float = 10000.0):
        self.symbol = symbol
        self.cfg = config or SMCHunterConfig()
        self.equity = initial_equity
        self.initial_equity = initial_equity
        self.peak_equity = initial_equity

        # Position
        self.position = 0       # 0=flat, 1=long (Boom), -1=short (Crash)
        self.entry_price = 0.0
        self.bars_held = 0
        self.current_pos_size = 0.0
        self.spikes_against = 0
        self._ema_cross_direction = 0  # +1=bullish cross, -1=bearish cross
        self._ema_cross_bar = -999     # when the last cross happened
        self._rsi_extreme_bar = -999   # when RSI last hit extreme
        self._rsi_extreme_dir = 0      # +1=oversold, -1=overbought

        # M1 data buffers (numpy arrays for speed)
        self._closes_arr = np.array([], dtype=float)
        self._highs_arr = np.array([], dtype=float)
        self._lows_arr = np.array([], dtype=float)
        self._rsi_arr = np.array([], dtype=float)
        self._ema_fast_arr = np.array([], dtype=float)
        self._ema_slow_arr = np.array([], dtype=float)
        self._prev_ema_fast = np.nan
        self._prev_ema_slow = np.nan

        # H1 zones
        self._zones: list[OrderBlock] = []
        self._active_zone = None

        # Win streak tracking for scaling
        self._win_streak = 0
        self._current_risk = self.cfg.risk_per_trade

        # Spike tracking
        self._spike_count = 0
        self._last_spike_bar = -999

        # Stats
        self.trade_log = []
        self.equity_curve = []
        self._trade_counter = 0
        self._session_trades = 0
        self._last_session_epoch = 0
        self._signals_seen = {"rsi": 0, "ema": 0, "zone": 0, "full": 0}

    def set_h1_zones(self, zones: list[OrderBlock]):
        """Set H1 order block zones (computed from H1 data)."""
        self._zones = zones

    def precompute(self, candles: list[dict]):
        """Pre-compute all indicators and price arrays (one-time O(n))."""
        self._closes_arr = np.array([b['close'] for b in candles], dtype=float)
        self._highs_arr = np.array([b['high'] for b in candles], dtype=float)
        self._lows_arr = np.array([b['low'] for b in candles], dtype=float)
        self._rsi_arr = compute_rsi(self._closes_arr, self.cfg.rsi_period)
        self._ema_fast_arr = compute_ema(self._closes_arr, self.cfg.ema_fast)
        self._ema_slow_arr = compute_sma(self._closes_arr, self.cfg.ema_slow)

    def on_bar(self, bar: dict, idx: int) -> dict | None:
        """Process a 1-minute bar at given index."""
        h, l, c = float(bar['high']), float(bar['low']), float(bar['close'])
        epoch = bar.get('epoch', 0)
        n = idx + 1

        result = None

        # Session tracking
        session = epoch // 86400
        if session != self._last_session_epoch:
            self._session_trades = 0
            self._last_session_epoch = session

        # Track EMA crossover FIRST (before entry check)
        curr_f = self._ema_fast_arr[idx] if idx < len(self._ema_fast_arr) else np.nan
        curr_s = self._ema_slow_arr[idx] if idx < len(self._ema_slow_arr) else np.nan
        prev_f = self._prev_ema_fast
        prev_s = self._prev_ema_slow
        if not (np.isnan(curr_f) or np.isnan(curr_s) or np.isnan(prev_f) or np.isnan(prev_s)):
            if prev_f <= prev_s and curr_f > curr_s:
                self._ema_cross_direction = 1  # bullish
                self._ema_cross_bar = idx
            elif prev_f >= prev_s and curr_f < curr_s:
                self._ema_cross_direction = -1  # bearish
                self._ema_cross_bar = idx
        self._prev_ema_fast = curr_f
        self._prev_ema_slow = curr_s

        # Position management
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit(h, l, c, idx)

        # Entry logic
        if self.position == 0 and n >= 30:
            result = self._check_entry(c, h, l, epoch, idx)

        return result

    def _check_entry(self, price: float, high: float, low: float, epoch: int, idx: int) -> dict | None:
        """Check for entry signal."""
        rsi = self._rsi_arr[idx]
        if np.isnan(rsi):
            return None

        # Session trade limit
        if self._session_trades >= self.cfg.max_trades_per_session:
            return None

        # --- Factor 1: H1 Order Block ---
        zone = price_in_zone(price, self._zones, self.cfg.zone_margin)
        if zone is None:
            return None
        self._signals_seen["zone"] += 1

        # --- Factor 2: RSI was recently extreme ---
        is_boom = "boom" in self.symbol.lower()
        is_crash = "crash" in self.symbol.lower()

        # Check if RSI is currently extreme (track it)
        if is_boom and rsi <= self.cfg.rsi_extreme_low:
            self._rsi_extreme_bar = idx
            self._rsi_extreme_dir = 1
        elif is_crash and rsi >= self.cfg.rsi_extreme_high:
            self._rsi_extreme_bar = idx
            self._rsi_extreme_dir = -1

        # Require RSI was extreme within last 15 bars
        bars_since_rsi = idx - self._rsi_extreme_bar
        if bars_since_rsi > 15 or self._rsi_extreme_dir == 0:
            return None

        rsi_signal = self._rsi_extreme_dir
        self._signals_seen["rsi"] += 1

        # --- Factor 3: EMA Crossover just happened ---
        # The cross is our ENTRY trigger — RSI was extreme recently
        if self._ema_cross_bar != idx:
            return None  # Only enter on the exact bar the cross happens
        ema_cross = self._ema_cross_direction
        self._signals_seen["ema"] += 1

        # --- Confirm alignment ---
        # For Boom: demand zone + RSI was recently low + bullish EMA cross → BUY
        # For Crash: supply zone + RSI was recently high + bearish EMA cross → SELL
        if is_boom and rsi_signal == 1 and ema_cross == 1 and zone.zone_type == "demand":
            direction = 1
        elif is_crash and rsi_signal == -1 and ema_cross == -1 and zone.zone_type == "supply":
            direction = -1
        else:
            return None

        # --- All 3 factors aligned! ---
        self._signals_seen["full"] += 1
        self._active_zone = zone

        return self._execute_entry(price, direction, zone, rsi)

    def _execute_entry(self, price: float, direction: int, zone: OrderBlock, rsi: float) -> dict:
        """Open a position with entry slippage modeled."""
        self.position = direction
        # Model entry slippage: on BOOM we enter SHORT (sell) so slippage is upward
        # On CRASH we enter LONG (buy) so slippage is downward
        slippage_pct = self.cfg.entry_slippage_pct
        if direction == -1:  # SHORT entry → worse fill is higher price
            self.entry_price = price * (1 + slippage_pct)
        else:  # LONG entry → worse fill is lower price
            self.entry_price = price * (1 - slippage_pct)
        self.bars_held = 0
        self.spikes_against = 0
        self._spike_count = 0

        # Position sizing with scaling
        # --- Base risk from scaling ---
        if self.cfg.use_scaling and self._win_streak >= self.cfg.scale_win_streak:
            multiplier = self.cfg.scale_up_factor ** (self._win_streak - self.cfg.scale_win_streak + 1)
            self._current_risk = min(self.cfg.risk_per_trade * multiplier, self.cfg.scale_max_risk)
        else:
            self._current_risk = self.cfg.risk_per_trade

        # --- Profit lock: reduce risk after equity gain ---
        if self.cfg.use_profit_lock:
            equity_gain = (self.equity - self.initial_equity) / self.initial_equity
            if equity_gain > self.cfg.profit_lock_start:
                # Reduce risk: each 3% above threshold halves risk
                excess = equity_gain - self.cfg.profit_lock_start
                steps = excess / self.cfg.profit_lock_start  # how many 3% steps
                lock_factor = self.cfg.profit_lock_factor ** steps
                self._current_risk = max(self._current_risk * lock_factor, self.cfg.profit_lock_min_risk)

        risk_dollars = self.equity * self._current_risk
        self.current_pos_size = risk_dollars / self.cfg.sl_pct if self.cfg.sl_pct > 0 else 0

        self._trade_counter += 1
        self._session_trades += 1

        side = "LONG" if direction == 1 else "SHORT"
        return {
            "action": "open",
            "side": side.lower(),
            "direction": direction,
            "entry_price": price,
            "position_size": self.current_pos_size,
            "equity_before": self.equity,
            "trade_id": self._trade_counter,
            "zone": zone.zone_type,
            "rsi": rsi,
            "reasoning": f"SMC {side} | {zone.zone_type} zone | RSI={rsi:.1f}",
        }

    def _check_exit(self, high: float, low: float, price: float, idx: int) -> dict | None:
        """Check exit conditions."""
        if self.position == 1:  # Long (Boom buy)
            # Check for spike against us (crash during our long)
            if idx >= 2 and low < self._lows_arr[idx - 2]:
                self.spikes_against += 1

            if idx >= 2 and high > self._highs_arr[idx - 2]:
                self._spike_count += 1
                if self._spike_count >= 1:
                    return self._close_position(price, "spike_exit", price)

            # SL
            if low <= self.entry_price * (1 - self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 - self.cfg.sl_pct))

            # Spikes against limit
            if self.spikes_against >= self.cfg.spike_exit_count:
                return self._close_position(price, "spike_against", price)

        elif self.position == -1:  # Short (Crash sell)
            if idx >= 2 and high > self._highs_arr[idx - 2]:
                self.spikes_against += 1

            if idx >= 2 and low < self._lows_arr[idx - 2]:
                self._spike_count += 1
                if self._spike_count >= 1:
                    return self._close_position(price, "spike_exit", price)

            # SL
            if high >= self.entry_price * (1 + self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 + self.cfg.sl_pct))

            # Spikes against limit
            if self.spikes_against >= self.cfg.spike_exit_count:
                return self._close_position(price, "spike_against", price)

        # Timeout
        if self.bars_held >= self.cfg.max_hold:
            return self._close_position(price, "timeout", price)

        return None

    def _close_position(self, price: float, reason: str, exit_price: float) -> dict:
        """Close position and record trade with exit slippage."""
        # Apply exit slippage for non-SL exits (SL fills at exact level)
        if reason not in ("sl",):
            slip = self.cfg.entry_slippage_pct * 0.5  # exits have half the slippage
            if self.position == 1:  # LONG exit → sell at slightly lower price
                exit_price *= (1 - slip)
            else:  # SHORT exit → buy at slightly higher price
                exit_price *= (1 + slip)

        if self.position == 1:
            pnl_pct = (exit_price - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - exit_price) / self.entry_price

        pnl_dollars = self.current_pos_size * pnl_pct if self.current_pos_size > 0 else 0
        self.equity += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.equity)
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0

        trade = {
            "trade_id": self._trade_counter,
            "direction": self.position,
            "entry_price": self.entry_price,
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl": pnl_dollars,
            "pnl_pct": pnl_pct,
            "bars_held": self.bars_held,
            "zone": self._active_zone.zone_type if self._active_zone else "none",
            "spikes_against": self.spikes_against,
            "risk_pct": self._current_risk,
            "win_streak": self._win_streak,
        }
        self.trade_log.append(trade)
        self.equity_curve.append({"equity": self.equity, "dd": dd})

        # Track win streak for scaling
        if pnl_dollars > 0:
            self._win_streak += 1
        else:
            if self.cfg.scale_reset_on_loss:
                self._win_streak = 0

        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0
        self.spikes_against = 0
        self._spike_count = 0
        self._active_zone = None

        return {"action": "close", "exit_price": exit_price, "exit_reason": reason, "pnl": pnl_dollars}


# ============================================================================
# Backtest Runner
# ============================================================================

def run_smc_backtest(symbol: str, sl_pct: float = 0.005, risk: float = 0.01,
                     rsi_low: float = 10.0, rsi_high: float = 90.0,
                     max_hold: int = 60) -> dict:
    """Run full SMC spike hunter backtest."""
    # Load all timeframes
    h1 = load_candles(symbol, "60min")
    m1 = load_candles(symbol, "1min")    # Zone threshold: Crash needs tighter zones (2.0), Boom uses default (2.5)
    zone_mult = 2.0 if 'crash' in symbol.lower() else 2.5

    # Build epoch → M1 index mapping
    epoch_to_m1 = {b['epoch']: i for i, b in enumerate(m1)}

    # Initialize strategy
    cfg = SMCHunterConfig(
        sl_pct=sl_pct,
        risk_per_trade=risk,
        rsi_extreme_low=rsi_low,
        rsi_extreme_high=rsi_high,
        max_hold=max_hold,
    )
    strat = SMCSpikeHunter(symbol, config=cfg)
    zones = detect_order_blocks(h1, threshold_mult=zone_mult)
    strat.set_h1_zones(zones)
    strat.precompute(m1)

    # Run through M1 bars
    for i, bar in enumerate(m1):
        strat.on_bar(bar, i)

    # Close any open position
    if strat.position != 0:
        strat._close_position(m1[-1]['close'], "end_of_data", m1[-1]['close'])

    # Results
    trades = strat.trade_log
    n = len(trades)
    if n == 0:
        return {"trades": 0, "zones": len(zones), "signals": strat._signals_seen}

    pnls = [t['pnl'] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / n if n > 0 else 0
    tw = sum(wins); tl = abs(sum(losses))
    pf = tw / tl if tl > 0 else 99
    ret = (strat.equity / strat.initial_equity - 1) * 100
    max_dd = max((e['dd'] for e in strat.equity_curve), default=0) * 100
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    ex = {}
    for t in trades: ex[t['exit_reason']] = ex.get(t['exit_reason'], 0) + 1

    return {
        "symbol": symbol,
        "zones": len(zones),
        "trades": n,
        "win_rate": wr,
        "profit_factor": pf,
        "total_return_pct": ret,
        "max_dd_pct": max_dd,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "exit_breakdown": ex,
        "signals": strat._signals_seen,
        "config": {"sl_pct": sl_pct, "risk": risk, "rsi_low": rsi_low,
                   "rsi_high": rsi_high, "max_hold": max_hold},
    }


# ============================================================================
# Main
# ============================================================================

if __name__ == "__main__":
    print("\n" + "=" * 72)
    print("  SMC SPIKE HUNTER — H1 Zones + RSI 90/10 + EMA Crossover")
    print("=" * 72)

    configs = [
        {"sl": 0.005, "risk": 0.01, "rsi_l": 10, "rsi_h": 90, "hold": 60, "label": "Baseline"},
        {"sl": 0.004, "risk": 0.01, "rsi_l": 10, "rsi_h": 90, "hold": 60, "label": "Tighter SL"},
        {"sl": 0.006, "risk": 0.01, "rsi_l": 10, "rsi_h": 90, "hold": 60, "label": "Wider SL"},
        {"sl": 0.005, "risk": 0.01, "rsi_l": 15, "rsi_h": 85, "hold": 60, "label": "Relaxed RSI"},
        {"sl": 0.005, "risk": 0.01, "rsi_l": 10, "rsi_h": 90, "hold": 30, "label": "Fast exit"},
        {"sl": 0.005, "risk": 0.01, "rsi_l": 10, "rsi_h": 90, "hold": 120, "label": "Patient"},
        {"sl": 0.005, "risk": 0.02, "rsi_l": 10, "rsi_h": 90, "hold": 60, "label": "2% risk"},
    ]

    for sym in ["BOOM500", "CRASH500"]:
        print(f"\n{'='*72}")
        print(f"  {sym}")
        print(f"{'='*72}")

        for cfg in configs:
            r = run_smc_backtest(sym, sl_pct=cfg["sl"], risk=cfg["risk"],
                                  rsi_low=cfg["rsi_l"], rsi_high=cfg["rsi_h"],
                                  max_hold=cfg["hold"])
            n = r['trades']
            if n == 0:
                print(f"  {cfg['label']:15s}  {r['zones']:3d} zones  No trades  Signals: {r['signals']}")
                continue

            ex_str = ' '.join(f'{k}:{v}' for k, v in sorted(r['exit_breakdown'].items()))
            print(f"  {cfg['label']:15s}  {r['zones']:3d} zones  "
                  f"{n:3d}T  WR={r['win_rate']*100:5.1f}%  PF={r['profit_factor']:5.2f}  "
                  f"Ret={r['total_return_pct']:+7.2f}%  DD={r['max_dd_pct']:.2f}%  {ex_str}")

        # Show signal funnel
        r = run_smc_backtest(sym)
        sig = r['signals']
        print(f"\n  Signal Funnel:")
        print(f"    Zone hits:    {sig['zone']}")
        print(f"    RSI signals:  {sig['rsi']}")
        print(f"    EMA crosses:  {sig['ema']}")
        print(f"    Full (entry): {sig['full']}")
        if sig['zone'] > 0:
            print(f"    Zone→RSI:     {sig['rsi']/sig['zone']*100:.1f}%")
        if sig['rsi'] > 0:
            print(f"    RSI→Full:     {sig['full']/sig['rsi']*100:.1f}%")
