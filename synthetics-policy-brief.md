# Synthetics Brain — Policy/Value Training Milestone (Build Brief)

**Precondition:** The world-model validation milestone (see `synthetics-world-model-brief.md`) has passed its gate for the target synthetic index. Do not start this milestone otherwise.

**Scope:** Train the policy/value component that decides actions (buy/sell/hold/size) using the validated world model's imagined rollouts plus direct environment experience. Paper-trading and live execution are NOT part of this milestone.

---

## 1. Reward function
**Module: `model/reward.py`**
- Implement the layered reward described in planning:
  - Risk-adjusted return (PnL scaled by rolling volatility) as the base term
  - Drawdown penalty, scaling with depth of current drawdown
  - Calibration penalty — penalizes the gap between the agent's stated confidence and its actual empirical accuracy over a rolling window
  - Transaction cost (spread/slippage) applied at every simulated trade, not just at evaluation
- Expose each term's weight as a configurable hyperparameter (not hardcoded) — you will need to tune these
- Decide and document: per-timestep reward (dense, includes unrealized PnL) vs per-trade only (sparse). Recommendation: per-timestep with realized PnL dominant at trade close.
- **Sanity check:** run the reward function against a few hand-constructed synthetic trade sequences (a clean winner, a big risky win, a slow bleed, a disciplined small loss) and confirm the reward ranks them the way you'd want a disciplined trader to be ranked — not just by raw PnL.

## 2. Structural detection layer
**Module: `features/structures.py`** — should already be complete from the world-model milestone (section 6 of that brief): swing-point utility, deterministic quality filter (per-instrument calibrated thresholds), and the full detector set (FVG, iFVG, order blocks, liquidity sweeps, BOS, CHoCH, EQH/EQL, premium/discount, mitigation blocks). If any of this wasn't finished in the prior milestone, finish it here before proceeding — the policy needs the full structural feature set, not just FVG/iFVG.
- **Sanity check (if not already done):** manually verify detected structures against a plotted chart for a known window — confirm no systematic off-by-one errors and that the quality filter isn't dropping obviously real structures or letting through obvious noise

## 3. Policy/value network
**Module: `model/policy.py`**
- Architecture: multi-branch encoder (reuse world model's timeframe branches where possible) → fusion layer → policy head (action distribution: buy/sell/hold + size) → value head (expected return) → confidence/calibration head (separate output, not just softmax confidence — softmax confidence is notoriously poorly calibrated)
- Training approach: use the validated world model to generate imagined rollouts (short-horizon "what if I take this action" simulations) as additional training signal, combined with direct experience replay from the real/historical environment
- **This is the highest-risk module technically** — keep the first version simple (fewer layers, smaller action space) and get the full training loop working end-to-end before adding complexity

## 4. Training environment
**Module: `env/trading_env.py`**
- Wraps the historical data + world model into an environment the policy can interact with (OpenAI Gym-style interface is a reasonable convention)
- Applies the reward function from section 1 at each step
- Enforces realistic constraints: spread, slippage, position limits (even in training, so the policy doesn't learn to exploit an unrealistic environment)
- Consider reusing/adapting relevant pieces from FinRL (AI4Finance-Foundation/FinRL) rather than building fully from scratch

## 5. Overfitting stress-tests (separate from world-model's own validation)
**Module: `eval/policy_validation.py`**
- RL agents can memorize entire training sequences, not just patterns — this is a more severe failure mode than typical supervised overfitting. Explicitly test for it:
  - Evaluate on walk-forward out-of-sample splits (reuse `data/splits.py`)
  - Check performance doesn't collapse when evaluated on a held-out period the agent never saw, even indirectly through the world model's training
  - Track calibration (confidence vs actual accuracy) on out-of-sample data specifically — this is the number that will later gate autonomy eligibility
- **Stricter final pass (before this brain is trusted with paper trading):** Combinatorial Purged Cross-Validation (CPCV) + Deflated Sharpe Ratio, per the research findings — catches overfitting that simple walk-forward can miss

## 6. Pass criteria (lock in before training, not after seeing results)
- Beats a random policy and a simple heuristic baseline (e.g. "trade FVG mitigations naively") out-of-sample
- Calibration holds up out-of-sample: stated confidence roughly matches actual win rate across confidence buckets
- No catastrophic drawdown in any individual walk-forward fold, even if average performance looks good
- Passes the CPCV + Deflated Sharpe stress-test before moving toward paper trading

## 7. Explicitly out of scope for this milestone
- Paper trading / live execution
- The governor/risk-cage layer (comes after this, wraps the policy's output)
- Gold or forex brains — synthetics only

## 8. Suggested Freebuff session breakdown
1. Session A: `model/reward.py` — build and hand-verify against constructed trade sequences
2. Session B: `features/structures.py` (if not already done) — FVG/iFVG detector + chart-verification
3. Session C: `env/trading_env.py` — training environment, check FinRL for reusable pieces first
4. Session D: `model/policy.py` — start simple, get the training loop running end-to-end
5. Session E: `eval/policy_validation.py` — walk-forward + overfitting stress-tests
6. Session F: review results together against section 6 criteria — go/no-go before paper trading