#!/usr/bin/env python3
"""Probe (Sep 18, AK follow-up): WHY did the executor miss 7 engine fills?

The AK bridge found 22 engine fills vs 15 live fills, 7 engine-only
(all 07:28-12:41Z Sep 18, net -$2.03, live-only 0). Executor log shows
NO guards/halts/backlog in that window, so the misses are signal-level.
Three candidate mechanisms, tested here:

  M1 THRESHOLD: engine qualified an entry spike the executor's live
     median scored just under fence 5 (x marginal).
  M2 CANCEL: executor armed but an opposite spike canceled before delay
     (executor saw a qualifying opposite spike the engine did not).
  M3 PHASE CASCADE: one early spike/vote divergence desynced cur_dir/cnt,
     flipping every subsequent arming for hours (the v1 restart disease,
     now without restarts).

Method:
  S1. engine trips (bit-exact C-clone d300) + live book + bridge ->
      engine-only set with x, med(thr) at entry.
  S2. executor occupancy from trades.csv (flat windows).
  S3. executor timeline from the log: HB lines carry session-min ->
      wall clock via uptime.json session_started; PENDING/BACKLOG/OPEN/
      CLOSE events bracketed by the preceding HB.
  S4. engine phase-at-spike replay (run_c loop, record cur_dir/cnt at
      every qualifying spike) vs executor HB phase -> first desync.

Usage: python analysis/flip_missed_fills_probe.py
"""
import sys
import csv
import json
import os
from datetime import datetime, timezone

import numpy as np

sys.path.insert(0, ".")
sys.path.insert(0, "analysis")
from flip_conditionality import load, WARMUP                      # noqa: E402
from flip_rolling_engine import VPU, STEP_LOTS, run_conv          # noqa: E402
import flip_rolling_engine as eng                                 # noqa: E402
from flip_oos_c import run_c                                      # noqa: E402

CACHE = "data/flip_exec/_rolling_med_flip1000.npz"
LOG = "archive/flip_exec.log"
PAIR_TOL = 240.0


def w(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def ts(s):
    return datetime.fromisoformat(s).timestamp()


def live_book():
    with open("data/flip_exec/trades.csv") as f:
        rows = list(csv.DictReader(f))
    trips, i = [], 0
    while i < len(rows) - 1:
        e, x = rows[i], rows[i + 1]
        if e["event"] == "OPEN" and x["event"] == "CLOSE":
            d = int(e["side"])
            trips.append({"side": d, "entry_ts": ts(e["ts_utc"]),
                          "exit_ts": ts(x["ts_utc"]),
                          "pnl": d * (float(x["price"]) - float(e["price"]))
                          * VPU * STEP_LOTS})
            i += 2
        else:
            i += 1
    return trips


def main():
    ms, px = load("flip1000")
    z = np.load(CACHE) if os.path.exists(CACHE) else None
    if z is None or int(z["n"]) != len(px):
        # archive grew since the last study - recompute + cache (same path
        # and key convention as flip_v2_score.py / flip_oos_c.py)
        from flip_rolling_engine import rolling_medians
        med = rolling_medians(ms, px)
        np.savez(CACHE, n=len(px), med=med)
        print(f"(median cache recomputed on extended archive, n={len(px):,})")
    else:
        med = z["med"]
    d = np.diff(px)

    # ---- S1. engine book + bridge ----
    cn, cc, cst = run_c(ms, px, med, side=0, mult=5.0, delay=300)
    rn, rc = run_conv(ms, px, med, None, side=0, mode="counter", mult=5.0, delay=300)
    assert len(rn) == len(cn) and np.allclose(rn, cn, atol=1e-12), "clone broke"
    eng_trips = [{"side": s[0], "x": s[1], "entry_ms": s[2], "exit_ms": c,
                  "pnl": p} for p, c, s in zip(cn.tolist(), cc.tolist(), cst)]
    live = live_book()
    t0 = min(t["entry_ts"] for t in live) * 1000
    t1 = max(t["exit_ts"] for t in live) * 1000
    win = [t for t in eng_trips if t0 - 60_000 <= t["entry_ms"] <= t1]

    used, matched, eng_only = set(), [], []
    for et in win:
        best, bdt = None, PAIR_TOL
        for k, lt in enumerate(live):
            if k in used or lt["side"] != et["side"]:
                continue
            dt = abs(lt["entry_ts"] * 1000 - et["entry_ms"]) / 1000
            if dt <= bdt:
                best, bdt = k, dt
        if best is None:
            eng_only.append(et)
        else:
            used.add(best)
            matched.append((et, live[best]))

    print("=== S1. the 7 engine-only fills, with threshold context ===")
    print(f"  {'entry':>11s} {'side':>4s} {'x':>8s} {'pnl':>6s} "
          f"{'med@entry':>9s} {'thr=5x':>7s} {'spike_d':>8s}")
    for t in sorted(eng_only, key=lambda t: t["entry_ms"]):
        j = np.searchsorted(ms, t["entry_ms"])
        m = med[min(j, len(med) - 1)]
        print(f"  {w(t['entry_ms'])} {t['side']:>+4d} {t['x']:>8.1f} "
              f"${t['pnl']:>+5.2f} {m:>9.5f} {5*m:>7.4f} {t['x']*m:>8.4f}")
    xs_m = [t[0]["x"] for t in matched]
    xs_o = [t["x"] for t in eng_only]
    print(f"  x of matched fills : min {min(xs_m):.1f}  median "
          f"{np.median(xs_m):.0f}")
    print(f"  x of missed fills  : min {min(xs_o):.1f}  median "
          f"{np.median(xs_o):.0f}  (fence = 5.0)")

    # ---- S2. executor occupancy ----
    print("\n=== S2. executor occupancy (was it even flat?) ===")
    for t in sorted(live, key=lambda t: t["entry_ts"]):
        print(f"  held {datetime.fromtimestamp(t['entry_ts'], tz=timezone.utc):%m-%d %H:%M}"
              f" -> {datetime.fromtimestamp(t['exit_ts'], tz=timezone.utc):%m-%d %H:%M}"
              f"  side {t['side']:+d}  ${t['pnl']:+5.2f}")

    # ---- S3. executor timeline from the log ----
    with open("data/flip_exec/uptime.json") as f:
        sess0 = json.load(f)["session_started"]
    events, last_hb = [], None
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[HB" in line:
                try:
                    mins = float(line.split("[HB")[1].split("m]")[0])
                    ticks = int(line.split("ticks=")[1].split(" ")[0])
                    medv = float(line.split("median=")[1].split(" ")[0])
                    ph = line.split("phase=")[1].split(" ")[0]
                    last_hb = (sess0 + mins * 60, ticks, medv, ph, line.strip())
                except (ValueError, IndexError):
                    pass
                continue
            for tag in ("[PENDING]", "[BACKLOG]", "OPEN ", "CLOSE ", "[ADOPT]"):
                if tag in line:
                    events.append((last_hb, tag.strip(), line.strip()))
                    break
    print("\n=== S3. executor event timeline (per-session wall-clock) ===")
    # The log is NOT timestamped. Segment sessions by banner lines; session
    # starts: S1 = the first v2 OPEN ts (trades.csv), S2+ = uptime.json
    # session_started of the current session (one restart known Sep 18).
    banners = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if "=== Flip1000 executor" in line:
                banners.append(i)
    s1 = live[0]["entry_ts"]                      # 19:38:17Z Sep 17 (first OPEN)
    with open("data/flip_exec/uptime.json") as f:
        s2 = json.load(f)["session_started"]
    lo, hi = datetime(2026, 9, 18, 6, 30, tzinfo=timezone.utc).timestamp(), \
             datetime(2026, 9, 18, 13, 30, tzinfo=timezone.utc).timestamp()
    print(f"  sessions detected in log: {len(banners)} (banner lines "
          f"{banners}); mapping S1 start {datetime.fromtimestamp(s1, tz=timezone.utc):%m-%d %H:%M}Z, "
          f"S2 start {datetime.fromtimestamp(s2, tz=timezone.utc):%m-%d %H:%M}Z")

    sess_idx = -1
    spikes_prev = ticks_prev = None
    cur_hb_t = None
    import time as _time
    hb_labels = []
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[HB" in line:
                try:
                    hb_labels.append(float(line.split("[HB")[1].split("m]")[0]))
                except (ValueError, IndexError):
                    pass
    if hb_labels:
        s1 = _time.time() - hb_labels[-1] * 60      # anchor: last HB ~ now
        s2 = s1
    with open(LOG, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if "=== Flip1000 executor" in line:
                sess_idx += 1
                spikes_prev = ticks_prev = None
                cur_hb_t = None
                continue
            if "[HB" in line:
                try:
                    mins = float(line.split("[HB")[1].split("m]")[0])
                    ticks = int(line.split("ticks=")[1].split(" ")[0]
                                .replace(",", ""))
                    medv = float(line.split("median=")[1].split(" ")[0])
                    ph = line.split("phase=")[1].split(" ")[0]
                    spk = int(line.split("spikes=")[1].split(" ")[0])
                except (ValueError, IndexError):
                    continue
                start = s1 if sess_idx <= 0 else s2
                tw = start + mins * 60
                cur_hb_t = tw
                if lo <= tw <= hi:
                    d_spk = (spk - spikes_prev
                             if spikes_prev is not None else spk)
                    d_tick = (ticks - ticks_prev
                              if ticks_prev is not None else ticks)
                    armed = "armed" in line
                    print(f"  {datetime.fromtimestamp(tw, tz=timezone.utc):%H:%M} "
                          f"S{sess_idx + 1} hb{mins:7.1f}m ticks+{d_tick:<6d} "
                          f"spikes+{d_spk:<3d} med={medv:.5f} phase={ph}"
                          f"{' ARMED' if armed else ''}")
                spikes_prev, ticks_prev = spk, ticks
                continue
            if cur_hb_t is not None and lo <= cur_hb_t <= hi:
                for tag in ("[PENDING]", "[BACKLOG]", "OPEN ", "CLOSE ",
                            "[ADOPT]", "[STATE]", "[RECONNECT]"):
                    if tag in line:
                        print(f"  ~{datetime.fromtimestamp(cur_hb_t, tz=timezone.utc):%H:%M} "
                              f"{line.strip()[:105]}")
                        break

    # ---- S4. engine phase at spikes vs executor phase ----
    print("\n=== S4. engine phase-at-spike replay, 06:30-13:30Z ===")
    bad = eng.bad_tick_mask(ms)
    thr_t = 5.0 * med
    cur_dir, cnt = 0, 0
    rows = []
    for j in range(WARMUP, len(px)):
        if bad[j]:
            cur_dir, cnt = 0, 0
            continue
        dj = d[j - 1]
        is_spike = bool(dj > thr_t[j - 1] or -dj > thr_t[j - 1])
        if is_spike:
            s = int(np.sign(dj))
            if s == cur_dir:
                cnt += 1
            else:
                cur_dir, cnt = s, 1
            tms = ms[j]
            if lo * 1000 <= tms <= hi * 1000:
                rows.append((tms, s, cnt, abs(dj) / med[j - 1]))
    for tms, s, cnt, x in rows:
        print(f"  {w(tms)}  spike {'+' if s>0 else '-'} phase={s:+d}x{cnt}"
              f"  x={x:8.1f}")


if __name__ == "__main__":
    main()
