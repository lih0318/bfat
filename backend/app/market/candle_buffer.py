"""Candle and ATR buffer for strategy input."""


class CandleBuffer:
    """Maintains candle history and ATR."""

    def append(self, ohlcv: dict):
        """Add candle."""
        ...

    def get_candles(self, count: int) -> list:
        """Return last N candles."""
        ...

    def get_atr(self, period: int = 14) -> float:
        """Return ATR value."""
        ...
