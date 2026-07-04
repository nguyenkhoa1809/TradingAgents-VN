@echo off
chcp 65001 > nul
set LOGDIR=%~dp0..\logs
if not exist "%LOGDIR%" mkdir "%LOGDIR%"
set LOGFILE=%LOGDIR%\%DATE:~10,4%-%DATE:~4,2%-%DATE:~7,2%_%TIME:~0,2%-%TIME:~3,2%.log
"%~dp0..\.venv\Scripts\python.exe" "%~dp0run_daily.py" >> "%LOGFILE%" 2>&1
