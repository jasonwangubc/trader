@echo off
REM Trader backend startup script.
REM Run this at Windows startup via Task Scheduler (trigger: At log on).
REM Or install as a service with NSSM: https://nssm.cc/download
REM
REM This script uses %~dp0 to find its own location — works from any user path.

cd /d "%~dp0backend"
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1
