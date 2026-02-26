"""Position and position state."""

from dataclasses import dataclass

from app.domain.enums import Side, StopPhase


@dataclass(frozen=True)
class Position:
    """Single position model. initial_stop_price is immutable (risk baseline)."""

    symbol: str
    side: Side
    size: float
    entry_price: float
    stop_price: float  # current (may trail)
    initial_stop_price: float  # never changes; used for R calculation
    stop_phase: StopPhase
    entry_time: str
    correlation_id: str
