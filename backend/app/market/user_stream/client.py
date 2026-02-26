"""User Data Stream WebSocket client."""


class UserStreamClient:
    """ListenKey refresh, OrderUpdate/PositionUpdate events, reconnect + REST reconcile."""

    def connect(self):
        """Connect and start listenKey keepalive."""
        ...

    def disconnect(self):
        """Disconnect."""
        ...
