"""
Account / Wallet API. Returns Binance Futures balance and account info.
"""
from fastapi import APIRouter, HTTPException, Query

from app.services.balance_history import get_history as get_balance_history, record_snapshot as record_balance_snapshot
from app.services.binance_client import binance_client

router = APIRouter()


@router.get("/balance")
def get_balance():
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        data = binance_client.balance()
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/account")
def get_account():
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        data = binance_client.account()
        try:
            total = float(data.get("totalMarginBalance") or 0)
            record_balance_snapshot(total)
        except Exception:
            pass
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/balance-history")
def balance_history(period: str = Query("1w", description="1d = last 24h, 1w = last 7 days")):
    """Return balance snapshots for Wallet chart. Max 30 days stored server-side."""
    if period == "1d":
        hours = 24
    elif period == "1w":
        hours = 24 * 7
    else:
        hours = 24 * 7
    points = get_balance_history(hours)
    return {"points": points}
