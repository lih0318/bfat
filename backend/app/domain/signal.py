"""Strategy signal output."""

from dataclasses import dataclass

from app.domain.enums import Side


@dataclass(frozen=True)
class Signal:
    """Entry signal (LONG or SHORT)."""

    symbol: str
    side: Side
    signal_time: str
    signal_candle_ts: str
