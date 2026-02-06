# Binance Futures Auto Trader

Binance USDT-M Futures용 자동매매 앱. 지갑 현황, 차트, 포지션, Autopilot(Confluence+ATR 전략)을 제공합니다.

## 구조

- **backend** (Python + FastAPI): Binance 공식 `binance-futures-connector` 사용, REST API 제공.
- **frontend** (React + TypeScript + Vite): Wallet / Charts / Positions / Autopilot 탭, Lightweight Charts 차트.

## 개발 환경 실행

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
# .env 파일에 BINANCE_API_KEY, BINANCE_API_SECRET, FAPI_BASE_URL(테스트넷 권장) 설정
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

브라우저에서 `http://localhost:5173` 접속. API는 Vite 프록시로 `http://127.0.0.1:8000`으로 전달됩니다.

## Windows Standalone 앱으로 패키징 시 고려 사항

- **설정 경로**: Backend는 `CONFIG_DIR` 환경변수 또는 미설정 시 `%APPDATA%\BinanceFuturesAutoTrader`(Windows)를 사용. 패키징 시 이 경로에 `autopilot.json`이 저장되도록 하면 사용자 설정이 유지됩니다.
- **API Base URL**: Frontend는 `VITE_API_BASE_URL`로 백엔드 주소를 지정. 개발 시에는 비워두면 프록시(`/api`)를 쓰고, Standalone 빌드 시에는 예: `http://127.0.0.1:8000`으로 빌드하면 Electron/Tauri에서 로컬 백엔드와 통신할 수 있습니다.
- **CORS**: 백엔드 `main.py`에 필요한 Origin을 추가해 Standalone 앱 Origin을 허용하면 됩니다.
- **실행 순서**: Standalone 앱에서는 백엔드를 먼저 실행(예: uvicorn 또는 내장 서버)한 뒤 프론트엔드를 띄우는 방식으로 구성하는 것이 좋습니다.

## 환경 변수 (Backend)

| 변수 | 설명 |
|------|------|
| `BINANCE_API_KEY` | Binance API Key |
| `BINANCE_API_SECRET` | Binance API Secret |
| `FAPI_BASE_URL` | Production: `https://fapi.binance.com`, Testnet: `https://testnet.binancefuture.com` |
| `CONFIG_DIR` | (선택) 설정 디렉터리. 비우면 APPDATA 또는 `./config` 사용 |

## 기능 요약

- **Wallet**: Futures 잔고, 사용 가능 잔고, 미실현 PnL, 마진 잔고.
- **Charts**: 심볼/인터벌 선택, 캔들 차트 (Lightweight Charts), 확대/축소.
- **Positions**: 열린 포지션, PnL 옆에 SL/TP 가격 표시.
- **Autopilot**: 최대 USDT/레버리지 설정, Confluence+ATR 전략, 페이퍼 모드, 일일 손실 한도, 활동 로그. SL/TP는 전략(ATR 배수)으로만 계산되며 사용자 개입 없음.

Testnet에서 충분히 검증한 뒤 실거래를 사용하세요.
