"""Position and position state."""

from dataclasses import dataclass

from app.domain.enums import Side, StopPhase


@dataclass(frozen=True)
class Position:
    """Single position model."""

    symbol: str
    side: Side
    size: float
    entry_price: float
    stop_price: float
    stop_phase: StopPhase
    entry_time: str
    correlation_id: str
