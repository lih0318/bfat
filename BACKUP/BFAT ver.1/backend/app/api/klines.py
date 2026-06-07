"""
Klines / Candlestick API. Proxies Binance Futures klines for charts.
"""
from fastapi import APIRouter, HTTPException, Query

from app.services.binance_client import binance_client

router = APIRouter()

VALID_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "3d", "1w"}


@router.get("")
def get_klines(
    symbol: str = Query(..., description="e.g. BTCUSDT"),
    interval: str = Query("15m", description="1m, 5m, 15m, 1h, 4h, 1d, etc."),
    limit: int = Query(500, ge=1, le=1500),
    start_time: int | None = Query(None),
    end_time: int | None = Query(None),
):
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    if interval not in VALID_INTERVALS:
        raise HTTPException(status_code=400, detail=f"Invalid interval. Use one of: {VALID_INTERVALS}")
    try:
        raw = binance_client.klines(
            symbol=symbol.upper(),
            interval=interval,
            limit=limit,
            start_time=start_time,
            end_time=end_time,
        )
        # Binance kline: [open_time, open, high, low, close, volume, ...]
        # Return as list of objects for frontend (Lightweight Charts: time in seconds)
        result = []
        for k in raw:
            result.append({
                "time": k[0] // 1000,
                "open": float(k[1]),
                "high": float(k[2]),
                "low": float(k[3]),
                "close": float(k[4]),
                "volume": float(k[5]),
            })
        return result
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
