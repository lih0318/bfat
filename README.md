# BFAT v2 (Binance Futures Auto Trader)

v2 작업용 루트입니다.

## 현재 구조

| 경로 | 설명 |
|------|------|
| **`backend/`** | v2 백엔드 (FastAPI, main.py 진입점, API/엔진/WebSocket) |
| **`frontend/`** | v2 프론트엔드 (Vite + React + Tailwind, Dashboard) |
| **`v1/`** | v1 레거시 코드 (마이그레이션 시 분리, 참고용) |
| **`_legacy/`** | ver.1 백엔드/프론트엔드 (참고용) |
| **`BACKUP/BFAT ver.1/`** | ver.1 전체 스냅샷 (복원용) |

API Key는 `backend/.env`에 설정합니다. `.env`는 `.gitignore`에 포함되어 Git에 올라가지 않습니다. 서버 배포 시 `SETUP.md`의 "서버에서 API Key 적용 방법"을 참고하세요.

## 실행

- **백엔드만**: `./start_backend.sh` → 루트 `backend/` (v2) 사용, 포트 8000
- **백엔드+프론트**: `./start_all.sh` → 루트 `backend/` + `frontend/` (v2), 포트 8000 / 5173

프론트는 최초 1회 `cd frontend && npm install` 필요할 수 있습니다.

## v2 플랜

이제 플랜을 세우고, 루트의 `backend/`와 `frontend/`만 수정하며 v2를 구성하면 됩니다.
