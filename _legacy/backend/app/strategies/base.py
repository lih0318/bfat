"""
Strategy interface. All strategies return direction + SL/TP prices (no user input).
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

SignalSide = Literal["long", "short", "flat"]


@dataclass
class SignalResult:
    side: SignalSide
    entry_price: float
    stop_loss: float
    take_profit: float
    reason: str = ""


@dataclass
class MarketDataCandle:
    time: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class MarketData:
    """Multi-timeframe market data for strategy input."""
    symbol: str
    candles: dict[str, list[MarketDataCandle]]  # key: interval e.g. "15m", "1h"
    funding_rate: float = 0.0
    volume_ratio: float = 1.0
    current_price: float = 0.0


class BaseStrategy(ABC):
    @abstractmethod
    def get_signal(self, data: MarketData, config: Any) -> tuple[SignalResult | None, str]:
        """
        Return (signal, skip_reason). If no trade: (None, reason_string). If trade: (SignalResult, "").
        When signal.side is long/short, SL and TP must be set (strategy formula only).
        skip_reason is shown in activity log when no entry (e.g. "RSI=48, MACD<0, trend_bear").
        """
        pass
