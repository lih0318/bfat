@echo off
echo Starting Backend and Frontend in new windows...
start "Backend" cmd /k "cd /d "%~dp0backend" && python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000"
start "Frontend" cmd /k "cd /d "%~dp0frontend" && npm run dev"
echo Backend: http://127.0.0.1:8000
echo Frontend: http://localhost:5173
