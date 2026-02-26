"""Main orchestrator: subscribes to events, invokes modules."""


class TradingEngine:
    """Event loop: CandleClosed, Tick, OrderUpdate -> state machine -> strategy/risk/execution/persistence."""

    def start(self):
        """Start engine and subscribe to streams."""
        ...

    def stop(self):
        """Stop engine."""
        ...
