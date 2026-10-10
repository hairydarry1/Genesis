#!/usr/bin/env python3
"""AJ early checkpoint (Sep 18): score book v2 (config C, reseeded Sep 17
19:38Z) against the config-C backtest engine on the EXACT same window.

Book v2 is the first book that runs the deployment config (C: fence5
counter + delay300) with the LI-1/AI fixes (state persistence, broker-truth
booking, uptime instrumentation). The gate (Oct 15) scores three things;
this script is that scoring, run early at n~15:

  S0. v2 LIVE book from trades.csv: per-trip PnL from fill prices,
      auto-EXIT vs manual-close split (user discretion is real PnL but is
      not the executor's exits), day table, win/loss shape.
  S1. ENGINE book: the validated C-clone (flip_oos_c.run_c, d300, rolling)
      over the same window, plus full-archive C baseline ($/trip).
  S2. BRIDGE: pair live vs engine by entry time (<=240s, same side);
      matched deltas split auto/manual; engine-only = live missed;
      live-only = executor discretion/adopt trips.
  S3. SCORECARD: live $/trip vs engine-window $/trip vs full-archive C
      $/trip (the AF expectation). x-class + regime context of the window.
  S4. GATE STATS: uptime (hb_current), pending fate mix vs engine fills.
  S5. What n~15 can and cannot prove (no config change from this).

Usage: python analysis/flip_v2_score.py
"""
import sys
import csv
import os
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP                      # noqa: E402
from flip_rolling_engine import (VPU, STEP_LOTS, SPREAD_USD,      # noqa: E402
                                 run_conv)
from flip_oos_c import run_c                                      # noqa: E402

SYMBOL = "flip1000"
BOOK = "data/flip_exec/trades.csv"
LEDGER = "data/flip_exec/slippage_ledger.csv"
CACHE = os.path.join("data", "flip_exec", "_rolling_med_flip1000.npz")
RESEED_MS = None            # set from first v2 OPEN at runtime
PAIR_TOL = 240.0


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def live_book():
    """Pair v2 trades.csv rows OPEN->CLOSE; PnL from real fill prices."""
    with open(BOOK) as f:
        rows = [r for r in csv.DictReader(f)]
    trips, i = [], 0
    while i < len(rows) - 1:
        e, x = rows[i], rows[i + 1]
        if e["event"] == "OPEN" and x["event"] == "CLOSE":
            d = int(e["side"])
            pnl = d * (float(x["price"]) - float(e["price"])) * VPU * STEP_LOTS
            manual = "manual" in x["note"]
            trips.append({
                "side": d,
                "entry_ts": ts(e["ts_utc"]),
                "exit_ts": ts(x["ts_utc"]),
                "entry_px": float(e["price"]),
                "exit_px": float(x["price"]),
                "pnl": pnl,
                "kind": "MANEXIT" if manual else "EXIT",
                "age": e["note"].split("age ")[-1] if "age" in e["note"] else "?",
            })
            i += 2
        else:
            i += 1
    return trips


def ledger_entry_x():
    """entry_x telemetry keyed by entry timestamp string."""
    with open(LEDGER) as f:
        return {r["spike_ts_utc"]: float(r["entry_x"])
                for r in csv.DictReader(f) if r.get("entry_x")}


def shape(trips, label):
    if not trips:
        print(f"  {label}: n=0")
        return
    p = np.array([t["pnl"] for t in trips])
    w, l = p[p > 0], p[p <= 0]
    print(f"  {label:30s} n={len(p):3d}  net=${p.sum():+7.2f}  "
          f"$/trip=${p.mean():+5.2f}  WR={(p > 0).mean():4.0%}  "
          f"avgW=${w.mean() if len(w) else 0:+5.2f}  "
          f"avgL=${l.mean() if len(l) else 0:+5.2f}  "
          f"worst=${p.min():+5.2f}")


def bridge(eng_trips, live, tol):
    used, matched, eng_only = set(), [], []
    for et in eng_trips:
        best, best_dt = None, tol
        for k, lt in enumerate(live):
            if k in used or lt["side"] != et["side"]:
                continue
            dt = abs(lt["entry_ts"] - et["entry_ts"])
            if dt <= best_dt:
                best, best_dt = k, dt
        if best is None:
            eng_only.append(et)
        else:
            used.add(best)
            matched.append((et, live[best]))
    live_only = [t for k, t in enumerate(live) if k not in used]
    return matched, eng_only, live_only


def main():
    live = live_book()
    if not live:
        print("no v2 trips found")
        return
    t0 = min(t["entry_ts"] for t in live)
    t1 = max(t["exit_ts"] for t in live)
    xmap = ledger_entry_x()

    print("=== S0. v2 LIVE book (config C, reseeded "
          f"{datetime.fromtimestamp(t0, tz=timezone.utc):%m-%d %H:%M}Z) ===")
    shape(live, "live (all trips)")
    shape([t for t in live if t["kind"] == "EXIT"], "live (auto exits)")
    shape([t for t in live if t["kind"] == "MANEXIT"], "live (manual closes)")
    book_chk = 10.0 + sum(t["pnl"] for t in live)
    last_eq = None
    with open(BOOK) as f:
        for r in csv.DictReader(f):
            if "eq " in r["note"]:
                last_eq = float(r["note"].split("eq ")[-1])
    print(f"  book check: 10.00 + sum(pnl) = ${book_chk:.2f} vs last CLOSE "
          f"eq note ${last_eq:.2f}  -> "
          f"{'CLOSES' if abs(book_chk - last_eq) < 0.005 else 'MISMATCH'}")
    from collections import defaultdict as dd
    days = dd(lambda: [0, 0.0])
    for t in live:
        d = datetime.fromtimestamp(t["exit_ts"], tz=timezone.utc).date()
        days[d][0] += 1
        days[d][1] += t["pnl"]
    print("  day table (by close date):")
    for d in sorted(days):
        n, p = days[d]
        print(f"    {d}  n={n:3d}  net ${p:+6.2f}")
    print()

    print("building engine inputs (rolling medians on archive)...")
    ms, px = load(SYMBOL)
    med = None
    if os.path.exists(CACHE):
        z = np.load(CACHE)
        if int(z["n"]) == len(px):
            med = z["med"]
            print(f"  (cache hit, n={len(px):,})")
    if med is None:
        med = rolling_medians_safe(ms, px)
        print(f"  computed & cached (n={len(px):,})")
    print(f"  archive end: {datetime.fromtimestamp(ms[-1]/1000, tz=timezone.utc):%m-%d %H:%M}Z"
          f"  (live last close {datetime.fromtimestamp(t1, tz=timezone.utc):%m-%d %H:%M}Z)")

    # ---- S1. engine books ----
    print("\n=== S1. ENGINE books (validated C-clone, d300, rolling LS) ===")
    rn, rc = run_conv(ms, px, med, None, side=0, mode="counter", mult=5.0, delay=300)
    cn, cc, cst = run_c(ms, px, med, side=0, mult=5.0, delay=300)
    ok = (len(rn) == len(cn) and np.allclose(rn, cn, atol=1e-12)
          and np.array_equal(rc, cc))
    base = cn.sum() / len(cn)
    print(f"  clone validation vs run_conv: {'BIT-EXACT' if ok else 'MISMATCH'}"
          f"  (full archive n={len(cn)}, net ${cn.sum():+.2f}, "
          f"${base:+.3f}/trip = the AF expectation)")
    if not ok:
        print("ABORT: engine clone failed validation")
        return

    eng = []
    for pnl, cms, st in zip(cn.tolist(), cc.tolist(), cst):
        e_s = st[2] / 1000.0
        if t0 - 60 <= e_s <= t1:
            eng.append({"side": st[0], "entry_ts": e_s, "exit_ts": cms / 1000.0,
                        "pnl": pnl, "x": st[1], "kind": "EXIT",
                        "age": "?"})
    forced = [t for t in eng if t["exit_ts"] >= ms[-1] / 1000 - 1]
    eng_closed = [t for t in eng if t not in forced]
    shape(eng_closed, "engine (same window, closed)")
    if forced:
        shape(forced, "engine (open at archive end, forced)")
    lx = [xmap[k] for k in _live_x_keys(live, xmap)]
    print(f"  entry-x: live median x={np.median(lx) if lx else float('nan'):.0f} "
          f"(minor-class x<8: {sum(1 for v in lx if v < 8)}/{len(lx)})  |  "
          f"engine-window median x={np.median([t['x'] for t in eng]):.0f} "
          f"(minor: {sum(1 for t in eng if t['x'] < 8)}/{len(eng)})")
    mwin = (ms[1:] >= (t0 - 60) * 1000)
    print(f"  window regime: mean rolling median {med[mwin].mean():.4f}u "
          f"(normal ~0.0085, elevated ~0.025) over "
          f"{mwin.sum():,} archived ticks")
    print()

    # ---- S2. bridge ----
    print(f"=== S2. BRIDGE (pair by entry time <= {PAIR_TOL:.0f}s, same side) ===")
    matched, eng_only, live_only = bridge(eng, live, PAIR_TOL)
    for tol in (120.0, 240.0, 600.0):
        m_, eo_, lo_ = bridge(eng, live, tol)
        print(f"  tol={tol:>4.0f}s: matched n={len(m_):2d}  engine-only n={len(eo_):2d}"
              f"  live-only n={len(lo_):2d}")
    print("  (using tol=240s below)")
    mm = [(e, l) for e, l in matched if l["kind"] == "EXIT"]
    mn = [(e, l) for e, l in matched if l["kind"] == "MANEXIT"]
    e_m = sum(e["pnl"] for e, _ in matched)
    l_m = sum(l["pnl"] for _, l in matched)
    e_o = sum(t["pnl"] for t in eng_only)
    l_o = sum(t["pnl"] for t in live_only)
    print(f"  matched auto    n={len(mm):2d}  engine ${sum(e['pnl'] for e,_ in mm):+6.2f}"
          f"  live ${sum(l['pnl'] for _,l in mm):+6.2f}"
          f"  delta ${sum(l['pnl']-e['pnl'] for e,l in mm):+6.2f}")
    print(f"  matched manual  n={len(mn):2d}  engine ${sum(e['pnl'] for e,_ in mn):+6.2f}"
          f"  live ${sum(l['pnl'] for _,l in mn):+6.2f}"
          f"  (user closed early; delta is discretion, not error)")
    print(f"  engine-only (missed)      n={len(eng_only):2d}  net ${e_o:+6.2f}")
    print(f"  live-only  (executor extra) n={len(live_only):2d}  net ${l_o:+6.2f}")
    for e, l in sorted(matched, key=lambda p_: p_[1]["pnl"] - p_[0]["pnl"]):
        print(f"    {datetime.fromtimestamp(l['entry_ts'], tz=timezone.utc):%m-%d %H:%M}"
              f" side={l['side']:+d} age={l['age']:>6s} {l['kind']:7s}"
              f" live ${l['pnl']:+6.2f} vs eng ${e['pnl']:+6.2f}"
              f" = {l['pnl']-e['pnl']:+6.2f}")
    if eng_only:
        print("  engine-only trips (live missed):")
        for t in eng_only:
            print(f"    {datetime.fromtimestamp(t['entry_ts'], tz=timezone.utc):%m-%d %H:%M}"
                  f" side={t['side']:+d} ${t['pnl']:+6.2f}")
    if live_only:
        print("  live-only trips (live took, engine wouldn't):")
        for t in sorted(live_only, key=lambda t_: t_["pnl"]):
            print(f"    {datetime.fromtimestamp(t['entry_ts'], tz=timezone.utc):%m-%d %H:%M}"
                  f" side={t['side']:+d} age={t['age']:>6s} {t['kind']:7s} ${t['pnl']:+6.2f}")
    print()

    # ---- S3. scorecard ----
    print("=== S3. SCORECARD ($/trip vs expectations) ===")
    lp = np.array([t["pnl"] for t in live])
    ep = np.array([t["pnl"] for t in eng_closed]) if eng_closed else np.array([0.0])
    print(f"  live v2 (all)      : ${lp.mean():+.3f}/trip  (n={len(lp)}, net ${lp.sum():+.2f})")
    print(f"  live v2 (auto only): "
          f"${np.mean([t['pnl'] for t in live if t['kind']=='EXIT']):+.3f}/trip"
          f"  (n={sum(1 for t in live if t['kind']=='EXIT')})")
    print(f"  engine same window : ${ep.mean():+.3f}/trip  (n={len(ep)}, net ${ep.sum():+.2f})")
    print(f"  engine full archive: ${base:+.3f}/trip  (AF expectation, n={len(cn)})")
    print(f"  -> live vs engine-window: {lp.mean()-ep.mean():+.3f}/trip"
          f"   live vs AF baseline: {lp.mean()-base:+.3f}/trip")
    print()

    # ---- S4. gate stats ----
    print("=== S4. GATE STATS (AJ template) ===")
    try:
        with open("data/flip_exec/uptime.json") as f:
            import json
            up = json.load(f)
        ok_, tot_ = up["hb_current"]
        pen = up["pending"]
        print(f"  uptime stream-current: {ok_}/{tot_} = {ok_/tot_:5.1%}")
        print(f"  pending fates: fills={pen['fills']} cancels={pen['cancels']} "
              f"expires={pen['expires']} (engine-window fills: {len(eng)})")
        span_d = (t1 - t0) / 86400.0
        print(f"  fill rate: live {pen['fills']/max(span_d,1e-9):.1f}/day vs "
              f"engine-window {len(eng)/max(span_d,1e-9):.1f}/day over "
              f"{span_d:.2f}d")
    except (OSError, KeyError, ValueError) as e:
        print(f"  uptime.json unreadable: {e}")

    # Wall-clock coverage: hb_current CANNOT see freezes - a suspended
    # process produces no heartbeats, so a 7h sleep reads as 99%+ currency.
    # Reconstruct coverage from HB-minute gaps (single-session assumption;
    # expected cadence 5m).
    try:
        hbs = []
        with open("archive/flip_exec.log", encoding="utf-8",
                  errors="replace") as f:
            for line in f:
                if "[HB" in line:
                    try:
                        hbs.append(float(line.split("[HB")[1].split("m]")[0]))
                    except ValueError:
                        pass
        gaps = [(a, b) for a, b in zip(hbs, hbs[1:]) if b - a > 10.0]
        if len(hbs) > 1:
            span = hbs[-1] - hbs[0]
            frozen = sum(b - a - 5.0 for a, b in gaps)
            print(f"  wall-clock coverage: {span/60:.1f}h log span, "
                  f"{len(gaps)} freeze gap(s) >10m "
                  f"{[f'+{b-a:.0f}m' for a, b in gaps]}")
            print(f"  frozen ~{frozen/60:.2f}h -> wall uptime "
                  f"~{100*(1 - min(frozen, span)/max(span, 1e-9)):.1f}% "
                  f"(hb_current cannot see freezes)")
    except OSError:
        print("  (executor log unreadable - wall-clock coverage skipped)")
    print()

    # ---- S5. honesty ----
    print("=== S5. what this can and cannot prove ===")
    print("  CAN: early direction of the reseed vs its own backtest on the")
    print("       same ticks; whether the delay machine fills as designed;")
    print("       whether availability is actually fixed (the AI finding).")
    print("  CANNOT: anything at n~15 - v1 was +$3.30 after 13 trips then")
    print("       died. No config change, no K re-point, no menu change.")
    print("       The Oct 15 gate (28d) is the decision point.")


def _live_x_keys(live, xmap):
    keys = []
    for t in live:
        best = min(xmap, key=lambda k: abs(ts(k) - t["entry_ts"]), default=None)
        if best is not None and abs(ts(best) - t["entry_ts"]) <= 2.0:
            keys.append(best)
    return keys


def rolling_medians_safe(ms, px):
    """Compute + cache immediately (timeout-survivable across reruns)."""
    from flip_rolling_engine import rolling_medians
    med = rolling_medians(ms, px)
    np.savez(CACHE, n=len(px), med=med)
    return med


if __name__ == "__main__":
    main()
