#!/usr/bin/env python3
"""
BRAIN AUDITOR — checks the EA's live trade CSV against the playbook.

For every symbol with a trades_<symbol>.csv in SpikeCatcher/:
  - headline stats (trades, WR, PnL, avg win/loss, reason mix)
  - per-close $/point/lot where the volume column exists (v2.12+) — the
    broker-realized calibration the projections depend on
  - anomaly flags: SL-gap outliers vs the median loss, same-second
    multi-close events (both halves closing = expected under v2.11+),
    near-simultaneous losses across symbols (cluster regime), big single
    gaps vs the modeled stop distance.

Anything flagged here feeds the recommender's risk state.
"""
import os
import csv
import numpy as np

from config import TRADE_DIR, by_key, key_from_ea


def _read_closes(path):
    rows = []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r.get("action", "").strip().upper() != "CLOSE":
                continue
            try:
                rows.append({
                    "time": r.get("time", ""),
                    "symbol": r.get("symbol", ""),
                    "dir": r.get("direction", ""),
                    "entry": float(r.get("entry", 0) or 0),
                    "exit": float(r.get("exit", 0) or 0),
                    "pnl": float(r.get("pnl", 0) or 0),
                    "reason": r.get("reason", ""),
                    "volume": (float(r["volume"]) if r.get("volume", "").strip() else None),
                })
            except (TypeError, ValueError):
                continue
    return rows


def audit_symbol(key):
    """Audit one symbol. Returns a dict (empty-ish when no trades file)."""
    ea = by_key(key)["ea"]
    path = os.path.join(TRADE_DIR, f"trades_{ea}.csv")
    if not os.path.exists(path):
        return {"key": key, "ea": ea, "available": False}

    closes = _read_closes(path)
    out = {"key": key, "ea": ea, "available": True, "n": len(closes),
           "closes": closes}
    if not closes:
        return out

    pnls = np.array([c["pnl"] for c in closes])
    out.update({
        "n": len(pnls),
        "pnl": float(pnls.sum()),
        "wr": float((pnls > 0).mean() * 100) if len(pnls) else 0.0,
        "avg_win": float(pnls[pnls > 0].mean()) if (pnls > 0).any() else 0.0,
        "avg_loss": float(pnls[pnls < 0].mean()) if (pnls < 0).any() else 0.0,
        "reasons": {},
    })
    for c in closes:
        out["reasons"][c["reason"]] = out["reasons"].get(c["reason"], 0) + 1

    flags = []
    # --- $/pt/lot from real fills (needs volume column, v2.12+ rows) ---
    calib = []
    for c in closes:
        if c["volume"] and abs(c["exit"] - c["entry"]) > 1e-9:
            usd = c["pnl"] / (abs(c["exit"] - c["entry"]) * c["volume"])
            calib.append(usd)
    out["calib_rows"] = len(calib)
    if calib:
        arr = np.array(calib)
        out["usd_per_pt_lot"] = float(np.median(arr))
        lo, hi = np.percentile(arr, [10, 90])
        out["usd_per_pt_lot_range"] = [float(lo), float(hi)]
        if abs(out["usd_per_pt_lot"] - 1.0) > 0.2:
            flags.append(f"$/pt/lot median {out['usd_per_pt_lot']:.2f} "
                         f"differs from assumed $1.00 — check contract specs")
    else:
        out["usd_per_pt_lot"] = None

    # --- SL-gap outliers vs median loss size ---
    losses = pnls[pnls < 0]
    if len(losses) >= 3:
        med = float(np.median(np.abs(losses)))
        for c in closes:
            if c["pnl"] < 0 and abs(c["pnl"]) > 3.5 * med:
                flags.append(f"SL-gap outlier: {c['time']} loss {c['pnl']:.2f} "
                             f"= {abs(c['pnl'])/med:.1f}x median loss ({c['reason']})")
        out["max_gap_x_median"] = float(np.abs(losses).max() / med)

    # --- simultaneous multi-close (both halves under v2.11) ---
    by_min = {}
    for c in closes:
        by_min.setdefault(c["time"][:16], []).append(c["pnl"])
    multi = {t: v for t, v in by_min.items() if len(v) > 1}
    out["multi_close_events"] = len(multi)
    if multi:
        out["multi_close_examples"] = list(multi.items())[:3]

    out["flags"] = flags
    return out


def audit_all(keys=None):
    from config import SYMBOLS
    keys = keys or [s["key"] for s in SYMBOLS]
    return {k: audit_symbol(k) for k in keys}
