#!/usr/bin/env python3
"""
BRAIN CONFIG — verified per-symbol settings + paths.

The baseline settings below come from docs/FINAL_VERIFIED_PLAYBOOK.md
(corrected M1 engine, OOS halves, real gap fills). The AI layer never
moves off these without re-validating on the corrected engine.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
TRADE_DIR = os.path.join(ROOT, "SpikeCatcher")   # live EA trade CSVs live here
REPORT_DIR = os.path.join(ROOT, "brain", "reports")

# Ordered list of the five traded symbols.
SYMBOLS = [
    {
        "key": "crash300",
        "ea": "Crash 300 Index",
        "is_crash": True,
        "mean_gap_min": 8.1,
        # entry_bar = bars after the spike bar before entering
        "verified": {"entry_bar": 1, "hold": 2, "cooldown": 1, "gate": True, "gate_mult": 8.0, "gate_window": 3},
    },
    {
        "key": "crash500",
        "ea": "Crash 500 Index",
        "is_crash": True,
        "mean_gap_min": 11.1,
        "verified": {"entry_bar": 1, "hold": 2, "cooldown": 1, "gate": True, "gate_mult": 8.0, "gate_window": 3},
    },
    {
        "key": "crash1000",
        "ea": "Crash 1000 Index",
        "is_crash": True,
        "mean_gap_min": 19.5,
        # v2.13: entry+3 OOS-verified both halves (PF 1.27/1.29 -> 1.45/1.40)
        "verified": {"entry_bar": 3, "hold": 5, "cooldown": 3, "gate": False},
    },
    {
        "key": "boom500",
        "ea": "Boom 500 Index",
        "is_crash": False,
        "mean_gap_min": 11.1,
        "verified": {"entry_bar": 1, "hold": 2, "cooldown": 1, "gate": True, "gate_mult": 8.0, "gate_window": 3},
    },
    {
        "key": "boom1000",
        "ea": "Boom 1000 Index",
        "is_crash": False,
        "mean_gap_min": 19.7,
        # v2.13: entry+2 OOS-verified both halves (PF 1.30/1.60 -> 1.58/1.80)
        "verified": {"entry_bar": 2, "hold": 2, "cooldown": 3, "gate": False},
    },
]

# 1000-series never use the gate (verified harmful OOS) — the EA also
# auto-disables it there, so the learner must not propose it either.
GATE_FORBIDDEN_KEYS = {"crash1000", "boom1000"}

# Broker minimum stop distance floor per symbol (price units) — mirrors
# BROKER_FLOOR in eval/partial_m1_corrected.py
BROKER_FLOOR = {"crash1000": 1.14, "crash500": 0.85, "crash300": 2.43,
                "boom1000": 1.14, "boom500": 0.85}

# Which engine/candle columns drive the spike definition
SPIKE_MULT = 3.0
CLUSTER_WINDOW_BARS = 10   # cluster filter: skip if 3+ spikes in this window
ENTRY_BAR = 1              # enter 1 bar after the spike (bar e+1 close)


def by_key(key):
    for s in SYMBOLS:
        if s["key"] == key:
            return s
    raise KeyError(key)


def candle_path(key):
    return os.path.join(DATA_DIR, f"candles_{key}_1min.parquet")


def trade_csv_path(ea_symbol):
    # EA writes "trades_<EA symbol>.csv" into the SpikeCatcher folder
    return os.path.join(TRADE_DIR, f"trades_{ea_symbol}.csv")


def key_from_ea(ea_symbol):
    for s in SYMBOLS:
        if s["ea"].lower() == ea_symbol.lower():
            return s["key"]
    # tolerate the raw data naming (crash_500 / boom_500)
    return ea_symbol.lower().replace(" index", "").replace(" ", "")
