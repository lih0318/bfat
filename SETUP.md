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

## 3. Backend Environment

Create `backend/.env`:

```
BINANCE_API_KEY=your_api_key
BINANCE_API_SECRET=your_api_secret
BINANCE_TESTNET=true
BFAT_SYMBOL=BTCUSDT
```

For production, set `BINANCE_TESTNET=false`.

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
