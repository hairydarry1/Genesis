#!/usr/bin/env python3
"""
BRAIN LEARNER — measures the regimes the EA's knobs respond to.

Spikes are memoryless (nothing predicts the NEXT one) but the market is
NOT static: spike RATE, drift state, volatility and loss clustering are
all time-varying. The learner quantifies them over a recent window vs the
full-history baseline so the recommender can say "this symbol is currently
in a hot/calm/updrift/cluster regime" instead of assuming the static
verified defaults always fit.

Features per symbol (default: last `days` of candles):
  rate_x_baseline      adverse spikes/hour vs full history
  mean_gap_min         mean minutes between adverse spikes (recent)
  p_gap_gt_hold        P(next adverse spike > EA hold minutes)  [exposure]
  drift_pct            % of recent bars closing in the drift direction
  vol_percentile       recent median range vs full-history distribution
  cluster_ratio        P(loss | prev loss) from the verified config's
                       trades on the recent window (engine-measured)
  cold_hours           hours of day with below-median adverse spike rate
  hot_hours            hours of day with above-median adverse spike rate
"""
import numpy as np
import pandas as pd

from config import by_key
from engine import load_prep, datetimes, spike_rows, run_cfg, outcome_stats
from stability import cold_hour_stability
from c_lottery import lottery_state, verdict as lottery_verdict


def _adv_mask(df, key):
    return spike_rows(df, key)


def _hourly_profile(df, dt, adv):
    if len(df) == 0:
        return {}
    h = dt.dt.hour.values
    hours = np.arange(24)
    counts = np.array([int(adv[h == x].sum()) for x in hours])
    bars = np.array([int((h == x).sum()) for x in hours])
    rate = np.where(bars > 0, counts / np.maximum(bars, 1) * 1000.0, np.nan)
    return {int(x): float(rate[x]) for x in hours if np.isfinite(rate[x])}


def learn_symbol(key, days=7, verified_cfg=None):
    df_all = load_prep(key)
    dt_all = datetimes(key)
    adv_all = _adv_mask(df_all, key)

    end = dt_all.max()
    start = end - pd.Timedelta(days=days)

    # recent window rows
    recent = (dt_all >= start) & (dt_all < end)
    if recent.sum() < 1000:
        # not enough recent data — fall back to the last third
        cut = int(len(df_all) * 0.7)
        recent = np.zeros(len(df_all), dtype=bool)
        recent[cut:] = True

    df_r = df_all[recent].reset_index(drop=True)
    dt_r = dt_all[recent]
    adv_r = adv_all[recent]

    total_min = len(df_all)
    n_spk_all = int(adv_all.sum())
    n_spk_r = int(adv_r.sum())
    rate_all = n_spk_all / (total_min / 60.0)
    rate_r = n_spk_r / (len(df_r) / 60.0) if len(df_r) else 0.0

    # gap analysis (recent): minutes between consecutive adverse spikes
    idx = np.where(adv_r)[0]
    if len(idx) >= 2:
        gaps_min = np.diff(idx)   # in bars = minutes
        mean_gap = float(gaps_min.mean())
        p_gt = float((gaps_min > by_key(key)["verified"]["hold"]).mean())
    else:
        mean_gap = float("nan")
        p_gt = float("nan")

    # drift: fraction of recent bars moving in the drift direction
    if len(df_r) > 0:
        closes = df_r["close"].values
        opens = df_r["open"].values
        if "crash" in key:      # drift direction for crash = up
            drift_pct = float((closes > opens).mean())
        else:                   # boom drift direction = down
            drift_pct = float((closes < opens).mean())
        med_now = float(np.nanmedian(df_r["range"].values)) if len(df_r) else float("nan")
        med_all = np.nanmedian(df_all["range"].values)
        vol_pct = float(np.nanmean((df_all["range"].values <= med_now))) if med_all else float("nan")
    else:
        drift_pct, med_now, vol_pct = float("nan"), float("nan"), float("nan")

    # hourly profile over the recent window (or full history if short)
    if len(dt_r) >= 3000:
        prof = _hourly_profile(df_r, dt_r, adv_r)
        hours = np.array([v for v in prof.values() if np.isfinite(v)])
        if len(hours):
            med_h = float(np.median(hours))
            cold = [int(x) for x, v in prof.items() if np.isfinite(v) and v < med_h * 0.85]
            hot = [int(x) for x, v in prof.items() if np.isfinite(v) and v > med_h * 1.15]
        else:
            cold, hot = [], []
    else:
        prof = _hourly_profile(df_all, dt_all, adv_all)
        cold, hot = [], []

    # loss clustering from the verified config on the recent window
    cluster_ratio = float("nan")
    cfg = verified_cfg or by_key(key)["verified"]
    trades = run_cfg(key, cfg["hold"], cooldown=cfg.get("cooldown", 0),
                     gate=cfg.get("gate", False),
                     gate_mult=cfg.get("gate_mult", 8.0),
                     gate_window=cfg.get("gate_window", 3),
                     entry_bar=cfg.get("entry_bar", 1),
                     df=df_r)
    os_ = outcome_stats(trades)
    if os_:
        cluster_ratio = float(os_["p_loss_given_loss"]) if np.isfinite(os_.get("p_loss_given_loss", np.nan)) else float("nan")

    # lottery-c monitor: chase-side race c over the recent third of history
    try:
        lottery = lottery_state(key)
    except Exception:
        lottery = None
    return {
        "key": key,
        "window_bars": int(len(df_r)),
        "spike_rate_h": rate_r,
        "rate_x_baseline": (rate_r / rate_all) if rate_all > 0 else float("nan"),
        "mean_gap_min": mean_gap,
        "p_gap_gt_hold": p_gt,
        "drift_pct": drift_pct,
        "vol_percentile": vol_pct,
        "cluster_ratio": cluster_ratio,
        "cold_hours": cold,
        "hot_hours": hot,
        "hourly_rate": prof,
        "hour_stability": cold_hour_stability(key),
        "lottery": lottery,
        "lottery_verdict": lottery_verdict(key, lottery) if lottery else "NO DATA",
    }


def learn_all(days=7):
    from config import SYMBOLS
    return {s["key"]: learn_symbol(s["key"], days=days) for s in SYMBOLS}


if __name__ == "__main__":
    import json
    res = learn_all(days=7)
    for k, v in res.items():
        print(json.dumps({k2: v2 for k2, v2 in v.items()
                          if k2 != "hourly_rate"}, default=str, indent=1))
