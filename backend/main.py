"""
BFAT unified deployment entrypoint.
FastAPI app with API routes and WebSocket.
"""
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.config.settings import Settings
from app.core.database import DatabaseFactory

from api.auth import decode_token
from api.auth_routes import router as auth_router
from api.engine_routes import router as engine_router
from api.log_routes import router as log_router
from api.status_routes import router as status_router
from services.engine_service import EngineService


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create DB, init tables, create engine service. Close on shutdown."""
    settings = Settings()
    app.state.settings = settings
    db = DatabaseFactory(settings)
    db.init_tables()
    app.state.db = db
    engine_service = EngineService(settings, db)
    app.state.engine_service = engine_service

    async def _equity_refresh_loop() -> None:
        """Refresh equity every 10s when engine stopped, so Dashboard stays updated."""
        while True:
            if not engine_service._running:
                await asyncio.get_running_loop().run_in_executor(
                    None, engine_service.refresh_equity
                )
            await asyncio.sleep(10)

    _equity_task = asyncio.create_task(_equity_refresh_loop())
    try:
        engine_service.refresh_equity()
    except Exception:
        pass

    yield
    _equity_task.cancel()
    try:
        await _equity_task
    except asyncio.CancelledError:
        pass
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
app.include_router(engine_router)
app.include_router(status_router)
app.include_router(log_router)


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


@app.websocket("/ws/status")
async def ws_status(websocket: WebSocket):
    """Push engine status periodically. Requires token query param."""
    token = websocket.query_params.get("token")
    settings = websocket.app.state.settings
    if not token:
        await websocket.close(code=4001)
        return
    payload = decode_token(token, settings.jwt_secret)
    if not payload or payload.get("type") != "access":
        await websocket.close(code=4001)
        return
    await websocket.accept()
    interval = 1.0
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
