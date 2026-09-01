"""
eval/live_inference.py — Live inference pipeline for the world model.

Connects to Deriv websocket, maintains a rolling candle buffer,
computes features via StateBuilder, and runs model predictions in real-time.

Usage:
    python -m eval.live_inference
    python -m eval.live_inference --symbol R_75 --history-bars 500
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import structlog
import torch

from data.deriv_connection import DerivConnection
from features.state import StateBuilder
from model.world_model import build_model_from_features, WorldModel

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SYMBOL = "R_75"
DEFAULT_HISTORY_BARS = 500   # enough for all feature lookbacks
DEFAULT_CHECKPOINT = "checkpoints/world_model_best.pt"
DEFAULT_STRUCTURES = "data/structures_R75.parquet"
HORIZON = 10  # must match training
DEFAULT_LOG_DIR = "checkpoints/predictions"


# ---------------------------------------------------------------------------
# Model loader
# ---------------------------------------------------------------------------

def load_model(checkpoint_path: str, device: torch.device) -> tuple[WorldModel, list[str], dict, float]:
    """Load trained model from checkpoint.

    Returns (model, feature_names, norm_stats, temperature).
    Temperature defaults to 1.0 if not saved in checkpoint.
    """
    ckpt = torch.load(checkpoint_path, map_location=device, weights_only=False)
    feature_names = [str(f) for f in ckpt["feature_names"]]
    model = build_model_from_features(
        feature_names, horizon=ckpt["horizon"],
        hidden_dim=32, branch_depth=2, fusion_depth=2,
    )
    model.load_state_dict(ckpt["model_state_dict"])
    model = model.to(device)
    model.eval()

    # Load normalization stats if present
    norm_stats = {}
    if "feat_mean" in ckpt and "feat_std" in ckpt:
        norm_stats["feat_mean"] = ckpt["feat_mean"]
        norm_stats["feat_std"] = ckpt["feat_std"]
        logger.info("normalization.loaded mean_range=[%.4f, %.4f] std_range=[%.6f, %.6f]",
                    float(ckpt["feat_mean"].min()), float(ckpt["feat_mean"].max()),
                    float(ckpt["feat_std"].min()), float(ckpt["feat_std"].max()))

    # Load temperature scaling factor
    temperature = float(ckpt.get("temperature", 1.0))
    if temperature != 1.0:
        logger.info("temperature.loaded T=%.4f (model was %sconfident)",
                    temperature, "over" if temperature > 1.0 else "under")

    n_params = sum(p.numel() for p in model.parameters())
    logger.info(
        "model.loaded checkpoint=%s epoch=%d features=%d params=%d temperature=%.4f",
        checkpoint_path, ckpt.get("epoch", -1), len(feature_names), n_params, temperature,
    )
    return model, feature_names, norm_stats, temperature


# ---------------------------------------------------------------------------
# Prediction logger
# ---------------------------------------------------------------------------

class PredictionLogger:
    """
    Logs predictions and actual outcomes to JSONL files.

    Each prediction record contains:
      - prediction_id, timestamp, price, means, variances
      - actual_price at each future step (filled in later)
      - direction accuracy, error metrics (filled in later)
    """

    def __init__(self, log_dir: str = DEFAULT_LOG_DIR) -> None:
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self.pred_path = self.log_dir / f"predictions_{today}.jsonl"
        self.pending: dict[int, dict] = {}  # prediction_id -> record
        self.pred_id_counter = 0
        logger.info("prediction_logger.init path=%s", self.pred_path)

    def log_prediction(
        self,
        epoch: int,
        price: float,
        means: np.ndarray,
        variances: np.ndarray,
        feature_vec: np.ndarray | None = None,
    ) -> int:
        """
        Log a prediction. Returns the prediction_id for later outcome matching.
        """
        self.pred_id_counter += 1
        pid = self.pred_id_counter

        record = {
            "prediction_id": pid,
            "epoch": epoch,
            "timestamp": datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat(),
            "price": float(price),
            "means": means.tolist(),
            "variances": variances.tolist(),
            "stds": np.sqrt(variances).tolist(),
            "horizon": len(means),
            # Will be filled in later:
            "actual_returns": None,
            "actual_prices": None,
            "direction_correct": None,
            "error_mae": None,
            "error_rmse": None,
            "coverage_1sigma": None,
            "coverage_2sigma": None,
        }

        self.pending[epoch] = record

        # Append to file
        with open(self.pred_path, "a") as f:
            f.write(json.dumps(record, default=str) + "\n")

        return pid

    def fill_outcomes(self, candle_buffer: CandleBuffer) -> int:
        """
        Match pending predictions with actual outcomes from the candle buffer.

        For each pending prediction, look ahead in the buffer to get actual
        prices at each future step.

        Returns the number of predictions matched.
        """
        matched = 0
        to_remove = []

        # Merge file records into pending if not already there
        self._load_pending_from_file()

        for pred_epoch, record in self.pending.items():
            # Find where this prediction's candle is in the buffer
            candle_idx = None
            for i, c in enumerate(candle_buffer.candles):
                if c["epoch"] == pred_epoch:
                    candle_idx = i
                    break

            if candle_idx is None:
                continue

            horizon = record["horizon"]
            # Check if we have enough future candles
            if candle_idx + horizon >= len(candle_buffer.candles):
                continue

            # Get actual prices at each future step
            entry_price = record["price"]
            actual_prices = []
            actual_returns = []

            for h in range(1, horizon + 1):
                future_price = candle_buffer.candles[candle_idx + h]["close"]
                actual_prices.append(future_price)
                actual_returns.append(np.log(future_price / entry_price) if entry_price > 0 else 0.0)

            actual_returns = np.array(actual_returns)
            means = np.array(record["means"])
            stds = np.array(record["stds"])

            # Compute metrics
            errors = actual_returns - means
            direction_correct = bool(np.sign(means[0]) == np.sign(actual_returns[0])) if actual_returns[0] != 0 else None

            # Coverage
            within_1s = np.mean(np.abs(errors) <= stds)
            within_2s = np.mean(np.abs(errors) <= 2 * stds)

            # Update record
            record["actual_returns"] = actual_returns.tolist()
            record["actual_prices"] = actual_prices
            record["direction_correct"] = direction_correct
            record["error_mae"] = float(np.mean(np.abs(errors)))
            record["error_rmse"] = float(np.sqrt(np.mean(errors**2)))
            record["coverage_1sigma"] = float(within_1s)
            record["coverage_2sigma"] = float(within_2s)

            to_remove.append(pred_epoch)
            matched += 1

            # Log outcome
            dir_str = "CORRECT" if direction_correct else "WRONG" if direction_correct is not None else "TIE"
            logger.info(
                "prediction.outcome pred_epoch=%d price=%.2f actual_move=%+.6f predicted=%+.6f %s",
                pred_epoch, entry_price, actual_returns[0], means[0], dir_str,
            )

        # Rewrite file BEFORE removing from pending so updated records are saved
        self._rewrite_file()

        # Now remove matched predictions from pending
        for epoch in to_remove:
            del self.pending[epoch]

        return matched

    def _load_pending_from_file(self) -> None:
        """Load predictions without outcomes from the file into self.pending."""
        if not self.pred_path.exists():
            return
        with open(self.pred_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                epoch = rec.get("epoch")
                if epoch is None:
                    continue
                # Only load if: (a) not already in pending, (b) no outcome yet
                if epoch not in self.pending and rec.get("actual_returns") is None:
                    self.pending[epoch] = rec
                    pid = rec.get("prediction_id", 0)
                    if pid >= self.pred_id_counter:
                        self.pred_id_counter = pid

    def _rewrite_file(self) -> None:
        """Rewrite the log file with all records (including updated outcomes)."""
        all_records = []
        # Read existing non-pending records
        if self.pred_path.exists():
            with open(self.pred_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rec = json.loads(line)
                            pid = rec.get("prediction_id")
                            epoch = rec.get("epoch")
                            # If this is a pending prediction, use the updated version
                            if epoch in self.pending:
                                all_records.append(self.pending[epoch])
                            elif pid and not any(r.get("prediction_id") == pid for r in all_records):
                                all_records.append(rec)
                        except json.JSONDecodeError:
                            pass

        # Add any pending records not yet in file
        for epoch, rec in self.pending.items():
            if not any(r.get("prediction_id") == rec["prediction_id"] for r in all_records):
                all_records.append(rec)

        with open(self.pred_path, "w") as f:
            for rec in all_records:
                f.write(json.dumps(rec, default=str) + "\n")

    def get_accuracy_summary(self) -> dict:
        """Get accuracy statistics from all logged predictions with outcomes."""
        records = []
        if self.pred_path.exists():
            with open(self.pred_path) as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

        matched = [r for r in records if r.get("actual_returns") is not None]
        if not matched:
            return {"total": len(records), "matched": 0, "accuracy": None}

        dir_correct = [r for r in matched if r.get("direction_correct") is True]
        dir_wrong = [r for r in matched if r.get("direction_correct") is False]
        mae = np.mean([r["error_mae"] for r in matched])
        rmse = np.mean([r["error_rmse"] for r in matched])
        cov1 = np.mean([r["coverage_1sigma"] for r in matched])
        cov2 = np.mean([r["coverage_2sigma"] for r in matched])

        return {
            "total": len(records),
            "matched": len(matched),
            "pending": len(records) - len(matched),
            "direction_accuracy": len(dir_correct) / len(matched) if matched else 0,
            "direction_correct": len(dir_correct),
            "direction_wrong": len(dir_wrong),
            "mean_mae": float(mae),
            "mean_rmse": float(rmse),
            "mean_coverage_1sigma": float(cov1),
            "mean_coverage_2sigma": float(cov2),
        }


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------

def compute_features(
    state_builder: StateBuilder,
    model_feature_names: list[str],
    timestamp: pd.Timestamp,
    norm_stats: dict | None = None,
) -> np.ndarray | None:
    """
    Compute feature vector from current candle state, ordered to match
    the model's expected feature names.

    Applies z-score normalization if norm_stats is provided.

    Returns None if features can't be computed.
    """
    try:
        state_dict = state_builder.build_state(timestamp)
    except Exception as e:
        logger.warning("features.build_error", error=str(e))
        return None

    # Order features to match the model's expected input
    feature_vec = np.zeros(len(model_feature_names), dtype=np.float32)
    for i, name in enumerate(model_feature_names):
        if name in state_dict:
            val = state_dict[name]
            if np.isfinite(val):
                feature_vec[i] = val
            # NaN/Inf → 0 (same as training)

    # Apply z-score normalization if stats are available
    if norm_stats and "feat_mean" in norm_stats and "feat_std" in norm_stats:
        feat_mean = norm_stats["feat_mean"]
        feat_std = norm_stats["feat_std"]
        if len(feat_mean) == len(feature_vec):
            feature_vec = (feature_vec - feat_mean) / feat_std

    return feature_vec


# ---------------------------------------------------------------------------
# Prediction display
# ---------------------------------------------------------------------------

def format_prediction(
    means: np.ndarray,
    variances: np.ndarray,
    current_price: float,
    horizon: int,
    dt: datetime | None = None,
) -> str:
    """Format prediction as a readable terminal output."""
    stds = np.sqrt(variances)

    lines = []
    ts = dt.strftime("%H:%M:%S") if dt else datetime.now(timezone.utc).strftime("%H:%M:%S")
    lines.append(f"\n{'=' * 65}")
    lines.append(f"  PREDICTION @ {ts}  |  Price: {current_price:.2f}")
    lines.append(f"{'=' * 65}")
    lines.append(f"  {'Step':>4s}  {'Mean':>8s}  {'Std':>8s}  {'95% CI':>20s}  {'Direction':>10s}")
    lines.append(f"  {'-'*4:>4s}  {'-'*8:>8s}  {'-'*8:>8s}  {'-'*20:>20s}  {'-'*10:>10s}")

    for h in range(horizon):
        m = means[h]
        s = stds[h]
        # Convert log returns to price levels
        price_move = current_price * (np.exp(m) - 1)
        price_low = current_price * np.exp(m - 1.96 * s)
        price_high = current_price * np.exp(m + 1.96 * s)

        # Direction
        if m > 1.96 * s:
            direction = "UP (sig)"
        elif m < -1.96 * s:
            direction = "DOWN (sig)"
        elif m > 0:
            direction = "UP"
        elif m < 0:
            direction = "DOWN"
        else:
            direction = "FLAT"

        lines.append(
            f"  {h+1:4d}  {m:+8.6f}  {s:8.6f}  "
            f"[{price_low:8.2f}, {price_high:8.2f}]  {direction:>10s}"
        )

    # Aggregate prediction
    total_mean = np.sum(means)
    total_var = np.sum(variances)
    total_std = np.sqrt(total_var)
    total_move = current_price * (np.exp(total_mean) - 1)
    ci_low = current_price * np.exp(total_mean - 1.96 * total_std)
    ci_high = current_price * np.exp(total_mean + 1.96 * total_std)

    lines.append(f"  {'-'*61}")
    lines.append(
        f"  TOTAL ({horizon} steps): "
        f"mean={total_mean:+.6f} ({total_move:+.2f})  "
        f"95% CI=[{ci_low:.2f}, {ci_high:.2f}]"
    )

    # Confidence summary
    if abs(total_mean) > 1.96 * total_std:
        conf = "HIGH"
    elif abs(total_mean) > 1.0 * total_std:
        conf = "MEDIUM"
    else:
        conf = "LOW"
    lines.append(f"  Confidence: {conf} (|mean|/std = {abs(total_mean)/total_std:.2f})")
    lines.append(f"{'=' * 65}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Candle buffer
# ---------------------------------------------------------------------------

class CandleBuffer:
    """Rolling buffer of 1-min candles for feature computation."""

    def __init__(self, max_size: int = 2000) -> None:
        self.max_size = max_size
        self.candles: list[dict] = []

    def add_candle(self, candle: dict) -> None:
        """Add a candle (dict with epoch, open, high, low, close, volume)."""
        self.candles.append(candle)
        if len(self.candles) > self.max_size:
            self.candles = self.candles[-self.max_size:]

    def to_dataframe(self) -> pd.DataFrame:
        """Convert to DataFrame for StateBuilder."""
        if not self.candles:
            return pd.DataFrame()
        df = pd.DataFrame(self.candles)
        if "timestamp" not in df.columns:
            df["timestamp"] = pd.to_datetime(df["epoch"], unit="s", utc=True).dt.tz_localize(None)
        df = df.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        return df

    @property
    def last_price(self) -> float:
        return self.candles[-1]["close"] if self.candles else 0.0

    @property
    def last_timestamp(self) -> pd.Timestamp:
        if self.candles:
            return pd.Timestamp(self.candles[-1]["epoch"], unit="s", tz="UTC").tz_localize(None)
        return pd.Timestamp.now(tz="UTC").tz_localize(None)

    def __len__(self) -> int:
        return len(self.candles)


# ---------------------------------------------------------------------------
# Live inference engine
# ---------------------------------------------------------------------------

class LiveInference:
    """
    Connects to Deriv, fetches history, subscribes to live candles,
    and runs model inference on each new candle.
    """

    def __init__(
        self,
        symbol: str = DEFAULT_SYMBOL,
        checkpoint: str = DEFAULT_CHECKPOINT,
        structures_path: str = DEFAULT_STRUCTURES,
        history_bars: int = DEFAULT_HISTORY_BARS,
        device_str: str | None = None,
    ) -> None:
        self.symbol = symbol
        self.checkpoint = checkpoint
        self.structures_path = structures_path
        self.history_bars = history_bars
        self.device = torch.device(
            device_str if device_str else ("cuda" if torch.cuda.is_available() else "cpu")
        )

        self.model: WorldModel | None = None
        self.model_feature_names: list[str] = []
        self.norm_stats: dict = {}
        self.temperature: float = 1.0
        self.buffer = CandleBuffer(max_size=2000)
        self.state_builder: StateBuilder | None = None
        self.sb_rebuild_interval = 50  # rebuild StateBuilder every N candles
        self.candles_since_rebuild = 0
        self.prediction_count = 0
        self.pred_logger = PredictionLogger()

    async def fetch_history(self, conn: DerivConnection) -> None:
        """Fetch historical 1-min candles to bootstrap the buffer."""
        logger.info("fetching_history bars=%d symbol=%s", self.history_bars, self.symbol)

        resp = await conn.get_ticks_history(
            self.symbol,
            style="candles",
            count=min(self.history_bars, 1000),
            granularity=60,  # 1-min candles
        )

        candles = resp.get("candles", [])
        if not candles:
            logger.error("no_candles_returned")
            return

        for c in candles:
            self.buffer.add_candle({
                "epoch": c["epoch"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c.get("volume", 0)),
            })

        logger.info(
            "history_fetched n=%d first=%s last=%s",
            len(candles),
            self.buffer.candles[0]["epoch"] if self.buffer.candles else "?",
            self.buffer.candles[-1]["epoch"] if self.buffer.candles else "?",
        )

    def rebuild_state_builder(self) -> None:
        """Rebuild StateBuilder from current candle buffer."""
        t0 = time.time()
        df = self.buffer.to_dataframe()
        if len(df) < 100:
            logger.warning("insufficient_candles_for_state_builder n=%d", len(df))
            return

        structs_path = self.structures_path if Path(self.structures_path).exists() else None
        self.state_builder = StateBuilder(df, structures_path=structs_path)
        self.candles_since_rebuild = 0
        elapsed = time.time() - t0
        logger.info("state_builder.rebuilt n_candles=%d elapsed=%.2fs", len(df), elapsed)

    def run_prediction(self) -> str | None:
        """Run model inference on the current state. Returns formatted prediction or None."""
        if self.model is None or self.state_builder is None:
            return None

        try:
            timestamp = self.buffer.last_timestamp
            current_price = self.buffer.last_price

            # Compute features
            feature_vec = compute_features(
                self.state_builder, self.model_feature_names, timestamp,
                norm_stats=self.norm_stats,
            )
            if feature_vec is None:
                return None

            # Run inference
            with torch.no_grad():
                x = torch.tensor(feature_vec, dtype=torch.float32).unsqueeze(0).to(self.device)
                mean, log_var = self.model(x)
                means = mean.cpu().numpy().flatten()
                variances = torch.exp(log_var).cpu().numpy().flatten()

            # Apply temperature scaling for calibrated uncertainty
            # T > 1 means model was overconfident (var too small)
            variances = variances * self.temperature

            self.prediction_count += 1

            # Log prediction (with temperature-corrected variances)
            candle_epoch = self.buffer.candles[-1]["epoch"]
            self.pred_logger.log_prediction(
                epoch=candle_epoch,
                price=current_price,
                means=means,
                variances=variances,
            )

            # Try to fill outcomes for older predictions
            n_matched = self.pred_logger.fill_outcomes(self.buffer)
            if n_matched > 0:
                summary = self.pred_logger.get_accuracy_summary()
                logger.info(
                    "accuracy_update matched=%d direction_acc=%.1f%% mae=%.6f",
                    summary["matched"],
                    summary.get("direction_accuracy", 0) * 100,
                    summary.get("mean_mae", 0),
                )

            # Format output
            dt = datetime.fromtimestamp(self.buffer.candles[-1]["epoch"], tz=timezone.utc)
            return format_prediction(means, variances, current_price, HORIZON, dt)

        except Exception as e:
            logger.error("prediction_error", error=str(e))
            return None

    def handle_candle(self, msg: dict) -> None:
        """Handle a new candle from the live stream (sync callback)."""
        # The candle arrives as a confirmation: {"candle": {...}, "subscription": {...}}
        candle = msg.get("candle", {})
        if not candle:
            return

        self.buffer.add_candle({
            "epoch": candle["epoch"],
            "open": float(candle["open"]),
            "high": float(candle["high"]),
            "low": float(candle["low"]),
            "close": float(candle["close"]),
            "volume": float(candle.get("volume", 0)),
        })

        self.candles_since_rebuild += 1

        # Rebuild StateBuilder periodically
        if self.candles_since_rebuild >= self.sb_rebuild_interval:
            self.rebuild_state_builder()

        # Run prediction
        output = self.run_prediction()
        if output:
            print(output)

    # Tick-based candle aggregation
    _current_candle_epoch: int = 0
    _current_candle: dict | None = None
    _tick_count: int = 0

    def handle_tick(self, msg: dict) -> None:
        """Handle a tick from the live stream — aggregate into 1-min candles."""
        tick = msg.get("tick", {})
        if not tick:
            return

        quote = float(tick.get("quote", 0))
        epoch = int(tick.get("epoch", 0))
        if quote <= 0 or epoch <= 0:
            return

        self._tick_count += 1

        # Which 1-min candle does this tick belong to?
        candle_epoch = (epoch // 60) * 60  # floor to 1-min boundary

        if candle_epoch != self._current_candle_epoch:
            # New candle started — finalize the previous one
            if self._current_candle is not None and self._current_candle["epoch"] > 0:
                self.buffer.add_candle(self._current_candle)
                self.candles_since_rebuild += 1
                if self.candles_since_rebuild >= self.sb_rebuild_interval:
                    self.rebuild_state_builder()
                output = self.run_prediction()
                if output:
                    print(output)
                else:
                    logger.debug("tick.candle_no_prediction", epoch=self._current_candle["epoch"])

            # Start new candle
            self._current_candle_epoch = candle_epoch
            self._current_candle = {
                "epoch": candle_epoch,
                "open": quote,
                "high": quote,
                "low": quote,
                "close": quote,
                "volume": 0,
            }
        else:
            # Update current candle
            if self._current_candle is not None:
                self._current_candle["high"] = max(self._current_candle["high"], quote)
                self._current_candle["low"] = min(self._current_candle["low"], quote)
                self._current_candle["close"] = quote

    async def run(self) -> None:
        """Main run loop — connect, fetch history, subscribe to live ticks."""
        # Load model (with temperature for calibrated uncertainty)
        self.model, self.model_feature_names, self.norm_stats, self.temperature = load_model(self.checkpoint, self.device)

        # Connect to Deriv
        conn = DerivConnection()
        await conn.connect()
        logger.info("connected_to_deriv")

        try:
            # Fetch history
            await self.fetch_history(conn)

            # Build initial StateBuilder
            self.rebuild_state_builder()

            # Run initial prediction
            output = self.run_prediction()
            if output:
                print(output)

            # Subscribe to live tick stream
            logger.info("subscribing_to_live_ticks symbol=%s", self.symbol)
            sub_id = await conn.subscribe(
                {"ticks": self.symbol},
                handler=self.handle_tick,
            )
            logger.info("live_subscription_active sub_id=%s", sub_id)

            # Keep running
            logger.info("live_inference_active waiting_for_ticks...")
            while True:
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            logger.info("interrupted_by_user")
        finally:
            await conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Live inference pipeline")
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--checkpoint", default=DEFAULT_CHECKPOINT)
    parser.add_argument("--structures", default=DEFAULT_STRUCTURES)
    parser.add_argument("--history-bars", type=int, default=DEFAULT_HISTORY_BARS)
    parser.add_argument("--device", default=None)
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    engine = LiveInference(
        symbol=args.symbol,
        checkpoint=args.checkpoint,
        structures_path=args.structures,
        history_bars=args.history_bars,
        device_str=args.device,
    )
    try:
        asyncio.run(engine.run())
    except KeyboardInterrupt:
        pass
    finally:
        # Print accuracy summary on exit
        summary = engine.pred_logger.get_accuracy_summary()
        print(f"\n{'=' * 50}")
        print(f"PREDICTION LOG SUMMARY")
        print(f"{'=' * 50}")
        print(f"  Log file: {engine.pred_logger.pred_path}")
        print(f"  Total predictions: {summary['total']}")
        print(f"  Matched with outcomes: {summary['matched']}")
        print(f"  Pending: {summary['pending']}")
        if summary['matched'] > 0:
            print(f"  Direction accuracy: {summary['direction_accuracy']:.1%}")
            print(f"  Correct: {summary['direction_correct']}, Wrong: {summary['direction_wrong']}")
            print(f"  Mean MAE: {summary['mean_mae']:.6f}")
            print(f"  Mean RMSE: {summary['mean_rmse']:.6f}")
            print(f"  Coverage 1sig: {summary['mean_coverage_1sigma']:.1%}")
            print(f"  Coverage 2sig: {summary['mean_coverage_2sigma']:.1%}")
        print(f"{'=' * 50}")


if __name__ == "__main__":
    main()
