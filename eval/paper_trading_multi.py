"""
eval/paper_trading_multi.py — Multi-index paper trading with portfolio optimization.

Runs all 4 Deriv synthetic indices simultaneously:
  - R_75 (Volatility 75)
  - R_10 (Volatility 10)
  - BOOM500
  - CRASH500

Features:
  1. Concurrent websocket connections to all indices
  2. Portfolio-level risk management with Kelly criterion
  3. Cross-index correlation tracking
  4. Combined monitoring dashboard
  5. Correlation-aware position sizing
  6. Dynamic hedging between correlated indices
  7. Real-time WebSocket broadcasting

Usage:
    python -m eval.paper_trading_multi
    python -m eval.paper_trading_multi --indices R_75 R_10
    python -m eval.paper_trading_multi --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import structlog
import torch

from data.deriv_connection import DerivConnection
from eval.monitoring import TradingMonitor
from eval.paper_trading import PaperTrader
from model.portfolio_optimizer import PortfolioOptimizer, OptimizerConfig
from model.risk_management import RiskManager, RiskConfig

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Index Configuration
# ---------------------------------------------------------------------------
INDEX_CONFIGS = {
    'R_75': {
        'symbol': 'R_75',
        'name': 'Volatility 75',
        'world_model': 'checkpoints_tiny/world_model_best.pt',
        'policy': 'checkpoints_tiny/policy_best.pt',
    },
    'R_10': {
        'symbol': 'R_10',
        'name': 'Volatility 10',
        'world_model': 'checkpoints_vol10/world_model_best.pt',
        'policy': 'checkpoints_vol10/policy_best.pt',
    },
    'BOOM500': {
        'symbol': 'BOOM500',
        'name': 'Boom 500',
        'world_model': 'checkpoints_boom_500/world_model_best.pt',
        'policy': 'checkpoints_boom_500/policy_best.pt',
    },
    'CRASH500': {
        'symbol': 'CRASH500',
        'name': 'Crash 500',
        'world_model': 'checkpoints_crash_500/world_model_best.pt',
        'policy': 'checkpoints_crash_500/policy_best.pt',
    },
}

DEFAULT_INDICES = ['R_75', 'R_10', 'BOOM500', 'CRASH500']
INITIAL_EQUITY = 10000.0


# ---------------------------------------------------------------------------
# Correlation Tracker
# ---------------------------------------------------------------------------
class CorrelationTracker:
    """
    Track rolling correlation between indices.
    """
    
    def __init__(self, window: int = 1000):
        self.window = window
        self._prices: dict[str, list[float]] = {}
    
    def update(self, index: str, price: float):
        """Update with new price for an index."""
        if index not in self._prices:
            self._prices[index] = []
        self._prices[index].append(price)
        if len(self._prices[index]) > self.window + 1:
            self._prices[index] = self._prices[index][-self.window-1:]
    
    def get_correlation(self, idx1: str, idx2: str) -> float:
        """Get rolling correlation between two indices."""
        if idx1 not in self._prices or idx2 not in self._prices:
            return 0.0
        
        r1 = self._prices[idx1]
        r2 = self._prices[idx2]
        
        min_len = min(len(r1), len(r2))
        if min_len < 10:
            return 0.0
        
        ret1 = np.diff(r1[-min_len:])
        ret2 = np.diff(r2[-min_len:])
        
        if len(ret1) < 5:
            return 0.0
        
        corr = np.corrcoef(ret1, ret2)[0, 1]
        return float(corr) if not np.isnan(corr) else 0.0
    
    def get_correlation_matrix(self) -> dict[str, dict[str, float]]:
        """Get full correlation matrix."""
        indices = list(self._prices.keys())
        matrix = {}
        for i1 in indices:
            matrix[i1] = {}
            for i2 in indices:
                matrix[i1][i2] = self.get_correlation(i1, i2)
        return matrix
    
    def get_diversification_score(self) -> float:
        """Compute portfolio diversification score (0-1)."""
        indices = list(self._prices.keys())
        n = len(indices)
        if n < 2:
            return 0.0
        
        total_corr = 0.0
        count = 0
        for i in range(n):
            for j in range(i+1, n):
                corr = self.get_correlation(indices[i], indices[j])
                total_corr += abs(corr)
                count += 1
        
        avg_corr = total_corr / count if count > 0 else 0.0
        return max(0.0, min(1.0, 1.0 - avg_corr))


# ---------------------------------------------------------------------------
# Portfolio Manager
# ---------------------------------------------------------------------------
class PortfolioManager:
    """
    Manages portfolio-level risk across multiple indices.
    """
    
    def __init__(self, initial_equity: float = INITIAL_EQUITY):
        self.initial_equity = initial_equity
        self.total_equity = initial_equity
        self.peak_equity = initial_equity
        self.total_pnl = 0.0
        
        # Per-index state
        self._index_equity: dict[str, float] = {}
        self._index_position: dict[str, int] = {}
        self._index_peak: dict[str, float] = {}
        
        # Risk management
        self.risk_mgr = RiskManager(RiskConfig(
            max_portfolio_exposure=3.0,
            max_single_index_exposure=1.5,
        ))
        
        # Portfolio optimizer
        self.optimizer = PortfolioOptimizer(OptimizerConfig(
            kelly_fraction=0.25,
            hedge_threshold=0.7,
            hedge_ratio=0.3,
        ))
        
        # Correlation tracking
        self.corr_tracker = CorrelationTracker(window=1000)
        
        # Monitoring
        self.monitor = TradingMonitor()
    
    def register_index(self, name: str):
        """Register an index."""
        self._index_equity[name] = self.initial_equity
        self._index_peak[name] = self.initial_equity
        self._index_position[name] = 0
        self.monitor.register_index(name, self.initial_equity)
    
    def update_index(
        self,
        name: str,
        equity: float,
        position: int,
        current_price: float,
        pnl: float,
        wm_direction: str = 'FLAT',
        wm_confidence: float = 0.0,
        wm_mean: float = 0.0,
        wm_std: float = 0.0,
        position_size: float = 1.0,
    ):
        """Update state for an index."""
        self._index_equity[name] = equity
        self._index_position[name] = position
        self._index_peak[name] = max(self._index_peak[name], equity)
        
        # Update correlation tracker
        self.corr_tracker.update(name, current_price)
        
        # Update portfolio totals
        self.total_equity = sum(self._index_equity.values())
        self.peak_equity = max(self.peak_equity, self.total_equity)
        self.total_pnl = self.total_equity - self.initial_equity * len(self._index_equity)
        
        # Update risk manager state
        self.risk_mgr.update_state(name, position, equity, self._index_peak[name])
        
        # Update monitoring
        self.monitor.update(
            name,
            equity=equity,
            position=position,
            current_price=current_price,
            pnl=pnl,
            wm_direction=wm_direction,
            wm_confidence=wm_confidence,
            wm_mean=wm_mean,
            wm_std=wm_std,
            position_size=position_size,
        )
    
    def record_trade(self, pnl: float):
        """Record trade for Kelly updates."""
        self.optimizer.record_trade(pnl)
    
    def update_momentum(self, index: str, ret: float):
        """Update momentum signal."""
        self.optimizer.update_momentum(index, ret)
    
    def compute_position_size(
        self,
        index: str,
        confidence: float,
        current_drawdown: float,
        volatility: float,
        current_price: float = 0.0,
    ) -> float:
        """Compute position size with portfolio-level optimizations."""
        # Get base size from risk manager
        size = self.risk_mgr.compute_position_size(
            confidence=confidence,
            current_drawdown=current_drawdown,
            volatility=volatility,
            index_type=index.lower(),
        )
        
        # Apply portfolio optimizer weights
        indices = list(self._index_equity.keys())
        expected_returns = np.array([
            confidence * 0.001 if idx == index else 0.0
            for idx in indices
        ])
        volatilities = {idx: volatility if idx == index else 0.001 for idx in indices}
        correlations = self.corr_tracker.get_correlation_matrix()
        current_positions = {
            idx: float(self._index_position.get(idx, 0))
            for idx in indices
        }
        
        weights = self.optimizer.compute_weights(
            indices=indices,
            expected_returns=expected_returns,
            volatilities=volatilities,
            correlations=correlations,
            current_positions=current_positions,
            method="combined",
        )
        
        # Blend risk manager size with optimizer weight
        optimizer_weight = abs(weights.get(index, 0.0))
        blended = 0.5 * size + 0.5 * optimizer_weight
        
        return max(0.0, min(1.5, blended))
    
    def get_portfolio_drawdown(self) -> float:
        """Get current portfolio-level drawdown."""
        if self.peak_equity <= 0:
            return 0.0
        return (self.peak_equity - self.total_equity) / self.peak_equity
    
    def should_trade(self, index: str, expected_return: float, confidence: float, volatility: float) -> bool:
        """Decide whether a trade is worth taking."""
        dd = (self._index_peak[index] - self._index_equity[index]) / self._index_peak[index]
        return self.risk_mgr.should_trade(expected_return, confidence, dd, volatility)
    
    def get_summary(self) -> dict:
        """Get portfolio summary."""
        portfolio_dd = self.get_portfolio_drawdown()
        div_score = self.corr_tracker.get_diversification_score()
        
        active_positions = sum(1 for p in self._index_position.values() if p != 0)
        total_trades = sum(m.trade_count for m in self.monitor._indices.values())
        total_wins = sum(m.win_count for m in self.monitor._indices.values())
        
        return {
            'total_equity': self.total_equity,
            'total_pnl': self.total_pnl,
            'portfolio_drawdown': portfolio_dd,
            'diversification_score': div_score,
            'active_positions': active_positions,
            'total_trades': total_trades,
            'overall_win_rate': total_wins / total_trades if total_trades > 0 else 0.0,
            'index_count': len(self._index_equity),
            'alerts': [],
        }
    
    def format_status(self) -> str:
        """Format portfolio status."""
        summary = self.get_summary()
        corr_matrix = self.corr_tracker.get_correlation_matrix()
        
        # Get optimizer status
        kelly_stats = self.optimizer.kelly.get_stats()
        momentum_signals = self.optimizer.momentum.get_cross_signals()
        
        lines = [
            f"\n{'='*90}",
            f"  PORTFOLIO STATUS @ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
            f"{'='*90}",
            f"  Total Equity: ${summary['total_equity']:.2f}  |  PnL: ${summary['total_pnl']:+.2f}  |  DD: {summary['portfolio_drawdown']:.1%}",
            f"  Diversification: {summary['diversification_score']:.2f}  |  Active: {summary['active_positions']}/{summary['index_count']}  |  Trades: {summary['total_trades']}",
            f"  Kelly Fraction: {kelly_stats['kelly_fraction']:.3f}  |  Win Rate: {kelly_stats.get('win_rate', 0):.0%}",
            f"{'-'*90}",
        ]
        
        # Per-index status
        for name in self._index_equity:
            pos_str = {1: 'LONG', -1: 'SHORT', 0: 'FLAT'}[self._index_position[name]]
            m = self.monitor._indices.get(name)
            momentum = momentum_signals.get(name, 0.0)
            if m:
                lines.append(
                    f"  {name:12s} | Equity: ${m.equity:8.2f} | "
                    f"PnL: ${m.total_pnl:+8.2f} | "
                    f"DD: {m.current_drawdown:.1%} | "
                    f"Pos: {pos_str:5s} | "
                    f"Mom: {momentum:+.3f} | "
                    f"Trades: {m.trade_count:3d}"
                )
        
        # Correlation matrix
        if len(corr_matrix) > 1:
            lines.append(f"{'-'*90}")
            lines.append(f"  Correlations:")
            indices = list(corr_matrix.keys())
            header = f"  {'':12s}" + "".join(f"{idx:>10s}" for idx in indices)
            lines.append(header)
            for i1 in indices:
                row = f"  {i1:12s}"
                for i2 in indices:
                    corr = corr_matrix[i1][i2]
                    row += f"{corr:10.3f}"
                lines.append(row)
        
        lines.append(f"{'='*90}")
        return "\n".join(lines)
    
    def save_state(self, path: str):
        """Save portfolio state to JSON."""
        # Get optimizer status
        kelly_stats = self.optimizer.kelly.get_stats()
        momentum_signals = self.optimizer.momentum.get_cross_signals()
        hedge_signals = self.optimizer.hedger.get_hedge_signals({
            idx: float(self._index_position.get(idx, 0))
            for idx in self._index_equity
        })
        
        data = {
            'summary': self.get_summary(),
            'correlation_matrix': self.corr_tracker.get_correlation_matrix(),
            'metrics': self.monitor.get_all_metrics(),
            'optimizer': {
                'kelly': kelly_stats,
                'momentum': momentum_signals,
                'hedge_signals': hedge_signals,
            },
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)


# ---------------------------------------------------------------------------
# Multi-Index Paper Trader
# ---------------------------------------------------------------------------
class MultiIndexPaperTrader:
    """
    Runs paper trading on multiple indices simultaneously.
    """
    
    def __init__(
        self,
        indices: list[str] | None = None,
        dry_run: bool = True,
        history_bars: int = 500,
        device_str: str | None = None,
    ):
        self.indices = indices or DEFAULT_INDICES
        self.dry_run = dry_run
        self.history_bars = history_bars
        self.device = torch.device(
            device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
        )
        
        # Validate indices
        for idx in self.indices:
            if idx not in INDEX_CONFIGS:
                raise ValueError(f"Unknown index: {idx}. Available: {list(INDEX_CONFIGS.keys())}")
        
        # Portfolio manager
        self.portfolio = PortfolioManager(INITIAL_EQUITY)
        for idx in self.indices:
            self.portfolio.register_index(idx)
        
        # Per-index traders
        self._traders: dict[str, PaperTrader] = {}
        for idx in self.indices:
            config = INDEX_CONFIGS[idx]
            self._traders[idx] = PaperTrader(
                symbol=config['symbol'],
                world_model_path=config['world_model'],
                policy_path=config['policy'],
                history_bars=history_bars,
                dry_run=dry_run,
                device_str=device_str,
            )
        
        # WebSocket broadcasting
        self._ws_clients: set = set()
        self._broadcast_queue: asyncio.Queue | None = None
        
        # State
        self._running = False
        self._candle_counts: dict[str, int] = {idx: 0 for idx in self.indices}
        self._last_prices: dict[str, float] = {}
    
    def _sync_portfolio(self, index: str):
        """Sync per-index state to portfolio manager."""
        trader = self._traders[index]
        
        # Compute SNR from world model prediction
        features = trader.compute_features()
        if features is not None:
            means, variances = trader.run_world_model(features)
            total_mean = float(np.sum(means))
            total_var = float(np.sum(variances))
            snr = abs(total_mean) / (np.sqrt(total_var) + 1e-10)
            snr = min(snr, 10.0)
            wm_direction = 'UP' if total_mean > 0 else 'DOWN'
        else:
            snr = 0.5
            total_mean = 0.0
            total_var = 0.001
            wm_direction = 'FLAT'
        
        # Compute position size with portfolio-level optimizations
        current_dd = (trader.peak_equity - trader.equity) / trader.peak_equity if trader.peak_equity > 0 else 0.0
        rolling_vol = trader.compute_rolling_vol()
        current_price = trader.candles[-1]['close'] if trader.candles else 0
        
        position_size = self.portfolio.compute_position_size(
            index=index,
            confidence=snr,
            current_drawdown=current_dd,
            volatility=rolling_vol,
            current_price=current_price,
        )
        
        # Update momentum
        if index in self._last_prices and self._last_prices[index] > 0:
            ret = (current_price - self._last_prices[index]) / self._last_prices[index]
            self.portfolio.update_momentum(index, ret)
        self._last_prices[index] = current_price
        
        # Update portfolio
        self.portfolio.update_index(
            name=index,
            equity=trader.equity,
            position=trader.position,
            current_price=current_price,
            pnl=trader.total_pnl,
            wm_direction=wm_direction,
            wm_confidence=min(snr / 2.0, 1.0),
            wm_mean=total_mean,
            wm_std=np.sqrt(total_var),
            position_size=position_size,
        )
    
    async def _broadcast_state(self):
        """Broadcast current state to WebSocket clients."""
        if not self._ws_clients or not self._broadcast_queue:
            return
        
        state = {
            'type': 'portfolio_update',
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'portfolio': self.portfolio.get_summary(),
            'indices': {},
        }
        
        for idx in self.indices:
            trader = self._traders[idx]
            m = self.portfolio.monitor._indices.get(idx)
            state['indices'][idx] = {
                'equity': trader.equity,
                'position': {1: 'LONG', -1: 'SHORT', 0: 'FLAT'}[trader.position],
                'pnl': trader.total_pnl,
                'trade_count': trader.trade_count,
                'wm_direction': m.wm_direction if m else 'FLAT',
                'wm_confidence': m.wm_confidence if m else 0.0,
            }
        
        state['correlation_matrix'] = self.portfolio.corr_tracker.get_correlation_matrix()
        
        # Broadcast to all clients
        disconnected = set()
        for client in self._ws_clients:
            try:
                await client.send(json.dumps(state, default=str))
            except Exception:
                disconnected.add(client)
        self._ws_clients -= disconnected
    
    async def run(self):
        """Run multi-index paper trading."""
        self._running = True
        self._broadcast_queue = asyncio.Queue()
        
        logger.info(
            "multi_index_start indices=%s n_indices=%d",
            self.indices, len(self.indices)
        )
        
        # Connect all traders concurrently
        connections = []
        for idx in self.indices:
            trader = self._traders[idx]
            connections.append(self._connect_trader(idx, trader))
        
        await asyncio.gather(*connections)
        
        logger.info("all_indices_connected")
        
        # Print initial status
        print(self.portfolio.format_status())
        
        # Main loop
        save_counter = 0
        broadcast_counter = 0
        try:
            while self._running:
                await asyncio.sleep(5)  # Sync every 5 seconds
                
                # Sync portfolio state from all traders
                for idx in self.indices:
                    if len(self._traders[idx].candles) > 0:
                        self._sync_portfolio(idx)
                
                # Print status periodically
                cycle = max(self._candle_counts.values()) if self._candle_counts else 0
                if cycle > 0 and cycle % 10 == 0:
                    print(self.portfolio.format_status())
                
                # Broadcast state via WebSocket
                broadcast_counter += 1
                if broadcast_counter >= 2:  # Every 10 seconds
                    broadcast_counter = 0
                    await self._broadcast_state()
                
                # Save state every 30 seconds for dashboard
                save_counter += 1
                if save_counter >= 6:  # 6 * 5s = 30s
                    save_counter = 0
                    self.portfolio.save_state('checkpoints_tiny/portfolio_state.json')
        
        except KeyboardInterrupt:
            logger.info("stopped_by_user")
        finally:
            self._running = False
            self._print_summary()
    
    async def _connect_trader(self, index: str, trader: PaperTrader):
        """Connect a single trader to Deriv."""
        try:
            # Load models
            trader.load_models()
            
            # Connect to Deriv
            conn = DerivConnection()
            await conn.connect()
            logger.info("connected index=%s", index)
            
            # Fetch history
            resp = await conn.get_ticks_history(
                trader.symbol, style="candles",
                count=min(trader.history_bars, 1000), granularity=60,
            )
            for c in resp.get("candles", []):
                trader.add_candle({
                    "epoch": c["epoch"],
                    "open": float(c["open"]), "high": float(c["high"]),
                    "low": float(c["low"]), "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            logger.info("history_fetched index=%s n=%d", index, len(trader.candles))
            
            # Build state builder
            trader.rebuild_state_builder()
            
            # Run initial prediction
            trader._run_cycle()
            self._candle_counts[index] = 1
            
            # Subscribe to live ticks
            sub_id = await conn.subscribe(
                {"ticks": trader.symbol}, handler=trader.handle_tick,
            )
            logger.info("live_active index=%s sub_id=%s", index, sub_id)
        
        except Exception as e:
            logger.error("connection_failed index=%s error=%s", index, str(e))
    
    def _print_summary(self):
        """Print trading summary on exit."""
        print(f"\n{'='*90}")
        print("MULTI-INDEX PAPER TRADING SUMMARY")
        print(f"{'='*90}")
        
        summary = self.portfolio.get_summary()
        print(f"  Total Equity: ${summary['total_equity']:.2f}")
        print(f"  Total PnL: ${summary['total_pnl']:+.2f}")
        print(f"  Portfolio Return: {(summary['total_equity'] / (INITIAL_EQUITY * summary['index_count']) - 1) * 100:+.1f}%")
        print(f"  Max Portfolio DD: {summary['portfolio_drawdown']:.1%}")
        print(f"  Total Trades: {summary['total_trades']}")
        print(f"  Overall Win Rate: {summary['overall_win_rate']:.0%}")
        print(f"  Diversification Score: {summary['diversification_score']:.2f}")
        
        # Kelly stats
        kelly_stats = self.portfolio.optimizer.kelly.get_stats()
        print(f"  Kelly Fraction: {kelly_stats['kelly_fraction']:.3f}")
        
        print(f"\n  Per-Index Breakdown:")
        for idx in self.indices:
            trader = self._traders[idx]
            ret = (trader.equity / INITIAL_EQUITY - 1) * 100
            print(f"    {idx:12s}: Equity=${trader.equity:.2f}  Return={ret:+.1f}%  Trades={trader.trade_count}")
        
        print(f"{'='*90}")
        
        # Save state
        state_path = "checkpoints_tiny/portfolio_state.json"
        self.portfolio.save_state(state_path)
        print(f"\nPortfolio state saved to: {state_path}")
        
        # Save trade logs
        for idx in self.indices:
            trader = self._traders[idx]
            log_path = Path(f"checkpoints_tiny/paper_trades_{idx.lower()}.json")
            with open(log_path, "w") as f:
                json.dump(trader.trade_log, f, indent=2, default=str)
            print(f"Trade log for {idx}: {log_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Multi-index paper trading")
    parser.add_argument("--indices", nargs="+", default=DEFAULT_INDICES,
                        help="Indices to trade (default: all 4)")
    parser.add_argument("--dry-run", action="store_true", default=True)
    parser.add_argument("--history-bars", type=int, default=500)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()
    
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S", stream=sys.stdout,
    )
    
    # Validate indices
    valid = [idx for idx in args.indices if idx in INDEX_CONFIGS]
    if not valid:
        print(f"Error: No valid indices. Available: {list(INDEX_CONFIGS.keys())}")
        sys.exit(1)
    
    print(f"\nStarting multi-index paper trading for: {valid}")
    print(f"Each index starts with ${INITIAL_EQUITY:.2f} equity")
    print(f"Total starting equity: ${INITIAL_EQUITY * len(valid):.2f}\n")
    
    trader = MultiIndexPaperTrader(
        indices=valid,
        dry_run=args.dry_run,
        history_bars=args.history_bars,
        device_str=args.device,
    )
    
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
