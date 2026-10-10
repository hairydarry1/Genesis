#!/usr/bin/env python3
"""Stage-3 edge backtest: bar-level phase-follower on the stable shortlist.

Strategy (mirrors the Flip study config, at bar scale):
  - large move: |bar return| > mult x rolling median (500-bar window)
  - position: after 2 consecutive same-direction large moves, hold that
    direction; flip on the next counter-direction large move
  - cost: live spread charged on every position change (round trip)
  - sizing: min lot (usually 0.1), $ conversion from live point/tvp specs

Control: flip1000's own M1 bars (aggregated from the tick store) run through
the same engine — its tick-level net is +$27/wk, so the bar-level result
calibrates how much optimism the bar aggregation adds.

Also reports a buy&hold row for Trek (same@3 = 1.0 is by-design drift, and the
only way to own drift is to hold it, not to trade phase flips).

Usage: python analysis/synth_edge_backtest.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data.flip_tick_store import mt5_init

SYMBOLS = [
    "Trek Up Index",
    "Trek Down Index",
    "Vol over Crash 400",
    "Vol over Crash 550",
    "Skew Step Index 5 Up",
    "Skew Step Index 5 Down",
    "Skew Step Index 4 Up",
    "Skew Step Index 4 Down",
    "Range Break 100 Index",
    "Volatility 100 (1s) Index",
    "Volatility 30 (1s) Index",
]
DAYS = 30
ROLL = 500


def fetch_bars(mt5, name, now, days=DAYS):
    safe = name.replace(" ", "_").replace("(", "").replace(")", "")
    path = f"data/synth_scan/bars30_{safe}.npy"
    if os.path.exists(path):
        return np.load(path)
    r = mt5.copy_rates_range(name, mt5.TIMEFRAME_M1, now - timedelta(days=days), now)
    if r is None or len(r) == 0:
        return None
    bars = np.column_stack([r["time"], r["open"], r["high"], r["low"], r["close"]]).astype(float)
    np.save(path, bars)
    return bars


def backtest2(close, spread, dpu, mult):
    ret = np.diff(close)
    n = len(ret)
    if n < ROLL + 100:
        return None
    med = np.array([np.median(np.abs(ret[i - ROLL:i])[np.abs(ret[i - ROLL:i]) > 0])
                    if (np.abs(ret[i - ROLL:i]) > 0).any() else np.nan
                    for i in range(ROLL, n)])
    med = np.concatenate([np.full(ROLL, np.nan), med])
    big = np.where(np.abs(ret) > mult * med)[0]
    if len(big) < 10:
        return {"trades": 0}
    big_set = set(big.tolist())

    pos = 0.0
    cash = 0.0
    last_dir = 0
    consec = 0
    trades = wins = 0
    eq = np.zeros(n)
    for i in range(n):
        cash += pos * ret[i] * dpu
        eq[i] = cash
        if i in big_set:
            d = int(np.sign(ret[i]))
            if d == last_dir:
                consec += 1
            else:
                last_dir, consec = d, 1
            # enter/flip after 2 consecutive same-dir large moves
            want = d if consec >= 2 else 0
            if want != pos:
                cash -= spread * dpu          # position change cost
                trades += 1
                pos = want
    # close at end
    cash -= spread * dpu * (1 if pos != 0 else 0)
    if pos != 0:
        trades += 1
    dd = np.max(np.maximum.accumulate(eq) - eq)
    # per-trade PnL proxy: cash / entries
    return {"trades": trades, "net": cash, "dd": dd, "eq": eq[-1]}


def main():
    mt5 = mt5_init()
    now = datetime.now(timezone.utc)
    rows = []
    try:
        for name in SYMBOLS + ["__flip1000_control__"]:
            if name == "__flip1000_control__":
                from data.flip_tick_store import load
                ms, px = load("flip1000")
                # aggregate ticks to M1 bars
                minute = ms // 60_000
                uniq, idx = np.unique(minute, return_index=True)
                close = px[np.sort(idx)]
                bars_h = (ms[-1] - ms[0]) / 3.6e6
                info = mt5.symbol_info("Crash Boom Flip 1000 Index")
                t = mt5.symbol_info_tick("Crash Boom Flip 1000 Index")
            else:
                bars = fetch_bars(mt5, name, now)
                if bars is None:
                    print(f"{name:28s} NO BARS"); continue
                close = bars[:, 4]
                bars_h = (bars[-1, 0] - bars[0, 0]) / 3600.0
                info = mt5.symbol_info(name)
                t = mt5.symbol_info_tick(name)
            if t is None or info is None or t.ask <= t.bid:
                print(f"{name:28s} NO QUOTE"); continue
            spread = float(t.ask - t.bid)
            dpu = float(info.trade_tick_value_profit or info.trade_tick_value) / float(info.point) * 0.1
            vmin = float(info.volume_min)
            margin = float(getattr(info, "margin_initial", 0) or 0) * vmin

            out_lines = [f"{name}  spread={spread:.4g}  ${dpu:.4f}/unit@0.1  "
                         f"minlot={vmin}  margin~${margin:.2f}  hours={bars_h:.0f}"]
            for mult in (2.0, 3.0):
                r = backtest2(close, spread, dpu, mult)
                if r is None:
                    out_lines.append(f"  mult={mult:g}: insufficient data")
                    continue
                if r.get("trades", 0) == 0:
                    out_lines.append(f"  mult={mult:g}: no trades")
                    continue
                wks = bars_h / 168.0
                out_lines.append(
                    f"  mult={mult:g}: trades={r['trades']:>4}  net=${r['net']:>8.2f} "
                    f"({r['net']/max(wks,1e-9):>7.2f}/wk)  maxDD=${r['dd']:.2f}")
                rows.append((name, mult, r["trades"], r["net"], r["net"] / max(wks, 1e-9),
                             r["dd"], spread, dpu))
            print("\n".join(out_lines))

        print("\n" + "=" * 100)
        print("RANKED by net $/week (mult=3 unless noted; flip1000 row = bar-level CONTROL vs +$27/wk tick truth)")
        print("=" * 100)
        for name, mult, trades, net, npw, dd, spread, dpu in sorted(rows, key=lambda r: -r[4]):
            print(f"{name:28s} m={mult:<3g} trades={trades:>5} net/wk=${npw:>8.2f} maxDD=${dd:>6.2f}")
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    main()
