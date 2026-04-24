"""
BFAT unified deployment entrypoint.
FastAPI app with API routes and WebSocket.
"""
import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import Settings, _ENV_PATH
from app.core.database import DatabaseFactory

from api.account_routes import router as account_router
from api.auth import decode_token
from api.auth_routes import router as auth_router
from api.engine_routes import router as engine_router
from api.log_routes import router as log_router
from api.status_routes import router as status_router
from app.services.binance_account import BinanceAccountClient
from services.engine_service import EngineService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB, init tables, create engine service. Close on shutdown."""
    settings = Settings()
    app.state.settings = settings
    # Log env loading for debugging equity/API key issues
    logger.info("Settings env_file: %s (exists=%s)", _ENV_PATH, _ENV_PATH.exists())
    if not (settings.binance_api_key and settings.binance_api_secret):
        logger.warning(
            "BINANCE_API_KEY or BINANCE_API_SECRET empty. Set them in %s or in environment for equity/orders.",
            _ENV_PATH,
        )
    db = DatabaseFactory(settings)
    db.init_tables()
    app.state.db = db

    binance_account = BinanceAccountClient(
        api_key=settings.binance_api_key,
        api_secret=settings.binance_api_secret,
        testnet=settings.binance_testnet,
    )
    app.state.binance_account_client = binance_account
    if binance_account.is_configured():
        logger.info("Binance account client configured (testnet=%s)", settings.binance_testnet)
    else:
        logger.warning("Binance API keys not set - equity/futures APIs will return 503")

    engine_service = EngineService(settings, db, binance_account)
    app.state.engine_service = engine_service

    yield
    await engine_service.stop()
    db.close()


app = FastAPI(
    title="BFAT",
    description="Bitcoin Futures Auto Trader",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(account_router)
app.include_router(engine_router)
app.include_router(status_router)
app.include_router(log_router)


@app.get("/api/health")
def health():
    svc = getattr(app.state, "engine_service", None)
    stream_ok = True
    if svc is not None:
        diag = svc._build_stream_diagnostics() if hasattr(svc, "_build_stream_diagnostics") else {}
        ms = diag.get("market_stream")
        if ms and not ms.get("connected"):
            stream_ok = False
        if diag.get("insight_stale"):
            stream_ok = False
    return {"status": "ok", "version": "2.0.0", "stream_ok": stream_ok}


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """Push engine status periodically. Requires token query param."""
    token = websocket.query_params.get("token")
    settings = websocket.app.state.settings
    if not token:
        await websocket.close(code=4001, reason="missing token")
        return
    payload = decode_token(token, settings.jwt_secret)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4003, reason="invalid or expired token")
        return
    await websocket.accept()
    interval = 3.0
    try:
        while True:
            svc = websocket.app.state.engine_service
            data = svc.get_status() if svc else {"engine_state": "stopped", "error": "not configured"}
            await websocket.send_json(data)
            await asyncio.sleep(interval)
    except Exception:
        pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
