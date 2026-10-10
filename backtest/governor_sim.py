"""
backtest/governor_sim.py — Governor replay for backtests.

Replays the governor's hard-coded risk rules against a simulated trade
sequence, exactly as they will run live:

  - Drawdown circuit breaker: suspend autonomous trading when drawdown from
    peak equity breaches the limit; resume only below the resume threshold
    (hysteresis, so one good bar doesn't flip-flop).
  - Consecutive-loss halt: pause after N losing trades in a row, for a
    cooldown of M bars.
  - Position limits: never allow |position| beyond the configured cap.

Every intervention is logged with a reason — the same audit trail a live
governor would need. The brief's acceptance test: during the worst historical
drawdown in the data, the breaker must actually trigger and halt autonomy.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class GovernorConfig:
    max_drawdown_pct: float = 0.10      # suspend autonomy at 10% DD from peak
    resume_drawdown_pct: float = 0.05   # re-enable below 5% DD (hysteresis)
    consecutive_loss_limit: int = 5     # halt after N losing trades in a row
    loss_cooldown_bars: int = 100       # bars to stay halted after loss streak
    max_position: int = 1               # hard cap on |position|


@dataclass
class GovernorState:
    halted: bool = False
    halt_reason: str | None = None
    peak_equity: float = 0.0
    consecutive_losses: int = 0
    cooldown_remaining: int = 0


class Governor:
    """Hard-coded risk cage around the policy. No ML — pure rules."""

    def __init__(self, config: GovernorConfig | None = None):
        self.cfg = config or GovernorConfig()
        self.state = GovernorState(peak_equity=float("inf"))
        self.interventions: list[dict] = []

    def reset(self) -> None:
        self.state = GovernorState(peak_equity=float("inf"))
        self.interventions = []

    def check(
        self,
        bar_idx: int,
        equity: float,
        requested_position: int,
    ) -> tuple[int, bool]:
        """
        Apply rules to one decision point.

        Parameters
        ----------
        bar_idx : int
            Current bar index (for the audit log).
        equity : float
            Current equity BEFORE this trade.
        requested_position : int
            Position the gated policy wants: -1, 0, or +1.

        Returns
        -------
        final_position : int
            Position actually allowed (may be forced to 0).
        vetoed : bool
            True if an intervention modified/overrode the request.
        """
        st = self.state

        # Track peak equity (first call sets it)
        if st.peak_equity == float("inf"):
            st.peak_equity = equity
        else:
            st.peak_equity = max(st.peak_equity, equity)

        drawdown = (
            (st.peak_equity - equity) / st.peak_equity if st.peak_equity > 0 else 0.0
        )

        # --- Circuit breaker with hysteresis ---
        if not st.halted and drawdown >= self.cfg.max_drawdown_pct:
            st.halted = True
            st.halt_reason = f"drawdown {drawdown:.1%} >= breaker {self.cfg.max_drawdown_pct:.1%}"
            self._log(bar_idx, "circuit_breaker", st.halt_reason)
        elif st.halted and drawdown <= self.cfg.resume_drawdown_pct:
            st.halted = False
            self._log(bar_idx, "circuit_breaker_resume",
                      f"drawdown recovered to {drawdown:.1%} <= {self.cfg.resume_drawdown_pct:.1%}")
            st.halt_reason = None

        # --- Consecutive-loss cooldown ---
        if st.cooldown_remaining > 0:
            st.cooldown_remaining -= 1
            if not st.halted:
                st.halted = True
                st.halt_reason = f"loss-streak cooldown ({st.cooldown_remaining} bars left)"
                # logged once when triggered (see report_trade)

        vetoed = False
        final_position = requested_position

        # --- Position limit ---
        if abs(final_position) > self.cfg.max_position:
            final_position = int(np_sign(final_position)) * self.cfg.max_position
            self._log(bar_idx, "position_limit",
                      f"|position| capped at {self.cfg.max_position}")
            vetoed = True

        # --- Halted: force flat ---
        if st.halted:
            if final_position != 0:
                vetoed = True
                reason = st.halt_reason or "halted"
                self._log(bar_idx, "forced_flat", f"{reason}; position {final_position} -> 0")
            final_position = 0

        return final_position, vetoed

    def report_trade(self, bar_idx: int, pnl: float) -> None:
        """Feed realized trade outcome into loss-streak tracking."""
        st = self.state
        if pnl < 0:
            st.consecutive_losses += 1
            if (
                st.consecutive_losses == self.cfg.consecutive_loss_limit
                and st.cooldown_remaining == 0
            ):
                st.halted = True
                st.cooldown_remaining = self.cfg.loss_cooldown_bars
                st.halt_reason = (
                    f"{st.consecutive_losses} consecutive losses; "
                    f"cooldown {self.cfg.loss_cooldown_bars} bars"
                )
                self._log(bar_idx, "loss_streak_halt", st.halt_reason)
        elif pnl > 0:
            st.consecutive_losses = 0

    def _log(self, bar_idx: int, kind: str, reason: str) -> None:
        self.interventions.append({
            "bar_idx": bar_idx,
            "kind": kind,
            "reason": reason,
        })

    @property
    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for iv in self.interventions:
            kinds[iv["kind"]] = kinds.get(iv["kind"], 0) + 1
        return {
            "n_interventions": len(self.interventions),
            "by_kind": kinds,
            "breaker_triggered": any(k == "circuit_breaker" for k in kinds),
            "final_halted": self.state.halted,
        }


def np_sign(x: int) -> int:
    return (x > 0) - (x < 0)
