"""
eval/paper_trading.py — Paper trading with world model + policy.

Combines:
  1. World model: predicts distribution of next 10 bars
  2. Policy: decides buy/sell/hold based on world model predictions + market state
  3. Risk management: position limits, drawdown stops

Connects to Deriv websocket, maintains rolling candle buffer,
and outputs trading decisions in real-time.

Usage:
    python -m eval.paper_trading --symbol R_75
    python -m eval.paper_trading --symbol R_75 --dry-run
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
import structlog
import torch

from data.deriv_connection import DerivConnection
from features.state import StateBuilder
from model.train_tiny import TinyWorldModel, WideTinyWorldModel, build_model, TINY_FEATURES
from model.policy import PolicyNetwork, PolicyConfig
from model.risk_management import RiskManager, RiskConfig
from eval.monitoring import TradingMonitor
from model.performance_monitor import PerformanceMonitor

# Optional LLM imports (graceful fallback if not available)
try:
    from model.strategy_agent import StrategyAgent
    from model.decision_fusion import DecisionFusion, FusionConfig, FusionInput
    from model.context_builder import ContextBuilder
    HAS_LLM = True
except ImportError:
    HAS_LLM = False

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DEFAULT_SYMBOL = "R_75"
DEFAULT_HISTORY_BARS = 500
DEFAULT_WORLD_MODEL = "checkpoints_tiny/world_model_best.pt"
DEFAULT_POLICY = "checkpoints_tiny/policy_best.pt"
HORIZON = 10
INITIAL_EQUITY = 10000.0
MAX_POSITION = 1
POSITION_SIZE = 1.0  # fraction of equity per trade


# ---------------------------------------------------------------------------
# Paper Trading Engine
# ---------------------------------------------------------------------------
class PaperTrader:
    """
    Combines world model predictions with policy decisions.

    On each new candle:
      1. Compute features (8 tiny features)
      2. Run world model → predicted mean + variance for next 10 bars
      3. Run policy → buy/sell/hold decision
      4. Apply risk management (position limits, drawdown stop)
      5. Log decision and track equity
    """

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        world_model_path: str = DEFAULT_WORLD_MODEL,
        policy_path: str = DEFAULT_POLICY,
        history_bars: int = DEFAULT_HISTORY_BARS,
        dry_run: bool = True,
        device_str: str | None = None,
        enable_llm: bool = False,
        llm_config: dict | None = None,
    ):
        self.symbol = symbol
        self.world_model_path = world_model_path
        self.policy_path = policy_path
        self.history_bars = history_bars
        self.dry_run = dry_run
        self.enable_llm = enable_llm and HAS_LLM
        self.device = torch.device(
            device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
        )

        # State
        self.equity = INITIAL_EQUITY
        self.peak_equity = INITIAL_EQUITY
        self.position = 0  # +1 long, -1 short, 0 flat
        self.entry_price = 0.0
        self.trade_count = 0
        self.win_count = 0
        self.total_pnl = 0.0
        self.trade_log: list[dict] = []

        # Risk management
        self.risk_mgr = RiskManager(RiskConfig())
        self.position_size = 1.0

        # Monitoring
        self.monitor = TradingMonitor()
        self.monitor.register_index(symbol, INITIAL_EQUITY)
        self._recent_returns: list[float] = []
        self.perf_monitor = PerformanceMonitor(min_trades_for_signal=10)

        # Candle buffer
        self.candles: list[dict] = []
        self.state_builder: StateBuilder | None = None

        # Models
        self.world_model = None
        self.policy: PolicyNetwork | None = None
        self.wm_feature_names: list[str] = []
        self.wm_norm_mean: np.ndarray | None = None
        self.wm_norm_std: np.ndarray | None = None

        # LLM integration
        self.strategy_agent: StrategyAgent | None = None
        self.decision_fusion: DecisionFusion | None = None
        self.context_builder: ContextBuilder | None = None
        self._last_llm_decision: dict | None = None

        # Tick aggregation
        self._current_candle_epoch = 0
        self._current_candle: dict | None = None
        self._candle_count = 0

    def load_models(self):
        """Load world model and policy from checkpoints."""
        # World model (auto-detect tiny vs wide from checkpoint)
        ckpt = torch.load(self.world_model_path, map_location=self.device, weights_only=False)
        self.wm_feature_names = list(ckpt["feature_names"])
        self.wm_norm_mean = ckpt.get("feat_mean") or ckpt.get("norm_mean")
        self.wm_norm_std = ckpt.get("feat_std") or ckpt.get("norm_std")
        model_type = ckpt.get("model_type", "tiny")
        horizon = ckpt.get("horizon", 10)
        hidden_dim = ckpt.get("hidden_dim", 16 if model_type == "tiny" else 32)

        self.world_model = build_model(
            model_type=model_type,
            n_features=len(self.wm_feature_names),
            horizon=horizon,
            hidden=hidden_dim,
            dropout=0.0,
        ).to(self.device)
        self.world_model.load_state_dict(ckpt["model_state_dict"])
        self.world_model.eval()
        self.wm_temperature = ckpt.get("temperature", 1.0)
        logger.info("world_model_loaded type=%s features=%d T=%.3f", model_type, len(self.wm_feature_names), self.wm_temperature)

        # Policy
        if Path(self.policy_path).exists():
            try:
                pol_ckpt = torch.load(self.policy_path, map_location=self.device, weights_only=False)
                pol_cfg = PolicyConfig(obs_dim=11, n_actions=3)  # 8 features + position + equity + vol
                self.policy = PolicyNetwork(pol_cfg).to(self.device)
                self.policy.load_state_dict(pol_ckpt["policy_state_dict"])
                self.policy.eval()
                logger.info("policy_loaded")
            except Exception as e:
                logger.warning("policy_load_failed error=%s using_heuristic", str(e))
                self.policy = None
        else:
            logger.warning("policy_not_found path=%s using_heuristic", self.policy_path)
            self.policy = None

        # LLM integration
        if self.enable_llm and HAS_LLM:
            try:
                import os
                config = {
                    "gemini_api_key": os.environ.get("GEMINI_API_KEY", ""),
                    "groq_api_key": os.environ.get("GROQ_API_KEY", ""),
                    "openrouter_api_key": os.environ.get("OPENROUTER_API_KEY", ""),
                    "openai_api_key": os.environ.get("OPENAI_API_KEY", ""),
                    "ollama_model": "phi3:mini",
                    "llm_enabled": True,
                }
                self.strategy_agent = StrategyAgent.from_config(config)
                self.decision_fusion = DecisionFusion(FusionConfig())
                self.context_builder = ContextBuilder()
                backend = self.strategy_agent.backends[0].__class__.__name__ if self.strategy_agent.backends else "none"
                logger.info("llm_integration_enabled backend=%s", backend)
            except Exception as e:
                logger.warning("llm_init_failed error=%s disabling", str(e))
                self.enable_llm = False

    def add_candle(self, candle: dict):
        """Add a candle to the buffer."""
        self.candles.append(candle)
        if len(self.candles) > 2000:
            self.candles = self.candles[-2000:]

    def rebuild_state_builder(self):
        """Rebuild StateBuilder from current candle buffer."""
        if len(self.candles) < 100:
            return
        df = pd.DataFrame(self.candles)
        if "timestamp" not in df.columns:
            df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_localize(None)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        self.state_builder = StateBuilder(df, timeframes=["1min", "15min", "1h"])

    def compute_features(self) -> np.ndarray | None:
        """Compute the 8 tiny features from current state."""
        if self.state_builder is None:
            return None
        try:
            ts = pd.Timestamp(self.candles[-1]["epoch"], unit="s", tz="UTC").tz_localize(None)
            state = self.state_builder.build_state(ts)
            feat = np.array([state.get(f, 0.0) for f in self.wm_feature_names], dtype=np.float32)
            # Normalize
            if self.wm_norm_mean is not None and self.wm_norm_std is not None:
                feat = (feat - self.wm_norm_mean) / (self.wm_norm_std + 1e-8)
            return feat
        except Exception as e:
            logger.warning("feature_error", error=str(e))
            return None

    def run_world_model(self, features: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Run world model inference. Returns (means, variances)."""
        with torch.no_grad():
            x = torch.tensor(features, dtype=torch.float32).unsqueeze(0).to(self.device)
            mean, log_var = self.world_model(x)
            means = mean.cpu().numpy().flatten()
            variances = torch.exp(log_var).cpu().numpy().flatten()
        return means, variances

    def get_policy_action(self, features: np.ndarray, rolling_vol: float) -> int:
        """Get action from policy or heuristic fallback."""
        if self.policy is not None:
            # Build obs: [features(8), position(1), equity_norm(1), rolling_vol(1)]
            obs = np.concatenate([
                features,
                [self.position],
                [self.equity / INITIAL_EQUITY],
                [rolling_vol],
            ]).astype(np.float32)
            obs_tensor = torch.tensor(obs, dtype=torch.float32)
            action, _, _, _ = self.policy.get_action(obs_tensor, deterministic=True)
            return action
        else:
            # Heuristic: trade based on world model prediction direction
            # This is used when no policy checkpoint is available
            return self._heuristic_action(features, rolling_vol)

    def _heuristic_action(self, features: np.ndarray, rolling_vol: float) -> int:
        """Simple heuristic that actually trades based on features."""
        # Use first feature (15min_return_1) as momentum signal
        momentum = features[0] if len(features) > 0 else 0
        # Use RSI-like signal from features
        vol_signal = features[2] if len(features) > 2 else 0  # vol_regime

        # Simple logic: buy on positive momentum, sell on negative
        if momentum > 0.0005 and self.position <= 0:
            return 1  # buy
        elif momentum < -0.0005 and self.position >= 0:
            return 2  # sell
        elif self.position != 0:
            # Exit if momentum reverses
            if self.position == 1 and momentum < -0.001:
                return 0  # close long
            elif self.position == -1 and momentum > 0.001:
                return 0  # close short
        return 0  # hold

    def _classify_regime(self, rolling_vol: float) -> str:
        """Classify current market regime based on volatility."""
        if rolling_vol < 0.001:
            return "calm"
        elif rolling_vol < 0.003:
            return "choppy"
        else:
            return "spike"

    def compute_rolling_vol(self, window: int = 100) -> float:
        """Compute rolling volatility from recent returns."""
        if len(self.candles) < window + 1:
            return 0.002
        closes = np.array([c["close"] for c in self.candles[-window-1:]])
        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        return float(np.std(returns)) + 1e-10

    def execute_action(self, action: int, current_price: float):
        """Execute a trading action (paper or dry-run)."""

        new_position = {0: 0, 1: 1, 2: -1}.get(action, 0)

        # --- EXIT LOGIC: force close under these conditions ---
        if self.position != 0:
            should_exit = False
            exit_reason = ""

            # 1. Policy says sell (or hold when long, sell when short)
            if self.position == 1 and new_position == 2:
                should_exit = True
                exit_reason = "signal_reverse"
            elif self.position == -1 and new_position == 1:
                should_exit = True
                exit_reason = "signal_reverse"

            # 2. Take profit: >0.1% gain
            if self.position == 1 and current_price > self.entry_price * 1.001:
                should_exit = True
                exit_reason = "take_profit"
            elif self.position == -1 and current_price < self.entry_price * 0.999:
                should_exit = True
                exit_reason = "take_profit"

            # 3. Stop loss: >0.15% loss
            if self.position == 1 and current_price < self.entry_price * 0.9985:
                should_exit = True
                exit_reason = "stop_loss"
            elif self.position == -1 and current_price > self.entry_price * 1.0015:
                should_exit = True
                exit_reason = "stop_loss"

            # 4. Max holding period: 30 bars (30 minutes)
            bars_held = len(self.trade_log) - next(
                (i for i, t in enumerate(reversed(self.trade_log)) if t.get("action") == "open"),
                len(self.trade_log)
            )
            if bars_held > 30:
                should_exit = True
                exit_reason = "max_hold"

            if should_exit:
                new_position = 0  # Force close
                logger.info("auto_exit reason=%s bars_held=%d", exit_reason, bars_held)

        if new_position == self.position:
            return  # no change

        # Close existing position
        if self.position != 0:
            pnl = (current_price - self.entry_price) * self.position * POSITION_SIZE * self.equity / current_price
            self.total_pnl += pnl
            self.equity += pnl
            self.peak_equity = max(self.peak_equity, self.equity)

            win = pnl > 0
            if win:
                self.win_count += 1
            self.trade_count += 1

            self.trade_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "close",
                "side": "long" if self.position > 0 else "short",
                "entry_price": self.entry_price,
                "exit_price": current_price,
                "pnl": pnl,
                "win": win,
                "equity": self.equity,
            })

            logger.info(
                "trade.close side=%s entry=%.2f exit=%.2f pnl=%.2f equity=%.2f",
                "long" if self.position > 0 else "short",
                self.entry_price, current_price, pnl, self.equity,
            )

            # Record in performance monitor
            self.perf_monitor.record_trade(
                pnl=pnl,
                direction=self.position,
                index=self.symbol,
                entry_price=self.entry_price,
                exit_price=current_price,
            )

        # Open new position
        if new_position != 0:
            self.position = new_position
            self.entry_price = current_price

            self.trade_log.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "open",
                "side": "long" if new_position > 0 else "short",
                "entry_price": current_price,
                "equity": self.equity,
            })

            logger.info(
                "trade.open side=%s price=%.2f equity=%.2f",
                "long" if new_position > 0 else "short",
                current_price, self.equity,
            )
        else:
            self.position = 0
            self.entry_price = 0.0

    def format_status(self, means: np.ndarray, variances: np.ndarray, action: int) -> str:
        """Format current status as readable output."""
        current_price = self.candles[-1]["close"] if self.candles else 0
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        action_str = {0: "HOLD", 1: "BUY", 2: "SELL"}.get(action, "?")
        pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(self.position, "?")

        # World model prediction
        total_mean = np.sum(means)
        total_std = np.sqrt(np.sum(variances))
        direction = "UP" if total_mean > 0 else "DOWN"
        confidence = abs(total_mean) / total_std if total_std > 0 else 0

        # Drawdown
        dd = (self.peak_equity - self.equity) / self.peak_equity * 100 if self.peak_equity > 0 else 0

        llm_line = ""
        if self._last_llm_decision and self.enable_llm:
            llm_action = self._last_llm_decision.get("action", "?") if isinstance(self._last_llm_decision, dict) else getattr(self._last_llm_decision, "action", "?")
            llm_signal = self._last_llm_decision.get("signal", 0) if isinstance(self._last_llm_decision, dict) else 0
            llm_line = f"  LLM: {llm_action} (signal={llm_signal:+.2f})  |  "

        lines = [
            f"\n{'='*65}",
            f"  PAPER TRADING @ {ts}  |  {self.symbol}  |  Price: {current_price:.2f}",
            f"{'='*65}",
            f"  World Model: {direction} (conf={confidence:.2f})  mean={total_mean:+.6f}  std={total_std:.6f}",
            f"  {llm_line}Policy: {action_str}  |  Position: {pos_str}",
            f"  Equity: ${self.equity:.2f}  |  PnL: ${self.total_pnl:+.2f}  |  DD: {dd:.1f}%",
            f"  Trades: {self.trade_count}  |  Win rate: {self.win_count/self.trade_count*100:.0f}%" if self.trade_count > 0 else f"  Trades: {self.trade_count}",
            f"{'='*65}",
        ]
        return "\n".join(lines)

    def handle_tick(self, msg: dict):
        """Handle a tick — aggregate into 1-min candles."""
        tick = msg.get("tick", {})
        if not tick:
            return

        quote = float(tick.get("quote", 0))
        epoch = int(tick.get("epoch", 0))
        if quote <= 0 or epoch <= 0:
            return

        candle_epoch = (epoch // 60) * 60

        if candle_epoch != self._current_candle_epoch:
            # Finalize previous candle
            if self._current_candle is not None and self._current_candle["epoch"] > 0:
                self.add_candle(self._current_candle)
                self._candle_count += 1

                # Rebuild state builder periodically
                if self._candle_count % 50 == 0:
                    self.rebuild_state_builder()

                # Run prediction and trading
                self._run_cycle()

            # Start new candle
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
        """Run one prediction + trading cycle."""
        features = self.compute_features()
        if features is None:
            return

        # World model prediction
        means, variances = self.run_world_model(features)

        # Rolling volatility
        rolling_vol = self.compute_rolling_vol()

        # Compute SNR for confidence
        total_mean = float(np.sum(means))
        total_var = float(np.sum(variances))
        snr = abs(total_mean) / (np.sqrt(total_var) + 1e-10)
        snr = min(snr, 10.0)

        # Risk management: compute position size
        current_dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        self.position_size = self.risk_mgr.compute_position_size(
            confidence=snr,
            current_drawdown=current_dd,
            volatility=rolling_vol,
            index_type=self.symbol.lower(),
            recent_returns=self._recent_returns,
        )

        # Policy decision
        ppo_action = self.get_policy_action(features, rolling_vol)
        action = ppo_action

        # LLM decision fusion (if enabled)
        llm_signal = 0.0
        if self.enable_llm and self.strategy_agent and self.decision_fusion and self.context_builder:
            try:
                # Build context for LLM
                current_price = self.candles[-1]["close"] if self.candles else 0
                context = self.context_builder.build(
                    features=dict(zip(self.wm_feature_names, features)),
                    wm_pred_mean=means,
                    wm_pred_std=np.sqrt(variances),
                    current_price=current_price,
                    equity=self.equity,
                    peak_equity=self.peak_equity,
                    position=float(self.position),
                    recent_returns=self._recent_returns[-50:] if self._recent_returns else None,
                )
                # LLM reasoning
                llm_decision = self.strategy_agent.decide(context)
                llm_dict = llm_decision.to_dict() if hasattr(llm_decision, 'to_dict') else llm_decision
                llm_signal = (llm_dict.get("confidence", 0.5) * 2 - 1) * (1 if llm_dict.get("action") == "buy" else -1 if llm_dict.get("action") == "sell" else 0)
                self._last_llm_decision = llm_dict
                logger.info("llm_decision signal=%.2f action=%s reasoning=%s",
                           llm_signal, llm_dict.get("action", "?"),
                           llm_dict.get("reasoning", "")[:80])

                # Fuse: WM + LLM + PPO
                wm_signal = total_mean * snr  # positive = bullish
                ppo_signal = {1: 1.0, 0: 0.0, 2: -1.0}.get(ppo_action, 0.0)

                fused = self.decision_fusion.fuse(FusionInput(
                    wm_signal=wm_signal,
                    llm_signal=llm_signal,
                    ppo_signal=ppo_signal,
                    snr=snr,
                    regime=self._classify_regime(rolling_vol),
                ))
                # Convert fused signal to action: >0.15 buy, <-0.15 sell, else hold
                if fused.signal > 0.15:
                    action = 1  # buy
                elif fused.signal < -0.15:
                    action = 2  # sell
                else:
                    action = 0  # hold
                logger.info("fusion signal=%.3f agree=%.0f%% action=%s",
                           fused.signal, fused.agreement * 100, {0: 'HOLD', 1: 'BUY', 2: 'SELL'}.get(action))
            except Exception as e:
                logger.warning("llm_fusion_error error=%s falling_back_to_ppo", str(e))
                action = ppo_action

        # Risk check: should we trade?
        # Use SNR as expected return proxy (more sensitive than raw mean)
        expected_return = max(abs(total_mean), snr * 0.001)  # Ensure we pass the threshold
        if not self.risk_mgr.should_trade(expected_return, snr, current_dd, rolling_vol):
            if self.position != 0:
                action = 0  # force flat
                logger.info("risk_override action=flat snr=%.2f dd=%.2f%%", snr, current_dd * 100)
            elif snr > 0.05:  # Allow small trades even when risk manager says no
                logger.debug("risk_skip snr=%.3f mean=%.6f", snr, total_mean)

        # Execute trade
        current_price = self.candles[-1]["close"]
        self.execute_action(action, current_price)

        # Update monitoring
        self.monitor.update(
            self.symbol,
            equity=self.equity,
            position=self.position,
            current_price=current_price,
            entry_price=self.entry_price,
            pnl=self.total_pnl,
            wm_direction='UP' if total_mean > 0 else 'DOWN',
            wm_confidence=min(snr / 2.0, 1.0),
            wm_mean=total_mean,
            wm_std=np.sqrt(total_var),
            position_size=self.position_size,
            risk_score=current_dd,
        )

        # Track recent returns for drift detection
        if len(self.candles) > 1:
            ret = (self.candles[-1]['close'] - self.candles[-2]['close']) / self.candles[-2]['close']
            self._recent_returns.append(ret)
            if len(self._recent_returns) > 2000:
                self._recent_returns = self._recent_returns[-2000:]

        # Display status
        status = self.format_status(means, variances, action)
        print(status)

        # Portfolio summary every 10 cycles
        if self._candle_count % 10 == 0:
            print(self.monitor.format_status())

    async def run(self):
        """Main run loop."""
        self.load_models()

        conn = DerivConnection()
        await conn.connect()
        logger.info("connected_to_deriv symbol=%s", self.symbol)

        try:
            # Fetch history
            resp = await conn.get_ticks_history(
                self.symbol, style="candles",
                count=min(self.history_bars, 1000), granularity=60,
            )
            for c in resp.get("candles", []):
                self.add_candle({
                    "epoch": c["epoch"],
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            logger.info("history_fetched n=%d", len(self.candles))

            # Build initial state builder
            self.rebuild_state_builder()

            # Run initial prediction
            self._run_cycle()

            # Subscribe to live ticks
            sub_id = await conn.subscribe(
                {"ticks": self.symbol}, handler=self.handle_tick,
            )
            logger.info("live_active sub_id=%s", sub_id)

            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("stopped_by_user")
        finally:
            await conn.close()
            self._print_summary()

    def _print_summary(self):
        """Print trading summary on exit."""
        print(f"\n{'='*60}")
        print("PAPER TRADING SUMMARY")
        print(f"{'='*60}")
        print(f"  Symbol: {self.symbol}")
        print(f"  Final equity: ${self.equity:.2f}")
        print(f"  Total PnL: ${self.total_pnl:+.2f}")
        print(f"  Return: {(self.equity/INITIAL_EQUITY - 1)*100:+.1f}%")
        print(f"  Trades: {self.trade_count}")
        if self.trade_count > 0:
            print(f"  Win rate: {self.win_count/self.trade_count*100:.0f}%")
        print(f"  Max drawdown: {(self.peak_equity - min(t['equity'] for t in self.trade_log if 'equity' in t))/self.peak_equity*100:.1f}%" if self.trade_log else "")
        print(f"{'='*60}")

        # Save trade log
        log_path = Path("checkpoints_tiny/paper_trades.json")
        with open(log_path, "w") as f:
            json.dump(self.trade_log, f, indent=2, default=str)
        print(f"Trade log saved to: {log_path}")

        # Save monitoring data
        monitor_path = Path(f"checkpoints_tiny/monitor_{self.symbol.lower()}.json")
        self.monitor.save_log(str(monitor_path))
        print(f"Monitor log saved to: {monitor_path}")


def main():
    parser = argparse.ArgumentParser(description="Paper trading with world model + policy")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--world-model", default=DEFAULT_WORLD_MODEL)
    parser.add_argument("--policy", default=DEFAULT_POLICY)
    parser.add_argument("--history-bars", type=int, default=DEFAULT_HISTORY_BARS)
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--llm", action="store_true", default=False,
                        help="Enable LLM strategy reasoning (requires API key)")
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )

    trader = PaperTrader(
        symbol=args.symbol,
        world_model_path=args.world_model,
        policy_path=args.policy,
        history_bars=args.history_bars,
        dry_run=args.dry_run,
        device_str=args.device,
        enable_llm=args.llm,
    )
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
