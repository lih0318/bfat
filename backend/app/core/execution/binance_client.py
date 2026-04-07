"""Binance Futures REST execution client."""

import hashlib
import hmac
import logging
import math
import os
import time
import urllib.parse
from typing import Any

import requests

from app.domain.enums import Side

logger = logging.getLogger(__name__)

BINANCE_FUTURES_MAINNET = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET = "https://testnet.binancefuture.com"

_EXCHANGE_INFO_TTL = 3600  # re-fetch exchange info every 1 hour


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


def _step_to_precision(step: float) -> int:
    """Convert step size (e.g. 0.001) to decimal precision (3)."""
    if step <= 0 or step >= 1:
        return 0
    return max(0, int(round(-math.log10(step))))


def _floor_to_step(value: float, step: float, precision: int) -> float:
    """Floor value to the nearest step."""
    if step <= 0:
        return value
    return round(math.floor(value / step) * step, precision)


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
        self._symbol_filters: dict[str, dict[str, Any]] = {}
        self._exchange_info_ts: float = 0.0

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

    def _post_algo_order(self, params: dict[str, Any]) -> dict:
        """POST /fapi/v1/algoOrder (conditional STOP / TAKE_PROFIT, etc.)."""
        return self._request("POST", "/fapi/v1/algoOrder", params)

    @staticmethod
    def _normalize_algo_response(resp: dict) -> dict:
        """Expose algoId as orderId for callers expecting classic order shape."""
        if isinstance(resp, dict) and "algoId" in resp and "orderId" not in resp:
            return {**resp, "orderId": str(resp["algoId"])}
        return resp

    # ── Exchange info & precision ─────────────────────────────────

    def _load_exchange_info(self) -> None:
        """Fetch /fapi/v1/exchangeInfo and cache LOT_SIZE + PRICE_FILTER per symbol."""
        now = time.monotonic()
        if self._symbol_filters and now - self._exchange_info_ts < _EXCHANGE_INFO_TTL:
            return
        try:
            url = f"{self._base_url}/fapi/v1/exchangeInfo"
            resp = requests.get(url, timeout=10)
            if resp.status_code != 200:
                logger.warning("exchangeInfo fetch failed: %s", resp.status_code)
                return
            data = resp.json()
            for sym in data.get("symbols", []):
                name = sym.get("symbol")
                if not name:
                    continue
                filters: dict[str, Any] = {}
                for f in sym.get("filters", []):
                    ft = f.get("filterType")
                    if ft == "LOT_SIZE":
                        filters["qty_step"] = float(f.get("stepSize", 0))
                        filters["qty_min"] = float(f.get("minQty", 0))
                        filters["qty_precision"] = _step_to_precision(filters["qty_step"])
                    elif ft == "PRICE_FILTER":
                        filters["price_step"] = float(f.get("tickSize", 0))
                        filters["price_precision"] = _step_to_precision(filters["price_step"])
                if filters:
                    self._symbol_filters[name] = filters
            self._exchange_info_ts = now
            logger.info("exchangeInfo loaded: %d symbols", len(self._symbol_filters))
        except Exception as e:
            logger.warning("exchangeInfo load error: %s", e)

    def _get_filters(self, symbol: str) -> dict[str, Any]:
        self._load_exchange_info()
        return self._symbol_filters.get(symbol, {})

    def format_quantity(self, symbol: str, qty: float) -> float:
        """Floor quantity to LOT_SIZE step. Returns 0 if below minQty."""
        f = self._get_filters(symbol)
        step = f.get("qty_step", 0)
        precision = f.get("qty_precision", 3)
        min_qty = f.get("qty_min", 0)
        if step > 0:
            qty = _floor_to_step(qty, step, precision)
        else:
            qty = round(qty, 3)
        if qty < min_qty:
            return 0.0
        return qty

    def format_price(self, symbol: str, price: float, *, ceil: bool = False) -> float:
        """Round price to PRICE_FILTER tick size.

        By default floors (safe for SL — trigger slightly closer to entry).
        Pass ``ceil=True`` for TP triggers so the price is never rounded below
        the target (avoids 1-tick adverse rounding).
        """
        f = self._get_filters(symbol)
        step = f.get("price_step", 0)
        precision = f.get("price_precision", 2)
        if step > 0:
            if ceil:
                return round(math.ceil(price / step) * step, precision)
            return _floor_to_step(price, step, precision)
        return round(price, 2)

    # ── Order methods ─────────────────────────────────────────────

    def place_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        client_order_id: str,
    ) -> dict:
        """Place MARKET order. Quantity is formatted to LOT_SIZE."""
        quantity = self.format_quantity(symbol, quantity)
        if quantity <= 0:
            raise ValueError(f"quantity after LOT_SIZE formatting is 0 (below minQty for {symbol})")
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
        """Place STOP_MARKET via Algo API (closePosition; required on USDT-M futures).

        For LONG SL the trigger is *below* entry → floor (default).
        For SHORT SL the trigger is *above* entry → ceil so it triggers slightly
        tighter, not looser.
        """
        quantity = self.format_quantity(symbol, quantity)
        sl_ceil = side == Side.SHORT
        trigger_px = self.format_price(symbol, stop_price, ceil=sl_ceil)
        if quantity <= 0:
            raise ValueError(f"quantity after LOT_SIZE formatting is 0 (below minQty for {symbol})")
        if trigger_px <= 0:
            raise ValueError("stop_price must be positive")
        # closePosition closes full one-way position; no quantity/reduceOnly (Binance rules).
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": _side_to_binance(side, for_reduce_only=True),
            "type": "STOP_MARKET",
            "triggerPrice": trigger_px,
            "workingType": "CONTRACT_PRICE",
            "closePosition": True,
            "clientAlgoId": client_order_id[:36],
        }
        resp = self._post_algo_order(params)
        if not isinstance(resp, dict):
            raise RuntimeError(f"Unexpected algo order response: {type(resp).__name__}")
        return self._normalize_algo_response(resp)

    def place_take_profit_market_order(
        self,
        symbol: str,
        side: Side,
        quantity: float,
        take_profit_price: float,
        client_order_id: str,
    ) -> dict:
        """Place TAKE_PROFIT_MARKET via Algo API (closePosition).

        For LONG TP the trigger must be *above* entry → ceil rounding.
        For SHORT TP the trigger must be *below* entry → floor rounding.
        """
        quantity = self.format_quantity(symbol, quantity)
        tp_ceil = side == Side.LONG
        trigger_px = self.format_price(symbol, take_profit_price, ceil=tp_ceil)
        if quantity <= 0:
            raise ValueError(f"quantity after LOT_SIZE formatting is 0 (below minQty for {symbol})")
        if trigger_px <= 0:
            raise ValueError("take_profit_price must be positive")
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": _side_to_binance(side, for_reduce_only=True),
            "type": "TAKE_PROFIT_MARKET",
            "triggerPrice": trigger_px,
            "workingType": "CONTRACT_PRICE",
            "closePosition": True,
            "clientAlgoId": client_order_id[:36],
        }
        resp = self._post_algo_order(params)
        if not isinstance(resp, dict):
            raise RuntimeError(f"Unexpected algo order response: {type(resp).__name__}")
        return self._normalize_algo_response(resp)

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        """Cancel order. SL/TP are algo orders (algoId); fallback to classic orderId."""
        try:
            algo_id = int(order_id)
        except (TypeError, ValueError):
            return self._request(
                "DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}
            )
        try:
            resp = self._request(
                "DELETE", "/fapi/v1/algoOrder", {"symbol": symbol, "algoId": algo_id}
            )
            if isinstance(resp, dict) and "orderId" not in resp and "algoId" in resp:
                return {**resp, "orderId": str(resp["algoId"])}
            return resp
        except RuntimeError as e:
            body = str(e).lower()
            if "-2011" in body or "unknown order" in body or "does not exist" in body:
                return self._request(
                    "DELETE", "/fapi/v1/order", {"symbol": symbol, "orderId": order_id}
                )
            raise

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

    def get_open_orders(self, symbol: str) -> list[dict]:
        """Fetch all open orders for symbol. Returns list of order dicts."""
        data = self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol})
        if not isinstance(data, list):
            return []
        return data

    def get_open_algo_orders(self, symbol: str) -> list[dict]:
        """GET /fapi/v1/openAlgoOrders — conditional SL/TP (not in openOrders)."""
        data = self._request(
            "GET",
            "/fapi/v1/openAlgoOrders",
            {"symbol": symbol, "algoType": "CONDITIONAL"},
        )
        return data if isinstance(data, list) else []

    def verify_algo_order_active(self, symbol: str, algo_order_id: str) -> bool:
        """Check if an algo order is live (NEW) on the exchange."""
        try:
            for o in self.get_open_algo_orders(symbol):
                aid = o.get("algoId")
                if aid is not None and str(aid) == str(algo_order_id):
                    status = o.get("algoStatus") or o.get("status")
                    return status == "NEW"
        except Exception:
            pass
        return False

    def cancel_all_algo_orders(self, symbol: str) -> int:
        """Cancel all CONDITIONAL algo orders for symbol. Returns count cancelled."""
        cancelled = 0
        for o in self.get_open_algo_orders(symbol):
            aid = o.get("algoId")
            if aid is None:
                continue
            try:
                self.cancel_order(symbol, str(aid))
                cancelled += 1
            except Exception:
                pass
        return cancelled

    def get_user_trades(self, symbol: str, limit: int = 20) -> list[dict]:
        """GET /fapi/v1/userTrades. Returns list of trade dicts (most recent first)."""
        data = self._request("GET", "/fapi/v1/userTrades", {"symbol": symbol, "limit": limit})
        return data if isinstance(data, list) else []
