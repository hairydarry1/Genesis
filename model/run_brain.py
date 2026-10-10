"""
model/run_brain.py — CLI for the metacognition system.

Usage:
    python -m model.run_brain              # Start autonomous learning loop
    python -m model.run_brain --once       # Run one cycle
    python -m model.run_brain --status     # Show current status
    python -m model.run_brain --test       # Test all components
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def cmd_start(args):
    """Start the autonomous learning loop."""
    from model.metacognition import Metacognition

    brain = Metacognition()
    print(f"\n{'='*60}")
    print(f"  Genesis Metacognition System")
    print(f"{'='*60}")
    print(f"  Interval: {args.interval} minutes")
    print(f"  Press Ctrl+C to stop")
    print(f"{'='*60}\n")

    brain.start(interval_minutes=args.interval)

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        brain.stop()
        print("Stopped.")


def cmd_once(args):
    """Run one cycle."""
    from model.metacognition import Metacognition

    brain = Metacognition()
    print("Running one metacognition cycle...")
    brain.run_cycle()
    print("\nStatus:")
    print(json.dumps(brain.get_status(), indent=2, default=str))


def cmd_status(args):
    """Show current status."""
    from model.metacognition import Metacognition

    brain = Metacognition()
    status = brain.get_status()

    print(f"\n{'='*60}")
    print(f"  Metacognition Status")
    print(f"{'='*60}")
    print(f"  Running: {status['running']}")
    print(f"  Cycles: {status['cycle']}")
    print(f"  Health: {status['health']:.2f}")
    print(f"  Active strategies: {status['active_strategies']}")
    print(f"  Retired strategies: {status['retired_strategies']}")
    print(f"  Total P&L: ${status['total_pnl']:.2f}")
    print(f"  Needs attention: {status['needs_attention']}")

    if status["recent_learnings"]:
        print(f"\n  Recent learnings:")
        for l in status["recent_learnings"][-5:]:
            print(f"    - {l}")

    if status["performance"]["recommendations"]:
        print(f"\n  Recommendations:")
        for r in status["performance"]["recommendations"]:
            print(f"    - {r}")

    print(f"{'='*60}\n")


def cmd_test(args):
    """Test all components."""
    print("Testing metacognition components...\n")

    # Test Auto Feature Engine
    print("[1/5] Auto Feature Engine...")
    try:
        from model.auto_features import AutoFeatureEngine
        import pandas as pd
        import numpy as np

        engine = AutoFeatureEngine()
        # Generate synthetic data
        n = 500
        close = 100 + np.cumsum(np.random.randn(n) * 0.5)
        df = pd.DataFrame({
            "open": close + np.random.randn(n) * 0.1,
            "high": close + abs(np.random.randn(n) * 0.5),
            "low": close - abs(np.random.randn(n) * 0.5),
            "close": close,
            "volume": np.random.randint(100, 1000, n),
        })
        candidates = engine.generate(df)
        print(f"  OK: Generated {len(candidates)} feature candidates")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test Performance Monitor
    print("[2/5] Performance Monitor...")
    try:
        from model.performance_monitor import PerformanceMonitor
        monitor = PerformanceMonitor()
        for i in range(30):
            monitor.record_trade(pnl=np.random.randn() * 100, index="R_75")
        status = monitor.get_status()
        print(f"  OK: {status.total_trades} trades, health={status.health_score:.2f}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test Strategy Lifecycle
    print("[3/5] Strategy Lifecycle...")
    try:
        from model.strategy_lifecycle import StrategyLifecycle
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            lifecycle = StrategyLifecycle(data_dir=tmpdir)
            sv = lifecycle.create("test_strategy", "Test", ["feature1", "feature2"])
            for i in range(20):
                lifecycle.record_trade(sv.id, pnl=np.random.randn() * 50)
            summary = lifecycle.get_summary()
            print(f"  OK: {summary['total']} strategies, active={summary['active']}")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test Online Retrainer
    print("[4/5] Online Retrainer...")
    try:
        from model.online_retrain import OnlineRetrainer
        retrainer = OnlineRetrainer()
        print(f"  OK: Retrainer initialized")
    except Exception as e:
        print(f"  FAILED: {e}")

    # Test Metacognition
    print("[5/5] Metacognition Controller...")
    try:
        from model.metacognition import Metacognition
        brain = Metacognition()
        status = brain.get_status()
        print(f"  OK: cycle={status['cycle']}, health={status['health']:.2f}")
    except Exception as e:
        print(f"  FAILED: {e}")

    print("\nAll tests complete!")


def main():
    parser = argparse.ArgumentParser(description="Genesis Metacognition System")
    sub = parser.add_subparsers(dest="command")

    start_p = sub.add_parser("start", help="Start autonomous learning loop")
    start_p.add_argument("--interval", type=int, default=60, help="Minutes between cycles")

    sub.add_parser("once", help="Run one cycle")
    sub.add_parser("status", help="Show status")
    sub.add_parser("test", help="Test all components")

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if args.command == "start":
        cmd_start(args)
    elif args.command == "once":
        cmd_once(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "test":
        cmd_test(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
