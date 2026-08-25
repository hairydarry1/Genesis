"""
test_connection_resilience.py — Soak / resilience test for DerivConnection.

Validates that the connection wrapper survives real-world conditions:
  - Continuous tick logging with gap detection
  - Reconnect event capture (drop, backoff, reconnect attempt/success/failure)
  - Graceful shutdown with summary

NOT part of the production pipeline. Run manually:

    python test_connection_resilience.py

Outputs:
    resilience_log.jsonl  — every tick, gap warning, and reconnect event

Press Ctrl+C to stop and see the summary.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Structlog setup — tee every log event to both stderr AND the JSONL file
# ---------------------------------------------------------------------------

import structlog

LOG_PATH = Path(__file__).parent / "resilience_log.jsonl"
GAP_THRESHOLD_S = 20  # seconds; longer than typical R_75 tick interval

_log_fh = None  # opened in main()


def _tee_to_file(logger: Any, method_name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Structlog processor that appends every log event to the JSONL file."""
    if _log_fh is not None:
        line = json.dumps(event_dict, default=str)
        _log_fh.write(line + "\n")
        _log_fh.flush()
    return event_dict


structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _tee_to_file,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(10),  # DEBUG+
    context_class=dict,
    logger_factory=structlog.PrintLoggerFactory(),
    cache_logger_on_first_use=True,
)

log = structlog.get_logger("resilience_test")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    global _log_fh

    # ---- Startup banner ----
    print()
    print("=" * 64)
    print("  DerivConnection Resilience / Soak Test")
    print("=" * 64)
    print()
    print("  How to use this test:")
    print()
    print("  1. SOAK TEST")
    print("     Let this script run untouched for several hours")
    print("     (ideally overnight) to test idle/keepalive behavior.")
    print()
    print("  2. FORCED DISCONNECT RECOVERY")
    print("     While this is running, disable your network connection")
    print("     for 30-60 seconds, then re-enable it.  Watch the log")
    print("     for reconnect events and resumed tick flow.")
    print()
    print(f"  Log file: {LOG_PATH}")
    print(f"  Gap threshold: {GAP_THRESHOLD_S}s")
    print()
    print("  Press Ctrl+C to stop and see the summary.")
    print("=" * 64)
    print()

    # ---- Open JSONL log ----
    _log_fh = open(LOG_PATH, "a", encoding="utf-8")
    log.info("resilience_test.started", pid=os.getpid())

    # ---- Counters ----
    total_ticks = 0
    gap_count = 0
    gap_durations: list[float] = []
    reconnect_events = 0
    start_time = time.monotonic()
    last_tick_time: float | None = None

    # ---- Import DerivConnection ----
    sys.path.insert(0, str(Path(__file__).parent))
    from data.deriv_connection import DerivConnection

    conn = DerivConnection()

    # ---- Tick handler ----
    def on_tick(msg: dict[str, Any]) -> None:
        nonlocal total_ticks, last_tick_time, gap_count, reconnect_events

        total_ticks += 1
        now = time.monotonic()

        tick_data = msg.get("tick", {})
        quote = tick_data.get("quote")
        epoch = tick_data.get("epoch")

        # Gap detection
        if last_tick_time is not None:
            gap_s = now - last_tick_time
            if gap_s > GAP_THRESHOLD_S:
                gap_count += 1
                gap_durations.append(gap_s)
                log.warning(
                    "gap_detected",
                    gap_seconds=round(gap_s, 2),
                    threshold=GAP_THRESHOLD_S,
                    sequence=total_ticks,
                )

        last_tick_time = now

        log.info(
            "tick",
            sequence=total_ticks,
            quote=quote,
            epoch=epoch,
            received_at=_now_iso(),
        )

    # ---- Subscribe ----
    try:
        await conn.connect()
        log.info("connection_established")

        sub_id = await conn.stream_ticks("R_75", handler=on_tick)
        log.info("subscribed", symbol="R_75", subscription_id=sub_id)

        print(f"[{_now_iso()}] Subscribed to R_75 (sub_id={sub_id})")
        print(f"[{_now_iso()}] Waiting for ticks…\n")

    except Exception as exc:
        log.error("startup_failed", error=str(exc))
        print(f"\n  FATAL: Could not connect or subscribe: {exc}\n")
        _log_fh.close()
        return

    # ---- Polling loop: detect reconnect events via connection state ----
    # The DerivConnection logs reconnect events via structlog, which is
    # already tee'd to our JSONL file.  We additionally poll `conn.connected`
    # to count reconnect *cycles* for the summary.
    prev_connected = True

    try:
        while True:
            await asyncio.sleep(1.0)

            cur_connected = conn.connected
            if prev_connected and not cur_connected:
                reconnect_events += 1
                log.info(
                    "reconnect_cycle_detected",
                    cycle=reconnect_events,
                    timestamp=_now_iso(),
                )
            elif not prev_connected and cur_connected:
                log.info(
                    "connection_restored",
                    cycle=reconnect_events,
                    timestamp=_now_iso(),
                )
            prev_connected = cur_connected

    except asyncio.CancelledError:
        pass
    except KeyboardInterrupt:
        pass

    # ---- Shutdown ----
    elapsed_s = time.monotonic() - start_time
    hours = int(elapsed_s // 3600)
    minutes = int((elapsed_s % 3600) // 60)
    seconds = int(elapsed_s % 60)

    try:
        await conn.close()
    except Exception:
        pass

    _log_fh.close()
    _log_fh = None

    # ---- Summary ----
    print()
    print("=" * 64)
    print("  RESILIENCE TEST SUMMARY")
    print("=" * 64)
    print(f"  Total ticks received:  {total_ticks}")
    print(f"  Total time running:    {hours}h {minutes}m {seconds}s")
    print(f"  Gaps detected:         {gap_count}")
    if gap_durations:
        print(f"    Gap durations (s):   {[round(g, 2) for g in gap_durations]}")
    print(f"  Reconnect cycles:      {reconnect_events}")
    print("=" * 64)
    print()
    print(f"  Full log written to: {LOG_PATH}")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # If Ctrl+C hits before the inner handler catches it, exit cleanly.
        print("\n  Interrupted. Exiting.\n")
