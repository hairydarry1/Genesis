#!/usr/bin/env python3
"""P21a (Sep 17): LIVE-vs-ENGINE PnL bridge on the exact live window.

The live book ran 53 trips for -$10.18 (+$0.19/trip incl. manual; -$11.44
over the 52 auto-paired). Backtest says +$0.235/trip. The collector
archived the SAME tick stream the executor traded, so both books can be
built from identical data and the difference attributed:

  S1. LIVE book: pair ledger ENTRY->EXIT/MANEXIT (probe rows excluded).
  S2. ENGINE book: rolling-convention fleet engine (d0, LS, counter,
      the live config) restricted to trips ENTERED inside the live window.
  S3. BRIDGE: pair trips by entry time (<=240s) + same side:
        matched   -> per-trip delta live-engine
        engine-only -> live missed these (halts/backlog/guard/late)
        live-only   -> live took, engine wouldn't (adopts, stale phase)
  S4. FILL FRICTION: sum adverse_usd on ENTRY and EXIT fills (the direct
      cost of filling at broker instead of at the engine's tick).
  S5. WIN/LOSS SHAPE: avgW/avgL table both books (the 17:1 claim).
  S6. VERDICT: the bridge must close to the cent.

Usage: python analysis/flip_live_gap_audit.py
"""
import sys
import csv
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP                      # noqa: E402
from flip_rolling_engine import (run_conv, rolling_medians,       # noqa: E402
                                 VPU, SPREAD_USD, STEP_LOTS)
from flip_oos_c import run_c                                      # noqa: E402

LEDGER = "data/flip_exec/slippage_ledger.csv"


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def live_book():
    """Pair ledger rows: ENTRY -> (EXIT|MANEXIT), probes excluded."""
    with open(LEDGER) as f:
        rows = list(csv.DictReader(f))
    sig = [r for r in rows
           if not r["mode"].startswith("PROBE") and r.get("actual_deal")]
    trips, fric = [], {"entry": 0.0, "exit": 0.0, "n_fills": 0}
    i = 0
    while i < len(sig) - 1:
        e, x = sig[i], sig[i + 1]
        if e["mode"] == "ENTRY" and x["mode"] in ("EXIT", "MANEXIT"):
            d = 1 if e["side"] == "buy" else -1
            pnl = d * (float(x["actual_deal"]) - float(e["actual_deal"])) \
                * VPU * STEP_LOTS          # 0.1 lot: $0.10/unit
            trips.append({
                "side": d,
                "entry_ts": ts(e["spike_ts_utc"]),
                "exit_ts": ts(x["spike_ts_utc"]),
                "entry_px": float(e["actual_deal"]),
                "exit_px": float(x["actual_deal"]),
                "pnl": pnl,
                "kind": x["mode"],
                "age": int(e["note"].split("age")[-1]) if "age" in e["note"] else 1,
            })
            for r, k in ((e, "entry"), (x, "exit")):
                fric[k] += float(r["adverse_usd"] or 0.0)
                fric["n_fills"] += 1
            i += 2
        else:
            i += 1
    return trips, fric


def shape(trips, label):
    if not trips:
        print(f"  {label}: n=0")
        return
    p = np.array([t["pnl"] for t in trips])
    w, l = p[p > 0], p[p <= 0]
    print(f"  {label:28s} n={len(p):3d}  net=${p.sum():+7.2f}  "
          f"WR={(p > 0).mean():4.0%}  "
          f"avgW=${w.mean() if len(w) else 0:+5.2f}  "
          f"avgL=${l.mean() if len(l) else 0:+5.2f}  "
          f"ratio={abs(w.mean() / l.mean()) if len(w) and len(l) and l.mean() else 0:4.1f}:1  "
          f"worst=${p.min():+5.2f}")


def bridge(eng_trips, live, tol):
    """Greedy pairing by (entry time <= tol, same side). Returns dicts."""
    used = set()
    matched, eng_only = [], []
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
    live, fric = live_book()
    t0 = min(t["entry_ts"] for t in live)
    t1 = max(t["exit_ts"] for t in live)
    print(f"=== S1. LIVE book ===")
    print(f"  window: {datetime.fromtimestamp(t0, tz=timezone.utc):%m-%d %H:%M}Z -> "
          f"{datetime.fromtimestamp(t1, tz=timezone.utc):%m-%d %H:%M}Z")
    shape(live, "live (all, incl MANEXIT)")
    shape([t for t in live if t["kind"] == "EXIT"], "live (auto EXIT only)")
    print()

    print("building engine inputs (rolling medians on archive)...")
    ms, px = load("flip1000")
    import os
    cache = os.path.join("data", "flip_exec", "_rolling_med_flip1000.npz")
    med = None
    if os.path.exists(cache):
        z = np.load(cache)
        if int(z["n"]) == len(px):
            med = z["med"]
    if med is None:
        med = rolling_medians(ms, px)
        np.savez(cache, n=len(px), med=med)
    n0, c0, st0 = run_c(ms, px, med, side=0, mult=5.0, delay=0)
    rn, rc = run_conv(ms, px, med, None, side=0, mode="counter", mult=5.0, delay=0)
    ok = len(n0) == len(rn) and np.allclose(n0, rn, atol=1e-12)
    print(f"  engine validated: {'BIT-EXACT' if ok else 'MISMATCH'} "
          f"(n={len(n0)}, full-archive net ${n0.sum():+.2f})")

    # engine trips entered inside the live window
    eng_trips = []
    for pnl, cms, st in zip(n0.tolist(), c0.tolist(), st0):
        d, x, ems = st
        e_s = ems / 1000.0
        if t0 - 60 <= e_s <= t1:
            eng_trips.append({"side": d, "entry_ts": e_s, "pnl": pnl,
                              "exit_ms": cms})
    print(f"\n=== S2. ENGINE book (live config, trips entered in window) ===")
    shape(eng_trips, "engine (same window)")

    # ---- S3. pairing / bridge ----
    print(f"\n=== S3. BRIDGE (pair by entry time, same side) ===")
    for tol in (240.0, 600.0):
        m_, eo_, lo_ = bridge(eng_trips, live, tol)
        print(f"  tol={tol:.0f}s: matched n={len(m_)}  engine-only n={len(eo_)}  "
              f"live-only n={len(lo_)}")
    matched, eng_only, live_only = bridge(eng_trips, live, 240.0)

    e_m = sum(et["pnl"] for et, _ in matched)
    l_m = sum(lt["pnl"] for _, lt in matched)
    e_o = sum(t["pnl"] for t in eng_only)
    l_o = sum(t["pnl"] for t in live_only)
    print(f"  matched   n={len(matched):3d}  engine ${e_m:+7.2f}  live ${l_m:+7.2f}  "
          f"delta(live-eng) ${l_m - e_m:+7.2f}")
    print(f"  engine-only (live missed) n={len(eng_only):3d}  net ${e_o:+7.2f}")
    print(f"  live-only   (adopts/stale) n={len(live_only):3d}  net ${l_o:+7.2f}")
    print(f"  totals: engine ${e_m + e_o:+7.2f}   live ${l_m + l_o:+7.2f}   "
          f"gap ${l_m + l_o - e_m - e_o:+7.2f}")
    print("\n  biggest per-trip deltas (live - engine) on matched pairs:")
    rows = sorted(matched, key=lambda p_: p_[1]["pnl"] - p_[0]["pnl"])
    for et, lt in rows[:6]:
        print(f"    {datetime.fromtimestamp(lt['entry_ts'], tz=timezone.utc):%m-%d %H:%M} "
              f"side={lt['side']:+d} age={lt['age']} kind={lt['kind']:7s} "
              f"live ${lt['pnl']:+6.2f} vs eng ${et['pnl']:+6.2f} "
              f"= {lt['pnl'] - et['pnl']:+6.2f}")
    print("\n  live-only trips (live took, engine wouldn't):")
    for t in sorted(live_only, key=lambda t_: t_["pnl"]):
        print(f"    {datetime.fromtimestamp(t['entry_ts'], tz=timezone.utc):%m-%d %H:%M} "
              f"side={t['side']:+d} age={t['age']} kind={t['kind']:7s} "
              f"${t['pnl']:+6.2f}")
    print("\n  engine-only trips by UTC day (live missed):")
    from collections import defaultdict as _dd
    eo_day = _dd(lambda: [0, 0.0])
    for t in eng_only:
        dd = datetime.fromtimestamp(t["entry_ts"], tz=timezone.utc).date()
        eo_day[dd][0] += 1
        eo_day[dd][1] += t["pnl"]
    for dd in sorted(eo_day):
        print(f"    {dd}  n={eo_day[dd][0]:3d}  net ${eo_day[dd][1]:+6.2f}")
    print("  (known live outages: Sep 12 23:10->Sep 13 18:59 sleep, "
          "Sep 14 01:49->10:37 sleep, Sep 11 pm account-guard, daily halts)")

    # ---- S4. fill friction ----
    n_ent = sum(1 for t in live)
    print(f"\n=== S4. FILL FRICTION (adverse_usd, {fric['n_fills']} fills) ===")
    print(f"  entries: ${fric['entry']:+.4f}   exits: ${fric['exit']:+.4f}   "
          f"total ${fric['entry'] + fric['exit']:+.4f}")
    print(f"  (engine models spread as ${SPREAD_USD:.4f}/trip flat; live fills "
          f"embed real spread+slippage in adverse_usd)")

    # ---- S5. shapes ----
    print(f"\n=== S5. WIN/LOSS SHAPE (the 17:1 claim) ===")
    shape(eng_trips, "engine same-window")
    shape(live, "live all")

    # ---- S6. verdict ----
    gap = (l_m + l_o) - (e_m + e_o)
    print(f"\n=== S6. BRIDGE CHECK ===")
    print(f"  engine window expectation ${e_m + e_o:+.2f}")
    print(f"  + live-only (extra trades) ${l_o:+.2f}")
    print(f"  - engine-only (missed)     ${e_o:+.2f}")
    print(f"  + matched delta            ${l_m - e_m:+.2f}")
    print(f"  = live actual              ${(e_m + e_o) + l_o - e_o + (l_m - e_m):+.2f} "
          f"(live book: ${l_m + l_o:+.2f})")
    print(f"  gap explained to the cent: "
          f"{abs(((e_m + e_o) + l_o - e_o + (l_m - e_m)) - (l_m + l_o)) < 0.005}")


if __name__ == "__main__":
    main()
