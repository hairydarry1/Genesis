#!/usr/bin/env python3
"""flip_power_guard.py - Oct 5/6 power guard for the flip1000 cutoff.

Advisory watchdog + state saver. It does NOT mute the executor and does not
intercept power events. It watches the machine and the executor, then writes
its own JSON for the human/judgment to act on.

Reads the same operational facts as the executor:
  - /AC vs /BATTERY (battery present -> not running on AC; 52% discharging).
  - data/flip_exec/executor.pid (does an old build hold a live process?)
  - data/flip_exec/day_state.json  (day_pnl / day_halted / floored)
  - data/flip_exec/engine_state.json (restart-neutral phase state)
  - data/flip_exec/uptime.json     (session_started)


How it should be used:

  1. Run it at every machine wake-up and at every hour.
  2. Before Oct 5:  power_state must READ /AC, AND the executor must be off
                    (no live PID), AND the executor must NOT be re-armed
                    until the flat-exit checklist is done.
  3. Before Oct 5/6:
       power_state == /AC
       AND no live executor PID that is NOT the flat-exit state
       AND day_state.json says day_halted=true, floored=false
     THEN the machine is clear to power off / hibernate.
  4. After any power loss, read the JSON before re-launching the executor:
       - if power_state == /BATT OR power_cycle_occurred == true
         -> check MT5 positions by hand; reconcile; then re-launch with
            FLIP_ALLOW_LIVE.

Rules inside the file (must match the code):
  - Live entries need FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY at launch
    (flag dies with the shell; 02:30 task restart cannot inherit it).
  - Halts: day_pnl <= -$9.00 per UTC day (raw); equity floor $4.00 ->
    "floored": true (permanent, manual review required).
  - 60u broker-side SL backstop caps worst case at ~ $6 on open positions.
"""


import argparse
import ctypes
import json
import os
import sys
import time
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "..", "data", "flip_exec")
DAY_STATE = os.path.join(OUT, "day_state.json")
ENGINE_STATE = os.path.join(OUT, "engine_state.json")
UPTIME_JSON = os.path.join(OUT, "uptime.json")
PID_FILE = os.path.join(OUT, "executor.pid")

# Live-entry precursor: same env var the executor reads (inherited by parent,
# not by a 02:30 restart - see assert_demo_account in eval/flip_executor.py).
LIVE_ENV = "FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY"


def utcnow_iso():
    return datetime.now(timezone.utc).isoformat()


def read_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)


def power_state():
    """Best-effort Windows battery/AC read via Win32; fall back to stale + log."""
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        class SYSTEM_POWER_STATUS(ctypes.Structure):
            _fields_ = [("ACLineStatus", ctypes.c_char),
                        ("BatteryFlag", ctypes.c_char),
                        ("BatteryLifePercent", ctypes.c_ubyte),
                        ("SystemStatusFlag", ctypes.c_ubyte),
                        ("BatteryLifeTime", ctypes.c_ulong),
                        ("BatteryFullLifeTime", ctypes.c_ulong)]
        status = SYSTEM_POWER_STATUS()
        if k32.GetSystemPowerStatus(ctypes.byref(status)):
            ac = status.ACLineStatus
            pct = status.BatteryLifePercent
            flag = status.BatteryFlag
            # ACLineStatus: 0=offline,1=online,255=batt unknown
            if ac == 1:
                return "ONAC"
            if ac == 0:
                return "BATTERY"
            if ac == 255:
                # Can't read AC; assume battery is only way to stay powered.
                return "BATTERY"
        return "UNKNOWN"
    except Exception:
        return "UNKNOWN"


def executor_alive():
    pid = read_json(PID_FILE, None)
    if not pid:
        return False, None
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False, None
    try:
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(0x1000, False, pid)
        if h:
            k32.CloseHandle(h)
            return True, pid
        return False, pid
    except Exception:
        return False, pid


def main():
    ap = argparse.ArgumentParser(description="Flip1000 Oct 5/6 power guard")
    ap.add_argument("--once", action="store_true",
                    help="Run one check and exit.")
    ap.add_argument("--loop", type=float, default=0,
                    help="Run in a loop, checking every N seconds "
                         "(0 = run once).")
    ap.add_argument("--file", default=os.path.join(OUT, "power_guard.json"),
                    help="Where to write/adopt the power state.")
    args = ap.parse_args()

    out = {
        "checked_at": utcnow_iso(),
        "power_state": power_state(),
        "executor_alive": False,
        "executor_pid": None,
        "day_state": read_json(DAY_STATE),
        "engine_state": read_json(ENGINE_STATE),
        "uptime": read_json(UPTIME_JSON),
        "power_cycle_occurred": False,   # set by caller on next boot
        "ruling": None,
        "ruling_at": None,
        "notes": [],
    }

    alive, pid = executor_alive()
    out["executor_alive"] = alive
    out["executor_pid"] = pid

    # Rules (these are the human/judgment checks, not a mute).
    ds = out["day_state"] or {}
    halted = bool(ds.get("day_halted", False))
    floored = bool(ds.get("floored", False))
    on_ac = out["power_state"] == "ONAC"
    live = alive

    notes = []
    if out["power_state"] == "ONAC":
        notes.append("ON AC: safe to power off / hibernate this session.")
    else:
        notes.append("NOT ON AC (battery/unknown): STOP the executor; "
                     "do not leave it running into power loss.")

    if floored:
        notes.append("EQUITY FLOOR active (floored=true): manual review "
                     "required, do not re-arm.")
    elif not halted:
        notes.append("day HALTED=false: entries are still open this UTC day - "
                     "do not power off with the executor armed unless the "
                     "flat-exit checklist is done first.")

    if live:
        notes.append("executor ALIVE (pid %s): STOP and re-check before any "
                     "power event." % pid)
    else:
        notes.append("executor DEAD (pid file absent/stale): nothing left "
                     "running; safe to power off once day_state is halted.")

    if on_ac and (not live) and (not floored) and halted:
        out["ruling"] = "CLEAR_TO_POWER_OFF"
        out["notes"] = notes
        out["ruling_at"] = utcnow_iso()
    elif not on_ac or live:
        out["ruling"] = "HOLD"
        out["notes"] = notes
        out["ruling_at"] = utcnow_iso()
    else:
        out["ruling"] = "HOLD"
        out["notes"] = notes
        out["ruling_at"] = utcnow_iso()

    write_json(args.file, out)

    print(json.dumps(out, indent=2))
    if out["ruling"] == "HOLD":
        print("RULING: HOLD - do not power off until the runbook's flat-exit "
              "checklist is done and the executor is off.")
        sys.exit(0)
    if out["ruling"] == "CLEAR_TO_POWER_OFF":
        print("RULING: CLEAR_TO_POWER_OFF - machine is ON AC, executor is "
              "dead, day_state is halted/false, floored false.")
        sys.exit(0)
    print("RULING: UNKNOWN")
    sys.exit(1)


if __name__ == "__main__":
    main()
