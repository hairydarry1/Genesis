@echo off
echo ============================================
echo   GENESIS SPIKE CATCHER — Full System
echo ============================================
echo.
echo This will:
echo   1. Start the Python Brain Bridge
echo   2. Monitor MQL5 trade logs
echo   3. Analyze and adapt parameters
echo.
echo Make sure:
echo   - MT5 is running with SpikeCatcher EA attached
echo   - AutoTrading is enabled (green button)
echo.
echo ============================================
echo.

REM Start the Python bridge
cd /d "%~dp0\.."
python mt5/mql5_bridge.py --symbol %1 --interval 30

pause
