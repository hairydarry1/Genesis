#!/usr/bin/env python3
"""
BRAIN STABILITY — are the learner's cold hours real, or noise that drifts?

The learner's cold-hour list is a 24-slot rate profile over a rolling
window. Three independent questions:

  1. PATTERN: does hour-of-day explain any of the spike-rate variance at
     all?  (eta-squared of the hour factor over weekly windows)
  2. CONSISTENCY: is the hourly shape the same across time?
     (adjacent-week Spearman rank correlation + cold-set Jaccard)
  3. CHURN: would consecutive DAILY runs (the way run_daily.py actually
     slides its 7-day window by one day) keep recommending the same set?
     (mean symmetric difference between adjacent runs' cold sets)

Verdict:
  STABLE  -> hour filter is usable; cold hours are trustworthy
  WEAK    -> pattern exists but drifts; keep cold lists wide, re-check
  NOISE   -> do NOT gate trading on fixed hours (display only)
"""
import numpy as np

from engine import load_prep, datetimes, spike_rows


def _spearman(a, b):
    import pandas as pd
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 4:
        return float("nan")
    ra = pd.Series(a[m]).rank().values
    rb = pd.Series(b[m]).rank().values
    ra = ra - ra.mean()
    rb = rb - rb.mean()
    den = float(np.sqrt((ra ** 2).sum() * (rb ** 2).sum()))
    return float((ra * rb).sum() / den) if den > 0 else float("nan")


def _cold_set(vec, frac=0.85):
    med = np.nanmedian(vec)
    if not np.isfinite(med):
        return set()
    return {int(h) for h, r in enumerate(vec) if np.isfinite(r) and r < med * frac}


def _rate_vec(dts, hours, advb, t0, t1):
    m = (dts >= t0) & (dts < t1)
    if int(m.sum()) == 0:
        return None
    h = hours[m]
    cnt = np.bincount(h, minlength=24).astype(float)
    spk = np.bincount(h, weights=advb[m].astype(float), minlength=24)
    return np.where(cnt > 0, spk / np.maximum(cnt, 1) * 1000.0, np.nan)


def _jaccard(a, b):
    u = a | b
    if not u:
        return 1.0 if a == b else 0.0
    return len(a & b) / len(u)


def cold_hour_stability(key, window_days=7):
    df = load_prep(key)
    dt = datetimes(key)
    adv = spike_rows(df, key)
    if len(df) < window_days * 1440 * 2:
        return {"verdict": "INSUFFICIENT DATA", "note": f"need ~{2 * window_days} days of bars"}

    # numpy-native arrays (dt is a pandas Series in engine.datetimes())
    dts = np.asarray(dt.values)                  # datetime64[ns]
    hours = np.asarray(dt.dt.hour.values)        # int hour per bar
    advb = np.asarray(adv, dtype=bool)
    end = np.datetime64(dt.max())
    start = np.datetime64(dt.min())
    total_days = int((end - start) / np.timedelta64(1, "D"))

    # --- 1/2: non-overlapping weekly windows (independent samples) ---
    n_weeks = int(total_days // window_days)
    weeks = []
    for w in range(n_weeks):
        t1 = end - np.timedelta64(w * window_days, "D")
        t0 = t1 - np.timedelta64(window_days, "D")
        v = _rate_vec(dts, hours, advb, t0, t1)
        if v is not None and np.isfinite(v).sum() >= 20:
            weeks.append(v)
    weeks = weeks[::-1]  # chronological

    adj_r, jac = float("nan"), float("nan")
    pool_r = float("nan")
    if len(weeks) >= 2:
        rs = [_spearman(weeks[i], weeks[i + 1]) for i in range(len(weeks) - 1)]
        rs = [r for r in rs if np.isfinite(r)]
        js = [_jaccard(_cold_set(weeks[i]), _cold_set(weeks[i + 1]))
              for i in range(len(weeks) - 1)]
        adj_r = float(np.mean(rs)) if rs else float("nan")
        jac = float(np.mean(js)) if js else float("nan")

        # share of (window x hour) variance explained by hour-of-day
        mat = np.vstack([w for w in weeks])
        finite = np.isfinite(mat)
        if finite.sum() > 0:
            grand = float(np.nanmean(mat))
            colm = np.nanmean(mat, axis=0)
            ss_hour = float(len(weeks) * np.nansum((colm - grand) ** 2))
            sst = float(np.nansum((mat - grand) ** 2))
            pool_r = (ss_hour / sst) if sst > 0 else float("nan")

    # --- 3: rolling daily-step windows (what run_daily does) ---
    churn = float("nan")
    n_runs = 0
    if total_days > window_days + 1:
        prev = None
        diffs = []
        for d in range(window_days, int(total_days)):
            t1 = end - np.timedelta64(d, "D")
            t0 = t1 - np.timedelta64(window_days, "D")
            v = _rate_vec(dts, hours, advb, t0, t1)
            if v is None or np.isfinite(v).sum() < 20:
                continue
            cur = _cold_set(v)
            if prev is not None:
                diffs.append(len(cur ^ prev) / 24.0)
            prev = cur
            n_runs += 1
        if diffs:
            churn = float(np.mean(diffs))

    # --- verdict ---
    caveat = (f"{int(total_days)} days = only {len(weeks)} independent weekly windows; "
              "Poisson noise per hour-slot is large, so a weak-but-real hour effect "
              "could sit below the detection threshold at this sample size.")
    if len(weeks) < 2:
        verdict = "INSUFFICIENT DATA"
        guidance = "need more history to judge hour stability"
    else:
        if adj_r >= 0.5 and jac >= 0.35:
            verdict = "STABLE"
            guidance = "hour filter is usable — cold hours are trustworthy"
        elif adj_r >= 0.25 or jac >= 0.20:
            verdict = "WEAK"
            guidance = ("pattern exists but drifts — keep cold-hour lists wide and "
                        "re-check each run")
        else:
            verdict = "NOISE"
            guidance = ("no hour pattern stable enough to trust in the available "
                        "history — do NOT gate live trading on fixed hours; treat "
                        "cold-hour lists as display only until more data confirms")

    return {
        "key": key,
        "verdict": verdict,
        "guidance": guidance,
        "n_weekly_windows": len(weeks),
        "adj_week_rank_corr": adj_r,
        "adj_week_cold_overlap": jac,
        "hour_explained_var": pool_r,
        "run_to_run_churn": churn,
        "caveat": caveat,
        "note": (f"cold sets = hours below median x 0.85 (matching the learner); "
                 f"rank corr is between adjacent non-overlapping windows; churn is "
                 f"between consecutive DAILY runs of a rolling {window_days}-day window"),
    }


def stability_all(window_days=7):
    from config import SYMBOLS
    return {s["key"]: cold_hour_stability(s["key"], window_days=window_days)
            for s in SYMBOLS}


if __name__ == "__main__":
    import json
    for k, v in stability_all().items():
        print(k, json.dumps({kk: (round(vv, 3) if isinstance(vv, float) else vv)
                             for kk, vv in v.items()}, default=str))
