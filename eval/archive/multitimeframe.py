"""
eval/multitimeframe.py — Multi-Timeframe Strategy for BOOM/CRASH.

Uses 15-minute bars for trend direction + 1-minute bars for entry timing.

Logic:
  1. 15min EMA(8) vs EMA(21) determines bullish/bearish bias
  2. 1-minute spike must align with 15min trend to enter
  3. If 15min trend is UP → only take LONG trades (with spikes)
  4. If 15min trend is DOWN → only take SHORT trades (with spikes)
  5. If 15min is flat/no clear trend → no trades (wait)

Key insight: BOOM500 spikes UP → in an uptrend, the spike is continuation.
             In a downtrend, the spike is a reversal → skip it.
"""

import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class MTFConfig:
    """Multi-timeframe strategy configuration."""
    # Trend detection (15min)
    ema_fast: int = 8          # Fast EMA period (15min bars)
    ema_slow: int = 21         # Slow EMA period (15min bars)
    trend_slope_window: int = 5  # Bars to measure trend slope
    trend_min_slope: float = 0.0  # Min slope to confirm trend (0 = any slope)

    # Spike detection (1min)
    spike_mult: float = 3.0
    median_window: int = 100

    # Entry
    confirm_bars: int = 3       # Wait N bars after spike for confirmation
    entry_delay: int = 1        # Enter N bars after confirmation

    # Risk management
    tp_pct: float = 0.004       # 0.4% TP
    sl_pct: float = 0.0012      # 0.12% SL
    max_hold: int = 20          # Max bars to hold (1min)

    # Trailing
    trailing_activation: float = 0.002
    trailing_distance: float = 0.0006

    # Filters
    min_spike_gap: int = 8      # Min bars between trades
    require_trend_aligned: bool = True  # Only trade with trend


class MultiTimeframeStrategy:
    """
    Multi-timeframe momentum strategy.

    15min = direction filter
    1min = entry execution
    """

    def __init__(self, symbol: str, config: MTFConfig = None,
                 risk_pct: float = 0.10, initial_equity: float = 10000.0):
        self.symbol = symbol
        self.cfg = config or MTFConfig()
        self.risk_pct = risk_pct
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity

        # Position
        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0
        self.current_pos_size = 0.0

        # 1min data
        self._closes_1m = []
        self._highs_1m = []
        self._lows_1m = []
        self._pct_changes = []
        self._median_vol = 0.001
        self._bars_since_trade = 999

        # 15min trend
        self._closes_15m = []
        self._ema_fast = []
        self._ema_slow = []
        self._trend = 0  # +1=bullish, -1=bearish, 0=flat

        # Pending entry
        self._pending_entry = None
        self._pending_direction = 0

        # Trailing
        self._trailing_active = False
        self._best_price = 0.0

        # Stats
        self.trade_log = []
        self.equity_curve = []
        self._trade_counter = 0
        self._trend_stats = {"bullish": 0, "bearish": 0, "flat": 0}

    def update_15min(self, bar_15m: dict):
        """Update 15-minute trend state."""
        c = float(bar_15m['close'])
        self._closes_15m.append(c)

        if len(self._closes_15m) < 2:
            return

        # Compute EMAs
        ema_fast = self._compute_ema(self._closes_15m, self.cfg.ema_fast)
        ema_slow = self._compute_ema(self._closes_15m, self.cfg.ema_slow)

        if ema_fast is not None and ema_slow is not None:
            self._ema_fast.append(ema_fast)
            self._ema_slow.append(ema_slow)

            # Trend direction
            if len(self._ema_fast) >= self.cfg.trend_slope_window:
                # Slope of fast EMA
                recent = self._ema_fast[-self.cfg.trend_slope_window:]
                slope = (recent[-1] - recent[0]) / recent[0] * 100  # % change

                if slope > self.cfg.trend_min_slope:
                    self._trend = 1  # Bullish
                    self._trend_stats["bullish"] += 1
                elif slope < -self.cfg.trend_min_slope:
                    self._trend = -1  # Bearish
                    self._trend_stats["bearish"] += 1
                else:
                    self._trend = 0  # Flat
                    self._trend_stats["flat"] += 1
            else:
                # Not enough data, use EMA crossover
                if ema_fast > ema_slow:
                    self._trend = 1
                elif ema_fast < ema_slow:
                    self._trend = -1
                else:
                    self._trend = 0

    def _compute_ema(self, data: list, period: int) -> float | None:
        """Compute EMA of a list."""
        if len(data) < period:
            return None
        arr = np.array(data, dtype=float)
        multiplier = 2 / (period + 1)
        ema = arr[0]
        for val in arr[1:]:
            ema = val * multiplier + ema * (1 - multiplier)
        return ema

    def on_bar_1m(self, bar: dict) -> dict | None:
        """Process a 1-minute bar. Call update_15min() first."""
        o, h, l, c = float(bar['open']), float(bar['high']), float(bar['low']), float(bar['close'])
        bar_idx = len(self._closes_1m)

        self._closes_1m.append(c)
        self._highs_1m.append(h)
        self._lows_1m.append(l)
        self._bars_since_trade += 1

        result = None

        # Position management
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit(h, l, c)

        # Pending entry countdown
        if self._pending_entry is not None and self.position == 0:
            self._pending_entry -= 1
            if self._pending_entry <= 0:
                result = self._execute_entry(c)
                self._pending_entry = None

        # Spike detection (only if we have trend data and are flat)
        if (len(self._closes_1m) > self.cfg.median_window
                and self.position == 0
                and self._pending_entry is None
                and self._bars_since_trade >= self.cfg.min_spike_gap):

            pct_change = (c - self._closes_1m[-2]) / self._closes_1m[-2] if self._closes_1m[-2] != 0 else 0
            self._pct_changes.append(abs(pct_change))

            recent = self._pct_changes[-self.cfg.median_window:]
            self._median_vol = max(float(np.median(recent)), 0.0001)

            spike_size = abs(pct_change) / self._median_vol

            if spike_size >= self.cfg.spike_mult:
                spike_direction = 1 if pct_change > 0 else -1

                # KEY: Only trade if spike aligns with 15min trend
                if self.cfg.require_trend_aligned:
                    if self._trend == 0:
                        return result  # No clear trend → skip
                    if spike_direction != self._trend:
                        return result  # Spike against trend → skip

                # Enter WITH the spike (momentum)
                self._pending_direction = spike_direction
                self._pending_entry = self.cfg.entry_delay

        return result

    def _execute_entry(self, price: float) -> dict:
        """Open a position."""
        self.position = self._pending_direction
        self.entry_price = price
        self.bars_held = 0
        self._trailing_active = False
        self._best_price = price

        # Position sizing
        risk_dollars = self.equity * self.risk_pct
        sl_dollars = self.entry_price * self.cfg.sl_pct
        self.current_pos_size = risk_dollars / sl_dollars if sl_dollars > 0 else 0

        self._bars_since_trade = 0
        self._trade_counter += 1
        self._pending_entry = None

        side = "LONG" if self.position == 1 else "SHORT"
        trend_str = "bullish" if self._trend == 1 else "bearish"
        return {
            "action": "open", "side": side.lower(), "direction": self.position,
            "entry_price": price, "position_size": self.current_pos_size,
            "equity_before": self.equity, "trade_id": self._trade_counter,
            "reasoning": f"MTF {side} | 15min={trend_str}",
        }

    def _check_exit(self, high: float, low: float, price: float) -> dict | None:
        """Check TP/SL/trailing/timeout."""
        if self.position == 1:  # Long
            if price > self._best_price:
                self._best_price = price
                if (self._best_price - self.entry_price) / self.entry_price >= self.cfg.trailing_activation:
                    self._trailing_active = True

            if high >= self.entry_price * (1 + self.cfg.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 + self.cfg.tp_pct))
            if low <= self.entry_price * (1 - self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 - self.cfg.sl_pct))
            if self._trailing_active:
                trail_stop = self._best_price * (1 - self.cfg.trailing_distance)
                if low <= trail_stop:
                    return self._close_position(price, "trailing", trail_stop)

        elif self.position == -1:  # Short
            if price < self._best_price:
                self._best_price = price
                if (self.entry_price - self._best_price) / self.entry_price >= self.cfg.trailing_activation:
                    self._trailing_active = True

            if low <= self.entry_price * (1 - self.cfg.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 - self.cfg.tp_pct))
            if high >= self.entry_price * (1 + self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 + self.cfg.sl_pct))
            if self._trailing_active:
                trail_stop = self._best_price * (1 + self.cfg.trailing_distance)
                if high >= trail_stop:
                    return self._close_position(price, "trailing", trail_stop)

        if self.bars_held >= self.cfg.max_hold:
            return self._close_position(price, "timeout", price)

        return None

    def _close_position(self, price: float, reason: str, exit_price: float) -> dict:
        """Close position and record trade."""
        if self.position == 1:
            pnl_pct = (exit_price - self.entry_price) / self.entry_price
        else:
            pnl_pct = (self.entry_price - exit_price) / self.entry_price

        pnl_dollars = self.current_pos_size * pnl_pct if self.current_pos_size > 0 else 0
        self.equity += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.equity)
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0

        self.trade_log.append({
            "trade_id": self._trade_counter,
            "direction": self.position,
            "entry_price": self.entry_price,
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl": pnl_dollars,
            "bars_held": self.bars_held,
            "trend_at_entry": self._trend,
        })
        self.equity_curve.append({"equity": self.equity, "dd": dd})

        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0
        self._trailing_active = False
        self._best_price = 0.0

        return {"action": "close", "exit_price": exit_price, "exit_reason": reason, "pnl": pnl_dollars}


def run_mtf_backtest(symbol: str, tp_pct=0.004, sl_pct=0.0012, max_hold=20,
                     spike_mult=3.0, risk_pct=0.10, ema_fast=8, ema_slow=21) -> dict:
    """Run multi-timeframe backtest."""
    import pandas as pd

    # Load 1min data
    f1m = Path(__file__).parent.parent / "data" / f"candles_{symbol.lower()}_1min.parquet"
    f15m = Path(__file__).parent.parent / "data" / f"candles_{symbol.lower()}_15min.parquet"
    if not f1m.exists() or not f15m.exists():
        return {"error": f"Missing data files"}

    df_1m = pd.read_parquet(f1m)
    df_15m = pd.read_parquet(f15m)

    # Build 1min candles
    eps1 = df_1m['epoch'].values.astype(int) if 'epoch' in df_1m.columns else np.zeros(len(df_1m), dtype=int)
    candles_1m = [
        {"epoch": int(eps1[i]), "open": float(df_1m['open'].values[i]),
         "high": float(df_1m['high'].values[i]), "low": float(df_1m['low'].values[i]),
         "close": float(df_1m['close'].values[i])}
        for i in range(len(df_1m))
    ]

    # Build 15min candles
    eps15 = df_15m['epoch'].values.astype(int) if 'epoch' in df_15m.columns else np.zeros(len(df_15m), dtype=int)
    candles_15m = [
        {"epoch": int(eps15[i]), "open": float(df_15m['open'].values[i]),
         "high": float(df_15m['high'].values[i]), "low": float(df_15m['low'].values[i]),
         "close": float(df_15m['close'].values[i])}
        for i in range(len(df_15m))
    ]

    # Build time index: map each 1min bar to its 15min bar
    # 15min bars are at 0, 900, 1800, ... seconds
    # 1min bars: epoch // 900 gives the 15min bucket
    epoch_to_15m = {}
    for i, b in enumerate(candles_15m):
        bucket = b['epoch'] // 900
        epoch_to_15m[bucket] = i

    # Run backtest
    cfg = MTFConfig(ema_fast=ema_fast, ema_slow=ema_slow, tp_pct=tp_pct,
                     sl_pct=sl_pct, max_hold=max_hold, spike_mult=spike_mult)
    strat = MultiTimeframeStrategy(symbol, config=cfg, risk_pct=risk_pct)

    last_15m_idx = -1
    for b1m in candles_1m:
        # Update 15min when we enter a new bucket
        bucket = b1m['epoch'] // 900
        if bucket in epoch_to_15m:
            idx = epoch_to_15m[bucket]
            if idx != last_15m_idx:
                strat.update_15min(candles_15m[idx])
                last_15m_idx = idx

        strat.on_bar_1m(b1m)

    # Close any open position
    if strat.position != 0:
        strat._close_position(candles_1m[-1]['close'], "end_of_data", candles_1m[-1]['close'])

    trades = strat.trade_log
    n = len(trades)
    if n == 0:
        return {"trades": 0, "symbol": symbol, "trend_stats": strat._trend_stats}

    pnls = [t["pnl"] for t in trades]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    wr = len(wins) / n if n > 0 else 0
    tw = sum(wins); tl = abs(sum(losses))
    pf = tw / tl if tl > 0 else 99
    ret = (strat.equity / strat.initial_equity - 1) * 100
    max_dd = max((e["dd"] for e in strat.equity_curve), default=0) * 100

    ex = {}
    for t in trades:
        ex[t["exit_reason"]] = ex.get(t["exit_reason"], 0) + 1

    # Trend at entry breakdown
    trend_entries = {}
    for t in trades:
        te = t.get("trend_at_entry", 0)
        label = "bull" if te == 1 else ("bear" if te == -1 else "flat")
        if label not in trend_entries:
            trend_entries[label] = {"count": 0, "wins": 0, "pnl": 0}
        trend_entries[label]["count"] += 1
        trend_entries[label]["pnl"] += t["pnl"]
        if t["pnl"] > 0:
            trend_entries[label]["wins"] += 1

    return {
        "symbol": symbol, "trades": n, "win_rate": wr, "profit_factor": pf,
        "total_return_pct": ret, "max_dd_pct": max_dd,
        "exit_breakdown": ex, "trend_entries": trend_entries,
        "trend_stats": strat._trend_stats,
    }


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("  MULTI-TIMEFRAME STRATEGY — 15min Trend + 1min Entry")
    print("=" * 80)

    # Test various configs
    configs = [
        {"tp": 0.004, "sl": 0.0012, "hold": 20, "sm": 3.0, "ef": 8, "es": 21},
        {"tp": 0.004, "sl": 0.0015, "hold": 25, "sm": 3.0, "ef": 8, "es": 21},
        {"tp": 0.003, "sl": 0.001, "hold": 15, "sm": 3.0, "ef": 5, "es": 13},
        {"tp": 0.005, "sl": 0.0015, "hold": 25, "sm": 2.5, "ef": 8, "es": 21},
        {"tp": 0.004, "sl": 0.0012, "hold": 20, "sm": 3.0, "ef": 12, "es": 26},
    ]

    for sym in ["BOOM500", "CRASH500"]:
        print(f"\n{'='*80}")
        print(f"  {sym}")
        print(f"{'='*80}")
        print(f"  {'TP%':>5} {'SL%':>6} {'Hold':>4} {'SM':>4} {'EF':>3} {'ES':>3}  "
              f"{'Trades':>6} {'WR':>5} {'PF':>5} {'Ret':>8} {'DD':>6} {'Exits'}")
        print(f"  {'-'*75}")

        for cfg in configs:
            r = run_mtf_backtest(sym, tp_pct=cfg["tp"], sl_pct=cfg["sl"],
                                  max_hold=cfg["hold"], spike_mult=cfg["sm"],
                                  ema_fast=cfg["ef"], ema_slow=cfg["es"])
            if r.get("trades", 0) == 0:
                print(f"  {cfg['tp']*100:.1f}% {cfg['sl']*100:.2f}% {cfg['hold']:3d} {cfg['sm']:.1f}  "
                      f"{cfg['ef']:2d} {cfg['es']:2d}   No trades")
                continue

            ex_str = ' '.join(f'{k}:{v}' for k, v in sorted(r['exit_breakdown'].items()))
            trend_str = ' '.join(f'{k}:{v["count"]}({v["wins"]}W)' for k, v in r['trend_entries'].items())

            print(f"  {cfg['tp']*100:.1f}% {cfg['sl']*100:.2f}% {cfg['hold']:3d} {cfg['sm']:.1f}  "
                  f"{cfg['ef']:2d} {cfg['es']:2d}  "
                  f"{r['trades']:6d} {r['win_rate']*100:5.1f}% {r['profit_factor']:5.2f} "
                  f"{r['total_return_pct']:+7.2f}% {r['max_dd_pct']:5.2f}%  {ex_str}")
            print(f"    Trend at entry: {trend_str}")
