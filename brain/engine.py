#!/usr/bin/env python3
"""
BRAIN ENGINE — corrected M1 engine wrapper.

Everything the AI layer recommends is measured on THIS engine (the same
one that killed the lookahead artifacts). Reuses eval/partial_m1_corrected
which is import-safe (its __main__ is guarded).

Adds what that script lacks:
  - the BIG-CLUSTER GATE (>= mult x median spike with another >= mult x
    spike in the prior `window` bars) — mirrors the EA's BigGatePassed()
  - windowed runs over a slice of the dataframe (for train/validate)
  - outcome-series helpers (P(loss | prev loss), reasons)
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "eval"))
from partial_m1_corrected import (  # noqa: E402  (import-safe: main guarded)
    load as _load, prep as _prep, arrays, half_exit, stats,
    partial_trade, full_trade,
)

from config import (candle_path, by_key, SPIKE_MULT, CLUSTER_WINDOW_BARS,
                    ENTRY_BAR, GATE_FORBIDDEN_KEYS, BROKER_FLOOR)  # noqa: E402


# --------------------------------------------------------------------------
# Load / prepare / datetime helpers
# --------------------------------------------------------------------------
_PREP_CACHE = {}
_DT_CACHE = {}


def load_prep(key, force=False):
    """Prepared full-history df per symbol (cached). Columns include
    'range' and 'median_range' like the corrected engine. Reads via the
    absolute data path (the eval engine uses a CWD-relative one)."""
    if key in _PREP_CACHE and not force:
        return _PREP_CACHE[key]
    import pandas as pd
    df = pd.read_parquet(candle_path(key))
    df.columns = [c.strip().lower() for c in df.columns]
    df = _prep(df)
    _PREP_CACHE[key] = df
    return df


def datetimes(key):
    """Datetime series for every bar. crash300 stores 'epoch' seconds
    instead of 'time'; the others have 'time'."""
    if key in _DT_CACHE:
        return _DT_CACHE[key]
    df = load_prep(key)
    if "time" in df.columns:
        dt = pd_to_datetime(df["time"])
    elif "epoch" in df.columns:
        raw = df["epoch"].astype(np.int64)
        unit = "ms" if raw.max() > 1e12 else "s"
        dt = pd_to_datetime(raw, unit=unit)
    else:
        dt = pd.to_datetime(df.index, unit="min", origin="2026-01-01")
    _DT_CACHE[key] = dt
    return dt


def pd_to_datetime(series, unit=None):
    import pandas as pd
    return pd.to_datetime(series, unit=unit) if unit else pd.to_datetime(series)


def slice_bars(key, start_dt=None, end_dt=None):
    """Row slice [start, end) by datetime. Returns (df_slice_reset,
    start_row, end_row)."""
    df = load_prep(key)
    dt = datetimes(key)
    if start_dt is None and end_dt is None:
        return df, 0, len(df)
    mask = np.ones(len(df), dtype=bool)
    if start_dt is not None:
        mask &= dt >= np.datetime64(start_dt)
    if end_dt is not None:
        mask &= dt < np.datetime64(end_dt)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return df.iloc[0:0].reset_index(drop=True), 0, 0
    lo, hi = int(idx[0]), int(idx[-1]) + 1
    return df.iloc[lo:hi].reset_index(drop=True), lo, hi


# --------------------------------------------------------------------------
# Spike qualification (mirrors EA spike detection + BigGatePassed)
# --------------------------------------------------------------------------
def spike_rows(df, sym, spike_mult=SPIKE_MULT):
    """Boolean mask of adverse-spike bars (same rule as corrected engine)."""
    med = df["median_range"].values
    rng = df["range"].values
    is_crash = "crash" in sym
    adv = (df["close"].values < df["open"].values) if is_crash else \
          (df["close"].values > df["open"].values)
    return (rng > spike_mult * med) & adv & np.isfinite(med) & (rng > 0.2)


def gate_mask(df, sym, gate_mult=8.0, gate_window=3, base_mask=None):
    """EA BigGatePassed(): a spike qualifies when ITS ratio >= gate_mult AND
    a PRIOR spike within gate_window bars also had ratio >= gate_mult.
    1000-series are always forbidden (verified harmful OOS)."""
    if sym in GATE_FORBIDDEN_KEYS:
        return np.zeros(len(df), dtype=bool)
    med = df["median_range"].values
    rng = df["range"].values
    if base_mask is None:
        base_mask = spike_rows(df, sym)
    ratio = np.where(np.isfinite(med) & (med > 0), rng / np.where(med > 0, med, np.nan), 0)
    big = base_mask & (ratio >= gate_mult)
    out = np.zeros(len(df), dtype=bool)
    last_big = -10**9
    for i in range(len(df)):
        if not base_mask[i]:
            continue
        if big[i]:
            if i - last_big <= gate_window:
                out[i] = True
            last_big = i
    return out


# --------------------------------------------------------------------------
# Config runner (mirrors corrected pipeline + gate + cluster + cooldown)
# --------------------------------------------------------------------------
def _partial_at(df, sym, i, hold, entry_bar, sl_floor):
    """Partial-entry trade entering at close of spike+entry_bar (1 = the
    corrected engine's canonical behavior). Mirrors eval partial_trade for
    entry_bar=1 (verified numerically identical)."""
    n = len(df)
    closes, highs, lows, rng, med, adv, is_crash = arrays(df, sym)
    e = i + entry_bar
    if e + 2 >= n:
        return None
    entry = closes[e]
    sl_dist = max(med[i], sl_floor)
    sl_level = (entry - sl_dist) if is_crash else (entry + sl_dist)
    tp_level = (entry + 2 * sl_dist) if is_crash else (entry - 2 * sl_dist)
    pA, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                      entry, sl_level, tp_level, e + 1, hold, n)
    pA1, rA1 = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                         entry, sl_level, tp_level, e + 1, 1, n)
    add_b = (rA1 == "timeout" and
             ((closes[e + 1] > entry) if is_crash else (closes[e + 1] < entry)))
    if add_b:
        pB, _ = half_exit(closes, highs, lows, rng, med, adv, is_crash,
                          entry, sl_level, tp_level, e + 2, hold - 1, n)
        return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1) +
                0.5 * (pB - entry) * (1 if is_crash else -1), "nhalves": 2}
    return {"pnl": 0.5 * (pA - entry) * (1 if is_crash else -1), "nhalves": 1}


def run_cfg(sym, hold, cooldown=0, partial=True, gate=False,
            gate_mult=8.0, gate_window=3, cluster=True,
            df=None, full_only=False, spike_mult=SPIKE_MULT,
            entry_bar=1):
    """Evaluate one config over a df (default: full history). Returns the
    list of trade dicts (pnl, nhalves, reason, entry_row) — feed to stats().

    Semantics mirror eval/partial_m1_corrected.pipeline exactly:
      - cluster: skip if 3+ spikes in CLUSTER_WINDOW_BARS
      - cooldown: skip `cooldown` spikes after a losing trade
      - gate: entry spikes restricted by gate_mask()
    """
    if df is None:
        df = load_prep(sym)
    n = len(df)
    if n < 50:
        return []

    base = spike_rows(df, sym, spike_mult)
    if gate:
        keep = gate_mask(df, sym, gate_mult, gate_window, base_mask=base)
        idx = np.where(keep)[0]
    else:
        idx = np.where(base)[0]

    trades = []
    skip = 0
    last_two = []   # last two spike row indices for the cluster check
    for i in idx:
        if skip > 0:
            skip -= 1
            continue
        if cluster and len(last_two) >= 2 and (i - last_two[-2]) <= CLUSTER_WINDOW_BARS:
            last_two.append(i)
            continue
        if i + 1 + 1 >= n:
            break
        if full_only:
            tr = full_trade(df, sym, i, hold)
        elif entry_bar == 1:
            tr = partial_trade(df, sym, i, hold)   # canonical, unchanged
        else:
            tr = _partial_at(df, sym, i, hold, entry_bar,
                             BROKER_FLOOR.get(sym, 1.0))
        last_two.append(i)
        if tr is None:
            continue
        tr["entry_row"] = i
        tr["reason"] = tr.get("reason", "timeout")
        trades.append(tr)
        if tr["pnl"] <= 0 and cooldown > 0:
            skip = cooldown
    return trades


def outcome_stats(trades):
    """engine.stats() plus loss-clustering ratio and reason breakdown."""
    s = stats(trades)
    if s is None:
        return None
    pnls = np.array([t["pnl"] for t in trades])
    losses = pnls <= 0
    if len(pnls) > 1 and losses.sum() > 0:
        both = np.zeros(len(pnls), dtype=bool)
        both[1:] = losses[:-1] & losses[1:]
        pll = both.sum() / max(1, int(losses[:-1].sum()))
    else:
        pll = float("nan")
    reasons = {}
    for t in trades:
        r = t.get("reason", "timeout")
        if r not in ("sl", "tp", "timeout"):
            r = "other"
        reasons[r] = reasons.get(r, 0) + 1
    return {**s, "p_loss": float(losses.mean()) if len(pnls) else float("nan"),
            "p_loss_given_loss": float(pll), "reasons": reasons}


def oos_halves(sym, cfg, split=0.5):
    """Train/validation split of outcome_stats for a config."""
    df = load_prep(sym)
    n = len(df)
    cut = int(n * split)
    tr = outcome_stats(run_cfg(sym, cfg["hold"], cfg.get("cooldown", 0),
                               partial=cfg.get("partial", True),
                               gate=cfg.get("gate", False),
                               gate_mult=cfg.get("gate_mult", 8.0),
                               gate_window=cfg.get("gate_window", 3),
                               df=df.iloc[:cut].reset_index(drop=True)))
    va = outcome_stats(run_cfg(sym, cfg["hold"], cfg.get("cooldown", 0),
                               partial=cfg.get("partial", True),
                               gate=cfg.get("gate", False),
                               gate_mult=cfg.get("gate_mult", 8.0),
                               gate_window=cfg.get("gate_window", 3),
                               df=df.iloc[cut:].reset_index(drop=True)))
    return tr, va
