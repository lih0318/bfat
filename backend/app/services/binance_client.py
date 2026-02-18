"""
Binance USDT-M Futures client wrapper using official binance-futures-connector.
All Binance calls go through this layer for base_url and error handling.
SL/TP use Algo Order API (POST /fapi/v1/algoOrder) due to -4120 on regular order endpoint.
Uses server time sync to avoid "Timestamp for this request is outside of the recvWindow" errors.
"""
import hashlib
import hmac
import logging
import time
from typing import Any, Optional
from urllib.parse import urlencode

import httpx
from binance.um_futures import UMFutures
from binance.error import ClientError

from app.core.config import settings

logger = logging.getLogger(__name__)

# Default recvWindow (ms). Use 10s to tolerate clock drift; Binance allows up to 60000.
DEFAULT_RECV_WINDOW = 10000


def _get_ws_base_url() -> str:
    if settings.ws_base_url:
        return settings.ws_base_url
    if "testnet" in settings.fapi_base_url.lower():
        return "wss://stream.binancefuture.com"
    return "wss://fstream.binance.com"


class BinanceFuturesClient:
    """Thin wrapper over UMFutures for fapi.binance.com / testnet. Syncs with Binance server time."""

    def __init__(self) -> None:
        self._client: Optional[UMFutures] = None
        self._server_time_offset: float = 0.0  # seconds to add to time.time() to get server time
        self._server_time_synced: bool = False

    def _sync_server_time(self) -> None:
        """Fetch Binance server time and set offset so timestamp in requests matches server."""
        try:
            url = f"{settings.fapi_base_url.rstrip('/')}/fapi/v1/time"
            with httpx.Client(timeout=5.0) as client:
                r = client.get(url)
            r.raise_for_status()
            data = r.json()
            server_time_ms = int(data.get("serverTime", 0))
            local_ms = int(time.time() * 1000)
            self._server_time_offset = (server_time_ms - local_ms) / 1000.0
            self._server_time_synced = True
            logger.info("Binance server time synced: offset %.2fs", self._server_time_offset)
        except Exception as e:
            logger.warning("Binance server time sync failed: %s. Using local time.", e)

    def _timestamp_ms(self) -> int:
        """Return current timestamp in ms adjusted for Binance server time."""
        if not self._server_time_synced:
            self._sync_server_time()
        return int((time.time() + self._server_time_offset) * 1000)

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
    def balance(self, recv_window: int = DEFAULT_RECV_WINDOW) -> list[dict[str, Any]]:
        return self.client.balance(recvWindow=recv_window)

    def account(self, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        return self.client.account(recvWindow=recv_window)

    def income_history(
        self,
        symbol: Optional[str] = None,
        income_type: Optional[str] = None,
        limit: int = 100,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        recv_window: int = DEFAULT_RECV_WINDOW,
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

    def user_trades(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100,
        recv_window: int = DEFAULT_RECV_WINDOW,
    ) -> list[dict[str, Any]]:
        """Get account trade list for a symbol (GET /fapi/v1/userTrades)."""
        params: dict[str, Any] = {
            "symbol": symbol,
            "limit": limit,
            "recvWindow": recv_window,
        }
        if start_time is not None:
            params["startTime"] = start_time
        if end_time is not None:
            params["endTime"] = end_time
        data = self._signed_request("GET", "/fapi/v1/userTrades", params)
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
    def position_information(self, symbol: Optional[str] = None, recv_window: int = DEFAULT_RECV_WINDOW) -> list[dict[str, Any]]:
        if symbol:
            return self.client.get_position_risk(symbol=symbol, recvWindow=recv_window)
        return self.client.get_position_risk(recvWindow=recv_window)

    def get_open_orders(self, symbol: str, recv_window: int = DEFAULT_RECV_WINDOW) -> list[dict[str, Any]]:
        return self.client.get_open_orders(symbol=symbol, recvWindow=recv_window)

    def cancel_all_open_orders(self, symbol: str, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """Cancel all open orders for the symbol (e.g. before flip to clear old SL/TP)."""
        return self.client.cancel_open_orders(symbol=symbol, recvWindow=recv_window)

    def _signed_request(self, method: str, path: str, params: dict[str, Any]) -> Any:
        """Send signed request to Binance FAPI (for algo endpoints not in SDK). Uses server-time-adjusted timestamp."""
        params = dict(params)
        params.setdefault("recvWindow", DEFAULT_RECV_WINDOW)
        params.setdefault("timestamp", self._timestamp_ms())
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

    def get_open_algo_orders(self, symbol: str, recv_window: int = DEFAULT_RECV_WINDOW) -> list[dict[str, Any]]:
        """Get open algo orders for symbol (SL/TP are algo orders)."""
        data = self._signed_request("GET", "/fapi/v1/openAlgoOrders", {"symbol": symbol, "recvWindow": recv_window})
        return data if isinstance(data, list) else []

    def cancel_algo_order(
        self,
        algo_id: Optional[int] = None,
        client_algo_id: Optional[str] = None,
        recv_window: int = DEFAULT_RECV_WINDOW,
    ) -> dict[str, Any]:
        """Cancel one algo order by algoId or clientAlgoId."""
        params: dict[str, Any] = {"recvWindow": recv_window}
        if algo_id is not None:
            params["algoId"] = algo_id
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        return self._signed_request("DELETE", "/fapi/v1/algoOrder", params)

    def cancel_all_algo_orders(self, symbol: str, recv_window: int = DEFAULT_RECV_WINDOW) -> None:
        """Cancel all open algo orders for the symbol (e.g. old SL/TP before flip)."""
        for order in self.get_open_algo_orders(symbol, recv_window):
            try:
                if order.get("clientAlgoId"):
                    self.cancel_algo_order(client_algo_id=order["clientAlgoId"], recv_window=recv_window)
                elif order.get("algoId") is not None:
                    self.cancel_algo_order(algo_id=order["algoId"], recv_window=recv_window)
            except Exception:
                pass

    @staticmethod
    def _format_decimal(value: float, max_decimals: int = 8) -> str:
        """Format float to string without scientific notation and trailing zeros, respecting precision."""
        formatted = f"{value:.{max_decimals}f}"
        if "." in formatted:
            formatted = formatted.rstrip("0").rstrip(".")
        return formatted

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
        recv_window: int = DEFAULT_RECV_WINDOW,
    ) -> dict[str, Any]:
        """
        Place STOP_MARKET or TAKE_PROFIT_MARKET via Algo Order API (required since -4120).
        algoType=CONDITIONAL, triggerPrice required.
        Returns response dict. Raises RuntimeError if Binance returns an error code.
        """
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": self._format_decimal(trigger_price),
            "reduceOnly": "true" if reduce_only else "false",
            "workingType": working_type,
            "positionSide": "BOTH",
            "recvWindow": recv_window,
            "timestamp": self._timestamp_ms(),
        }
        if quantity is not None:
            params["quantity"] = self._format_decimal(quantity)
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        result = self._signed_request("POST", "/fapi/v1/algoOrder", params)
        # Validate: success has algoId, error has negative code
        if isinstance(result, dict):
            if result.get("algoId"):
                return result  # Success
            if result.get("code") and int(result["code"]) < 0:
                raise RuntimeError(f"Algo order rejected: code={result.get('code')} msg={result.get('msg')}")
        logger.warning("Algo order (qty) unexpected response for %s: %s", symbol, result)
        return result

    def new_algo_order_close_position(
        self,
        symbol: str,
        side: str,
        order_type: str,
        trigger_price: float,
        client_algo_id: Optional[str] = None,
        working_type: str = "CONTRACT_PRICE",
        recv_window: int = DEFAULT_RECV_WINDOW,
    ) -> dict[str, Any]:
        """
        Place STOP_MARKET or TAKE_PROFIT_MARKET via Algo Order API using closePosition=true.
        This closes the entire position without specifying quantity (avoids rounding issues).
        Cannot be used with quantity or reduceOnly.
        """
        params: dict[str, Any] = {
            "algoType": "CONDITIONAL",
            "symbol": symbol,
            "side": side,
            "type": order_type,
            "triggerPrice": self._format_decimal(trigger_price),
            "closePosition": "true",
            "workingType": working_type,
            "positionSide": "BOTH",
            "recvWindow": recv_window,
            "timestamp": self._timestamp_ms(),
        }
        if client_algo_id is not None:
            params["clientAlgoId"] = client_algo_id
        result = self._signed_request("POST", "/fapi/v1/algoOrder", params)
        # Validate: success has algoId, error has negative code
        if isinstance(result, dict):
            if result.get("algoId"):
                return result  # Success
            if result.get("code") and int(result["code"]) < 0:
                raise RuntimeError(f"Algo order rejected: code={result.get('code')} msg={result.get('msg')}")
        logger.warning("Algo order unexpected response for %s: %s", symbol, result)
        return result

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
        recv_window: int = DEFAULT_RECV_WINDOW,
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

    def set_leverage(self, symbol: str, leverage: int, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        return self.client.change_leverage(symbol=symbol, leverage=leverage, recvWindow=recv_window)

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED", recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """
        Set margin type for a symbol: 'ISOLATED' or 'CROSSED'.
        Handles Binance error -4046 (no need to change margin type) gracefully.
        Returns response dict or {'code': 200, 'msg': 'already_set'} if already correct.
        """
        try:
            return self._signed_request("POST", "/fapi/v1/marginType", {
                "symbol": symbol,
                "marginType": margin_type,
                "recvWindow": recv_window,
            })
        except httpx.HTTPStatusError as exc:
            # Binance returns 400 with code -4046 if already set to the same margin type
            try:
                body = exc.response.json()
                if body.get("code") == -4046:
                    logger.debug("set_margin_type: %s already %s", symbol, margin_type)
                    return {"code": 200, "msg": "already_set"}
            except Exception:
                pass
            raise
        except ClientError as exc:
            # binance-connector may raise ClientError with error_code=-4046
            if getattr(exc, "error_code", None) == -4046:
                logger.debug("set_margin_type: %s already %s", symbol, margin_type)
                return {"code": 200, "msg": "already_set"}
            raise

    def get_margin_type(self, symbol: str, recv_window: int = DEFAULT_RECV_WINDOW) -> str:
        """
        Get current margin type for a symbol by inspecting position info.
        Returns 'isolated' or 'cross'.
        """
        try:
            positions = self.position_information(symbol=symbol, recv_window=recv_window)
            for p in positions:
                if p.get("symbol") == symbol:
                    return str(p.get("marginType", "cross")).lower()
        except Exception as exc:
            logger.warning("get_margin_type failed for %s: %s", symbol, exc)
        return "cross"  # default assumption

    # ── New methods for TSMOM engine ─────────────────────────────

    def book_ticker(self, symbol: str) -> dict[str, Any]:
        """Best bid/ask for a symbol (GET /fapi/v1/ticker/bookTicker)."""
        data = self.client.book_ticker(symbol=symbol)
        if isinstance(data, list):
            return data[0] if data else {}
        return data if isinstance(data, dict) else {}

    def ticker_24hr(self, symbol: Optional[str] = None) -> Any:
        """24-hour ticker statistics. Returns list (all) or dict (single symbol)."""
        if symbol:
            return self.client.ticker_24hr_price_change(symbol=symbol)
        return self.client.ticker_24hr_price_change()

    def change_position_mode(self, dual_side: bool = False, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """Set hedge mode (dual=True) or one-way mode (dual=False)."""
        return self._signed_request("POST", "/fapi/v1/positionSide/dual", {
            "dualSidePosition": "true" if dual_side else "false",
            "recvWindow": recv_window,
        })

    def get_position_mode(self, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """Get current position mode."""
        return self._signed_request("GET", "/fapi/v1/positionSide/dual", {
            "recvWindow": recv_window,
        })

    def listen_key_create(self) -> str:
        """Create a listenKey for user data stream."""
        data = self._signed_request("POST", "/fapi/v1/listenKey", {})
        return data.get("listenKey", "") if isinstance(data, dict) else ""

    def listen_key_keepalive(self) -> None:
        """Keepalive existing listenKey."""
        self._signed_request("PUT", "/fapi/v1/listenKey", {})

    def listen_key_close(self) -> None:
        """Close listenKey."""
        self._signed_request("DELETE", "/fapi/v1/listenKey", {})

    def cancel_order(self, symbol: str, order_id: Optional[int] = None,
                     client_order_id: Optional[str] = None,
                     recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """Cancel a single order by orderId or origClientOrderId."""
        params: dict[str, Any] = {"symbol": symbol, "recvWindow": recv_window}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self.client.cancel_order(**params)

    def get_order(self, symbol: str, order_id: Optional[int] = None,
                  client_order_id: Optional[str] = None,
                  recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """Query a single order."""
        params: dict[str, Any] = {"symbol": symbol, "recvWindow": recv_window}
        if order_id is not None:
            params["orderId"] = order_id
        if client_order_id is not None:
            params["origClientOrderId"] = client_order_id
        return self.client.query_order(**params)


binance_client = BinanceFuturesClient()
