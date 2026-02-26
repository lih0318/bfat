"""BB compression + breakout + volume + overextension filter (fixed spec)."""


class BBBreakoutStrategy:
    """Evaluates candles and returns Signal."""

    def evaluate(self, symbol: str, candles: list, atr: float):
        """Return Signal(LONG/SHORT) or FLAT."""
        ...
