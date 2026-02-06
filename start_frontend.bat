@echo off
setlocal

cd /d "%~dp0frontend"
echo Starting Frontend (Vite) on http://0.0.0.0:5173
echo.

npm run dev -- --host 0.0.0.0 --port 5173
