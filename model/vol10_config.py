"""
model/vol10_config.py — Production configuration for VOL10 trading.

All settings in one place. Edit this file to tune the strategy.
"""

# ---------------------------------------------------------------------------
# World Model
# ---------------------------------------------------------------------------
WORLD_MODEL_PATH = "checkpoints_vol10/world_model_best.pt"
MODEL_TYPE = "tiny"           # tiny (516 params) or wide (2132 params)
N_FEATURES = 8
HORIZON = 10                  # Predict next 10 bars

# ---------------------------------------------------------------------------
# Strategy Parameters (optimized via sweep)
# ---------------------------------------------------------------------------
SNR_THRESHOLD = 0.30          # Minimum SNR to enter a trade
TAKE_PROFIT_PCT = 0.0030      # 0.30% take profit
STOP_LOSS_PCT = 0.0010        # 0.10% stop loss (3:1 reward:risk)
MAX_HOLD_BARS = 60            # 60 minutes max hold
SIGNAL_REVERSAL_THRESHOLD = 0.0005  # Exit if mean reverses by this much

# ---------------------------------------------------------------------------
# Risk Management
# ---------------------------------------------------------------------------
INITIAL_EQUITY = 10000.0
MAX_POSITION_SIZE = 1.0       # Full equity per trade
MAX_DRAWDOWN_STOP = 0.20      # Stop trading if DD > 20%
COOLDOWN_BARS = 5             # Wait 5 bars after a loss before re-entry

# ---------------------------------------------------------------------------
# Transaction Costs (realistic for synthetic indices)
# ---------------------------------------------------------------------------
SPREAD_COST = 0.0001          # 0.01% per side
SLIPPAGE_COST = 0.00005       # 0.005% per side
TOTAL_COST_PER_TRADE = SPREAD_COST + SLIPPAGE_COST  # 0.03% round-trip

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
SYMBOL = "R_10"               # Deriv symbol for Volatility 10
DATASET_PATH = "data/dataset_volatility_10_h10.npz"
HISTORY_BARS = 500            # Candles to fetch on startup
CANDLE_GRANULARITY = 60       # 1-minute candles

# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------
TRADE_LOG_PATH = "checkpoints_vol10/paper_trades.json"
STATUS_INTERVAL = 1           # Print status every N candles
SUMMARY_INTERVAL = 10         # Print portfolio summary every N candles
