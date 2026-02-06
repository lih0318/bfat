"""
Binance Futures Auto Trader - FastAPI backend.
CORS and API prefix set for frontend (dev and Windows Standalone).
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import account, klines, positions, autopilot, journal
from app.core.config import settings

app = FastAPI(
    title="Binance Futures Auto Trader",
    description="USDT-M Futures wallet, charts, positions, and Rich Man.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(account.router, prefix="/api/account", tags=["account"])
app.include_router(klines.router, prefix="/api/klines", tags=["klines"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
app.include_router(autopilot.router, prefix="/api/autopilot", tags=["autopilot"])
app.include_router(journal.router, prefix="/api/journal", tags=["journal"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
