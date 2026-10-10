"""
model/trade_thinker.py — Trade Thinking Layer

Captures rich market context at entry and reasons about WHY each trade
won or lost, then uses those insights to adapt strategy in real-time.

NOT just "price went against us" but:
  - "We faded a trending move — drift was against our direction"
  - "Spike was algorithmic (20x+ median), not noise — these don't retrace"
  - "Volatility collapsed after entry, removing retrace fuel"
  - "Cluster was actually a trend continuation, not exhaustion"
  - "We entered too early — the cluster wasn't fully done"
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np


# ---------------------------------------------------------------------------
# Market Context — captured at every trade entry
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Rich snapshot of market state at the moment of trade entry."""
    # Price context
    price: float = 0.0
    spike_price: float = 0.0          # Price at the spike we're fading
    spike_size_pct: float = 0.0       # Spike magnitude as % of price
    spike_size_vs_median: float = 0.0 # Spike size relative to median vol

    # Trend context
    drift_direction: str = "flat"     # up / down / flat — recent price drift
    drift_strength: float = 0.0       # 0-1, how strong the drift is
    trend_alignment: str = "none"     # "with_drift" / "against_drift" / "none"
    
    # Volatility context
    current_vol: float = 0.0          # Current volatility (rolling)
    vol_regime: str = "normal"        # "low" / "normal" / "high" / "extreme"
    vol_vs_baseline: float = 1.0      # Current vol / baseline vol

    # Cluster context
    cluster_size: int = 0             # Number of spikes in current cluster
    cluster_age_bars: int = 0         # Bars since cluster started
    is_isolated_spike: bool = False   # True if spike is not part of cluster
    
    # Spike classification
    is_algorithmic_spike: bool = False  # True if 20x+ median (real algo spike)
    is_noise_spike: bool = False        # True if <5x median (likely noise)
    
    # Timing context
    bars_since_last_spike: int = 0    # Gap since previous spike
    avg_gap_recent: float = 0.0       # Average gap in recent history
    
    # Market phase
    recent_win_rate: float = 0.0      # Recent fade win rate (last 20 trades)
    recent_avg_pnl: float = 0.0       # Recent average PnL per trade
    consecutive_losses: int = 0       # Current losing streak
    
    # Session context
    session_hour: int = 0             # UTC hour
    bars_in_session: int = 0          # How long we've been running
    
    # Conviction score (0-1)
    conviction: float = 0.0           # How confident we are in this trade

    def summary(self) -> str:
        """One-line human-readable summary of the context."""
        parts = []
        parts.append(f"drift={self.drift_direction}({self.drift_strength:.2f})")
        parts.append(f"vol={self.vol_regime}")
        parts.append(f"spike={self.spike_size_vs_median:.1f}x")
        if self.cluster_size > 0:
            parts.append(f"cluster={self.cluster_size}")
        if self.is_algorithmic_spike:
            parts.append("ALGO_SPIKE")
        parts.append(f"conviction={self.conviction:.2f}")
        return " | ".join(parts)


# ---------------------------------------------------------------------------
# Trade Outcome — captured at trade close
# ---------------------------------------------------------------------------

@dataclass
class TradeOutcome:
    """What happened with a trade and WHY."""
    trade_id: int = 0
    entry_price: float = 0.0
    exit_price: float = 0.0
    pnl_pct: float = 0.0
    pnl_dollars: float = 0.0
    direction: int = 0                # +1 long, -1 short
    bars_held: int = 0
    exit_reason: str = ""             # tp / sl / timeout

    # Context at entry (copy from MarketContext)
    context: MarketContext = field(default_factory=MarketContext)
    
    # Post-mortem reasoning
    diagnosis: str = ""               # WHY it won or lost
    category: str = ""                # classified outcome type
    fixable: bool = False             # Could our strategy have avoided this?
    suggested_fix: str = ""           # What to change

    def is_win(self) -> bool:
        return self.pnl_pct > 0


# ---------------------------------------------------------------------------
# Post-Mortem Analyzer — reasons about WHY trades won/lost
# ---------------------------------------------------------------------------

class PostMortemAnalyzer:
    """
    Analyzes each closed trade and generates a human-readable diagnosis
    of WHY it won or lost, going beyond surface-level observations.
    """

    # Outcome categories
    CATEGORIES = {
        "good_fade": "Correctly faded a noise spike that retracted",
        "lucky_timeout": "Didn't hit TP but ended profitable by timeout",
        "trend_against": "Faded against a strong drift/trend",
        "algo_spike_trap": "Faded an algorithmic spike (20x+) that held gains",
        "cluster_continuation": "Entered during an unfinished cluster",
        "vol_collapse": "Volatility dropped after entry, removing retrace",
        "overtrade": "Traded during a losing streak (consecutive losses)",
        "noise_fade_win": "Faded a small spike that happened to retrace",
        "late_entry": "Entered too late after cluster end",
        "good_timing": "Entered at optimal point after clean cluster end",
    }

    def __init__(self):
        self.history: list[TradeOutcome] = []
        self._recent_pnls: deque[float] = deque(maxlen=50)
        self._category_stats: dict[str, dict] = {}

    def analyze(self, outcome: TradeOutcome) -> TradeOutcome:
        """
        Analyze a closed trade and populate diagnosis, category, and fixes.
        Returns the enriched outcome.
        """
        ctx = outcome.context
        win = outcome.is_win()

        # ---- Core Diagnosis ----
        reasons = []
        category_parts = []

        # 1. Drift alignment
        if ctx.drift_direction != "flat":
            fade_dir = "down" if outcome.direction == -1 else "up"
            if ctx.drift_direction == fade_dir:
                reasons.append(f"Drift {ctx.drift_direction} aligned with our fade")
                category_parts.append("good_fade")
            else:
                reasons.append(f"Drift {ctx.drift_direction} AGAINST our fade (strength={ctx.drift_strength:.2f})")
                if ctx.drift_strength > 0.4:
                    category_parts.append("trend_against")

        # 2. Spike classification
        if ctx.is_algorithmic_spike:
            reasons.append(f"Algorithmic spike ({ctx.spike_size_vs_median:.0f}x median) — these hold gains")
            category_parts.append("algo_spike_trap")
        elif ctx.is_noise_spike:
            reasons.append(f"Noise spike ({ctx.spike_size_vs_median:.1f}x median) — likely to retrace")
            category_parts.append("noise_fade_win" if win else "noise_fade_loss")

        # 3. Volatility regime
        if ctx.vol_regime == "low":
            reasons.append("Low vol regime — limited retrace potential")
            if not win:
                category_parts.append("vol_collapse")
        elif ctx.vol_regime == "high":
            reasons.append("High vol regime — strong retrace expected")
        elif ctx.vol_regime == "extreme":
            reasons.append("Extreme vol — unpredictable, could go either way")

        # 4. Cluster analysis
        if ctx.cluster_size >= 3:
            reasons.append(f"Large cluster ({ctx.cluster_size} spikes) — trend-like behavior")
            if not win:
                category_parts.append("cluster_continuation")
        elif ctx.cluster_size == 1 or ctx.is_isolated_spike:
            reasons.append("Isolated spike — cleaner fade setup")
            if win:
                category_parts.append("good_fade")

        # 5. Timing
        if ctx.bars_since_last_spike > ctx.avg_gap_recent * 2:
            reasons.append("Long gap since last spike — extended quiet period")
            if win:
                category_parts.append("good_timing")
        elif ctx.bars_since_last_spike < 3:
            reasons.append("Quick succession from last spike — may be in a cluster")
            if not win:
                category_parts.append("late_entry")

        # 6. Losing streak
        if ctx.consecutive_losses >= 3:
            reasons.append(f"Losing streak ({ctx.consecutive_losses} in a row) — regime may have shifted")
            category_parts.append("overtrade")

        # 7. Conviction
        if ctx.conviction < 0.3:
            reasons.append(f"Low conviction entry ({ctx.conviction:.2f})")
        elif ctx.conviction > 0.7:
            reasons.append(f"High conviction entry ({ctx.conviction:.2f})")

        # ---- Build diagnosis ----
        if win:
            outcome.diagnosis = f"WON: {'; '.join(reasons)}" if reasons else "WON: Standard fade"
        else:
            outcome.diagnosis = f"LOST: {'; '.join(reasons)}" if reasons else "LOST: Unknown cause"

        # ---- Classify category ----
        if not category_parts:
            if win:
                outcome.category = "good_fade"
            else:
                outcome.category = "unknown_loss"
        else:
            # Pick the most impactful category
            outcome.category = category_parts[0]

        # ---- Fixable? ----
        outcome.fixable = outcome.category in {
            "trend_against", "algo_spike_trap", "cluster_continuation",
            "vol_collapse", "overtrade", "late_entry",
        }

        # ---- Suggested fix ----
        fixes = {
            "trend_against": "Filter: skip fades when drift_strength > 0.4 against direction",
            "algo_spike_trap": "Filter: skip fades when spike_size_vs_median > 15x",
            "cluster_continuation": "Wait longer after cluster (increase cluster_gap)",
            "vol_collapse": "Filter: skip fades when vol_regime == 'low'",
            "overtrade": "Pause trading after 3+ consecutive losses",
            "late_entry": "Tighten entry window (reduce cluster_gap + 2)",
        }
        outcome.suggested_fix = fixes.get(outcome.category, "")

        # ---- Record ----
        self.history.append(outcome)
        self._recent_pnls.append(outcome.pnl_pct)
        self._update_category_stats(outcome)

        return outcome

    def _update_category_stats(self, outcome: TradeOutcome):
        """Update running stats per category."""
        cat = outcome.category
        if cat not in self._category_stats:
            self._category_stats[cat] = {"trades": 0, "wins": 0, "total_pnl": 0.0}
        
        stats = self._category_stats[cat]
        stats["trades"] += 1
        stats["wins"] += 1 if outcome.is_win() else 0
        stats["total_pnl"] += outcome.pnl_pct

    def get_category_report(self) -> dict:
        """Get win rate and PnL by category."""
        report = {}
        for cat, stats in self._category_stats.items():
            report[cat] = {
                "trades": stats["trades"],
                "win_rate": stats["wins"] / stats["trades"] * 100 if stats["trades"] > 0 else 0,
                "total_pnl": stats["total_pnl"],
                "avg_pnl": stats["total_pnl"] / stats["trades"] if stats["trades"] > 0 else 0,
            }
        return report

    def get_recent_win_rate(self, n: int = 20) -> float:
        """Win rate of last N trades."""
        recent = list(self._recent_pnls)[-n:]
        if not recent:
            return 0.5
        return sum(1 for p in recent if p > 0) / len(recent)

    def get_consecutive_losses(self) -> int:
        """Current consecutive loss count."""
        count = 0
        for pnl in reversed(list(self._recent_pnls)):
            if pnl <= 0:
                count += 1
            else:
                break
        return count

    def get_latest_diagnosis(self) -> str:
        """Get diagnosis of the most recent trade."""
        if self.history:
            return self.history[-1].diagnosis
        return "No trades analyzed yet"

    def print_summary(self):
        """Print human-readable summary of all post-mortems."""
        if not self.history:
            print("  No trades analyzed yet.")
            return

        print(f"\n{'='*72}")
        print(f"  TRADE THINKING LAYER — POST-MORTEM SUMMARY")
        print(f"{'='*72}")
        
        total = len(self.history)
        wins = sum(1 for t in self.history if t.is_win())
        print(f"  Total trades: {total}  |  Win rate: {wins/total*100:.1f}%")
        
        # Category breakdown
        report = self.get_category_report()
        print(f"\n  --- Diagnosis by Category ---")
        for cat, stats in sorted(report.items(), key=lambda x: -x[1]["trades"]):
            print(f"    {cat:30s}: {stats['trades']:3d} trades, "
                  f"WR={stats['win_rate']:.0f}%, "
                  f"avg PnL={stats['avg_pnl']*100:+.3f}%")
        
        # Fixable losses
        fixable = [t for t in self.history if not t.is_win() and t.fixable]
        if fixable:
            print(f"\n  --- Avoidable Losses ({len(fixable)}/{total}) ---")
            for t in fixable[-5:]:  # Last 5
                print(f"    #{t.trade_id}: {t.category}")
                print(f"      {t.diagnosis}")
                print(f"      FIX: {t.suggested_fix}")
        
        # Recent diagnostics
        print(f"\n  --- Last 5 Trade Diagnostics ---")
        for t in self.history[-5:]:
            emoji = "+" if t.is_win() else "-"
            print(f"    #{t.trade_id} {emoji} PnL={t.pnl_pct*100:+.3f}% [{t.category}]")
            print(f"      {t.diagnosis}")
        
        print(f"{'='*72}")


# ---------------------------------------------------------------------------
# Adaptive Learner — uses post-mortem patterns to adjust strategy
# ---------------------------------------------------------------------------

class AdaptiveLearner:
    """
    Observes post-mortem patterns and suggests or applies strategy adjustments.
    
    Think of it as the "learning" layer that closes the feedback loop:
    Trade → Post-Mortem → Pattern Detection → Strategy Adjustment → Better Trades
    """

    def __init__(self):
        self.analyzer = PostMortemAnalyzer()
        
        # Adaptive thresholds (start at defaults, adjust based on patterns)
        self.min_conviction = 0.3       # Minimum conviction to enter
        self.max_consecutive_losses = 3 # Pause after N consecutive losses
        self.drift_filter = 0.4         # Skip fades when drift > this against us
        self.spike_size_filter = 15.0   # Skip fades when spike > this x median
        self.vol_filter = "low"         # Skip fades in this vol regime
        
        # Classification-based filters (learned from post-mortem)
        self.skip_late_entry = True           # Skip entries that historically lose
        self.skip_cluster_trades = True       # Skip trades in clusters of 3+
        self.cluster_threshold = 3            # Min spikes in cluster to skip
        self.min_bars_after_spike = 4         # Need at least this many bars after spike
        self.max_algo_spike_mult = 12.0       # Skip spikes above this multiplier
        
        # Loss category tracking for filter decisions
        self._category_losses: dict[str, int] = {}  # category -> loss count
        self._category_total: dict[str, int] = {}   # category -> total count
        
        # Tracking
        self._adjustments_made: list[dict] = []
        self._check_interval = 10       # Re-evaluate every N trades
        self._last_check = 0

    def record_trade(self, outcome: TradeOutcome) -> TradeOutcome:
        """Record a trade and run post-mortem. Returns enriched outcome."""
        return self.analyzer.analyze(outcome)

    def should_trade(self, context: MarketContext) -> tuple[bool, str]:
        """
        Check current adaptive filters against the trade context.
        Returns (should_trade, reason).
        """
        # Check consecutive losses
        losses = self.analyzer.get_consecutive_losses()
        if losses >= self.max_consecutive_losses:
            return False, f"Paused: {losses} consecutive losses (max={self.max_consecutive_losses})"

        # ── Classification-based filters (learned from post-mortem) ──
        
        # Filter 1: Skip late entries (historically 0% WR)
        if self.skip_late_entry and context.bars_since_last_spike <= self.min_bars_after_spike:
            # Check if we're entering too soon after spike (within cluster window)
            if context.cluster_size >= 2:
                return False, f"Late entry: {context.bars_since_last_spike} bars after spike, cluster={context.cluster_size}"
        
        # Filter 2: Skip cluster trades (cluster_continuation = 0% WR)
        if self.skip_cluster_trades and context.cluster_size >= self.cluster_threshold:
            return False, f"Cluster continuation: {context.cluster_size} spikes in window (threshold={self.cluster_threshold})"
        
        # Filter 3: Skip algo spikes that hold gains (algo_spike_trap = ~8% WR)
        if context.is_algorithmic_spike and context.spike_size_vs_median > self.max_algo_spike_mult:
            return False, f"Algo spike trap: {context.spike_size_vs_median:.0f}x median (max={self.max_algo_spike_mult:.0f}x)"

        # ── Original filters ──

        # Check drift filter
        if context.drift_direction != "flat" and context.drift_strength > self.drift_filter:
            fade_dir = "down" if context.spike_size_pct > 0 else "up"
            if context.drift_direction != fade_dir:
                return False, f"Drift {context.drift_direction}({context.drift_strength:.2f}) against fade"

        # Check spike size filter
        if context.spike_size_vs_median > self.spike_size_filter:
            return False, f"Algorithmic spike ({context.spike_size_vs_median:.0f}x) — skip"

        # Check vol regime
        if context.vol_regime == self.vol_filter:
            return False, f"Low volatility regime — limited retrace"

        # Check conviction
        if context.conviction < self.min_conviction:
            return False, f"Low conviction ({context.conviction:.2f} < {self.min_conviction})"

        return True, "All filters passed"

    def maybe_adjust(self):
        """
        Periodically check patterns and adjust thresholds.
        Called after each trade close.
        """
        n_trades = len(self.analyzer.history)
        if n_trades - self._last_check < self._check_interval:
            return
        self._last_check = n_trades

        report = self.analyzer.get_category_report()

        # ---- Pattern 1: Too many trend-against losses ----
        trend_losses = report.get("trend_against", {}).get("trades", 0)
        if trend_losses >= 3:
            old = self.drift_filter
            self.drift_filter = max(0.2, self.drift_filter - 0.1)
            self._record_adjustment(
                "drift_filter", old, self.drift_filter,
                f"Reduced drift filter from {old:.2f} to {self.drift_filter:.2f} "
                f"({trend_losses} trend-against losses)"
            )

        # ---- Pattern 2: Too many algo_spike_trap losses ----
        algo_losses = report.get("algo_spike_trap", {}).get("trades", 0)
        if algo_losses >= 2:
            old = self.spike_size_filter
            self.spike_size_filter = max(10, self.spike_size_filter - 3)
            self._record_adjustment(
                "spike_size_filter", old, self.spike_size_filter,
                f"Reduced spike filter from {old:.0f}x to {self.spike_size_filter:.0f}x "
                f"({algo_losses} algo-spike traps)"
            )

        # ---- Pattern 3: Low vol losses ----
        vol_losses = report.get("vol_collapse", {}).get("trades", 0)
        if vol_losses >= 3:
            old = self.vol_filter
            # Already filtering low vol, but tighten it
            self._record_adjustment(
                "vol_filter", old, "low+normal",
                f"Vol collapse losses: {vol_losses} — consider filtering normal vol too"
            )

        # ---- Pattern 4: Win rate dropped below 50% ----
        wr = self.analyzer.get_recent_win_rate(20)
        if wr < 0.45 and n_trades >= 20:
            old = self.min_conviction
            self.min_conviction = min(0.5, self.min_conviction + 0.05)
            self._record_adjustment(
                "min_conviction", old, self.min_conviction,
                f"Win rate dropped to {wr:.0%} — raising minimum conviction"
            )

        # ---- Pattern 5: Win rate above 70% — can be more aggressive ----
        if wr > 0.70 and n_trades >= 20:
            old = self.min_conviction
            self.min_conviction = max(0.2, self.min_conviction - 0.02)
            self._record_adjustment(
                "min_conviction", old, self.min_conviction,
                f"Win rate strong at {wr:.0%} — relaxing minimum conviction"
            )

        # ---- Pattern 6: Too many late_entry losses — tighten entry window ----
        late_losses = report.get("late_entry", {}).get("trades", 0)
        late_wr = report.get("late_entry", {}).get("win_rate", 100)
        if late_losses >= 5 and late_wr < 20:
            old = self.min_bars_after_spike
            self.min_bars_after_spike = min(6, self.min_bars_after_spike + 1)
            self._record_adjustment(
                "min_bars_after_spike", old, self.min_bars_after_spike,
                f"Tightened entry window: {late_losses} late_entry losses (WR={late_wr:.0f}%)",
            )

        # ---- Pattern 7: Too many cluster_continuation losses — increase cluster threshold ----
        cluster_losses = report.get("cluster_continuation", {}).get("trades", 0)
        cluster_wr = report.get("cluster_continuation", {}).get("win_rate", 100)
        if cluster_losses >= 5 and cluster_wr < 20:
            old = self.cluster_threshold
            self.cluster_threshold = max(2, self.cluster_threshold - 1)
            self._record_adjustment(
                "cluster_threshold", old, self.cluster_threshold,
                f"Lowered cluster threshold: {cluster_losses} cluster losses (WR={cluster_wr:.0f}%)",
            )

        # ---- Pattern 8: Too many algo_spike_trap — tighten spike filter ----
        algo_losses = report.get("algo_spike_trap", {}).get("trades", 0)
        algo_wr = report.get("algo_spike_trap", {}).get("win_rate", 100)
        if algo_losses >= 5 and algo_wr < 20:
            old = self.max_algo_spike_mult
            self.max_algo_spike_mult = max(5, self.max_algo_spike_mult - 2)
            self._record_adjustment(
                "max_algo_spike_mult", old, self.max_algo_spike_mult,
                f"Tightened algo spike filter: {algo_losses} algo losses (WR={algo_wr:.0f}%)",
            )

    def _record_adjustment(self, param: str, old_val, new_val, reason: str):
        """Log a strategy adjustment."""
        adj = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "param": param,
            "old_value": old_val,
            "new_value": new_val,
            "reason": reason,
        }
        self._adjustments_made.append(adj)
        print(f"  [ADAPTIVE] {reason}")

    def get_status(self) -> dict:
        """Get current adaptive state."""
        return {
            "min_conviction": self.min_conviction,
            "max_consecutive_losses": self.max_consecutive_losses,
            "drift_filter": self.drift_filter,
            "spike_size_filter": self.spike_size_filter,
            "vol_filter": self.vol_filter,
            # Classification-based filters
            "skip_late_entry": self.skip_late_entry,
            "skip_cluster_trades": self.skip_cluster_trades,
            "cluster_threshold": self.cluster_threshold,
            "min_bars_after_spike": self.min_bars_after_spike,
            "max_algo_spike_mult": self.max_algo_spike_mult,
            # Stats
            "adjustments_made": len(self._adjustments_made),
            "recent_wr": self.analyzer.get_recent_win_rate(20),
            "consecutive_losses": self.analyzer.get_consecutive_losses(),
            "total_analyzed": len(self.analyzer.history),
        }

    def print_status(self):
        """Print current adaptive state."""
        status = self.get_status()
        print(f"\n  [ADAPTIVE] conv={status['min_conviction']:.2f} "
              f"drift={status['drift_filter']:.2f} "
              f"spike<{status['spike_size_filter']:.0f}x "
              f"WR={status['recent_wr']:.0%} "
              f"losses={status['consecutive_losses']} "
              f"adjustments={status['adjustments_made']}")


# ---------------------------------------------------------------------------
# Context Builder — builds MarketContext from engine state
# ---------------------------------------------------------------------------

class ContextBuilder:
    """
    Builds a rich MarketContext from the SpikeFadeEngine's internal state.
    Called at every potential trade entry to capture the full picture.
    """

    def __init__(self):
        self._recent_returns: deque[float] = deque(maxlen=2000)
        self._recent_gaps: deque[int] = deque(maxlen=100)
        self._baseline_vol: float = 0.001  # Will be calibrated

    def build(self, engine, bar_idx: int = 0) -> MarketContext:
        """
        Build a MarketContext from the engine's current state.
        
        Args:
            engine: SpikeFadeEngine instance
            bar_idx: Current bar index
        """
        closes = list(engine._closes)
        returns = list(engine._returns)
        
        if len(closes) < 100:
            return MarketContext()

        ctx = MarketContext()
        
        # ---- Price context ----
        ctx.price = closes[-1] if closes else 0
        ctx.spike_price = engine._last_spike_price
        ctx.spike_size_pct = engine._last_spike_size * 100

        # ---- Volatility ----
        recent_rets = np.array(returns[-200:]) if returns else np.array([0])
        abs_rets = np.abs(recent_rets)
        current_vol = float(np.median(abs_rets)) if len(abs_rets) > 0 else 0
        ctx.current_vol = current_vol
        
        # Calibrate baseline (EMA of vol)
        if self._baseline_vol == 0.001:
            self._baseline_vol = current_vol
        else:
            self._baseline_vol = self._baseline_vol * 0.95 + current_vol * 0.05
        
        ctx.vol_vs_baseline = current_vol / self._baseline_vol if self._baseline_vol > 0 else 1.0
        
        if ctx.vol_vs_baseline > 2.0:
            ctx.vol_regime = "extreme"
        elif ctx.vol_vs_baseline > 1.3:
            ctx.vol_regime = "high"
        elif ctx.vol_vs_baseline < 0.6:
            ctx.vol_regime = "low"
        else:
            ctx.vol_regime = "normal"

        # ---- Spike size vs median ----
        if current_vol > 0:
            ctx.spike_size_vs_median = engine._last_spike_size / current_vol
        ctx.is_algorithmic_spike = ctx.spike_size_vs_median > 15
        ctx.is_noise_spike = ctx.spike_size_vs_median < 5

        # ---- Drift direction ----
        if len(closes) >= 50:
            lookback = min(50, len(closes))
            drift_returns = np.array(returns[-lookback:])
            drift_sum = float(np.sum(drift_returns))
            drift_range = float(np.max(np.cumsum(drift_returns)) - np.min(np.cumsum(drift_returns)))
            
            if drift_range > 0:
                drift_pos = (np.cumsum(drift_returns)[-1] - np.min(np.cumsum(drift_returns))) / drift_range
                if drift_pos > 0.65:
                    ctx.drift_direction = "up"
                elif drift_pos < 0.35:
                    ctx.drift_direction = "down"
                else:
                    ctx.drift_direction = "flat"
                ctx.drift_strength = abs(drift_sum) / drift_range if drift_range > 0 else 0
            else:
                ctx.drift_direction = "flat"
                ctx.drift_strength = 0

        # ---- Cluster context ----
        spike_indices = engine._spike_indices
        if spike_indices:
            ctx.cluster_size = 1
            for i in range(len(spike_indices) - 1, 0, -1):
                gap = spike_indices[i] - spike_indices[i-1]
                if gap <= engine.cluster_gap:
                    ctx.cluster_size += 1
                else:
                    break
            
            ctx.is_isolated_spike = ctx.cluster_size == 1
            ctx.cluster_age_bars = bar_idx - spike_indices[-1] if spike_indices else 0
            ctx.bars_since_last_spike = engine._bars_since_spike

        # ---- Recent gaps ----
        if len(spike_indices) >= 2:
            gaps = [spike_indices[i] - spike_indices[i-1] for i in range(1, len(spike_indices))]
            ctx.avg_gap_recent = float(np.mean(gaps[-20:])) if gaps else 0

        # ---- Session context ----
        ctx.session_hour = datetime.now(timezone.utc).hour
        ctx.bars_in_session = bar_idx

        # ---- Conviction score ----
        ctx.conviction = self._compute_conviction(ctx)

        return ctx

    def _compute_conviction(self, ctx: MarketContext) -> float:
        """
        Compute a conviction score (0-1) based on context factors.
        Higher = more confident the trade will win.
        """
        score = 0.5  # Base

        # Drift alignment: +0.2 if aligned, -0.2 if against
        if ctx.drift_direction == "flat":
            score += 0.05  # Slight edge — no trend resistance
        elif ctx.drift_strength < 0.3:
            score += 0.1   # Weak drift, ok to fade
        elif ctx.drift_strength > 0.6:
            score -= 0.2   # Strong drift against us
        
        # Volatility: +0.15 for high vol, -0.1 for low
        if ctx.vol_regime == "high":
            score += 0.15
        elif ctx.vol_regime == "extreme":
            score += 0.05  # Too unpredictable
        elif ctx.vol_regime == "low":
            score -= 0.15
        elif ctx.vol_regime == "normal":
            score += 0.05

        # Spike type: +0.1 for noise, -0.15 for algo
        if ctx.is_noise_spike:
            score += 0.1
        elif ctx.is_algorithmic_spike:
            score -= 0.2

        # Cluster: +0.1 for isolated, -0.1 for large cluster
        if ctx.is_isolated_spike:
            score += 0.1
        elif ctx.cluster_size >= 3:
            score -= 0.1

        # Recent form: +0.1 if winning, -0.1 if losing streak
        if ctx.recent_win_rate > 0.65:
            score += 0.1
        elif ctx.recent_win_rate < 0.45:
            score -= 0.1
        
        if ctx.consecutive_losses >= 3:
            score -= 0.15

        return max(0.0, min(1.0, score))
