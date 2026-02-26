"""Position sizing (2.5% risk, 5x leverage, stop-distance based)."""


class SizingEngine:
    """Computes position size from equity and stop distance."""

    def size(self, signal, equity: float, stop_distance: float):
        """Return (size, leverage)."""
        ...
