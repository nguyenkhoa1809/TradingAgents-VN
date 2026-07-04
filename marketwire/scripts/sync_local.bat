@echo off
chcp 65001 > nul
echo.
echo ============================================================
echo   MarketWire - Sync DB tu Cloudflare R2 ve local
echo ============================================================
echo.

"%~dp0..\.venv\Scripts\python.exe" "%~dp0db_sync.py" download
if errorlevel 1 (
    echo.
    echo   [X] Sync that bai - xem loi chi tiet o tren.
    exit /b 1
)

echo.
echo   [OK] Sync thanh cong. DB hien tai nam o:
"%~dp0..\.venv\Scripts\python.exe" "%~dp0db_sync.py" path
echo.
