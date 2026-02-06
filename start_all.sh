#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Starting Backend and Frontend..."
echo ""

# Backend in background
cd backend
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!
cd ..

# Frontend in foreground (so Ctrl+C stops both via trap)
cd frontend
npm run dev &
FRONTEND_PID=$!
cd ..

cleanup() {
  echo ""
  echo "Stopping Backend (PID $BACKEND_PID) and Frontend (PID $FRONTEND_PID)..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

echo "Backend: http://127.0.0.1:8000 (PID $BACKEND_PID)"
echo "Frontend: http://localhost:5173 (PID $FRONTEND_PID)"
echo "Press Ctrl+C to stop both."
echo ""

wait
