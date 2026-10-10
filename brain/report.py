#!/usr/bin/env python3
"""Render learner + auditor + recommender outputs into a markdown report."""

import os
import datetime

from config import by_key, REPORT_DIR


def _pf(x):
    if not x:
        return "-"
    return f"{x['pf']:.2f}"


def _num(v, nd=2):
    try:
        if v is None or (isinstance(v, float) and (v != v)):
            return "-"
        return f"{v:.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


def _hours(hours):
    if not hours:
        return "none"
    def fmt(h):
        return f"{h:02d}:00"
    # compact ranges
    out, start = [], None
    prev = None
    for h in sorted(hours) + [None]:
        if h is not None and (prev is None or h == prev + 1):
            if start is None:
                start = h
        else:
            if start is not None:
                out.append(fmt(start) + (f"-{fmt(prev)}" if prev != start else ""))
            start = h if h is not None else None
        prev = h
    return ", ".join(out)


def fmt_audit_flags(aud):
    fl = aud.get("flags") or []
    if not fl:
        return "none"
    return "; ".join(fl)


def render(learner, audits, recs, title_dt=None):
    title_dt = title_dt or datetime.datetime.now()
    L = []
    L.append(f"# 🧠 GENESIS AI LAYER — DAILY BRAIN REPORT")
    L.append(f"**Generated:** {title_dt.strftime('%Y-%m-%d %H:%M UTC')}")
    L.append("")
    L.append("## Summary")
    L.append("")
    L.append("| Symbol | Regime | Spike rate | Drift | P(loss\\|loss) | Verdict |")
    L.append("|---|---|---|---|---|---|")
    for key, reg in learner.items():
        rec = recs.get(key, {})
        ver = rec.get("verdict", "?")
        cl = reg.get("cluster_ratio", float("nan"))
        L.append(f"| {key} | {_regime_label(reg)} | "
                 f"{_num(reg.get('rate_x_baseline'), 2)}x | "
                 f"{_num((reg.get('drift_pct') or 0) * 100, 0)}% | "
                 f"{_num(cl, 2)} | {ver} |")
    L.append("")
    for key in learner:
        aud = audits.get(key, {})
        rec = recs.get(key, {})
        reg = learner[key]
        s = by_key(key)
        L.append(f"## {s['ea']} (`{key}`)")
        L.append("")
        L.append("### Regime (learner, ~7d vs baseline)")
        L.append("")
        L.append(f"- Spike rate: {_num(reg.get('spike_rate_h'), 2)}/h = "
                 f"**{_num(reg.get('rate_x_baseline'), 2)}x** full-history rate"
                 + (" 🔥" if (reg.get('rate_x_baseline') or 0) > 1.3 else
                    (" ❄️" if (reg.get('rate_x_baseline') or 0) < 0.75 else "")))
        L.append(f"- Mean gap to next adverse spike: {_num(reg.get('mean_gap_min'), 1)} min | "
                 f"P(gap > {s['verified']['hold']}m hold) = {_num(reg.get('p_gap_gt_hold') * 100, 0)}%")
        L.append(f"- Drift-direction bars: {_num((reg.get('drift_pct') or 0) * 100, 0)}% | "
                 f"vol percentile: {_num((reg.get('vol_percentile') or 0) * 100, 0)}")
        L.append(f"- Loss clustering P(loss\\|loss): {_num(reg.get('cluster_ratio'), 2)}"
                 + (" ⚠️ elevated" if (reg.get('cluster_ratio') or 0) >= 1.3 else ""))
        L.append(f"- Cold hours (below-median spike rate): {_hours(reg.get('cold_hours'))} | "
                 f"Hot hours: {_hours(reg.get('hot_hours'))}")
        lot = reg.get("lottery") or {}
        lv = reg.get("lottery_verdict") or "NO DATA"
        if lv.startswith("LOTTERY OPEN"):
            L.append(f"- 🎰 **{lv}**")
        elif lv.startswith("WATCH"):
            L.append(f"- 🎰 {lv}")
        elif lot:
            parts = []
            for K in sorted(lot.keys()):
                st = lot[K]
                parts.append(f"K={K}: c={_num(st['c'], 3)} (be {_num(st['breakeven_c'], 3)}, "
                             f"EV {_num(st['ev'], 3)}R, n={st.get('resolved')})")
            L.append(f"- 🎰 Lottery-c (chase race, recent): {' | '.join(parts)} — {lv}")
        stab = reg.get("hour_stability") or {}
        if stab.get("verdict"):
            L.append(f"- **Cold-hour stability: {stab.get('verdict')}** — {stab.get('guidance', '')}")
            L.append(f"  (week-over-week rank corr {_num(stab.get('adj_week_rank_corr'), 2)} | "
                     f"cold-set overlap {_num(stab.get('adj_week_cold_overlap'), 2)} | "
                     f"hour explains {_num((stab.get('hour_explained_var') or 0) * 100, 0)}% of variance | "
                     f"run-to-run churn {_num((stab.get('run_to_run_churn') or 0) * 100, 0)}% of the day)")
            if stab.get("note"):
                L.append(f"  ({stab['note']})")
            if stab.get("caveat"):
                L.append(f"  ⚠️ {stab['caveat']}")
        L.append("")
        L.append("### Live audit (EA trade CSV)")
        L.append("")
        if not aud.get("available"):
            L.append("No live trades file yet.")
        else:
            L.append(f"- {aud.get('n', 0)} closes | WR {_num(aud.get('wr'), 0)}% | "
                     f"PnL ${_num(aud.get('pnl'), 2)} | avg win {_num(aud.get('avg_win'), 2)} / "
                     f"avg loss {_num(aud.get('avg_loss'), 2)}")
            if aud.get("usd_per_pt_lot"):
                L.append(f"- **$/pt/lot (measured): {_num(aud['usd_per_pt_lot'], 3)}** "
                         f"(10-90 pct: {_num(aud['usd_per_pt_lot_range'][0], 3)}-"
                         f"{_num(aud['usd_per_pt_lot_range'][1], 3)}, n={aud.get('calib_rows')})")
            L.append(f"- Flags: {fmt_audit_flags(aud)}")
        L.append("")
        L.append("### Recommendation (corrected-engine validated)")
        L.append("")
        if rec.get("verdict") == "ERROR":
            L.append(f"❌ {rec.get('reason')}")
        elif rec.get("verdict") == "HOLD":
            L.append(f"✅ **HOLD verified baseline** — {rec.get('reason')}")
            L.append(f"- Baseline train: PF {_pf(rec.get('baseline_train'))} | "
                     f"val: PF {_pf(rec.get('baseline_val'))}")
        else:
            cand = rec["candidate"]
            L.append(f"🔄 **CHANGE to** hold={cand['hold']} cooldown={cand['cooldown']} "
                     f"gate={'ON' if cand.get('gate') else 'OFF'} — {rec.get('reason')}")
            L.append(f"- Baseline train PF {_pf(rec.get('baseline_train'))} → "
                     f"candidate train PF {_pf(rec.get('candidate_train'))}")
            L.append(f"- Baseline **val** PF {_pf(rec.get('baseline_val'))} → "
                     f"candidate **val** PF {_pf(rec.get('candidate_val'))} ✅")
        L.append("")
    L.append("---")
    L.append("*Rule of thumb: never apply a recommendation without re-running "
             "`python brain/run_daily.py` — and when in doubt, re-run "
             "`eval/partial_m1_corrected.py` before believing any new claim.*")
    os.makedirs(REPORT_DIR, exist_ok=True)
    path = os.path.join(REPORT_DIR, f"{title_dt.strftime('%Y-%m-%d')}_ai_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    return path


def _regime_label(reg):
    rx = reg.get("rate_x_baseline", 1.0) or 1.0
    cl = reg.get("cluster_ratio", 1.0) or 1.0
    parts = []
    parts.append("HOT" if rx > 1.3 else ("COLD" if rx < 0.75 else "NORMAL"))
    if cl >= 1.3:
        parts.append("CLUSTER")
    if (reg.get("drift_pct") or 50) >= 65:
        parts.append("DRIFT")
    return "+".join(parts)
