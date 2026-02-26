# BFAT Setup Instructions

## Prerequisites

- Ubuntu EC2 (or similar Linux)
- Python 3.10+
- Node.js 18+
- nginx

## 1. Install Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv nodejs npm nginx
```

## 2. Clone / Copy Project

Place the BFAT project on the server.

## 3. Backend Environment (API Key – Git에 커밋하지 않음)

`.env`는 `.gitignore`에 포함되어 있으므로 **절대 Git에 올라가지 않습니다**. 서버에서 직접 설정하세요.

### 방법 A: backend/.env 파일 생성 (권장)

```bash
cd /path/to/bfat/backend
cp .env.example .env
nano .env   # 또는 vim
```

다음 내용을 입력 후 저장:

```
BINANCE_API_KEY=실제_API_키
BINANCE_API_SECRET=실제_시크릿
BINANCE_TESTNET=true
BFAT_SYMBOL=BTCUSDT
```

프로덕션 시 `BINANCE_TESTNET=false` 로 변경.

### 방법 B: 환경 변수로 직접 전달

```bash
export BINANCE_API_KEY="실제_API_키"
export BINANCE_API_SECRET="실제_시크릿"
export BINANCE_TESTNET=true
./run_backend.sh
```

또는 한 줄:

```bash
BINANCE_API_KEY=xxx BINANCE_API_SECRET=yyy BINANCE_TESTNET=true uvicorn main:app --host 0.0.0.0 --port 8000
```

### 방법 C: systemd 서비스에서 환경 변수 사용

`/etc/systemd/system/bfat.service`:

```ini
[Unit]
Description=BFAT Backend
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/path/to/bfat/backend
Environment="BINANCE_API_KEY=실제_API_키"
Environment="BINANCE_API_SECRET=실제_시크릿"
Environment="BINANCE_TESTNET=false"
ExecStart=/path/to/bfat/backend/.venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

`systemctl daemon-reload && systemctl start bfat`

### 방법 D: 환경 변수 파일 (별도 보관)

```bash
# 서버에 /opt/bfat/secrets.env 생성 (권한 600)
echo 'BINANCE_API_KEY=xxx' > /opt/bfat/secrets.env
echo 'BINANCE_API_SECRET=yyy' >> /opt/bfat/secrets.env
chmod 600 /opt/bfat/secrets.env

# 실행 시 로드
set -a
source /opt/bfat/secrets.env
set +a
./run_backend.sh
```

## 4. Run Backend Manually

```bash
./run_backend.sh
```

Backend listens on `http://0.0.0.0:8000`.

## 5. Run Frontend Dev

```bash
cd frontend
npm install
npm run dev
```

## 6. Full Deployment

1. Edit `nginx-bfat.conf`: replace `YOUR_PUBLIC_IP` with your server IP or domain.
2. Copy config:
   ```bash
   sudo cp nginx-bfat.conf /etc/nginx/sites-available/bfat
   sudo ln -sf /etc/nginx/sites-available/bfat /etc/nginx/sites-enabled/
   sudo nginx -t && sudo systemctl reload nginx
   ```
3. Run full deploy:
   ```bash
   chmod +x deploy_all.sh
   ./deploy_all.sh
   ```

## 7. API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /api/health | Health check |
| GET | /api/status | Engine status |
| GET | /api/position | Current position |
| GET | /api/logs | System logs |
| POST | /api/start | Start engine |
| POST | /api/stop | Stop engine |
| WS | /ws/status | Live status stream |

## 8. Mobile Compatibility

- Viewport meta tag configured
- Tailwind responsive breakpoints (sm, md, lg)
- Touch-friendly buttons (min 44px)
