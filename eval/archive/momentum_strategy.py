"""
eval/momentum_strategy.py — Momentum Continuation Strategy for BOOM/CRASH.

Instead of fading spikes, this trades WITH the spike direction:
  1. Detect a spike (price jumps 3x median)
  2. Wait for a small retracement (confirmation)
  3. Enter IN the spike direction (long after up-spike, short after down-spike)
  4. SL just below the pre-spike price (tight)
  5. TP at the next spike or a fixed target

Key insight: BOOM500 spikes UP and often continues. CRASH500 spikes DOWN and continues.
The retrace after a spike is NOT a reversal — it's a pause before continuation.
"""

import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class MomentumConfig:
    """Configuration for momentum continuation strategy."""
    # Spike detection
    spike_mult: float = 3.0        # Spike = this x median volatility
    median_window: int = 100       # Bars to compute median volatility

    # Confirmation
    confirm_bars_min: int = 1      # Wait at least this many bars after spike
    confirm_bars_max: int = 5      # Enter within this many bars after spike
    confirm_retrace_min: float = 0.0002  # Min retrace (0.02%) to confirm entry

    # Risk management
    tp_pct: float = 0.003          # Take profit at 0.3%
    sl_pct: float = 0.0008         # Stop loss at 0.08%
    max_hold: int = 20             # Max bars to hold

    # Trailing stop
    trailing_activation: float = 0.0015  # Activate after 0.15% profit
    trailing_distance: float = 0.0005   # Trail 0.05% behind best price

    # Filters
    min_spike_gap: int = 10        # Min bars between trades
    max_cluster: int = 3           # Skip if >N spikes in cluster (trend, not exhaustion)


@dataclass
class MomentumTrade:
    """Single trade record."""
    trade_id: int = 0
    direction: int = 0       # 1=long, -1=short
    entry_price: float = 0.0
    exit_price: float = 0.0
    exit_reason: str = ""
    pnl: float = 0.0
    bars_held: int = 0
    spike_size: float = 0.0
    spike_direction: str = ""
    entry_bar: int = 0


class MomentumStrategy:
    """
    Momentum continuation strategy for BOOM/CRASH indices.

    Trades WITH the spike direction after a small confirmation retracement.
    """

    def __init__(self, symbol: str, config: MomentumConfig = None,
                 risk_pct: float = 0.10, initial_equity: float = 10000.0):
        self.symbol = symbol
        self.cfg = config or MomentumConfig()
        self.risk_pct = risk_pct
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity

        # Position state
        self.position = 0         # 0=flat, 1=long, -1=short
        self.entry_price = 0.0
        self.entry_bar = 0
        self.bars_held = 0
        self.current_pos_size = 0.0

        # Spike detection
        self._closes = []
        self._highs = []
        self._lows = []
        self._pct_changes = []
        self._median_vol = 0.001
        self._bars_since_trade = 999

        # Pending entry
        self._pending_entry = None   # countdown
        self._pending_direction = 0  # +1 long, -1 short
        self._pending_spike_size = 0.0
        self._spike_entry_price = 0.0  # price when spike was detected
        self._confirm_high = 0.0    # track retrace high
        self._confirm_low = 999999.0

        # Trailing stop
        self._trailing_active = False
        self._best_price = 0.0

        # Stats
        self.trade_log = []
        self.equity_curve = []
        self._trade_counter = 0

    def on_bar(self, bar: dict) -> dict | None:
        """Process a single OHLC bar. Returns action dict or None."""
        o, h, l, c = float(bar['open']), float(bar['high']), float(bar['low']), float(bar['close'])
        bar_idx = len(self._closes)

        self._closes.append(c)
        self._highs.append(h)
        self._lows.append(l)
        self._bars_since_trade += 1

        result = None

        # Update position management
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit(h, l, c)

        # Pending entry countdown
        if self._pending_entry is not None and self.position == 0:
            self._pending_entry -= 1
            if self._pending_entry <= 0:
                # Check confirmation: did price retrace enough?
                confirmed = self._check_confirmation(c, h, l)
                if confirmed:
                    result = self._execute_entry(c)
                else:
                    self._pending_entry = None

        # Spike detection
        if len(self._closes) > self.cfg.median_window:
            pct_change = (c - self._closes[-2]) / self._closes[-2] if self._closes[-2] != 0 else 0
            self._pct_changes.append(abs(pct_change))

            # Rolling median volatility
            recent = self._pct_changes[-self.cfg.median_window:]
            self._median_vol = float(np.median(recent)) if recent else 0.001
            self._median_vol = max(self._median_vol, 0.0001)

            spike_size = abs(pct_change) / self._median_vol

            if spike_size >= self.cfg.spike_mult:
                # Spike detected!
                spike_direction = 'up' if pct_change > 0 else 'down'

                # Only enter if flat and cooldown passed
                if (self.position == 0
                        and self._pending_entry is None
                        and self._bars_since_trade >= self.cfg.min_spike_gap):

                    # Set pending entry with direction = WITH the spike
                    self._pending_direction = 1 if spike_direction == 'up' else -1
                    self._pending_spike_size = spike_size
                    self._spike_entry_price = c
                    self._confirm_high = c  # track for retrace
                    self._confirm_low = c

                    # Wait for confirmation retracement
                    self._pending_entry = self.cfg.confirm_bars_max

        return result

    def _check_confirmation(self, price: float, high: float, low: float) -> bool:
        """Check if price retraced enough to confirm entry."""
        if self._pending_entry is None:
            return False

        # Update tracked levels
        self._confirm_high = max(self._confirm_high, high)
        self._confirm_low = min(self._confirm_low, low)

        # Calculate retrace from the spike
        if self._pending_direction == 1:
            # Long entry: spike was UP, price should retrace DOWN first
            retrace = (self._confirm_high - price) / self._confirm_high
        else:
            # Short entry: spike was DOWN, price should retrace UP first
            retrace = (price - self._confirm_low) / self._confirm_low

        # Enter if retrace is small enough (confirmation of pause, not reversal)
        # or if we've waited long enough
        bars_waited = self.cfg.confirm_bars_max - self._pending_entry
        if retrace >= self.cfg.confirm_retrace_min:
            return True
        if bars_waited >= self.cfg.confirm_bars_min:
            # Even without retrace, enter after min bars
            return True

        return False

    def _execute_entry(self, price: float) -> dict:
        """Open a momentum position."""
        self.position = self._pending_direction
        self.entry_price = price
        self.entry_bar = len(self._closes)
        self.bars_held = 0
        self._trailing_active = False
        self._best_price = price

        # Position sizing
        risk_dollars = self.equity * self.risk_pct
        sl_dollars = self.entry_price * self.cfg.sl_pct
        self.current_pos_size = risk_dollars / sl_dollars if sl_dollars > 0 else 0

        self._pending_entry = None
        self._bars_since_trade = 0
        self._trade_counter += 1

        side = "LONG" if self.position == 1 else "SHORT"
        return {
            "action": "open",
            "side": side.lower(),
            "direction": self.position,
            "entry_price": price,
            "position_size": self.current_pos_size,
            "equity_before": self.equity,
            "trade_id": self._trade_counter,
            "spike_size": self._pending_spike_size,
            "reasoning": f"Momentum {'long' if self.position == 1 else 'short'} after {self._pending_spike_size:.1f}x spike",
        }

    def _check_exit(self, high: float, low: float, price: float) -> dict | None:
        """Check TP/SL/trailing/timeout exits."""
        if self.position == 1:  # Long
            # Update best price for trailing
            if price > self._best_price:
                self._best_price = price
                if (self._best_price - self.entry_price) / self.entry_price >= self.cfg.trailing_activation:
                    self._trailing_active = True

            # TP
            if high >= self.entry_price * (1 + self.cfg.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 + self.cfg.tp_pct))

            # SL
            if low <= self.entry_price * (1 - self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 - self.cfg.sl_pct))

            # Trailing stop
            if self._trailing_active:
                trail_stop = self._best_price * (1 - self.cfg.trailing_distance)
                if low <= trail_stop:
                    return self._close_position(price, "trailing", trail_stop)

        elif self.position == -1:  # Short
            if price < self._best_price:
                self._best_price = price
                if (self.entry_price - self._best_price) / self.entry_price >= self.cfg.trailing_activation:
                    self._trailing_active = True

            # TP
            if low <= self.entry_price * (1 - self.cfg.tp_pct):
                return self._close_position(price, "tp", self.entry_price * (1 - self.cfg.tp_pct))

            # SL
            if high >= self.entry_price * (1 + self.cfg.sl_pct):
                return self._close_position(price, "sl", self.entry_price * (1 + self.cfg.sl_pct))

            # Trailing stop
            if self._trailing_active:
                trail_stop = self._best_price * (1 + self.cfg.trailing_distance)
                if high >= trail_stop:
                    return self._close_position(price, "trailing", trail_stop)

        # Timeout
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

        trade = MomentumTrade(
            trade_id=self._trade_counter,
            direction=self.position,
            entry_price=self.entry_price,
            exit_price=exit_price,
            exit_reason=reason,
            pnl=pnl_dollars,
            bars_held=self.bars_held,
            spike_size=self._pending_spike_size,
            spike_direction='up' if self._pending_direction == 1 else 'down',
            entry_bar=self.entry_bar,
        )
        self.trade_log.append(trade)
        self.equity_curve.append({"equity": self.equity, "dd": dd})

        self.position = 0
        self.entry_price = 0.0
        self.bars_held = 0
        self._trailing_active = False
        self._best_price = 0.0

        return {
            "action": "close",
            "exit_price": exit_price,
            "exit_reason": reason,
            "pnl": pnl_dollars,
            "equity": self.equity,
        }


def run_momentum_backtest(symbol: str, tp_pct: float = 0.003, sl_pct: float = 0.0008,
                          max_hold: int = 20, spike_mult: float = 3.0,
                          risk_pct: float = 0.10, confirm_bars_max: int = 5) -> dict:
    """Run momentum backtest on parquet data and return results."""
    import pandas as pd

    data_file = Path(__file__).parent.parent / "data" / f"candles_{symbol.lower()}_1min.parquet"
    if not data_file.exists():
        return {"error": f"No data file: {data_file}"}

    df = pd.read_parquet(data_file)
    eps = df['epoch'].values.astype(int) if 'epoch' in df.columns else np.zeros(len(df), dtype=int)
    candles = [
        {"epoch": int(eps[i]), "open": float(df['open'].values[i]),
         "high": float(df['high'].values[i]), "low": float(df['low'].values[i]),
         "close": float(df['close'].values[i]), "volume": 1}
        for i in range(len(df))
    ]

    cfg = MomentumConfig(
        spike_mult=spike_mult,
        tp_pct=tp_pct,
        sl_pct=sl_pct,
        max_hold=max_hold,
        confirm_bars_max=confirm_bars_max,
    )
    strat = MomentumStrategy(symbol, config=cfg, risk_pct=risk_pct)

    for c in candles:
        strat.on_bar(c)

    closes = [t for t in strat.trade_log]
    n = len(closes)
    if n == 0:
        return {"trades": 0, "symbol": symbol}

    pnls = [t.pnl for t in closes]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]

    wr = len(wins) / n if n > 0 else 0
    tw = sum(wins)
    tl = abs(sum(losses))
    pf = tw / tl if tl > 0 else 99
    ret = (strat.equity / strat.initial_equity - 1) * 100
    max_dd = max((e["dd"] for e in strat.equity_curve), default=0) * 100
    avg_win = np.mean(wins) if wins else 0
    avg_loss = np.mean(losses) if losses else 0

    # Exit breakdown
    exit_counts = {}
    for t in closes:
        exit_counts[t.exit_reason] = exit_counts.get(t.exit_reason, 0) + 1

    return {
        "symbol": symbol,
        "trades": n,
        "win_rate": wr,
        "profit_factor": pf,
        "total_return_pct": ret,
        "max_dd_pct": max_dd,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "exit_breakdown": exit_counts,
        "equity": strat.equity,
        "config": {
            "tp_pct": tp_pct, "sl_pct": sl_pct, "max_hold": max_hold,
            "spike_mult": spike_mult, "risk_pct": risk_pct,
        },
    }


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  MOMENTUM CONTINUATION BACKTEST — BOOM500 & CRASH500")
    print("=" * 70)

    for sym in ["BOOM500", "CRASH500"]:
        print(f"\n--- {sym} ---")
        # Test multiple TP/SL combos
        for tp, sl, hold in [(0.003, 0.0008, 20), (0.002, 0.0006, 15),
                              (0.004, 0.001, 25), (0.003, 0.0005, 20)]:
            r = run_momentum_backtest(sym, tp_pct=tp, sl_pct=sl, max_hold=hold)
            if r.get("trades", 0) > 0:
                print(f"  TP={tp*100:.2f}% SL={sl*100:.3f}% Hold={hold:2d}  "
                      f"  {r['trades']:3d}T  WR={r['win_rate']*100:5.1f}%  "
                      f"PF={r['profit_factor']:5.2f}  Ret={r['total_return_pct']:+7.2f}%  "
                      f"DD={r['max_dd_pct']:.2f}%  Exits={r['exit_breakdown']}")
            else:
                print(f"  TP={tp*100:.2f}% SL={sl*100:.3f}% Hold={hold:2d}  No trades")
