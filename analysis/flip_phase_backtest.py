#!/usr/bin/env python3
"""Phase-following backtest on Crash Boom Flip indices (tick data).

Strategy: the phase = direction of the last large move (|delta| > mult x median).
  - flat -> enter at the next tick after the first large move
  - positioned with phase -> hold (collect grind + same-direction spikes)
  - counter-spike (flip) -> close and reverse at the next tick after it

Realism:
  - fills happen at the tick AFTER the spike (you never catch the spike itself)
  - spread paid once per completed phase (modeled on the bid series)
  - median delta calibrated on the first warmup ticks, then FROZEN (no lookahead)

Reports per symbol x spike_mult: trades/week, WR, gross/net USD per week at
minimum lot, worst phase, max drawdown, and a $10->$100 compounding projection.

Usage: python analysis/flip_phase_backtest.py
"""

import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole

sys.path.insert(0, ".")

FLIPS = ["flip150", "flip300", "flip500", "flip1000"]
WARMUP = 5000          # ticks used to calibrate the frozen median delta
MULTS = [2.5, 3.0, 3.5, 4.0, 5.0]


def load(sym):
    # prefer the accumulating daily store; fall back to the legacy 7-day fetch
    try:
        from data.flip_tick_store import load as store_load
        return store_load(sym)
    except (FileNotFoundError, ModuleNotFoundError):
        arr = np.load(f"data/ticks_{sym}.npy")
        return arr[:, 0], arr[:, 1]  # ms, bid price


def simulate(px, med, mult, spread_units, ms=None):
    """Run the phase-follower. Returns (per-phase PnLs in price units,
    spread EXCLUDED, per-phase direction)."""
    d = np.diff(px)
    ad = np.abs(d)
    big_idx = np.where(ad > mult * med)[0]
    if ms is not None:                     # gap-masking: drop fake edge-spikes
        bad = bad_tick_mask(ms)
        big_idx = big_idx[~bad[np.minimum(big_idx + 1, len(px) - 1)]]
        holes = hole_intervals(ms)
    else:
        holes = []

    pos = 0
    entry = 0.0
    trades = []          # (gross pnl price units, direction)
    last_t = None
    for i in big_idx:
        if ms is not None:
            t_fill = ms[i + 1] if i + 1 < len(ms) else ms[-1]
            if last_t is not None and crosses_hole(holes, last_t, t_fill):
                pos = 0                    # position would have spanned a hole
            last_t = t_fill
        s = int(np.sign(d[i]))
        fill = px[i + 1] if i + 1 < len(px) else px[-1]
        if pos == 0:
            pos, entry = s, fill
        elif pos != s:                     # flip: close + reverse (1 spread per
            trades.append((pos * (fill - entry), pos))   # phase, paid at close)
            pos, entry = s, fill
    if pos != 0:
        trades.append((pos * (px[-1] - entry), pos))
    return trades


def path_sim(tr_usd, start_eq=10.0, step_usd=10.0):
    """Ruin-aware compounding walk over the ACTUAL trade sequence.
    Exposure = one 0.1-lot step per $step_usd of equity. Returns dict."""
    eq = start_eq
    min_eq = eq
    for t in tr_usd:
        steps = max(1, int(eq // step_usd))
        eq += t * steps
        min_eq = min(min_eq, eq)
        if eq <= 0:
            return dict(final=0.0, min_eq=0.0, ruined=True)
    return dict(final=eq, min_eq=min_eq, ruined=False)


def bootstrap_odds(tr_usd, n_sims=1000, start_eqs=(10.0, 20.0, 50.0),
                   step_usd=10.0, target=100.0, seed=7):
    """Resample the week's per-phase net PnLs (iid) into n_sims walks per
    starting equity. Returns {start_eq: (ruin%, flip%, median_final)}.
    iid resampling ignores loss clustering -> optimistic; stated as such."""
    rng = np.random.default_rng(seed)
    out = {}
    n = len(tr_usd)
    for s0 in start_eqs:
        ruined = 0
        flipped = 0
        finals = []
        for _ in range(n_sims):
            eq = s0
            dead = False
            for t in rng.choice(tr_usd, size=n, replace=True):
                steps = max(1, int(eq // step_usd))
                eq += t * steps
                if eq <= 0:
                    dead = True
                    break
            if dead:
                ruined += 1
            else:
                finals.append(eq)
                if eq >= target:
                    flipped += 1
        med = float(np.median(finals)) if finals else 0.0
        out[s0] = (ruined / n_sims * 100, flipped / n_sims * 100, med)
    return out


def max_drawdown(cum):
    peak = np.maximum.accumulate(cum)
    return float(np.max(peak - cum))


MT5_NAMES = {
    "flip150": "Crash Boom Flip 150 Index",
    "flip300": "Crash Boom Flip 300 Index",
    "flip500": "Crash Boom Flip 500 Index",
    "flip1000": "Crash Boom Flip 1000 Index",
}


def get_specs():
    """Live contract specs (read-only) for spread + USD conversion."""
    import MetaTrader5 as mt5
    if not mt5.initialize():
        raise RuntimeError(f"MT5 init failed: {mt5.last_error()}")
    specs = {}
    for sym in FLIPS:
        info = mt5.symbol_info(MT5_NAMES[sym])
        if info is None:
            raise RuntimeError(f"no symbol_info for {MT5_NAMES[sym]}")
        specs[sym] = dict(
            point=info.point,
            tick_value=info.trade_tick_value,
            tick_size=info.trade_tick_size,
            spread_pts=info.spread,
            vol_min=info.volume_min,
            bid=info.bid,
        )
        mt5.symbol_select(sym, True)
    mt5.shutdown()
    return specs


def main():
    specs = get_specs()
    print(f"specs: spread(pts)/point/tick_value/tick_size/vol_min")
    for s, v in specs.items():
        print(f"  {s:9s} {v['spread_pts']:>3}  {v['point']:<10g} "
              f"{v['tick_value']:<8g} {v['tick_size']:<10g} {v['vol_min']}")

    for sym in FLIPS:
        ms, px = load(sym)
        sp = specs[sym]
        # USD per 1.0 price unit at minimum lot
        vpu = sp["tick_value"] / sp["tick_size"] * sp["vol_min"]
        spread_units = sp["spread_pts"] * sp["point"]
        dur_wk = (ms[-1] - ms[0]) / (3.6e6 * 168)

        d = np.diff(px)
        med = float(np.median(np.abs(d[:WARMUP])))

        print("=" * 76)
        print(f"  {sym.upper()}  ticks={len(px):,}  {dur_wk:.2f}wk  median_delta={med:.5f}  "
              f"spread={spread_units:g} ({sp['spread_pts']} pts)  vpu@{sp['vol_min']}lot=${vpu:.5f}/unit")
        print("=" * 76)

        for mult in MULTS:
            trades = simulate(px, med, mult, spread_units, ms=ms)
            tr = np.array([t for t, _ in trades])
            dirs = np.array([dd for _, dd in trades])
            n = len(tr)
            if n == 0:
                print(f"  mult={mult}: no trades\n")
                continue
            gross = tr.sum()
            friction = n * spread_units
            net = gross - friction
            wins = tr[tr > 0]
            losses = tr[tr <= 0]
            cum = np.cumsum(tr)          # gross cum for MDD shape
            worst = tr.min()

            net_usd = net * vpu
            worst_usd = worst * vpu
            mdd_usd = max_drawdown(cum) * vpu

            print(f"  mult={mult}: phases={n} ({n/dur_wk:.0f}/wk)  "
                  f"WR={len(wins)/n*100:.1f}%  avgW={wins.mean() if len(wins) else 0:+.1f}u  "
                  f"avgL={losses.mean() if len(losses) else 0:+.1f}u")
            print(f"    gross {gross:+.0f}u  friction {friction:.0f}u  NET {net:+.0f}u "
                  f"= ${net_usd:+.2f}/wk @ {sp['vol_min']} lot")
            print(f"    worst phase {worst:+.1f}u (${worst_usd:+.2f})  maxDD ${mdd_usd:+.2f}")

            # stability + ruin analysis only where there is an edge to test
            if net_usd > 0:
                half = n // 2
                h1 = (tr[:half].sum() - half * spread_units) * vpu
                h2 = (tr[half:].sum() - (n - half) * spread_units) * vpu
                longs = tr[dirs == 1]
                shorts = tr[dirs == -1]
                net_l = (longs.sum() - len(longs) * spread_units) * vpu
                net_s = (shorts.sum() - len(shorts) * spread_units) * vpu
                tr_usd = tr * vpu - spread_units * vpu   # net per phase, in USD
                ps = path_sim(tr_usd)
                print(f"    stability: H1 ${h1:+.2f} / H2 ${h2:+.2f}   "
                      f"longs ${net_l:+.2f} (n={len(longs)}) / shorts ${net_s:+.2f} (n={len(shorts)})")
                print(f"    $10 path sim (0.1-lot step per $10): "
                      f"final ${ps['final']:.2f}  min equity ${ps['min_eq']:.2f}  "
                      f"ruined={ps['ruined']}")
                odds = bootstrap_odds(tr_usd)
                for s0, (ru, fl, med_fin) in odds.items():
                    print(f"    bootstrap ${s0:>4.0f} start: ruin {ru:.0f}%   "
                          f"flip->$100 {fl:.1f}%   median final ${med_fin:.2f}")
        print()


if __name__ == "__main__":
    main()
