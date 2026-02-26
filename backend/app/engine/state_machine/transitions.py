"""Position state transitions and guards."""


class PositionStateMachine:
    """Pure transitions: Flat, PendingEntry, Open, PendingClose."""

    def transition(self, event, current_state, context: dict):
        """Return (new_state, reason) or None if invalid."""
        ...
