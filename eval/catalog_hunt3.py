#!/usr/bin/env python3
"""Diagnose the T3-vs-engine discrepancy: bucket canonical GATE trades by
their position within >=8x runs and by run size, using engine trades
(trusted accounting) and their entry_row. Also run an OOS hold sweep on
gated crash300."""
import os
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, BROKER_FLOOR  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
from partial_m1_corrected import half_exit, arrays  # noqa: E402


def ver(key):
    return dict([s for s in SYMBOLS if s["key"] == key][0]["verified"])


def f(trs):
    if not trs:
        return "none"
    p = np.array([t["pnl"] for t in trs])
    w = p[p > 0]; l = p[p <= 0]
    pf = w.sum() / abs(l.sum()) if len(l) else 999
    eq = np.cumsum(p); dd = (np.maximum.accumulate(eq) - eq).max()
    return (f"n={len(p):>5} WR={len(w)/len(p)*100:>5.1f}% PF={pf:>6.2f} "
            f"PnL={p.sum():>+9.1f} avg={p.mean():>+6.3f} avgW={w.mean() if len(w) else 0:+.2f}"
            f" DD={dd:>7.1f}")


def run_positions(df, key):
    """For every >=8x spike row: (run_size, pos_within_run, is_final)."""
    base = E.spike_rows(df, key).astype(bool)
    med = df["median_range"].values
    rng = df["range"].values
    ratio = np.where(np.isfinite(med) & (med > 0),
                     rng / np.maximum(med, 1e-9), 0)
    big = np.where(base & (ratio >= 8.0))[0]
    info = {}
    runs = []
    for i in big:
        if runs and i - runs[-1][-1] <= 3:
            runs[-1].append(i)
        else:
            runs.append([i])
    for run in runs:
        for pos, i in enumerate(run, start=1):
            info[i] = dict(size=len(run), pos=pos, final=pos == len(run))
    return info


def main():
    for key in ("crash300", "crash500", "boom500"):
        cfg = ver(key)
        df = E.load_prep(key)
        trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                        partial=True, gate=True, gate_mult=8.0,
                        gate_window=3, entry_bar=1, df=df)
        info = run_positions(df, key)
        print("=" * 100)
        print(f"{key} canonical gate trades: {f(trs)}")
        print("=" * 100)
        # bucket by run size of the ENTRY spike
        for rsz in (2, 3, 4):
            sel = [t for t in trs
                   if t["entry_row"] in info and info[t["entry_row"]]["size"] == rsz]
            if sel:
                print(f"  run size {rsz}:            {f(sel)}")
        # bucket by position within run
        for pos in (2, 3, 4):
            sel = [t for t in trs
                   if t["entry_row"] in info and info[t["entry_row"]]["pos"] == pos]
            if sel:
                print(f"  position {pos} in run:     {f(sel)}")
        finals = [t for t in trs
                  if t["entry_row"] in info and info[t["entry_row"]]["final"]]
        mids = [t for t in trs
                if t["entry_row"] in info and not info[t["entry_row"]]["final"]]
        print(f"  FINAL-of-run trades:      {f(finals)}")
        print(f"  MID-run trades:           {f(mids)}")

    # crash300 gate hold sweep OOS (playbook says hold 2)
    key = "crash300"
    df = E.load_prep(key)
    cfg = ver(key)
    print("\n" + "=" * 100)
    print("crash300 gated hold sweep (full + OOS)")
    print("=" * 100)
    cut = len(df) // 2
    for hold in (1, 2, 3, 4):
        full = E.run_cfg(key, hold, cooldown=cfg["cooldown"], partial=True,
                         gate=True, gate_mult=8.0, gate_window=3, df=df)
        h1 = E.run_cfg(key, hold, cooldown=cfg["cooldown"], partial=True,
                       gate=True, gate_mult=8.0, gate_window=3,
                       df=df.iloc[:cut].reset_index(drop=True))
        h2 = E.run_cfg(key, hold, cooldown=cfg["cooldown"], partial=True,
                       gate=True, gate_mult=8.0, gate_window=3,
                       df=df.iloc[cut:].reset_index(drop=True))
        print(f"  hold={hold}: full {f(full)}")
        print(f"           OOS 1st {f(h1)}")
        print(f"           OOS 2nd {f(h2)}")


if __name__ == "__main__":
    main()
