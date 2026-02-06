"""
Positions API. Returns open positions and open orders (SL/TP) from Binance Futures.
Supports closing a position at market (Live only).
"""
from fastapi import APIRouter, HTTPException, Query

from app.services.binance_client import binance_client
from app.services.exchange_info import ExchangeInfoCache

router = APIRouter()


def _normalize_position(p: dict) -> dict:
    """Ensure camelCase keys for frontend; Binance API uses camelCase, some SDKs may use snake_case.
    V3 positionRisk does not return leverage; compute it from notional/initialMargin when missing."""
    out = dict(p)
    if out.get("positionAmt") is None and out.get("position_amt") is not None:
        out["positionAmt"] = out["position_amt"]
    if out.get("entryPrice") is None and out.get("entry_price") is not None:
        out["entryPrice"] = out["entry_price"]
    if out.get("markPrice") is None and out.get("mark_price") is not None:
        out["markPrice"] = out["mark_price"]
    if out.get("unRealizedProfit") is None and out.get("un_realized_profit") is not None:
        out["unRealizedProfit"] = out["un_realized_profit"]
    # Leverage: V3 positionRisk does not return it; compute from notional/initialMargin
    if not out.get("leverage"):
        notional = out.get("notional")
        init_margin = out.get("initialMargin") or out.get("initial_margin")
        if notional is not None and init_margin is not None:
            try:
                n, m = float(notional), float(init_margin)
                if m and m > 0:
                    out["leverage"] = str(round(abs(n) / m))
            except (TypeError, ValueError):
                pass
    return out


@router.get("")
def get_positions(symbol: str | None = Query(None, description="Filter by symbol e.g. BTCUSDT")):
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        positions_raw = binance_client.position_information(symbol=symbol.upper() if symbol else None)
        # Only positions with non-zero positionAmt
        positions = []
        for p in positions_raw:
            amt = p.get("positionAmt") or p.get("position_amt") or 0
            if float(amt) != 0:
                positions.append(_normalize_position(p))
        return positions
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


def _normalize_order(o: dict) -> dict:
    """Ensure camelCase stopPrice/type for frontend; Binance API uses camelCase, some SDKs may use snake_case."""
    out = dict(o)
    if out.get("stopPrice") is None and out.get("stop_price") is not None:
        out["stopPrice"] = out["stop_price"]
    if out.get("type") is None and out.get("origType") is not None:
        out["type"] = out["origType"]
    return out


@router.get("/open-orders")
def get_open_orders(symbol: str = Query(..., description="e.g. BTCUSDT")):
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    try:
        orders = binance_client.get_open_orders(symbol=symbol.upper())
        return [_normalize_order(o) if isinstance(o, dict) else o for o in orders]
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/close")
def close_position(symbol: str = Query(..., description="e.g. BTCUSDT")):
    """Close position at market price (Live only). Places reduce-only market order."""
    if not binance_client.is_configured():
        raise HTTPException(status_code=503, detail="Binance API not configured")
    symbol = symbol.upper()
    try:
        positions = binance_client.position_information(symbol=symbol)
        pos = None
        for p in positions:
            amt = float(p.get("positionAmt", 0))
            if amt != 0:
                pos = p
                break
        if not pos:
            raise HTTPException(status_code=404, detail=f"No open position for {symbol}")
        amt = float(pos.get("positionAmt", 0))
        qty = abs(amt)
        side = "SELL" if amt > 0 else "BUY"
        qty = ExchangeInfoCache.round_quantity(symbol, qty)
        if qty <= 0:
            raise HTTPException(status_code=400, detail="Invalid quantity")
        binance_client.new_order(
            symbol=symbol,
            side=side,
            order_type="MARKET",
            quantity=qty,
            reduce_only=True,
        )
        return {"ok": True, "message": f"Close order sent for {symbol}"}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))
