"""Status and position schemas."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class PositionResponse(BaseModel):
    """Position info for API."""

    symbol: str
    side: str
    size: float
    entry_price: float
    stop_price: float
    stop_phase: str
    entry_time: str
    correlation_id: str


class LastSignalResponse(BaseModel):
    """Last signal info."""

    symbol: str
    side: str
    signal_time: str
    signal_candle_ts: str


class StatusResponse(BaseModel):
    """Engine status for /api/status and WebSocket."""

    engine_state: str
    symbols: list[str] = Field(default_factory=list)
    max_concurrent_positions: int = 1
    open_position_count: int = 0
    positions: list[PositionResponse] = Field(default_factory=list)
    symbol_statuses: list[dict[str, Any]] = Field(default_factory=list)
    position: Optional[PositionResponse] = None
    last_signal: Optional[LastSignalResponse] = None
    current_stop_price: Optional[float] = None
    take_profit: Optional[float] = None
    tp_protection_mode: str = "none"
    tp_verified: Optional[bool] = None
    sl_protection_mode: str = "none"
    sl_verified: Optional[bool] = None
    equity: float
    kill_switch_triggered: bool
    error: Optional[str] = None


class LogEntryResponse(BaseModel):
    """System log entry."""

    id: int
    ts: str
    level: str
    event: str
    message: str
    payload: Optional[str] = None
    correlation_id: Optional[str] = None
