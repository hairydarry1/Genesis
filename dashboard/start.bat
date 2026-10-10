@echo off
echo ============================================
echo   Genesis Trading Terminal
echo ============================================
echo.
echo Starting server on http://localhost:8080
echo.
python -m dashboard.server --port 8080 --ws-port 8081
pause
