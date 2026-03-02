"""Strategy signal output."""

from dataclasses import dataclass
from typing import Optional

from app.domain.enums import Side


@dataclass(frozen=True)
class Signal:
    """Entry signal (LONG or SHORT).

    stop_price / take_profit are optional strategy-provided levels.
    When present the engine uses them instead of ATR-based defaults.
    position_scale multiplies the risk-manager-calculated base size.
    """

    symbol: str
    side: Side
    signal_time: str
    signal_candle_ts: str
    stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    position_scale: float = 1.0


@dataclass(frozen=True)
class CloseSignal:
    """Close existing position (e.g. regime switch)."""

    reason: str
