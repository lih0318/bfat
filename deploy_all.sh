#!/usr/bin/env bash
# BFAT full deployment. Assumes Ubuntu EC2, Node + Python installed.
# Run from project root.
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "[1/4] Building frontend..."
cd frontend
npm install
npm run build
cd ..

echo "[2/4] Deploying frontend to /var/www/bfat..."
sudo mkdir -p /var/www/bfat
sudo cp -r frontend/dist/* /var/www/bfat/

echo "[3/4] Restarting nginx..."
sudo systemctl reload nginx 2>/dev/null || sudo systemctl restart nginx 2>/dev/null || true

echo "[4/4] Starting backend..."
cd backend
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt -q
nohup uvicorn main:app --host 127.0.0.1 --port 8000 > /tmp/bfat_backend.log 2>&1 &
echo "Backend started. Log: /tmp/bfat_backend.log"
