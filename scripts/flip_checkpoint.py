#!/usr/bin/env python3
"""Daily Flip checkpoint: executor liveness + slippage review capture.

Runs daily via Task Scheduler ("Genesis FlipCheckpoint Daily"). Each run:
  0. ensures the MT5 terminal is up (relaunches terminal64.exe if dead; a
     dead terminal fails every mt5.initialize() and would wedge both the
     probe and the executor restart silently - found 2026-09-21)
  1. checks executor liveness via data/flip_exec/executor.pid (OpenProcess)
  2. if dead: if our magic holds an open position on the account -> ALERT and
     stop (the executor cannot adopt an orphan; manual intervention needed);
     else auto-restart the executor detached (PowerShell Start-Process, same
     method that survived shell teardown on 2026-09-10)
  3. appends the slippage review output to archive/flip_checkpoint.log
  4. prints a one-line status: alive/restarted, n signal sides, verdict zone
  5. VERDICT SETTLING: on the first run with n >= 20 signal sides, freezes
     the verdict (GO / MARGINAL / NO-GO from the review's verdict zone) into
     data/flip_exec/verdict.json. Settled ONCE - later runs never overwrite
     it, so the decision cannot drift with subsequent noisy data. Note: this
     verdict applies to the incumbent L+S config; the long-only adoption is
     gated separately on the 28-day confirmation (flip_confirm_longonly).

Exit codes: 0 ok, 1 alert (position orphaned / review failed), 2 verdict
settled this run.

Usage: python scripts/flip_checkpoint.py
"""
import csv
import ctypes
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = f"{BASE}/data/flip_exec/executor.pid"
LEDGER = f"{BASE}/data/flip_exec/slippage_ledger.csv"
CKPT_LOG = f"{BASE}/archive/flip_checkpoint.log"
DIGEST = f"{BASE}/archive/flip_daily_digest.csv"
DOCS = f"{BASE}/docs/FLIP_INDEX_MECHANICS.md"
SETTLE_SENTINEL = f"{BASE}/data/flip_exec/settle_recorded.json"
SETTLE_GATE_N = 50
REVIEW = f"{BASE}/analysis/flip_slippage_review.py"
EXECUTOR = "eval/flip_executor.py"
SYMBOL = "Crash Boom Flip 1000 Index"
MAGIC = 620001
VPU = 0.10  # $ per price-unit per 0.1 lot (verified vs broker PnL)


def executor_alive():
    try:
        pid = int(open(PID_FILE).read().strip())
    except (OSError, ValueError):
        return False, 0
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if h:
        k32.CloseHandle(h)
        return True, pid
    return False, pid


def open_position_count():
    """Our magic's open positions (an orphaned one blocks auto-restart)."""
    try:
        import MetaTrader5 as mt5
        if not mt5.initialize():
            return -1
        try:
            pos = mt5.positions_get(symbol=SYMBOL) or []
            return sum(1 for p in pos if p.magic == MAGIC)
        finally:
            mt5.shutdown()
    except Exception as e:
        print(f"[WARN] MT5 position check failed: {e}")
        return -1


def terminal_ensure():
    """Make sure the MT5 terminal is running, relaunching it if dead.

    A dead terminal makes every mt5.initialize() fail, so session_deaf()
    bails with "probe init failed" and the executor restart below never
    fires - the whole chain wedges silently (gap found 2026-09-21).
    Returns (ok, reason).
    """
    import glob
    import time as _time
    import MetaTrader5 as mt5
    if mt5.initialize():
        mt5.shutdown()
        return True, "terminal up"
    exe = None
    for cand in sorted(glob.glob("C:/Program Files/*/terminal64.exe")):
        exe = cand
        break
    if not exe or not os.path.exists(exe):
        return False, "terminal down and terminal64.exe not found"
    ps = f"Start-Process -FilePath '{exe}'"
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   cwd=BASE, timeout=60, check=False)
    for i in range(12):  # terminal needs time to start, connect and log in
        _time.sleep(5)
        if mt5.initialize():
            mt5.shutdown()
            return True, f"terminal relaunched from {exe} (init ok ~{(i + 1) * 5}s)"
    return False, "terminal relaunched but initialize() still failing"


def session_deaf():
    """Detect the alive-but-deaf wedge (2026-09-12: PC slept ~18h, the
    executor kept printing heartbeats while its MT5 tick feed froze —
    process liveness passed, the machine traded nothing for 18h).

    Signal: the executor's own heartbeat tick count FROZEN across two
    consecutive heartbeats spaced >3 minutes (healthy = ~300 ticks per
    5-min heartbeat on flip1000), CONFIRMED against the terminal still
    streaming (if the terminal is frozen too, restarting won't help).

    Returns (deaf: bool, detail: str). Any error -> (False, ...) so the
    check can only ADD restarts, never break the checkpoint."""
    try:
        hbs = []  # (minutes, tick_count) from the executor's own heartbeats
        with open(f"{BASE}/archive/flip_exec.log", "r", encoding="utf-8",
                  errors="replace") as f:
            for ln in f:
                if ln.startswith("[HB") and "ticks=" in ln:
                    try:
                        mins = float(ln[3:ln.index("m]")])
                        ticks = int(ln.split("ticks=")[1].split(" ")[0]
                                    .replace(",", ""))
                        hbs.append((mins, ticks))
                    except (ValueError, IndexError):
                        pass
        if len(hbs) < 2:
            return False, f"only {len(hbs)} heartbeat(s) - too fresh to judge"
        (m0, t0c), (m1, t1c) = hbs[-2], hbs[-1]
        if not (t0c == t1c and m1 - m0 > 3):
            return False, (f"tick count advancing "
                           f"({t0c:,} -> {t1c:,} over {m1 - m0:.1f}m)")
        import MetaTrader5 as mt5
        import time as _time
        if not mt5.initialize():
            return False, "probe init failed (not restarting on a blind check)"
        try:
            tk = mt5.symbol_info_tick(SYMBOL)
            if tk is None:
                return False, ("terminal has no tick (no subscription) "
                               "- not executor deafness")
            _time.sleep(4)
            tk2 = mt5.symbol_info_tick(SYMBOL)
            if not (tk2 is not None and tk2.time > tk.time):
                return False, "terminal not streaming either - not executor deafness"
            return True, (f"tick count frozen at {t1c:,} for {m1 - m0:.1f}m "
                          "while terminal streams - session deaf")
        finally:
            mt5.shutdown()
    except Exception as e:
        return False, f"probe error (ignored): {e}"


def restart_executor():
    py = sys.executable
    exe = py.replace("\\", "/")
    log = f"{BASE}/archive/flip_exec.log".replace("\\", "/")
    err = f"{BASE}/archive/flip_exec_err.log".replace("\\", "/")
    wd = BASE.replace("\\", "/")
    ps = (f"Start-Process -FilePath '{exe}' "
          f"-ArgumentList '-u','{EXECUTOR.replace(os.sep, '/')}' "
          f"-WorkingDirectory '{wd}' -WindowStyle Hidden "
          f"-RedirectStandardOutput '{log}' -RedirectStandardError '{err}'")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   cwd=BASE, timeout=60, check=False)


def side_split_block(lines, now, side_split_cmd=None,
                     sentinel_path=None, docs_path=None):
    """Run the Appendix P side-split study and capture its output.

    The study prints INTERIM until live pairable trips reach n>=50, then a
    formal SETTLE section (inversion YES/NO + significance-gated escalation,
    per the Appendix P pre-registration). On the first SETTLE, the settle
    section is auto-recorded ONCE into docs/FLIP_INDEX_MECHANICS.md and a
    settle_recorded.json sentinel is written - same discipline as the
    verdict.json freeze, so the record cannot be rewritten or drift.

    side_split_cmd / sentinel_path / docs_path are injectable for testing;
    defaults are the real study, real sentinel, real docs.
    """
    if side_split_cmd is None:
        side_split_cmd = [sys.executable, f"{BASE}/analysis/flip_side_split.py"]
    if sentinel_path is None:
        sentinel_path = SETTLE_SENTINEL
    if docs_path is None:
        docs_path = DOCS
    try:
        out = subprocess.run(side_split_cmd, cwd=BASE, capture_output=True,
                             text=True, timeout=300)
        tail = "\n".join(out.stdout.rstrip().splitlines()[-14:])
        lines.append(f"[SIDE-SPLIT] {tail or out.stderr.strip()[:200] or '(no output)'}")
        # extract the settle section (from '--- settle ---' to EOF)
        grab = False
        settle_txt = ""
        for ln in out.stdout.splitlines():
            if "--- settle ---" in ln:
                grab = True
            if grab:
                settle_txt += ln + "\n"
        if (settle_txt and "SETTLE (n=" in settle_txt
                and not os.path.exists(sentinel_path)):
            n_live = settle_txt.split("SETTLE (n=")[1].split(")")[0]
            with open(sentinel_path, "w") as sf:
                json.dump({"settled": now.strftime("%Y-%m-%d %H:%M UTC"),
                           "n_live": n_live}, sf, indent=2)
            with open(docs_path, "a", encoding="utf-8") as df:
                df.write(f"\n**Side-split settle - auto-recorded by the daily "
                         f"checkpoint {now.strftime('%Y-%m-%d %H:%M UTC')} "
                         f"(first run at n>={SETTLE_GATE_N}):**\n\n```\n"
                         f"{settle_txt.rstrip()}\n```\n")
            lines.append(">>> SIDE-SPLIT SETTLE recorded into "
                         "docs/FLIP_INDEX_MECHANICS.md (Appendix P) - once only")
    except Exception as e:
        lines.append(f"[WARN] side_split failed: {e}")


def count_signal_sides():
    try:
        with open(LEDGER) as f:
            rows = list(csv.DictReader(f))
        return sum(1 for r in rows if not r["mode"].startswith("PROBE")
                   and r.get("actual_deal"))
    except OSError:
        return 0


def build_digest():
    """Rebuild archive/flip_daily_digest.csv from the slippage ledger:
    one row per UTC day with strategy round trips, PnL, model equity and
    order-failure counts. Recomputed whole each run (ledger is tiny), so
    ledger corrections self-heal the digest. Failed orders (empty
    actual_deal) are excluded from PnL and counted separately.
    Returns (yesterday_str, today_str) digest lines for the checkpoint log."""
    try:
        with open(LEDGER) as f:
            rows = [r for r in csv.DictReader(f)
                    if not r["mode"].startswith("PROBE") and r.get("actual_deal")]
    except OSError:
        return None, None
    fails = 0
    try:
        with open(LEDGER) as f:
            fails = sum(1 for r in csv.DictReader(f)
                        if not r["mode"].startswith("PROBE")
                        and not r.get("actual_deal")
                        and r.get("note", "").startswith("retcode"))
    except OSError:
        pass

    days = {}
    open_pos = None  # (day, side, price)
    eq = 10.0
    for r in rows:
        day = r["spike_ts_utc"][:10]
        d = days.setdefault(day, {"rt": 0, "wins": 0, "pnl": 0.0})
        side = r["side"]
        price = float(r["actual_deal"])
        if r["mode"] == "ENTRY":
            open_pos = (day, side, price)
        elif r["mode"].startswith("EXIT") and open_pos is not None \
                and side != open_pos[1]:
            oday, oside, oprice = open_pos
            pnl = (price - oprice) * VPU * (1 if oside == "buy" else -1)
            d["rt"] += 1
            d["wins"] += pnl > 0
            d["pnl"] += pnl
            eq += pnl
            days[oday].setdefault("pnl", 0.0)  # ensure open-day key exists
            open_pos = None
    with open(DIGEST, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day_utc", "round_trips", "wins", "day_pnl_usd",
                    "model_eq_usd"])
        for day in sorted(days):
            d = days[day]
            w.writerow([day, d["rt"], d["wins"], f"{d['pnl']:+.2f}",
                        f"{10.0 + sum(days[k]['pnl'] for k in days if k <= day):.2f}"])
    # failures are reported in the checkpoint log only — the digest CSV stays
    # a clean one-row-per-day schema (a footer row broke consumers on Sep 12)
    last2 = sorted(days)[-2:]
    lines = [f"DIGEST {day}: rt={days[day]['rt']} wins={days[day]['wins']} "
             f"pnl=${days[day]['pnl']:+.2f} eq=${10.0 + sum(days[k]['pnl'] for k in days if k <= day):.2f}"
             for day in last2]
    if fails:
        lines.append(f"DIGEST order_failures_total={fails}")
    return (lines + [None, None])[:2]


def main():
    now = datetime.now(timezone.utc)
    lines = [f"\n===== checkpoint {now:%Y-%m-%d %H:%M} UTC ====="]
    rc = 0
    up, why = terminal_ensure()
    if up:
        lines.append(f"terminal: ok ({why})")
    else:
        lines.append(f"ALERT: MT5 terminal unavailable - {why}; executor "
                     "restarts would fail tonight too")
        rc = 1
    alive, pid = executor_alive()
    if alive:
        # alive: is it alive-but-deaf? (post-sleep MT5 wedge, 2026-09-12)
        deaf, why = session_deaf()
        if deaf:
            npos = open_position_count()
            lines.append(f"executor: ALIVE but DEAF (pid {pid}) - {why} "
                         "-> restarting (ADOPT path re-syncs any position)")
            restart_executor()
            rc = 1
        else:
            lines.append(f"executor: alive (pid {pid}), session streaming ({why})")
    else:
        npos = open_position_count()
        if npos > 0:
            lines.append(f"ALERT: executor DEAD (stale pid {pid}) but {npos} open "
                         "position(s) with our magic — NOT auto-restarting "
                         "(cannot adopt an orphan); close manually, delete "
                         "executor.pid, then restart")
            rc = 1
        elif npos == 0:
            lines.append(f"executor: dead (pid {pid}), no open position -> "
                         "auto-restarting")
            restart_executor()
        else:
            lines.append("executor: dead, position check FAILED -> not "
                         "restarting this cycle")
            rc = 1

    # heal today's ticks (a sleep/wifi wedge leaves the current day nearly
    # empty and broker history depth is only hours - rescue early and
    # idempotently). Best-effort: never blocks the checkpoint's own duties.
    try:
        out = subprocess.run([sys.executable, f"{BASE}/scripts/heal_today.py"],
                             cwd=BASE, capture_output=True, text=True, timeout=420)
        heal_tail = "\n".join(out.stdout.rstrip().splitlines()[-6:])
        lines.append(f"[HEAL] {heal_tail or out.stderr.strip()[:200] or '(no output)'}")
        if out.returncode != 0:
            lines.append(f"[WARN] heal_today exit {out.returncode}")
            rc = rc or 1
    except Exception as e:
        lines.append(f"[WARN] heal_today failed: {e}")

    # daily digest: rebuild + echo last two days into the log
    try:
        d1, d2 = build_digest()
        for ln in (d1, d2):
            if ln:
                lines.append(ln)
    except Exception as e:
        lines.append(f"[WARN] digest failed: {e}")
        rc = rc or 1

    # telemetry scoring capture (P1 session pocket / P2 2h momentum): the
    # scorer self-gates on n>=8 per bucket and prints one summary when
    # meaningful; before that it just reports the stamped-trip count.
    try:
        out = subprocess.run(
            [sys.executable, f"{BASE}/analysis/flip_telemetry_score.py"],
            cwd=BASE, capture_output=True, text=True, timeout=120)
        tail = "\n".join(out.stdout.rstrip().splitlines()[-10:])
        lines.append(f"[TELEMETRY] {tail or out.stderr.strip()[:200] or '(no output)'}")
    except Exception as e:
        lines.append(f"[WARN] telemetry_score failed: {e}")

    # Appendix P side-split settle watch (function below; tested synthetically)
    side_split_block(lines, now)

    # review capture
    try:
        out = subprocess.run([sys.executable, REVIEW], cwd=BASE,
                             capture_output=True, text=True, timeout=120)
        lines.append(out.stdout.rstrip())
        if out.returncode != 0:
            lines.append(f"[WARN] review exit {out.returncode}: {out.stderr[:300]}")
            rc = rc or 1
        # compact status line from the review
        n = count_signal_sides()
        zone = next((ln.split("VERDICT ZONE:")[1].strip(" —")
                     for ln in out.stdout.splitlines() if "VERDICT ZONE:" in ln),
                    "?")
        lines.append(f"STATUS: n_signal={n} zone={zone}")
        # verdict settling: first crossing of n >= 20 freezes the decision
        verdict_path = f"{BASE}/data/flip_exec/verdict.json"
        if n >= 20 and not os.path.exists(verdict_path):
            zone_l = zone.lower()
            if "favorable" in zone_l:
                verdict = "GO"
            elif "marginal" in zone_l:
                verdict = "MARGINAL"
            else:
                verdict = "NO-GO"
            with open(verdict_path, "w") as vf:
                json.dump({"settled": now.strftime("%Y-%m-%d %H:%M UTC"),
                           "n_signal": n, "zone": zone, "verdict": verdict},
                          vf, indent=2)
            lines.append(f">>> VERDICT SETTLED: {verdict} (zone={zone}, n={n}) "
                         "-> data/flip_exec/verdict.json (L+S baseline config; "
                         "long-only adoption gated separately on 28-day confirm)")
            rc = rc or 2
    except Exception as e:
        lines.append(f"[WARN] review run failed: {e}")
        rc = rc or 1

    text = "\n".join(lines)
    print(text)
    with open(CKPT_LOG, "a", encoding="utf-8") as f:
        f.write(text + "\n")
    sys.exit(rc)


if __name__ == "__main__":
    main()
