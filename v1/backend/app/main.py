"""
BFAT v2 - FastAPI backend (minimal entrypoint).
Add routes and services here as you build v2.
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.database import DatabaseFactory


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create tables on startup, close DB on shutdown."""
    db = DatabaseFactory()
    db.init_tables()
    app.state.db = db
    yield
    db.close()


app = FastAPI(
    title="BFAT v2",
    description="Binance Futures Auto Trader v2",
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


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "2.0.0"}


# Register API routes when implemented
# from app.api.routes import register_routes
# register_routes(app)
