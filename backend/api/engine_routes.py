"""Engine control routes: start, stop."""

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["engine"])


@router.post("/start")
async def start_engine(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Start BFAT engine in background."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "start"):
        raise HTTPException(status_code=503, detail="Engine service not configured")
    try:
        await svc.start()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stop")
async def stop_engine(
    request: Request,
    _: str = Depends(get_current_user),
):
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "stop"):
        raise HTTPException(status_code=503, detail="Engine service not configured")
    try:
        await svc.stop()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
