@echo off
REM ============================================================
REM  Flip tick-collector keepalive - safe to run at ANY time.
REM  Scheduled task "FlipTickCollector-Keepalive" runs this
REM  every 30 minutes. The collector is idempotent (72h rebuild
REM  window, dedupe, merge) so a missed run simply self-heals on
REM  the next one. Without it the gate's engine benchmark loses
REM  its data (found Sep 18: collector dead since 02:30, 44h of
REM  gaps that self-healed only because the broker's 72h depth
REM  still covered them).
REM ============================================================

cd /d "C:\Users\hp\Desktop\code\Genesis"

REM --- skip while the store was refreshed in the last 25 min ---
if exist data\flip_exec\_store_last_run.txt (
    powershell -NoProfile -Command ^
      "if ((Get-Date) - (Get-Item 'data\flip_exec\_store_last_run.txt').LastWriteTime -lt (New-TimeSpan -Minutes 25)) { exit 1 } else { exit 0 }"
    if not errorlevel 1 exit /b 0
)

set TMPLOG=data\flip_exec\_store_run.log
"C:\Users\hp\AppData\Local\Python\bin\python.exe" -u data\flip_tick_store.py > %TMPLOG% 2>&1
if errorlevel 1 (
    echo [%date% %time%] tick store run FAILED - see %TMPLOG%
) else (
    echo collector ok %date% %time% > data\flip_exec\_store_last_run.txt
)
exit /b 0
