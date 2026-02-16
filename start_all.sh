#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

BACKEND_PORT=8000
FRONTEND_PORT=5173

# Error 98 (EADDRINUSE) 방지: 기존 포트 사용 프로세스 정리
for port in $BACKEND_PORT $FRONTEND_PORT; do
  if command -v fuser &>/dev/null; then
    if fuser -s "$port/tcp" 2>/dev/null; then
      echo "Port $port in use. Stopping existing process..."
      fuser -k "$port/tcp" 2>/dev/null || true
      sleep 1
    fi
  elif command -v lsof &>/dev/null; then
    PID=$(lsof -ti ":$port" 2>/dev/null || true)
    if [ -n "$PID" ]; then
      echo "Port $port in use (PID $PID). Stopping..."
      kill -9 $PID 2>/dev/null || true
      sleep 1
    fi
  fi
done

echo "Starting Backend and Frontend..."
echo ""

# Backend in background
cd backend
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$BACKEND_PORT" &
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
npm run dev -- --host 0.0.0.0 --port "$FRONTEND_PORT" &
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

echo "Backend:  http://$LOCAL_IP:$BACKEND_PORT (PID $BACKEND_PID)"
echo "Frontend: http://$LOCAL_IP:$FRONTEND_PORT (PID $FRONTEND_PID)"
if [ -n "$PUBLIC_IP" ]; then
  echo ""
  echo "External access (if Security Group allows):"
  echo "  Backend:  http://$PUBLIC_IP:$BACKEND_PORT"
  echo "  Frontend: http://$PUBLIC_IP:$FRONTEND_PORT"
fi
echo ""
echo "Press Ctrl+C to stop both."
echo ""

wait
