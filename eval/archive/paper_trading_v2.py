"""
eval/paper_trading_v2.py — Paper Trading with Auto-Detection.

Supports two trading modes:
  1. ICT Orchestrator (R_75, R_10) — structure-based trading
  2. Spike Fade Engine (BOOM500, CRASH500, BOOM1000, CRASH1000) — cluster-end fading

Usage:
    python -m eval.paper_trading_v2 --symbol R_75        # ICT mode
    python -m eval.paper_trading_v2 --symbol BOOM500     # Spike fade mode
    python -m eval.paper_trading_v2 --symbol CRASH500    # Spike fade mode
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import structlog

from data.deriv_connection import DerivConnection
from model.trade_thinker import (
    AdaptiveLearner, ContextBuilder, MarketContext, TradeOutcome,
)
from model.enhancements import (
    SessionFilter, TradeLogger, SlippageModel, CorrelationManager,
    VolatilityRegime, StressTester, AdaptiveAutoTuner, AnomalyDetector,
    make_slippage_model,
)

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Dashboard WebSocket Pusher
# ---------------------------------------------------------------------------

DASHBOARD_WS_URL = "ws://localhost:8767"


class DashboardPusher:
    """Push trade events to the spike fade dashboard via WebSocket.
    
    Connects to dashboard/spike_fade_server.py as a client and sends
    events (ticks, opens, closes, status updates) for real-time display.
    """

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._ws = None
        self._connected = False
        self._task = None

    async def connect(self):
        """Connect to dashboard server (non-blocking, auto-reconnect)."""
        if not self.enabled:
            return
        try:
            import websockets
            self._ws = await websockets.connect(DASHBOARD_WS_URL)
            self._connected = True
            print(f"  [DASHBOARD] Connected to {DASHBOARD_WS_URL}")
        except Exception as e:
            self._connected = False
            print(f"  [DASHBOARD] Not available ({e}). Dashboard disabled.")
            self.enabled = False

    async def send(self, msg: dict):
        """Send a message to the dashboard."""
        if not self._connected or not self._ws:
            return
        try:
            await self._ws.send(json.dumps(msg, default=str))
        except Exception:
            self._connected = False

    async def close(self):
        """Disconnect from dashboard."""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._connected = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SYMBOL = "R_75"
DEFAULT_HISTORY_BARS = 500
INITIAL_EQUITY = 10000.0

SPIKE_INSTRUMENTS = {"BOOM500", "CRASH500", "BOOM1000", "CRASH1000"}
SPIKE_DIR_MAP = {
    "BOOM500": "up", "BOOM1000": "up",
    "CRASH500": "down", "CRASH1000": "down",
}

# Post-mortem filters per symbol (from ablation study)
# drift: skip fades when drift_strength > 0.4 against direction
# algo: skip fades when spike > 15x median (algorithmic spike)
# cluster: skip fades when cluster >= 3 spikes (trend continuation)
# low_vol: skip fades in low volatility regime
# conviction: skip fades with conviction < 0.3
SYMBOL_FILTERS = {
    "BOOM500": [],                          # No filters — drift naturally aligned
    "CRASH500": [],                         # Insufficient data — neutral
    "BOOM1000": [],                         # Insufficient data — neutral
    "CRASH1000": ["drift", "algo", "cluster"],  # Best: PF 1.56->2.29
}

# Spike fade parameters (backtested optimal)
SPIKE_FADE_CONFIG = {
    "spike_mult": 3.0,        # Spike = 3x median vol
    "cluster_gap": 5,          # Gap <= 5 bars = same cluster
    "large_spike_pct": 0.0016, # Skip spikes > 0.16%
    "entry_delay": 1,          # Enter 1 bar after cluster ends
    "tp_pct": 0.005,           # Take profit 0.5% (backup — most exit by timeout)
    "sl_pct": 0.001,           # Stop loss 0.1% (tight — cut losers fast)
    "max_hold": 8,             # Max hold 8 bars (BOOM500 optimal)
}

# CRASH500 prefers longer hold (retrace slower)
SPIKE_FADE_CONFIG_CRASH = {
    **SPIKE_FADE_CONFIG,
    "max_hold": 12,            # CRASH500 needs more time
}

SPIKE_FADE_CONFIGS = {
    # BOOM500: Default works best (5.0% return, 64% WR, 1.83 PF)
    "BOOM500": SPIKE_FADE_CONFIG,
    # CRASH500: Wide hold works best (2.3% return, 54% WR, 1.55 PF)
    "CRASH500": {**SPIKE_FADE_CONFIG, "max_hold": 15},
    # BOOM1000: Wide hold works best (2.4% return, 60% WR, 1.38 PF)
    "BOOM1000": {**SPIKE_FADE_CONFIG, "max_hold": 15},
    # CRASH1000: Tight TP/SL works best (0.84% return, 82% WR, 1.93 PF)
    "CRASH1000": {**SPIKE_FADE_CONFIG, "tp_pct": 0.003, "sl_pct": 0.001, "max_hold": 5},
}

# MT5 symbol name mapping
MT5_SYMBOL_MAP = {
    "BOOM500": "Boom 500 Index",
    "BOOM1000": "Boom 1000 Index",
    "CRASH500": "Crash 500 Index",
    "CRASH1000": "Crash 1000 Index",
    "BOOM300": "Boom 300 Index",
    "BOOM600": "Boom 600 Index",
    "BOOM900": "Boom 900 Index",
    "CRASH300": "Crash 300 Index",
    "CRASH600": "Crash 600 Index",
    "CRASH900": "Crash 900 Index",
    "BOOM150": "Boom 150 Index",
    "CRASH150": "Crash 150 Index",
}


# ---------------------------------------------------------------------------
# MT5 Tick Data Source
# ---------------------------------------------------------------------------

class MT5TickSource:
    """Real-time tick data from Deriv MT5 via MetaTrader5 Python library."""

    def __init__(self, symbol: str, login: int = 0, password: str = "", server: str = "Deriv-Demo"):
        self.symbol = symbol
        self.mt5_symbol = MT5_SYMBOL_MAP.get(symbol, symbol)
        self.login = login
        self.password = password
        self.server = server
        self._connected = False
        self._mt5 = None

    def connect(self) -> bool:
        """Connect to MT5 terminal."""
        try:
            import MetaTrader5 as mt5
            self._mt5 = mt5
        except ImportError:
            print("  [MT5] MetaTrader5 not installed. Run: python -m pip install MetaTrader5")
            return False

        init_kwargs = {"timeout": 10000}
        if self.login:
            init_kwargs["login"] = self.login
        if self.password:
            init_kwargs["password"] = self.password
        if self.server:
            init_kwargs["server"] = self.server

        if not self._mt5.initialize(**init_kwargs):
            error = self._mt5.last_error()
            print(f"  [MT5] Init failed: {error}")
            return False

        # Enable the symbol
        if not self._mt5.symbol_select(self.mt5_symbol, True):
            print(f"  [MT5] Failed to select {self.mt5_symbol}")
            return False

        info = self._mt5.account_info()
        if info:
            print(f"  [MT5] Connected: {info.login} @ {info.server}")

        self._connected = True
        return True

    def get_tick(self) -> dict | None:
        """Get latest tick from MT5."""
        if not self._connected or not self._mt5:
            return None

        tick = self._mt5.symbol_info_tick(self.mt5_symbol)
        if not tick:
            return None

        return {
            "price": tick.bid,
            "ask": tick.ask,
            "epoch": tick.time,
            "time_msc": tick.time_msc,
            "spread": tick.ask - tick.bid,
        }

    def get_history(self, days: int = 3) -> list[dict]:
        """Get tick history from MT5."""
        if not self._connected or not self._mt5:
            return []

        from datetime import timedelta
        end = datetime.now()
        start = end - timedelta(days=days)

        ticks = self._mt5.copy_ticks_range(self.mt5_symbol, start, end, self._mt5.COPY_TICKS_ALL)
        if ticks is None or len(ticks) == 0:
            return []

        return [{
            "price": float(t["bid"]),
            "epoch": int(t["time"]),
            "time_msc": int(t["time_msc"]),
        } for t in ticks]

    def close(self):
        """Disconnect from MT5."""
        if self._mt5 and self._connected:
            self._mt5.shutdown()
            self._connected = False
            print("  [MT5] Disconnected")


# ---------------------------------------------------------------------------
# Multi-TF Trend Helper
# ---------------------------------------------------------------------------

class TrendTracker:
    """Compute multi-timeframe trends from 1-minute candles."""

    def __init__(self):
        self._closes: deque[float] = deque(maxlen=5000)

    def update(self, close: float):
        self._closes.append(close)

    def trend(self, period: int) -> str:
        if len(self._closes) < period:
            return "flat"
        vals = list(self._closes)[-period:]
        hi, lo = max(vals), min(vals)
        rng = hi - lo
        if rng < 1e-10:
            return "flat"
        pos = (vals[-1] - lo) / rng
        if pos > 0.65:
            return "up"
        elif pos < 0.35:
            return "down"
        return "flat"


# ---------------------------------------------------------------------------
# Spike Fade Engine (BOOM/CRASH)
# ---------------------------------------------------------------------------

class SpikeFadeEngine:
    """
    Detects spikes and fades the LAST spike of each cluster.

    Supports fractional Kelly compounding:
      - risk_pct: fraction of equity to risk per trade (e.g., 0.10 = 10%)
      - Kelly fraction auto-adjusts based on live win rate
      - Equity curve and drawdown tracked per-trade
    """

    def __init__(self, symbol: str, config: dict | None = None, risk_pct: float = 0.10):
        self.symbol = symbol
        self.spike_dir = SPIKE_DIR_MAP[symbol]  # "up" or "down"
        self.fade_direction = -1 if self.spike_dir == "up" else +1  # -1=short, +1=long

        cfg = config or SPIKE_FADE_CONFIGS.get(symbol, SPIKE_FADE_CONFIG)
        self.spike_mult = cfg["spike_mult"]
        self.cluster_gap = cfg["cluster_gap"]
        self.large_spike_pct = cfg["large_spike_pct"]
        self.entry_delay = cfg["entry_delay"]
        self.tp_pct = cfg["tp_pct"]
        self.sl_pct = cfg["sl_pct"]
        self.max_hold = cfg["max_hold"]

        # Compounding / position sizing
        self.base_risk_pct = risk_pct      # original risk level (never changes)
        self.risk_pct = risk_pct           # current risk level (reduced by profit targets)
        self.max_pos_pct = 0.50            # hard cap: never risk more than 50%
        self._kelly_wins = 0               # running count
        self._kelly_losses = 0             # running count
        self._total_pnl_pct = 0.0          # sum of trade PnL %
        self._daily_start_equity = INITIAL_EQUITY  # for daily PnL

        # Profit target tiers: (return_pct, risk_multiplier)
        # When equity hits a tier, risk is multiplied by the factor.
        # Example: [(10, 0.5), (20, 0.25), (30, 0)] means
        #   +10% → 50% risk, +20% → 25% risk, +30% → stop trading
        self._profit_tiers: list[tuple[float, float]] = []
        self._current_tier = -1            # index into _profit_tiers (-1 = no tier hit)
        self._trading_paused = False       # True when max tier hit
        self._tier_log: list[dict] = []    # log of tier transitions

        # State
        self.position = 0       # +1 long, -1 short, 0 flat
        self.entry_price = 0.0
        self.bars_held = 0
        self.current_pos_size = 0.0   # $ at risk this trade
        self.equity = INITIAL_EQUITY
        self.peak_equity = INITIAL_EQUITY
        self.trade_log: list[dict] = []
        self.equity_curve: list[dict] = []  # [{equity, dd, trade_num}]

        # Spike tracking
        self._closes: deque[float] = deque(maxlen=5000)
        self._returns: deque[float] = deque(maxlen=5000)
        self._spike_indices: list[int] = []  # indices into _closes where spikes occurred
        self._pending_entry: int | None = None  # countdown to entry
        self._last_spike_size: float = 0.0
        self._last_spike_price: float = 0.0
        self._cluster_active: bool = False
        self._bars_since_spike: int = 0
        self._ticks_since_spike: int = 0  # Tick-level counter
        self._total_ticks: int = 0  # Total ticks processed

        # Thinking layer
        self.thinker = AdaptiveLearner()
        self.context_builder = ContextBuilder()
        self._entry_context: MarketContext | None = None  # Context at last entry
        self._trade_counter = 0

        # Symbol-specific filters (from ablation study)
        self.active_filters: list[str] = list(SYMBOL_FILTERS.get(symbol, []))
        self._symbol_filtered = 0
        self._symbol_filter_reasons: dict[str, int] = {}

        # ── Enhancement modules ──
        self.session_filter = SessionFilter(symbol, enabled=True)
        self.slippage_model = make_slippage_model(symbol)
        self.vol_regime = VolatilityRegime(lookback=50)
        self.auto_tuner = AdaptiveAutoTuner(symbol)
        self.trade_logger: TradeLogger | None = None  # Created when trading starts
        self.correlation_manager: CorrelationManager | None = None  # Set by MultiPaperTrader

        # Enhancement toggles (set via CLI)
        self.use_session_filter = False   # off by default until we have more data
        self.use_slippage = True          # always model real costs
        self.use_vol_regime = True        # always detect regime
        self.use_auto_tuner = True        # always tune
        self.use_trade_log = True         # always log

        # Vol regime adjusted TP/SL cache
        self._base_tp_pct = self.tp_pct
        self._base_sl_pct = self.sl_pct
        self._base_max_hold = self.max_hold

        # Anomaly detector
        self.anomaly_detector = AnomalyDetector(symbol)
        self.use_anomaly_detector = True
        self._anomaly_paused = False

    def set_profit_tiers(self, tiers: list[tuple[float, float]]):
        """Set profit target tiers.
        
        Args:
            tiers: List of (return_pct, risk_multiplier) sorted ascending.
                   Example: [(10, 0.5), (20, 0.25), (30, 0)]
                   +10% return → 50% risk, +20% → 25%, +30% → stop
        """
        self._profit_tiers = sorted(tiers, key=lambda t: t[0])

    def _check_profit_target(self):
        """Check if equity has hit a profit tier. Adjust risk or pause trading."""
        if not self._profit_tiers or self._trading_paused:
            return

        return_pct = (self.equity / INITIAL_EQUITY - 1) * 100

        # Find the highest tier we've passed
        new_tier = -1
        for i, (target, _) in enumerate(self._profit_tiers):
            if return_pct >= target:
                new_tier = i

        # Only act on NEW tier hits
        if new_tier > self._current_tier:
            self._current_tier = new_tier
            target, mult = self._profit_tiers[new_tier]

            if mult <= 0:
                # Stop trading
                self._trading_paused = True
                self.risk_pct = 0.0
                action = "STOP"
            else:
                # Reduce risk
                self.risk_pct = self.base_risk_pct * mult
                action = f"{mult*100:.0f}% risk"

            entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "profit_target",
                "tier": new_tier + 1,
                "target_pct": target,
                "return_pct": return_pct,
                "action": action,
                "new_risk_pct": self.risk_pct,
                "equity": self.equity,
            }
            self._tier_log.append(entry)
            self.trade_log.append(entry)

    def on_bar(self, candle: dict) -> dict | None:
        """Process a new candle. Returns action dict or None."""
        price = float(candle["close"])
        high = float(candle["high"])
        low = float(candle["low"])
        bar_idx = len(self._closes)

        # Update volatility regime
        if self.use_vol_regime:
            self.vol_regime.update(candle)
            self.tp_pct = self.vol_regime.get_adjusted_tp(self._base_tp_pct)
            self.sl_pct = self.vol_regime.get_adjusted_sl(self._base_sl_pct)
            self.max_hold = self.vol_regime.get_adjusted_hold(self._base_max_hold)

        self._closes.append(price)

        # Compute return
        if len(self._closes) >= 2:
            ret = (self._closes[-1] - self._closes[-2]) / self._closes[-2]
        else:
            ret = 0.0
        self._returns.append(ret)

        result = None

        # --- Position management ---
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit(high, low, price)

        # --- Pending entry ---
        if self._pending_entry is not None and self.position == 0 and not self._trading_paused:
            self._pending_entry -= 1
            if self._pending_entry <= 0:
                result = self._execute_entry(price)
                self._pending_entry = None

        # --- Anomaly detector ---
        if self.use_anomaly_detector:
            is_anomaly, reason = self.anomaly_detector.update(candle, bar_idx)
            if is_anomaly:
                self._anomaly_paused = True
                if not hasattr(self, '_last_anomaly_log') or self._last_anomaly_log != reason:
                    print(f"  [{self.symbol}] ANOMALY: {reason} — pausing {self.anomaly_detector.cooldown_bars} bars")
                    self._last_anomaly_log = reason
                    # Push to dashboard
                    if getattr(self, '_owner_dashboard', None):
                        asyncio.ensure_future(self._owner_dashboard.send({
                            'type': 'filter_alert', 'symbol': self.symbol,
                            'reason': f'ANOMALY: {reason}',
                        }))
            else:
                self._anomaly_paused = False

        # --- Check external control file ---
        try:
            ctrl_path = Path('logs/control.json')
            if ctrl_path.exists():
                ctrl = json.loads(ctrl_path.read_text())
                if ctrl.get('trading_active') is False:
                    self._trading_paused = True
                elif self._trading_paused and ctrl.get('trading_active') is True:
                    self._trading_paused = False
        except Exception:
            pass

        # --- Spike detection (skip if paused) ---
        if self._trading_paused or self._anomaly_paused:
            return result
        if len(self._returns) >= 100:
            abs_returns = np.array(list(self._returns)[-200:])
            median_vol = float(np.median(np.abs(abs_returns)))
            threshold = median_vol * self.spike_mult

            is_spike = False
            if self.spike_dir == "up" and ret > threshold:
                is_spike = True
            elif self.spike_dir == "down" and ret < -threshold:
                is_spike = True

            if is_spike:
                spike_size = abs(ret)
                self._spike_indices.append(bar_idx)
                self._bars_since_spike = 0

                # Check cluster: is this spike close to the previous one?
                if len(self._spike_indices) >= 2:
                    gap = self._spike_indices[-1] - self._spike_indices[-2]
                    if gap <= self.cluster_gap:
                        self._cluster_active = True
                    else:
                        self._cluster_active = False
                else:
                    self._cluster_active = False

                self._last_spike_size = spike_size
                self._last_spike_price = price
            else:
                self._bars_since_spike += 1

                # Check if cluster just ended
                if (self._cluster_active
                        and self._bars_since_spike > self.cluster_gap
                        and self.position == 0):
                    # Cluster ended — check if last spike is fadeable
                    if (self._last_spike_size < self.large_spike_pct
                            and self._pending_entry is None):
                        self._pending_entry = self.entry_delay
                        self._cluster_active = False

                # Also check isolated spikes
                if (not self._cluster_active
                        and self._bars_since_spike > self.cluster_gap
                        and len(self._spike_indices) > 0
                        and self.position == 0
                        and self._pending_entry is None):
                    # Check if the last spike was isolated (gap > cluster_gap from previous)
                    if len(self._spike_indices) >= 2:
                        last_gap = self._spike_indices[-1] - self._spike_indices[-2]
                        is_isolated = last_gap > self.cluster_gap
                    else:
                        is_isolated = True

                    if (is_isolated
                            and self._last_spike_size < self.large_spike_pct
                            and self._bars_since_spike <= self.cluster_gap + 2):
                        self._pending_entry = self.entry_delay

        return result

    def on_tick(self, tick: dict) -> dict | None:
        """Process a single tick (from MT5). Returns action dict or None.
        
        This enables tick-level spike detection and counting.
        """
        price = float(tick["price"])
        tick_idx = len(self._closes)

        self._closes.append(price)

        # Compute return
        if len(self._closes) >= 2:
            ret = (self._closes[-1] - self._closes[-2]) / self._closes[-2]
        else:
            ret = 0.0
        self._returns.append(ret)

        result = None

        # --- Position management (check every tick for precision) ---
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit_tick(price)

        # --- Pending entry ---
        if self._pending_entry is not None and self.position == 0:
            self._pending_entry -= 1
            if self._pending_entry <= 0:
                result = self._execute_entry(price)
                self._pending_entry = None

        # --- Spike detection (tick-level) ---
        if len(self._returns) >= 50:
            abs_returns = np.array(list(self._returns)[-200:])
            median_vol = float(np.median(np.abs(abs_returns)))
            threshold = median_vol * self.spike_mult

            is_spike = False
            if self.spike_dir == "up" and ret > threshold:
                is_spike = True
            elif self.spike_dir == "down" and ret < -threshold:
                is_spike = True

            if is_spike:
                spike_size = abs(ret)
                self._spike_indices.append(tick_idx)
                self._ticks_since_spike = 0

                # Check cluster
                if len(self._spike_indices) >= 2:
                    gap = self._spike_indices[-1] - self._spike_indices[-2]
                    if gap <= self.cluster_gap:
                        self._cluster_active = True
                    else:
                        self._cluster_active = False
                else:
                    self._cluster_active = False

                self._last_spike_size = spike_size
                self._last_spike_price = price
            else:
                self._ticks_since_spike += 1

                # Check if cluster just ended
                if (self._cluster_active
                        and self._ticks_since_spike > self.cluster_gap
                        and self.position == 0):
                    if (self._last_spike_size < self.large_spike_pct
                            and self._pending_entry is None):
                        self._pending_entry = self.entry_delay
                        self._cluster_active = False

                # Isolated spike
                if (not self._cluster_active
                        and self._ticks_since_spike > self.cluster_gap
                        and len(self._spike_indices) > 0
                        and self.position == 0
                        and self._pending_entry is None):
                    if len(self._spike_indices) >= 2:
                        last_gap = self._spike_indices[-1] - self._spike_indices[-2]
                        is_isolated = last_gap > self.cluster_gap
                    else:
                        is_isolated = True

                    if (is_isolated
                            and self._last_spike_size < self.large_spike_pct
                            and self._ticks_since_spike <= self.cluster_gap + 2):
                        self._pending_entry = self.entry_delay

        return result

    def _check_exit_tick(self, price: float) -> dict | None:
        """Check TP/SL at tick-level precision."""
        if self.position == 1:  # Long
            if price >= self.entry_price * (1 + self.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 + self.tp_pct))
            if price <= self.entry_price * (1 - self.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 - self.sl_pct))
        elif self.position == -1:  # Short
            if price <= self.entry_price * (1 - self.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 - self.tp_pct))
            if price >= self.entry_price * (1 + self.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 + self.sl_pct))

        if self.bars_held >= self.max_hold:
            return self._close_position(price, "timeout", price)

        return None

    def _compute_position_size(self, price: float) -> float:
        """Compute position size based on fractional Kelly.
        
        Uses running win rate to dynamically adjust Kelly fraction.
        Falls back to fixed risk_pct if insufficient data.
        """
        total = self._kelly_wins + self._kelly_losses
        if total < 20:
            # Not enough data — use fixed fractional
            return self.equity * self.risk_pct

        wr = self._kelly_wins / total
        avg_win = self._total_pnl_pct / max(self._kelly_wins, 1) if self._kelly_wins > 0 else self.tp_pct
        avg_loss = abs(self._total_pnl_pct / max(self._kelly_losses, 1)) if self._kelly_losses > 0 else self.sl_pct

        # Kelly formula: f* = (W/A) - ((1-W)/B)
        # where W=win_rate, A=avg_loss, B=avg_win
        if avg_win > 0 and avg_loss > 0:
            kelly_f = (wr * avg_win - (1 - wr) * avg_loss) / avg_win
            kelly_f = max(0, min(kelly_f, 1.0))
        else:
            kelly_f = self.risk_pct

        # Use fractional Kelly (0.5x) blended with fixed risk
        blended = (kelly_f * 0.5 + self.risk_pct * 0.5)
        blended = min(blended, self.max_pos_pct)

        return self.equity * blended

    def _execute_entry(self, price: float) -> dict:
        """Open a fade position with compounding position size."""
        # Session filter check
        if self.use_session_filter:
            from datetime import datetime as _dt, timezone as _tz
            hour = _dt.now(_tz.utc).hour
            if not self.session_filter.is_active_hour(hour):
                self._pending_entry = None
                self._entry_context = None
                return None

        # Auto-tuner: apply optimized params if available
        if self.use_auto_tuner and self.auto_tuner.should_tune():
            tune_result = self.auto_tuner.tune()
            params = self.auto_tuner.get_params()
            if 'tp_pct' in params:
                self._base_tp_pct = params['tp_pct']
            if 'sl_pct' in params:
                self._base_sl_pct = params['sl_pct']
            if 'max_hold' in params:
                self._base_max_hold = int(params['max_hold'])

        # Build context BEFORE entering
        self._trade_counter += 1
        ctx = self.context_builder.build(self, bar_idx=len(self._closes))
        ctx.recent_win_rate = self.thinker.analyzer.get_recent_win_rate(20)
        ctx.recent_avg_pnl = float(np.mean(list(self.thinker.analyzer._recent_pnls)[-20:])) if self.thinker.analyzer._recent_pnls else 0
        ctx.consecutive_losses = self.thinker.analyzer.get_consecutive_losses()
        self._entry_context = ctx

        # Check symbol-specific filters (from ablation study)
        for f in self.active_filters:
            skip = False
            if f == "drift" and ctx.drift_direction != "flat" and ctx.drift_strength > 0.4:
                fade_dir = "down" if self.fade_direction == -1 else "up"
                if ctx.drift_direction == fade_dir:
                    skip = True
            elif f == "algo" and ctx.spike_size_vs_median > 15:
                skip = True
            elif f == "cluster" and ctx.cluster_size >= 3:
                skip = True
            elif f == "low_vol" and ctx.vol_regime == "low":
                skip = True
            elif f == "conviction" and ctx.conviction < 0.3:
                skip = True

            if skip:
                self._symbol_filtered += 1
                self._symbol_filter_reasons[f] = self._symbol_filter_reasons.get(f, 0) + 1
                self._pending_entry = None
                self._entry_context = None
                return None

        # Check adaptive filters
        should_enter, reason = self.thinker.should_trade(ctx)
        if not should_enter:
            self._pending_entry = None
            self._entry_context = None
            return None

        self.position = self.fade_direction
        self.entry_price = price
        self.bars_held = 0

        # Position sizing
        self.current_pos_size = self._compute_position_size(price)

        side = "LONG" if self.position == 1 else "SHORT"
        action = {
            "action": "open",
            "side": side.lower(),
            "direction": self.position,
            "entry_price": price,
            "position_size": self.current_pos_size,
            "equity_before": self.equity,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "reasoning": f"Fade {'up' if self.spike_dir == 'up' else 'down'}-spike cluster end (size={self._last_spike_size*100:.3f}%)",
            "spike_size": self._last_spike_size,
            "conviction": ctx.conviction,
            "context_summary": ctx.summary(),
        }
        self.trade_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **action,
        })

        # Push to dashboard
        if getattr(self, '_owner_dashboard', None):
            asyncio.ensure_future(self._owner_dashboard.send({
                "type": "open", "symbol": self.symbol, **action,
            }))

        return action

    def _check_exit(self, high: float, low: float, price: float) -> dict | None:
        """Check TP/SL/timeout exits."""
        if self.position == 1:  # Long
            if high >= self.entry_price * (1 + self.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 + self.tp_pct))
            if low <= self.entry_price * (1 - self.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 - self.sl_pct))
        elif self.position == -1:  # Short
            if low <= self.entry_price * (1 - self.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 - self.tp_pct))
            if high >= self.entry_price * (1 + self.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 + self.sl_pct))

        if self.bars_held >= self.max_hold:
            return self._close_position(price, "timeout", price)

        return None

    def _close_position(self, price: float, reason: str, exit_price: float) -> dict:
        """Close position, compound equity, and record trade."""
        if self.position == 1:
            pnl = (exit_price - self.entry_price)
        else:
            pnl = (self.entry_price - exit_price)

        pnl_pct = pnl / self.entry_price

        # Apply slippage costs
        slippage_info = {'total_cost': 0, 'cost_pct': 0}
        if self.use_slippage and self.current_pos_size > 0:
            slippage_info = self.slippage_model.calculate_cost(
                self.current_pos_size, self.entry_price,
                is_spike_entry=True,
            )
            pnl -= slippage_info['total_cost']
            pnl_pct = pnl / self.entry_price

        # PnL in dollars based on position size
        pnl_dollars = self.current_pos_size * pnl_pct if self.current_pos_size > 0 else pnl

        # Compound equity
        self.equity += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.equity)

        # Update Kelly stats
        self._total_pnl_pct += pnl_pct
        if pnl_pct > 0:
            self._kelly_wins += 1
        else:
            self._kelly_losses += 1

        # Record equity point
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0
        self.equity_curve.append({
            "equity": self.equity,
            "dd": dd,
            "trade_num": len(self.trade_log),
        })

        side = "long" if self.position == 1 else "short"
        result = {
            "action": "close",
            "side": side,
            "entry_price": self.entry_price,
            "exit_price": exit_price,
            "position_size": self.current_pos_size,
            "pnl": pnl_dollars,
            "pnl_pct": pnl_pct,
            "bars_held": self.bars_held,
            "exit_reason": reason,
            "equity": self.equity,
            "drawdown": dd,
        }

        self.trade_log.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **result,
        })

        # Push close to dashboard
        if getattr(self, '_owner_dashboard', None):
            asyncio.ensure_future(self._owner_dashboard.send({
                "type": "close", "symbol": self.symbol, **result,
            }))

        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0
        self.current_pos_size = 0.0

        # Check profit targets after every trade close
        self._check_profit_target()

        # Run post-mortem analysis
        if self._entry_context is not None:
            outcome = TradeOutcome(
                trade_id=self._trade_counter,
                entry_price=self.entry_price if self.entry_price > 0 else result.get("entry_price", 0),
                exit_price=exit_price,
                pnl_pct=pnl_pct,
                pnl_dollars=pnl_dollars,
                direction=self.fade_direction,
                bars_held=self.bars_held,
                exit_reason=reason,
                context=self._entry_context,
            )
            self.thinker.record_trade(outcome)
            self.thinker.maybe_adjust()
            result["diagnosis"] = outcome.diagnosis
            result["category"] = outcome.category
            result["conviction"] = self._entry_context.conviction
            self._entry_context = None

        # ── Enhancement recordings ──

        # Anomaly detector: record trade outcome
        if self.use_anomaly_detector:
            self.anomaly_detector.record_trade(pnl_pct > 0)

        # Session filter: record trade quality for this hour
        if self.use_session_filter:
            from datetime import datetime as _dt, timezone as _tz
            hour = _dt.now(_tz.utc).hour
            won = pnl_pct > 0
            self.session_filter.record_trade(hour, won, abs(pnl_pct))
            if self._trade_counter % 50 == 0:
                self.session_filter.update_hours()

        # Auto-tuner: record trade outcome
        if self.use_auto_tuner:
            ctx_data = {}
            if self._entry_context:
                ctx_data = {
                    'drift': self._entry_context.drift_direction,
                    'spike_x': self._entry_context.spike_size_vs_median,
                    'conviction': self._entry_context.conviction,
                }
            self.auto_tuner.record_trade(pnl_pct > 0, pnl_pct * 100, ctx_data)

        # Trade logger: write to CSV
        if self.use_trade_log and self.trade_logger:
            from datetime import datetime as _dt, timezone as _tz
            self.trade_logger.log_trade(
                entry_time=result.get('timestamp', ''),
                exit_time=_dt.now(_tz.utc).isoformat(),
                direction='long' if self.fade_direction == 1 else 'short',
                entry_price=result.get('entry_price', 0),
                exit_price=exit_price,
                exit_reason=reason,
                pnl=pnl_dollars,
                pnl_pct=pnl_pct * 100,
                position_size=self.current_pos_size,
                equity_before=self.equity - pnl_dollars,
                equity_after=self.equity,
                spike_size_x=getattr(self._entry_context, 'spike_size_vs_median', 0) if self._entry_context else 0,
                drift_strength=getattr(self._entry_context, 'drift_strength', 0) if self._entry_context else 0,
                drift_direction=getattr(self._entry_context, 'drift_direction', 'unknown') if self._entry_context else 'unknown',
                cluster_size=getattr(self._entry_context, 'cluster_size', 0) if self._entry_context else 0,
                bars_since_spike=self._bars_since_spike,
                volatility=float(np.std(list(self._returns)[-50:])) if len(self._returns) >= 50 else 0,
                conviction=result.get('conviction', 0),
                thinking_category=result.get('category', ''),
                thinking_text=result.get('diagnosis', ''),
                slippage_cost=slippage_info.get('total_cost', 0),
                spread_cost=slippage_info.get('spread_cost', 0),
                total_cost=slippage_info.get('total_cost', 0),
                kelly_fraction=self.auto_tuner.get_params().get('min_conviction', 0) if self.use_auto_tuner else 0,
                risk_pct=self.risk_pct,
                profit_tier=self._current_tier + 1 if self._current_tier >= 0 else 0,
            )

        return result

    def get_summary(self) -> dict:
        """Get performance summary with compounding stats."""
        closes = [t for t in self.trade_log if t.get("action") == "close"]
        if not closes:
            return {
                "trades": 0, "pnl": 0, "win_rate": 0, "equity": self.equity,
                "return_pct": 0, "max_dd_pct": 0, "avg_pos_size": 0,
                "kelly_f": 0, "kelly_wr": 0,
            }

        pnls = [t["pnl"] for t in closes]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        pos_sizes = [t.get("position_size", 0) for t in closes]

        # Max drawdown from equity curve
        max_dd = 0
        if self.equity_curve:
            max_dd = max(point["dd"] for point in self.equity_curve)

        # Live Kelly fraction
        total = self._kelly_wins + self._kelly_losses
        kelly_wr = self._kelly_wins / total if total > 0 else 0
        kelly_f = 0
        if total >= 20:
            avg_w = self._total_pnl_pct / max(self._kelly_wins, 1) if self._kelly_wins > 0 else 0
            avg_l = abs(self._total_pnl_pct / max(self._kelly_losses, 1)) if self._kelly_losses > 0 else 0
            if avg_w > 0 and avg_l > 0:
                kelly_f = (kelly_wr * avg_w - (1 - kelly_wr) * avg_l) / avg_w
                kelly_f = max(0, min(kelly_f, 1.0))

        # Profit target info
        current_target = None
        next_target = None
        if self._profit_tiers:
            if self._current_tier >= 0:
                current_target = self._profit_tiers[self._current_tier]
            if self._current_tier + 1 < len(self._profit_tiers):
                next_target = self._profit_tiers[self._current_tier + 1]

        return {
            "trades": len(closes),
            "pnl": sum(pnls),
            "win_rate": len(wins) / len(closes) if closes else 0,
            "avg_win": np.mean(wins) if wins else 0,
            "avg_loss": np.mean(losses) if losses else 0,
            "equity": self.equity,
            "return_pct": (self.equity / INITIAL_EQUITY - 1) * 100,
            "max_dd_pct": max_dd * 100,
            "avg_pos_size": np.mean(pos_sizes) if pos_sizes else 0,
            "kelly_f": kelly_f,
            "kelly_wr": kelly_wr,
            "risk_pct": self.risk_pct,
            "base_risk_pct": self.base_risk_pct,
            "trading_paused": self._trading_paused,
            "current_tier": self._current_tier + 1 if self._current_tier >= 0 else 0,
            "current_target": current_target,
            "next_target": next_target,
            "tier_log": self._tier_log,
        }


# ---------------------------------------------------------------------------
# Paper Trader V2 (Auto-Detecting)
# ---------------------------------------------------------------------------

class PaperTraderV2:
    """
    Paper trading with auto-detection of instrument type.
      - R_75, R_10: ICT Orchestrator
      - BOOM500, CRASH500, BOOM1000, CRASH1000: Spike Fade Engine
    """

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        history_bars: int = DEFAULT_HISTORY_BARS,
        min_confluence: float = 95.0,
        risk_pct: float = 0.10,
        profit_tiers: list[tuple[float, float]] | None = None,
        no_filters: bool = False,
        use_mt5: bool = False,
        mt5_login: int = 0,
        mt5_password: str = "",
        mt5_server: str = "Deriv-Demo",
    ):
        self.symbol = symbol
        self.history_bars = history_bars
        self.is_spike_instrument = symbol in SPIKE_INSTRUMENTS
        self.use_mt5 = use_mt5 and self.is_spike_instrument
        self.risk_pct = risk_pct

        # Select engine
        if self.is_spike_instrument:
            self.engine = SpikeFadeEngine(symbol, risk_pct=risk_pct)
            if profit_tiers:
                self.engine.set_profit_tiers(profit_tiers)
            if no_filters:
                self.engine.active_filters = []
            self.mode = "spike_fade"
        else:
            from model.decision_orchestrator import DecisionOrchestrator
            self.orchestrator = DecisionOrchestrator(
                initial_equity=INITIAL_EQUITY,
                min_confluence=min_confluence,
            )
            self.engine = None
            self.mode = "ict"

        # MT5 tick source (optional)
        self.mt5_source = None
        if self.use_mt5 and self.is_spike_instrument:
            self.mt5_source = MT5TickSource(
                symbol, login=mt5_login, password=mt5_password, server=mt5_server
            )
            self._tick_count = 0
            self._spike_tick_gaps = []  # Track ticks between spikes

        # Shared
        self.trend_tracker = TrendTracker()
        self.candles: list[dict] = []
        self._current_candle_epoch = 0
        self._current_candle: dict | None = None
        self._candle_count = 0

        # Dashboard pusher
        self.dashboard = DashboardPusher(enabled=True)
        if self.is_spike_instrument and self.engine:
            self.engine._owner_dashboard = self.dashboard

    def add_candle(self, candle: dict):
        self.candles.append(candle)
        if len(self.candles) > 2000:
            self.candles = self.candles[-2000:]
        self.trend_tracker.update(float(candle["close"]))

    def handle_tick(self, msg: dict):
        tick = msg.get("tick", {})
        if not tick:
            return

        quote = float(tick.get("quote", 0))
        epoch = int(tick.get("epoch", 0))
        if quote <= 0 or epoch <= 0:
            return

        # Push tick to dashboard (throttled)
        now = time.time()
        last_push = getattr(self, '_last_tick_push', 0)
        if now - last_push > 0.5:
            asyncio.ensure_future(self.dashboard.send({"type": "tick", "symbol": self.symbol, "price": quote}))
            self._last_tick_push = now

        self.trend_tracker.update(quote)
        candle_epoch = (epoch // 60) * 60

        if candle_epoch != self._current_candle_epoch:
            if self._current_candle is not None and self._current_candle["epoch"] > 0:
                self.add_candle(self._current_candle)
                self._candle_count += 1
                self._run_cycle()

            self._current_candle_epoch = candle_epoch
            self._current_candle = {
                "epoch": candle_epoch,
                "open": quote, "high": quote, "low": quote,
                "close": quote, "volume": 0,
            }
        else:
            if self._current_candle is not None:
                self._current_candle["high"] = max(self._current_candle["high"], quote)
                self._current_candle["low"] = min(self._current_candle["low"], quote)
                self._current_candle["close"] = quote

    def _run_cycle(self):
        if not self.candles:
            return

        candle = self.candles[-1]

        if self.mode == "spike_fade":
            result = self.engine.on_bar(candle)
            self._display_spike_status(result)
        else:
            trend_15m = self.trend_tracker.trend(15)
            trend_1h = self.trend_tracker.trend(60)
            result = self.orchestrator.on_bar(
                candle, trend_15min=trend_15m, trend_1h=trend_1h
            )
            self._display_ict_status(result, trend_15m, trend_1h)

    def _display_spike_status(self, result):
        """Display status for spike fade mode with compounding info."""
        if not self.candles:
            return

        candle = self.candles[-1]
        price = candle["close"]
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        eng = self.engine

        pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(eng.position, "?")
        summary = eng.get_summary()
        n_trades = summary["trades"]
        wr = summary["win_rate"] * 100
        ret = summary["return_pct"]
        dd = summary["max_dd_pct"]
        kelly = summary["kelly_f"]

        # Current PnL
        if eng.position != 0 and eng.entry_price > 0:
            pnl_pct = (price - eng.entry_price) / eng.entry_price * eng.position * 100
            if eng.current_pos_size > 0:
                unreal_dollar = eng.current_pos_size * (pnl_pct / 100)
                pnl_str = f" | Unrealized: {pnl_pct:+.3f}% (${unreal_dollar:+.2f}, {eng.bars_held} bars)"
            else:
                pnl_str = f" | Unrealized: {pnl_pct:+.3f}% ({eng.bars_held} bars)"
        else:
            pnl_str = ""

        # Cluster status
        cluster_str = "ACTIVE" if eng._cluster_active else "ended"
        quiet_str = f"{eng._bars_since_spike} bars" if eng._bars_since_spike > 0 else "---"

        # Pending entry
        pending_str = ""
        if eng._pending_entry is not None:
            pending_str = f"  >>> PENDING ENTRY in {eng._pending_entry} bar(s) -- {'SHORT' if eng.fade_direction == -1 else 'LONG'}"

        # Profit target status
        tier = summary["current_tier"]
        next_tgt = summary["next_target"]
        paused = summary["trading_paused"]
        if paused:
            tier_str = f"  |  ✅ TARGET HIT -- Trading paused"
        elif tier > 0 and next_tgt:
            tier_str = f"  |  Tier {tier}: risk={summary['risk_pct']*100:.0f}% → next: +{next_tgt[0]}%"
        elif eng._profit_tiers and next_tgt:
            tier_str = f"  |  Next target: +{next_tgt[0]}% (reduce to {next_tgt[1]*100:.0f}% risk)"
        else:
            tier_str = ""

        lines = [
            f"\n{'='*72}",
            f"  SPIKE FADE v2 @ {ts}  |  {self.symbol}  |  Price: {price:.2f}",
            f"{'='*72}",
            f"  Mode: {'SELL' if eng.fade_direction == -1 else 'BUY'} after {'up' if eng.spike_dir == 'up' else 'down'}-spike clusters",
            f"  Position: {pos_str}  |  Equity: ${eng.equity:,.2f}  |  Return: {ret:+.2f}%  |  DD: {dd:.2f}%{pnl_str}",
            f"  Trades: {n_trades}  |  WR: {wr:.0f}%  |  PnL: ${summary['pnl']:+.2f}",
            f"  Risk: {self.risk_pct*100:.1f}%/trade  |  Kelly: {kelly*100:.0f}%  |  TP={eng.tp_pct*100:.1f}% SL={eng.sl_pct*100:.2f}%{tier_str}",
            f"  Cluster: {cluster_str}  |  Quiet: {quiet_str}",
            f"  Vol: {eng.vol_regime.regime}  |  TP={eng.tp_pct*100:.2f}% SL={eng.sl_pct*100:.3f}% Hold={eng.max_hold}",
        ]

        # Filter status
        if eng.active_filters:
            filters_str = ", ".join(eng.active_filters)
            filtered = eng._symbol_filtered
            lines.append(f"  Filters: {filters_str}  |  Blocked: {filtered} trades")

        if eng.position != 0 and eng.entry_price > 0 and eng.current_pos_size > 0:
            lines.append(f"  Entry: {eng.entry_price:.2f}  |  Size: ${eng.current_pos_size:,.2f}  |  Bars: {eng.bars_held}/{eng.max_hold}")

        if pending_str:
            lines.append(pending_str)

        if result and result.get("action") == "open":
            pos_size = result.get("position_size", 0)
            conv = result.get("conviction", 0)
            ctx_sum = result.get("context_summary", "")
            lines.append(f"  >>> OPENED: {result['side'].upper()} at {result['entry_price']:.2f} (conviction={conv:.2f})")
            lines.append(f"      Size: ${pos_size:,.2f} | {result['reasoning']}")
            lines.append(f"      Context: {ctx_sum}")
        elif result and result.get("action") == "close":
            pnl = result.get("pnl", 0)
            emoji = "+" if pnl > 0 else ""
            eq = result.get("equity", eng.equity)
            diagnosis = result.get("diagnosis", "")
            category = result.get("category", "")
            lines.append(f"  >>> CLOSED: {result['exit_reason']} | PnL: {emoji}${pnl:.2f} | Equity: ${eq:,.2f}")
            if diagnosis:
                lines.append(f"      Think: {diagnosis}")
            if category:
                lines.append(f"      Category: {category}")

        # Show tier hit notification
        if eng._tier_log and eng._tier_log[-1].get("event") == "profit_target":
            last_tier = eng._tier_log[-1]
            if last_tier["return_pct"] > 0:  # Only show if recently triggered
                lines.append(f"  >>> PROFIT TARGET HIT: +{last_tier['return_pct']:.1f}% → {last_tier['action']}")

        lines.append(f"{'='*72}")
        print("\n".join(lines))

        # Push status to dashboard
        if getattr(eng, '_owner_dashboard', None):
            asyncio.ensure_future(eng._owner_dashboard.send({
                "type": "status", "symbol": self.symbol,
                "price": price,
                "position": pos_str,
                "equity": eng.equity,
                "return_pct": ret,
                "drawdown": dd,
                "trades": n_trades,
                "wins": int(n_trades * wr / 100),
                "pnl": summary["pnl"],
                "risk_pct": self.risk_pct,
                "vol_regime": eng.vol_regime.regime,
                "tp_pct": eng.tp_pct,
                "sl_pct": eng.sl_pct,
                "max_hold": eng.max_hold,
                "kelly": kelly,
                "filters_blocked": eng._symbol_filtered,
                "spike_count": len(eng._spike_indices),
                "session_active": eng.session_filter.is_active_hour(datetime.now(timezone.utc).hour) if eng.use_session_filter else True,
                "auto_tune": eng.auto_tuner.get_summary() if eng.use_auto_tuner else {},
                "anomaly": eng.anomaly_detector.get_summary() if eng.use_anomaly_detector else {},
            }))

    def _display_ict_status(self, result, trend_15m, trend_1h):
        """Display status for ICT mode (existing logic)."""
        if not self.candles:
            return

        candle = self.candles[-1]
        price = candle["close"]
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        orch = self.orchestrator

        narrative = orch.narrative_builder.update(
            structures=orch._cached_structures,
            current_price=price,
            bar_idx=len(orch._candles) - 1,
            timestamp=datetime.now(timezone.utc),
        )

        pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(orch.position, "?")
        dd = (orch.peak_equity - orch.equity) / orch.peak_equity * 100 if orch.peak_equity > 0 else 0
        n_trades = len([t for t in orch.trade_log if t["action"] == "close"])
        wins = sum(1 for t in orch.trade_log if t["action"] == "close" and t.get("pnl", 0) > 0)
        wr = (wins / n_trades * 100) if n_trades > 0 else 0

        vol = orch._compute_rolling_vol()
        vol_regime = orch._classify_vol(vol)

        if orch.position != 0 and orch.entry_price > 0:
            pnl_pct = (price - orch.entry_price) / orch.entry_price * orch.position * 100
            pnl_str = f" | Unrealized: {pnl_pct:+.2f}% ({orch.bars_held} bars)"
        else:
            pnl_str = ""

        vol_tp = orch._vol_adjusted_tp() * 100
        vol_sl = orch._vol_adjusted_sl() * 100

        lines = [
            f"\n{'='*72}",
            f"  ICT PAPER TRADING v2 @ {ts}  |  {self.symbol}  |  Price: {price:.2f}",
            f"{'='*72}",
            f"  Phase: {narrative.phase.current_phase.value}  |  Structure: {narrative.structure.trend.value}",
            f"  Bias: {narrative.bias} ({narrative.bias_strength:.0%})",
            f"  Multi-TF: 15m={trend_15m}  |  1h={trend_1h}",
            f"  Vol: {vol_regime} ({vol:.5f})  |  TP={vol_tp:.2f}%  SL={vol_sl:.2f}%",
            f"  Position: {pos_str}  |  Equity: ${orch.equity:.2f}  |  DD: {dd:.1f}%{pnl_str}",
            f"  Trades: {n_trades}  |  WR: {wr:.0f}%  |  Conf: {orch.min_confluence:.0f}",
        ]

        if orch.position != 0 and orch.entry_price > 0:
            be_active = "YES" if orch.sl_breakeven_activated else "NO"
            trail_active = "YES" if orch.trailing_activated else "NO"
            lines.append(f"  Entry: {orch.entry_price:.2f}  |  BE: {be_active}  |  Trail: {trail_active}")

        if result and hasattr(result, "action") and result.action != "hold":
            side = "LONG" if result.direction == 1 else "SHORT" if result.direction == -1 else "CLOSE"
            lines.append(f"  >>> DECISION: {result.action} {side} (score={result.confluence_score:.0f})")
            lines.append(f"      Source: {result.signal_source}")
            lines.append(f"      {result.reasoning[:80]}")
        elif result and hasattr(result, "exit_type") and result.should_exit:
            lines.append(f"  >>> EXIT: {result.exit_type} -- {result.reason}")

        n_structs = len(orch._cached_structures)
        if n_structs > 0:
            recent = [s for s in orch._cached_structures if s.age_bars <= 5]
            lines.append(f"  Structures: {n_structs} total, {len(recent)} recent")

        lines.append(f"{'='*72}")
        print("\n".join(lines))

    def _print_summary(self):
        """Print trading summary on exit."""
        if self.mode == "spike_fade":
            self._print_spike_summary()
        else:
            self._print_ict_summary()

    def _print_spike_summary(self):
        eng = self.engine
        summary = eng.get_summary()
        closes = [t for t in eng.trade_log if t.get("action") == "close"]
        n_trades = summary["trades"]

        print(f"\n{'='*72}")
        print(f"  SPIKE FADE SUMMARY -- {self.symbol}")
        print(f"{'='*72}")
        print(f"  Final Equity:    ${eng.equity:,.2f}")
        print(f"  Total PnL:       ${eng.equity - INITIAL_EQUITY:+,.2f}")
        print(f"  Return:          {(eng.equity / INITIAL_EQUITY - 1) * 100:+.2f}%")
        print(f"  Max Drawdown:    {summary['max_dd_pct']:.2f}%")
        print(f"  Risk/Trade:      {self.risk_pct*100:.0f}% (fractional compounding)")
        print(f"  Live Kelly:      {summary['kelly_f']*100:.0f}%")
        print(f"  Trades:          {n_trades}")

        if n_trades > 0:
            pnls = [t["pnl"] for t in closes]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p < 0]
            pos_sizes = [t.get("position_size", 0) for t in closes]
            print(f"  Win Rate:        {len(wins)/n_trades*100:.1f}%")
            print(f"  Avg Win:         ${np.mean(wins):+.2f}" if wins else "")
            print(f"  Avg Loss:        ${np.mean(losses):+.2f}" if losses else "")
            print(f"  Avg Position:    ${np.mean(pos_sizes):,.2f}" if pos_sizes else "")
            print(f"  Max Position:    ${max(pos_sizes):,.2f}" if pos_sizes else "")

            # Growth projection
            if n_trades > 10:
                trades_per_day = n_trades / max(1, (len(closes) * 8 / 1440))  # approximate
                print(f"\n  --- Growth Projection ---")
                for days in [7, 14, 30]:
                    eq = eng.equity
                    for _ in range(int(days * trades_per_day)):
                        idx = _ % len(pnls)
                        eq *= (1 + pnls[idx] * self.risk_pct)
                    print(f"    {days:3d} days: ${eq:,.2f} ({(eq/INITIAL_EQUITY-1)*100:+.1f}%)")

            from collections import Counter
            reasons = Counter(t.get("exit_reason", "unknown") for t in closes)
            print(f"\n  --- Exit Breakdown ---")
            for reason, count in reasons.most_common():
                rp = [t["pnl"] for t in closes if t.get("exit_reason") == reason]
                rwr = sum(1 for p in rp if p > 0) / len(rp) * 100 if rp else 0
                print(f"    {reason:15s}: {count:3d} trades, PnL=${sum(rp):+.2f}, WR={rwr:.0f}%")

        # Enhancement summary
        eng = self.engine
        if eng.use_vol_regime:
            vr = eng.vol_regime.get_summary()
            print(f"\n  --- Volatility Regime ---")
            print(f"    Final regime: {vr['regime']}  |  Changes: {vr['changes']}")
            print(f"    TP mult: {vr['params']['tp_mult']:.1f}x  |  SL mult: {vr['params']['sl_mult']:.1f}x  |  Hold mult: {vr['params']['hold_mult']:.1f}x")

        if eng.use_auto_tuner:
            at = eng.auto_tuner.get_summary()
            if at['trades'] > 0:
                print(f"\n  --- Auto-Tuner ---")
                print(f"    Trades: {at['trades']}  |  WR: {at['wr']*100:.0f}%")
                if at['optimal_params']:
                    for k, v in at['optimal_params'].items():
                        print(f"    {k}: {v:.4f}" if isinstance(v, float) else f"    {k}: {v}")

        if eng.use_session_filter:
            sf = eng.session_filter.get_stats()
            if sf:
                print(f"\n  --- Session Filter ---")
                print(f"    Active hours: {sorted(eng.session_filter.active_hours)}")
                print(f"    Blocked trades: {eng._symbol_filtered}")

        # Stress test
        print(f"\n  --- Stress Test ---")
        st = StressTester(eng)
        stress_results = st.run_stress_tests(eng.equity)
        for name, r in stress_results.items():
            status = 'OK' if r['survived'] else 'BLOWN'
            print(f"    [{status}] {r['scenario']}: ${r['equity_after']:,.2f} (loss {r['loss_pct']:.3f}%)")

        # Slippage impact
        if eng.use_slippage:
            total_cost = sum(t.get('total_cost', 0) for t in closes)
            if total_cost > 0:
                print(f"\n  --- Execution Costs ---")
                print(f"    Total slippage/spread: ${total_cost:.2f}")
                print(f"    Avg per trade: ${total_cost/len(closes):.4f}")

        # Trade log file
        if eng.use_trade_log and eng.trade_logger:
            print(f"\n  Trade log: {eng.trade_logger.get_filename()}")

        print(f"{'='*72}")

        log_path = Path("checkpoints_tiny/paper_trades_v2.json")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump({
                "trade_log": eng.trade_log,
                "equity_curve": eng.equity_curve,
                "config": {
                    "symbol": self.symbol,
                    "risk_pct": self.risk_pct,
                    "initial_equity": INITIAL_EQUITY,
                    "final_equity": eng.equity,
                    "return_pct": summary["return_pct"],
                    "max_dd_pct": summary["max_dd_pct"],
                },
            }, f, indent=2, default=str)
        print(f"  Trade log: {log_path}")

    def _print_ict_summary(self):
        orch = self.orchestrator
        summary = orch.get_summary()
        closes = [t for t in orch.trade_log if t["action"] == "close"]
        n_trades = len(closes)

        print(f"\n{'='*72}")
        print(f"  ICT PAPER TRADING SUMMARY -- {self.symbol}")
        print(f"{'='*72}")
        print(f"  Final Equity:    ${orch.equity:,.2f}")
        print(f"  Total PnL:       ${orch.equity - INITIAL_EQUITY:+,.2f}")
        print(f"  Return:          {(orch.equity / INITIAL_EQUITY - 1) * 100:+.2f}%")
        print(f"  Trades:          {n_trades}")

        if n_trades > 0:
            pnls = [t.get("pnl", 0) for t in closes]
            wins = [p for p in pnls if p > 0]
            print(f"  Win Rate:        {len(wins)/n_trades*100:.1f}%")

            from collections import Counter
            reasons = Counter(t.get("exit_reason", "unknown") for t in closes)
            print(f"\n  --- Exit Breakdown ---")
            for reason, count in reasons.most_common(8):
                rp = [t["pnl"] for t in closes if t.get("exit_reason") == reason]
                rwr = sum(1 for p in rp if p > 0) / len(rp) * 100 if rp else 0
                print(f"    {reason:45s}: {count:3d} trades, WR={rwr:.0f}%")

        print(f"{'='*72}")

        log_path = Path("checkpoints_tiny/paper_trades_v2.json")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "w") as f:
            json.dump(orch.get_trade_log(), f, indent=2, default=str)
        print(f"  Trade log: {log_path}")

    async def run(self):
        # Create control file
        control_path = Path('logs/control.json')
        control_path.parent.mkdir(parents=True, exist_ok=True)
        if not control_path.exists():
            control_path.write_text(json.dumps({"trading_active": True}))

        if self.use_mt5 and self.mt5_source:
            await self._run_mt5()
        else:
            await self._run_websocket()

    async def _run_websocket(self):
        """Run with Deriv WebSocket (default mode)."""
        # Connect to dashboard first
        await self.dashboard.connect()

        conn = DerivConnection()
        try:
            await conn.connect()
        except Exception as e:
            print(f"  Connection failed: {e}")
            return

        sym = self.symbol

        # Fetch history
        print(f"  [{sym}] Fetching {self.history_bars} bars of history...")
        try:
            resp = await conn.get_ticks_history(
                sym, count=min(self.history_bars, 1000), style="candles", granularity=60,
            )
            for c in resp.get("candles", []):
                self.add_candle({
                    "epoch": c["epoch"],
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            print(f"  [{sym}] Got {len(self.candles)} candles")
        except Exception as e:
            print(f"  [{sym}] History fetch failed: {e}")

        # Initialize trade logger
        if self.mode == "spike_fade" and self.use_trade_log:
            self.engine.trade_logger = TradeLogger(sym)
            print(f"  [{sym}] Trade log: {self.engine.trade_logger.get_filename()}")

        # Run initial cycle with history
        if self.mode == "spike_fade" and self.candles:
            self._run_cycle()

        # Subscribe to live ticks
        try:
            sub_id = await conn.subscribe(
                {"ticks": sym}, handler=self.handle_tick,
            )
            print(f"  [{sym}] Subscribed to live ticks. Trading...\n")
        except Exception as e:
            print(f"  [{sym}] Subscribe failed: {e}")
            return

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            await conn.close()
            await self.dashboard.close()
            self._print_summary()

    async def _run_mt5(self):
        """Run with MT5 tick data (real-time tick counting)."""
        if not self.mt5_source.connect():
            print("  [MT5] Failed to connect. Falling back to WebSocket.")
            await self._run_websocket()
            return

        print(f"  [MT5] Streaming ticks for {self.symbol}...")
        print(f"  [MT5] Press Ctrl+C to stop\n")

        try:
            last_display = 0
            while True:
                tick = self.mt5_source.get_tick()
                if tick:
                    self._tick_count += 1

                    # Feed to spike engine
                    if self.mode == "spike_fade" and self.engine:
                        result = self.engine.on_tick(tick)

                        # Track tick gaps between spikes
                        if self.engine._ticks_since_spike == 0 and len(self.engine._spike_indices) > 1:
                            gap = self.engine._spike_indices[-1] - self.engine._spike_indices[-2]
                            self._spike_tick_gaps.append(gap)

                        # Display every 5 seconds
                        if time.time() - last_display > 5:
                            self._display_mt5_status(tick, result)
                            last_display = time.time()

                    # Also aggregate into candles for display
                    self.trend_tracker.update(tick["price"])
                    epoch = tick["epoch"]
                    candle_epoch = (epoch // 60) * 60

                    if candle_epoch != self._current_candle_epoch:
                        if self._current_candle and self._current_candle["epoch"] > 0:
                            self.add_candle(self._current_candle)
                            self._candle_count += 1
                        self._current_candle_epoch = candle_epoch
                        self._current_candle = {
                            "epoch": candle_epoch,
                            "open": tick["price"], "high": tick["price"],
                            "low": tick["price"], "close": tick["price"],
                            "volume": 0,
                        }
                    else:
                        if self._current_candle:
                            self._current_candle["high"] = max(self._current_candle["high"], tick["price"])
                            self._current_candle["low"] = min(self._current_candle["low"], tick["price"])
                            self._current_candle["close"] = tick["price"]

                await asyncio.sleep(0.001)  # 1ms poll

        except KeyboardInterrupt:
            logger.info("stopped_by_user")
        finally:
            self.mt5_source.close()
            self._print_summary()

    def _display_mt5_status(self, tick: dict, result):
        """Display status for MT5 tick mode."""
        price = tick["price"]
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        eng = self.engine

        pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(eng.position, "?")
        dd = (eng.peak_equity - eng.equity) / eng.peak_equity * 100 if eng.peak_equity > 0 else 0
        summary = eng.get_summary()
        n_trades = summary["trades"]
        wr = summary["win_rate"] * 100

        # Tick stats
        avg_gap = np.mean(self._spike_tick_gaps) if self._spike_tick_gaps else 0
        
        # Current PnL
        if eng.position != 0 and eng.entry_price > 0:
            pnl_pct = (price - eng.entry_price) / eng.entry_price * eng.position * 100
            pnl_str = f" | Unrealized: {pnl_pct:+.3f}% ({eng.bars_held} ticks)"
        else:
            pnl_str = ""

        lines = [
            f"\r  MT5 {self.symbol} @ {ts} | {price:.4f} | {pos_str} | Ticks: {self._tick_count} | Spikes: {len(eng._spike_indices)} | Avg gap: {avg_gap:.0f}{pnl_str}",
        ]

        if result and result.get("action") == "open":
            lines.append(f"\n  >>> OPENED: {result['side'].upper()} at {result['entry_price']:.4f}")
        elif result and result.get("action") == "close":
            pnl = result.get("pnl", 0)
            lines.append(f"\n  >>> CLOSED: {result['exit_reason']} | PnL: ${pnl:+.2f}")

        print("".join(lines), end="", flush=True)


class MultiPaperTrader:
    """Run multiple PaperTraderV2 instances concurrently, each on its own WebSocket connection."""

    def __init__(self, traders: list[PaperTraderV2]):
        self.traders = traders
        self._connections: list[DerivConnection] = []

    async def run(self):
        """Run all traders concurrently. Each gets its own WebSocket + candle aggregation."""
        # Connect all first
        for trader in self.traders:
            conn = DerivConnection()
            self._connections.append(conn)

        # Print combined header
        symbols = ", ".join(t.symbol for t in self.traders)
        risk_info = "  |  ".join(f"{t.symbol}: {t.risk_pct*100:.0f}%" for t in self.traders)
        print(f"\n{'='*72}")
        print(f"  MULTI-SYMBOL PAPER TRADING  |  {symbols}")
        print(f"  {len(self.traders)} traders  |  {risk_info}")
        print(f"  Starting equity per symbol: ${INITIAL_EQUITY:,.2f}")
        print(f"{'='*72}\n")

        tasks = []
        for trader, conn in zip(self.traders, self._connections):
            tasks.append(self._run_one(trader, conn))

        # Also connect a shared dashboard pusher
        # (each trader has its own, but they all push to the same server)

        try:
            await asyncio.gather(*tasks)
        except KeyboardInterrupt:
            print("\n\n  Stopping all traders...")
        finally:
            for conn in self._connections:
                try:
                    await conn.close()
                except Exception:
                    pass
            for trader in self.traders:
                try:
                    await trader.dashboard.close()
                except Exception:
                    pass
            # Print all summaries
            print(f"\n\n{'#'*72}")
            print(f"  COMBINED SUMMARY")
            print(f"{'#'*72}")
            total_pnl = 0
            total_trades = 0
            for trader in self.traders:
                trader._print_summary()
                if trader.mode == "spike_fade":
                    summary = trader.engine.get_summary()
                    total_pnl += summary["pnl"]
                    total_trades += summary["trades"]
            print(f"\n  TOTAL PnL across all symbols: ${total_pnl:+,.2f}")
            print(f"  TOTAL trades: {total_trades}")
            print(f"{'#'*72}")

    async def _run_one(self, trader: PaperTraderV2, conn: DerivConnection):
        """Run a single trader with its own WebSocket connection."""
        sym = trader.symbol

        try:
            await conn.connect()
        except Exception as e:
            print(f"  [{sym}] Connection failed: {e}")
            return

        # Fetch history
        print(f"  [{sym}] Fetching {trader.history_bars} bars of history...")
        try:
            resp = await conn.get_ticks_history(
                sym, count=min(trader.history_bars, 1000), style="candles", granularity=60
            )
            for c in resp.get("candles", []):
                trader.add_candle({
                    "epoch": c["epoch"],
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            print(f"  [{sym}] Got {len(trader.candles)} candles (last: {trader.candles[-1]['close']:.2f})")
        except Exception as e:
            print(f"  [{sym}] History fetch failed: {e}")

        # Initialize trade logger
        if trader.mode == "spike_fade" and trader.engine.use_trade_log:
            trader.engine.trade_logger = TradeLogger(sym)

        # Connect dashboard
        await trader.dashboard.connect()

        # Subscribe to ticks with callback
        async def tick_handler(msg):
            try:
                trader.handle_tick(msg)
            except Exception as e:
                logger.error(f"[{sym}] tick handler error", error=str(e))

        try:
            await conn.subscribe({"ticks": sym}, handler=tick_handler)
            print(f"  [{sym}] Subscribed to live ticks. Trading...\n")
        except Exception as e:
            print(f"  [{sym}] Subscribe failed: {e}")
            return

        # Keep alive until cancelled
        try:
            while True:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"\n  [{sym}] Error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Paper Trading v2 (Auto-Detect)")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL,
                        help="Symbol: R_75, R_10 (ICT) or BOOM500, CRASH500, etc. (Spike Fade)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols for multi-symbol mode, e.g. BOOM500,CRASH500")
    parser.add_argument("--history-bars", type=int, default=DEFAULT_HISTORY_BARS)
    parser.add_argument("--confluence", type=float, default=95.0)
    parser.add_argument("--risk", type=float, default=0.10,
                        help="Fractional risk per trade (0.01-0.50, default 0.10 = 10%% of equity)")
    parser.add_argument("--symbol-risks", type=str, default=None,
                        help="Comma-separated risk levels per symbol, e.g. 0.15,0.10,0.08,0.06")
    parser.add_argument("--profit-targets", type=str, default=None,
                        help="Profit target tiers: 'pct:mult,pct:mult,...' e.g. '10:0.5,20:0.25,30:0'")
    parser.add_argument("--no-filters", action="store_true",
                        help="Disable symbol-specific post-mortem filters")
    parser.add_argument("--no-slippage", action="store_true",
                        help="Disable slippage/spread modeling in backtest mode")
    parser.add_argument("--session-filter", action="store_true",
                        help="Enable time-of-day session filter")
    parser.add_argument("--no-vol-regime", action="store_true",
                        help="Disable volatility regime detection")
    parser.add_argument("--no-auto-tune", action="store_true",
                        help="Disable adaptive auto-tuning")
    parser.add_argument("--mt5", action="store_true", help="Use MT5 tick data (BOOM/CRASH only)")
    parser.add_argument("--mt5-login", type=int, default=0, help="MT5 login number")
    parser.add_argument("--mt5-password", type=str, default="", help="MT5 password")
    parser.add_argument("--mt5-server", type=str, default="Deriv-Demo", help="MT5 server name")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )

    # Parse profit targets
    profit_tiers = None
    if args.profit_targets:
        profit_tiers = []
        for pair in args.profit_targets.split(","):
            pct_str, mult_str = pair.strip().split(":")
            profit_tiers.append((float(pct_str), float(mult_str)))
        profit_tiers.sort(key=lambda t: t[0])
        print(f"  Profit targets: {', '.join(f'+{t[0]}%→{t[1]*100:.0f}% risk' for t in profit_tiers)}")

    # Multi-symbol mode
    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]

        # Per-symbol risk levels
        if args.symbol_risks:
            risk_strs = [r.strip() for r in args.symbol_risks.split(",")]
            if len(risk_strs) != len(symbols):
                print(f"  ERROR: --symbol-risks has {len(risk_strs)} values but --symbols has {len(symbols)} symbols")
                return
            risk_levels = [float(r) for r in risk_strs]
        else:
            risk_levels = [args.risk] * len(symbols)

        print(f"\n  Multi-symbol mode: {', '.join(symbols)}")
        print(f"  Starting equity per symbol: ${INITIAL_EQUITY:,.2f}")
        print(f"  Risk per symbol:")
        for sym, risk in zip(symbols, risk_levels):
            print(f"    {sym:12s}  {risk*100:5.1f}%/trade")

        traders = []
        for sym, risk in zip(symbols, risk_levels):
            trader = PaperTraderV2(
                symbol=sym,
                history_bars=args.history_bars,
                min_confluence=args.confluence,
                risk_pct=risk,
                profit_tiers=profit_tiers,
                no_filters=args.no_filters,
                use_mt5=args.mt5,
                mt5_login=args.mt5_login,
                mt5_password=args.mt5_password,
                mt5_server=args.mt5_server,
            )
            # Set enhancement flags
            if hasattr(trader, 'engine') and trader.engine:
                trader.engine.use_session_filter = args.session_filter
                trader.engine.use_slippage = not args.no_slippage
                trader.engine.use_vol_regime = not args.no_vol_regime
                trader.engine.use_auto_tuner = not args.no_auto_tune
            traders.append(trader)

        multi = MultiPaperTrader(traders)
        try:
            asyncio.run(multi.run())
        except KeyboardInterrupt:
            pass
    else:
        # Single symbol mode (original behavior)
        print(f"\n  Risk per trade: {args.risk*100:.0f}% of equity (compounding)")
        print(f"  Starting equity: ${INITIAL_EQUITY:,.2f}")

        trader = PaperTraderV2(
            symbol=args.symbol,
            history_bars=args.history_bars,
            min_confluence=args.confluence,
            risk_pct=args.risk,
            profit_tiers=profit_tiers,
            no_filters=args.no_filters,
            use_mt5=args.mt5,
            mt5_login=args.mt5_login,
            mt5_password=args.mt5_password,
            mt5_server=args.mt5_server,
        )
        # Set enhancement flags
        if hasattr(trader, 'engine') and trader.engine:
            trader.engine.use_session_filter = args.session_filter
            trader.engine.use_slippage = not args.no_slippage
            trader.engine.use_vol_regime = not args.no_vol_regime
            trader.engine.use_auto_tuner = not args.no_auto_tune
        try:
            asyncio.run(trader.run())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
