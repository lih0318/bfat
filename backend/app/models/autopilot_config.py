"""
Autopilot configuration model. Saved to config_dir/autopilot.json for dev and Windows Standalone.
"""
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class AutopilotConfig(BaseModel):
    # User limits
    max_usdt: float = Field(1000.0, description="Max USDT for Autopilot to use")
    max_leverage: int = Field(5, ge=1, le=125, description="Max leverage")
    daily_loss_limit_usdt: float = Field(0.0, description="Daily loss limit USDT (0=disabled)")
    # Strategy mode: trend = Confluence+ATR, range = RSI mean reversion (sideways market)
    strategy_mode: Literal["trend", "range"] = Field("trend", description="trend=follow trend, range=mean reversion in sideways")
    # Range mode: RSI oversold -> long, RSI overbought -> short
    rsi_oversold: float = Field(30.0, ge=0, le=100, description="Range mode: long when RSI <= this")
    rsi_overbought: float = Field(70.0, ge=0, le=100, description="Range mode: short when RSI >= this")
    # Strategy params
    symbol: str = Field("BTCUSDT", description="Trading symbol")
    entry_tf: str = Field("15m", description="Entry timeframe e.g. 15m")
    trend_tf: str = Field("1h", description="Trend timeframe e.g. 1h")
    atr_period: int = Field(14, ge=1, le=100)
    atr_sl_mult: float = Field(1.5, gt=0, description="ATR multiplier for SL")
    atr_tp_mult: float = Field(2.0, gt=0, description="ATR multiplier for TP")
    rsi_period: int = Field(14, ge=1, le=50)
    rsi_long_min: float = Field(50.0, ge=0, le=100)
    rsi_short_max: float = Field(50.0, ge=0, le=100)
    volume_ratio_min: float = Field(0.0, ge=0, description="Min volume ratio (0=disabled)")
    funding_rate_long_threshold: Optional[float] = Field(None, description="Skip long if funding > this")
    funding_rate_short_threshold: Optional[float] = Field(None, description="Skip short if funding < this")
    reentry_cooldown_minutes: int = Field(15, ge=0, description="Minutes before re-entry after exit (0=disabled)")
    # Flip: when opposite signal, allow close current + open opposite only if economically justified
    allow_position_flip: bool = Field(True, description="Allow flip to opposite position when signal reverses (if edge > cost)")
    flip_fee_bps: float = Field(8.0, ge=0, le=100, description="Fee in basis points per leg (e.g. 8 = 0.08%)")
    flip_slippage_bps: float = Field(5.0, ge=0, le=100, description="Slippage estimate in bps per leg")
    flip_min_edge_ratio: float = Field(1.5, gt=0, le=10, description="New position upside must be >= this × flip cost")
    trading_hours_utc: Optional[str] = Field(None, description="e.g. 08:00-16:00 (UTC)")
    alerts_telegram_bot_token: Optional[str] = Field(None)
    alerts_telegram_chat_id: Optional[str] = Field(None)

    def model_dump_for_save(self) -> dict[str, Any]:
        d = self.model_dump()
        # Don't persist secrets in plain text in file; env is preferred
        if d.get("alerts_telegram_bot_token"):
            d["alerts_telegram_bot_token"] = "***"
        return d
