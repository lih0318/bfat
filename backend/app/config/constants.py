"""Central strategy and risk constants.

The defaults are intentionally conservative for BTC/USDT perpetuals on 15m
candles. They favor volatility-normalized exits and mandatory protection over
frequent entries.
"""


class StrategyConstants:
    """Fixed strategy parameters used by the engine and strategies."""

    CANDLE_INTERVAL = "15m"

    ATR_PERIOD = 14
    PROTECTIVE_SL_ATR_MULTIPLIER = 1.8
    PROTECTIVE_TP_ATR_MULTIPLIER = 3.6
    TP_BUFFER_TICKS = 5

    TREND_EMA_FAST_PERIOD = 12
    TREND_EMA_SLOW_PERIOD = 50
    TREND_VOLUME_LOOKBACK = 20
    TREND_VOLUME_RATIO_THRESHOLD = 1.10
    TREND_OVEREXTENSION_LOOKBACK = 10
    TREND_ATR_OVEREXTENSION = 2.5
    TREND_SL_ATR_MULTIPLIER = 1.8
    TREND_TP_ATR_MULTIPLIER = 3.6

    RANGE_LOOKBACK = 48
    RANGE_RSI_PERIOD = 14
    RANGE_ENTRY_THRESHOLD = 0.0025
    RANGE_STOP_BUFFER_PCT = 0.006
    RANGE_STOP_ATR_MULTIPLIER = 0.7
    RANGE_RSI_LONG = 35
    RANGE_RSI_SHORT = 65
    RANGE_VOLUME_LOOKBACK = 20
    RANGE_MIN_REWARD_RISK = 1.10

    REGIME_ADX_THRESHOLD = 22
    REGIME_ADX_PERIOD = 14
    REGIME_BB_WIDTH_PCT_THRESHOLD = 65
    REGIME_STRUCTURE_RATIO_THRESHOLD = 0.6
    REGIME_TRENDING_CONFIRM_BARS = 3
    REGIME_RANGING_CONFIRM_BARS = 5
    REGIME_BB_PERIOD = 20
    REGIME_BB_NUM_STD = 2.0
    REGIME_BB_WIDTH_LOOKBACK = 100
    REGIME_STRUCTURE_LOOKBACK = 20


class RiskConstants:
    """Risk parameters."""

    RISK_PERCENT = 0.01
