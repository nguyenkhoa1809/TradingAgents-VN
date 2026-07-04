@echo off
chcp 65001 > nul
cd /d "%~dp0"
"%~dp0.venv\Scripts\python.exe" scripts\run_daily.py >> "%~dp0data\run_daily.log" 2>&1
