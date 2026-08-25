"""
eval/monitoring.py — Real-time monitoring for multi-index trading.

Provides:
1. Performance metrics tracking (Sharpe, Sortino, max DD, win rate)
2. WebSocket server for live dashboard updates
3. JSON logging for historical analysis
4. Alert system for risk events

Usage:
    monitor = TradingMonitor()
    monitor.update('R_75', equity=10050, position=1, pnl=50, ...)
    metrics = monitor.get_metrics('R_75')
    monitor.save_log('checkpoints_tiny/monitor_log.json')
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class IndexMetrics:
    """Metrics for a single index."""
    name: str
    
    # Current state
    equity: float = 10000.0
    peak_equity: float = 10000.0
    position: int = 0  # +1 long, -1 short, 0 flat
    entry_price: float = 0.0
    current_price: float = 0.0
    
    # PnL
    total_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    
    # Trade stats
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    
    # Risk metrics
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    sharpe_ratio: float = 0.0
    sortino_ratio: float = 0.0
    profit_factor: float = 0.0
    
    # World model
    wm_direction: str = 'FLAT'
    wm_confidence: float = 0.0
    wm_mean: float = 0.0
    wm_std: float = 0.0
    
    # Risk management
    position_size: float = 1.0
    risk_score: float = 0.0
    
    # History (last N values)
    equity_history: list[float] = field(default_factory=list)
    pnl_history: list[float] = field(default_factory=list)
    trade_history: list[dict] = field(default_factory=list)
    
    # Timestamps
    last_update: str = ''
    start_time: str = ''


class TradingMonitor:
    """
    Real-time monitoring for multi-index trading.
    
    Tracks performance metrics, risk events, and provides
    data for live dashboard visualization.
    """
    
    def __init__(self, max_history: int = 1000):
        self.max_history = max_history
        self._indices: dict[str, IndexMetrics] = {}
        self._alerts: list[dict] = []
        self._start_time = datetime.now(timezone.utc).isoformat()
    
    def register_index(self, name: str, initial_equity: float = 10000.0):
        """Register an index for monitoring."""
        self._indices[name] = IndexMetrics(
            name=name,
            equity=initial_equity,
            peak_equity=initial_equity,
            start_time=self._start_time,
            last_update=self._start_time,
        )
    
    def update(
        self,
        index: str,
        equity: float | None = None,
        position: int | None = None,
        current_price: float | None = None,
        entry_price: float | None = None,
        pnl: float | None = None,
        wm_direction: str | None = None,
        wm_confidence: float | None = None,
        wm_mean: float | None = None,
        wm_std: float | None = None,
        position_size: float | None = None,
        risk_score: float | None = None,
    ):
        """Update metrics for an index."""
        if index not in self._indices:
            self.register_index(index)
        
        m = self._indices[index]
        now = datetime.now(timezone.utc).isoformat()
        
        if equity is not None:
            m.equity = equity
            m.peak_equity = max(m.peak_equity, equity)
            m.current_drawdown = (m.peak_equity - equity) / m.peak_equity if m.peak_equity > 0 else 0.0
            m.max_drawdown = max(m.max_drawdown, m.current_drawdown)
            
            # Track equity history
            m.equity_history.append(equity)
            if len(m.equity_history) > self.max_history:
                m.equity_history = m.equity_history[-self.max_history:]
        
        if position is not None:
            m.position = position
        if current_price is not None:
            m.current_price = current_price
        if entry_price is not None:
            m.entry_price = entry_price
        if pnl is not None:
            m.total_pnl = pnl
            m.pnl_history.append(pnl)
            if len(m.pnl_history) > self.max_history:
                m.pnl_history = m.pnl_history[-self.max_history:]
        
        if wm_direction is not None:
            m.wm_direction = wm_direction
        if wm_confidence is not None:
            m.wm_confidence = wm_confidence
        if wm_mean is not None:
            m.wm_mean = wm_mean
        if wm_std is not None:
            m.wm_std = wm_std
        if position_size is not None:
            m.position_size = position_size
        if risk_score is not None:
            m.risk_score = risk_score
        
        m.last_update = now
        
        # Check for risk alerts
        self._check_alerts(index, m)
    
    def record_trade(
        self,
        index: str,
        side: str,
        entry_price: float,
        exit_price: float,
        pnl: float,
        equity: float,
    ):
        """Record a completed trade."""
        if index not in self._indices:
            self.register_index(index)
        
        m = self._indices[index]
        m.trade_count += 1
        if pnl > 0:
            m.win_count += 1
        else:
            m.loss_count += 1
        
        m.realized_pnl += pnl
        m.trade_history.append({
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'side': side,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'equity': equity,
        })
        
        if len(m.trade_history) > self.max_history:
            m.trade_history = m.trade_history[-self.max_history:]
    
    def _check_alerts(self, index: str, m: IndexMetrics):
        """Check for risk events and generate alerts."""
        # Drawdown alert
        if m.current_drawdown > 0.10:
            self._add_alert('CRITICAL', index, f'Drawdown {m.current_drawdown:.1%} exceeds 10%')
        elif m.current_drawdown > 0.05:
            self._add_alert('WARNING', index, f'Drawdown {m.current_drawdown:.1%} exceeds 5%')
    
    def _add_alert(self, level: str, index: str, message: str):
        """Add an alert."""
        alert = {
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'level': level,
            'index': index,
            'message': message,
        }
        self._alerts.append(alert)
        if len(self._alerts) > 100:
            self._alerts = self._alerts[-100:]
    
    def get_metrics(self, index: str) -> dict:
        """Get computed metrics for an index."""
        if index not in self._indices:
            return {}
        
        m = self._indices[index]
        
        # Compute derived metrics
        win_rate = m.win_count / m.trade_count if m.trade_count > 0 else 0.0
        
        # Sharpe ratio (annualized, assuming 1-min bars)
        if len(m.pnl_history) > 10:
            returns = np.array(m.pnl_history)
            if np.std(returns) > 0:
                m.sharpe_ratio = float(np.mean(returns) / np.std(returns) * np.sqrt(252 * 24 * 60))
        
        # Sortino ratio (downside deviation)
        if len(m.pnl_history) > 10:
            returns = np.array(m.pnl_history)
            downside = returns[returns < 0]
            if len(downside) > 0 and np.std(downside) > 0:
                m.sortino_ratio = float(np.mean(returns) / np.std(downside) * np.sqrt(252 * 24 * 60))
        
        # Profit factor
        if len(m.trade_history) > 0:
            gross_profit = sum(t['pnl'] for t in m.trade_history if t['pnl'] > 0)
            gross_loss = abs(sum(t['pnl'] for t in m.trade_history if t['pnl'] < 0))
            m.profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
        
        return {
            'name': m.name,
            'equity': m.equity,
            'peak_equity': m.peak_equity,
            'position': m.position,
            'current_price': m.current_price,
            'total_pnl': m.total_pnl,
            'realized_pnl': m.realized_pnl,
            'trade_count': m.trade_count,
            'win_rate': win_rate,
            'max_drawdown': m.max_drawdown,
            'current_drawdown': m.current_drawdown,
            'sharpe_ratio': m.sharpe_ratio,
            'sortino_ratio': m.sortino_ratio,
            'profit_factor': m.profit_factor,
            'wm_direction': m.wm_direction,
            'wm_confidence': m.wm_confidence,
            'wm_mean': m.wm_mean,
            'wm_std': m.wm_std,
            'position_size': m.position_size,
            'risk_score': m.risk_score,
            'equity_history': m.equity_history[-100:],  # Last 100 points
            'last_update': m.last_update,
        }
    
    def get_all_metrics(self) -> dict[str, dict]:
        """Get metrics for all indices."""
        return {name: self.get_metrics(name) for name in self._indices}
    
    def get_portfolio_summary(self) -> dict:
        """Get portfolio-level summary."""
        total_equity = sum(m.equity for m in self._indices.values())
        total_pnl = sum(m.total_pnl for m in self._indices.values())
        total_trades = sum(m.trade_count for m in self._indices.values())
        total_wins = sum(m.win_count for m in self._indices.values())
        
        # Portfolio drawdown
        peak_equity = sum(m.peak_equity for m in self._indices.values())
        portfolio_dd = (peak_equity - total_equity) / peak_equity if peak_equity > 0 else 0.0
        
        return {
            'total_equity': total_equity,
            'total_pnl': total_pnl,
            'portfolio_drawdown': portfolio_dd,
            'total_trades': total_trades,
            'overall_win_rate': total_wins / total_trades if total_trades > 0 else 0.0,
            'index_count': len(self._indices),
            'active_positions': sum(1 for m in self._indices.values() if m.position != 0),
            'alerts': self._alerts[-10:],  # Last 10 alerts
            'start_time': self._start_time,
        }
    
    def get_dashboard_data(self) -> dict:
        """Get all data needed for the dashboard."""
        return {
            'portfolio': self.get_portfolio_summary(),
            'indices': self.get_all_metrics(),
            'timestamp': datetime.now(timezone.utc).isoformat(),
        }
    
    def save_log(self, path: str):
        """Save monitoring data to JSON."""
        data = self.get_dashboard_data()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w') as f:
            json.dump(data, f, indent=2, default=str)
    
    def format_status(self) -> str:
        """Format current status as readable output."""
        portfolio = self.get_portfolio_summary()
        lines = [
            f"\n{'='*80}",
            f"  PORTFOLIO STATUS @ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}",
            f"{'='*80}",
            f"  Total Equity: ${portfolio['total_equity']:.2f}",
            f"  Total PnL: ${portfolio['total_pnl']:+.2f}",
            f"  Portfolio DD: {portfolio['portfolio_drawdown']:.1%}",
            f"  Trades: {portfolio['total_trades']} | Win Rate: {portfolio['overall_win_rate']:.0%}",
            f"  Active Positions: {portfolio['active_positions']}/{portfolio['index_count']}",
            f"{'='*80}",
        ]
        
        for name, metrics in self._indices.items():
            pos_str = {1: 'LONG', -1: 'SHORT', 0: 'FLAT'}[metrics.position]
            lines.append(
                f"  {name:12s} | Equity: ${metrics.equity:8.2f} | "
                f"PnL: ${metrics.total_pnl:+8.2f} | "
                f"DD: {metrics.current_drawdown:.1%} | "
                f"Pos: {pos_str} | "
                f"Trades: {metrics.trade_count}"
            )
        
        lines.append(f"{'='*80}")
        return "\n".join(lines)
