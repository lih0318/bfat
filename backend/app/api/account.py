"""
Account / Wallet API. Returns Binance Futures balance and account info.
"""
from fastapi import APIRouter, HTTPException

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
        return data
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
