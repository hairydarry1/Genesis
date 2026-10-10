#!/usr/bin/env python3
"""Adversarial re-test of the Flip study (challenge response).

A1. Spread audit: static info.spread vs live tick spread (all 4 symbols).
A2. Corrected economics: every backtest config re-netted at the live spread.
A3. Rolling-median vs frozen-median detection parity (flip1000, pandas).
A4. Survival re-run with corrected per-phase PnL (iid + historical path).
"""
import sys

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import simulate_ma, load, WARMUP
from flip_phase_backtest import get_specs, path_sim

CONFIGS = [
    ("flip1000", 5.0, 1), ("flip1000", 5.0, 0),
    ("flip500", 3.5, 2), ("flip500", 3.5, 0),
    ("flip300", 3.0, 0),
]
MODEL_SPREAD = 0.05


def live_spreads():
    import MetaTrader5 as mt5
    mt5.initialize()
    out = {}
    for sym, name in [("flip150", "Crash Boom Flip 150 Index"),
                      ("flip300", "Crash Boom Flip 300 Index"),
                      ("flip500", "Crash Boom Flip 500 Index"),
                      ("flip1000", "Crash Boom Flip 1000 Index")]:
        t = mt5.symbol_info_tick(name)
        out[sym] = (t.ask - t.bid, mt5.symbol_info(name).spread)
    mt5.shutdown()
    return out


def main():
    spreads = live_spreads()
    print("A1. SPREAD AUDIT (live tick vs static info.spread)")
    for s, (live, static) in spreads.items():
        print(f"  {s:9s} live {live:.4f}u   info.spread {static * 1e-4:.4f}u   "
              f"ratio {live / (static * 1e-4):.1f}x")

    print("\nA2. CORRECTED ECONOMICS (re-net every config at live spread)")
    corrected = {}
    for sym, mult, k in CONFIGS:
        ms, px = load(sym)
        med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
        sp = get_specs()[sym]
        vpu = sp["tick_value"] / sp["tick_size"] * sp["vol_min"]
        tr = np.array(simulate_ma(px, med, mult, min_age=k, ms=ms, start=WARMUP))
        n = len(tr)
        dur_wk = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7     # weekly denominator
        gross_usd = tr.sum() * vpu          # raw price PnL (simulate_ma never
                                            # subtracted a spread: nothing to add back)
        live_sp = spreads[sym][0]
        net_old = tr.sum() * vpu
        net_new = gross_usd - n * live_sp * vpu
        corrected[(sym, mult, k)] = (net_new, n)
        print(f"  {sym}@{mult} k={k}: n={n:5d}  gross ${net_old / dur_wk:+8.2f}/wk"
              f"  ->  net(live sp) ${net_new / dur_wk:+8.2f}/wk   "
              f"per-trade ${net_new / n:+.3f}"
              .replace("$+nan", "  nan"))

    print("\nA3. ROLLING vs FROZEN median detection (flip1000 @5.0)")
    import pandas as pd
    ms, px = load("flip1000")
    d = np.diff(px)
    med_frozen = float(np.median(np.abs(d[:WARMUP])))
    # executor semantics: append-then-compute, window 5000
    s_pd = pd.Series(np.abs(d))
    med_roll = s_pd.rolling(5000, min_periods=5000).median().to_numpy()
    big_frozen = np.where(np.abs(d) > 5.0 * med_frozen)[0]
    big_roll = np.where((np.abs(d) > 5.0 * med_roll) & (np.arange(len(d)) >= 5000))[0]
    print(f"  frozen: {len(big_frozen)} spikes   rolling: {len(big_roll)} spikes"
          f"   ({len(big_roll) / len(big_frozen) * 100:.1f}%)")
    # how many rolling spikes the frozen detector missed and vice versa
    set_f, set_r = set(big_frozen.tolist()), set(big_roll.tolist())
    print(f"  rolling-only spikes: {len(set_r - set_f)}   frozen-only: {len(set_f - set_r)}")
    tr_f = simulate_ma(px, med_frozen, 5.0, min_age=1, ms=ms, start=WARMUP)
    # rolling-median variant of the phase sim (threshold per-tick array)
    def sim_roll(big_idx):
        pos, entry, cur_dir, cnt, trades = 0, 0.0, 0, 0, []
        for i in big_idx:
            s = int(np.sign(d[i]))
            fill = px[i + 1] if i + 1 < len(px) else px[-1]
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos == 0:
                if cnt >= 2:
                    pos, entry = s, fill
            elif pos != s:
                trades.append(pos * (fill - entry))
                pos = 0
                if cnt >= 2:
                    pos, entry = s, fill
        if pos:
            trades.append(pos * (px[-1] - entry))
        return trades
    tr_r = sim_roll(big_roll[(big_roll + 1) >= WARMUP])
    vpu = get_specs()["flip1000"]["tick_value"] / get_specs()["flip1000"]["tick_size"] * 0.1
    live_sp = spreads["flip1000"][0]
    dur_wk3 = (ms[-1] - ms[WARMUP]) / 86_400_000 / 7
    print(f"  frozen-median net(live sp) ${np.sum(tr_f) * vpu - len(tr_f) * live_sp * vpu:+.2f}/wk "
          f"({len(tr_f)} trades, over {dur_wk3:.2f}wk)")
    print(f"  rolling-median net(live sp) ${np.sum(tr_r) * vpu - len(tr_r) * live_sp * vpu:+.2f}/wk "
          f"({len(tr_r)} trades, over {dur_wk3:.2f}wk)")

    print("\nA4. SURVIVAL WITH CORRECTED PnL (flip1000@5 k=1, $10, 4-week horizon)")
    net_new, n = corrected[("flip1000", 5.0, 1)]
    tr_old = None
    ms, px = load("flip1000")
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    tr_old = np.array(simulate_ma(px, med, 5.0, min_age=1, ms=ms, start=WARMUP))
    vpu = get_specs()["flip1000"]["tick_value"] / get_specs()["flip1000"]["tick_size"] * 0.1
    tr_corr = (tr_old - spreads["flip1000"][0]) * vpu   # raw PnL - live spread, same as concepts
    rng = np.random.default_rng(5)
    SIMS, WEEKS = 4000, 4
    ruined = flipped = alive = 0
    for s in range(SIMS):
        eq = 10.0
        done = False
        for w in range(WEEKS):
            for t in rng.choice(tr_corr, size=len(tr_corr), replace=True):
                steps = max(1, int(eq // 10))
                eq += t * steps
                if eq <= 0:
                    eq, done = 0.0, True
                    break
            if done or eq >= 100:
                break
        if eq <= 0:
            ruined += 1
        elif eq >= 100:
            flipped += 1
        else:
            alive += 1
    print(f"  iid bootstrap: ruined {ruined / SIMS * 100:.1f}%  "
          f"flipped {flipped / SIMS * 100:.1f}%  alive {alive / SIMS * 100:.1f}%")
    ps = path_sim(tr_corr)
    print(f"  historical path (clustering intact): final ${ps['final']:.2f}  "
          f"min ${ps['min_eq']:.2f}  ruined={ps['ruined']}")


if __name__ == "__main__":
    main()
