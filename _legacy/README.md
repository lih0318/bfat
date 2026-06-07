# Legacy (BFAT ver.1 코드)

v2 작업 시 **기존 코드에 영향받지 않도록** 분리해 둔 폴더입니다.

- **`backend/`** — 이전 FastAPI 앱 (엔진, API, 서비스 등)
- **`frontend/`** — 이전 React + Vite 앱 (탭, 레이아웃, API 클라이언트 등)

**사용 목적**
- v2 개발 시 참고용으로만 사용
- 필요 시 특정 파일만 참고해서 루트의 `backend/`, `frontend/`에 이식

**실행**
- 루트의 `start_backend.sh` / `start_all.sh`는 루트의 `backend/`, `frontend/`(v2)를 사용합니다.
- legacy 앱을 실행하려면:
  - `cd _legacy/backend` 후 `uvicorn app.main:app --reload ...`
  - `cd _legacy/frontend` 후 `npm run dev`

원본 스냅샷은 **`BACKUP/BFAT ver.1/`** 에 있습니다.
