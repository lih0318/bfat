"""Account / Wallet API. Returns Binance Futures balance and account info. Same as v1."""
from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["account"])


@router.get("/equity")
async def get_equity(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Return equity. Forces refresh if cache is 0. Use as fallback when WebSocket shows 0."""
    svc = request.app.state.engine_service
    if svc is None:
        return {"equity": 0.0}
    if svc._equity_cache <= 0:
        svc.refresh_equity()
    return {"equity": svc._equity_cache}


@router.get("/account/balance")
async def get_balance(
    request: Request,
    _: str = Depends(get_current_user),
):
    if not request.app.state.binance_account_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        return request.app.state.binance_account_client.balance()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/account/account")
async def get_account(
    request: Request,
    _: str = Depends(get_current_user),
):
    if not request.app.state.binance_account_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        return request.app.state.binance_account_client.account()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
