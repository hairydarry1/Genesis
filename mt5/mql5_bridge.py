"""
mt5/mql5_bridge.py — Bridge between MQL5 EA and Python Brain

Connects:
  MQL5 EA (writes trade CSV) → Python Brain (analyzes) → MQL5 EA (reads config)

The MQL5 EA handles execution (fast, <1ms).
The Python Brain handles thinking (adaptive, learning).

They communicate through files:
  - trades_SYMBOL.csv  (MQL5 writes, Python reads)
  - config_SYMBOL.json (Python writes, MQL5 reads)

Usage:
    python mt5/mql5_bridge.py --symbol CRASH300
    python mt5/mql5_bridge.py --symbol BOOM500 --interval 60
"""

import sys
import csv
import json
import time
import argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_brain import StrategyBrain
from model.trade_thinker import MarketContext, TradeOutcome


# ─── Symbol mapping ────────────────────────────────────────────────────────

SYMBOL_MAP = {
    "CRASH300": "Crash 300 Index",
    "CRASH500": "Crash 500 Index",
    "CRASH1000": "Crash 1000 Index",
    "BOOM300": "Boom 300 Index",
    "BOOM500": "Boom 500 Index",
    "BOOM1000": "Boom 1000 Index",
}

CONFIG_DIR = Path("SpikeCatcher")
CONFIG_DIR.mkdir(exist_ok=True)


class MQL5Bridge:
    """
    Bridge between MQL5 EA and Python Brain.
    
    Reads trade logs from MQL5, analyzes with brain,
    writes config updates back to MQL5.
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.mt5_symbol = SYMBOL_MAP.get(symbol, symbol)
        
        # File paths (matching MQL5 EA)
        self.log_file = CONFIG_DIR / f"trades_{self.mt5_symbol}.csv"
        self.config_file = CONFIG_DIR / f"config_{self.mt5_symbol}.json"
        
        # Brain
        self.brain = StrategyBrain(symbol)
        
        # Trade tracking
        self._processed_trades = 0
        self._last_trade_count = 0
        self._pending_entry = None
        
        # Performance stats
        self._total_trades = 0
        self._wins = 0
        self._losses = 0
        self._total_pnl = 0.0
        
        print(f"  [{symbol}] Bridge initialized")
        print(f"  [{symbol}] Log file: {self.log_file}")
        print(f"  [{symbol}] Config file: {self.config_file}")
    
    def read_trades(self) -> list:
        """Read new trades from MQL5 CSV log."""
        if not self.log_file.exists():
            return []
        
        trades = []
        try:
            with open(self.log_file, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
            # Process only new trades
            new_rows = rows[self._processed_trades:]
            self._processed_trades = len(rows)
            
            for row in new_rows:
                trades.append({
                    'time': row.get('time', ''),
                    'symbol': row.get('symbol', ''),
                    'action': row.get('action', ''),
                    'direction': row.get('direction', ''),
                    'entry': float(row.get('entry', 0)),
                    'exit': float(row.get('exit', 0)),
                    'pnl': float(row.get('pnl', 0)),
                    'pnl_pct': float(row.get('pnl_pct', 0)),
                    'reason': row.get('reason', ''),
                    'bars_held': int(row.get('bars_held', 0)),
                    'equity': float(row.get('equity', 0)),
                    'tp_mult': float(row.get('tp_mult', 2.0)),
                    'sl_mult': float(row.get('sl_mult', 1.0)),
                    'spike_threshold': float(row.get('spike_threshold', 3.0)),
                })
        except Exception as e:
            print(f"  [{self.symbol}] Read error: {e}")
        
        return trades
    
    def process_trade(self, trade: dict):
        """Process a single trade through the brain."""
        if trade['action'] == 'OPEN':
            # Store entry context
            self._pending_entry = {
                'entry_price': trade['entry'],
                'direction': 1 if trade['direction'] == 'LONG' else -1,
                'time': trade['time'],
            }
        
        elif trade['action'] == 'CLOSE' and self._pending_entry:
            # Create trade outcome for brain analysis
            ctx = MarketContext()
            ctx.price = self._pending_entry['entry_price']
            ctx.conviction = 0.6  # Default
            
            outcome = TradeOutcome(
                trade_id=self._total_trades + 1,
                entry_price=self._pending_entry['entry_price'],
                exit_price=trade['exit'],
                pnl_pct=trade['pnl_pct'] / 100,
                pnl_dollars=trade['pnl'],
                direction=self._pending_entry['direction'],
                bars_held=trade['bars_held'],
                exit_reason=trade['reason'],
                context=ctx,
            )
            
            # Brain analysis
            enriched = self.brain.analyze(outcome)
            
            # Update stats
            self._total_trades += 1
            self._total_pnl += trade['pnl']
            if trade['pnl'] > 0:
                self._wins += 1
            else:
                self._losses += 1
            
            # Print analysis
            wr = self._wins / self._total_trades * 100 if self._total_trades > 0 else 0
            emoji = "✅" if trade['pnl'] > 0 else "❌"
            print(f"  [{self.symbol}] {emoji} {trade['direction']} "
                  f"PnL=${trade['pnl']:+.4f} [{trade['reason']}] "
                  f"| {self._total_trades}T WR={wr:.1f}% "
                  f"PnL=${self._total_pnl:+.4f}")
            
            if enriched.diagnosis:
                print(f"  [{self.symbol}] [BRAIN] {enriched.category}: "
                      f"{enriched.diagnosis[:80]}")
            
            self._pending_entry = None
    
    def write_config(self, adjustments: dict):
        """Write config update for MQL5 EA to read."""
        config = {
            'updated_at': datetime.now().isoformat(),
            'total_trades': self._total_trades,
            'win_rate': self._wins / self._total_trades * 100 if self._total_trades > 0 else 0,
            'total_pnl': self._total_pnl,
            **adjustments,
        }
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"  [{self.symbol}] Config updated → {self.config_file}")
        except Exception as e:
            print(f"  [{self.symbol}] Write error: {e}")
    
    def analyze_and_adjust(self):
        """
        Analyze recent trades and write parameter adjustments.
        Called periodically by the bridge.
        """
        if self._total_trades < 5:
            return  # Need at least 5 trades
        
        # Get brain status
        status = self.brain.thinker.get_status()
        
        # Determine adjustments based on patterns
        adjustments = {}
        
        # If win rate is dropping, tighten TP
        wr = self._wins / self._total_trades * 100
        if wr < 60 and self._total_trades >= 10:
            adjustments['tp_mult'] = 1.5  # Tighter TP
            adjustments['cooldown'] = 3   # Longer cooldown
            print(f"  [{self.symbol}] [ADAPT] WR low ({wr:.1f}%) → tightening TP, adding cooldown")
        
        # If too many safety exits, reduce spike threshold
        # (be more selective about entries)
        if status.get('consecutive_losses', 0) >= 3:
            adjustments['cluster_min'] = 2  # More aggressive cluster filter
            print(f"  [{self.symbol}] [ADAPT] {status['consecutive_losses']} consecutive losses → tightening cluster filter")
        
        # If doing well, relax slightly for more trades
        if wr > 75 and self._total_trades >= 20:
            adjustments['tp_mult'] = 2.5  # Wider TP for bigger wins
            adjustments['cooldown'] = 1   # Shorter cooldown
            print(f"  [{self.symbol}] [ADAPT] WR good ({wr:.1f}%) → widening TP")
        
        if adjustments:
            self.write_config(adjustments)
    
    def get_status(self) -> dict:
        """Get current bridge status."""
        wr = self._wins / self._total_trades * 100 if self._total_trades > 0 else 0
        return {
            'symbol': self.symbol,
            'total_trades': self._total_trades,
            'wins': self._wins,
            'losses': self._losses,
            'win_rate': wr,
            'total_pnl': self._total_pnl,
            'processed_csv': self._processed_trades,
            'brain_status': self.brain.thinker.get_status(),
        }


def run_bridge(symbol: str, interval: int = 30):
    """Run the bridge, checking for new trades every N seconds."""
    bridge = MQL5Bridge(symbol)
    
    print(f"\n  {'='*60}")
    print(f"  MQL5 BRIDGE — {symbol}")
    print(f"  Checking for new trades every {interval}s")
    print(f"  Press Ctrl+C to stop")
    print(f"  {'='*60}\n")
    
    try:
        while True:
            # Read new trades
            trades = bridge.read_trades()
            
            for trade in trades:
                bridge.process_trade(trade)
            
            # Analyze and adjust periodically
            if bridge._total_trades > 0 and bridge._total_trades % 10 == 0:
                bridge.analyze_and_adjust()
            
            # Print status every 60 seconds
            if int(time.time()) % 60 < interval:
                status = bridge.get_status()
                print(f"\n  [{symbol}] STATUS: {status['total_trades']} trades | "
                      f"WR={status['win_rate']:.1f}% | "
                      f"PnL=${status['total_pnl']:+.4f}")
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print(f"\n  [{symbol}] Bridge stopped")
        bridge.brain._save_state()


def main():
    parser = argparse.ArgumentParser(description="MQL5 ↔ Python Brain Bridge")
    parser.add_argument("--symbol", default="CRASH300", 
                        help="Symbol code (CRASH300, BOOM500, etc.)")
    parser.add_argument("--interval", type=int, default=30,
                        help="Check interval in seconds (default: 30)")
    args = parser.parse_args()
    
    run_bridge(args.symbol.upper(), args.interval)


if __name__ == "__main__":
    main()
