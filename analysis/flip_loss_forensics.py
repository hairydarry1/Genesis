#!/usr/bin/env python3
"""Loss-side forensics for the Flip phase-follower.

Same detectors as flip_conditionality.simulate_ma (parity-checked), plus
per-trade close timestamps for daily/clustering profiling. All loss stats
are NET of spread (the number you actually eat).

Usage: python analysis/flip_loss_forensics.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import simulate_ma, load, WARMUP
from flip_phase_backtest import get_specs, path_sim

CONFIGS = [
    ("flip1000", 5.0, 1),   # recommended
    ("flip1000", 5.0, 0),   # baseline
    ("flip500",  3.5, 2),
    ("flip500",  3.5, 0),
    ("flip300",  3.0, 0),
]


def simulate_ma_ts(px, ms, med, mult, min_age=0):
    """simulate_ma + close tick index. Parity-checked against simulate_ma."""
    d = np.diff(px)
    big_idx = np.where(np.abs(d) > mult * med)[0]
    bad = bad_tick_mask(ms)                    # gap-masking
    big_idx = big_idx[~bad[np.minimum(big_idx + 1, len(px) - 1)]]
    holes = hole_intervals(ms)
    pos, entry = 0, 0.0
    trades = []                       # (gross pnl, close tick index)
    cur_dir, cnt = 0, 0
    last_t = None
    for i in big_idx:
        t_fill = ms[i + 1] if i + 1 < len(ms) else ms[-1]
        if last_t is not None and crosses_hole(holes, last_t, t_fill):
            pos, cur_dir, cnt = 0, 0, 0
        last_t = t_fill
        s = int(np.sign(d[i]))
        ci = i + 1 if i + 1 < len(px) else len(px) - 1
        fill = px[ci]
        if s == cur_dir:
            cnt += 1
        else:
            cur_dir, cnt = s, 1
        if pos == 0:
            if cnt >= min_age + 1:
                pos, entry = s, fill
        elif pos != s:
            trades.append((pos * (fill - entry), ci))
            pos = 0
            if cnt >= min_age + 1:
                pos, entry = s, fill
    if pos != 0:
        trades.append((pos * (px[-1] - entry), len(px) - 1))
    return trades


def main():
    specs = get_specs()
    for sym, mult, k in CONFIGS:
        ms, px = load(sym)
        d = np.diff(px)
        med = float(np.median(np.abs(d[:WARMUP])))
        sp = specs[sym]
        vpu = sp["tick_value"] / sp["tick_size"] * sp["vol_min"]
        spread_units = sp["spread_pts"] * sp["point"]
        dur_wk = (ms[-1] - ms[0]) / (3.6e6 * 168)

        tr_ref = simulate_ma(px, med, mult, min_age=k, ms=ms)
        tr_ts = simulate_ma_ts(px, ms, med, mult, min_age=k)
        gross_ref = float(np.sum(tr_ref))
        gross_ts = float(sum(p for p, _ in tr_ts))
        if len(tr_ref) != len(tr_ts) or abs(gross_ref - gross_ts) > 1e-9:
            print(f"!! PARITY FAIL {sym}@{mult} k={k}: {len(tr_ref)}/{len(tr_ts)} "
                  f"trades, {gross_ref:.4f}/{gross_ts:.4f} gross")
            continue

        net = (np.array([p for p, _ in tr_ts]) - spread_units) * vpu   # USD
        ts = [ms[ci] for _, ci in tr_ts]
        n = len(net)
        wins, losses = net[net > 0], net[net <= 0]
        avgW, avgL = wins.mean(), losses.mean()
        wu = -avgL / avgW
        be = -avgL / (avgW - avgL)

        # clustering: longest losing streak and its total
        streaks = []
        run, run_sum = 0, 0.0
        for t in net:
            if t <= 0:
                run += 1
                run_sum += t
            else:
                if run:
                    streaks.append((run, run_sum))
                run, run_sum = 0, 0.0
        if run:
            streaks.append((run, run_sum))
        worst_streak = min(streaks, key=lambda x: x[1]) if streaks else (0, 0.0)

        # daily (UTC) PnL
        days = {}
        for t, m in zip(net, ts):
            day = datetime.fromtimestamp(m / 1000, tz=timezone.utc).strftime("%m-%d")
            days.setdefault(day, []).append(t)
        day_pnl = {k2: sum(v) for k2, v in days.items()}
        worst_day = min(day_pnl.items(), key=lambda x: x[1])
        losing_days = sum(1 for v in day_pnl.values() if v < 0)

        ps = path_sim(net, start_eq=10.0)

        print(f"\n{sym.upper()} @ {mult}x min_age={k}  ({n} trades, {dur_wk:.2f}wk, "
              f"NET {net.sum():+.2f}$)")
        print(f"  avg win ${avgW:+.3f}   avg loss ${avgL:+.3f}   "
              f"1 loss = {wu:.2f} wins   breakeven WR {be*100:.1f}% vs actual "
              f"{len(wins)/n*100:.1f}%")
        print(f"  loss size (USD): median {np.percentile(losses,50):+.3f}  "
              f"p90 |loss| {np.percentile(losses,10):+.3f}  worst {losses.min():+.3f}")
        print(f"  worst losing streak: {worst_streak[0]} trades = "
              f"${worst_streak[1]:+.2f}   losing days {losing_days}/{len(day_pnl)}   "
              f"worst day {worst_day[0]} ${worst_day[1]:+.2f}")
        print(f"  $10 hist path: final ${ps['final']:.2f}  min equity "
              f"${ps['min_eq']:.2f}")


if __name__ == "__main__":
    main()
