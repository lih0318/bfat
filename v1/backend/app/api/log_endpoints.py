"""Trade, equity, system log endpoints."""


class LogEndpoints:
    """GET trade_log, equity_log, system_log."""

    def get_trades(self, filters: dict):
        """Return trade log entries."""
        ...

    def get_equity(self, filters: dict):
        """Return equity log entries."""
        ...

    def get_system_logs(self, filters: dict):
        """Return system log entries."""
        ...
