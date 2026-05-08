@echo off
REM Trader backend startup script.
REM Run this at Windows startup via Task Scheduler (trigger: At log on).
REM Or install as a service with NSSM: https://nssm.cc/download

cd /d C:\Users\wyc_j\dev\trader\backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --workers 1
