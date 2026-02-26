"""Position state machine. Pure transitions, no side effects."""

from typing import Optional

from app.domain.enums import PositionState, Side, StopPhase
from app.domain.position import Position
from app.domain.signal import Signal


_STOP_PHASE_ORDER = (StopPhase.INITIAL, StopPhase.BREAKEVEN, StopPhase.TRAILING)


def _stop_phase_index(phase: StopPhase) -> int:
    return _STOP_PHASE_ORDER.index(phase)


def _is_forward_only(current: StopPhase, new_phase: StopPhase) -> bool:
    """True if new_phase is strictly after current in the forward direction."""
    return _stop_phase_index(new_phase) > _stop_phase_index(current)


def _is_stop_price_favorable(side: Side, current_stop: float, new_stop: float) -> bool:
    """LONG: new > current. SHORT: new < current."""
    if side == Side.LONG:
        return new_stop > current_stop
    return new_stop < current_stop


class StateMachine:
    """Manages position lifecycle. Validates and performs state transitions."""

    def __init__(self) -> None:
        self._state: PositionState = PositionState.FLAT
        self._position: Optional[Position] = None
        self._pending_signal: Optional[Signal] = None

    @property
    def state(self) -> PositionState:
        return self._state

    @property
    def position(self) -> Optional[Position]:
        return self._position

    @property
    def pending_signal(self) -> Optional[Signal]:
        return self._pending_signal

    def on_signal(self, signal: Signal) -> None:
        """FLAT → ENTERING. Cannot enter unless FLAT."""
        if self._state != PositionState.FLAT:
            raise ValueError(
                f"Cannot accept signal in {self._state.value}, must be FLAT"
            )
        self._state = PositionState.ENTERING
        self._position = None
        self._pending_signal = signal

    def on_entry_filled(self, position: Position) -> None:
        """ENTERING → OPEN. Cannot fill entry unless ENTERING."""
        if self._state != PositionState.ENTERING:
            raise ValueError(
                f"Cannot fill entry in {self._state.value}, must be ENTERING"
            )
        if self._pending_signal is None:
            raise ValueError("Cannot fill entry: no pending signal")
        self._state = PositionState.OPEN
        self._position = position
        self._pending_signal = None

    def on_stop_update(self, new_stop_phase: StopPhase, new_stop_price: float) -> None:
        """OPEN → OPEN. StopPhase and stop_price update. Both must be favorable."""
        if self._state != PositionState.OPEN:
            raise ValueError(
                f"Cannot update stop in {self._state.value}, must be OPEN"
            )
        if self._position is None:
            raise ValueError("No active position to update")
        current_phase = self._position.stop_phase
        current_price = self._position.stop_price
        if not _is_forward_only(current_phase, new_stop_phase):
            raise ValueError(
                f"StopPhase cannot move backward: {current_phase.value} → {new_stop_phase.value}"
            )
        if not _is_stop_price_favorable(self._position.side, current_price, new_stop_price):
            raise ValueError(
                f"Stop price not favorable for {self._position.side.value}: "
                f"{current_price} → {new_stop_price}"
            )
        self._position = Position(
            symbol=self._position.symbol,
            side=self._position.side,
            size=self._position.size,
            entry_price=self._position.entry_price,
            stop_price=new_stop_price,
            stop_phase=new_stop_phase,
            entry_time=self._position.entry_time,
            correlation_id=self._position.correlation_id,
        )

    def on_exit_requested(self) -> None:
        """OPEN → CLOSING. Cannot exit unless OPEN."""
        if self._state != PositionState.OPEN:
            raise ValueError(
                f"Cannot request exit in {self._state.value}, must be OPEN"
            )
        if self._position is None:
            raise ValueError("Cannot request exit: no active position")
        self._state = PositionState.CLOSING

    def rollback_entry(self) -> None:
        """ENTERING → FLAT. Only allowed when ENTERING."""
        if self._state != PositionState.ENTERING:
            raise ValueError(
                f"Cannot rollback in {self._state.value}, must be ENTERING"
            )
        self._state = PositionState.FLAT
        self._position = None
        self._pending_signal = None

    def on_exit_filled(self) -> None:
        """CLOSING → FLAT."""
        if self._state != PositionState.CLOSING:
            raise ValueError(
                f"Cannot fill exit in {self._state.value}, must be CLOSING"
            )
        if self._position is None:
            raise ValueError("Cannot fill exit: no active position")
        self._state = PositionState.FLAT
        self._position = None
