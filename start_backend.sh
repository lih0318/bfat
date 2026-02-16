#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

PORT=8000
# Error 98 (EADDRINUSE): 기존에 8000 포트를 쓰는 프로세스가 있으면 종료 후 재시작
if command -v fuser &>/dev/null; then
  if fuser -s "$PORT/tcp" 2>/dev/null; then
    echo "Port $PORT already in use. Stopping existing process..."
    fuser -k "$PORT/tcp" 2>/dev/null || true
    sleep 2
  fi
elif command -v lsof &>/dev/null; then
  PID=$(lsof -ti ":$PORT" 2>/dev/null || true)
  if [ -n "$PID" ]; then
    echo "Port $PORT already in use (PID $PID). Stopping..."
    kill -9 $PID 2>/dev/null || true
    sleep 2
  fi
fi

echo "Starting Backend (uvicorn) on http://0.0.0.0:$PORT"
echo ""
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port "$PORT"
