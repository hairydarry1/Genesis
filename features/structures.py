"""
features/structures.py — Structural detection layer for synthetic indices.

Three sub-layers:
  6a. Swing-point detector (shared utility — everything depends on it)
  6b. Deterministic quality filter (housekeeping, not the brain's judgment)
  6c. Structure detectors (consume swing points + quality filter)

Each structure instance outputs a standardised dict:
  type, zone (high, low), size_ATR, age_bars, mitigated, distance, confluence

Division of labor:
  - Quality filter decides whether something is structurally REAL → logged or dropped
  - World model / policy (later) decide whether a real instance is RELEVANT → learned

Usage:
    from features.structures import detect_structures, StructureInstance

    bars_1m = ...  # OHLCV DataFrame
    structures = detect_structures(bars_1m, atr_period=14, instrument_cfg={})
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# 6a. Swing-point detector
# ---------------------------------------------------------------------------

@dataclass
class SwingPoint:
    """A detected swing high or swing low."""
    index: int          # integer position in the bars DataFrame
    timestamp: pd.Timestamp
    price: float
    kind: str           # "high" or "low"

    def __repr__(self) -> str:
        return f"Swing{self.kind.capitalize()}(idx={self.index}, price={self.price:.2f})"


def detect_swing_points(
    bars: pd.DataFrame,
    lookback: int = 5,
) -> list[SwingPoint]:
    """
    Identify local swing highs/lows.

    A swing high at position i requires bars[i].high to be the highest high
    among the `lookback` bars on each side.  Similarly for swing lows.

    Parameters
    ----------
    bars : pd.DataFrame
        Must have columns: ['timestamp', 'high', 'low'].
    lookback : int
        Minimum number of bars on each side to confirm a swing point.
        Larger → fewer but more significant swings.

    Returns
    -------
    list[SwingPoint] sorted by index.
    """
    if len(bars) < 2 * lookback + 1:
        return []

    highs = bars["high"].values
    lows = bars["low"].values
    timestamps = bars["timestamp"].values

    swings: list[SwingPoint] = []

    for i in range(lookback, len(bars) - lookback):
        window_highs = highs[i - lookback: i + lookback + 1]
        window_lows = lows[i - lookback: i + lookback + 1]

        # Swing high: current high is strictly the max in the window
        if highs[i] == np.max(window_highs) and highs[i] > np.max(window_highs[:lookback]) and highs[i] > np.max(window_highs[lookback + 1:]):
            swings.append(SwingPoint(
                index=i,
                timestamp=pd.Timestamp(timestamps[i]),
                price=float(highs[i]),
                kind="high",
            ))

        # Swing low: current low is strictly the min in the window
        if lows[i] == np.min(window_lows) and lows[i] < np.min(window_lows[:lookback]) and lows[i] < np.min(window_lows[lookback + 1:]):
            swings.append(SwingPoint(
                index=i,
                timestamp=pd.Timestamp(timestamps[i]),
                price=float(lows[i]),
                kind="low",
            ))

    swings.sort(key=lambda s: s.index)

    logger.debug(
        "structures.swing_points",
        count=len(swings),
        lookback=lookback,
        n_bars=len(bars),
    )
    return swings


# ---------------------------------------------------------------------------
# 6b. Deterministic quality filter
# ---------------------------------------------------------------------------

@dataclass
class InstrumentQualityCfg:
    """
    Per-instrument quality thresholds.

    Calibrated relative to the instrument's baseline volatility (ATR).
    Vol 75, Vol 10, Vol 100 all have very different baseline ATR —
    do NOT use one global threshold across instruments.
    """
    min_structure_ATR: float = 0.3    # minimum size as fraction of ATR to be "real"
    min_impulse_ATR: float = 0.2     # candle creating structure must have range >= this * ATR
    max_retests_failed: int = 2      # order block invalidated after N failed retests
    eqh_eql_tolerance_ATR: float = 0.1  # swing points within this fraction of ATR are "equal"
    liquidity_wick_ratio: float = 0.3   # wick must be this fraction of total candle range


@dataclass
class StructureInstance:
    """A single detected structural instance."""
    stype: str               # "FVG", "iFVG", "OB", "liquidity_sweep", "BOS", "CHoCH", "EQH", "EQL"
    zone_high: float
    zone_low: float
    index: int               # bar index where detected
    timestamp: pd.Timestamp
    direction: str           # "bullish" or "bearish" (or "neutral")
    size_ATR: float          # zone size as fraction of ATR
    age_bars: int            # bars since formation
    mitigated: bool = False
    distance: float = 0.0    # distance from current price (set at query time)
    confluence: bool = False  # aligns with same structure on higher timeframe

    def __repr__(self) -> str:
        return (
            f"Structure({self.stype}, {self.direction}, "
            f"zone=[{self.zone_low:.2f}, {self.zone_high:.2f}], "
            f"size={self.size_ATR:.2f}ATR, age={self.age_bars})"
        )


def compute_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    """Compute Average True Range."""
    high = bars["high"]
    low = bars["low"]
    close = bars["close"].shift(1)

    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()

    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=period, min_periods=1).mean()
    return atr


def passes_quality_filter(
    zone_high: float,
    zone_low: float,
    atr: float,
    cfg: InstrumentQualityCfg,
) -> bool:
    """
    Deterministic quality check: is this structure big enough to be real?

    Returns True if the zone passes all quality thresholds.
    """
    zone_size = zone_high - zone_low
    if atr <= 0:
        return False
    size_ratio = zone_size / atr
    return size_ratio >= cfg.min_structure_ATR


def passes_impulse_check(candle: pd.Series, atr: float, cfg: InstrumentQualityCfg) -> bool:
    """Check that a candle has sufficient range/momentum."""
    if atr <= 0:
        return False
    candle_range = candle["high"] - candle["low"]
    return (candle_range / atr) >= cfg.min_impulse_ATR


# ---------------------------------------------------------------------------
# 6c. Structure detectors
# ---------------------------------------------------------------------------

def detect_fvg(
    bars: pd.DataFrame,
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Fair Value Gap: standard 3-candle gap.
    Bullish FVG: candle 1 high < candle 3 low
    Bearish FVG: candle 1 low > candle 3 high
    """
    results = []
    n = len(bars)
    for i in range(2, n):
        c1 = bars.iloc[i - 2]
        c2 = bars.iloc[i - 1]
        c3 = bars.iloc[i]
        atr_val = atr.iloc[i]

        # Bullish FVG
        if c1["high"] < c3["low"]:
            zone_low = float(c1["high"])
            zone_high = float(c3["low"])
            if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                age = (current_index - i) if current_index is not None else 0
                results.append(StructureInstance(
                    stype="FVG",
                    zone_high=zone_high,
                    zone_low=zone_low,
                    index=i,
                    timestamp=pd.Timestamp(c2["timestamp"]),
                    direction="bullish",
                    size_ATR=(zone_high - zone_low) / atr_val if atr_val > 0 else 0,
                    age_bars=age,
                ))

        # Bearish FVG
        if c1["low"] > c3["high"]:
            zone_low = float(c3["high"])
            zone_high = float(c1["low"])
            if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                age = (current_index - i) if current_index is not None else 0
                results.append(StructureInstance(
                    stype="FVG",
                    zone_high=zone_high,
                    zone_low=zone_low,
                    index=i,
                    timestamp=pd.Timestamp(c2["timestamp"]),
                    direction="bearish",
                    size_ATR=(zone_high - zone_low) / atr_val if atr_val > 0 else 0,
                    age_bars=age,
                ))

    return results


def detect_ifvg(
    bars: pd.DataFrame,
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Inverse FVG: an FVG that transitions from unmitigated to fully mitigated,
    then shows rejection at the same zone.

    Heuristic: find FVGs that have been mitigated (price returned through the zone)
    and then formed a reversal candle near the zone.
    """
    results = []
    fvgs = detect_fvg(bars, atr, cfg, current_index=None)

    n = len(bars)
    for fvg in fvgs:
        if fvg.index + 3 >= n:
            continue

        # Check if subsequent price mitigated the FVG zone
        mitigated = False
        for j in range(fvg.index + 3, min(fvg.index + 30, n)):
            candle = bars.iloc[j]
            # Mitigation: candle body trades through the FVG zone
            if candle["low"] <= fvg.zone_high and candle["high"] >= fvg.zone_low:
                mitigated = True
                # Look for rejection after mitigation
                for k in range(j + 1, min(j + 5, n)):
                    rejection_candle = bars.iloc[k]
                    atr_val = atr.iloc[j]

                    if fvg.direction == "bullish":
                        # Bullish iFVG: price dips into zone then rejects upward
                        if (rejection_candle["low"] <= fvg.zone_high and
                                rejection_candle["close"] > rejection_candle["open"]):
                            if passes_quality_filter(fvg.zone_high, fvg.zone_low, atr_val, cfg):
                                age = (current_index - k) if current_index is not None else 0
                                results.append(StructureInstance(
                                    stype="iFVG",
                                    zone_high=fvg.zone_high,
                                    zone_low=fvg.zone_low,
                                    index=k,
                                    timestamp=pd.Timestamp(rejection_candle["timestamp"]),
                                    direction="bullish",
                                    size_ATR=fvg.size_ATR,
                                    age_bars=age,
                                ))
                            break

                    elif fvg.direction == "bearish":
                        # Bearish iFVG: price pops into zone then rejects downward
                        if (rejection_candle["high"] >= fvg.zone_low and
                                rejection_candle["close"] < rejection_candle["open"]):
                            if passes_quality_filter(fvg.zone_high, fvg.zone_low, atr_val, cfg):
                                age = (current_index - k) if current_index is not None else 0
                                results.append(StructureInstance(
                                    stype="iFVG",
                                    zone_high=fvg.zone_high,
                                    zone_low=fvg.zone_low,
                                    index=k,
                                    timestamp=pd.Timestamp(rejection_candle["timestamp"]),
                                    direction="bearish",
                                    size_ATR=fvg.size_ATR,
                                    age_bars=age,
                                ))
                            break
                break

    return results


def detect_order_blocks(
    bars: pd.DataFrame,
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Order Blocks: last opposing candle before a strong directional move.
    Bullish OB: last bearish candle before a strong up-move.
    Bearish OB: last bullish candle before a strong down-move.
    """
    results = []
    n = len(bars)
    strong_move_ATR = 1.5  # candle(s) after the OB must move this much in ATR units

    for i in range(1, n - 2):
        atr_val = atr.iloc[i]
        if atr_val <= 0:
            continue

        candle = bars.iloc[i]
        is_bearish = candle["close"] < candle["open"]
        is_bullish = candle["close"] > candle["open"]

        # Look for strong move in next 1-3 candles
        max_move_up = 0.0
        max_move_down = 0.0
        for j in range(1, min(4, n - i)):
            future = bars.iloc[i + j]
            move_up = future["high"] - candle["close"]
            move_down = candle["close"] - future["low"]
            max_move_up = max(max_move_up, move_up)
            max_move_down = max(max_move_down, move_down)

        # Bullish OB: bearish candle followed by strong up-move
        if is_bearish and max_move_up > strong_move_ATR * atr_val:
            if passes_impulse_check(candle, atr_val, cfg):
                zone_low = float(candle["low"])
                zone_high = float(candle["high"])
                if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="OB",
                        zone_high=zone_high,
                        zone_low=zone_low,
                        index=i,
                        timestamp=pd.Timestamp(candle["timestamp"]),
                        direction="bullish",
                        size_ATR=(zone_high - zone_low) / atr_val,
                        age_bars=age,
                    ))

        # Bearish OB: bullish candle followed by strong down-move
        if is_bullish and max_move_down > strong_move_ATR * atr_val:
            if passes_impulse_check(candle, atr_val, cfg):
                zone_low = float(candle["low"])
                zone_high = float(candle["high"])
                if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="OB",
                        zone_high=zone_high,
                        zone_low=zone_low,
                        index=i,
                        timestamp=pd.Timestamp(candle["timestamp"]),
                        direction="bearish",
                        size_ATR=(zone_high - zone_low) / atr_val,
                        age_bars=age,
                    ))

    return results


def detect_liquidity_sweeps(
    bars: pd.DataFrame,
    swings: list[SwingPoint],
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
    n_bars_after: int = 3,
) -> list[StructureInstance]:
    """
    Liquidity Sweeps: price wicks beyond a swing high/low then closes
    back inside within N bars.
    """
    results = []
    n = len(bars)

    for sp in swings:
        if sp.index + n_bars_after >= n:
            continue

        atr_val = atr.iloc[sp.index] if sp.index < len(atr) else 0
        if atr_val <= 0:
            continue

        # Look for sweep in the next N bars
        for j in range(sp.index + 1, min(sp.index + n_bars_after + 1, n)):
            candle = bars.iloc[j]

            if sp.kind == "high":
                # Sweep: wick above swing high, close below it
                if candle["high"] > sp.price and candle["close"] < sp.price:
                    # Check wick ratio
                    wick = candle["high"] - max(candle["open"], candle["close"])
                    total_range = candle["high"] - candle["low"]
                    if total_range > 0 and (wick / total_range) >= cfg.liquidity_wick_ratio:
                        age = (current_index - j) if current_index is not None else 0
                        zone_high = float(candle["high"])
                        zone_low = float(sp.price)
                        results.append(StructureInstance(
                            stype="liquidity_sweep",
                            zone_high=zone_high,
                            zone_low=zone_low,
                            index=j,
                            timestamp=pd.Timestamp(candle["timestamp"]),
                            direction="bearish",
                            size_ATR=(zone_high - zone_low) / atr_val,
                            age_bars=age,
                        ))
                        break

            elif sp.kind == "low":
                # Sweep: wick below swing low, close above it
                if candle["low"] < sp.price and candle["close"] > sp.price:
                    wick = min(candle["open"], candle["close"]) - candle["low"]
                    total_range = candle["high"] - candle["low"]
                    if total_range > 0 and (wick / total_range) >= cfg.liquidity_wick_ratio:
                        age = (current_index - j) if current_index is not None else 0
                        zone_high = float(sp.price)
                        zone_low = float(candle["low"])
                        results.append(StructureInstance(
                            stype="liquidity_sweep",
                            zone_high=zone_high,
                            zone_low=zone_low,
                            index=j,
                            timestamp=pd.Timestamp(candle["timestamp"]),
                            direction="bullish",
                            size_ATR=(zone_high - zone_low) / atr_val,
                            age_bars=age,
                        ))
                        break

    return results


def detect_bos(
    bars: pd.DataFrame,
    swings: list[SwingPoint],
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Break of Structure (BOS): close beyond a prior swing high/low
    in the direction of the existing trend.
    """
    results = []
    n = len(bars)

    # Separate swing highs and lows
    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows = [s for s in swings if s.kind == "low"]

    for i in range(n):
        close = bars.iloc[i]["close"]
        atr_val = atr.iloc[i]
        if atr_val <= 0:
            continue

        # Bullish BOS: close above a recent swing high
        for sh in swing_highs:
            if sh.index < i and (i - sh.index) <= 50:  # lookback window
                if close > sh.price:
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="BOS",
                        zone_high=float(close),
                        zone_low=sh.price,
                        index=i,
                        timestamp=pd.Timestamp(bars.iloc[i]["timestamp"]),
                        direction="bullish",
                        size_ATR=(close - sh.price) / atr_val,
                        age_bars=age,
                    ))
                    break  # only first break per bar

        # Bearish BOS: close below a recent swing low
        for sl in swing_lows:
            if sl.index < i and (i - sl.index) <= 50:
                if close < sl.price:
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="BOS",
                        zone_high=sl.price,
                        zone_low=float(close),
                        index=i,
                        timestamp=pd.Timestamp(bars.iloc[i]["timestamp"]),
                        direction="bearish",
                        size_ATR=(sl.price - close) / atr_val,
                        age_bars=age,
                    ))
                    break

    return results


def detect_choch(
    bars: pd.DataFrame,
    swings: list[SwingPoint],
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Change of Character (CHoCH): first break of structure AGAINST
    the prevailing trend sequence.

    If recent swing highs and lows have been making higher highs/higher lows
    (uptrend), a CHoCH is a close below the most recent swing low.
    Vice versa for downtrend.
    """
    results = []
    n = len(bars)

    if len(swings) < 4:
        return results

    for i in range(n):
        close = bars.iloc[i]["close"]
        atr_val = atr.iloc[i]
        if atr_val <= 0:
            continue

        # Look at recent swing sequence to determine trend
        recent_swings = [s for s in swings if s.index < i]
        if len(recent_swings) < 4:
            continue

        recent_highs = [s for s in recent_swings[-6:] if s.kind == "high"]
        recent_lows = [s for s in recent_swings[-6:] if s.kind == "low"]

        # Determine prevailing trend from swing sequence
        if len(recent_highs) >= 2 and len(recent_lows) >= 2:
            highs_rising = recent_highs[-1].price > recent_highs[-2].price
            lows_rising = recent_lows[-1].price > recent_lows[-2].price
            highs_falling = recent_highs[-1].price < recent_highs[-2].price
            lows_falling = recent_lows[-1].price < recent_lows[-2].price

            is_uptrend = highs_rising and lows_rising
            is_downtrend = highs_falling and lows_falling

            # CHoCH in uptrend: close below most recent swing low
            if is_uptrend and recent_lows:
                sl = recent_lows[-1]
                if close < sl.price and (i - sl.index) > 0:
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="CHoCH",
                        zone_high=sl.price,
                        zone_low=float(close),
                        index=i,
                        timestamp=pd.Timestamp(bars.iloc[i]["timestamp"]),
                        direction="bearish",
                        size_ATR=(sl.price - close) / atr_val,
                        age_bars=age,
                    ))

            # CHoCH in downtrend: close above most recent swing high
            if is_downtrend and recent_highs:
                sh = recent_highs[-1]
                if close > sh.price and (i - sh.index) > 0:
                    age = (current_index - i) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="CHoCH",
                        zone_high=float(close),
                        zone_low=sh.price,
                        index=i,
                        timestamp=pd.Timestamp(bars.iloc[i]["timestamp"]),
                        direction="bullish",
                        size_ATR=(close - sh.price) / atr_val,
                        age_bars=age,
                    ))

    return results


def detect_eqh_eql(
    swings: list[SwingPoint],
    atr: pd.Series,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Equal Highs / Equal Lows: two or more swing points within a tolerance band
    (fraction of ATR).
    """
    results = []

    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows = [s for s in swings if s.kind == "low"]

    # Check pairs of swing highs for equality
    for i in range(len(swing_highs)):
        for j in range(i + 1, len(swing_highs)):
            sh1 = swing_highs[i]
            sh2 = swing_highs[j]
            if sh2.index - sh1.index > 50:
                break  # too far apart

            # Use ATR at the later swing point for tolerance
            atr_idx = min(sh2.index, len(atr) - 1)
            atr_val = atr.iloc[atr_idx] if atr_idx >= 0 else 0
            if atr_val <= 0:
                continue

            tolerance = cfg.eqh_eql_tolerance_ATR * atr_val
            if abs(sh1.price - sh2.price) <= tolerance:
                zone_high = max(sh1.price, sh2.price)
                zone_low = min(sh1.price, sh2.price)
                if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                    age = (current_index - sh2.index) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="EQH",
                        zone_high=zone_high,
                        zone_low=zone_low,
                        index=sh2.index,
                        timestamp=sh2.timestamp,
                        direction="neutral",
                        size_ATR=(zone_high - zone_low) / atr_val,
                        age_bars=age,
                    ))

    # Check pairs of swing lows for equality
    for i in range(len(swing_lows)):
        for j in range(i + 1, len(swing_lows)):
            sl1 = swing_lows[i]
            sl2 = swing_lows[j]
            if sl2.index - sl1.index > 50:
                break

            atr_idx = min(sl2.index, len(atr) - 1)
            atr_val = atr.iloc[atr_idx] if atr_idx >= 0 else 0
            if atr_val <= 0:
                continue

            tolerance = cfg.eqh_eql_tolerance_ATR * atr_val
            if abs(sl1.price - sl2.price) <= tolerance:
                zone_high = max(sl1.price, sl2.price)
                zone_low = min(sl1.price, sl2.price)
                if passes_quality_filter(zone_high, zone_low, atr_val, cfg):
                    age = (current_index - sl2.index) if current_index is not None else 0
                    results.append(StructureInstance(
                        stype="EQL",
                        zone_high=zone_high,
                        zone_low=zone_low,
                        index=sl2.index,
                        timestamp=sl2.timestamp,
                        direction="neutral",
                        size_ATR=(zone_high - zone_low) / atr_val,
                        age_bars=age,
                    ))

    return results


def compute_premium_discount(
    swings: list[SwingPoint],
    current_price: float,
) -> dict[str, float]:
    """
    Premium/Discount Zones: midpoint of range between recent swing high and low.

    Returns dict with:
        - midpoint: midpoint price
        - premium_threshold: above midpoint = premium (sell zone)
        - discount_threshold: below midpoint = discount (buy zone)
        - zone_range: total range between SH and SL
        - position: current price position as fraction (0=at SL, 1=at SH)
    """
    swing_highs = [s for s in swings if s.kind == "high"]
    swing_lows = [s for s in swings if s.kind == "low"]

    if not swing_highs or not swing_lows:
        return {"midpoint": current_price, "premium_threshold": current_price,
                "discount_threshold": current_price, "zone_range": 0.0, "position": 0.5}

    # Use most recent SH and SL
    recent_sh = max(swing_highs, key=lambda s: s.index)
    recent_sl = max(swing_lows, key=lambda s: s.index)

    sh_price = recent_sh.price
    sl_price = recent_sl.price
    zone_range = sh_price - sl_price

    if zone_range <= 0:
        return {"midpoint": current_price, "premium_threshold": current_price,
                "discount_threshold": current_price, "zone_range": 0.0, "position": 0.5}

    midpoint = (sh_price + sl_price) / 2.0
    position = (current_price - sl_price) / zone_range if zone_range > 0 else 0.5

    return {
        "midpoint": midpoint,
        "premium_threshold": midpoint,
        "discount_threshold": midpoint,
        "zone_range": zone_range,
        "position": float(np.clip(position, 0.0, 1.0)),
    }


def detect_mitigation_blocks(
    order_blocks: list[StructureInstance],
    bars: pd.DataFrame,
    cfg: InstrumentQualityCfg,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Mitigation Blocks: order blocks that price is expected to revisit
    (failed-breakout zones).

    Shares detection logic with Order Blocks — triggered when an OB zone
    has been partially tested but not fully mitigated.
    """
    results = []
    n = len(bars)

    for ob in order_blocks:
        if ob.index + 3 >= n:
            continue

        # Check if price has entered the OB zone partially but not fully
        partially_tested = False
        fully_tested = False

        for j in range(ob.index + 1, min(ob.index + 20, n)):
            candle = bars.iloc[j]
            candle_low = candle["low"]
            candle_high = candle["high"]

            if candle_low <= ob.zone_high and candle_high >= ob.zone_low:
                # Price entered the zone
                if candle_low <= ob.zone_low and candle_high >= ob.zone_high:
                    fully_tested = True
                    break
                else:
                    partially_tested = True

        if partially_tested and not fully_tested:
            atr_val = 0.0
            if current_index is not None and current_index < len(bars):
                # Approximate ATR
                recent = bars.iloc[max(0, current_index - 14):current_index + 1]
                tr = recent["high"] - recent["low"]
                atr_val = tr.mean() if len(tr) > 0 else 0

            age = (current_index - ob.index) if current_index is not None else 0
            results.append(StructureInstance(
                stype="mitigation_block",
                zone_high=ob.zone_high,
                zone_low=ob.zone_low,
                index=ob.index,
                timestamp=ob.timestamp,
                direction=ob.direction,
                size_ATR=ob.size_ATR,
                age_bars=age,
            ))

    return results


# ---------------------------------------------------------------------------
# Master detection function
# ---------------------------------------------------------------------------

def detect_structures(
    bars: pd.DataFrame,
    atr_period: int = 14,
    swing_lookback: int = 5,
    instrument_cfg: dict[str, Any] | None = None,
    current_index: int | None = None,
) -> list[StructureInstance]:
    """
    Run all structure detectors on a bar DataFrame.

    Parameters
    ----------
    bars : pd.DataFrame
        OHLCV bars with columns: timestamp, open, high, low, close, volume
    atr_period : int
        Period for ATR computation.
    swing_lookback : int
        Lookback for swing-point detection.
    instrument_cfg : dict, optional
        Override InstrumentQualityCfg fields.
    current_index : int, optional
        If set, age_bars is computed relative to this index.

    Returns
    -------
    list of StructureInstance, sorted by index.
    """
    cfg = InstrumentQualityCfg(**(instrument_cfg or {}))

    # Compute ATR
    atr = compute_atr(bars, period=atr_period)

    # Detect swing points (shared foundation)
    swings = detect_swing_points(bars, lookback=swing_lookback)

    # Run all detectors
    all_structures: list[StructureInstance] = []

    fvgs = detect_fvg(bars, atr, cfg, current_index)
    all_structures.extend(fvgs)

    ifvgs = detect_ifvg(bars, atr, cfg, current_index)
    all_structures.extend(ifvgs)

    obs = detect_order_blocks(bars, atr, cfg, current_index)
    all_structures.extend(obs)

    liq_sweeps = detect_liquidity_sweeps(bars, swings, atr, cfg, current_index)
    all_structures.extend(liq_sweeps)

    bos_list = detect_bos(bars, swings, atr, cfg, current_index)
    all_structures.extend(bos_list)

    choch_list = detect_choch(bars, swings, atr, cfg, current_index)
    all_structures.extend(choch_list)

    eqh_eql = detect_eqh_eql(swings, atr, cfg, current_index)
    all_structures.extend(eqh_eql)

    mitigation = detect_mitigation_blocks(obs, bars, cfg, current_index)
    all_structures.extend(mitigation)

    # Sort by index
    all_structures.sort(key=lambda s: s.index)

    logger.info(
        "structures.detect_complete",
        total=len(all_structures),
        fvgs=len(fvgs),
        ifvgs=len(ifvgs),
        obs=len(obs),
        liquidity_sweeps=len(liq_sweeps),
        bos=len(bos_list),
        choch=len(choch_list),
        eqh_eql=len(eqh_eql),
        mitigation=len(mitigation),
        n_bars=len(bars),
    )

    return all_structures


def structures_to_dataframe(structures: list[StructureInstance]) -> pd.DataFrame:
    """Convert a list of StructureInstance to a DataFrame for easier inspection."""
    if not structures:
        return pd.DataFrame()
    rows = []
    for s in structures:
        rows.append({
            "stype": s.stype,
            "direction": s.direction,
            "zone_high": s.zone_high,
            "zone_low": s.zone_low,
            "index": s.index,
            "timestamp": s.timestamp,
            "size_ATR": s.size_ATR,
            "age_bars": s.age_bars,
            "mitigated": s.mitigated,
            "distance": s.distance,
            "confluence": s.confluence,
        })
    return pd.DataFrame(rows)
