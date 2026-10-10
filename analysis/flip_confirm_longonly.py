#!/usr/bin/env python3
"""Long-only asymmetry confirmation on the FULL tick archive (self-arming).

Runs daily via Task Scheduler ("Genesis FlipConfirm Daily"). Behavior:
  - archive < 28 days: print STATUS: WAITING (n/28 days) and exit 0
  - archive >= 28 days: run the full confirmation on flip1000 @5.0 min_age=1:
      1. long-only vs short-only net & WR per complete UTC week (stability)
      2. full-archive baseline vs long-only: trips, WR, net/wk, maxDD
      3. long-only stop window (deepest dip of positive days, worst day total)
      4. day-block survival ladder (none/-$9) + runway curve (4/8/12 wk)
    Verdict CONFIRMED iff: >=70% of complete weeks have long net > short net,
    long maxDD < baseline maxDD, and long-only 4wk ruin <= 1% with -$9 stop.
    Appends a dated block to archive/flip_confirm_longonly.log.

The asymmetry being confirmed (Sep 10, 7.1d sample): longs 52.1% WR vs shorts
41.9%, long maxDD 3x smaller, short-only ruin 48.8%, long-only ruin 0.0%.

Usage: python analysis/flip_confirm_longonly.py
"""
import sys
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_concepts import load, simulate, SYMBOL, WARMUP, LIVE_SPREAD
import flip_longonly_backtest as lb

MIN_DAYS = 28.0
LOG = "archive/flip_confirm_longonly.log"
N_SIMS = 2000


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
    if span < MIN_DAYS:
        line = f"[{now}] STATUS: WAITING ({span:.1f}/{MIN_DAYS:.0f} days of {SYMBOL} ticks)"
        print(line)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return

    med = float(np.median(np.abs(np.diff(px))[:WARMUP]))
    print(f"[{now}] archive {span:.1f}d, {len(px):,} ticks - running confirmation")

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

    confirmed = (share >= 70.0 and el["maxdd"] < eb["maxdd"]
                 and res["stop 9.0"][1] <= 1.0)

    lines = [
        f"[{now}] STATUS: {'CONFIRMED' if confirmed else 'NOT-CONFIRMED'} "
        f"({SYMBOL} @5.0 k=1, {span:.1f}d, {len(px):,} ticks)",
        f"  weekly asymmetry: long>short in {wins}/{len(common)} complete weeks ({share:.0f}%)",
        f"  baseline: {eb['n']} trips WR {eb['wr']:.1f}% net {eb['net_wk']:+.2f}/wk maxDD {eb['maxdd']:.2f} worst {eb['worst']:+.2f}",
        f"  long-only: {el['n']} trips WR {el['wr']:.1f}% net {el['net_wk']:+.2f}/wk maxDD {el['maxdd']:.2f} worst {el['worst']:+.2f}",
        f"  short-only: {es['n']} trips WR {es['wr']:.1f}% net {es['net_wk']:+.2f}/wk maxDD {es['maxdd']:.2f}",
        f"  long-only stop window: deepest positive-day dip {dip:+.2f}, worst day {worst_day:+.2f}",
        f"  survival 4wk: no-stop {res['stop None'][0]:.1f}/{res['stop None'][1]:.1f}  "
        f"-$9 {res['stop 9.0'][0]:.1f}/{res['stop 9.0'][1]:.1f} (flip/ruin %)",
        f"  survival 8wk no-stop: {fl8 / N_SIMS * 100:.1f}/{ru8 / N_SIMS * 100:.1f}",
    ]
    for ln in lines:
        print(ln)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
