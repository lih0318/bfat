"""Status and position routes."""

from fastapi import APIRouter, Request

router = APIRouter(prefix="/api", tags=["status"])


@router.get("/status")
async def get_status(request: Request):
    """Return engine status (state, position, equity, kill switch)."""
    svc = request.app.state.engine_service
    if svc is None:
        return {
            "engine_state": "stopped",
            "position": None,
            "last_signal": None,
            "current_stop_price": None,
            "equity": 0.0,
            "kill_switch_triggered": False,
            "error": "Engine service not configured",
        }
    return svc.get_status()


@router.get("/position")
async def get_position(request: Request):
    """Return current position or null."""
    svc = request.app.state.engine_service
    if svc is None:
        return None
    st = svc.get_status()
    return st.get("position")
