"""Position and position state."""

from dataclasses import dataclass
from typing import Optional

from app.domain.enums import Side, StopPhase


@dataclass(frozen=True)
class Position:
    """Single position model. initial_stop_price is immutable (risk baseline)."""

    symbol: str
    side: Side
    size: float
    entry_price: float
    stop_price: float  # fixed SL level (exchange STOP_MARKET order)
    initial_stop_price: float  # never changes; used for R calculation
    stop_phase: StopPhase
    entry_time: str
    correlation_id: str
    take_profit: Optional[float] = None  # logical TP level (engine checks close vs this)
