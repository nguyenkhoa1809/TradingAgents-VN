@echo off
chcp 65001 > nul
REM ─────────────────────────────────────────────────────────────────────────
REM  fetch_local.bat — chạy fetch TỪ MÁY LOCAL để lấy video YouTube.
REM
REM  Vì sao cần chạy tay ở máy: YouTube chặn IP datacenter của GitHub Actions
REM  với transcript API, nên phần video CHỈ lấy được từ máy có IP thường (máy
REM  bạn). Các nguồn RSS/tin khác thì GitHub tự chạy 8h sáng rồi, không cần.
REM
REM  Trình tự: kéo DB mới nhất từ R2 -> fetch (gồm YouTube) -> tóm tắt DeepSeek
REM  -> đẩy DB trở lại R2. Video sẽ hiện trên web ở lần deploy kế tiếp (8h sáng
REM  hôm sau CI tự render).
REM
REM  LƯU Ý: đừng chạy đúng lúc ~8h sáng (khi CI đang chạy) để tránh 2 bên cùng
REM  ghi đè DB lên R2. Chạy chiều/tối sau khi wavy scan xong là hợp lý nhất.
REM ─────────────────────────────────────────────────────────────────────────
cd /d "%~dp0"
set "PY=%~dp0.venv\Scripts\python.exe"

echo ============================================================
echo   MarketWire - Fetch local (YouTube) + sync R2
echo ============================================================

echo.
echo [1/4] Keo DB moi nhat tu R2...
%PY% scripts\db_sync.py download
if errorlevel 1 goto :err

echo.
echo [2/4] Fetch tin + video YouTube...
%PY% ingest\fetch.py

echo.
echo [3/4] Tom tat bang DeepSeek...
%PY% ingest\summarize.py

echo.
echo [4/4] Day DB tro lai R2...
%PY% scripts\db_sync.py upload
if errorlevel 1 goto :err

echo.
echo   [OK] Xong. Video se hien tren web o lan deploy ke tiep (8h sang hom sau).
echo.
pause
goto :eof

:err
echo.
echo   [X] Co buoc that bai - xem thong bao loi o tren.
echo.
pause
exit /b 1
