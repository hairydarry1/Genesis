#!/usr/bin/env python3
"""Grinddown forensics for flip1000 (triggered by the Sep 12 -$5.37 trade).

The live loss: long entered on a +1 up-spike (age 1), phase stayed +1 x2..x6
for 106 minutes while price DRIFTED down 52u on 6,303 down ticks vs 79 up
ticks. No counter-spike crossed 5x median until the closing -11.26u spike.
So: the detector tracks spike direction, PnL tracks drift, and they diverged.

Questions this script answers on the full archive (same conventions as
flip_concepts.py: frozen median @WARMUP=5000, |d| > 5x, min_age=1 gate,
live spread per episode, $0.10/unit at 0.1 lot):
  1. How common are one-sided drift windows (pct of small moves down over a
     sliding 60-min window)? Where does today's ~98% sit in the distribution?
  2. Do grind windows change the up/down spike balance, or do up-spikes keep
     arriving at the normal rate while price bleeds?
  3. At the live config, what does the full loss distribution look like —
     where does -$5.37 rank? What is the MAE (max adverse excursion)
     distribution in units — where does today's ~56u rank?
  4. Decompose every big loss into drift PnL vs spike PnL: is the tail made
     of drift (detector-blind) or spikes (counter-spike cost)?

Usage: python analysis/flip_grind_forensics.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_concepts import LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS, simulate
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]  # $/unit/1.0 lot
WIN_S = 3600          # 1-hour sliding window for drift census
STEP_S = 600          # census step
LIVE_LOSS = -5.37     # Sep 12 realized loss (usd)
LIVE_MAE_U = 55.9     # worst tick vs entry during the hold (mid, 9584.1 vs 9640.01)
LIVE_OPEN = datetime(2026, 9, 12, 17, 42, tzinfo=timezone.utc)
LIVE_CLOSE = datetime(2026, 9, 12, 19, 28, tzinfo=timezone.utc)


def utss(*a):
    return datetime(*a, tzinfo=timezone.utc).timestamp()


def main():
    ms, px = load(SYMBOL)
    d = np.diff(px)
    med = float(np.median(np.abs(d[:WARMUP])))
    thr = MULT * med
    big_idx = np.where(np.abs(d) > thr)[0]
    big_idx = big_idx[big_idx >= WARMUP]
    days = (ms[-1] - ms[0]) / 86400e3
    print(f"archive: {len(ms)} ticks, {days:.1f} days, med|d|={med:.5f}, "
          f"thr={thr:.5f}, spikes={len(big_idx)} ({len(big_idx)/days:.1f}/day)")

    # ---- 1. drift census: sliding 60-min window, pct of small moves down ----
    small_mask = np.abs(d) <= thr
    ts_s = ms / 1000.0
    edges = np.arange(ts_s[WARMUP], ts_s[-1] - WIN_S, STEP_S)
    pct_down = np.empty(len(edges))
    for i, e in enumerate(edges):
        a, b = np.searchsorted(ts_s, [e, e + WIN_S])
        w = d[a:max(b, a + 1)]
        w = w[np.abs(w) <= thr]
        pct_down[i] = (w < 0).mean() if len(w) else 0.5
    print("\n--- 1. drift census (60-min sliding window, small moves only)")
    print(f"    windows: {len(pct_down)}  mean pct_down={pct_down.mean():.3f}")
    for q in (0.5, 0.9, 0.95, 0.99):
        print(f"    p{int(q*100)} pct_down = {np.quantile(pct_down, q):.3f}")
    print(f"    windows >=85% down: {(pct_down>=0.85).mean()*100:.2f}%   "
          f">=90%: {(pct_down>=0.90).mean()*100:.2f}%   "
          f">=95%: {(pct_down>=0.95).mean()*100:.2f}%")
    a, b = np.searchsorted(ts_s, [utss(2026,9,12,17,42), utss(2026,9,12,19,28)])
    w = d[a:max(b, a + 1)]
    w = w[np.abs(w) <= thr]
    pd_today = (w < 0).mean()
    rank = (pct_down < pd_today).mean() * 100
    print(f"    TODAY's hold window: pct_down={pd_today:.3f} -> more "
          f"one-sided than {rank:.2f}% of all windows")

    # ---- 2. spike balance: worst-decile drift windows vs the rest ----
    print("\n--- 2. spike balance inside worst-decile drift windows")
    thr90 = np.quantile(pct_down, 0.9)
    up_w = dn_w = 0
    for i in np.where(pct_down >= thr90)[0]:
        e = edges[i]
        a, b = np.searchsorted(ts_s, [e, e + WIN_S])
        for j in big_idx:
            if a <= j < b:
                if d[j] > 0:
                    up_w += 1
                else:
                    dn_w += 1
    up_r = int((d[big_idx] > 0).sum())
    print(f"    worst-decile windows: up-spikes={up_w} down-spikes={dn_w} "
          f"(up share {up_w/max(up_w+dn_w,1):.2f})")
    print(f"    full archive:         up-spikes={up_r} down-spikes={len(big_idx)-up_r} "
          f"(up share {up_r/len(big_idx):.2f})")

    # ---- 3. loss + MAE distribution at the live config ----
    print("\n--- 3. live-config backtest over the full archive (mode=base)")
    trades, n_trips = simulate(ms, px, med, mult=MULT, min_age=MIN_AGE,
                               mode="base", side=0)
    nets = np.array([t[0] for t in trades])
    wins = nets[nets > 0]
    losses = np.sort(nets[nets <= 0])
    print(f"    trips={n_trips}  WR={len(wins)/n_trips*100:.1f}%  "
          f"net=${nets.sum():+.2f}  avg win=${wins.mean():+.2f}  "
          f"avg loss=${losses.mean():+.2f}")
    print(f"    loss dist (usd): p50={np.quantile(losses,0.5):+.3f} "
          f"p90={np.quantile(losses,0.9):+.3f} p99={np.quantile(losses,0.99):+.3f} "
          f"worst={losses.min():+.2f}")
    rank = int((losses > LIVE_LOSS).sum())
    print(f"    today's -${abs(LIVE_LOSS):.2f}: worse than {rank}/{len(losses)} "
          f"backtest losses ({rank/len(losses)*100:.2f}% of the loss tail; "
          f"a uniform draw would put it there {(100/n_trips):.2f}% of the time)")

    # ---- 4. MAE + drift/spike decomposition per trade ----
    print("\n--- 4. per-trade MAE + decomposition (exact: drift + fav-spikes")
    print("    + exit-spike + spread = net)")
    mae_u, decomps = walk_with_decomp(ms, px, med)
    mae_u = np.sort(np.array(mae_u))
    print(f"    MAE (price units): p50={np.quantile(mae_u,0.5):.1f} "
          f"p90={np.quantile(mae_u,0.9):.1f} p99={np.quantile(mae_u,0.99):.1f} "
          f"worst={mae_u[-1]:.1f}")
    rank = int((mae_u < LIVE_MAE_U).sum())
    print(f"    today's {LIVE_MAE_U:.0f}u adverse excursion: worse than "
          f"{rank}/{len(mae_u)} trades ({rank/len(mae_u)*100:.2f}%)")
    resid = np.array([t["net"] - (t["drift"] + t["spike"] + t["exit"] + t["spread"])
                      for t in decomps])
    print(f"    decomposition residual: max |net - parts| = {np.abs(resid).max():.2e} "
          f"(should be ~0)")
    big_losses = sorted(decomps, key=lambda x: x["net"])[:6]
    print("    six biggest losses:")
    print("     net$    drift$  favspk$  exit$  spread$ n_spk  mae_u  dur_m  open(UTC)")
    for t in big_losses:
        print(f"     {t['net']:+6.2f} {t['drift']:+7.2f} {t['spike']:+7.2f} "
              f"{t['exit']:+6.2f} {t['spread']:6.2f} {t['n_spk']:5d} {t['mae']:6.1f} "
              f"{t['dur_min']:5.0f}  {t['t_str']}")
    tail = [t for t in decomps if t["net"] < -1.0]
    if tail:
        ds = np.array([abs(t["drift"]) / max(abs(t["net"]), 1e-9) for t in tail])
        print(f"    losses >$1 in backtest (n={len(tail)}): drift share of loss "
              f"p50={np.quantile(ds,0.5):.2f} p90={np.quantile(ds,0.9):.2f}")


def walk_with_decomp(ms, px, med):
    """Re-run the base strategy (mode=base, side=0), recording per trade:
    MAE, drift sum (small ticks), favorable-spike sum, exit-spike delta."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)                    # gap-masking
    per_unit = STEP_LOTS * VPU            # $ per price unit at 0.1 lot
    spread_usd = LIVE_SPREAD * per_unit

    pos = 0.0
    entry = 0.0
    entry_ms = 0
    cur_dir, cnt = 0, 0
    drift_sum = spike_sum = 0.0
    exit_spike = 0.0
    n_spk = 0
    worst = 0.0
    mae_u, decomps = [], []

    def finish(fill, tms, exs):
        nonlocal pos, n_spk, drift_sum, spike_sum, worst, exit_spike
        gross = (fill - entry) * pos * VPU * STEP_LOTS / STEP_LOTS  # = (fill-entry)*pos*VPU
        net = (fill - entry) * pos * VPU - spread_usd
        mae_u.append(-worst)
        decomps.append({
            "net": net,
            "drift": drift_sum * per_unit * np.sign(pos),
            "spike": spike_sum * per_unit * np.sign(pos),
            "exit": exs * per_unit * np.sign(pos),
            "spread": -spread_usd,
            "n_spk": n_spk, "mae": -worst,
            "dur_min": (tms - entry_ms) / 60e3,
            "t_str": datetime.fromtimestamp(entry_ms / 1000, tz=timezone.utc)
                     .strftime("%m-%d %H:%M"),
        })
        pos = 0.0
        n_spk = 0
        drift_sum = spike_sum = 0.0
        exit_spike = 0.0
        worst = 0.0

    n = len(px)
    for j in range(WARMUP, n):
        if bad[j]:                       # hole: drop position + all state
            pos = 0.0
            cur_dir, cnt = 0, 0
            drift_sum = spike_sum = exit_spike = 0.0
            n_spk = 0
            worst = 0.0
            continue
        if pos != 0:
            worst = min(worst, (px[j] - entry) * np.sign(pos))
        if big[j - 1]:
            s = int(np.sign(d[j - 1]))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            if pos != 0 and s != np.sign(pos):
                finish(px[j], ms[j], d[j - 1])
                continue
        else:
            if pos != 0:
                drift_sum += d[j - 1]
        if pos == 0:
            if cnt >= MIN_AGE + 1 and big[j - 1]:
                pos, entry, entry_ms = STEP_LOTS * cur_dir, px[j], ms[j]
                drift_sum = spike_sum = exit_spike = 0.0
                n_spk = 1
                worst = 0.0
        else:
            if big[j - 1] and int(np.sign(d[j - 1])) == np.sign(pos):
                spike_sum += d[j - 1]
                n_spk += 1
    return mae_u, decomps


if __name__ == "__main__":
    main()
