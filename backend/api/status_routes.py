"""Status and position routes."""

from fastapi import APIRouter, Depends, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["status"])


def _default_status():
    return {
        "engine_state": "stopped",
        "strategy_mode": "TRENDING",
        "symbols": ["BTCUSDT"],
        "max_concurrent_positions": 1,
        "open_position_count": 0,
        "positions": [],
        "symbol_statuses": [],
        "position": None,
        "last_signal": None,
        "current_stop_price": None,
        "equity": 0.0,
        "kill_switch_triggered": False,
        "error": "Engine service not configured",
        "diagnostics": {
            "market_stream": None,
            "user_stream": None,
            "last_insight_update_ts": None,
            "insight_age_seconds": None,
            "insight_stale": False,
        },
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
    symbol: str | None = None,
    limit: int = 50,
    offset: int = 0,
):
    """Return closed trades with computed display fields from DB only."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_trades"):
        return []
    try:
        return svc.get_trades(symbol=symbol, limit=limit, offset=offset)
    except Exception:
        return []


@router.get("/trades/summary")
async def get_trade_summary(
    request: Request,
    _: str = Depends(get_current_user),
    symbol: str | None = None,
):
    """Return performance summary metrics computed from closed trades."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_trade_summary"):
        return {
            "total_trades": 0, "win_rate": 0.0, "average_r": 0.0,
            "expectancy_r": 0.0, "total_net_pnl": 0.0,
            "max_drawdown_r": 0.0, "best_trade_r": 0.0, "worst_trade_r": 0.0,
        }
    try:
        return svc.get_trade_summary(symbol=symbol)
    except Exception:
        return {
            "total_trades": 0, "win_rate": 0.0, "average_r": 0.0,
            "expectancy_r": 0.0, "total_net_pnl": 0.0,
            "max_drawdown_r": 0.0, "best_trade_r": 0.0, "worst_trade_r": 0.0,
        }


def _default_insight():
    return {
        "regime": "Unknown",
        "strategy_mode": "TRENDING",
        "volatility_score": 0.0,
        "atr_value": 0.0,
        "volume_ratio": 0.0,
        "ema_fast": 0.0,
        "ema_slow": 0.0,
        "engine_reasoning": ["Engine service not configured"],
    }


@router.get("/insight")
async def get_insight(
    request: Request,
    _: str = Depends(get_current_user),
    symbol: str | None = None,
):
    """Return market regime and engine reasoning from last strategy evaluation."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is None or not hasattr(svc, "get_insight"):
        return _default_insight()
    try:
        return svc.get_insight(symbol=symbol)
    except Exception:
        return _default_insight()
