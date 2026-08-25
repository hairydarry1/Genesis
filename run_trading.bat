@echo off
echo ========================================
echo   Genesis Trading System
echo ========================================
echo.
echo This will start:
echo   1. Multi-index paper trading (all 4 indices)
echo   2. Dashboard server on http://localhost:8080
echo.
echo Press any key to start, or Ctrl+C to cancel.
pause > nul

echo.
echo Starting dashboard server in background...
start /B python dashboard/server.py --port 8080

echo Waiting for server to start...
timeout /t 2 /nobreak > nul

echo.
echo Starting multi-index paper trading...
echo (Dashboard available at http://localhost:8080)
echo.
python -m eval.paper_trading_multi

echo.
echo Trading stopped. Dashboard server still running.
echo Press any key to stop dashboard server.
pause > nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *server*" > nul 2>&1
echo Dashboard server stopped.
