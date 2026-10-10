#!/usr/bin/env python3
"""
PORTFOLIO MONTE CARLO - monthly equity distribution at the Kelly-corrected
risk levels, worst months, and intra-month drawdowns.

Model (calibrated from the verified corrected engine, ~30 days of candles):
  - Per symbol, per day: empirical daily R-return and daily point-PnL panels
    from the VERIFIED per-symbol configs (30-31 days each).
  - A month = 30 simulated days, bootstrap-drawn per symbol and coupled
    across the 5 symbols with a Gaussian copula at the measured (shrunk)
    cross-symbol daily correlation (near zero - all |r| < 0.23).
  - LOSS-GAP FRAMES: the M1 sim understates live single-tick gap losses
    (measured live SL hits run 4-9x SL vs ~2x in the bar sim). We sweep a
    uniform multiplier m on all loss-side draws:
        m = 1.0  "sim tails"   (optimistic upper bound)
        m = 1.5  mild friction
        m = 2.0  realistic live gaps (crash1000 measured ~2-4x)
        m = 2.5  severe
    The per-symbol EV breakeven multiplier (where monthly wins == monthly
    losses) is computed analytically from the sim trade lists.
  - Kelly-corrected per-chart risk fractions (of account equity):
        crash300 2.0%   crash500 2.0%   boom500 2.0%
        crash1000 1.5%  boom1000 1.0%
    vs OLD flat 2% everywhere (boom1000 was ~full-Kelly => overbet).
  - Dollar frame = the live 5-lot capped book at $1/pt/lot (measured from
    live fills). The cap binds above ~$600-700 equity, so % risk only bites
    once MaxLots is raised.

Outputs: terminal tables + brain/reports/portfolio_mc_<date>.md
"""
import os
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "brain"))
import engine as E  # noqa: E402
from config import SYMBOLS, BROKER_FLOOR  # noqa: E402

try:
    from scipy.special import erf as _erf
    HAVE_SCIPY = True
except Exception:                                   # pragma: no cover
    HAVE_SCIPY = False

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPORT_DIR = os.path.join(ROOT, "brain", "reports")

CORRECTED = {"crash300": 0.020, "crash500": 0.020, "crash1000": 0.015,
             "boom500": 0.020, "boom1000": 0.010}
OLD = {k: 0.020 for k in CORRECTED}

N_MONTHS = 200_000
N_PATH = 40_000
DAYS = 30
LOTS = 5.0
USD_PER_PT_LOT = 1.0
E0 = 1_000.0
MULTS = (1.0, 1.5, 2.0, 2.5)
rng = np.random.default_rng(20260903)

# Live-measured loss-gap multipliers (n=1 each so far, from the EA's
# CALIB lines - real broker fills): boom1000 -55.85 loss went 22.34 pts
# through a 5.23 stop => m = 22.342/9.569 = 2.33; crash1000 -6.79 loss
# closed 2.716 pts adverse (safety exit) vs 5.064 sim avg => m = 0.54.
# Symbols without a live loss yet use the frame default.
MEASURED_M = {"boom1000": 2.33, "crash1000": 0.54}
# Per-trade cost: MEASURED = $0 (docs/LIVE_COST_VERDICT.md) - realized
# PnL == price move x $1/pt/lot on every fill, so the model has no cost
# term to subtract. Zero-cost confirmed, not assumed.


# ---------------------------------------------------------------- panels
def load_panels():
    out = {}
    for s in SYMBOLS:
        key = s["key"]
        df = E.load_prep(key)
        cfg = s["verified"]
        trs = E.run_cfg(key, cfg["hold"], cooldown=cfg["cooldown"],
                        partial=True, gate=cfg.get("gate", False),
                        gate_mult=cfg.get("gate_mult", 8.0),
                        gate_window=cfg.get("gate_window", 3),
                        entry_bar=cfg.get("entry_bar", 1), df=df)
        dt = E.datetimes(key)
        med = df["median_range"].values
        pos = pd.Series(dtype=float)
        neg = pd.Series(dtype=float)
        posR = pd.Series(dtype=float)
        negR = pd.Series(dtype=float)
        for t in trs:
            sl = max(med[t["entry_row"]], BROKER_FLOOR.get(key, 1.0))
            d = pd.Timestamp(dt[t["entry_row"]]).floor("D")
            v, vr = t["pnl"], t["pnl"] / sl
            s = pos if v >= 0 else neg
            s.loc[d] = s.get(d, 0.0) + v
            s2 = posR if v >= 0 else negR
            s2.loc[d] = s2.get(d, 0.0) + vr
        pos, neg = pos.sort_index(), neg.sort_index()
        posR, negR = posR.sort_index(), negR.sort_index()
        pnl = (pos.add(neg, fill_value=0.0)).sort_index()   # daily net
        # align all four components to the union of days
        pos = pos.reindex(pnl.index).fillna(0.0)
        neg = neg.reindex(pnl.index).fillna(0.0)
        posR = posR.reindex(pnl.index).fillna(0.0)
        negR = negR.reindex(pnl.index).fillna(0.0)
        pnls = np.array([t["pnl"] for t in trs])
        out[key] = dict(pos_p=pos.values.astype(float),
                        neg_p=neg.values.astype(float),
                        pos_R=posR.values.astype(float),
                        neg_R=negR.values.astype(float),
                        index=pnl.index, n=len(pnl),
                        daily_net=pnl.values.astype(float),
                        lam=len(trs) / len(pnl),
                        n_tr=len(trs),
                        win_pts=float(pnls[pnls > 0].sum()),
                        loss_pts=float(-pnls[pnls <= 0].sum()))
        print(f"  panel {key}: {out[key]['n']} days, {out[key]['n_tr']} trades,"
              f" {out[key]['lam']:.1f}/day")
    return out


def build_copula(panels, keys):
    aligned = pd.DataFrame({k: pd.Series(panels[k]["daily_net"],
                                         index=panels[k]["index"])
                            for k in keys})
    corr = aligned.corr().fillna(0.0).values
    n = len(keys)
    shrunk = 0.3 * corr + 0.7 * np.eye(n)
    return np.linalg.cholesky(shrunk + 1e-9 * np.eye(n))


# ---------------------------------------------------------------- draws
def day_indices(panels, keys, L, shape):
    n_sym = len(keys)
    z = rng.normal(size=(shape[0], shape[1], n_sym)) @ L.T
    if HAVE_SCIPY:
        u = 0.5 * (1.0 + _erf(z / np.sqrt(2.0)))
    else:                                   # pragma: no cover
        u = 0.5 * (1.0 + np.tanh(z / np.sqrt(2.0) * 0.826))
    u = np.clip(u, 1e-9, 1 - 1e-9)
    idx = {}
    for j, k in enumerate(keys):
        n = panels[k]["n"]
        idx[k] = np.clip(np.floor(u[:, :, j] * n).astype(np.int64), 0, n - 1)
    return idx


def daily_path(panels, idx, keys, frac=None, loss_mult=1.0, value="R"):
    """(paths, days) portfolio daily series. 'R': % of equity/day summed at
    frac. 'pnl': $/day at 5 lots x $1/pt/lot. Loss-side components are
    scaled by loss_mult (win components unchanged). loss_mult may be a
    float (uniform) or a dict keyed by symbol (per-symbol measured m;
    missing symbols use the frame default)."""
    acc = np.zeros(idx[keys[0]].shape, dtype=float)
    for k in keys:
        p = panels[k]
        lm = loss_mult.get(k, loss_mult.get("default", 1.0)) \
            if isinstance(loss_mult, dict) else loss_mult
        if value == "R":
            w, l = p["pos_R"][idx[k]], p["neg_R"][idx[k]]
            acc += frac[k] * (w.astype(float) + lm * l.astype(float))
        else:
            w, l = p["pos_p"][idx[k]], p["neg_p"][idx[k]]
            acc += (w.astype(float) + lm * l.astype(float)) * \
                LOTS * USD_PER_PT_LOT
    return acc


def measured_frame(panels, idx, keys, unknown_m=1.0):
    """Per-symbol m dict: live-measured where we have a loss, else the
    frame default (unknown_m) for symbols still unmeasured."""
    return {k: MEASURED_M.get(k, unknown_m) for k in keys}


def dd_stats(daily_pct):
    eq = np.cumsum(daily_pct, axis=1)
    peak = np.maximum.accumulate(eq, axis=1)
    dd = (peak - eq).max(axis=1)
    return np.percentile(dd, [50, 90, 95, 99]), dd.max()


def row_dist(x, unit="%"):
    return (f"med {np.median(x):+9.2f}{unit}  mean {x.mean():+9.2f}{unit}"
            f"  sd {x.std():+8.2f}{unit}  p1 {np.percentile(x,1):+9.2f}{unit}"
            f"  min {x.min():+9.2f}{unit}  P<0 {100*(x<0).mean():5.1f}%")


# ---------------------------------------------------------------- run
def run():
    print("Loading verified-config daily panels ...")
    keys = [s["key"] for s in SYMBOLS]
    panels = load_panels()
    L = build_copula(panels, keys)
    print()

    # -------- analytic EV breakeven multipliers (per symbol) --------
    print("== Per-symbol EV structure (sim, full history) - breakeven "
          "loss multiplier = win pts / loss pts ==")
    print(f"{'sym':<11}{'trades':>8}{'win-share':>10}{'win pts':>10}{'loss pts':>10}"
          f"{'breakeven m':>12}{'win $/mo':>10}")
    tot_w = tot_l = 0.0
    for s in SYMBOLS:
        k = s["key"]
        p = panels[k]
        mb = p["win_pts"] / p["loss_pts"] if p["loss_pts"] > 0 else float("inf")
        tot_w += p["win_pts"] * (30.0 / p["n"]) * LOTS
        tot_l += p["loss_pts"] * (30.0 / p["n"]) * LOTS
        ws = p["win_pts"] / (p["win_pts"] + p["loss_pts"]) * 100
        print(f"{k:<11}{p['n_tr']:>8}{ws:>9.1f}%"
              f"{p['win_pts']:>10.0f}{p['loss_pts']:>10.0f}{mb:>12.2f}"
              f"{p['win_pts']*(30.0/p['n'])*LOTS:>+10.0f}")
    mb_port = tot_w / tot_l if tot_l > 0 else float("inf")
    print(f"{'PORTFOLIO':<11}{'':>8}{'':>10}{tot_w:>10.0f}{tot_l:>10.0f}"
          f"{mb_port:>12.2f}")
    print("  (win $/mo = win side at 5 lots; a month's net = wins - m x losses)")
    print()

    idx = day_indices(panels, keys, L, (N_MONTHS, DAYS))
    idxp = day_indices(panels, keys, L, (N_PATH, DAYS))

    # -------- capped 5-lot book $ across gap frames --------
    print("== 5-lot capped book ($/month, E0=$1000 anchor) across loss-gap "
          "multipliers m ==")
    print(f"{'frame':>16}{'median $':>10}{'mean $':>10}{'p1 $':>10}{'min $':>10}"
          f"{'P<0':>7}{'DD p50':>8}{'DD p95':>8}{'DD max':>8}")
    for m in MULTS:
        d = daily_path(panels, idx, keys, value="pnl", loss_mult=m).sum(axis=1)
        dp = daily_path(panels, idxp, keys, value="pnl", loss_mult=m)
        dq, dm = dd_stats(dp)
        print(f"{'uniform m='+str(m):>16}{np.median(d):>+10.0f}{d.mean():>+10.0f}"
              f"{np.percentile(d,1):>+10.0f}{d.min():>+10.0f}"
              f"{100*(d<0).mean():>6.1f}%{dq[0]:>8.0f}{dq[2]:>8.0f}{dm:>8.0f}")
    # live-measured per-symbol m (boom1000 2.33, crash1000 0.54), with the
    # unmeasured symbols at the sim tail (m=1.0) and at mild friction (1.5)
    for label, unk in (("MEASURED (unk m=1.0)", 1.0),
                       ("MEASURED (unk m=1.5)", 1.5)):
        fm = measured_frame(panels, idx, keys, unknown_m=unk)
        d = daily_path(panels, idx, keys, value="pnl",
                       loss_mult=fm).sum(axis=1)
        fmp = measured_frame(panels, idxp, keys, unknown_m=unk)
        dp = daily_path(panels, idxp, keys, value="pnl", loss_mult=fmp)
        dq, dm = dd_stats(dp)
        print(f"{label:>16}{np.median(d):>+10.0f}{d.mean():>+10.0f}"
              f"{np.percentile(d,1):>+10.0f}{d.min():>+10.0f}"
              f"{100*(d<0).mean():>6.1f}%{dq[0]:>8.0f}{dq[2]:>8.0f}{dm:>8.0f}")
    print()

    # -------- Kelly correction vs old at realistic gap frames --------
    print("== Uncapped risk-fraction frame - corrected vs OLD 2% "
          "(monthly return %, at m = 1.5 / 2.0) ==")
    print(f"{'frame':<12}{'m':>5}{'median':>10}{'mean':>10}{'sd':>9}{'p1':>10}"
          f"{'min':>10}{'P<0':>7}{'DD p50':>8}{'DD p95':>8}")
    for name, frac in (("OLD", OLD), ("CORRECTED", CORRECTED)):
        for m in (1.5, 2.0):
            r = daily_path(panels, idx, keys, frac=frac,
                           loss_mult=m).sum(axis=1) * 100
            rp = daily_path(panels, idxp, keys, frac=frac, loss_mult=m) * 100
            dq, _ = dd_stats(rp)
            print(f"{name:<12}{m:>5.1f}{np.median(r):>+10.1f}{r.mean():>+10.1f}"
                  f"{r.std():>9.1f}{np.percentile(r,1):>+10.1f}{r.min():>+10.1f}"
                  f"{100*(r<0).mean():>6.1f}%{dq[0]:>8.1f}{dq[2]:>8.1f}")
    print()

    # -------- per-symbol monthly risk at m = 1.5 and 2.0 --------
    print("== Per-symbol monthly $ (5 lots) at m=1.5 and m=2.0 ==")
    print(f"{'sym':<11}{'med$ m1.5':>10}{'P<0':>7}{'min':>9}"
          f"{'med$ m2.0':>11}{'P<0':>7}{'min':>9}")
    for s in SYMBOLS:
        k = s["key"]
        row = [k]
        for m in (1.5, 2.0):
            d = daily_path(panels, {k: idx[k]}, [k],
                           value="pnl", loss_mult=m).sum(axis=1)
            row += [f"{np.median(d):>+10.0f}", f"{100*(d<0).mean():>6.1f}%",
                    f"{d.min():>+9.0f}"]
        print("".join(row))
    print()

    # -------- report file --------
    os.makedirs(REPORT_DIR, exist_ok=True)
    date = datetime.now().strftime("%Y%m%d")
    ln = [f"# Portfolio Monte Carlo - {date}", "",
          f"{DAYS}-day months, {N_MONTHS:,} months for distributions, "
          f"{N_PATH:,} paths for DD. Days bootstrap-drawn per symbol, "
          "Gaussian-copula coupled at the measured (shrunk) correlation "
          "(near independent).",
          "Kelly-corrected risk: crash300 2%, crash500 2%, crash1000 1.5%, "
          "boom500 2%, boom1000 1%. OLD = 2% everywhere. $ frame: 5 lots "
          "x $1/pt/lot (live-measured).",
          "m = uniform multiplier on loss-side daily draws: 1.0 = sim "
          "tails (optimistic), 1.5-2.0 = realistic live gap friction "
          "(live SL hits measured 4-9x SL vs ~2x in the bar sim), 2.5 = "
          "severe.", ""]
    ln += [f"Portfolio EV breakeven loss multiplier: {mb_port:.2f} "
           "(below 1.0 means the book is negative even in the sim).", ""]
    for m in MULTS:
        d = daily_path(panels, idx, keys, value="pnl", loss_mult=m).sum(axis=1)
        dp = daily_path(panels, idxp, keys, value="pnl", loss_mult=m)
        dq, dm = dd_stats(dp)
        ln += [f"## 5-lot book at uniform m={m}", row_dist(d, "$"),
               f"intra-month max DD $ p50/p90/p95: {dq[0]:.0f}/{dq[1]:.0f}/"
               f"{dq[2]:.0f}, worst {dm:.0f}", ""]
    for label, unk in (("MEASURED m, unknowns 1.0", 1.0),
                       ("MEASURED m, unknowns 1.5", 1.5)):
        fm = measured_frame(panels, idx, keys, unknown_m=unk)
        d = daily_path(panels, idx, keys, value="pnl",
                       loss_mult=fm).sum(axis=1)
        fmp = measured_frame(panels, idxp, keys, unknown_m=unk)
        dp = daily_path(panels, idxp, keys, value="pnl", loss_mult=fmp)
        dq, dm = dd_stats(dp)
        ln += [f"## 5-lot book at {label}", row_dist(d, "$"),
               f"intra-month max DD $ p50/p90/p95: {dq[0]:.0f}/{dq[1]:.0f}/"
               f"{dq[2]:.0f}, worst {dm:.0f}",
               "live-measured per-symbol m from real broker fills: "
               "boom1000 2.33 (one -55.85 loss, 22.34 pts through a 5.23 "
               "stop), crash1000 0.54 (one -6.79 safety exit). n=1 each - "
               "re-measure at ~30 closes/symbol.", ""]
    path = os.path.join(REPORT_DIR, f"portfolio_mc_{date}.md")
    with open(path, "w") as fh:
        fh.write("\n".join(ln))
    print(f"wrote {os.path.relpath(path, ROOT)}")


if __name__ == "__main__":
    run()
