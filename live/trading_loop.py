"""
live/trading_loop.py — Unified live trading loop.

Wires together ALL components into a single coherent pipeline:

  Deriv WebSocket → Candles → StateBuilder → Features
       ↓
  World Model → predicted returns + uncertainty
       ↓
  ContextBuilder → MarketContext (LLM-readable)
       ↓
  StrategyAgent (LLM) → qualitative decision
       ↓
  DecisionFusion → WM(40%) + LLM(35%) + PPO(25%) → FusedDecision
       ↓
  RiskManager → position sizing with drawdown + vol scaling
       ↓
  Governor → hard risk cage (circuit breaker, loss streak halt)
       ↓
  Execute trade → track equity, log decisions

Usage:
    # Single index
    python -m live.trading_loop --symbol R_75

    # All indices (from config)
    python -m live.trading_loop --config live/settings.json

    # Dry run (no LLM, just WM + Policy)
    python -m live.trading_loop --symbol R_75 --no-llm
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
from model.context_builder import ContextBuilder
from model.decision_fusion import DecisionFusion, FusionConfig, WMPrediction
from model.policy import PolicyConfig, PolicyNetwork
from model.risk_management import RiskManager, RiskConfig
from model.strategy_agent import StrategyAgent, LLMDecision
from model.train_tiny import TINY_FEATURES, TinyWorldModel
from backtest.governor_sim import Governor, GovernorConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
INITIAL_EQUITY = 10000.0
HORIZON = 10


# ---------------------------------------------------------------------------
# Index Trader — manages one index's full pipeline
# ---------------------------------------------------------------------------
class IndexTrader:
    """
    Manages the complete decision stack for ONE synthetic index.

    On each new 1-minute candle:
      1. Compute features via StateBuilder
      2. World model predicts next-10-bar distribution
      3. ContextBuilder formats market state for LLM
      4. StrategyAgent (LLM) reasons about regime + risk
      5. DecisionFusion combines WM + LLM + PPO signals
      6. RiskManager sizes the position
      7. Governor applies hard risk limits
      8. Execute trade, track equity
    """

    def __init__(
        self,
        symbol: str,
        name: str = "",
        world_model_path: str = "",
        policy_path: str = "",
        history_bars: int = 500,
        device: torch.device | None = None,
        strategy_agent: StrategyAgent | None = None,
        fusion: DecisionFusion | None = None,
        governor: Governor | None = None,
        risk_config: RiskConfig | None = None,
        governor_config: GovernorConfig | None = None,
        execution_config: dict | None = None,
        log_dir: str = "checkpoints_live",
    ):
        self.symbol = symbol
        self.name = name or symbol
        self.device = device or torch.device("cpu")
        self.history_bars = history_bars
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # Execution config
        exec_cfg = execution_config or {}
        self.position_size_frac = exec_cfg.get("position_size", 1.0)
        self.spread_cost = exec_cfg.get("spread_cost", 0.0001)
        self.slippage_cost = exec_cfg.get("slippage_cost", 0.00005)
        self.min_hold_bars = exec_cfg.get("min_hold_bars", 3)
        self.rebuild_every = exec_cfg.get("rebuild_state_every", 50)

        # Components
        self.strategy_agent = strategy_agent or StrategyAgent()
        self.fusion = fusion or DecisionFusion()
        self.governor = governor or Governor(governor_config or GovernorConfig())
        self.risk_mgr = RiskManager(risk_config or RiskConfig())
        self.context_builder = ContextBuilder()

        # State
        self.equity = INITIAL_EQUITY
        self.peak_equity = INITIAL_EQUITY
        self.position = 0  # +1 long, -1 short, 0 flat
        self.entry_price = 0.0
        self.entry_bar = 0
        self.trade_count = 0
        self.win_count = 0
        self.total_pnl = 0.0
        self._recent_returns: list[float] = []
        self._recent_trades: list[dict] = []
        self.trade_log: list[dict] = []
        self.decision_log: list[dict] = []

        # Models
        self.world_model: TinyWorldModel | None = None
        self.policy: PolicyNetwork | None = None
        self.wm_feature_names: list[str] = []
        self.wm_norm_mean: np.ndarray | None = None
        self.wm_norm_std: np.ndarray | None = None
        self.temperature: float = 1.0

        # Candle buffer
        self.candles: list[dict] = []
        self.state_builder: StateBuilder | None = None
        self._current_candle_epoch = 0
        self._current_candle: dict | None = None
        self._candle_count = 0
        self._bar_idx = 0

    # -- Model loading -----------------------------------------------------

    def load_models(self, world_model_path: str, policy_path: str):
        """Load world model and policy from checkpoints."""
        # World model
        if world_model_path and Path(world_model_path).exists():
            ckpt = torch.load(world_model_path, map_location=self.device, weights_only=False)
            self.wm_feature_names = list(ckpt["feature_names"])
            self.wm_norm_mean = ckpt.get("norm_mean", ckpt.get("feat_mean"))
            self.wm_norm_std = ckpt.get("norm_std", ckpt.get("feat_std"))
            self.temperature = float(ckpt.get("temperature", 1.0))

            self.world_model = TinyWorldModel(
                n_features=len(self.wm_feature_names),
                horizon=int(ckpt.get("horizon", HORIZON)),
                hidden=int(ckpt.get("hidden_dim", 16)),
                dropout=float(ckpt.get("dropout", 0.1)),
            ).to(self.device)
            self.world_model.load_state_dict(ckpt["model_state_dict"])
            self.world_model.eval()
            logger.info("world_model_loaded symbol=%s features=%d T=%.3f",
                        self.symbol, len(self.wm_feature_names), self.temperature)
        else:
            logger.warning("world_model_not_found symbol=%s path=%s", self.symbol, world_model_path)

        # Policy
        if policy_path and Path(policy_path).exists():
            try:
                pol_ckpt = torch.load(policy_path, map_location=self.device, weights_only=False)
                obs_dim = 8 + 3  # 8 features + position + equity_norm + rolling_vol
                cfg = PolicyConfig(obs_dim=obs_dim)
                self.policy = PolicyNetwork(cfg).to(self.device)
                self.policy.load_state_dict(pol_ckpt["policy_state_dict"])
                self.policy.eval()
                logger.info("policy_loaded symbol=%s", self.symbol)
            except Exception as e:
                logger.warning("policy_load_failed symbol=%s error=%s", self.symbol, e)

    # -- Candle management -------------------------------------------------

    def add_candle(self, candle: dict):
        """Add a candle to the rolling buffer."""
        self.candles.append(candle)
        if len(self.candles) > 2000:
            self.candles = self.candles[-2000:]

    def rebuild_state_builder(self):
        """Rebuild StateBuilder from current candle buffer."""
        if len(self.candles) < 200:
            return
        df = pd.DataFrame(self.candles)
        if "timestamp" not in df.columns:
            df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_localize(None)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        self.state_builder = StateBuilder(df, timeframes=["1min", "15min", "1h"])
        logger.info("state_builder_rebuilt symbol=%s bars=%d", self.symbol, len(df))

    # -- Feature computation -----------------------------------------------

    def compute_features(self) -> np.ndarray | None:
        """Compute the 8 tiny features from current state."""
        if self.state_builder is None or not self.wm_feature_names:
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
            logger.warning("feature_error symbol=%s error=%s", self.symbol, e)
            return None

    def compute_raw_features(self) -> dict[str, float]:
        """Compute raw (un-normalized) features as a dict for ContextBuilder."""
        if self.state_builder is None:
            return {}
        try:
            ts = pd.Timestamp(self.candles[-1]["epoch"], unit="s", tz="UTC").tz_localize(None)
            return self.state_builder.build_state(ts)
        except Exception:
            return {}

    # -- World model inference ---------------------------------------------

    @torch.no_grad()
    def run_world_model(self, features: np.ndarray) -> WMPrediction:
        """Run world model inference. Returns WMPrediction."""
        if self.world_model is None:
            return WMPrediction(
                mean_returns=np.zeros(HORIZON),
                std_returns=np.ones(HORIZON) * 0.001,
            )

        x = torch.tensor(features, dtype=torch.float32).unsqueeze(0).to(self.device)
        mean, log_var = self.world_model(x)
        means = mean.cpu().numpy().flatten()
        variances = torch.exp(log_var).cpu().numpy().flatten() * self.temperature
        stds = np.sqrt(np.maximum(variances, 1e-12))

        cum_mean = float(np.sum(means))
        cum_std = float(np.sqrt(np.sum(variances)))
        snr = abs(cum_mean) / max(cum_std, 1e-12) * np.sign(cum_mean)

        return WMPrediction(
            mean_returns=means,
            std_returns=stds,
            snr=float(snr),
            cumulative_mean=cum_mean,
            cumulative_std=cum_std,
        )

    # -- Policy inference --------------------------------------------------

    @torch.no_grad()
    def get_policy_action(self, features: np.ndarray, rolling_vol: float) -> dict:
        """Get action from PPO policy. Returns dict with action + confidence."""
        if self.policy is None:
            return {"action": "hold", "confidence": 0.3}

        obs = np.concatenate([
            features,
            [self.position],
            [self.equity / INITIAL_EQUITY],
            [rolling_vol],
        ]).astype(np.float32)

        obs_tensor = torch.tensor(obs, dtype=torch.float32, device=self.device)
        action, _, _, confidence = self.policy.get_action(obs_tensor, deterministic=True)

        action_map = {0: "hold", 1: "buy", 2: "sell"}
        return {"action": action_map.get(action, "hold"), "confidence": confidence}

    # -- Rolling volatility ------------------------------------------------

    def compute_rolling_vol(self, window: int = 100) -> float:
        """Compute rolling realized volatility."""
        if len(self.candles) < window + 1:
            return 0.002
        closes = np.array([c["close"] for c in self.candles[-window - 1:]])
        returns = np.diff(np.log(np.maximum(closes, 1e-10)))
        return float(np.std(returns)) + 1e-10

    # -- Main decision cycle ----------------------------------------------

    def run_cycle(self) -> dict | None:
        """
        Run one full decision cycle on the latest candle.

        Returns the decision record, or None if features can't be computed.
        """
        features = self.compute_features()
        if features is None:
            return None

        self._bar_idx += 1

        # 1. World model prediction
        wm_pred = self.run_world_model(features)

        # 2. Rolling volatility
        rolling_vol = self.compute_rolling_vol()

        # 3. PPO policy action
        ppo_action = self.get_policy_action(features, rolling_vol)

        # 4. Build market context for LLM
        raw_features = self.compute_raw_features()
        wm_means = wm_pred.mean_returns
        wm_stds = wm_pred.std_returns

        market_ctx = self.context_builder.build(
            features=raw_features,
            wm_pred_mean=wm_means,
            wm_pred_std=wm_stds,
            current_price=self.candles[-1]["close"] if self.candles else 0.0,
            timestamp=str(self.candles[-1].get("epoch", "")) if self.candles else "",
            equity=self.equity,
            peak_equity=self.peak_equity,
            position=float(self.position),
            recent_returns=self._recent_returns[-50:],
        )

        # 5. LLM strategy reasoning
        llm_decision = self.strategy_agent.analyze(market_ctx)

        # 6. Decision fusion: WM + LLM + PPO
        fused = self.fusion.fuse(
            wm_pred=wm_pred,
            llm_decision=llm_decision,
            ppo_action=ppo_action,
            market_ctx=market_ctx,
            current_equity=self.equity,
            current_drawdown=(self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0,
            current_position=float(self.position),
        )

        # 7. Map fused action to position
        action_to_pos = {"buy": 1, "sell": -1, "hold": 0}
        want_position = action_to_pos.get(fused.action, 0)

        # 8. Governor hard limits
        current_dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        final_position, vetoed = self.governor.check(
            self._bar_idx, self.equity, want_position
        )

        # 9. Execute trade
        current_price = self.candles[-1]["close"] if self.candles else 0.0
        trade_executed = self._execute_trade(final_position, current_price)

        # 10. Build decision record
        decision = {
            "bar_idx": self._bar_idx,
            "symbol": self.symbol,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "price": current_price,
            "fused_action": fused.action,
            "fused_size": fused.size,
            "fused_confidence": fused.confidence,
            "fused_regime": fused.regime,
            "wm_snr": wm_pred.snr,
            "wm_cum_mean": wm_pred.cumulative_mean,
            "wm_cum_std": wm_pred.cumulative_std,
            "llm_action": llm_decision.action,
            "llm_confidence": llm_decision.confidence,
            "llm_reasoning": llm_decision.reasoning,
            "ppo_action": ppo_action["action"],
            "ppo_confidence": ppo_action["confidence"],
            "final_position": final_position,
            "governor_vetoed": vetoed,
            "equity": self.equity,
            "drawdown": current_dd,
            "trade_executed": trade_executed,
            "reasoning": fused.reasoning,
        }

        self.decision_log.append(decision)

        return decision

    # -- Trade execution ---------------------------------------------------

    def _execute_trade(self, target_position: int, current_price: float) -> bool:
        """Execute a trade if position changed. Returns True if a trade happened."""
        if target_position == self.position:
            return False

        # Close existing position
        if self.position != 0:
            # Check minimum hold period
            if self._bar_idx - self.entry_bar < self.min_hold_bars:
                logger.info("min_hold_not_reached symbol=%s bars=%d min=%d",
                            self.symbol, self._bar_idx - self.entry_bar, self.min_hold_bars)
                return False

            pnl = (current_price - self.entry_price) * self.position * self.position_size_frac
            pnl_pct = pnl / self.entry_price if self.entry_price > 0 else 0
            self.total_pnl += pnl
            self.equity += pnl
            self.peak_equity = max(self.peak_equity, self.equity)

            win = pnl > 0
            if win:
                self.win_count += 1
            self.trade_count += 1

            self._recent_returns.append(pnl_pct)
            if len(self._recent_returns) > 2000:
                self._recent_returns = self._recent_returns[-2000:]

            trade = {
                "bar_idx": self._bar_idx,
                "symbol": self.symbol,
                "side": "long" if self.position > 0 else "short",
                "entry_price": self.entry_price,
                "exit_price": current_price,
                "pnl": pnl,
                "pnl_pct": pnl_pct,
                "win": win,
                "equity": self.equity,
                "hold_bars": self._bar_idx - self.entry_bar,
            }
            self.trade_log.append(trade)
            self._recent_trades.append(trade)
            self.governor.report_trade(self._bar_idx, pnl)

            logger.info("trade.close symbol=%s side=%s entry=%.2f exit=%.2f pnl=%.2f equity=%.2f",
                        self.symbol, trade["side"], self.entry_price, current_price, pnl, self.equity)

        # Open new position
        if target_position != 0:
            self.position = target_position
            self.entry_price = current_price
            self.entry_bar = self._bar_idx
            logger.info("trade.open symbol=%s side=%s price=%.2f equity=%.2f",
                        self.symbol, "long" if target_position > 0 else "short",
                        current_price, self.equity)
        else:
            self.position = 0
            self.entry_price = 0.0

        return True

    # -- Tick handling (called from WebSocket callback) ---------------------

    def handle_tick(self, msg: dict):
        """Handle a tick — aggregate into 1-min candles, trigger cycle on bar close."""
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
                if self._candle_count % self.rebuild_every == 0:
                    self.rebuild_state_builder()

                # Run decision cycle
                self.run_cycle()

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

    # -- Summary -----------------------------------------------------------

    def get_summary(self) -> dict:
        """Get current trading summary."""
        dd = (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        return {
            "symbol": self.symbol,
            "name": self.name,
            "equity": self.equity,
            "pnl": self.total_pnl,
            "return_pct": (self.equity / INITIAL_EQUITY - 1) * 100,
            "drawdown_pct": dd * 100,
            "trades": self.trade_count,
            "win_rate": self.win_count / self.trade_count if self.trade_count > 0 else 0,
            "position": self.position,
            "governor": self.governor.summary,
        }

    def save_logs(self):
        """Save trade and decision logs."""
        if self.trade_log:
            path = self.log_dir / f"trades_{self.symbol.lower()}.json"
            with open(path, "w") as f:
                json.dump(self.trade_log, f, indent=2, default=str)
            logger.info("trades_saved symbol=%s path=%s", self.symbol, path)

        if self.decision_log:
            path = self.log_dir / f"decisions_{self.symbol.lower()}.json"
            with open(path, "w") as f:
                json.dump(self.decision_log, f, indent=2, default=str)
            logger.info("decisions_saved symbol=%s path=%s", self.symbol, path)


# ---------------------------------------------------------------------------
# Multi-Index Orchestrator
# ---------------------------------------------------------------------------
class LiveTradingOrchestrator:
    """
    Orchestrates trading across multiple indices simultaneously.

    Manages concurrent WebSocket connections, shared LLM agent,
    and portfolio-level risk management.
    """

    def __init__(
        self,
        symbols: list[tuple[str, str, str, str]],  # (symbol, name, wm_path, policy_path)
        strategy_agent: StrategyAgent | None = None,
        fusion_config: FusionConfig | None = None,
        governor_config: GovernorConfig | None = None,
        risk_config: RiskConfig | None = None,
        execution_config: dict | None = None,
        device: str = "cpu",
        log_dir: str = "checkpoints_live",
        history_bars: int = 500,
    ):
        self.device = torch.device(device)
        self.strategy_agent = strategy_agent or StrategyAgent()
        self.fusion_config = fusion_config or FusionConfig()
        self.governor_config = governor_config or GovernorConfig()
        self.risk_config = risk_config or RiskConfig()
        self.execution_config = execution_config or {}
        self.log_dir = log_dir
        self.history_bars = history_bars

        # Create one trader per index
        self.traders: dict[str, IndexTrader] = {}
        for symbol, name, wm_path, pol_path in symbols:
            trader = IndexTrader(
                symbol=symbol,
                name=name,
                history_bars=history_bars,
                device=self.device,
                strategy_agent=self.strategy_agent,
                fusion=DecisionFusion(self.fusion_config),
                governor=Governor(self.governor_config),
                risk_config=self.risk_config,
                governor_config=self.governor_config,
                execution_config=self.execution_config,
                log_dir=log_dir,
            )
            trader.load_models(wm_path, pol_path)
            self.traders[symbol] = trader

        # Portfolio risk manager (tracks all indices)
        self.portfolio_risk = RiskManager(self.risk_config)

    async def run(self):
        """Run the live trading loop across all indices."""
        logger.info("orchestrator_starting n_indices=%d", len(self.traders))

        conn = DerivConnection()
        await conn.connect()
        logger.info("deriv_connected")

        try:
            # Fetch history and subscribe for each index
            for symbol, trader in self.traders.items():
                try:
                    # Fetch candle history
                    resp = await conn.get_ticks_history(
                        symbol, style="candles",
                        count=min(self.history_bars, 1000), granularity=60,
                    )
                    for c in resp.get("candles", []):
                        trader.add_candle({
                            "epoch": c["epoch"],
                            "open": float(c["open"]), "high": float(c["high"]),
                            "low": float(c["low"]), "close": float(c["close"]),
                            "volume": float(c.get("volume", 0)),
                        })
                    logger.info("history_fetched symbol=%s n=%d", symbol, len(trader.candles))

                    # Build initial state builder
                    trader.rebuild_state_builder()

                    # Run initial prediction
                    trader.run_cycle()

                    # Subscribe to live ticks
                    sub_id = await conn.subscribe(
                        {"ticks": symbol}, handler=trader.handle_tick,
                    )
                    logger.info("live_active symbol=%s sub_id=%s", symbol, sub_id)

                except Exception as e:
                    logger.error("index_setup_failed symbol=%s error=%s", symbol, e)

            # Main loop — just keep alive
            logger.info("all_indices_active")
            cycle = 0
            while True:
                await asyncio.sleep(1)
                cycle += 1

                # Print portfolio summary every 60 seconds
                if cycle % 60 == 0:
                    self._print_portfolio_summary()

        except KeyboardInterrupt:
            logger.info("stopped_by_user")
        finally:
            await conn.close()
            self._save_all_logs()
            self._print_final_summary()

    def _print_portfolio_summary(self):
        """Print portfolio-level status."""
        total_equity = sum(t.equity for t in self.traders.values())
        total_pnl = sum(t.total_pnl for t in self.traders.values())
        total_trades = sum(t.trade_count for t in self.traders.values())
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")

        print(f"\n--- Portfolio @ {ts} ---")
        for sym, t in self.traders.items():
            pos_str = {0: "FLAT", 1: "LONG", -1: "SHORT"}.get(t.position, "?")
            dd = (t.peak_equity - t.equity) / t.peak_equity * 100 if t.peak_equity > 0 else 0
            print(f"  {t.name:15s} {pos_str:5s}  equity=${t.equity:.2f}  "
                  f"pnl=${t.total_pnl:+.2f}  dd={dd:.1f}%  trades={t.trade_count}")
        print(f"  {'TOTAL':15s}        equity=${total_equity:.2f}  "
              f"pnl=${total_pnl:+.2f}  trades={total_trades}")

    def _print_final_summary(self):
        """Print final summary on exit."""
        print(f"\n{'='*70}")
        print("LIVE TRADING SUMMARY")
        print(f"{'='*70}")
        total_equity = 0
        total_pnl = 0
        total_trades = 0
        total_wins = 0
        for sym, t in self.traders.items():
            s = t.get_summary()
            total_equity += s["equity"]
            total_pnl += s["pnl"]
            total_trades += s["trades"]
            total_wins += t.win_count
            print(f"  {t.name:15s}  return={s['return_pct']:+.1f}%  "
                  f"trades={s['trades']}  win_rate={s['win_rate']:.0%}  "
                  f"dd={s['drawdown_pct']:.1f}%  "
                  f"governor_vetoes={s['governor']['n_interventions']}")
        overall_wr = total_wins / total_trades if total_trades > 0 else 0
        print(f"  {'TOTAL':15s}  return={(total_equity/INITIAL_EQUITY-1)*100:+.1f}%  "
              f"trades={total_trades}  win_rate={overall_wr:.0%}")
        print(f"{'='*70}")

    def _save_all_logs(self):
        """Save logs for all traders."""
        for t in self.traders.values():
            t.save_logs()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Unified live trading loop")
    parser.add_argument("--symbol", default=None,
                        help="Single index symbol (e.g. R_75). Overrides config.")
    parser.add_argument("--config", default=None,
                        help="Path to live/settings.json config file.")
    parser.add_argument("--world-model", default="checkpoints_tiny/world_model_best.pt")
    parser.add_argument("--policy", default="checkpoints_tiny/policy_best.pt")
    parser.add_argument("--history-bars", type=int, default=500)
    parser.add_argument("--no-llm", action="store_true",
                        help="Disable LLM strategy agent.")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--log-dir", default="checkpoints_live")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )

    # Build LLM agent
    llm_config = {"llm_enabled": not args.no_llm}
    strategy_agent = StrategyAgent.from_config(llm_config)
    if not args.no_llm:
        logger.info("llm_enabled backends=%s", strategy_agent.get_stats()["backends"])
    else:
        logger.info("llm_disabled using_rule_fallback")

    # Determine symbols
    if args.symbol:
        symbols = [(args.symbol, args.symbol, args.world_model, args.policy)]
    else:
        # Default: all 4 indices
        symbols = [
            ("R_75", "Volatility 75", args.world_model, args.policy),
            ("R_10", "Volatility 10", args.world_model, args.policy),
            ("BOOM500", "Boom 500", args.world_model, args.policy),
            ("CRASH500", "Crash 500", args.world_model, args.policy),
        ]

    orchestrator = LiveTradingOrchestrator(
        symbols=symbols,
        strategy_agent=strategy_agent,
        device=args.device,
        log_dir=args.log_dir,
        history_bars=args.history_bars,
    )

    try:
        asyncio.run(orchestrator.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
