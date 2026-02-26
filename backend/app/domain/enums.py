"""Domain enums."""

from enum import Enum


class PositionState(Enum):
    """Position lifecycle state."""

    FLAT = "flat"
    ENTERING = "entering"
    OPEN = "open"
    CLOSING = "closing"


class StopPhase(Enum):
    """Stop order phase."""

    INITIAL = "initial"
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"


class Side(Enum):
    """Trade side."""

    LONG = "long"
    SHORT = "short"
