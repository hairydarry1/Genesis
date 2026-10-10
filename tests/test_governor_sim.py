"""
tests/test_governor_sim.py — Tests for backtest/governor_sim.py.

Covers:
  - Normal trading never trips the governor.
  - Drawdown circuit breaker triggers at max DD and forces flat.
  - Hysteresis: stays halted until drawdown recovers below resume threshold.
  - Consecutive-loss halt fires at N losses, cooldown expires after M bars.
  - Position cap clamps oversized requests.
  - Summary reports interventions and breaker status.
"""

from __future__ import annotations

from backtest.governor_sim import Governor, GovernorConfig


def test_normal_trading_no_intervention():
    gov = Governor()
    equity = 10_000.0
    for bar in range(50):
        equity *= 1.001  # gentle uptrend
        pos, vetoed = gov.check(bar, equity, requested_position=1)
        assert pos == 1
        assert not vetoed
    assert not gov.state.halted
    assert len(gov.interventions) == 0


def test_circuit_breaker_triggers_and_forces_flat():
    cfg = GovernorConfig(max_drawdown_pct=0.10, resume_drawdown_pct=0.05)
    gov = Governor(cfg)

    # Build up peak
    gov.check(0, 10_000.0, 1)
    # Crash to >10% drawdown
    pos, vetoed = gov.check(1, 8_800.0, 1)
    assert gov.state.halted
    assert pos == 0
    assert vetoed
    kinds = [iv["kind"] for iv in gov.interventions]
    assert "circuit_breaker" in kinds
    assert "forced_flat" in kinds


def test_hysteresis_stays_halted_until_recovered():
    cfg = GovernorConfig(max_drawdown_pct=0.10, resume_drawdown_pct=0.05)
    gov = Governor(cfg)

    gov.check(0, 10_000.0, 1)
    gov.check(1, 8_800.0, 1)          # halt (12% dd)
    assert gov.state.halted

    # Partial bounce: still >5% dd -> must stay halted
    pos, _ = gov.check(2, 9_400.0, 1)   # 6% dd
    assert gov.state.halted
    assert pos == 0

    # Full recovery to <=5% dd -> resumes autonomy
    pos, vetoed = gov.check(3, 9_600.0, 1)   # 4% dd
    assert not gov.state.halted
    assert pos == 1
    assert not vetoed
    kinds = [iv["kind"] for iv in gov.interventions]
    assert "circuit_breaker_resume" in kinds


def test_consecutive_loss_halt_and_cooldown_expiry():
    cfg = GovernorConfig(consecutive_loss_limit=3, loss_cooldown_bars=5,
                         max_drawdown_pct=1.0)  # disable breaker
    gov = Governor(cfg)

    for i in range(2):
        gov.report_trade(i, pnl=-1.0)
    assert not gov.state.halted       # streak not yet at limit

    gov.report_trade(2, pnl=-1.0)     # third consecutive loss -> halt
    assert gov.state.halted
    assert "loss_streak_halt" in [iv["kind"] for iv in gov.interventions]

    # While cooling down, positions are forced flat
    pos, vetoed = gov.check(3, 10_000.0, 1)
    assert pos == 0 and vetoed

    # Burn through the cooldown (4 bars left after this check)
    for bar in range(4, 9):
        gov.check(bar, 10_000.0, 0)

    # Cooldown expired -> autonomy restored
    pos, vetoed = gov.check(9, 10_000.0, 1)
    assert pos == 1
    assert not vetoed


def test_win_resets_loss_streak():
    cfg = GovernorConfig(consecutive_loss_limit=3, loss_cooldown_bars=5)
    gov = Governor(cfg)
    for i in range(2):
        gov.report_trade(i, pnl=-1.0)
    gov.report_trade(2, pnl=+0.5)     # win resets streak
    gov.report_trade(3, pnl=-1.0)
    gov.report_trade(4, pnl=-1.0)
    assert not gov.state.halted       # only 2 consecutive losses now


def test_zero_pnl_does_not_reset_nor_extend_streak():
    cfg = GovernorConfig(consecutive_loss_limit=2, loss_cooldown_bars=5)
    gov = Governor(cfg)
    gov.report_trade(0, pnl=-1.0)
    gov.report_trade(1, pnl=0.0)
    gov.report_trade(2, pnl=-1.0)
    assert gov.state.halted           # strict <0 counts; 0-pnl ignored


def test_position_cap_clamps():
    gov = Governor(GovernorConfig(max_position=1))
    pos, vetoed = gov.check(0, 10_000.0, requested_position=3)
    assert pos == 1 and vetoed
    pos, vetoed = gov.check(1, 10_000.0, requested_position=-2)
    assert pos == -1 and vetoed
    assert sum(1 for iv in gov.interventions if iv["kind"] == "position_limit") == 2


def test_summary_shape():
    gov = Governor(GovernorConfig(max_drawdown_pct=0.05))
    gov.check(0, 10_000.0, 1)
    gov.check(1, 9_000.0, 1)          # breaker
    s = gov.summary
    assert s["n_interventions"] >= 1
    assert s["breaker_triggered"] is True
    assert s["final_halted"] is True
    assert "circuit_breaker" in s["by_kind"]
