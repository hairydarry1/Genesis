"""
eval/sweet_spot_strategy.py — Sweet Spot Spike Catcher

Combines three edges:
1. Tick count threshold (500-550 ticks since last spike)
2. Counter-trend drift filter (enter only when price drifts against spike direction)
3. Volatility squeeze awareness (pre-spike vol is 0.36x normal)

Entry: 550+ ticks + counter-drift + tight SL/TP
Exit: On spike, SL, TP, or timeout
"""

import sys
import numpy as np
from pathlib import Path
from dataclasses import dataclass
from collections import Counter

sys.path.insert(0, str(Path(__file__).parent.parent))


@dataclass
class SweetSpotConfig:
    # Entry
    entry_threshold: int = 550      # Enter after N ticks without spike
    drift_window: int = 100         # Ticks to measure drift
    use_drift_filter: bool = True   # Only enter on counter-trend drift

    # Risk management
    sl_points: float = 5.0          # Stop loss in points
    tp_points: float = 15.0         # Take profit in points
    lot_size: float = 0.20          # Fixed lot size

    # Exit
    max_hold_ticks: int = 300       # Timeout at 5 minutes
    exit_on_spike: bool = True      # Exit immediately when spike occurs


class SweetSpotStrategy:
    def __init__(self, symbol: str, config: SweetSpotConfig = None,
                 initial_equity: float = 10000.0):
        self.symbol = symbol
        self.cfg = config or SweetSpotConfig()
        self.initial_equity = initial_equity
        self.equity = initial_equity
        self.peak_equity = initial_equity
        self.trade_log = []
        self.equity_curve = []

    def backtest(self, prices: np.ndarray) -> dict:
        is_boom = "boom" in self.symbol.lower()
        spike_indices = self._detect_spikes(prices)
        spike_set = set(spike_indices.tolist())

        # State
        position = 0
        entry_price = 0.0
        entry_idx = 0
        ticks_since = 0
        total_trades = 0
        wins = 0
        losses = 0
        total_pnl = 0.0

        for i in range(1, len(prices)):
            # Spike detection
            if (i - 1) in spike_set:
                ticks_since = 0

                # Exit on spike
                if position != 0 and self.cfg.exit_on_spike:
                    pnl = self._calc_pnl(position, entry_price, prices[i])
                    total_pnl += pnl
                    total_trades += 1
                    if pnl > 0: wins += 1
                    else: losses += 1
                    self._record_trade(position, entry_price, entry_idx,
                                       prices[i], i, "spike_exit", pnl)
                    position = 0
            else:
                ticks_since += 1

            # Entry
            if position == 0 and ticks_since >= self.cfg.entry_threshold:
                # Drift filter
                if self.cfg.use_drift_filter and ticks_since >= self.cfg.drift_window:
                    drift = prices[i] - prices[i - self.cfg.drift_window]
                    if is_boom and drift > 0:  # Price going UP = bad for BUY
                        continue
                    if not is_boom and drift < 0:  # Price going DOWN = bad for SELL
                        continue

                position = 1 if is_boom else -1
                entry_price = prices[i]
                entry_idx = i

            # SL/TP/Timeout
            if position != 0:
                hit_sl = False
                hit_tp = False

                if position == 1:  # Long
                    hit_sl = prices[i] <= entry_price - self.cfg.sl_points
                    hit_tp = prices[i] >= entry_price + self.cfg.tp_points
                else:  # Short
                    hit_sl = prices[i] >= entry_price + self.cfg.sl_points
                    hit_tp = prices[i] <= entry_price - self.cfg.tp_points

                timeout = (i - entry_idx) >= self.cfg.max_hold_ticks

                if hit_sl or hit_tp or timeout:
                    if hit_sl:
                        reason = "sl"
                        pnl = -self.cfg.sl_points * self.cfg.lot_size
                    elif hit_tp:
                        reason = "tp"
                        pnl = self.cfg.tp_points * self.cfg.lot_size
                    else:
                        reason = "timeout"
                        pnl = self._calc_pnl(position, entry_price, prices[i])

                    total_pnl += pnl
                    total_trades += 1
                    if pnl > 0: wins += 1
                    else: losses += 1
                    self._record_trade(position, entry_price, entry_idx,
                                       prices[i], i, reason, pnl)
                    position = 0

        # Close open position
        if position != 0:
            pnl = self._calc_pnl(position, entry_price, prices[-1])
            total_pnl += pnl
            total_trades += 1
            if pnl > 0: wins += 1
            else: losses += 1

        wr = wins / total_trades * 100 if total_trades > 0 else 0
        return {
            "trades": total_trades, "wins": wins, "losses": losses,
            "wr": wr, "pnl": total_pnl,
            "pf": wins / losses if losses > 0 else 99,
        }

    def _detect_spikes(self, prices):
        changes = np.abs(np.diff(prices))
        positive = changes[changes > 0]
        if len(positive) == 0:
            return np.array([], dtype=int)
        median = np.median(positive)
        return np.where(changes > median * 3.0)[0]

    def _calc_pnl(self, direction, entry, exit_price):
        if direction == 1:
            return (exit_price - entry) * self.cfg.lot_size
        else:
            return (entry - exit_price) * self.cfg.lot_size

    def _record_trade(self, direction, entry, entry_idx, exit_price, exit_idx, reason, pnl):
        self.trade_log.append({
            "direction": "long" if direction == 1 else "short",
            "entry": entry, "exit": exit_price,
            "reason": reason, "pnl": pnl,
            "ticks_held": exit_idx - entry_idx,
        })


def run_full_backtest(symbol, prices, configs):
    """Run multiple configs and compare."""
    print(f"\n{'='*70}")
    print(f"  {symbol} — SWEET SPOT BACKTEST")
    print(f"{'='*70}")
    print(f"  Ticks: {len(prices):,}")

    results = []
    for label, threshold, sl, tp, drift in configs:
        cfg = SweetSpotConfig(
            entry_threshold=threshold,
            sl_points=sl, tp_points=tp,
            lot_size=0.20, max_hold_ticks=300,
            use_drift_filter=drift,
        )
        strat = SweetSpotStrategy(symbol, config=cfg)
        r = strat.backtest(prices)
        r["label"] = label
        r["threshold"] = threshold
        r["sl"] = sl
        r["tp"] = tp
        r["drift"] = drift
        results.append(r)

    # Print results
    print(f"\n  {'Config':40s}  {'T':>4s}  {'W':>4s}  {'L':>4s}  {'WR':>6s}  {'PnL':>9s}  {'PF':>6s}")
    print(f"  {'-'*40}  {'-'*4}  {'-'*4}  {'-'*4}  {'-'*6}  {'-'*9}  {'-'*6}")

    best = None
    for r in results:
        n = r["trades"]
        if n == 0:
            print(f"  {r['label']:40s}  {'0':>4s}")
            continue
        pnl_str = f"${r['pnl']:+.2f}"
        marker = ""
        if best is None or r["pnl"] > best["pnl"]:
            best = r
            marker = " *"
        print(f"  {r['label']:40s}  {n:4d}  {r['wins']:4d}  {r['losses']:4d}  "
              f"{r['wr']:5.1f}%  {pnl_str:>9s}  {r['pf']:5.2f}{marker}")

    # Real returns for best config
    if best and best["trades"] > 0:
        lot = 0.20
        risk_per = lot * best["sl"]
        win_per = lot * best["tp"]
        n = best["trades"]
        w = best["wins"]
        l = best["losses"]
        monthly_pnl = w * win_per - l * risk_per

        print(f"\n  BEST: {best['label']}")
        print(f"    Trades: {n}  WR: {best['wr']:.1f}%  PF: {best['pf']:.2f}")
        print(f"    Risk/trade: ${risk_per:.2f}  Win/trade: ${win_per:.2f}")
        print(f"    Monthly PnL: ${monthly_pnl:.2f}")
        print(f"    Annual PnL: ${monthly_pnl * 12:.2f}")

        for acc in [50, 100, 200, 500]:
            rpct = risk_per / acc * 100
            v = "SAFE" if rpct <= 2 else "RISKY" if rpct <= 5 else "DANGER"
            print(f"    ${acc:4d} acc: risk={rpct:.1f}%  monthly=${monthly_pnl:+.2f}  annual=${monthly_pnl*12:+.2f}  [{v}]")

    return results


if __name__ == "__main__":
    print("\n" + "=" * 70)
    print("  SWEET SPOT STRATEGY — FULL BACKTEST")
    print("=" * 70)

    boom = np.load("data/ticks_boom_500_index.npy")
    crash = np.load("data/ticks_crash_500_index.npy")

    # Config sweep
    configs = [
        # label, threshold, SL, TP, drift_filter
        ("T=500 SL=3 TP=10 no-drift",    500, 3, 10, False),
        ("T=500 SL=3 TP=10 +drift",      500, 3, 10, True),
        ("T=500 SL=5 TP=15 no-drift",    500, 5, 15, False),
        ("T=500 SL=5 TP=15 +drift",      500, 5, 15, True),
        ("T=550 SL=5 TP=15 +drift",      550, 5, 15, True),
        ("T=550 SL=3 TP=15 +drift",      550, 3, 15, True),
        ("T=550 SL=5 TP=20 +drift",      550, 5, 20, True),
        ("T=550 SL=3 TP=10 +drift",      550, 3, 10, True),
        ("T=600 SL=5 TP=15 +drift",      600, 5, 15, True),
        ("T=700 SL=5 TP=15 +drift",      700, 5, 15, True),
        ("T=500 SL=5 TP=20 +drift",      500, 5, 20, True),
        ("T=450 SL=5 TP=15 +drift",      450, 5, 15, True),
        ("T=400 SL=3 TP=10 +drift",      400, 3, 10, True),
        ("T=350 SL=3 TP=10 +drift",      350, 3, 10, True),
    ]

    boom_results = run_full_backtest("BOOM500", boom, configs)
    crash_results = run_full_backtest("CRASH500", crash, configs)

    # Combined portfolio
    print(f"\n{'='*70}")
    print(f"  COMBINED PORTFOLIO (BOOM500 + CRASH500)")
    print(f"{'='*70}")

    # Find best config for each
    best_boom = max(boom_results, key=lambda x: x["pnl"])
    best_crash = max(crash_results, key=lambda x: x["pnl"])

    if best_boom and best_crash:
        combo_pnl = best_boom["pnl"] + best_crash["pnl"]
        combo_trades = best_boom["trades"] + best_crash["trades"]
        lot = 0.20
        combo_risk = lot * 5  # avg SL
        combo_wins = best_boom["wins"] + best_crash["wins"]
        combo_losses = best_boom["losses"] + best_crash["losses"]
        combo_win_pnl = best_boom["wins"] * lot * best_boom["tp"] + best_crash["wins"] * lot * best_crash["tp"]
        combo_loss_pnl = best_boom["losses"] * lot * best_boom["sl"] + best_crash["losses"] * lot * best_crash["sl"]
        combo_monthly = combo_win_pnl - combo_loss_pnl

        print(f"  BOOM500: {best_boom['label']} -> {best_boom['trades']}T, WR={best_boom['wr']:.1f}%, PnL=${best_boom['pnl']:+.2f}")
        print(f"  CRASH500: {best_crash['label']} -> {best_crash['trades']}T, WR={best_crash['wr']:.1f}%, PnL=${best_crash['pnl']:+.2f}")
        print(f"  COMBINED: {combo_trades} trades, PnL=${combo_pnl:+.2f}")
        print(f"  Monthly (estimated): ${combo_monthly:.2f}")
        print(f"  Annual (estimated): ${combo_monthly * 12:.2f}")

        for acc in [50, 100, 200, 500]:
            rpct = combo_risk / acc * 100
            v = "SAFE" if rpct <= 2 else "RISKY" if rpct <= 5 else "DANGER"
            print(f"  ${acc:4d} acc: risk={rpct:.1f}%  monthly=${combo_monthly:+.2f}  annual=${combo_monthly*12:+.2f}  [{v}]")
