"""Account / Wallet API. Returns Binance Futures balance and account info. Same as v1."""
from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user

router = APIRouter(prefix="/api", tags=["account"])


def _extract_equity(acct: dict) -> float:
    """Extract equity from Binance account response."""
    for k in ("totalMarginBalance", "totalWalletBalance"):
        v = acct.get(k)
        if v is not None:
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
    for a in acct.get("assets") or []:
        if str(a.get("asset", "")).upper() == "USDT":
            for k in ("marginBalance", "walletBalance"):
                v = a.get(k)
                if v is not None:
                    try:
                        f = float(v)
                        if f > 0:
                            return f
                    except (TypeError, ValueError):
                        pass
    return 0.0


@router.get("/equity")
async def get_equity(
    request: Request,
    _: str = Depends(get_current_user),
):
    """Return equity. Uses cache or fetches from Binance if 0."""
    svc = getattr(request.app.state, "engine_service", None)
    if svc is not None:
        cache = getattr(svc, "_equity_cache", 0.0)
        if cache > 0:
            return {"equity": cache}
        if hasattr(svc, "refresh_equity"):
            try:
                svc.refresh_equity()
                cache = getattr(svc, "_equity_cache", 0.0)
                if cache > 0:
                    return {"equity": cache}
            except Exception:
                pass
    bac = getattr(request.app.state, "binance_account_client", None)
    if bac is not None and bac.is_configured():
        try:
            acct = bac.account()
            return {"equity": _extract_equity(acct)}
        except Exception:
            pass
    return {"equity": getattr(svc, "_equity_cache", 0.0) if svc else 0.0}


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
