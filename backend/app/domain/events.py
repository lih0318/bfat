"""Domain events (CandleClosed, Tick, OrderUpdate, etc.)."""


class CandleClosed:
    """15m candle close event."""

    pass


class Tick:
    """Price tick event."""

    pass


class OrderUpdate:
    """User Data Stream order update."""

    pass


class PositionUpdate:
    """User Data Stream position update."""

    pass
