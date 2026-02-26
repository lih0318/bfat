"""Kill switch (daily -10%, 6 consecutive losses)."""


class KillSwitch:
    """Checks and tracks kill switch conditions."""

    def report_trade(self, pnl: float, equity: float):
        """Update state after trade close."""
        ...

    def is_active(self) -> bool:
        """Return True if kill switch triggered."""
        ...
