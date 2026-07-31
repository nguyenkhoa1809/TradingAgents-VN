@echo off
chcp 65001 > nul
REM Thin ASCII wrapper -- see scripts/fetch_local.py for docs (UTF-8 safe there).
REM Do NOT put non-ASCII text in this file: cmd.exe mis-parses UTF-8 bytes in .bat.
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" "%~dp0scripts\fetch_local.py"
echo.
pause
