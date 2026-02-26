#!/usr/bin/env bash
# BFAT backend startup script. Run from project root.
set -e
cd "$(dirname "$0")/backend" || exit 1

if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -r requirements.txt -q

exec uvicorn main:app --host 0.0.0.0 --port 8000
