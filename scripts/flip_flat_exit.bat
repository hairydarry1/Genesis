@echo off
REM flip_flat_exit.bat - Oct 5-6 clean flat + ledger reconciliation + file sync
REM Usage: double-click or run from a shell. Prints the flat-exit checklist.

setlocal

set "B=%~dp0.."
set "EXEC=%B%\eval\flip_executor.py"
set "OUT=%B%\data\flip_exec"
set "PID=%OUT%\executor.pid"
set "DAY_STATE=%OUT%\day_state.json"
set "TRADES=%OUT%\trades.csv"
set "SLIP=%OUT%\slippage_ledger.csv"
set "ENGINE=%OUT%\engine_state.json"
set "LOG=%B%\archive\flip_exec.log"

echo.
echo ====== Flip1000 flat exit + reconciliation + sync ======
echo.

echo [1] Power check
echo   -> machine on AC? If NO, STOP and plug in.
if not exist "%LOG%" (
  echo   -> WARN: %LOG% not found yet; executor may not have run this session.
)

echo.
echo [2] Read executor status
if exist "%PID%" (
  for /f %%p in (%PID%) do set "FLIP_PID=%%p"
  echo   -> executor PID in file: %FLIP_PID%
) else (
  echo   -> no executor.pid; no live executor session is assumed.
)

echo.
echo [3] Flat exit via manual counterfactual (records 10W/2L)
echo       python scripts\flip_manual_counterfactual.py
echo.
python "%B%\scripts\flip_manual_counterfactual.py"
if errorlevel 1 (
  echo [!] counterfactual script failed; do not power off yet.
  pause
  exit /b 1
)

echo.
echo [4] Ledger reconciliation
echo       python scripts\heal_ledger_slfail.py
echo.
python "%B%\scripts\heal_ledger_slfail.py"
if errorlevel 1 (
  echo [!] reconciliation reported slfail/phantom rows; do not proceed.
  pause
  exit /b 1
)

echo.
echo [5] Verify ledger whole: MANEXIT + SL-backstop rows present
python "%B%\data\flip_exec\day_state.json" >nul 2>&1
if exist "%DAY_STATE%" (
  echo   -> day_state.json exists: %DAY_STATE%
  type "%DAY_STATE%"
) else (
  echo   -> WARN: %DAY_STATE% not found.
)

echo.
echo [6] Confirm no open position in MT5 before powering off
echo       (manual close + heartbeat sync should be reflected)
echo.
echo   -> Open MT5 -> Terminal -> Positions. Confirm:
echo        - symbol Crash Boom Flip 1000 Index: NONE open
echo        - no DEAL_REASON_SL / SL backstop position left orphaned
echo.
echo [7] Confirm day state is halted, not floored
if exist "%DAY_STATE%" (
  for /f "tokens=1-7 delims=:," %%a in ('findstr /i "day_pnl" "%DAY_STATE%"') do (
    echo   -> day_pnl=%%b  day_halted=%%c  floored=%%d  (read %DAY_STATE%)
  )
)

echo.
echo [8] Expected at flat: day_halted=true, floored=false, no open position.
echo     If floored=true -> STOP (manual review required, do NOT re-arm).
echo.
echo [9] Sync files for migration (if you are moving to a new host)
echo       Copy from %B% to the new host:
echo         eval/flip_executor.py
echo         scripts/flip_checkpoint.py
echo         scripts/flip_keepalive.cmd
echo         scripts/flip_status.ps1
echo         scripts/flip_store_keepalive.cmd
echo         scripts/flip_tick_update.bat
echo         data/flip_exec/
echo         SpikeCatcher/brain_config_*.json
echo         SpikeCatcher/config_*.json
echo.
echo [10] STOP: do NOT run the executor into a power loss with an open position.
echo      Power off / hibernate only after this checklist completes.
echo.
echo [11] If the executor is still running, do NOT kill it without first
echo      confirming: no open MT5 position and day_state.json says
echo      day_halted=true, floored=false. The Oct 6 cutover re-arms this
echo      check on your PC (or the migrated host).
echo.
pause
