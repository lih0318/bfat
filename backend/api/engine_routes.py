"""Engine control routes: start, stop."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["engine"])


@router.post("/start")
async def start_engine(request: Request):
    """Start BFAT engine in background."""
    svc = request.app.state.engine_service
    if svc is None:
        return {"ok": False, "error": "Engine service not configured"}
    await svc.start()
    return {"ok": True}


@router.post("/stop")
async def stop_engine(request: Request):
    """Stop BFAT engine."""
    svc = request.app.state.engine_service
    if svc is None:
        return {"ok": False, "error": "Engine service not configured"}
    await svc.stop()
    return {"ok": True}
