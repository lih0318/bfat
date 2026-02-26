"""Kill switch status and manual reset."""


class KillSwitchEndpoints:
    """Kill switch state and reset."""

    def get_status(self):
        """Return kill switch status."""
        ...

    def reset(self):
        """Manual reset (optional)."""
        ...
