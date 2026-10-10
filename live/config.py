"""
live/config.py — Unified trading configuration.

All settings in one place: which indices to trade, risk limits, LLM backend,
checkpoint paths, and execution parameters.

Usage:
    from live.config import LiveConfig, IndexConfig
    cfg = LiveConfig.from_json("live/settings.json")
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class IndexConfig:
    """Configuration for a single synthetic index."""

    symbol: str                          # Deriv API symbol, e.g. "R_75"
    name: str = ""                       # Human-readable name
    enabled: bool = True

    # Checkpoint paths
    world_model_path: str = ""           # path to world_model_best.pt
    policy_path: str = ""                # path to policy_best.pt

    # Risk overrides (None = use global defaults)
    max_position: int | None = None
    max_drawdown_pct: float | None = None
    drift_detection: bool | None = None  # Crash500-specific

    def __post_init__(self):
        if not self.name:
            self.name = self.symbol


@dataclass
class LLMConfig:
    """LLM strategy agent settings."""

    enabled: bool = False

    # API keys (set via env vars or config file)
    groq_api_key: str = ""
    openai_api_key: str = ""
    ollama_model: str = ""               # e.g. "phi3:mini" for local

    # Behavior
    cache_ttl_s: float = 60.0            # cache LLM responses for this long
    timeout_s: float = 10.0              # max wait for LLM response
    fallback_to_rules: bool = True       # use rule-based if LLM unavailable


@dataclass
class RiskConfig:
    """Global risk management settings."""

    initial_equity: float = 10000.0
    max_portfolio_exposure: float = 3.0  # max total across all indices
    max_single_exposure: float = 1.5     # max per index
    max_drawdown_warning: float = 0.05   # 5%: start reducing
    max_drawdown_critical: float = 0.10  # 10%: reduce hard
    max_drawdown_stop: float = 0.15      # 15%: stop trading
    vol_target: float = 0.001            # target volatility for sizing


@dataclass
class GovernorConfig:
    """Hard-coded risk cage settings."""

    max_drawdown_pct: float = 0.10
    resume_drawdown_pct: float = 0.05
    consecutive_loss_limit: int = 5
    loss_cooldown_bars: int = 100


@dataclass
class ExecutionConfig:
    """Execution parameters."""

    position_size: float = 1.0           # fraction of equity per trade
    spread_cost: float = 0.0001          # 1 pip
    slippage_cost: float = 0.00005       # 0.5 pip
    min_hold_bars: int = 3               # minimum bars before closing
    history_bars: int = 500              # bars to fetch on startup
    rebuild_state_every: int = 50        # rebuild StateBuilder every N candles


@dataclass
class LiveConfig:
    """Top-level trading configuration."""

    # Indices to trade
    indices: list[IndexConfig] = field(default_factory=lambda: [
        IndexConfig(symbol="R_75", name="Volatility 75"),
        IndexConfig(symbol="R_10", name="Volatility 10"),
        IndexConfig(symbol="BOOM500", name="Boom 500"),
        IndexConfig(symbol="CRASH500", name="Crash 500"),
    ])

    # Sub-configs
    llm: LLMConfig = field(default_factory=LLMConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    governor: GovernorConfig = field(default_factory=GovernorConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)

    # Device
    device: str = "cpu"                  # "cpu" or "cuda"

    # Logging
    log_dir: str = "checkpoints_live"
    log_trades: bool = True
    log_decisions: bool = True

    @classmethod
    def from_json(cls, path: str | Path) -> LiveConfig:
        """Load config from JSON file."""
        with open(path) as f:
            data = json.load(f)

        cfg = cls()

        if "indices" in data:
            cfg.indices = [IndexConfig(**idx) for idx in data["indices"]]
        if "llm" in data:
            for k, v in data["llm"].items():
                if hasattr(cfg.llm, k):
                    setattr(cfg.llm, k, v)
        if "risk" in data:
            for k, v in data["risk"].items():
                if hasattr(cfg.risk, k):
                    setattr(cfg.risk, k, v)
        if "governor" in data:
            for k, v in data["governor"].items():
                if hasattr(cfg.governor, k):
                    setattr(cfg.governor, k, v)
        if "execution" in data:
            for k, v in data["execution"].items():
                if hasattr(cfg.execution, k):
                    setattr(cfg.execution, k, v)
        for k in ("device", "log_dir", "log_trades", "log_decisions"):
            if k in data:
                setattr(cfg, k, data[k])

        return cfg

    def to_json(self, path: str | Path) -> None:
        """Save config to JSON file."""
        from dataclasses import asdict
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2, default=str)

    def enabled_indices(self) -> list[IndexConfig]:
        """Return only enabled indices."""
        return [idx for idx in self.indices if idx.enabled]
