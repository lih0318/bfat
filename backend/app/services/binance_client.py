"""
Binance USDT-M Futures client wrapper using official binance-futures-connector.
All Binance calls go through this layer for base_url and error handling.
SL/TP use Algo Order API (POST /fapi/v1/algoOrder) due to -4120 on regular order endpoint.
"""
import hashlib
import hmac
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from binance.um_futures import UMFutures
from binance.error import ClientError

from app.core.config import settings


def _get_ws_base_url() -> str:
    if settings.ws_base_url:
        return settings.ws_base_url
    if "testnet" in settings.fapi_base_url.lower():
        return "wss://stream.binancefuture.com"
    return "wss://fstream.binance.com"


class BinanceFuturesClient:
    """Thin wrapper over UMFutures for fapi.binance.com / testnet."""

    def __init__(self) -> None:
        self._client: Optional[UMFutures] = None

    @property
    def client(self) -> UMFutures:
        if self._client is None:
            self._client = UMFutures(
                key=settings.binance_api_key,
                secret=settings.binance_api_secret,
                base_url=settings.fapi_base_url,
            )
        return self._client

    @property
    def ws_base_url(self) -> str:
        return _get_ws_base_url()

    def is_configured(self) -> bool:
        return bool(settings.binance_api_key and settings.binance_api_secret)

    # --- Account ---
    def balance(self, recv_window: int = 6000) -> list[dict[str, Any]]:
        return self.client.balance(recvWindow=recv_window)

    def account(self, recv_window: int = 6000) -> dict[str, Any]:
        return self.client.account(recvWindow=recv_window)

    def income_history(
        self,
        symbol: Optional[str] = None,
        income_type: Optional[str] = None,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        recv_window: int = 6000,
    ) -> list[dict[str, Any]]:
        """Get income history (e.g. REALIZED_PNL). Returns newest first."""
        params: dict[str, Any] = {"limit": limit, "recvWindow": recv_window}
        if symbol is not None:
            params["symbol"] = symbol
        if income_type is not None:
            params["incomeType"] = income_type
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = self._signed_request("GET", "/fapi/v1/income", params)
        return data if isinstance(data, list) else []

    # --- Market ---
    def klines(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> list[Any]:
        params: dict[str, Any] = {"symbol": symbol, "interval": interval, "limit": limit}
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        return self.client.klines(**params)

    def exchange_info(self) -> dict[str, Any]:
        return self.client.exchange_info()

    def premium_index(self, symbol: Optional[str] = None) -> Any:
        if symbol:
            return self.client.premium_index(symbol=symbol)
        return self.client.premium_index()

    def funding_rate(self, symbol: str, limit: int = 1) -> list[dict[str, Any]]:
        return self.client.funding_rate(symbol=symbol, limit=limit)

    # --- Positions / Orders ---
    def position_information(self, symbol: Optional[str] = None, recv_window: int = 6000) -> list[dict[str, Any]]:
        if symbol:
            return self.client.get_position_risk(symbol=symbol, recvWindow=recv_window)
        return self.client.get_position_risk(recvWindow=recv_window)

    def get_open_orders(self, symbol: str, recv_window: int = 6000) -> list[dict[str, Any]]:
        return self.client.get_open_orders(symbol=symbol, recvWindow=recv_window)

    def cancel_all_open_orders(self, symbol: str, recv_window: int = 6000) -> dict[str, Any]:
        """Cancel all open orders for the symbol (e.g. before flip to clear old SL/TP)."""
        return self.client.cancel_open_orders(symbol=symbol, recvWindow=recv_window)

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        """Send signed request to Binance FAPI (for algo endpoints not in SDK)."""
        params = dict(params)
        params.setdefault("recvWindow", 6000)
        params.setdefault("timestamp", int(time.time() * 1000))
        query = urlencode(params)
        sig = hmac.new(
            settings.binance_api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        url = f"{settings.fapi_base_url.rstrip('/')}{path}?{query}&signature={sig}"
        with httpx.Client(timeout=10.0) as client:
            r = client.request(method, url, headers={"X-MBX-APIKEY": settings.binance_api_key})
        r.raise_for_status()
        return r.json()

    def get_open_algo_orders(self, symbol: str, recv_window: int = 6000) -> list[dict[str, Any]]:
        """Get open algo orders for symbol (SL/TP are algo orders)."""
        data = self._signed_request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol, "recvWindow": recv_window})
        return data if isinstance(data, list) else []

    def cancel_algo_order(
        self,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
        recv_window: int = 6000,
    ) -> dict[str, Any]:
        """Cancel one algo order by algoId or clientAlgoId."""
        params: dict[str, Any] = {"recvWindow": recv_window}
        if algo_id is not None:
            params["algoId"] = algo_id
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        return self._signed_request("DELETE", "/fapi/v1/algoOrder", params)

    def cancel_all_algo_orders(self, symbol: str, recv_window: int = 6000) -> None:
        """Cancel all open algo orders for the symbol (e.g. old SL/TP before flip)."""
        for order in self.get_open_algo_orders(symbol, recv_window):
            try:
                if order.get("clientAlgoId"):
                    self.cancel_algo_order(client_algo_id=order["clientAlgoId"], recv_window=recv_window)
                elif order.get("algoId") is not None:
                    self.cancel_algo_order(algo_id=order["algoId"], recv_window=recv_window)
            except Exception:
                pass

    def new_algo_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        trigger_price: float,
        quantity: Optional[float] = None,
        reduce_only: bool = True,
        client_algo_id: Optional[str] = None,
        working_type: str = "CONTRACT_PRICE",
        recv_window: int = 6000,
    ) -> dict[str, Any]:
        """
        Place STOP_MARKET or TAKE_PROFIT_MARKET via Algo Order API (required since -4120).
        algoType=CONDITIONAL, triggerPrice required.
        """
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": trigger_price,
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": working_type,
            "recvWindow": recv_window,
            "timestamp": int(time.time() * 1000),
        }
        if quantity is not None:
            params["quantity"] = quantity
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        return self._signed_request("POST", "/fapi/v1/algoOrder", params)

    def new_order(
        self,
        symbol: str,
        side: str,
        order_type: str,
        quantity: Optional[float] = None,
        quote_order_qty: Optional[float] = None,
        price: Optional[float] = None,
        stop_price: Optional[float] = None,
        close_position: Optional[bool] = None,
        reduce_only: Optional[bool] = None,
        time_in_force: Optional[str] = None,
        new_client_order_id: Optional[str] = None,
        recv_window: int = 6000,
        **kwargs: Any,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "recvWindow": recv_window,
        }
        if quantity is not None:
            params["quantity"] = quantity
        if quote_order_qty is not None:
            params["quoteOrderQty"] = quote_order_qty
        if price is not None:
            params["price"] = price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if close_position is not None:
            params["closePosition"] = close_position
        if reduce_only is not None:
            params["reduceOnly"] = reduce_only
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if new_client_order_id is not None:
            params["newClientOrderId"] = new_client_order_id
        params.update(kwargs)
        return self.client.new_order(**params)

    def set_leverage(self, symbol: str, leverage: int, recv_window: int = 6000) -> dict[str, Any]:
        return self.client.change_leverage(symbol=symbol, leverage=leverage, recvWindow=recv_window)


binance_client = BinanceFuturesClient()
