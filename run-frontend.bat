@echo off
REM trader frontend startup script.
REM Next.js dev server binds to 0.0.0.0 by default — accessible on LAN.
REM Access from other devices: http://[this-machine-ip]:3000

cd /d "%~dp0frontend"
npm run dev
