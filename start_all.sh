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

# Frontend in background
cd frontend
# node_modules 없으면 자동 설치(원치 않으면 이 블록 삭제해도 됨)
if [ ! -d "node_modules" ]; then
  echo "frontend/node_modules not found. Installing dependencies..."
  npm install
fi

# Expose Vite to network
npm run dev -- --host 0.0.0.0 --port 5173 &
FRONTEND_PID=$!
cd ..

cleanup() {
  echo ""
  echo "Stopping Backend (PID $BACKEND_PID) and Frontend (PID $FRONTEND_PID)..."
  kill $BACKEND_PID $FRONTEND_PID 2>/dev/null || true
  exit 0
}
trap cleanup SIGINT SIGTERM

LOCAL_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
PUBLIC_IP="$(curl -s --max-time 1 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"

echo "Backend:  http://$LOCAL_IP:8000 (PID $BACKEND_PID)"
echo "Frontend: http://$LOCAL_IP:5173 (PID $FRONTEND_PID)"
if [ -n "$PUBLIC_IP" ]; then
  echo ""
  echo "External access (if Security Group allows):"
  echo "  Backend:  http://$PUBLIC_IP:8000"
  echo "  Frontend: http://$PUBLIC_IP:5173"
fi
echo ""
echo "Press Ctrl+C to stop both."
echo ""

wait
