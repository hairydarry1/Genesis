"""
model/strategy_agent.py — LLM-based strategy reasoning agent.

Combines world model predictions with LLM reasoning to produce
informed trading decisions. Supports multiple backends:
  - OpenAI (GPT-4o-mini) — cheap, reliable
  - Groq (Llama 3) — free tier, fast
  - Ollama (local) — free, no API key needed

Usage:
    agent = StrategyAgent(backend="openai")
    decision = agent.analyze(market_context, world_model_prediction)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class MarketContext:
    """Structured market state for LLM consumption."""
    # Current prices
    current_price: float = 0.0
    timestamp: str = ""

    # World model predictions
    wm_mean_returns: list[float] = field(default_factory=list)  # next H bar returns
    wm_std_returns: list[float] = field(default_factory=list)   # std per horizon
    wm_snr: float = 0.0  # signal-to-noise ratio

    # Volatility regime
    realized_vol: float = 0.0
    vol_regime: float = 1.0  # short/long vol ratio
    vol_percentile: float = 50.0  # where current vol sits historically

    # Market structure
    nearest_structure_distance: float = 0.0
    nearest_structure_type: str = "none"
    n_structures_200: int = 0

    # Trend/momentum
    trend_15min: str = "flat"  # up/down/flat
    trend_1h: str = "flat"
    streak: int = 0  # consecutive up/down bars

    # Price position
    price_z_score: float = 0.0  # position in recent range
    price_position_in_range: float = 0.5  # 0=low, 1=high

    # Risk state
    current_drawdown: float = 0.0
    daily_pnl_pct: float = 0.0
    current_position: str = "flat"  # flat/long/short
    position_size: float = 0.0

    # Recent performance
    recent_win_rate: float = 0.5
    recent_avg_return: float = 0.0

    def to_prompt_context(self) -> str:
        """Format as human-readable context for LLM."""
        direction = "BULLISH" if self.wm_snr > 0.5 else "BEARISH" if self.wm_snr < -0.5 else "NEUTRAL"
        vol_state = "HIGH" if self.vol_regime > 1.2 else "LOW" if self.vol_regime < 0.8 else "NORMAL"

        lines = [
            f"=== MARKET STATE ({self.timestamp}) ===",
            f"Price: {self.current_price:.2f}",
            f"",
            f"--- World Model Prediction (next {len(self.wm_mean_returns)} bars) ---",
            f"Direction: {direction} (SNR={self.wm_snr:.3f})",
        ]

        if self.wm_mean_returns:
            cum_return = sum(self.wm_mean_returns)
            cum_std = np.sqrt(sum(s**2 for s in self.wm_std_returns)) if self.wm_std_returns else 0
            lines.append(f"Expected cumulative return: {cum_return*100:.3f}%")
            lines.append(f"Uncertainty (1σ): {cum_std*100:.3f}%")
            lines.append(f"Sharpe-like ratio: {cum_return/cum_std:.2f}" if cum_std > 0 else "Sharpe: N/A")

        lines.extend([
            f"",
            f"--- Volatility ---",
            f"Realized vol: {self.realized_vol*100:.4f}%/bar",
            f"Regime: {vol_state} (ratio={self.vol_regime:.2f})",
            f"Percentile: {self.vol_percentile:.0f}th",
            f"",
            f"--- Structure ---",
            f"Nearest structure: {self.nearest_structure_type} at {self.nearest_structure_distance*100:.2f}% distance",
            f"Active structures (200-bar): {self.n_structures_200}",
            f"",
            f"--- Trend ---",
            f"15min: {self.trend_15min}  |  1h: {self.trend_1h}  |  Streak: {self.streak}",
            f"Price z-score: {self.price_z_score:.2f}  |  Range position: {self.price_position_in_range:.2f}",
            f"",
            f"--- Portfolio ---",
            f"Position: {self.current_position} ({self.position_size:.1f}x)",
            f"Drawdown: {self.current_drawdown*100:.1f}%",
            f"Daily P&L: {self.daily_pnl_pct*100:.2f}%",
            f"Recent win rate: {self.recent_win_rate*100:.0f}%",
        ])

        return "\n".join(lines)


@dataclass
class LLMDecision:
    """Structured decision from the LLM strategy agent."""
    action: str = "hold"  # buy/sell/hold
    confidence: float = 0.5  # 0-1
    size_multiplier: float = 1.0  # scale of position size (0-2)
    stop_loss_pct: float = 0.003  # suggested stop loss as % of price
    take_profit_pct: float = 0.006  # suggested take profit
    reasoning: str = ""  # LLM's reasoning
    risk_notes: str = ""  # risk considerations
    regime_adjustment: str = ""  # regime-specific adjustments
    latency_ms: float = 0.0  # LLM response time

    def to_dict(self) -> dict:
        return {
            "action": self.action,
            "confidence": self.confidence,
            "size_multiplier": self.size_multiplier,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "reasoning": self.reasoning,
            "risk_notes": self.risk_notes,
            "regime_adjustment": self.regime_adjustment,
            "latency_ms": self.latency_ms,
        }


# ---------------------------------------------------------------------------
# LLM Backend abstraction
# ---------------------------------------------------------------------------

class LLMBackend:
    """Base class for LLM backends."""

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        return "base"


class OpenAIBackend(LLMBackend):
    """OpenAI API backend (GPT-4o-mini)."""

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self.api_key = api_key
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()

        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            logger.warning("openai_api_error status=%d", e.code)
            raise
        except Exception as e:
            logger.warning("openai_request_error %s", e)
            raise

    @property
    def name(self) -> str:
        return f"openai:{self.model}"


class OllamaBackend(LLMBackend):
    """Ollama local backend."""

    def __init__(self, model: str = "phi3:mini", base_url: str = "http://localhost:11434"):
        self.model = model
        self.base_url = base_url

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
            "options": {"num_predict": max_tokens, "temperature": 0.3},
        }).encode()

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
                return data["message"]["content"]
        except urllib.error.URLError:
            logger.warning("ollama_unreachable url=%s", self.base_url)
            raise
        except Exception as e:
            logger.warning("ollama_error %s", e)
            raise

    @property
    def name(self) -> str:
        return f"ollama:{self.model}"


class GroqBackend(LLMBackend):
    """Groq free-tier backend (Llama 3)."""

    def __init__(self, api_key: str, model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()

        req = urllib.request.Request(
            "https://api.groq.com/openai/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("groq_error %s", e)
            raise

    @property
    def name(self) -> str:
        return f"groq:{self.model}"


class GeminiBackend(LLMBackend):
    """Google Gemini free-tier backend (1,500 req/day)."""

    def __init__(self, api_key: str, model: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        import urllib.request
        import urllib.error

        # Gemini uses a different API format
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        payload = json.dumps({
            "contents": [
                {"role": "user", "parts": [
                    {"text": f"System: {system_prompt}\n\nUser: {user_prompt}"}
                ]}
            ],
            "generationConfig": {
                "maxOutputTokens": max_tokens,
                "temperature": 0.3,
            },
        }).encode()

        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception as e:
            logger.warning("gemini_error %s", e)
            raise

    @property
    def name(self) -> str:
        return f"gemini:{self.model}"


class OpenRouterBackend(LLMBackend):
    """OpenRouter free-tier backend (28+ free models)."""

    def __init__(self, api_key: str, model: str = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"):
        self.api_key = api_key
        self.model = model

    def complete(self, system_prompt: str, user_prompt: str, max_tokens: int = 500) -> str:
        import urllib.request
        import urllib.error

        payload = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.3,
        }).encode()

        req = urllib.request.Request(
            "https://openrouter.ai/api/v1/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                data = json.loads(resp.read())
                return data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning("openrouter_error %s", e)
            raise

    @property
    def name(self) -> str:
        return f"openrouter:{self.model}"


# ---------------------------------------------------------------------------
# Strategy Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a quantitative trading strategy agent for synthetic indices (Deriv Boom/Crash/Volatility).

Your job: analyze the market state and world model predictions, then output a JSON trading decision.

RULES:
1. NEVER risk more than 2% of equity per trade
2. Reduce size during high volatility or drawdowns
3. Respect the world model's confidence — low SNR = smaller/no position
4. Account for market structure (support/resistance zones)
5. Consider regime: calm markets favor mean-reversion, volatile favor trend-following
6. If world model and price trend disagree, be cautious (reduce size)

OUTPUT FORMAT (JSON only, no markdown):
{
  "action": "buy" | "sell" | "hold",
  "confidence": 0.0-1.0,
  "size_multiplier": 0.0-2.0,
  "stop_loss_pct": 0.001-0.01,
  "take_profit_pct": 0.002-0.02,
  "reasoning": "1-2 sentence explanation",
  "risk_notes": "key risk factors",
  "regime_adjustment": "how regime affects the decision"
}

Be concise. Focus on actionable insights, not restating the data."""


class StrategyAgent:
    """
    LLM-powered strategy reasoning agent.

    Combines world model predictions with LLM reasoning to produce
    trading decisions. Falls back to rule-based logic when LLM is unavailable.
    """

    def __init__(
        self,
        backends: list[LLMBackend] | None = None,
        cache_ttl_s: float = 60.0,
        enabled: bool = True,
    ):
        self.backends = backends or []
        self.cache_ttl_s = cache_ttl_s
        self.enabled = enabled

        # Response cache (avoid duplicate calls)
        self._cache: dict[str, tuple[float, LLMDecision]] = {}
        self._call_count = 0
        self._error_count = 0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> StrategyAgent:
        """Create from a config dict with API keys."""
        backends = []

        # Order: Gemini (best free) → Groq (fast) → OpenRouter (many models) → OpenAI (paid) → Ollama (local)
        if config.get("gemini_api_key"):
            backends.append(GeminiBackend(api_key=config["gemini_api_key"]))
        if config.get("groq_api_key"):
            backends.append(GroqBackend(api_key=config["groq_api_key"]))
        if config.get("openrouter_api_key"):
            backends.append(OpenRouterBackend(api_key=config["openrouter_api_key"]))
        if config.get("openai_api_key"):
            backends.append(OpenAIBackend(api_key=config["openai_api_key"]))
        # Ollama: always try if available (local, free, unlimited)
        ollama_model = config.get("ollama_model")
        if ollama_model:
            backends.append(OllamaBackend(model=ollama_model))
        else:
            # Auto-detect Ollama
            try:
                import urllib.request
                urllib.request.urlopen("http://localhost:11434/api/tags", timeout=2)
                backends.append(OllamaBackend(model="phi3:mini"))
            except Exception:
                pass

        return cls(
            backends=backends,
            cache_ttl_s=config.get("cache_ttl_s", 60.0),
            enabled=config.get("llm_enabled", len(backends) > 0),
        )

    def analyze(
        self,
        context: MarketContext,
        fallback_action: str = "hold",
    ) -> LLMDecision:
        """
        Analyze market state and produce a trading decision.

        If LLM is unavailable or errors, falls back to rule-based logic.
        """
        if not self.enabled or not self.backends:
            return self._rule_based_fallback(context)

        # Check cache
        cache_key = self._cache_key(context)
        if cache_key in self._cache:
            cached_time, cached_decision = self._cache[cache_key]
            if time.time() - cached_time < self.cache_ttl_s:
                return cached_decision

        # Try each backend
        user_prompt = context.to_prompt_context()

        for backend in self.backends:
            try:
                t0 = time.time()
                response = backend.complete(SYSTEM_PROMPT, user_prompt, max_tokens=400)
                latency_ms = (time.time() - t0) * 1000

                decision = self._parse_response(response)
                decision.latency_ms = latency_ms
                self._call_count += 1

                # Cache
                self._cache[cache_key] = (time.time(), decision)

                logger.info(
                    "llm_decision backend=%s action=%s conf=%.2f size=%.1f latency=%.0fms",
                    backend.name, decision.action, decision.confidence,
                    decision.size_multiplier, latency_ms,
                )
                return decision

            except Exception as e:
                self._error_count += 1
                logger.warning("llm_backend_failed backend=%s error=%s", backend.name, e)
                continue

        # All backends failed — fall back to rules
        logger.warning("all_llm_backends_failed using_rule_fallback")
        return self._rule_based_fallback(context)

    def _parse_response(self, response: str) -> LLMDecision:
        """Parse LLM JSON response into LLMDecision."""
        # Try to extract JSON from response (might be wrapped in markdown)
        text = response.strip()
        if "```json" in text:
            text = text.split("```json")[1].split("```")[0].strip()
        elif "```" in text:
            text = text.split("```")[1].split("```")[0].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in the text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                try:
                    data = json.loads(text[start:end])
                except json.JSONDecodeError:
                    logger.warning("llm_parse_failed response=%s", response[:200])
                    return LLMDecision(reasoning=f"Parse error: {response[:100]}")
            else:
                return LLMDecision(reasoning=f"No JSON found: {response[:100]}")

        return LLMDecision(
            action=data.get("action", "hold"),
            confidence=max(0.0, min(1.0, data.get("confidence", 0.5))),
            size_multiplier=max(0.0, min(2.0, data.get("size_multiplier", 1.0))),
            stop_loss_pct=max(0.001, min(0.01, data.get("stop_loss_pct", 0.003))),
            take_profit_pct=max(0.002, min(0.02, data.get("take_profit_pct", 0.006))),
            reasoning=data.get("reasoning", ""),
            risk_notes=data.get("risk_notes", ""),
            regime_adjustment=data.get("regime_adjustment", ""),
        )

    def _rule_based_fallback(self, context: MarketContext) -> LLMDecision:
        """
        Rule-based fallback when LLM is unavailable.

        Uses world model SNR + simple heuristics.
        """
        snr = context.wm_snr
        vol_ratio = context.vol_regime
        dd = context.current_drawdown

        # Basic SNR-based action
        if abs(snr) < 0.3:
            action = "hold"
            confidence = 0.3
            size = 0.0
        elif snr > 0:
            action = "buy"
            confidence = min(abs(snr) / 2.0, 1.0)
            size = confidence
        else:
            action = "sell"
            confidence = min(abs(snr) / 2.0, 1.0)
            size = confidence

        # Reduce size in high vol
        if vol_ratio > 1.5:
            size *= 0.5
        elif vol_ratio > 1.2:
            size *= 0.75

        # Reduce size during drawdown
        if dd > 0.10:
            size *= 0.2
        elif dd > 0.05:
            size *= 0.5

        return LLMDecision(
            action=action,
            confidence=confidence,
            size_multiplier=size,
            stop_loss_pct=0.003 if vol_ratio < 1.2 else 0.005,
            take_profit_pct=0.006 if vol_ratio < 1.2 else 0.010,
            reasoning=f"Rule fallback: SNR={snr:.3f}, vol_ratio={vol_ratio:.2f}",
            risk_notes=f"DD={dd*100:.1f}%",
        )

    def _cache_key(self, context: MarketContext) -> str:
        """Generate cache key from market context."""
        # Round values to reduce cache misses
        return (
            f"{context.wm_snr:.3f}_{context.vol_regime:.2f}_"
            f"{context.price_z_score:.1f}_{context.current_drawdown:.3f}"
        )

    def get_stats(self) -> dict:
        """Get agent statistics."""
        return {
            "enabled": self.enabled,
            "backends": [b.name for b in self.backends],
            "call_count": self._call_count,
            "error_count": self._error_count,
            "cache_size": len(self._cache),
        }
