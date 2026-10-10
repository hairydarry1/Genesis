#!/usr/bin/env python3
"""Can grinddown regimes be detected at entry time? (flip1000, pre-28d study)

Motivation: Sep 12 live loss -$5.37. Long entered at phase age 1 while the
book was bleeding down (98.8% of small ticks down over the hold); up-spikes
kept firing against the bleed and the phase counter voted long. If the
pre-entry drift state is observable (it is: it is just recent tick history),
does conditioning entries on it improve the strategy?

Key structural fact: at the live gate (min_age=1) EVERY entry happens at
phase age 1 (2nd consecutive spike), so phase-age features carry zero
variation at entry. The candidate discriminators are drift/regime features
computed on ticks strictly before the fill.

Features (all computable in real time at fill):
  pct_dn_60m   share of small (non-spike) ticks that are down, last 60 min
  drift_60m    net small-tick move over last 60 min (price units, signed)
  div_5m       # up-spikes minus # down-spikes, last 5 min
  sp_amp_5m    sum of |spike| amplitudes, last 5 min (bounce intensity)
  med60        rolling median |small delta|, last 60 min (vol regime)
  since_flip   minutes since the spike direction last flipped (phase age in time)
  div_ratio_60m  up-spike amplitude sum / max(-drift_60m, 0.1): bounce-vs-bleed

Outputs:
  1. per-trade feature table -> terciles vs outcome (mean net, WR, worst, MAE p90)
  2. features of the six biggest losses (do they share a signature?)
  3. filter backtests: skip LONG entries when pre-entry state says "bleeding"
     (pct_dn_60m > T or drift_60m < -X), LS and long-only, vs incumbent
  4. day-block survival ($10, fixed lot, 4wk) for incumbent vs best filter

Caveats printed with results: 176 trades / ~10 days, one ~20h unhealable
archive hole masked out, in-sample feature mining (pre-registered for the
28-day re-test, not adopted).

Usage: python analysis/flip_grind_prediction.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_gapmask import bad_tick_mask, hole_intervals, crosses_hole
from flip_conditionality import load, WARMUP
from flip_concepts import (LIVE_SPREAD, MIN_AGE, MULT, STEP_LOTS,
                           day_block_survival)
from flip_phase_backtest import get_specs

SYMBOL = "flip1000"
VPU = get_specs()[SYMBOL]["tick_value"] / get_specs()[SYMBOL]["tick_size"]
PER_UNIT = STEP_LOTS * VPU
SPREAD_USD = LIVE_SPREAD * PER_UNIT
MASK_GAP_S = 300        # trades touching a >300s data gap are dropped


def utc_str(tms):
    return datetime.fromtimestamp(tms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


class Features:
    """Sliding-window pre-entry features from prefix sums (O(1) per query)."""

    def __init__(self, ms, px, med):
        self.ms = ms
        self.px = px
        self.thr = MULT * med
        d = np.diff(px)
        self.d = d
        big = np.abs(d) > self.thr
        small = ~big
        n = len(d)
        z = lambda x: np.concatenate([[0.0], np.cumsum(x)])
        self.c_small = np.concatenate([[0], np.cumsum(small)])
        self.c_dn = np.concatenate([[0], np.cumsum(small & (d < 0))])
        self.cs_small_d = z(np.where(small, d, 0.0))
        self.div = z(np.where(big, np.sign(d), 0.0))
        self.sp_amp = z(np.where(big, np.abs(d), 0.0))
        self.med = med

    def window(self, j, secs):
        """d-index range [a, b) covering (ms[j]-secs, ms[j]); b lands before
        the current spike d[j-1] which is added by the caller if wanted."""
        t = self.ms[j] - secs * 1000
        a = np.searchsorted(self.ms, t)
        b = j - 1  # d indices a..j-2 are strictly before the current move
        if b <= a:
            a = b
        return a, b

    def at(self, j):
        a60, b60 = self.window(j, 3600)
        a5, b5 = self.window(j, 300)
        n_sm = self.c_small[b60] - self.c_small[a60]
        pct_dn = ((self.c_dn[b60] - self.c_dn[a60]) / n_sm) if n_sm >= 30 else 0.5
        drift = self.cs_small_d[b60] - self.cs_small_d[a60]
        div5 = self.div[b5] - self.div[a5]
        sp5 = self.sp_amp[b5] - self.sp_amp[a5]
        seg = self.d[a60:b60]
        seg = seg[np.abs(seg) <= self.thr]
        med60 = float(np.median(np.abs(seg))) if len(seg) > 30 else self.med
        up_amp = self.sp_amp[b60] - self.sp_amp[a60]
        up_only = up_amp  # includes both dirs; up share computed from div
        div60 = self.div[b60] - self.div[a60]
        up_amp60 = (up_only + div60 * self.thr) / 2.0  # (up+down)/2+div/2*thr
        return dict(pct_dn_60m=pct_dn, drift_60m=drift, div_5m=div5,
                    sp_amp_5m=sp5, med60=med60, div_ratio_60m=up_amp60 / max(-drift, 0.1))


def collect_trades(ms, px, feats, side=0, skip=None):
    """Base phase-follower with optional entry filter. Returns (trades, feats).
    trades = [(net, close_ms, entry_ms, is_long, mae_units)], feats aligned."""
    thr = MULT * med
    d = np.diff(px)
    big = np.abs(d) > thr
    bad = bad_tick_mask(ms)                    # gap-masking
    trades, frows = [], []
    pos = 0.0
    entry = 0.0
    entry_ms = 0
    entry_j = 0
    cur_dir, cnt = 0, 0
    worst = 0.0
    last_flip_ms = None

    def close(fill, tms):
        nonlocal pos, worst
        net = (fill - entry) * pos * VPU - SPREAD_USD
        trades.append((net, tms, entry_ms, pos > 0, -worst))
        pos = 0.0
        worst = 0.0

    for j in range(WARMUP, len(px)):
        if bad[j]:                       # hole: drop position + phase memory
            pos = 0.0
            cur_dir, cnt = 0, 0
            last_flip_ms = None
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
                last_flip_ms = ms[j]
            if pos != 0 and s != np.sign(pos):
                close(px[j], ms[j])
                continue
        if pos == 0 and cnt >= MIN_AGE + 1 and big[j - 1]:
            want = (side == 0) or (cur_dir == side)
            f = feats.at(j)
            f["since_flip"] = ((ms[j] - last_flip_ms) / 60e3
                               if last_flip_ms else np.nan)
            if want and (skip is None or not skip(f, cur_dir)):
                pos, entry, entry_ms, entry_j = STEP_LOTS * cur_dir, px[j], ms[j], j
                worst = 0.0
                frows.append((f, cur_dir))
    return trades, frows


def gap_mask(ms, trades):
    """Drop trades whose [entry, close] span crosses a >MASK_GAP_S gap."""
    dts = np.diff(ms)
    hole_starts = np.where(dts > MASK_GAP_S * 1000)[0]
    hole_lo = ms[hole_starts]
    hole_hi = ms[hole_starts + 1]
    keep = []
    for t in trades:
        e, c = t[2], t[1]
        bad = ((hole_lo < c) & (hole_hi > e)).any()
        keep.append(not bad)
    return keep


def tercile_table(name, vals, nets, wr_den, maes):
    print(f"\n  feature: {name}")
    qs = np.quantile(vals, [1 / 3, 2 / 3])
    labs = ["low", "mid", "high"]
    for i, lab in enumerate(labs):
        m = (vals >= qs[i - 1]) & (vals < qs[i]) if i == 1 else (
            vals < qs[0] if i == 0 else vals >= qs[1])
        if m.sum() == 0:
            continue
        nn = nets[m]
        print(f"    {lab:4s} n={m.sum():3d}  mean net ${nn.mean():+6.2f}  "
              f"WR {(nn > 0).mean() * 100:4.1f}%  worst ${nn.min():+6.2f}  "
              f"MAE p90 {np.quantile(maes[m], 0.9):5.1f}u")


def run_filter(ms, px, med, feats, rule, label, days):
    trades, _ = collect_trades(ms, px, feats, skip=rule)
    if not trades:
        print(f"  {label:34s} no trades")
        return trades
    nets = np.array([t[0] for t in trades])
    eq = np.cumsum(nets)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    wins = nets[nets > 0]
    losses = nets[nets <= 0]
    print(f"  {label:34s} trips={len(nets):3d}  WR {len(wins)/len(nets)*100:4.1f}%  "
          f"net ${nets.sum():+6.2f} (${nets.sum()/days*7:+5.2f}/wk)  "
          f"avgW ${wins.mean() if len(wins) else 0:+5.2f}  "
          f"avgL ${losses.mean() if len(losses) else 0:+5.2f}  "
          f"worst ${losses.min() if len(losses) else 0:+5.2f}  maxDD ${dd:5.2f}")
    return trades


def main():
    global med
    ms, px = load(SYMBOL)
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    days = (ms[-1] - ms[0]) / 86400e3
    print(f"archive: {days:.1f} days, {len(ms):,} ticks, med={med:.5f}")

    feats = Features(ms, px, med)
    trades, frows = collect_trades(ms, px, feats, side=0, skip=None)

    # mask trades touching the unhealable archive hole
    keep = gap_mask(ms, trades)
    trades = [t for t, k in zip(trades, keep) if k]
    frows = [fr for fr, k in zip(frows, keep) if k]
    print(f"trades kept after gap-mask: {len(trades)} "
          f"(dropped {len(keep) - sum(keep)})")

    nets = np.array([t[0] for t in trades])
    maes = np.array([t[4] for t in trades])
    is_long = np.array([t[3] for t in trades])
    print(f"incumbent (masked): net ${nets.sum():+.2f}, WR {(nets>0).mean()*100:.1f}%, "
          f"worst ${nets.min():+.2f}")

    # ---- 1. feature terciles vs outcome (longs only: the decision we filter) ----
    print("\n--- 1. terciles vs outcome (LONG entries only, n=%d) ---"
          % is_long.sum())
    keys = ["pct_dn_60m", "drift_60m", "div_5m", "sp_amp_5m", "med60",
            "div_ratio_60m", "since_flip"]
    li = np.where(is_long)[0]
    for k in keys:
        vals = np.array([f[k] for f, s in frows], dtype=float)[li]
        ok = np.isfinite(vals)
        tercile_table(k, vals[ok], nets[li][ok], None, maes[li][ok])

    # ---- 2. the six biggest losses: shared signature? ----
    print("\n--- 2. six biggest losses: pre-entry features ---")
    order = np.argsort(nets)[:6]
    hdr = ["net$", "pct_dn60", "drift60", "div5", "sp_amp5", "med60",
           "divR60", "since_flip", "side", "open(UTC)"]
    print("  " + "  ".join(f"{h:>9s}" for h in hdr))
    for i in order:
        f, s = frows[i]
        row = [f"{nets[i]:+9.2f}", f"{f['pct_dn_60m']:9.2f}", f"{f['drift_60m']:9.1f}",
               f"{f['div_5m']:9.0f}", f"{f['sp_amp_5m']:9.2f}", f"{f['med60']:9.5f}",
               f"{f['div_ratio_60m']:9.2f}", f"{f['since_flip']:9.0f}",
               f"{'L' if is_long[i] else 'S':>9s}", utc_str(trades[i][2]).rjust(9)]
        print("  " + "  ".join(row))

    # ---- 3. filter backtests ----
    print("\n--- 3. filter backtests (skip rule applied at entry) ---")
    print("  [LS incumbent for reference]")
    run_filter(ms, px, med, feats, None, "LS no filter", days)
    print("  [candidate filters]")

    def mk_pct(thr_):
        def r(f, cur_dir):
            return cur_dir == 1 and f["pct_dn_60m"] > thr_
        return r

    def mk_drift(x):
        def r(f, cur_dir):
            return cur_dir == 1 and f["drift_60m"] < -x
        return r

    def mk_pct_any(thr_):
        def r(f, cur_dir):
            return f["pct_dn_60m"] > thr_
        return r

    for t_ in (0.60, 0.70, 0.80):
        run_filter(ms, px, med, feats, mk_pct(t_), f"L skip pct_dn>{t_:.2f}", days)
    for x in (5.0, 10.0):
        run_filter(ms, px, med, feats, mk_drift(x), f"L skip drift<-{x:.0f}u", days)
    run_filter(ms, px, med, feats, mk_pct_any(0.80), "LS skip pct_dn>0.80", days)

    # NOTE: run_filter's collect_trades applies side via closure; for
    # long-only runs re-collect with side=1
    print("  [long-only precise runs]")
    for label, rule in (("L no filter", None),
                        ("L skip pct_dn>0.70", mk_pct(0.70)),
                        ("L skip drift<-10u", mk_drift(10.0))):
        tr, _ = collect_trades(ms, px, feats, side=1, skip=rule)
        keep = gap_mask(ms, tr)
        tr = [t for t, k in zip(tr, keep) if k]
        nn = np.array([x[0] for x in tr])
        eq = np.cumsum(nn)
        dd = float((np.maximum.accumulate(eq) - eq).max())
        w_ = nn[nn > 0]
        l_ = nn[nn <= 0]
        print(f"  {label:34s} trips={len(nn):3d}  WR {len(w_)/len(nn)*100:4.1f}%  "
              f"net ${nn.sum():+6.2f} (${nn.sum()/days*7:+5.2f}/wk)  "
              f"avgW ${w_.mean() if len(w_) else 0:+5.2f}  "
              f"avgL ${l_.mean() if len(l_) else 0:+5.2f}  "
              f"worst ${l_.min() if len(l_) else 0:+5.2f}  maxDD ${dd:5.2f}")
        if "no filter" in label or "0.70" in label:
            surv_trades = [(x[0], x[1]) for x in tr]
            f_, r_ = day_block_survival(surv_trades)
            print(f"    -> survival: flip {f_:.1f}%  ruin {r_:.1f}%  "
                  f"(2000 sims, 28d, $10 fixed lot)")

    print("\ncaveats: ~10 days of data, 176 trades, one ~20h unhealable hole "
          "masked,\nin-sample feature mining. Pre-registered: re-run on the "
          "28-day archive\nat Oct 8; adopt only if the tercile ordering "
          "persists and survival improves.")


if __name__ == "__main__":
    main()
