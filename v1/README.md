# BFAT V1 (Archived)

이 폴더는 BFAT V1 → V2 마이그레이션 시 분리된 레거시 코드를 보관합니다.

## 구조

```
v1/backend/app/
├── api/          # 구 API 엔드포인트 (kill_switch, log, stats, routes)
├── engine/       # 구 엔진 (orchestrator, state_machine)
├── execution/    # 구 실행 레이어
├── market/       # 구 마켓 모듈
├── risk/         # 구 리스크 모듈
├── strategy/     # 구 전략 모듈
└── main.py       # 구 FastAPI 진입점 (app.main:app)
```

## 참고

- **실행 가능한 앱**: 프로젝트 루트 `backend/main.py` (V2)
- **V1 코드**: 참조 및 히스토리 보존용으로만 보관
- V1 모듈은 app.services, app.models 등 삭제된 의존성이 있어 단독 실행 불가
