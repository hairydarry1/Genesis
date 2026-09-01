"""
eval/paper_trading_vol10.py — Focused VOL10 paper trading with optimized strategy.

Strategy (from parameter sweep):
  - SNR threshold: 0.30 (high conviction only)
  - Take profit: 0.30%
  - Stop loss: 0.10% (asymmetric 3:1)
  - Max hold: 60 bars (1 hour)
  - Exit on signal reversal

This is a stripped-down, production-ready version that:
  1. Connects to Deriv for live VOL10 1-min candles
  2. Runs world model prediction
  3. Trades only when SNR > 0.30
  4. Uses clean data checkpoints only
  5. Logs all trades for later analysis

Usage:
    python -m eval.paper_trading_vol10
    python -m eval.paper_trading_vol10 --llm
    python -m eval.paper_trading_vol10 --bars 1000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import torch

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# VOL10 strategy parameters (from walk-forward optimization)
# ---------------------------------------------------------------------------
# Walk-forward results (5 windows, 30K-bar test):
#   Most common SNR:  0.20 (appeared 2/5 windows)
#   Most common TP:   0.20% (appeared 3/5 windows)
#   Most common SL:   0.10% (appeared 2/5 windows)
#   Most common Hold: 30 bars (appeared 4/5 windows)
#   Avg test return:  -0.35% (2/5 windows profitable)
#
# Verdict: Edge is thin and regime-dependent.
# Use conservative params + strict filters to survive.
# ---------------------------------------------------------------------------
SNR_THRESHOLD = 0.25        # Walk-forward: 0.20-0.25 range (conservative midpoint)
TAKE_PROFIT = 0.0020        # Walk-forward: 0.20% (appeared 3/5 windows)
STOP_LOSS = 0.0015          # Walk-forward: 0.10-0.15% (wider for safety)
MAX_HOLD = 30               # Walk-forward: 30 bars (appeared 4/5 windows)
INITIAL_EQUITY = 10000.0

# Multi-timeframe confirmation
# Feature indices in the 8-feature vector:
#   0: 15min_return_1        4: 15min_range_ATR
#   1: 15min_autocorrelation  5: 1h_return_1
#   2: 15min_vol_regime       6: nearest_structure_distance
#   3: 15min_price_z_score    7: 15min_up_streak
TF_15M_RETURN = 0            # 15-min return index
TF_1H_RETURN = 5             # 1-hour return index
TF_15M_STREAK = 7            # 15-min streak index
TF_15M_VOL_REGIME = 2        # volatility regime index

# Feature names used by the world model (8 tiny features)
TINY_FEATURES = [
    "15min_return_1", "15min_autocorrelation", "15min_vol_regime",
    "15min_price_z_score", "15min_range_ATR", "1h_return_1",
    "nearest_structure_distance", "15min_up_streak",
]


class VOL10Trader:
    """Clean VOL10 paper trader with optimized strategy."""

    def __init__(self, enable_llm=False):
        self.equity = INITIAL_EQUITY
        self.peak_equity = INITIAL_EQUITY
        self.position = 0  # +1 long, -1 short, 0 flat
        self.entry_price = 0.0
        self.entry_step = 0
        self.trade_count = 0
        self.win_count = 0
        self.total_pnl = 0.0
        self.total_costs = 0.0
        self.trade_log: list[dict] = []
        self.candles: list[dict] = []
        self.enable_llm = enable_llm
        self._candle_count = 0

        # Model (ensemble)
        self.ensemble = None
        self.wm_feature_names: list[str] = []
        self.wm_norm_mean = None
        self.wm_norm_std = None
        self.state_builder = None

        # Regime + sizing + filters
        from model.regime import RegimeDetector, KellySizer, SessionFilter, VolatilityFilter
        from model.exit_engine import ExitEngine
        self.regime_det = RegimeDetector()
        self.kelly = KellySizer()
        self.session_filter = SessionFilter()
        self.vol_filter = VolatilityFilter()
        self.exit_engine = ExitEngine()
        self.current_regime = None
        self.current_session = None
        self.position_size_frac = 0.25  # Default 25% of equity

        # LLM
        self.strategy_agent = None
        self._last_llm = None

    def load_model(self):
        """Load the VOL10 ensemble (tiny + wide models)."""
        from model.ensemble import VOL10Ensemble

        self.ensemble = VOL10Ensemble()
        self.ensemble.load()

        self.wm_feature_names = self.ensemble.feature_names

        # Get normalization from tiny model checkpoint
        ckpt = torch.load("checkpoints_vol10/world_model_best.pt", map_location="cpu", weights_only=False)
        self.wm_norm_mean = ckpt.get("feat_mean") or ckpt.get("norm_mean")
        self.wm_norm_std = ckpt.get("feat_std") or ckpt.get("norm_std")

        # Load LLM if enabled
        if self.enable_llm:
            try:
                import os
                from model.strategy_agent import StrategyAgent
                config = {
                    "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
                    "llm_enabled": True,
                }
                self.strategy_agent = StrategyAgent.from_config(config)
                print(f"  LLM enabled: {self.strategy_agent.backends[0].__class__.__name__}")
            except Exception as e:
                print(f"  LLM failed ({e}), using WM only")

    def add_candle(self, candle: dict):
        self.candles.append(candle)
        if len(self.candles) > 2000:
            self.candles = self.candles[-2000:]

    def rebuild_state_builder(self):
        if len(self.candles) < 100:
            return
        from features.state import StateBuilder
        df = pd.DataFrame(self.candles)
        if "timestamp" not in df.columns:
            df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_localize(None)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        self.state_builder = StateBuilder(df, timeframes=["1min", "15min", "1h"])

    def compute_features(self) -> np.ndarray | None:
        if self.state_builder is None:
            return None
        try:
            ts = pd.Timestamp(self.candles[-1]["epoch"], unit="s", tz="UTC").tz_localize(None)
            state = self.state_builder.build_state(ts)
            feat = np.array([state.get(f, 0.0) for f in self.wm_feature_names], dtype=np.float32)
            if self.wm_norm_mean is not None and self.wm_norm_std is not None:
                feat = (feat - self.wm_norm_mean) / (self.wm_norm_std + 1e-8)
            return feat
        except Exception as e:
            return None

    def predict(self, features: np.ndarray) -> tuple[float, float]:
        """Run ensemble prediction. Returns (total_mean, snr)."""
        return self.ensemble.predict(features)

    def check_exit(self, current_price: float, total_mean: float) -> tuple[bool, str]:
        """Check if we should exit using the exit engine."""
        if self.position == 0:
            return False, ""

        decision = self.exit_engine.check_exit(
            current_price=current_price,
            position=self.position,
            current_bar=self._candle_count,
            max_hold=MAX_HOLD,
            signal_mean=total_mean,
        )
        return decision.should_exit, decision.reason

    def mtf_confirm(self, features: np.ndarray, model_direction: int) -> tuple[bool, str]:
        """
        Multi-timeframe confirmation using raw candle data (not normalized features).
        Returns (confirmed, reason).

        Uses actual price returns computed from candles:
          1. 15-min return: last 15 candles
          2. 1-hour return: last 60 candles
          3. Trend consistency: recent candles in same direction
        """
        if len(self.candles) < 61:
            return True, "insufficient_history"  # Allow trades early on

        closes = np.array([c["close"] for c in self.candles])

        # 15-minute return (last 15 candles)
        ret_15m = (closes[-1] - closes[-16]) / closes[-16] if len(closes) >= 16 else 0

        # 1-hour return (last 60 candles)
        ret_1h = (closes[-1] - closes[-61]) / closes[-61] if len(closes) >= 61 else 0

        # Trend consistency: count candles in same direction as model
        recent = closes[-10:]
        up_count = sum(1 for i in range(1, len(recent)) if recent[i] > recent[i-1])
        trend_pct = up_count / (len(recent) - 1)

        # Volatility filter: avoid trading during extreme moves
        recent_vol = np.std(np.diff(np.log(np.maximum(closes[-20:], 1))))
        if recent_vol > 0.005:  # Extreme volatility
            return False, f"vol_extreme ({recent_vol:.4f})"

        # 15-min confirmation
        if model_direction == 1 and ret_15m < -0.001:  # Strong bearish 15m while model says UP
            return False, f"15m_bearish ({ret_15m:+.4f})"
        if model_direction == -1 and ret_15m > 0.001:   # Strong bullish 15m while model says DOWN
            return False, f"15m_bullish ({ret_15m:+.4f})"

        # 1-hour confirmation (looser threshold — higher TF, more weight)
        if model_direction == 1 and ret_1h < -0.003:   # Strong bearish hourly
            return False, f"1h_bearish ({ret_1h:+.4f})"
        if model_direction == -1 and ret_1h > 0.003:    # Strong bullish hourly
            return False, f"1h_bullish ({ret_1h:+.4f})"

        # Trend consistency: at least 40% of recent candles should agree
        if model_direction == 1 and trend_pct < 0.3:
            return False, f"trend_weak ({trend_pct:.0%} up)"
        if model_direction == -1 and trend_pct > 0.7:
            return False, f"trend_weak ({trend_pct:.0%} up)"

        return True, "confirmed"

    def execute(self, action: int, current_price: float):
        """Execute a trade: action 1=long, -1=short, 0=close."""
        new_position = action

        # Exit existing position
        if self.position != 0 and new_position != self.position:
            pnl_pct = (current_price - self.entry_price) / self.entry_price * self.position
            cost = abs(pnl_pct) * 0.0003 * 2  # 0.03% round trip
            # Apply position sizing (Kelly)
            sized_pnl = self.position_size_frac * pnl_pct
            sized_cost = self.position_size_frac * cost
            net_pnl = self.equity * (sized_pnl - sized_cost)
            self.equity += net_pnl
            self.total_pnl += net_pnl
            self.total_costs += self.equity * sized_cost
            self.peak_equity = max(self.peak_equity, self.equity)

            win = net_pnl > 0
            self.trade_count += 1
            if win:
                self.win_count += 1

            # Record in Kelly sizer and exit engine
            self.kelly.record_trade(net_pnl)
            self.exit_engine.record_trade(win)

            side = "LONG" if self.position > 0 else "SHORT"
            streak_status = self.exit_engine.streak.get_status()
            self.trade_log.append({
                "time": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                "side": side,
                "entry": self.entry_price,
                "exit": current_price,
                "pnl": round(net_pnl, 2),
                "pnl_pct": round(pnl_pct * 100, 4),
                "cost": round(sized_cost * self.equity, 2),
                "win": win,
                "equity": round(self.equity, 2),
                "regime": self.current_regime.regime.value if self.current_regime else "unknown",
                "session": self.current_session or "unknown",
                "kelly_size": round(self.position_size_frac, 3),
                "consec_losses": streak_status["consec_losses"],
            })

            print(f"  {'✅' if win else '❌'} CLOSE {side} @ {current_price:.2f}  PnL: ${net_pnl:+.2f} ({pnl_pct*100:+.3f}%)  Size: {self.position_size_frac*100:.0f}%  Streak: {streak_status['consec_losses']}L/{streak_status['consec_wins']}W  Equity: ${self.equity:.2f}")

        # Open new position
        if new_position != 0 and new_position != self.position:
            self.position = new_position
            self.entry_price = current_price
            self.entry_step = self._candle_count
            self.exit_engine.start_trade(current_price, self._candle_count)
            side = "LONG" if new_position > 0 else "SHORT"
            print(f"  {'🟢' if new_position > 0 else '🔴'} OPEN {side} @ {current_price:.2f}  Size: {self.position_size_frac*100:.0f}%")

        elif new_position == 0 and self.position != 0:
            self.position = 0
            self.entry_price = 0.0

    def handle_tick(self, msg: dict):
        """Handle incoming tick from Deriv."""
        tick = msg.get("tick", {})
        if not tick:
            return

        quote = float(tick.get("quote", 0))
        epoch = int(tick.get("epoch", 0))
        if quote <= 0 or epoch <= 0:
            return

        candle_epoch = (epoch // 60) * 60

        if candle_epoch != getattr(self, "_current_epoch", 0):
            # Finalize previous candle
            if hasattr(self, "_current_candle") and self._current_candle:
                self.add_candle(self._current_candle)
                self._candle_count += 1

                if self._candle_count % 50 == 0:
                    self.rebuild_state_builder()

                self._run_cycle()

            # Start new candle
            self._current_epoch = candle_epoch
            self._current_candle = {
                "epoch": candle_epoch, "open": quote, "high": quote,
                "low": quote, "close": quote, "volume": 0,
            }
        elif hasattr(self, "_current_candle") and self._current_candle:
            self._current_candle["high"] = max(self._current_candle["high"], quote)
            self._current_candle["low"] = min(self._current_candle["low"], quote)
            self._current_candle["close"] = quote

    def _run_cycle(self):
        """One prediction + trade cycle."""
        features = self.compute_features()
        if features is None:
            return

        total_mean, snr = self.predict(features)
        current_price = self.candles[-1]["close"]
        direction = "▲" if total_mean > 0 else "▼"

        # --- REGIME DETECTION ---
        closes = np.array([c["close"] for c in self.candles])
        self.current_regime = self.regime_det.detect(closes)

        # --- SESSION CHECK ---
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        can_trade_session, session_name = self.session_filter.can_trade(now)
        self.current_session = session_name

        # --- VOLATILITY FILTER ---
        vol_ok, vol_reason = self.vol_filter.check(closes, self._candle_count)

        # --- EXIT ENGINE UPDATE ---
        self.exit_engine.update(closes, self._candle_count)

        # --- KELLY + DRAWDOWN + STREAK SIZING ---
        kelly_base = self.kelly.compute_size() if self.kelly.total_trades >= 20 else 0.25
        self.position_size_frac = self.exit_engine.get_position_size(
            kelly_base, self.equity, self.peak_equity
        )

        # Check streak
        streak_ok, streak_reason = self.exit_engine.streak.can_trade()

        # Check exit first
        if self.position != 0:
            should_exit, reason = self.check_exit(current_price, total_mean)
            if should_exit:
                self.execute(0, current_price)
                print(f"  [{reason}]")

        # Check entry (only if flat and SNR above threshold)
        if self.position == 0 and snr > SNR_THRESHOLD:
            model_dir = 1 if total_mean > 0 else (-1 if total_mean < 0 else 0)
            if model_dir != 0:
                # Filter 1: Regime check
                if not self.current_regime.should_trade:
                    print(f"  [REGIME BLOCKED: {self.current_regime.reason}]")
                # Filter 2: Session check
                elif not can_trade_session:
                    print(f"  [SESSION BLOCKED: {session_name}]")
                # Filter 3: Volatility check
                elif not vol_ok:
                    print(f"  [VOL BLOCKED: {vol_reason}]")
                # Filter 4: Streak check
                elif not streak_ok:
                    print(f"  [STREAK BLOCKED: {streak_reason}]")
                # Filter 5: MTF confirmation
                else:
                    confirmed, mtf_reason = self.mtf_confirm(features, model_dir)
                    if confirmed:
                        self.execute(model_dir, current_price)
                    else:
                        print(f"  [MTF BLOCKED: {mtf_reason}]")

        # LLM reasoning (optional, informational only)
        if self.enable_llm and self.strategy_agent and self._candle_count % 5 == 0:
            try:
                from model.context_builder import ContextBuilder
                ctx = ContextBuilder()
                context = ctx.build(
                    features=dict(zip(self.wm_feature_names, features)),
                    wm_pred_mean=np.array([total_mean]),
                    wm_pred_std=np.array([abs(total_mean) / snr if snr > 0 else 0.01]),
                    current_price=current_price,
                    equity=self.equity,
                    peak_equity=self.peak_equity,
                    position=float(self.position),
                )
                llm_decision = self.strategy_agent.decide(context)
                self._last_llm = llm_decision
            except Exception:
                pass

        # Status line
        pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}[self.position]
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # Regime + session + Kelly + ATR
        regime_str = self.current_regime.regime.value if self.current_regime else "--"
        session_str = self.current_session or "--"
        kelly_str = f"{self.position_size_frac*100:.0f}%"
        atr_tp = self.exit_engine.adaptive.get_tp() * 100
        atr_sl = self.exit_engine.adaptive.get_sl() * 100
        streak = self.exit_engine.streak.get_status()
        streak_str = f"{streak['consec_losses']}L/{streak['consec_wins']}W"

        win_pct = (self.win_count / self.trade_count * 100) if self.trade_count > 0 else 0
        print(
            f"  [{ts}] {direction} SNR={snr:.3f}  "
            f"Regime={regime_str} Session={session_str} Size={kelly_str}  "
            f"TP={atr_tp:.2f}% SL={atr_sl:.2f}% Streak={streak_str}  "
            f"Pos={pos_str}  Equity=${self.equity:.2f}  "
            f"PnL=${self.total_pnl:+.2f}  DD={dd:.1f}%  "
            f"Trades={self.trade_count} WR={win_pct:.0f}%"
        )

    async def run(self):
        """Main async loop."""
        from data.deriv_connection import DerivConnection

        print("\n" + "=" * 70)
        print("  VOL10 PAPER TRADING (ENSEMBLE + REGIME + KELLY)")
        print(f"  Strategy: SNR>{SNR_THRESHOLD}, TP={TAKE_PROFIT*100:.2f}%, SL={STOP_LOSS*100:.2f}%, Hold={MAX_HOLD} bars")
        print(f"  Filters: Regime detection, Session filter, Volatility filter, MTF confirmation")
        print(f"  Sizing: Kelly Criterion (25% fractional)")
        print("=" * 70)

        self.load_model()

        conn = DerivConnection()
        await conn.connect()
        print("  Connected to Deriv")

        try:
            # Fetch history
            resp = await conn.get_ticks_history(
                "R_10", style="candles", count=500, granularity=60,
            )
            for c in resp.get("candles", []):
                self.add_candle({
                    "epoch": c["epoch"], "open": float(c["open"]),
                    "high": float(c["high"]), "low": float(c["low"]),
                    "close": float(c["close"]), "volume": float(c.get("volume", 0)),
                })
            print(f"  History loaded: {len(self.candles)} candles")

            self.rebuild_state_builder()
            self._run_cycle()

            sub_id = await conn.subscribe({"ticks": "R_10"}, handler=self.handle_tick)
            print(f"  Live stream active (sub={sub_id})")
            print("  Waiting for ticks...\n")

            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            pass
        finally:
            await conn.close()
            self._print_summary()

    def _print_summary(self):
        print(f"\n{'='*60}")
        print("  VOL10 PAPER TRADING SUMMARY")
        print(f"{'='*60}")
        print(f"  Final equity:  ${self.equity:.2f}")
        print(f"  Total PnL:     ${self.total_pnl:+.2f}")
        print(f"  Return:        {(self.equity/INITIAL_EQUITY - 1)*100:+.2f}%")
        print(f"  Total costs:   ${self.total_costs:.2f}")
        print(f"  Trades:        {self.trade_count}")
        if self.trade_count > 0:
            print(f"  Win rate:      {self.win_count/self.trade_count*100:.0f}%")
            print(f"  Avg PnL:       ${self.total_pnl/self.trade_count:+.2f}")
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100
        print(f"  Max drawdown:  {dd:.1f}%")
        print(f"{'='*60}\n")

        # Save trades
        out = Path("checkpoints_vol10/paper_trades.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        with open(out, "w") as f:
            json.dump(self.trade_log, f, indent=2, default=str)
        print(f"  Trades saved to: {out}")


def main():
    parser = argparse.ArgumentParser(description="VOL10 Paper Trading")
    parser.add_argument("--llm", action="store_true", help="Enable LLM reasoning")
    parser.add_argument("--bars", type=int, default=500, help="History bars to load")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    trader = VOL10Trader(enable_llm=args.llm)
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
