#!/usr/bin/env python3
"""INTERIM PREVIEW of the long-only confirmation - NON-BINDING, read-only.

Why this exists: the user asked for an early read before the archive reaches
MIN_DAYS. The pre-registered gate (analysis/flip_confirm_longonly.py, fired
by "Genesis FlipConfirm Daily") remains the ONLY authority to settle the
long-only adoption; this preview deliberately:

  - runs the IDENTICAL machinery (same load/simulate/econ/ladder calls,
    same warmup, same spread convention, same rng seeds)
  - on the CURRENT archive (may be < MIN_DAYS = 28 days)
  - NEVER writes archive/flip_confirm_longonly.log, verdict files, or
    anything the checkpoint chain reads - output goes to stdout and
    archive/flip_confirm_preview_interim.log only
  - prints PREVIEW instead of CONFIRMED/NOT-CONFIRMED so a grepped log
    line can never be mistaken for the settled verdict
  - Part 2 scores the DEPLOYED condition (rolling 5000-tick median +
    Menu-C 300-tick pending delay) via flip_rolling_engine.run_conv,
    because Part 1's frozen-median immediate-entry engine is NOT what
    the live book runs (P17 divergence, Sep 16)

Interpretation rules: a preview PASS does not authorize funding; a preview
FAIL is genuinely informative (bad asymmetry rarely becomes good with more
same-regime data, but the Oct 1-2 gate still decides).

Usage: python analysis/flip_confirm_preview.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_concepts import load, simulate, SYMBOL, WARMUP
import flip_longonly_backtest as lb
from flip_rolling_engine import rolling_medians, run_conv

MIN_DAYS = 28.0          # reported, not enforced - that is the point
LOG = "archive/flip_confirm_preview_interim.log"
N_SIMS = 2000
DEPLOY_DELAY = 300       # executor Menu-C pending delay (DELAY_TICKS)


def archive_span_days(ms):
    return (ms[-1] - ms[WARMUP]) / 86_400_000


def complete_weeks(trades):
    """Bucket trips into ISO weeks; keep only weeks with all 7 days present
    (partial first/last weeks are excluded from the stability count)."""
    weeks = {}
    for pnl, tms in trades:
        d = datetime.fromtimestamp(tms / 1000, tz=timezone.utc)
        key = d.strftime("%G-W%V")
        weeks.setdefault(key, []).append((pnl, d))
    out = {}
    for key, items in weeks.items():
        days = {d.date() for _, d in items}
        # a complete week contains trades spanning >= 6 distinct UTC days
        if len(days) >= 6:
            arr = np.array([p for p, _ in items])
            out[key] = dict(net=float(arr.sum()), wr=float((arr > 0).mean() * 100),
                            n=len(arr))
    return out


def main():
    ms, px = load(SYMBOL)
    span = archive_span_days(ms)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    print("=" * 78)
    print("=== INTERIM PREVIEW - NON-BINDING - NOT THE PRE-REGISTERED GATE ===")
    print(f"=== official gate remains analysis/flip_confirm_longonly.py ===")
    print("=" * 78)
    print(f"[{now}] archive {span:.1f}d / {MIN_DAYS:.0f}d required "
          f"({len(px):,} ticks) - running IDENTICAL confirmation machinery")

    base_tr, _ = simulate(ms, px, med)
    long_tr, _ = simulate(ms, px, med, side=1)
    short_tr, _ = simulate(ms, px, med, side=-1)

    # 1. weekly stability of the asymmetry
    lw, sw = complete_weeks(long_tr), complete_weeks(short_tr)
    common = sorted(set(lw) & set(sw))
    wins = sum(1 for w in common if lw[w]["net"] > sw[w]["net"])
    share = wins / len(common) * 100 if common else 0.0

    # 2. full-archive economics
    def econ(tr):
        arr = np.array([t[0] for t in tr])
        cum = np.cumsum(arr)
        return dict(n=len(arr), wr=float((arr > 0).mean() * 100),
                    net_wk=float(arr.sum() / (span / 7)),
                    maxdd=float(np.max(np.maximum.accumulate(cum) - cum)),
                    worst=float(arr.min()))
    eb, el, es = econ(base_tr), econ(long_tr), econ(short_tr)

    # 3. long-only stop window on the full archive
    days = lb.day_blocks(long_tr)
    dip, worst_day = lb.stop_window(days)

    # 4. survival ladder + runway (long-only)
    res = {}
    for thresh in (None, 9.0):
        fl = ru = 0
        rng = np.random.default_rng(7)
        for _ in range(N_SIMS):
            eq, r, f, _, _ = lb.sim(days, thresh, rng)
            fl += f
            ru += r
        res[f"stop {thresh}"] = (fl / N_SIMS * 100, ru / N_SIMS * 100)
    lb.HORIZON_DAYS = 56
    fl8 = ru8 = 0
    rng = np.random.default_rng(7)
    for _ in range(N_SIMS):
        eq, r, f, _, _ = lb.sim(days, None, rng)
        fl8 += f
        ru8 += r
    lb.HORIZON_DAYS = 28

    # gate booleans REPORTED, never acted on
    would = (share >= 70.0 and el["maxdd"] < eb["maxdd"]
             and res["stop 9.0"][1] <= 1.0)

    lines = [
        f"[{now}] PREVIEW ({SYMBOL} @5.0 k=1, {span:.1f}d of {MIN_DAYS:.0f}d, "
        f"{len(px):,} ticks) - NON-BINDING",
        f"  weekly asymmetry: long>short in {wins}/{len(common)} complete weeks ({share:.0f}%)",
        f"  baseline: {eb['n']} trips WR {eb['wr']:.1f}% net {eb['net_wk']:+.2f}/wk maxDD {eb['maxdd']:.2f} worst {eb['worst']:+.2f}",
        f"  long-only: {el['n']} trips WR {el['wr']:.1f}% net {el['net_wk']:+.2f}/wk maxDD {el['maxdd']:.2f} worst {el['worst']:+.2f}",
        f"  short-only: {es['n']} trips WR {es['wr']:.1f}% net {es['net_wk']:+.2f}/wk maxDD {es['maxdd']:.2f}",
        f"  long-only stop window: deepest positive-day dip {dip:+.2f}, worst day {worst_day:+.2f}",
        f"  survival 4wk: no-stop {res['stop None'][0]:.1f}/{res['stop None'][1]:.1f}  "
        f"-$9 {res['stop 9.0'][0]:.1f}/{res['stop 9.0'][1]:.1f} (flip/ruin %)",
        f"  survival 8wk no-stop: {fl8 / N_SIMS * 100:.1f}/{ru8 / N_SIMS * 100:.1f}",
        f"  gate criteria on this partial archive: weekly>70% {share >= 70.0}, "
        f"longDD<baseDD {el['maxdd'] < eb['maxdd']}, ruin<=1% {res['stop 9.0'][1] <= 1.0} "
        f"-> would-score {'PASS' if would else 'FAIL'}",
        f"  REFERENCE (Sep 10 pre-registration, 7.1d): long WR 52.1 vs short 41.9, "
        f"long maxDD 3x smaller, short ruin 48.8%",
        "  A PREVIEW PASS DOES NOT AUTHORIZE FUNDING - the 28d gate decides.",
    ]

    # ---- Part 2: DEPLOYED-CONDITION read (rolling median + delay=300) ----
    print("\ncomputing rolling-median series (executor semantics)...",
          flush=True)
    med_roll = rolling_medians(ms, px)
    frozen_med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    p2 = [f"  [deployed-condition: rolling median + pending delay={DEPLOY_DELAY}]"]
    tr2 = {}
    for label, sd in (("all", 0), ("long-only", 1), ("short-only", -1)):
        nets, cms = run_conv(ms, px, med_roll, frozen_med, side=sd,
                             mode="counter", mult=5.0, delay=DEPLOY_DELAY)
        tr = list(zip(nets.tolist(), cms.tolist()))
        tr2[label] = tr
        arr = np.array([p for p, _ in tr]) if tr else np.array([0.0])
        cum = np.cumsum(arr)
        p2.append(f"  {label:10s}: {len(tr)} trips WR {(arr > 0).mean() * 100:.1f}% "
                  f"net {arr.sum() / (span / 7):+.2f}/wk "
                  f"maxDD {np.max(np.maximum.accumulate(cum) - cum):.2f}")
    lw2, sw2 = complete_weeks(tr2["long-only"]), complete_weeks(tr2["short-only"])
    common2 = sorted(set(lw2) & set(sw2))
    wins2 = sum(1 for w in common2 if lw2[w]["net"] > sw2[w]["net"])
    share2 = wins2 / len(common2) * 100 if common2 else 0.0
    p2.append(f"  weekly asymmetry (deployed): long>short in {wins2}/"
              f"{len(common2)} complete weeks ({share2:.0f}%)")
    days2 = lb.day_blocks(tr2["all"])
    res2 = {}
    for thresh in (None, 9.0):
        fl = ru = 0
        rng = np.random.default_rng(7)
        for _ in range(N_SIMS):
            eq, r, f, _, _ = lb.sim(days2, thresh, rng)
            fl += f
            ru += r
        res2[f"stop {thresh}"] = (fl / N_SIMS * 100, ru / N_SIMS * 100)
    p2.append(f"  survival 4wk deployed: no-stop {res2['stop None'][0]:.1f}/"
              f"{res2['stop None'][1]:.1f}  -$9 {res2['stop 9.0'][0]:.1f}/"
              f"{res2['stop 9.0'][1]:.1f} (flip/ruin %)")
    lines += p2

    for ln in lines:
        print(ln)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n\n")


if __name__ == "__main__":
    main()
