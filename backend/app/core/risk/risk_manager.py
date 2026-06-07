"""Position sizing and R-multiple calculation."""

from app.config.constants import RiskConstants
from app.domain.enums import Side


class RiskManager:
    """Fixed risk position sizing."""

    def __init__(self, risk_percent: float = RiskConstants.RISK_PERCENT) -> None:
        if not 0 < risk_percent < 1:
            raise ValueError("risk_percent must be between 0 and 1")
        self._risk_percent = risk_percent

    def calculate_position_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
    ) -> float:
        """Return raw position size. No rounding."""
        if equity <= 0:
            raise ValueError("equity must be positive")
        stop_distance = abs(entry_price - stop_price)
        if stop_distance <= 0:
            raise ValueError("stop_distance must be positive")
        risk_amount = equity * self._risk_percent
        return risk_amount / stop_distance

    @staticmethod
    def calculate_r_multiple(
        entry_price: float,
        exit_price: float,
        stop_price: float,
        side: Side,
    ) -> float:
        """Return R-multiple of the trade."""
        stop_distance = abs(entry_price - stop_price)
        if stop_distance == 0:
            raise ValueError("stop_distance must be non-zero")
        if side == Side.LONG:
            profit_per_unit = exit_price - entry_price
        else:
            profit_per_unit = entry_price - exit_price
        return profit_per_unit / stop_distance
