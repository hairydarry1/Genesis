"""
eval/auto_retrain.py — Automated retraining pipeline.

Retrains world model and policy on new data periodically.
Supports:
  1. Scheduled retraining (daily/weekly)
  2. Performance-triggered retraining
  3. Walk-forward validation before deployment
  4. Model versioning and rollback

Usage:
    python eval.auto_retrain.py --schedule weekly
    python eval.auto_retrain.py --force-retrain
    python eval.auto_retrain.py --check-performance
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class RetrainConfig:
    """Configuration for automated retraining."""
    
    # Schedule
    schedule: str = "weekly"  # daily, weekly, monthly
    check_interval_hours: int = 6
    
    # Performance thresholds
    min_sharpe: float = 0.5
    max_drawdown: float = 0.15
    min_win_rate: float = 0.45
    performance_window_days: int = 30
    
    # Training parameters
    world_model_epochs: int = 50
    policy_iterations: int = 100
    policy_rollout_steps: int = 500
    
    # Validation
    validation_folds: int = 4
    train_days: int = 60
    val_days: int = 30
    
    # Model versioning
    max_versions: int = 5
    auto_deploy: bool = True  # Auto-deploy if validation passes
    
    # Data
    dataset_dir: str = "data"
    checkpoint_dir: str = "checkpoints_tiny"
    
    # Indices to retrain
    indices: list = field(default_factory=lambda: ["R_75", "R_10", "BOOM500", "CRASH500"])


class PerformanceTracker:
    """Tracks live trading performance for retraining triggers."""
    
    def __init__(self, config: RetrainConfig):
        self.config = config
        self._trades: list[dict] = []
        self._equity_history: list[float] = []
        self._last_retrain: datetime | None = None
    
    def record_trade(self, index: str, pnl: float, equity: float):
        """Record a completed trade."""
        self._trades.append({
            'timestamp': datetime.now(timezone.utc),
            'index': index,
            'pnl': pnl,
            'equity': equity,
        })
        self._equity_history.append(equity)
        
        # Keep last N trades
        if len(self._trades) > 1000:
            self._trades = self._trades[-1000:]
        if len(self._equity_history) > 1000:
            self._equity_history = self._equity_history[-1000:]
    
    def get_performance_metrics(self, window_days: int | None = None) -> dict:
        """Compute performance metrics over recent trades."""
        window = window_days or self.config.performance_window_days
        cutoff = datetime.now(timezone.utc) - timedelta(days=window)
        
        recent_trades = [t for t in self._trades if t['timestamp'] > cutoff]
        
        if not recent_trades:
            return {'needs_retrain': False, 'reason': 'no_data'}
        
        pnls = [t['pnl'] for t in recent_trades]
        wins = sum(1 for p in pnls if p > 0)
        
        # Sharpe ratio (annualized)
        if len(pnls) > 10:
            returns = np.diff(self._equity_history[-len(pnls)-1:]) / np.array(self._equity_history[-len(pnls)-1:-1])
            sharpe = float(np.mean(returns) / (np.std(returns) + 1e-10) * np.sqrt(252))
        else:
            sharpe = 0.0
        
        # Max drawdown
        if self._equity_history:
            peak = self._equity_history[0]
            max_dd = 0.0
            for eq in self._equity_history:
                peak = max(peak, eq)
                dd = (peak - eq) / peak if peak > 0 else 0
                max_dd = max(max_dd, dd)
        else:
            max_dd = 0.0
        
        win_rate = wins / len(recent_trades) if recent_trades else 0.0
        
        return {
            'n_trades': len(recent_trades),
            'total_pnl': sum(pnls),
            'avg_pnl': np.mean(pnls),
            'sharpe_ratio': sharpe,
            'max_drawdown': max_dd,
            'win_rate': win_rate,
            'needs_retrain': self._check_retrain_trigger(sharpe, max_dd, win_rate),
        }
    
    def _check_retrain_trigger(self, sharpe: float, max_dd: float, win_rate: float) -> bool:
        """Check if retraining is needed based on performance."""
        triggers = []
        
        if sharpe < self.config.min_sharpe:
            triggers.append(f"sharpe={sharpe:.2f} < {self.config.min_sharpe}")
        
        if max_dd > self.config.max_drawdown:
            triggers.append(f"max_dd={max_dd:.1%} > {self.config.max_drawdown:.0%}")
        
        if win_rate < self.config.min_win_rate:
            triggers.append(f"win_rate={win_rate:.0%} < {self.config.min_win_rate:.0%}")
        
        # Check schedule
        if self._last_retrain:
            hours_since = (datetime.now(timezone.utc) - self._last_retrain).total_seconds() / 3600
            if self.config.schedule == 'daily' and hours_since < 20:
                return False
            elif self.config.schedule == 'weekly' and hours_since < 140:
                return False
            elif self.config.schedule == 'monthly' and hours_since < 680:
                return False
        
        return len(triggers) > 0
    
    def mark_retrained(self):
        """Mark that retraining has been performed."""
        self._last_retrain = datetime.now(timezone.utc)


class ModelVersionManager:
    """Manages model versions for rollback capability."""
    
    def __init__(self, checkpoint_dir: str, max_versions: int = 5):
        self.checkpoint_dir = Path(checkpoint_dir)
        self.max_versions = max_versions
        self._versions_file = self.checkpoint_dir / "model_versions.json"
        self._versions = self._load_versions()
    
    def _load_versions(self) -> list[dict]:
        if self._versions_file.exists():
            with open(self._versions_file) as f:
                return json.load(f)
        return []
    
    def _save_versions(self):
        with open(self._versions_file, 'w') as f:
            json.dump(self._versions, f, indent=2, default=str)
    
    def save_version(self, name: str, metrics: dict) -> str:
        """Save current model as a version."""
        version_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        version_dir = self.checkpoint_dir / f"version_{version_id}"
        version_dir.mkdir(exist_ok=True)
        
        # Copy current checkpoints
        for f in ['world_model_best.pt', 'policy_best.pt']:
            src = self.checkpoint_dir / f
            if src.exists():
                shutil.copy2(src, version_dir / f)
        
        # Save metadata
        version_info = {
            'id': version_id,
            'name': name,
            'timestamp': datetime.now(timezone.utc).isoformat(),
            'metrics': metrics,
            'path': str(version_dir),
        }
        self._versions.append(version_info)
        
        # Trim old versions
        if len(self._versions) > self.max_versions:
            old_versions = self._versions[:-self.max_versions]
            for v in old_versions:
                v_path = Path(v['path'])
                if v_path.exists():
                    shutil.rmtree(v_path)
            self._versions = self._versions[-self.max_versions:]
        
        self._save_versions()
        return version_id
    
    def rollback(self, version_id: str) -> bool:
        """Rollback to a previous version."""
        version = next((v for v in self._versions if v['id'] == version_id), None)
        if not version:
            return False
        
        version_dir = Path(version['path'])
        for f in ['world_model_best.pt', 'policy_best.pt']:
            src = version_dir / f
            if src.exists():
                shutil.copy2(src, self.checkpoint_dir / f)
        
        return True
    
    def get_versions(self) -> list[dict]:
        return self._versions
    
    def get_latest(self) -> dict | None:
        return self._versions[-1] if self._versions else None


class AutoRetrainer:
    """
    Automated retraining pipeline.
    
    Workflow:
    1. Check performance metrics
    2. If retraining needed, fetch latest data
    3. Retrain world model and policy
    4. Validate with walk-forward
    5. Deploy if validation passes
    6. Save version for rollback
    """
    
    def __init__(self, config: RetrainConfig | None = None):
        self.config = config or RetrainConfig()
        self.tracker = PerformanceTracker(self.config)
        self.version_mgr = ModelVersionManager(
            self.config.checkpoint_dir,
            self.config.max_versions,
        )
        self._running = False
    
    def check_and_retrain(self, force: bool = False) -> dict:
        """Check if retraining is needed and perform it."""
        result = {
            'retrained': False,
            'validation_passed': False,
            'metrics': {},
            'errors': [],
        }
        
        # Check performance
        perf = self.tracker.get_performance_metrics()
        result['metrics'] = perf
        
        if not force and not perf.get('needs_retrain', False):
            logger.info("performance_ok no_retrain_needed")
            return result
        
        logger.info("retrain_triggered force=%s metrics=%s", force, perf)
        
        # Save current version before retraining
        self.version_mgr.save_version('pre_retrain', perf)
        
        # Retrain each index
        for index in self.config.indices:
            try:
                logger.info("retraining_index=%s", index)
                self._retrain_index(index)
            except Exception as e:
                logger.error("retrain_failed index=%s error=%s", index, str(e))
                result['errors'].append(f"{index}: {str(e)}")
                continue
        
        # Validate
        validation_passed = self._validate_all()
        result['validation_passed'] = validation_passed
        
        if validation_passed and self.config.auto_deploy:
            self.version_mgr.save_version('post_retrain', perf)
            result['retrained'] = True
            self.tracker.mark_retrained()
            logger.info("retrain_deployed")
        else:
            # Rollback
            latest = self.version_mgr.get_latest()
            if latest:
                self.version_mgr.rollback(latest['id'])
                logger.info("retrain_rollback version=%s", latest['id'])
        
        return result
    
    def _retrain_index(self, index: str):
        """Retrain world model and policy for a specific index."""
        dataset_map = {
            'R_75': 'data/dataset_R75_h10.npz',
            'R_10': 'data/dataset_volatility_10_h10.npz',
            'BOOM500': 'data/dataset_boom_500_h10.npz',
            'CRASH500': 'data/dataset_crash_500_h10.npz',
        }
        checkpoint_map = {
            'R_75': 'checkpoints_tiny',
            'R_10': 'checkpoints_vol10',
            'BOOM500': 'checkpoints_boom_500',
            'CRASH500': 'checkpoints_crash_500',
        }
        
        dataset = dataset_map.get(index)
        checkpoint_dir = checkpoint_map.get(index)
        
        if not dataset or not checkpoint_dir:
            raise ValueError(f"Unknown index: {index}")
        
        # Retrain world model
        from model.train_tiny import train_tiny
        train_tiny(
            dataset_path=dataset,
            output_dir=checkpoint_dir,
            epochs=self.config.world_model_epochs,
        )
        
        # Retrain policy
        from env.trading_env import TradingEnv, TradingEnvConfig
        from model.train_tiny import TINY_FEATURES
        from model.policy import train_policy
        
        env = TradingEnv(
            dataset,
            tiny_features=TINY_FEATURES,
            config=TradingEnvConfig(max_steps=self.config.policy_rollout_steps),
        )
        
        train_policy(
            env,
            n_iterations=self.config.policy_iterations,
            rollout_steps=self.config.policy_rollout_steps,
        )
    
    def _validate_all(self) -> bool:
        """Validate all indices with walk-forward."""
        from model.train_tiny import evaluate_walk_forward
        
        for index in self.config.indices:
            checkpoint_map = {
                'R_75': 'checkpoints_tiny/world_model_best.pt',
                'R_10': 'checkpoints_vol10/world_model_best.pt',
                'BOOM500': 'checkpoints_boom_500/world_model_best.pt',
                'CRASH500': 'checkpoints_crash_500/world_model_best.pt',
            }
            dataset_map = {
                'R_75': 'data/dataset_R75_h10.npz',
                'R_10': 'data/dataset_volatility_10_h10.npz',
                'BOOM500': 'data/dataset_boom_500_h10.npz',
                'CRASH500': 'data/dataset_crash_500_h10.npz',
            }
            
            try:
                report = evaluate_walk_forward(
                    checkpoint_path=checkpoint_map[index],
                    dataset_path=dataset_map[index],
                )
                if report['verdict'] == 'FAIL':
                    logger.warning("validation_failed index=%s verdict=%s", index, report['verdict'])
                    return False
            except Exception as e:
                logger.error("validation_error index=%s error=%s", index, str(e))
                return False
        
        return True
    
    def get_status(self) -> dict:
        """Get retraining pipeline status."""
        perf = self.tracker.get_performance_metrics()
        latest_version = self.version_mgr.get_latest()
        versions = self.version_mgr.get_versions()
        
        return {
            'performance': perf,
            'latest_version': latest_version,
            'total_versions': len(versions),
            'schedule': self.config.schedule,
            'last_retrain': self.tracker._last_retrain.isoformat() if self.tracker._last_retrain else None,
        }


def main():
    parser = argparse.ArgumentParser(description="Auto-retraining pipeline")
    parser.add_argument("--schedule", choices=["daily", "weekly", "monthly"], default="weekly")
    parser.add_argument("--force-retrain", action="store_true")
    parser.add_argument("--check-performance", action="store_true")
    parser.add_argument("--indices", nargs="+", default=["R_75", "R_10", "BOOM500", "CRASH500"])
    args = parser.parse_args()
    
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    
    config = RetrainConfig(
        schedule=args.schedule,
        indices=args.indices,
    )
    
    retrainer = AutoRetrainer(config)
    
    if args.check_performance:
        status = retrainer.get_status()
        print(json.dumps(status, indent=2, default=str))
        return
    
    result = retrainer.check_and_retrain(force=args.force_retrain)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
