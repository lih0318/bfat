"""Account / Wallet API. Returns Binance Futures balance and account info. Same as v1."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from api.deps import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["account"])


def _extract_equity(acct: dict) -> float:
    """Extract equity from Binance account response. Handles camelCase, snake_case, and string values."""
    # Top-level: camelCase (API) or snake_case (some SDKs)
    for k in (
        "totalMarginBalance",
        "totalWalletBalance",
        "total_margin_balance",
        "total_wallet_balance",
    ):
        v = acct.get(k)
        if v is not None and v != "":
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                pass
    # Fallback: assets[USDT]
    for a in acct.get("assets") or []:
        if str(a.get("asset", "")).upper() == "USDT":
            for k in ("marginBalance", "walletBalance", "margin_balance", "wallet_balance"):
                v = a.get(k)
                if v is not None and v != "":
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
    """Return equity. When Binance is configured, always fetch fresh from API first; otherwise use cache."""
    svc = getattr(request.app.state, "engine_service", None)
    bac = getattr(request.app.state, "binance_account_client", None)

    # Prefer direct Binance fetch when configured so equity is never stale
    if bac is not None and bac.is_configured():
        try:
            acct = bac.account()
            eq = _extract_equity(acct)
            if eq > 0:
                logger.info("Equity from /api/equity: %.2f USDT", eq)
            if svc is not None:
                svc._equity_cache = eq
            return {"equity": eq}
        except Exception as e:
            logger.warning("Equity fetch from Binance failed: %s", e)
            # Fall through to cache

    # Use engine_service cache (updated by refresh_equity or previous fetch)
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
            except Exception as e:
                logger.warning("Equity refresh failed: %s", e)
    return {"equity": getattr(svc, "_equity_cache", 0.0) if svc else 0.0}


@router.get("/account/balance")
async def get_balance(
    request: Request,
    _: str = Depends(get_current_user),
):
    bac = getattr(request.app.state, "binance_account_client", None)
    if bac is None or not bac.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        return bac.balance()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/account/account")
async def get_account(
    request: Request,
    _: str = Depends(get_current_user),
):
    bac = getattr(request.app.state, "binance_account_client", None)
    if bac is None or not bac.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        return bac.account()
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
