@echo off
chcp 65001 > nul
echo.
echo ============================================================
echo   MarketWire Preview
echo   http://localhost:8001
echo ============================================================
echo.

cd /d "%~dp0web\dist"
"%~dp0.venv\Scripts\python.exe" -m http.server 8001
