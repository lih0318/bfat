"""Binance Futures REST client."""


class BinanceFuturesClient:
    """REST API: market orders, reduceOnly Stop-Market, client order id."""

    def place_market_order(self, symbol: str, side: str, size: float, client_order_id: str):
        """Place market entry order."""
        ...

    def place_stop_market(self, symbol: str, side: str, size: float, stop_price: float, client_order_id: str):
        """Place reduceOnly Stop-Market order."""
        ...

    def modify_stop_order(self, symbol: str, order_id: int, stop_price: float):
        """Modify existing stop order price."""
        ...

    def cancel_order(self, symbol: str, order_id: int):
        """Cancel order."""
        ...

    def get_open_orders(self, symbol: str):
        """Fetch open orders (REST)."""
        ...

    def get_position_risk(self, symbol: str):
        """Fetch position (REST)."""
        ...
