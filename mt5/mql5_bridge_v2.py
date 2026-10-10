"""
mt5/mql5_bridge_v2.py — Bridge between SpikeCatcher v2 EA and Python Brain

Connects:
  SpikeCatcher v2 (MQL5) → writes CSV → Python Brain → writes config → MQL5

Key improvement: tracks safety exits separately to measure their impact.

Usage:
    python mt5/mql5_bridge_v2.py --symbol CRASH300
    python mt5/mql5_bridge_v2.py --symbol BOOM500 --interval 30
"""

import sys
import csv
import json
import time
import argparse
from pathlib import Path
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).parent.parent))

from model.strategy_brain import StrategyBrain
from model.trade_thinker import MarketContext, TradeOutcome


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


class MQL5BridgeV2:
    """
    Bridge between SpikeCatcher v2 EA and Python Brain.
    
    Tracks:
      - Normal exits (tp, sl, timeout)
      - Safety exits (spike detected, closed immediately)
      - Win rate by exit type
      - Parameter effectiveness
    """
    
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.mt5_symbol = SYMBOL_MAP.get(symbol, symbol)
        
        self.log_file = CONFIG_DIR / f"trades_{self.mt5_symbol}.csv"
        self.config_file = CONFIG_DIR / f"config_{self.mt5_symbol}.json"
        
        self.brain = StrategyBrain(symbol)
        
        self._processed_trades = 0
        self._pending_entry = None
        
        # Detailed stats
        self._stats = {
            'total': 0,
            'wins': 0,
            'losses': 0,
            'pnl': 0.0,
            'by_reason': defaultdict(lambda: {'count': 0, 'wins': 0, 'pnl': 0.0}),
            'safety_exits': 0,
            'safety_saved': 0.0,  # Estimated damage avoided
            'spike_hits': 0,
            'spike_damage': 0.0,
            'consecutive_losses': 0,  # Current losing streak
            'max_consecutive_losses': 0,  # Worst streak
            'last_pnl': 0.0,  # Last trade PnL
        }
        
        print(f"  [{symbol}] Bridge v2 initialized")
        print(f"  [{symbol}] Log: {self.log_file}")
        print(f"  [{symbol}] Config: {self.config_file}")
    
    def read_trades(self) -> list:
        """Read new trades from MQL5 CSV."""
        if not self.log_file.exists():
            return []
        
        trades = []
        try:
            with open(self.log_file, 'r') as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            
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
                    'reason': row.get('reason', ''),
                    'bars_held': int(row.get('bars_held', 0)),
                    'equity': float(row.get('equity', 0)),
                    'tp_mult': float(row.get('tp_mult', 2.0)),
                    'sl_mult': float(row.get('sl_mult', 1.0)),
                })
        except Exception as e:
            print(f"  [{self.symbol}] Read error: {e}")
        
        return trades
    
    def process_trade(self, trade: dict):
        """Process a single trade."""
        if trade['action'] == 'OPEN':
            self._pending_entry = {
                'entry_price': trade['entry'],
                'direction': 1 if trade['direction'] == 'LONG' else -1,
                'time': trade['time'],
                'tp_mult': trade.get('tp_mult', 2.0),
                'sl_mult': trade.get('sl_mult', 1.0),
            }
        
        elif trade['action'] == 'CLOSE' and self._pending_entry:
            reason = trade['reason']
            pnl = trade['pnl']
            
            # Update stats
            self._stats['total'] += 1
            self._stats['pnl'] += pnl
            self._stats['by_reason'][reason]['count'] += 1
            self._stats['by_reason'][reason]['pnl'] += pnl
            
            if pnl > 0:
                self._stats['wins'] += 1
                self._stats['by_reason'][reason]['wins'] += 1
                self._stats['consecutive_losses'] = 0  # Reset streak
            else:
                self._stats['losses'] += 1
                self._stats['consecutive_losses'] += 1
                self._stats['max_consecutive_losses'] = max(
                    self._stats['max_consecutive_losses'],
                    self._stats['consecutive_losses']
                )
            
            self._stats['last_pnl'] = pnl
            
            # Track safety exits specifically
            if reason == 'safety_spike':
                self._stats['safety_exits'] += 1
                # Estimate damage saved: without safety, full spike (5-18x SL)
                # With safety, tick 1 only (~65% of spike)
                # Saved: ~35% of what full spike would have been
                estimated_full_damage = abs(pnl) * 2.5  # Estimate full spike damage
                self._stats['safety_saved'] += estimated_full_damage * 0.35
                self._stats['spike_hits'] += 1
                self._stats['spike_damage'] += abs(pnl)
            
            # Brain analysis
            ctx = MarketContext()
            ctx.price = self._pending_entry['entry_price']
            ctx.conviction = 0.6
            
            outcome = TradeOutcome(
                trade_id=self._stats['total'],
                entry_price=self._pending_entry['entry_price'],
                exit_price=trade['exit'],
                pnl_pct=trade.get('pnl', 0) / max(trade.get('equity', 10000), 1) * 100,
                pnl_dollars=pnl,
                direction=self._pending_entry['direction'],
                bars_held=trade['bars_held'],
                exit_reason=reason,
                context=ctx,
            )
            
            enriched = self.brain.analyze(outcome)
            
            # Print
            emoji = "✅" if pnl > 0 else "❌"
            wr = self._stats['wins'] / self._stats['total'] * 100 if self._stats['total'] > 0 else 0
            print(f"  [{self.symbol}] {emoji} {trade['direction']} "
                  f"PnL=${pnl:+.4f} [{reason}] "
                  f"| {self._stats['total']}T WR={wr:.1f}% "
                  f"PnL=${self._stats['pnl']:+.4f}")
            
            if enriched.diagnosis:
                print(f"  [{self.symbol}] [BRAIN] {enriched.category}: "
                      f"{enriched.diagnosis[:80]}")
            
            self._pending_entry = None
    
    def write_config(self, adjustments: dict):
        """Write config update for MQL5 EA.
        
        MQL5 parser only handles flat key:value pairs.
        No nested objects or arrays.
        """
        config = {
            'updated_at': datetime.now().isoformat(),
            'version': 'v2',
            'total_trades': self._stats['total'],
            'win_rate': self._stats['wins'] / self._stats['total'] * 100 if self._stats['total'] > 0 else 0,
            'total_pnl': self._stats['pnl'],
            'safety_exits': self._stats['safety_exits'],
            'safety_saved': self._stats['safety_saved'],
            'consecutive_losses': self._stats['consecutive_losses'],
            'max_consecutive_losses': self._stats['max_consecutive_losses'],
        }
        
        # Add adjustments (only flat key:value pairs)
        for k, v in adjustments.items():
            if isinstance(v, (int, float, str, bool)):
                config[k] = v
        
        try:
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
            print(f"  [{self.symbol}] Config updated")
        except Exception as e:
            print(f"  [{self.symbol}] Write error: {e}")
    
    def analyze_and_adjust(self):
        """Analyze patterns and write adjustments."""
        if self._stats['total'] < 10:
            return
        
        adjustments = {}
        total = self._stats['total']
        wr = self._stats['wins'] / total * 100
        
        # Safety exit effectiveness
        safety = self._stats['safety_exits']
        saved = self._stats['safety_saved']
        
        if safety > 0:
            print(f"  [{self.symbol}] SAFETY EXIT STATS:")
            print(f"    Triggered: {safety} times")
            print(f"    Est. saved: ${saved:.4f}")
            print(f"    Avg damage per hit: ${self._stats['spike_damage']/safety:.4f}")
        
        # Adjust based on performance
        if wr < 65:
            adjustments['tp_mult'] = 1.8  # Tighter TP
            adjustments['cooldown'] = 3
            print(f"  [{self.symbol}] [ADAPT] WR low ({wr:.1f}%) → tighter TP")
        
        if self._stats.get('consecutive_losses', 0) >= 3:
            adjustments['cluster_min'] = 2
            print(f"  [{self.symbol}] [ADAPT] Losing streak → stricter cluster filter")
        
        if wr > 80 and total >= 20:
            adjustments['tp_mult'] = 2.5  # Wider TP for more profit
            print(f"  [{self.symbol}] [ADAPT] WR good ({wr:.1f}%) → wider TP")
        
        if adjustments:
            self.write_config(adjustments)
    
    def print_summary(self):
        """Print detailed summary."""
        total = self._stats['total']
        if total == 0:
            print(f"  [{self.symbol}] No trades yet")
            return
        
        wr = self._stats['wins'] / total * 100
        safety = self._stats['safety_exits']
        
        print(f"\n  {'='*60}")
        print(f"  SPIKE CATCHER v2 — {self.symbol} SUMMARY")
        print(f"  {'='*60}")
        print(f"  Total Trades:  {total}")
        print(f"  Win Rate:      {wr:.1f}%")
        print(f"  Total PnL:     ${self._stats['pnl']:+.4f}")
        print(f"  Safety Exits:  {safety} ({safety/total*100:.1f}% of trades)")
        print(f"  Est. Saved:    ${self._stats['safety_saved']:.4f}")
        print(f"  Spike Hits:    {self._stats['spike_hits']}")
        print(f"  Avg Spike Dmg: ${self._stats['spike_damage']/max(safety,1):.4f}")
        print(f"  {'─'*60}")
        print(f"  Exit Breakdown:")
        for reason, data in sorted(self._stats['by_reason'].items(), 
                                   key=lambda x: -x[1]['count']):
            r_wr = data['wins'] / data['count'] * 100 if data['count'] > 0 else 0
            print(f"    {reason:20s}: {data['count']:4d} trades, "
                  f"WR={r_wr:.0f}%, PnL=${data['pnl']:+.4f}")
        print(f"  {'='*60}\n")
    
    def get_status(self) -> dict:
        """Get current status."""
        total = self._stats['total']
        return {
            'symbol': self.symbol,
            'total_trades': total,
            'win_rate': self._stats['wins'] / total * 100 if total > 0 else 0,
            'total_pnl': self._stats['pnl'],
            'safety_exits': self._stats['safety_exits'],
            'safety_saved': self._stats['safety_saved'],
            'by_reason': dict(self._stats['by_reason']),
        }


def run_bridge(symbol: str, interval: int = 30):
    """Run the bridge."""
    bridge = MQL5BridgeV2(symbol)
    
    print(f"\n  {'='*60}")
    print(f"  SPIKE CATCHER v2 BRIDGE — {symbol}")
    print(f"  Checking every {interval}s")
    print(f"  Press Ctrl+C to stop")
    print(f"  {'='*60}\n")
    
    last_analyze = 0
    
    try:
        while True:
            trades = bridge.read_trades()
            
            for trade in trades:
                bridge.process_trade(trade)
            
            # Analyze every 60 seconds
            now = time.time()
            if now - last_analyze >= 60 and bridge._stats['total'] > 0:
                bridge.analyze_and_adjust()
                bridge.print_summary()
                last_analyze = now
            
            time.sleep(interval)
    
    except KeyboardInterrupt:
        print(f"\n  [{symbol}] Bridge stopped")
        bridge.print_summary()
        bridge.brain._save_state()


def main():
    parser = argparse.ArgumentParser(description="SpikeCatcher v2 Bridge")
    parser.add_argument("--symbol", default="CRASH300")
    parser.add_argument("--interval", type=int, default=30)
    args = parser.parse_args()
    
    run_bridge(args.symbol.upper(), args.interval)


if __name__ == "__main__":
    main()
