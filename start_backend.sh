#!/usr/bin/env bash
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/backend"

echo "Starting Backend (uvicorn) on http://127.0.0.1:8000"
echo ""
python3 -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
