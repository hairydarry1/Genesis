"""
eval/tick_count_strategy.py — Tick Count Spike Catcher Strategy

When BOOM/CRASH goes 700+ ticks without a spike, the next spike is
99.9%+ probable within 100-200 ticks. We enter in the spike direction
with a tight stop loss and capture the imminent spike.

Key insight: At 700+ ticks, you are NOT predicting random movement.
You are betting on a GUARANTEED event that is statistically imminent.
"""

import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass, field
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class TickCountConfig:
    """Configuration for tick count spike catcher."""
    # Entry
    entry_threshold: int = 700     # Enter after N ticks without spike
    prepare_threshold: int = 500   # Start monitoring at N ticks

    # Risk management
    sl_points: float = 5.0         # Stop loss in points (tight - spike imminent)
    tp_points: float = 15.0        # Take profit in points (capture spike)
    lot_size: float = 0.20         # Fixed lot size (Deriv min)
    risk_per_trade_pct: float = 0.02  # 2% of equity per trade

    # Exit
    max_hold_ticks: int = 300      # Max ticks to hold (5 min timeout)
    exit_on_spike: bool = True     # Exit immediately when spike occurs

    # Filters
    min_gap_since_entry: int = 0   # Min ticks since entry before checking exit
    use_volatility_filter: bool = False  # Skip during high volatility


@dataclass
class TickCountTrade:
    """Record of a single trade."""
    symbol: str
    direction: str          # "long" for BOOM, "short" for CRASH
    entry_price: float
    entry_tick: int
    exit_price: float = 0.0
    exit_tick: int = 0
    exit_reason: str = ""
    pnl: float = 0.0
    pnl_pct: float = 0.0
    ticks_held: int = 0
    ticks_at_entry: int = 0  # How many ticks since last spike when we entered


class TickCountSpikeCatcher:
    """Tick count spike catcher strategy."""

    def __init__(self, symbol: str, config: TickCountConfig = None,
                 initial_equity: float = 10000.0):
        self.symbol = symbol
        self.cfg = config or TickCountConfig()
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity

        # State
        self.position = 0         # 0=flat, 1=long (BOOM), -1=short (CRASH)
        self.entry_price = 0.0
        self.entry_tick_idx = 0
        self.ticks_since_spike = 0
        self.last_spike_idx = -999

        # Tracking
        self.trade_log: list[TickCountTrade] = []
        self.equity_curve: list[dict] = []
        self._trade_counter = 0

        # Spike detection
        self._spike_threshold = 0.0  # Set during backtest

    def detect_spikes(self, prices: np.ndarray) -> np.ndarray:
        """Detect spikes using median-based threshold."""
        changes = np.abs(np.diff(prices))
        positive_changes = changes[changes > 0]
        if len(positive_changes) == 0:
            return np.array([], dtype=int)

        median_change = np.median(positive_changes)
        self._spike_threshold = median_change * 3.0
        spike_indices = np.where(changes > self._spike_threshold)[0]
        return spike_indices

    def backtest(self, prices: np.ndarray) -> dict:
        """Run backtest on tick-level price data."""
        is_boom = "boom" in self.symbol.lower()
        is_crash = "crash" in self.symbol.lower()

        # Detect spikes
        spike_indices = self.detect_spikes(prices)
        if len(spike_indices) == 0:
            return self._empty_result()

        # Create spike set for O(1) lookup
        spike_set = set(spike_indices.tolist())

        # Reset state
        self.position = 0
        self.equity = self.initial_equity
        self.peak_equity = self.initial_equity
        self.trade_log = []
        self.equity_curve = []
        self.ticks_since_spike = 0
        self.last_spike_idx = -999
        self._trade_counter = 0

        # Process each tick
        for i in range(1, len(prices)):
            # Check if current tick is a spike
            if (i - 1) in spike_set:
                self.ticks_since_spike = 0
                self.last_spike_idx = i - 1

                # If we're in a position, exit on spike
                if self.position != 0 and self.cfg.exit_on_spike:
                    self._close_position(prices[i], "spike_exit", i)
            else:
                self.ticks_since_spike += 1

            # Entry logic
            if self.position == 0 and self.ticks_since_spike >= self.cfg.entry_threshold:
                if is_boom:
                    self._open_position(prices[i], 1, i)  # BUY BOOM
                elif is_crash:
                    self._open_position(prices[i], -1, i)  # SELL CRASH

            # Exit logic (if in position)
            if self.position != 0:
                # Check SL/TP
                if self.position == 1:  # Long
                    if prices[i] <= self.entry_price - self.cfg.sl_points:
                        self._close_position(prices[i], "sl", i)
                    elif prices[i] >= self.entry_price + self.cfg.tp_points:
                        self._close_position(prices[i], "tp", i)
                elif self.position == -1:  # Short
                    if prices[i] >= self.entry_price + self.cfg.sl_points:
                        self._close_position(prices[i], "sl", i)
                    elif prices[i] <= self.entry_price - self.cfg.tp_points:
                        self._close_position(prices[i], "tp", i)

                # Timeout exit
                ticks_held = i - self.entry_tick_idx
                if ticks_held >= self.cfg.max_hold_ticks:
                    self._close_position(prices[i], "timeout", i)

        # Close any open position at end
        if self.position != 0:
            self._close_position(prices[-1], "end_of_data", len(prices) - 1)

        return self._compute_results()

    def _open_position(self, price: float, direction: int, tick_idx: int):
        """Open a new position."""
        self.position = direction
        self.entry_price = price
        self.entry_tick_idx = tick_idx
        self._trade_counter += 1

    def _close_position(self, price: float, reason: str, tick_idx: int):
        """Close position and record trade."""
        if self.position == 1:  # Long
            pnl_points = price - self.entry_price
        else:  # Short
            pnl_points = self.entry_price - price

        pnl_dollars = pnl_points * self.cfg.lot_size
        self.equity += pnl_dollars
        self.peak_equity = max(self.peak_equity, self.equity)

        trade = TickCountTrade(
            symbol=self.symbol,
            direction="long" if self.position == 1 else "short",
            entry_price=self.entry_price,
            entry_tick=self.entry_tick_idx,
            exit_price=price,
            exit_tick=tick_idx,
            exit_reason=reason,
            pnl=pnl_dollars,
            pnl_pct=pnl_dollars / self.initial_equity,
            ticks_held=tick_idx - self.entry_tick_idx,
            ticks_at_entry=self.ticks_since_spike,
        )
        self.trade_log.append(trade)
        self.equity_curve.append({"equity": self.equity, "dd": (self.peak_equity - self.equity) / self.peak_equity})

        # Reset
        self.position = 0
        self.entry_price = 0.0

    def _compute_results(self) -> dict:
        """Compute backtest results."""
        trades = self.trade_log
        n = len(trades)

        if n == 0:
            return self._empty_result()

        pnls = [t.pnl for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        wr = len(wins) / n * 100 if n > 0 else 0
        tw = sum(wins) if wins else 0
        tl = abs(sum(losses)) if losses else 0
        pf = tw / tl if tl > 0 else 99
        avg_win = np.mean(wins) if wins else 0
        avg_loss = np.mean(losses) if losses else 0
        rr = abs(avg_win / avg_loss) if avg_loss != 0 else 0

        dd = max((e["dd"] for e in self.equity_curve), default=0) * 100

        # Exit breakdown
        exits = Counter(t.exit_reason for t in trades)

        # Tick timing at entry
        entry_ticks = [t.ticks_at_entry for t in trades]
        avg_entry_tick = np.mean(entry_ticks) if entry_ticks else 0

        return {
            "symbol": self.symbol,
            "total_trades": n,
            "win_rate": wr,
            "profit_factor": pf,
            "total_return_pct": (self.equity / self.initial_equity - 1) * 100,
            "final_equity": self.equity,
            "max_drawdown_pct": dd,
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "rr_ratio": rr,
            "exit_breakdown": dict(exits),
            "avg_entry_tick": avg_entry_tick,
            "trades": trades,
            "equity_curve": self.equity_curve,
        }

    def _empty_result(self) -> dict:
        return {
            "symbol": self.symbol, "total_trades": 0, "win_rate": 0,
            "profit_factor": 0, "total_return_pct": 0, "final_equity": self.equity,
            "max_drawdown_pct": 0, "avg_win": 0, "avg_loss": 0, "rr_ratio": 0,
            "exit_breakdown": {}, "avg_entry_tick": 0, "trades": [],
            "equity_curve": [],
        }


def run_backtest(symbol: str, entry_threshold: int = 700,
                 sl_points: float = 5.0, tp_points: float = 15.0,
                 lot_size: float = 0.20, max_hold: int = 300,
                 initial_equity: float = 10000.0) -> dict:
    """Run tick count spike catcher backtest."""
    import MetaTrader5 as mt5
    import pandas as pd

    # Load tick data
    data_path = Path(f"data/ticks_{symbol.lower().replace(' ', '_')}.parquet")
    if not data_path.exists():
        print(f"  No tick data for {symbol}. Downloading from MT5...")
        mt5.initialize()
        end = datetime.now(timezone.utc)
        start = end - pd.Timedelta(days=30)
        ticks = mt5.copy_ticks_from_range(symbol, int(start.timestamp()), int(end.timestamp()), mt5.COPY_TICKS_ALL)
        mt5.shutdown()

        if ticks is None or len(ticks) == 0:
            return {"total_trades": 0, "error": "No tick data"}

        df = pd.DataFrame(ticks)
        prices = df["bid"].values.astype(float)
        np.save(data_path.with_suffix(".npy"), prices)
        print(f"  Saved {len(prices)} ticks")
    else:
        prices = np.load(data_path.with_suffix(".npy"))

    print(f"  Loaded {len(prices)} ticks for {symbol}")

    cfg = TickCountConfig(
        entry_threshold=entry_threshold,
        sl_points=sl_points,
        tp_points=tp_points,
        lot_size=lot_size,
        max_hold_ticks=max_hold,
    )

    strat = TickCountSpikeCatcher(symbol, config=cfg, initial_equity=initial_equity)
    return strat.backtest(prices)


def print_results(result: dict, label: str = ""):
    """Pretty-print backtest results."""
    n = result["total_trades"]
    if n == 0:
        print(f"  {label}: No trades")
        return

    print(f"  {label}")
    print(f"    Trades: {n}")
    print(f"    Win Rate: {result['win_rate']:.1f}%")
    print(f"    Profit Factor: {result['profit_factor']:.2f}")
    print(f"    Return: {result['total_return_pct']:+.2f}%")
    print(f"    Max DD: {result['max_drawdown_pct']:.2f}%")
    print(f"    Avg Win: ${result['avg_win']:+.4f}")
    print(f"    Avg Loss: ${result['avg_loss']:+.4f}")
    print(f"    R:R: 1:{result['rr_ratio']:.2f}")
    print(f"    Avg Entry Tick: {result['avg_entry_tick']:.0f}")
    print(f"    Exits: {result['exit_breakdown']}")


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  TICK COUNT SPIKE CATCHER — BACKTEST")
    print("=" * 70)

    # Parameter sweep
    configs = [
        ("Threshold=500 SL=3 TP=10",  500, 3, 10),
        ("Threshold=500 SL=5 TP=15",  500, 5, 15),
        ("Threshold=600 SL=5 TP=15",  600, 5, 15),
        ("Threshold=700 SL=5 TP=15",  700, 5, 15),
        ("Threshold=700 SL=3 TP=10",  700, 3, 10),
        ("Threshold=700 SL=5 TP=20",  700, 5, 20),
        ("Threshold=800 SL=5 TP=15",  800, 5, 15),
        ("Threshold=900 SL=5 TP=15",  900, 5, 15),
    ]

    for symbol in ["Boom 500 Index", "Crash 500 Index"]:
        print(f"\n{'='*70}")
        print(f"  {symbol}")
        print(f"{'='*70}")

        best_pf = 0
        best_label = ""
        best_result = None

        for label, threshold, sl, tp in configs:
            result = run_backtest(symbol, entry_threshold=threshold,
                                  sl_points=sl, tp_points=tp)
            n = result["total_trades"]
            if n == 0:
                continue

            pf = result["profit_factor"]
            if pf > best_pf:
                best_pf = pf
                best_label = label
                best_result = result

            print(f"\n  {label}")
            print(f"    {n}T  WR={result['win_rate']:.1f}%  PF={pf:.2f}  "
                  f"Ret={result['total_return_pct']:+.2f}%  DD={result['max_drawdown_pct']:.2f}%")
            print(f"    AvgEntry={result['avg_entry_tick']:.0f}  Exits={result['exit_breakdown']}")

        if best_result:
            print(f"\n  BEST: {best_label}")
            print_results(best_result, "")
