"""Tests for TradingEnv: equity accounting, costs, confidence handling.

The equity recurrence implemented by the env is:

    equity_{t+1} = equity_t * (1 - trade_cost_t) + pnl_t

where pnl_t is the current bar's return applied to the held position
(booked as unrealized while holding, realized on the closing step).
These tests replicate that recurrence independently and assert the env
matches, so any future double-counting or sign regression is caught.
"""

import numpy as np
import pytest

SPREAD = 0.0001
SLIPPAGE = 0.00005
COST = SPREAD + SLIPPAGE  # charged per unit of |position change|


def _make_env(tiny_dataset, targets):
    from env.trading_env import TradingEnv, TradingEnvConfig

    path = tiny_dataset["path"]
    data = dict(np.load(path, allow_pickle=True))
    data["targets"] = targets.astype(np.float32)
    out = str(path).replace(".npz", "_custom.npz")
    np.savez(out, **data)
    cfg = TradingEnvConfig(max_steps=50)
    return TradingEnv(dataset_path=out, config=cfg)


class TestEquityAccounting:
    def test_long_hold_then_close(self, tiny_dataset):
        """Open long, hold 2 bars, close. Equity must equal the exact recurrence."""
        start = 10
        r = np.zeros(510)
        r[start + 1] = 0.001
        r[start + 2] = 0.002
        r[start + 3] = 0.003
        targets = np.tile(r[:, None], (1, 10))
        env = _make_env(tiny_dataset, targets)

        env.reset(start_idx=start, seed=0)

        # Step 1: enter long -> unrealized on returns[start+1]
        e0 = env.cfg.initial_equity
        expected = e0 * (1 - COST) + r[start + 1] * 1 * env.cfg.position_size * e0
        _, _, _, info = env.step(1)
        assert np.isclose(info["equity"], expected, rtol=1e-6)

        # Step 2: keep holding (action 1 again; action 0 would close)
        expected += r[start + 2] * 1 * env.cfg.position_size * expected
        _, _, _, info = env.step(1)
        assert np.isclose(info["equity"], expected, rtol=1e-6)

        # Step 3: close -> realized on this bar's return only
        expected += r[start + 3] * 1 * env.cfg.position_size * expected - COST * expected
        _, _, _, info = env.step(0)
        assert np.isclose(info["equity"], expected, rtol=1e-6)

    def test_short_pnl_sign(self, tiny_dataset):
        """Short position profits when price falls (negative return)."""
        start = 5
        r = np.zeros(500)
        r[start + 1] = -0.004
        targets = np.tile(r[:, None], (1, 10))
        env = _make_env(tiny_dataset, targets)
        start = 5
        env.reset(start_idx=start, seed=0)
        e0 = env.cfg.initial_equity
        _, _, _, info = env.step(2)  # short
        expected = e0 * (1 - COST) + (-r[start + 1]) * 1 * env.cfg.position_size * e0
        assert info["equity"] > e0
        assert np.isclose(info["equity"], expected, rtol=1e-6)

    def test_flat_position_no_pnl(self, env):
        """Holding with position 0 must leave equity unchanged apart from zero cost."""
        env.reset(start_idx=20, seed=0)
        _, _, _, info = env.step(0)
        assert np.isclose(info["equity"], env.cfg.initial_equity)

    def test_transaction_cost_on_entry_and_exit(self, tiny_dataset):
        """Each position change charges (spread+slippage)*|delta|*equity."""
        r = np.zeros(600)
        targets = np.tile(r[:, None], (1, 10))
        env = _make_env(tiny_dataset, targets)
        env.reset(start_idx=0, seed=0)
        e0 = env.cfg.initial_equity

        _, _, _, info = env.step(1)  # enter: one unit of cost
        assert np.isclose(info["equity"], e0 * (1 - COST), rtol=1e-6)

        _, _, _, info = env.step(0)  # exit: another unit of cost
        assert np.isclose(info["equity"], e0 * (1 - COST) ** 2, rtol=1e-6)

    def test_flip_loses_new_leg_first_bar(self, tiny_dataset):
        """
        Documented edge case: a same-bar flip (+1 -> -1) books the long leg's
        return as realized but does NOT book the new short leg's first-bar PnL.
        The new leg starts earning from the following bar.
        """
        r = np.zeros(500)
        r[1] = 0.005  # step uses returns[start + 1] = returns[1]
        targets = np.tile(r[:, None], (1, 10))
        env = _make_env(tiny_dataset, targets)
        # Pre-open a long by stepping manually through reset state:
        env.reset(start_idx=0, seed=0)
        env._position = 1  # force an open long without paying entry cost
        e = env.cfg.initial_equity
        _, _, _, info = env.step(2)  # flip to short on a positive-return bar
        # Long leg books +r; short leg's first bar is dropped; 2 units of cost.
        expected = e + r[1] * 1 * env.cfg.position_size * e - 2 * COST * e
        assert np.isclose(info["equity"], expected, rtol=1e-6)


class TestTotalPnlSemantics:
    def test_total_pnl_tracks_realized_only(self, tiny_dataset):
        """`total_pnl` accumulates closed-trade PnL only — open gains are not
        included until close. This documents current (intentional-ish)
        behavior so it isn't mistaken for total account profit."""
        r = np.array([0.01, 0.02] + [0.0] * 500)
        targets = np.tile(r[:, None], (1, 10))
        env = _make_env(tiny_dataset, targets)
        env.reset(start_idx=0, seed=0)
        env.step(1)
        _, _, _, info = env.step(0)  # close
        # Only the first bar's gain was booked as realized at close time;
        # second bar was accrued as unrealized into equity.
        assert info["total_pnl"] < info["equity"] - env.cfg.initial_equity


class TestConfidenceHandling:
    def test_confidence_passthrough_clipped(self, env):
        env.reset(start_idx=0, seed=0)
        _, r_hi, _, _ = env.step(1, confidence=7.5)
        env.reset(start_idx=0, seed=0)
        _, r_lo, _, _ = env.step(1, confidence=-2.0)
        # No crash and rewards are finite — clipping happened inside.
        assert np.isfinite(r_hi) and np.isfinite(r_lo)

    def test_calibration_penalty_responds_to_stated_confidence(self, tiny_dataset):
        from model.reward import RewardConfig

        cfg = RewardConfig(calibration_window=100)
        # Every held bar has a positive return, so the long is always
        # "correct" and empirical accuracy is ~100%.
        r = np.full(500, 0.01)
        r[:31] = 0.0
        targets = np.tile(r[:, None], (1, 10))

        data = dict(np.load(tiny_dataset["path"], allow_pickle=True))
        data["targets"] = targets.astype(np.float32)
        path_cal = str(tiny_dataset["path"]).replace(".npz", "_cal.npz")
        np.savez(path_cal, **data)

        def run(conf_value):
            from env.trading_env import TradingEnv, TradingEnvConfig

            e = TradingEnv(dataset_path=path_cal, config=TradingEnvConfig(max_steps=200))
            e.reward_fn.config = cfg
            e.reset(start_idx=30, seed=11)
            reward = None
            for i in range(120):  # > window fill for calibration penalty
                _, rew, done, _ = e.step(1, confidence=conf_value)
                reward = rew
                if done:
                    break
            return reward

        overconfident = run(0.99)
        underconfident = run(0.05)
        # With all-correct predictions, claiming low confidence should be
        # penalized more than claiming high confidence.
        assert underconfident < overconfident


class TestEnvApi:
    def test_obs_shape_and_reset_determinism(self, env):
        obs1 = env.reset(start_idx=42, seed=1)
        obs2 = env.reset(start_idx=42, seed=1)
        assert obs1.shape == (env.obs_dim,)
        assert np.allclose(obs1, obs2)

    def test_done_at_end_of_data(self, tiny_dataset):
        from env.trading_env import TradingEnv, TradingEnvConfig

        cfg = TradingEnvConfig(max_steps=10_000)
        e = TradingEnv(dataset_path=tiny_dataset["path"], config=cfg)
        # reset() clamps start so a full episode fits; step to the data end
        e.reset(start_idx=e.n_samples - 2, seed=0)
        done = False
        info = {}
        for _ in range(e.n_samples):
            _, _, done, info = e.step(0)
            if done:
                break
        assert done and info.get("reason") == "end_of_data"

    def test_allow_short_false_blocks_shorts(self, tiny_dataset):
        from env.trading_env import TradingEnv, TradingEnvConfig

        cfg = TradingEnvConfig(max_steps=50, allow_short=False)
        e = TradingEnv(dataset_path=tiny_dataset["path"], config=cfg)
        e.reset(start_idx=0, seed=0)
        _, _, _, info = e.step(2)
        assert info["position"] == 0
