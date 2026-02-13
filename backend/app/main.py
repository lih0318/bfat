"""
Binance Futures Auto Trader - FastAPI backend.
CORS and API prefix set for frontend (dev and Windows Standalone).
Auth middleware protects all /api/* endpoints except /api/auth/login and /api/health.
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api import account, klines, positions, autopilot, journal, auth, engine_api
from app.core.config import settings

app = FastAPI(
    title="Binance Futures Auto Trader",
    description="USDT-M Futures wallet, charts, positions, and Rich Man.",
    version="0.1.0",
)

# ── CORS ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Widened for remote access; auth token protects the API
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth middleware ──
# Paths that don't require authentication
_PUBLIC_PATHS = {"/api/health", "/api/auth/login", "/api/auth/check"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Skip auth for public paths and OPTIONS (CORS preflight)
        if request.method == "OPTIONS" or path in _PUBLIC_PATHS:
            return await call_next(request)
        # Skip auth if credentials not configured (development mode)
        if not settings.app_username or not settings.app_password:
            return await call_next(request)
        # Only protect /api/* paths
        if not path.startswith("/api/"):
            return await call_next(request)
        # Check Authorization header
        authorization = request.headers.get("Authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:]
            if auth.validate_token(token):
                return await call_next(request)
        return JSONResponse(status_code=401, content={"detail": "Not authenticated"})


app.add_middleware(AuthMiddleware)

# ── Routers ──
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(account.router, prefix="/api/account", tags=["account"])
app.include_router(klines.router, prefix="/api/klines", tags=["klines"])
app.include_router(positions.router, prefix="/api/positions", tags=["positions"])
# New TSMOM engine API (replaces old autopilot)
app.include_router(engine_api.router, prefix="/api/autopilot", tags=["engine"])
# Keep old autopilot router at /api/autopilot-legacy for reference (optional)
app.include_router(autopilot.router, prefix="/api/autopilot-legacy", tags=["autopilot-legacy"])
app.include_router(journal.router, prefix="/api/journal", tags=["journal"])


@app.get("/api/health")
def health():
    return {"status": "ok"}
