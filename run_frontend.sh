#!/usr/bin/env bash
# BFAT frontend build script. Run from project root.
set -e
cd "$(dirname "$0")/frontend" || exit 1

npm install
npm run build
