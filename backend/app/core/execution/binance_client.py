"""Binance Futures REST execution client."""

import hashlib
import hmac
import os
import time
import urllib.parse
from typing import Any

import requests

from app.domain.enums import Side


BINANCE_FUTURES_MAINNET = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"


def _generate_client_order_id(prefix: str) -> str:
    """Generate unique client order ID (timestamp + random suffix)."""
    ts = int(time.time() * 1000)
    suffix = hashlib.sha256(os.urandom(8)).hexdigest()[:6]
    return f"{prefix}_{ts}_{suffix}"


def _side_to_binance(side: Side, for_reduce_only: bool = False) -> str:
    """Convert domain Side to Binance side. For reduceOnly, LONG->SELL, SHORT->BUY."""
    if for_reduce_only:
        return "SELL" if side == Side.LONG else "BUY"
    return "BUY" if side == Side.LONG else "SELL"


class BinanceExecutionClient:
    """REST-based Binance USDT-M Futures order client."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str | None = None,
        testnet: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = base_url or (
            BINANCE_FUTURES_TESTNET if testnet else BINANCE_FUTURES_MAINNET
        )

    def _sign(self, params: dict[str, Any]) -> str:
        """Sign params with HMAC-SHA256. Converts bool to Binance string format."""
        def _serialize(v: Any) -> Any:
            if v is True:
                return "true"
            if v is False:
                return "false"
            return v
        serializable = {k: _serialize(v) for k, v in params.items()}
        query = urllib.parse.urlencode(serializable)
        signature = hmac.new(
            self._api_secret.encode("utf-8"),
            query.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return query + "&signature=" + signature

    def _request(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict:
        """Execute signed request. Raises RuntimeError on HTTP failure."""
        params = dict(params) if params else {}
        params["timestamp"] = int(time.time() * 1000)
        params["recvWindow"] = 5000
        signed = self._sign(params)
        url = f"{self._base_url}{path}?{signed}"
        headers = {"X-MBX-APIKEY": self._api_key}
        resp = requests.request(method, url, headers=headers)
        if not (200 <= resp.status_code < 300):
            raise RuntimeError(
                f"Binance API error: {method} {path} status={resp.status_code} body={resp.text}"
            )
        return resp.json()

    def _post_order(self, params: dict[str, Any]) -> dict:
        """POST /fapi/v1/order."""
        return self._request("POST", "/fapi/v1/order", params)

    def place_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        client_order_id: str,
    ) -> dict:
        """Place MARKET order. Returns raw response dict."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        params = {
            "symbol": symbol,
            "side": _side_to_binance(side, for_reduce_only=False),
            "type": "MARKET",
            "quantity": quantity,
            "newClientOrderId": client_order_id,
        }
        return self._post_order(params)

    def place_stop_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        stop_price: float,
        client_order_id: str,
    ) -> dict:
        """Place STOP_MARKET reduceOnly order. Returns raw response dict."""
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if stop_price <= 0:
            raise ValueError("stop_price must be positive")
        params = {
            "symbol": symbol,
            "side": _side_to_binance(side, for_reduce_only=True),
            "type": "STOP_MARKET",
            "quantity": quantity,
            "stopPrice": stop_price,
            "reduceOnly": True,
            "closePosition": False,
            "workingType": "CONTRACT_PRICE",
            "newClientOrderId": client_order_id,
        }
        return self._post_order(params)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel order. Returns raw response dict."""
        params = {
            "symbol": symbol,
            "orderId": order_id,
        }
        return self._request("DELETE", "/fapi/v1/order", params)

    def get_position(self, symbol: str) -> dict:
        """Fetch position info for symbol. Returns raw position dict or {} if none."""
        data = self._request("GET", "/fapi/v2/positionRisk", {})
        if not isinstance(data, list):
            raise RuntimeError(
                f"Unexpected get_position response type: expected list, got {type(data).__name__}"
            )
        for p in data:
            if isinstance(p, dict) and p.get("symbol") == symbol:
                return p
        return {}
