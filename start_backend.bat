@echo off
cd /d "%~dp0backend"
echo Starting Backend (uvicorn) on http://127.0.0.1:8000
echo.
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
pause
