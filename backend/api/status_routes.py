"""Status and position routes."""

from fastapi import APIRouter, Depends, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["status"])


def _default_status():
    return {
        "engine_state": "stopped",
        "position": None,
        "last_signal": None,
        "current_stop_price": None,
        "equity": 0.0,
        "kill_switch_triggered": False,
        "error": "Engine service not configured",
    }


@router.get("/status")
async def get_status(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Return engine status (state, position, equity, kill switch)."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_status"):
        return _default_status()
    try:
        return svc.get_status()
    except Exception:
        return _default_status()


@router.get("/position")
async def get_position(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Return current position or null."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_status"):
        return None
    try:
        st = svc.get_status()
        return st.get("position") if isinstance(st, dict) else None
    except Exception:
        return None


@router.get("/trades")
async def get_trades(
    request: Request,
    _: str = Depends(get_current_user),
    limit: int = 20,
    offset: int = 0,
):
    """Return closed trades with stored r_multiple from DB only."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_trades"):
        return []
    try:
        symbol = getattr(request.app.state.settings, "bfat_symbol", "BTCUSDT")
        return svc.get_trades(symbol=symbol, limit=limit, offset=offset)
    except Exception:
        return []


def _default_insight():
    return {
        "regime": "Unknown",
        "volatility_score": 0.0,
        "bb_width_percentile": 0.0,
        "atr_value": 0.0,
        "volume_ratio": 0.0,
        "engine_reasoning": ["Engine service not configured"],
    }


@router.get("/insight")
async def get_insight(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Return market regime and engine reasoning from last strategy evaluation."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_insight"):
        return _default_insight()
    try:
        return svc.get_insight()
    except Exception:
        return _default_insight()
