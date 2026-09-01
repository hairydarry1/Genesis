"""
eval/live_deploy.py — Dual-Strategy Live Deployment

Runs SMC Spike Hunter + Drift Fade simultaneously on the same account.
Both strategies share equity and risk budget.

Usage:
    python eval/live_deploy.py --symbol BOOM500 --equity 200
    python eval/live_deploy.py --symbol BOOM500,CRASH500 --equity 200
    python eval/live_deploy.py --symbol BOOM500 --risk 0.02 --smc-share 0.6

Strategy Split:
    - SMC (60% of risk budget): H1 zones + RSI extreme + EMA cross
    - Drift (40% of risk budget): Counter-drift entry 3 bars after spike
"""

import asyncio
import argparse
import json
import signal
import sys
import time
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass, field

sys.path.insert(0, str(Path(__file__).parent.parent))

from eval.smc_spike_hunter import (
    SMCSpikeHunter, SMCHunterConfig, load_candles,
    detect_order_blocks, OrderBlock, compute_rsi,
)
from model.strategy_brain import StrategyBrain, BrainDecision
from model.trade_thinker import MarketContext, TradeOutcome

# ─── Deriv WebSocket ──────────────────────────────────────────────────────────
DERIV_APP_ID = 1089
DERIV_WS_URL = f"wss://api.derivws.com/trading/v1/options/ws/public?app_id={DERIV_APP_ID}"

SYMBOL_MAP = {
    "BOOM500": "Boom 500 Index",
    "CRASH500": "Crash 500 Index",
    "BOOM1000": "Boom 1000 Index",
    "CRASH1000": "Crash 1000 Index",
}


# ─── Drift Fade Engine (Live) ────────────────────────────────────────────────
class DriftFadeLive:
    """
    Live Drift Fade strategy.
    
    Logic:
      - Detect spike on M1 bars (range > 3x median)
      - Wait N bars (default: 3)
      - Enter counter-drift position
      - Exit: TP, SL, spike_against, or timeout
    
    Config from backtest optimization:
      BOOM: entry_bar=3, SL=2pts, TP=3pts, max_hold=9
      CRASH: entry_bar=3, SL=2pts, TP=3pts, max_hold=9
    """
    
    def __init__(self, symbol: str, risk_share: float = 0.4,
                 entry_bar: int = 3, sl_pts: float = 1.0, tp_pts: float = 3.0,
                 max_hold: int = 9, spike_threshold: float = 3.0):
        self.symbol = symbol
        self.is_boom = "boom" in symbol.lower()
        self.dollar_per_point = 0.10 if "1000" in symbol else 0.20
        
        # Config
        self.entry_bar = entry_bar
        self.sl_pts = sl_pts
        self.tp_pts = tp_pts
        self.max_hold = max_hold
        self.spike_threshold = spike_threshold
        self.risk_share = risk_share
        
        # State
        self.position = 0       # 0=flat, 1=long, -1=short
        self.entry_price = 0.0
        self.entry_idx = 0
        self.bars_held = 0
        self.last_spike_idx = -999
        self.position_size = 0.0
        
        # Bars buffer (circular, keep last 50)
        self._bars = []
        self._ranges = []
        self._median_range = 1.0
        self._spike_set = set()
        self._spike_indices = []  # Track spike indices for cluster detection
        self._spike_cluster = set()  # Spikes in clusters of 3+ within 10 bars
        
        # Trade tracking
        self.trade_count = 0
        self.wins = 0
        self.losses = 0
        self.total_pnl = 0.0
        self.peak_equity = 0.0
        self.trade_log = []
    
    def on_bar(self, bar: dict, idx: int, equity: float, risk_pct: float) -> dict | None:
        """Process a new M1 bar."""
        o, h, l, c = bar['open'], bar['high'], bar['low'], bar['close']
        bar_range = h - l
        
        # Track range for spike detection
        self._bars.append(bar)
        self._ranges.append(bar_range)
        if len(self._ranges) > 100:
            self._ranges.pop(0)
        self._median_range = float(np.median(self._ranges)) if self._ranges else 1.0
        
        # Detect spike
        is_spike = bar_range > self.spike_threshold * self._median_range
        is_boom_spike = is_spike and c > o
        is_crash_spike = is_spike and c < o
        
        if (self.is_boom and is_boom_spike) or (not self.is_boom and is_crash_spike):
            self.last_spike_idx = idx
            self._spike_set.add(idx)
            self._spike_indices.append(idx)
            # Update cluster detection: spikes in groups of 3+ within 10 bars
            nearby = sum(1 for s in self._spike_indices if abs(s - idx) <= 10 and s != idx)
            if nearby >= 2:
                self._spike_cluster.add(idx)
                # Mark nearby spikes as clustered too
                for s in self._spike_indices:
                    if abs(s - idx) <= 10 and s != idx:
                        self._spike_cluster.add(s)
        
        # Position management
        if self.position != 0:
            self.bars_held += 1
            result = self._check_exit(h, l, c, idx)
            if result:
                return result
            return None
        
        # Entry: wait entry_bar bars after spike, then counter-drift entry
        bars_since_spike = idx - self.last_spike_idx if self.last_spike_idx >= 0 else 999
        if bars_since_spike == self.entry_bar and self.last_spike_idx >= 0:
            # FILTER: Skip clustered spikes (3+ spikes within 10 bars)
            if self.last_spike_idx in self._spike_cluster:
                return None  # Skip this entry
            
            # ── Classification-based filters (learned from post-mortem) ──
            # 1. Skip late entries (entering during cluster = 0% WR)
            cluster_nearby = sum(1 for s in self._spike_indices if abs(s - idx) <= 10)
            if bars_since_spike <= 4 and cluster_nearby >= 2:
                return None  # Late entry — entering tail of cluster
            
            # 2. Skip algo spike traps (spikes >12x median hold gains)
            if self._median_range > 0 and bar_range / self._median_range > 12:
                return None  # Algorithmic spike — won't retrace
            
            # 3. Skip cluster continuation (3+ spikes = trend, not noise)
            if cluster_nearby >= 3:
                return None  # Cluster continuation — wrong side of trend
            # Counter-drift: BOOM spikes UP then drifts DOWN → SHORT the drift
            # CRASH spikes DOWN then drifts UP → LONG the drift
            self.position = -1 if self.is_boom else 1
            self.entry_price = c
            self.entry_idx = idx
            self.bars_held = 0
            
            # Position sizing
            risk_dollars = equity * risk_pct * self.risk_share
            if self.sl_pts > 0:
                self.position_size = risk_dollars / (self.sl_pts * self.dollar_per_point)
            else:
                self.position_size = 0
            
            return {
                "action": "open", "strategy": "drift",
                "side": "long" if self.position == 1 else "short",
                "entry_price": c,
                "position_size": self.position_size,
            }
        
        return None
    
    def _check_exit(self, h: float, l: float, c: float, idx: int) -> dict | None:
        """Check exit conditions."""
        entry = self.entry_price
        
        if self.position == 1:  # Long
            hit_tp = (h - entry) >= self.tp_pts
            hit_sl = (l - entry) <= -self.sl_pts
        else:  # Short
            hit_tp = (entry - l) >= self.tp_pts
            hit_sl = (entry - h) <= -self.sl_pts
        
        spike_against = idx in self._spike_set and (
            (self.position == 1 and c < entry) or
            (self.position == -1 and c > entry)
        )
        
        exit_pts = None
        reason = None
        
        if hit_tp:
            exit_pts = self.tp_pts
            reason = "tp"
        elif hit_sl:
            exit_pts = -self.sl_pts
            reason = "sl"
        elif spike_against:
            unrealized = (c - entry) if self.position == 1 else (entry - c)
            exit_pts = unrealized
            reason = "spike"
        elif self.bars_held >= self.max_hold:
            unrealized = (c - entry) if self.position == 1 else (entry - c)
            exit_pts = unrealized
            reason = "timeout"
        
        if exit_pts is None:
            return None
        
        pnl_dollars = exit_pts * self.dollar_per_point * self.position_size
        self.total_pnl += pnl_dollars
        self.trade_count += 1
        if pnl_dollars > 0:
            self.wins += 1
        else:
            self.losses += 1
        
        self.trade_log.append({
            "strategy": "drift",
            "entry": entry, "exit": c,
            "pnl": pnl_dollars, "reason": reason,
            "bars_held": self.bars_held,
        })
        
        self.position = 0
        self.bars_held = 0
        
        return {
            "action": "close", "strategy": "drift",
            "exit_price": c, "exit_reason": reason, "pnl": pnl_dollars,
        }


# ─── Dual Strategy Trader ────────────────────────────────────────────────────
class DualStrategyTrader:
    """
    Runs SMC + Drift Fade on the same symbol, sharing equity.
    
    Risk budget split:
      - SMC gets smc_share * risk_per_trade
      - Drift gets (1 - smc_share) * risk_per_trade
    """
    
    def __init__(self, symbol: str, equity: float = 100.0,
                 risk_pct: float = 0.02, smc_share: float = 0.6,
                 rsi_low: float = 15.0, rsi_high: float = 85.0,
                 sl_pct: float = 0.005):
        self.symbol = symbol
        self.mt5_symbol = SYMBOL_MAP.get(symbol, symbol)
        self.initial_equity = equity
        self.equity = equity
        self.risk_pct = risk_pct
        self.smc_share = smc_share
        self.peak_equity = equity
        
        # ── SMC Strategy ──
        print(f"  [{symbol}] Loading SMC H1 zones...")
        try:
            h1_data = load_candles(symbol, "60min")
            zone_mult = 2.0 if "crash" in symbol.lower() else 2.5
            self.zones = detect_order_blocks(h1_data, threshold_mult=zone_mult)
            print(f"  [{symbol}] Found {len(self.zones)} H1 order blocks")
        except FileNotFoundError:
            print(f"  [{symbol}] No H1 data — SMC will have no zones")
            self.zones = []
        
        cfg = SMCHunterConfig(
            sl_pct=sl_pct,
            risk_per_trade=risk_pct * smc_share,
            rsi_extreme_low=rsi_low,
            rsi_extreme_high=rsi_high,
        )
        self.smc = SMCSpikeHunter(symbol, config=cfg, initial_equity=equity)
        self.smc.set_h1_zones(self.zones)
        
        # ── Drift Fade Strategy ──
        self.drift = DriftFadeLive(
            symbol, risk_share=(1 - smc_share),
            entry_bar=3, sl_pts=1.0, tp_pts=3.0, max_hold=9,
        )
        
        # ── Shared State ──
        self._m1_bars = []
        self._current_bar = None
        self._tick_count = 0
        self._bar_count = 0
        self._last_zone_refresh = time.time()
        self._zone_refresh_interval = 8 * 3600
        self._running = True
        self._dashboard_url = "http://localhost:8080"
        self._last_dashboard_push = 0
        
        # ── Strategy Brain (thinking layer) ──
        self.brain = StrategyBrain(symbol)
        self._pending_context: MarketContext | None = None
        self._pending_entry_price: float = 0.0
        self._pending_direction: int = 0
        self._pending_strategy: str = ""
        self._trade_id_counter: int = 0
        
        # Combined trade log
        self._all_trades = []
        self._start_time = time.time()
    
    # ── Tick Processing ────────────────────────────────────────────────────
    
    def process_tick(self, tick: dict):
        """Process a live tick — aggregate into M1 bars."""
        price = float(tick.get("quote", 0))
        ts = tick.get("epoch", int(time.time()))
        
        if price <= 0:
            return
        
        self._tick_count += 1
        bar_minute = ts // 60
        
        if self._current_bar is None or bar_minute != self._current_bar.get("_minute"):
            if self._current_bar is not None and len(self._m1_bars) > 0:
                self._finalize_bar()
            
            self._current_bar = {
                "_minute": bar_minute,
                "epoch": bar_minute * 60,
                "open": price, "high": price,
                "low": price, "close": price,
            }
        else:
            self._current_bar["high"] = max(self._current_bar["high"], price)
            self._current_bar["low"] = min(self._current_bar["low"], price)
            self._current_bar["close"] = price
    
    def _finalize_bar(self):
        """Close bar, run both strategies, check for conflicts."""
        bar = {k: v for k, v in self._current_bar.items() if not k.startswith("_")}
        self._m1_bars.append(bar)
        self._bar_count += 1
        idx = len(self._m1_bars) - 1
        
        # Run SMC
        self.smc.precompute(self._m1_bars)
        smc_result = self.smc.on_bar(bar, idx)
        
        # Run Drift Fade (only if SMC didn't open a position)
        drift_result = None
        if self.smc.position == 0:
            drift_result = self.drift.on_bar(
                bar, idx, self.equity, self.risk_pct
            )
        
        # Handle results
        if smc_result:
            self._handle_result(smc_result)
        if drift_result:
            self._handle_result(drift_result)
        
        # Sync equity
        self.equity = self.smc.equity
        self.peak_equity = max(self.peak_equity, self.equity)
        
        # Dashboard push
        self._push_dashboard()
        
        # Zone refresh
        self._refresh_zones()
        
        # Status display every 10 bars
        if self._bar_count % 10 == 0:
            self._display_status()
    
    # ── Result Handling ────────────────────────────────────────────────────
    
    def _handle_result(self, result: dict):
        """Handle trade open/close from either strategy."""
        action = result.get("action")
        strategy = result.get("strategy", "smc")
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        
        if action == "open":
            side = result.get("side", "?").upper()
            entry = result.get("entry_price", 0)
            tag = "[SMC]" if strategy == "smc" else "[DRF]"
            print(f"\n  [{ts}] {tag} {self.symbol} OPEN {side} @ {entry:.2f}")
            
            # Capture context for brain analysis
            ctx = MarketContext()
            if self._m1_bars:
                ctx.price = self._m1_bars[-1]["close"]
                # Compute drift from recent bars
                if len(self._m1_bars) >= 20:
                    closes = [b["close"] for b in self._m1_bars[-20:]]
                    returns = np.diff(closes) / closes[:-1]
                    ctx.drift_direction = "up" if np.sum(returns) > 0 else "down"
                    ctx.drift_strength = float(abs(np.sum(returns)))
                    ctx.current_vol = float(np.median(np.abs(returns)))
                # Spike info from drift engine
                if strategy == "drift" and hasattr(self.drift, '_median_range'):
                    ctx.spike_size_vs_median = self.drift.spike_threshold
                    ctx.cluster_size = len([s for s in self.drift._spike_indices 
                                           if abs(s - len(self._m1_bars)) <= 10])
                    ctx.is_isolated_spike = ctx.cluster_size <= 1
            ctx.session_hour = datetime.now(timezone.utc).hour
            ctx.conviction = 0.5  # Default
            self._pending_context = ctx
            self._pending_entry_price = entry
            self._pending_direction = 1 if side == "LONG" else -1
            self._pending_strategy = strategy
        
        elif action == "close":
            pnl = result.get("pnl", 0)
            reason = result.get("exit_reason", "?")
            exit_p = result.get("exit_price", 0)
            tag = "[SMC]" if strategy == "smc" else "[DRF]"
            emoji = "✅" if pnl > 0 else "❌"
            print(f"  [{ts}] {tag} {self.symbol} CLOSE {exit_p:.2f} "
                  f"| {emoji} PnL=${pnl:+.4f} | {reason}")
            
            # Brain analysis
            self._trade_id_counter += 1
            if self._pending_context is not None:
                outcome = TradeOutcome(
                    trade_id=self._trade_id_counter,
                    entry_price=self._pending_entry_price,
                    exit_price=exit_p,
                    pnl_pct=pnl / max(self.equity, 1) * 100,
                    pnl_dollars=pnl,
                    direction=self._pending_direction,
                    bars_held=result.get("bars_held", 0),
                    exit_reason=reason,
                    context=self._pending_context,
                )
                enriched = self.brain.analyze(outcome)
                if enriched.diagnosis:
                    print(f"  [{ts}] [BRAIN] {enriched.category}: {enriched.diagnosis[:80]}")
                self._pending_context = None
            
            self._all_trades.append({
                "time": ts, "symbol": self.symbol,
                "strategy": strategy, **result,
            })
    
    # ── Zone Refresh ───────────────────────────────────────────────────────
    
    def _refresh_zones(self):
        now = time.time()
        if now - self._last_zone_refresh < self._zone_refresh_interval:
            return
        self._last_zone_refresh = now
        try:
            h1_data = load_candles(self.symbol, "60min")
            zone_mult = 2.0 if "crash" in self.symbol.lower() else 2.5
            new_zones = detect_order_blocks(h1_data, threshold_mult=zone_mult)
            self.zones = new_zones
            self.smc.set_h1_zones(new_zones)
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n  [{ts}] [{self.symbol}] Zones refreshed → {len(new_zones)}")
        except Exception as e:
            ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"\n  [{ts}] [{self.symbol}] Zone refresh failed: {e}")
    
    # ── Dashboard ──────────────────────────────────────────────────────────
    
    def _push_dashboard(self):
        import urllib.request
        now = time.time()
        if now - self._last_dashboard_push < 2.0:
            return
        self._last_dashboard_push = now
        
        try:
            price = self._m1_bars[-1]["close"] if self._m1_bars else 0
            smc_n = len(self.smc.trade_log)
            drift_n = len(self.drift.trade_log)
            smc_wins = sum(1 for t in self.smc.trade_log if t.get("pnl", 0) > 0)
            drift_wins = self.drift.wins
            
            brain_data = self.brain.get_dashboard_data()
            payload = {
                "symbol": self.symbol,
                "price": price,
                "equity": self.equity,
                "smc": {
                    "position": self.smc.position,
                    "trades": smc_n,
                    "win_rate": (smc_wins / smc_n * 100) if smc_n > 0 else 0,
                },
                "drift": {
                    "position": self.drift.position,
                    "trades": drift_n,
                    "win_rate": (drift_wins / drift_n * 100) if drift_n > 0 else 0,
                },
                "total_trades": smc_n + drift_n,
                "uptime_min": (time.time() - self._start_time) / 60,
                "brain": brain_data,
            }
            req = urllib.request.Request(
                f"{self._dashboard_url}/api/state",
                data=json.dumps(payload).encode(),
                headers={"Content-Type": "application/json"},
            )
            urllib.request.urlopen(req, timeout=2)
        except Exception:
            pass
    
    # ── Status Display ─────────────────────────────────────────────────────
    
    def _display_status(self):
        ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
        price = self._m1_bars[-1]["close"] if self._m1_bars else 0
        elapsed = (time.time() - self._start_time) / 60
        
        smc_n = len(self.smc.trade_log)
        drift_n = len(self.drift.wins + self.drift.losses)
        smc_wins = sum(1 for t in self.smc.trade_log if t.get("pnl", 0) > 0)
        drift_wins = self.drift.wins
        
        smc_pnl = sum(t.get("pnl", 0) for t in self.smc.trade_log)
        drift_pnl = self.drift.total_pnl
        
        # Position status
        smc_pos = "FLAT"
        if self.smc.position == 1: smc_pos = "LONG"
        elif self.smc.position == -1: smc_pos = "SHORT"
        
        drift_pos = "FLAT"
        if self.drift.position == 1: drift_pos = "LONG"
        elif self.drift.position == -1: drift_pos = "SHORT"
        
        rsi_str = "---"
        if len(self.smc._rsi_arr) > 0:
            rsi_str = f"{self.smc._rsi_arr[-1]:.1f}"
        
        print(f"\n  {'='*64}")
        print(f"  {self.symbol} DUAL STRATEGY @ {ts}  ({elapsed:.0f} min)")
        print(f"  {'='*64}")
        print(f"  Price: {price:.2f}  |  Equity: ${self.equity:,.4f}  "
              f"|  DD: {(1 - self.equity/self.peak_equity)*100:.2f}%")
        print(f"  RSI: {rsi_str}  |  Zones: {len(self.zones)}  "
              f"|  Bars: {self._bar_count}  |  Ticks: {self._tick_count}")
        print(f"  {'─'*64}")
        print(f"  {'Strategy':<10} {'Pos':<8} {'Trades':>6} {'WR%':>6} {'PnL':>10}")
        print(f"  {'─'*64}")
        print(f"  {'SMC':<10} {smc_pos:<8} {smc_n:>6} "
              f"{(smc_wins/smc_n*100 if smc_n else 0):>5.1f}% ${smc_pnl:>+9.4f}")
        print(f"  {'Drift':<10} {drift_pos:<8} {drift_n:>6} "
              f"{(drift_wins/drift_n*100 if drift_n else 0):>5.1f}% ${drift_pnl:>+9.4f}")
        print(f"  {'─'*64}")
        print(f"  {'TOTAL':<10} {'':8} {smc_n+drift_n:>6} "
              f"{'':>6} ${smc_pnl+drift_pnl:>+9.4f}")
        print(f"  {'='*64}")

    def save_trades(self):
        """Save trade log and brain state."""
        out_dir = Path(__file__).parent.parent / "checkpoints_tiny"
        out_dir.mkdir(exist_ok=True)
        out_file = out_dir / f"deploy_trades_{self.symbol}.json"

        with open(out_file, "w") as f:
            json.dump(self._all_trades, f, indent=2, default=str)
        print(f"  [{self.symbol}] Saved {len(self._all_trades)} trades → {out_file}")
        
        # Save brain state
        self.brain._save_state()
        self.brain.print_summary()


# ─── WebSocket Runner ────────────────────────────────────────────────────────

async def run_trader(trader: DualStrategyTrader):
    """Run a single symbol connected to Deriv WebSocket."""
    import websockets
    
    sym = trader.symbol
    print(f"\n  [{sym}] Connecting to Deriv WebSocket...")
    
    async with websockets.connect(DERIV_WS_URL) as ws:
        subscribe_msg = {"ticks": sym, "subscribe": 1}
        await ws.send(json.dumps(subscribe_msg))
        
        response = await asyncio.wait_for(ws.recv(), timeout=10)
        resp = json.loads(response)
        if resp.get("error"):
            print(f"  [{sym}] Error: {resp['error']}")
            return
        
        print(f"  [{sym}] ✅ Connected! Streaming live ticks...")
        print(f"  [{sym}] SMC risk: {trader.smc_share*100:.0f}%  "
              f"Drift risk: {(1-trader.smc_share)*100:.0f}%  "
              f"Total risk: {trader.risk_pct*100:.0f}%/trade")
        
        async for message in ws:
            if not trader._running:
                break
            try:
                data = json.loads(message)
                if "tick" in data:
                    trader.process_tick(data["tick"])
            except json.JSONDecodeError:
                continue
            except Exception as e:
                print(f"  [{sym}] Error: {e}")
                continue


async def run_multi(traders: list[DualStrategyTrader]):
    """Run multiple symbols simultaneously."""
    tasks = [run_trader(t) for t in traders]
    
    symbols = ", ".join(t.symbol for t in traders)
    print(f"\n  Starting {len(traders)} dual-strategy traders: {symbols}")
    print(f"  Press Ctrl+C to stop\n")
    
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("\n  Shutting down...")
    finally:
        for t in traders:
            t._running = False
            t.save_trades()


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Dual-Strategy Live Deployment (SMC + Drift Fade)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python eval/live_deploy.py --symbol BOOM500 --equity 200
  python eval/live_deploy.py --symbol BOOM500,CRASH500 --equity 200 --risk 0.02
  python eval/live_deploy.py --symbol BOOM1000 --equity 500 --smc-share 0.7
        """,
    )
    parser.add_argument("--symbol", default="BOOM500",
                        help="Symbol(s), comma-separated (default: BOOM500)")
    parser.add_argument("--equity", type=float, default=100.0,
                        help="Starting equity per symbol in USD (default: 100)")
    parser.add_argument("--risk", type=float, default=0.02,
                        help="Total risk per trade as fraction (default: 0.02)")
    parser.add_argument("--smc-share", type=float, default=0.6,
                        help="Fraction of risk budget for SMC (default: 0.6)")
    parser.add_argument("--rsi-low", type=float, default=15.0,
                        help="RSI extreme low for Boom (default: 15)")
    parser.add_argument("--rsi-high", type=float, default=85.0,
                        help="RSI extreme high for Crash (default: 85)")
    parser.add_argument("--sl", type=float, default=0.005,
                        help="SMC stop loss pct (default: 0.005)")
    args = parser.parse_args()
    
    symbols = [s.strip().upper() for s in args.symbol.split(",")]
    
    print("\n" + "=" * 64)
    print("  DUAL-STRATEGY LIVE DEPLOYMENT")
    print("  SMC Spike Hunter + Drift Fade")
    print("=" * 64)
    print(f"  Symbols:     {', '.join(symbols)}")
    print(f"  Equity:      ${args.equity:,.2f} per symbol")
    print(f"  Risk:        {args.risk*100:.1f}% per trade")
    print(f"  SMC share:   {args.smc_share*100:.0f}%")
    print(f"  Drift share: {(1-args.smc_share)*100:.0f}%")
    print(f"  RSI bounds:  {args.rsi_low:.0f} / {args.rsi_high:.0f}")
    print(f"  SL (SMC):    {args.sl*100:.1f}%")
    print("=" * 64)
    
    traders = []
    for sym in symbols:
        t = DualStrategyTrader(
            sym,
            equity=args.equity,
            risk_pct=args.risk,
            smc_share=args.smc_share,
            rsi_low=args.rsi_low,
            rsi_high=args.rsi_high,
            sl_pct=args.sl,
        )
        traders.append(t)
    
    # Handle Ctrl+C gracefully
    def shutdown(sig, frame):
        print("\n\n  ⚡ Shutdown signal received...")
        for t in traders:
            t._running = False
            t.save_trades()
        sys.exit(0)
    
    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    
    asyncio.run(run_multi(traders))


if __name__ == "__main__":
    main()
