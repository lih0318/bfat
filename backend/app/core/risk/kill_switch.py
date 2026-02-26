"""Kill switch logic. Reports status only."""

from typing import Optional


class KillSwitch:
    """Tracks daily and consecutive loss conditions."""

    def __init__(
        self,
        daily_loss_limit_percent: float = 0.10,
        max_consecutive_losses: int = 6,
        daily_start_equity: Optional[float] = None,
    ) -> None:
        if not 0 <= daily_loss_limit_percent <= 1:
            raise ValueError("daily_loss_limit_percent must be between 0 and 1")
        if max_consecutive_losses <= 0:
            raise ValueError("max_consecutive_losses must be positive")
        self._daily_loss_limit_percent = daily_loss_limit_percent
        self._max_consecutive_losses = max_consecutive_losses
        self._daily_start_equity: Optional[float] = daily_start_equity
        self._current_equity: Optional[float] = None
        self._consecutive_losses: int = 0

    def set_daily_start_equity(self, equity: float) -> None:
        """Set daily start equity (call at day boundary)."""
        if equity <= 0:
            raise ValueError("equity must be positive")
        self._daily_start_equity = equity

    def reset_daily(self, new_daily_start_equity: float) -> None:
        """Explicit reset: set daily start equity and reset consecutive losses."""
        if new_daily_start_equity <= 0:
            raise ValueError("equity must be positive")
        self._daily_start_equity = new_daily_start_equity
        self._consecutive_losses = 0

    def update_equity(self, equity: float) -> None:
        """Update current equity."""
        if equity <= 0:
            raise ValueError("equity must be positive")
        self._current_equity = equity
        if self._daily_start_equity is None:
            self._daily_start_equity = equity

    def register_trade_result(self, r_multiple: float) -> None:
        """Register trade close. Resets consecutive losses on positive R."""
        if r_multiple > 0:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1

    def is_triggered(self) -> bool:
        """Return True if kill switch conditions are met."""
        if self._current_equity is not None and self._daily_start_equity is not None:
            if self._daily_start_equity <= 0:
                return False
            threshold = self._daily_start_equity * (1 - self._daily_loss_limit_percent)
            if self._current_equity <= threshold:
                return True
        if self._consecutive_losses >= self._max_consecutive_losses:
            return True
        return False
