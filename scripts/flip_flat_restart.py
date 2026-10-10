#!/usr/bin/env python3
"""Flat-moment restart for the live flip1000 executor.

WHY THIS EXISTS
---------------
The 2026-10-06 book-anchor fix (eval/flip_executor.py: live book anchored to
account_info().balance + a $1.50 equity floor) only takes effect on a restart.
Restarting while a position is OPEN is survivable - the executor adopts the
orphan and the 60u SL is already attached broker-side - but it drops the
pending phase state and muddies the measurement. Restarting FLAT is clean.

Waiting for flat by hand is fragile (the position can close at 4am, or while
you are moving house). This watches for it and does the swap at that moment.

WHAT IT DOES
------------
  1. refuses to start if the executor is not already running (a dead executor
     is a human problem, not something to paper over with a relaunch)
  2. polls until NO position carries MAGIC 620001
  3. kills the executor, clears executor.pid, relaunches it with
     FLIP_ALLOW_LIVE armed - the SAME way scripts/flip_keepalive.cmd does
  4. reads the new log back and confirms the anchor + floor actually landed

SAFETY
------
- Read-only against the broker until step 3; it never closes a position.
- The gap between "flat" and "relaunched" is a few seconds. If a spike opens a
  fresh position inside that gap, the relaunch adopts it (the ADOPT path) and
  its SL is already attached, so nothing is left unmanaged.
- The single-instance lock in the executor is the backstop against a double
  launch if the 30-min keepalive task happens to fire in the same window.
- --dry-run prints the whole plan and touches nothing.

Usage:
  python scripts/flip_flat_restart.py --dry-run
  python scripts/flip_flat_restart.py --timeout-minutes 720
"""
import argparse
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PID_FILE = os.path.join(ROOT, "data", "flip_exec", "executor.pid")
LOG_FILE = os.path.join(ROOT, "archive", "flip_exec.log")
ERR_FILE = os.path.join(ROOT, "archive", "flip_exec.err.log")
SYMBOL = "Crash Boom Flip 1000 Index"
MAGIC = 620001
VPU_PER_UNIT = 0.10          # $/price-unit at 0.1 lot (matches VPU in the executor)
LIVE_ENV = "I_UNDERSTAND_THIS_IS_REAL_MONEY"


def log(msg):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def read_pid():
    try:
        with open(PID_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return None


def pid_alive(pid):
    """A pid file only counts as 'running' if that PID is still python.exe."""
    if not pid:
        return False
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                         capture_output=True, text=True).stdout
    return "python.exe" in out


def own_positions(mt5):
    return [p for p in (mt5.positions_get(symbol=SYMBOL) or [])
            if p.magic == MAGIC]


def tail_new(before, lines=40):
    """Lines appended to the log since `before` bytes (for post-restart checks)."""
    try:
        with open(LOG_FILE, "r", errors="replace") as f:
            f.seek(before)
            return f.read().splitlines()[-lines:]
    except OSError:
        return []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--poll-seconds", type=float, default=15.0)
    ap.add_argument("--timeout-minutes", type=float, default=720.0,
                    help="give up after this long (default 12h)")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the plan, change nothing")
    args = ap.parse_args()

    import MetaTrader5 as mt5
    if not mt5.initialize():
        log(f"FATAL MT5 init failed: {mt5.last_error()}")
        return 1
    try:
        pid = read_pid()
        if not pid_alive(pid):
            log(f"ABORT: executor is not running (pid file={pid}). Nothing to "
                "restart - launch it by hand and investigate why it died.")
            return 2
        if args.dry_run:
            pos = own_positions(mt5)
            log(f"DRY RUN: book is {'FLAT' if not pos else 'OPEN'} "
                f"({len(pos)} MAGIC-620001 position(s))")
            log(f"DRY RUN: would kill PID {pid}, clear {PID_FILE}, relaunch "
                f"{sys.executable} -u eval/flip_executor.py with "
                f"FLIP_ALLOW_LIVE={LIVE_ENV}, then verify the '[BOOK] LIVE "
                "anchor' banner and the floor")
            return 0

        log(f"executor alive as PID {pid}; waiting for a flat book")

        started = time.time()
        deadline = started + args.timeout_minutes * 60
        warned = False
        while True:
            pos = own_positions(mt5)
            if not pos:
                log("flat - no MAGIC-620001 position open")
                break
            if time.time() > deadline:
                log(f"TIMEOUT after {args.timeout_minutes:.0f} min with "
                    f"{len(pos)} position(s) still open - leaving the executor "
                    "alone. Restart it manually when flat.")
                return 3
            p = pos[0]
            log(f"holding ticket={p.ticket} vol={p.volume} "
                f"open={p.price_open} cur={p.price_current} "
                f"profit={p.profit:+.2f} sl={p.sl} - waiting")
            if p.sl and p.price_current and p.volume:
                # how far the broker's own backstop still is, in dollars
                left = abs(p.sl - p.price_current) * VPU_PER_UNIT * (p.volume / 0.1)
                if left > 5.0 and not warned:
                    warned = True
                    log(f"  note: the 60u backstop is ${left:.2f} away, but "
                        "the margin stop-out is far closer at ~$0.80 equity")
            time.sleep(args.poll_seconds)

        # ---- stand the old process down ----
        subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                       capture_output=True, text=True)
        time.sleep(3)
        if pid_alive(pid):
            log(f"ABORT: PID {pid} survived taskkill - refusing to relaunch "
                "into a possible double instance. Investigate by hand.")
            return 4
        try:
            os.remove(PID_FILE)
            log(f"removed {PID_FILE}")
        except OSError as e:
            log(f"could not remove pid file ({e}) - the executor's own lock "
                "will refuse a double launch, continuing")

        # ---- relaunch in the same shape the keepalive task uses ----
        env = dict(os.environ)
        env["FLIP_ALLOW_LIVE"] = LIVE_ENV
        env["PYTHONUNBUFFERED"] = "1"
        before = os.path.getsize(LOG_FILE) if os.path.exists(LOG_FILE) else 0
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP"):
            flags |= getattr(subprocess, name, 0)
        with open(LOG_FILE, "a") as out, open(ERR_FILE, "a") as err:
            subprocess.Popen(
                [sys.executable, "-u", os.path.join("eval", "flip_executor.py")],
                cwd=ROOT, env=env, stdout=out, stderr=err,
                stdin=subprocess.DEVNULL, creationflags=flags)
        log("relaunched with FLIP_ALLOW_LIVE armed - waiting for the banner")

        # ---- confirm the fix actually landed ----
        time.sleep(20)
        new = tail_new(before)
        anchor = [l for l in new if "LIVE anchor" in l]
        floored = [l for l in new if "EQUITY FLOOR" in l]
        refused = [l for l in new if "REFUSED" in l]
        for line in new:
            if any(k in line for k in ("LIVE MODE ARMED", "LIVE anchor",
                                       "BALANCE-OPS", "STATE]", "EQUITY FLOOR",
                                       "REFUSED")):
                log(f"  | {line.strip()}")
        if refused:
            log("WARNING: a second instance refused to start - the relaunch "
                "lost a race to the keepalive task. The running executor is "
                "still the RIGHT build; verify the floor on the next heartbeat.")
            return 0
        if not anchor:
            log("WARNING: no '[BOOK] LIVE anchor' line yet. Check "
                f"{LOG_FILE} by hand before trusting the floor.")
            return 5
        log(f"OK: anchor landed (EQUITY FLOOR lines: {len(floored)})")
        if floored:
            log("NOTE: the floor tripped on startup - entries are halted and "
                "need a reviewed 'floored: false' to re-arm.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())
