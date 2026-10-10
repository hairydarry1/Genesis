#!/usr/bin/env python3
"""
LIVE GAP MEASUREMENT - pin the portfolio MC row to reality.

Reads the EA's trade CSVs (v2.11+, written to the MT5 Common folder at
%APPDATA%/MetaQuotes/Terminal/Common/Files/SpikeCatcher/ and copied here
as SpikeCatcher/trades_*.csv) and compares the REALIZED loss size per
losing close against the corrected engine's simulated average loss for the
same symbol.

  gap multiplier m = mean(|exit - entry| over losing CLOSEs)
                     / (simulated mean loss per losing trade)

The MC (eval/portfolio_mc.py) treats m as the loss-gap frame:
    m = 1.0  sim tails (optimistic)       breakeven is book-wide 1.60
    m = 1.5  mild friction                (per-symbol breakevens below)
    m = 2.0  realistic live gaps          (crash1000 dies at 1.43)
    m = 2.5  severe

KEY PROPERTY: |exit - entry| is in PRICE POINTS directly from the broker's
fill prices - it needs NO volume, NO $/pt/lot, NO tick-value metadata, so
it is immune to the calibration circularity. Volume, when present (v2.12+
appends it as the 13th column), additionally lets us verify $/pt/lot.

Notes:
  - MT5 FileWrite without FILE_ANSI writes UTF-16LE with BOM - the reader
    sniffs UTF-16 vs UTF-8.
  - Files keep their ORIGINAL header (created once by InitFiles), so rows
    may carry 12 columns (pre-v2.12) or 13 (v2.12+, trailing volume).
  - Partial-entry cycles produce 2 CLOSE rows (one per half) - both count.
  - Manual EA exits (timeout / safety / bar-based SL) are usually SMALLER
    than the sim's broker-floor loss; broker-SL-through-spike fills are
    BIGGER. The mixture is exactly what we want to measure.

Usage:
    python eval/gap_measure.py                 # scan default locations
    python eval/gap_measure.py --dir <folder>  # scan a specific folder
"""
import argparse
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, by_key  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DIRS = [
    os.path.join(ROOT, "SpikeCatcher"),
    os.path.join(os.environ.get("APPDATA", ""), "MetaQuotes", "Terminal",
                 "Common", "Files", "SpikeCatcher"),
]
# per-symbol EV breakeven multipliers from the MC (win pts / loss pts)
BREAKEVEN = {"crash300": 2.19, "crash500": 1.65, "crash1000": 1.43,
             "boom500": 1.86, "boom1000": 1.69}


# ---------------------------------------------------------------- io
def read_csv(path):
    """UTF-16 (BOM) or UTF-8 tolerant CSV read -> list of row lists."""
    with open(path, "rb") as fh:
        raw = fh.read()
    enc = "utf-16" if raw[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig"
    text = raw.decode(enc, errors="replace").replace("\x00", "")
    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        rows.append([c.strip() for c in line.split(",")])
    return rows


def sim_loss_pts(key):
    """Mean simulated loss size (points) per losing trade for a symbol."""
    df = E.load_prep(key)
    cfg = by_key(key)["verified"]
    trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"], partial=True,
                    gate=cfg.get("gate", False),
                    gate_mult=cfg.get("gate_mult", 8.0),
                    gate_window=cfg.get("gate_window", 3),
                    entry_bar=cfg.get("entry_bar", 1), df=df)
    losses = np.array([t["pnl"] for t in trs if t["pnl"] <= 0])
    return float(-losses.mean()) if len(losses) else float("nan"), len(losses)


# ---------------------------------------------------------------- parse
def parse_closes(rows):
    """Extract CLOSE rows. Returns list of dicts with symbol, dir, entry,
    exit, pnl, reason, and volume (may be None)."""
    out = []
    for r in rows:
        if len(r) < 7:
            continue
        if r[2].upper() != "CLOSE":
            continue
        def num(i, d=None):
            try:
                return float(r[i])
            except (ValueError, IndexError):
                return d
        out.append(dict(symbol=r[1], direction=r[3].upper(),
                        entry=num(4), exit=num(5), pnl=num(6),
                        reason=r[7] if len(r) > 7 else "?",
                        volume=num(12) if len(r) > 12 else None))
    return out


def scan(paths):
    files = []
    for d in paths:
        if d and os.path.isdir(d):
            files += sorted(glob.glob(os.path.join(d, "trades_*.csv")))
    files = sorted(set(files))
    by_sym = {}
    for f in files:
        try:
            rows = read_csv(f)
        except Exception as exc:
            print(f"  [skip] {f}: {exc}")
            continue
        closes = parse_closes(rows)
        if closes:
            by_sym.setdefault(closes[0]["symbol"], []).extend(closes)
        else:
            print(f"  [empty] {os.path.basename(f)}  (no CLOSE rows)")
    return by_sym


# ---------------------------------------------------------------- report
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", action="append", default=[])
    ap.add_argument("--min-losses", type=int, default=15,
                    help="losing closes needed before m is trustworthy")
    args = ap.parse_args()

    dirs = args.dir or DEFAULT_DIRS
    print("Scanning:", *[d for d in dirs if d], sep="\n  ")
    by_sym = scan(dirs)

    # sim benchmarks (computed once, cached)
    sim = {}
    for s in SYMBOLS:
        sim[s["key"]] = sim_loss_pts(s["key"])
    print()
    print(f"{'symbol':<14}{'loss rows':>10}{'win rows':>10}{'avg loss pts':>14}"
          f"{'sim loss pts':>13}{'gap m':>8}{'verdict':>34}")
    found = False
    for s in SYMBOLS:
        key, ea = s["key"], s["ea"]
        closes = by_sym.get(ea, [])
        losses = [c for c in closes if c["pnl"] is not None and c["pnl"] < 0]
        wins = [c for c in closes if c["pnl"] is not None and c["pnl"] > 0]
        if not closes:
            print(f"{ea:<14}{'--':>10}{'--':>10}{'--':>14}{'--':>13}"
                  f"{'--':>8}  no closes logged yet")
            continue
        found = True
        sim_avg, sim_n = sim[key]
        if losses:
            pts = np.array([abs(c["exit"] - c["entry"]) for c in losses])
            live_avg = float(pts.mean())
            m = live_avg / sim_avg if sim_avg > 0 else float("nan")
            m_str = f"{m:.2f}" if np.isfinite(m) else "n/a"
            if len(losses) >= args.min_losses:
                if m < 1.0:
                    verdict = f"BETTER than sim - MC row m<1"
                elif m < BREAKEVEN[key]:
                    verdict = f"viable (breakeven {BREAKEVEN[key]:.2f})"
                else:
                    verdict = (f"EV-NEGATIVE - past breakeven "
                               f"{BREAKEVEN[key]:.2f}")
            else:
                verdict = f"need {args.min_losses - len(losses)} more losses"
            print(f"{ea:<14}{len(losses):>10}{len(wins):>10}{live_avg:>14.3f}"
                  f"{sim_avg:>13.3f}{m_str:>8}  {verdict}")
        else:
            print(f"{ea:<14}{0:>10}{len(wins):>10}{'--':>14}"
                  f"{sim_avg:>13.3f}{'--':>8}  no losing closes yet")

    if not found:
        print("\nNo CLOSE rows found anywhere yet.")
    else:
        print(f"\nsim loss pts = mean losing-trade size from the corrected "
              f"engine (n={dict((k, v[1]) for k, v in sim.items())}).")
        print("gap m < breakeven => the MC's positive rows apply; "
              "m >= breakeven => that chart is EV-negative live.")
        print("Re-run after ~30 closes per symbol accumulate (mix of "
              "timeout/sl/safety reasons).")


if __name__ == "__main__":
    main()
