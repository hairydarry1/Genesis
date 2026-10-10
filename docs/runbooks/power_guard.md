# Flip1000 power guard — Oct 5/6

Run this wherever the executor is running. It is a **watchdog**, not a funding gate:

1. Machine is on battery (52%, discharging) -> STOP the executor; do not let it run into power loss.
2. Power is pulled while the executor is running -> the executor may have an open position
   (60u broker backstop only caps the *size* of a force-close, it does not preserve the
   measurement day). After power returns, reconcile before re-launching.
3. Logged-in MT5 terminal on a LIVE account + executor running -> any abortion of the
   executor must be deliberate; the flag `FLIP_ALLOW_LIVE` dies with the launching shell.

How to read this file:

- `power_state`: BATTERY / CHARGING / ONAC
- `executor_oldest_start`: if the executor is older than 24h, check for a real 24h run
  vs a zombie restarted during the power outage (the book only counts clean days).

This file is advisory. The hard stops live in `docs/runbooks/oct5-6_flip1000_funding_cutover.md`.
