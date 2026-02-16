#!/bin/bash
# 서버에서 git pull 시 로컬 변경이 있으면 stash 후 pull
set -e
cd "$(dirname "$0")"

if ! git diff --quiet -- start_all.sh start_backend.sh 2>/dev/null; then
  echo "로컬 변경을 stash 합니다..."
  git stash push -m "server local $(date +%Y%m%d-%H%M%S)" -- start_all.sh start_backend.sh
fi
git pull
echo "Pull 완료. stash 한 변경을 버리려면: git stash drop"
