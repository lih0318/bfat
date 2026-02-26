"""Public WebSocket: price, kline streams."""


class PublicWebSocket:
    """Price + 15m kline streams, exponential backoff reconnect."""

    def connect(self):
        """Connect to streams."""
        ...

    def disconnect(self):
        """Disconnect."""
        ...
