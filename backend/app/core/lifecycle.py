"""App lifecycle: startup/shutdown, WebSocket cleanup."""


class LifecycleManager:
    """Manages application startup and graceful shutdown."""

    def on_startup(self):
        """Called when app starts."""
        ...

    def on_shutdown(self):
        """Called when app shuts down (e.g. SIGTERM)."""
        ...
