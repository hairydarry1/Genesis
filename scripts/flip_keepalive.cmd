@echo off
REM ============================================================
REM  Flip1000 executor keepalive - safe to run at ANY time.
REM  Scheduled task "FlipExecutor-Keepalive-*" runs this at logon
REM  and every 30 minutes so a reboot or crash cannot leave the
REM  book dead until someone notices.
REM
REM  Double-launch is impossible: this checks the executor's own
REM  pid file first, and eval/flip_executor.py holds a single-
REM  instance lock (prints "REFUSED: another executor instance")
REM  as the backstop.
REM ============================================================

cd /d "C:\Users\hp\Desktop\code\Genesis"

REM --- if a pid file exists and that PID is a live python.exe, do nothing ---
if exist data\flip_exec\executor.pid (
    for /f %%p in (data\flip_exec\executor.pid) do set FLIP_PID=%%p
    if defined FLIP_PID (
        tasklist /FI "PID eq %FLIP_PID%" 2>NUL | find /I "python.exe" >NUL
        if not errorlevel 1 exit /b 0
    )
)

REM --- not running (or pid file stale): launch detached + unbuffered ---
REM     stdout -> archive/flip_exec.log (the log the user tails),
REM     stderr -> archive/flip_exec.err.log
REM
REM  LIVE ARM: OUT_DIR is data/flip_exec for BOTH modes, so resurrecting this
REM  without the flag would append DEMO trades to the LIVE slippage_ledger.csv
REM  / trades.csv / day_state.json and silently corrupt the book. The executor
REM  is therefore relaunched in the SAME mode it was meant to run in: live.
REM  The flag also dies with this cmd - it only ever applies to the launch below.
set FLIP_PID=
set FLIP_ALLOW_LIVE=I_UNDERSTAND_THIS_IS_REAL_MONEY
start "flip-exec" /B "C:\Users\hp\AppData\Local\Python\bin\python.exe" -u eval\flip_executor.py 1>>archive\flip_exec.log 2>>archive\flip_exec.err.log
exit /b 0
