"""
Binance Futures account/balance client using official binance-futures-connector.
Same approach as v1: UMFutures SDK for reliable account fetch.
"""
import logging
from typing import Any, Optional

from binance.um_futures import UMFutures

logger = logging.getLogger(__name__)

DEFAULT_RECV_WINDOW = 10000

# URLs match v1 / Binance docs
FAPI_MAINNET = "https://fapi.binance.com"
FAPI_TESTNET = "https://testnet.binancefuture.com"


class BinanceAccountClient:
    """Thin wrapper over UMFutures for account/balance (same as v1)."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        testnet: bool = True,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._base_url = FAPI_TESTNET if testnet else FAPI_MAINNET
        self._client: Optional[UMFutures] = None

    @property
    def client(self) -> UMFutures:
        if self._client is None:
            self._client = UMFutures(
                key=self._api_key,
                secret=self._api_secret,
                base_url=self._base_url,
            )
        return self._client

    def is_configured(self) -> bool:
        return bool(self._api_key and self._api_secret)

    def account(self, recv_window: int = DEFAULT_RECV_WINDOW) -> dict[str, Any]:
        """GET /fapi/v2/account. Returns full account dict."""
        return self.client.account(recvWindow=recv_window)

    def balance(self, recv_window: int = DEFAULT_RECV_WINDOW) -> list[dict[str, Any]]:
        """GET /fapi/v2/balance. Returns list of asset balances."""
        return self.client.balance(recvWindow=recv_window)
