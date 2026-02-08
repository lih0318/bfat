"""
Range (RSI mean reversion) strategy for sideways markets.
Entry: long when RSI <= oversold, short when RSI >= overbought. SL/TP: ATR multiples.
"""
from typing import Any

from app.models.autopilot_config import AutopilotConfig
from app.strategies.base import BaseStrategy, MarketData, MarketDataCandle, SignalResult


def _ema(prices: list[float], period: int) -> float:
    if not prices or len(prices) < period:
        return prices[-1] if prices else 0.0
    k = 2.0 / (period + 1)
    ema_val = sum(prices[:period]) / period
    for p in prices[period:]:
        ema_val = p * k + ema_val * (1 - k)
    return ema_val


def _rsi(closes: list[float], period: int) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(d if d > 0 else 0.0)
        losses.append(-d if d < 0 else 0.0)
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _atr(candles: list[MarketDataCandle], period: int) -> float:
    if len(candles) < period + 1:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        h, l_, prev_c = candles[i].high, candles[i].low, candles[i - 1].close
        tr = max(h - l_, abs(h - prev_c), abs(l_ - prev_c))
        trs.append(tr)
    return sum(trs[-period:]) / period


class RangeRSIStrategy(BaseStrategy):
    """RSI mean reversion: long on oversold, short on overbought. For ranging/sideways markets."""

    def get_signal(self, data: MarketData, config: Any) -> tuple[SignalResult | None, str]:
        if not isinstance(config, AutopilotConfig):
            return (None, "invalid config")
        entry_tf = config.entry_tf
        if entry_tf not in data.candles or not data.candles[entry_tf]:
            return (None, "no candles")
        entry_candles = data.candles[entry_tf]
        closes = [c.close for c in entry_candles]
        if len(closes) < max(config.rsi_period, config.atr_period) + 5:
            return (None, "insufficient candles")
        price = data.current_price or (closes[-1] if closes else 0)
        if price <= 0:
            return (None, "invalid price")
        atr_val = _atr(entry_candles, config.atr_period)
        if atr_val <= 0:
            return (None, "ATR=0")
        rsi_val = _rsi(closes, config.rsi_period)
        if config.funding_rate_long_threshold is not None and data.funding_rate > config.funding_rate_long_threshold:
            return (None, f"funding_rate too high for long ({data.funding_rate:.4f})")
        if config.funding_rate_short_threshold is not None and data.funding_rate < config.funding_rate_short_threshold:
            return (None, f"funding_rate too low for short ({data.funding_rate:.4f})")
        if config.volume_ratio_min > 0 and data.volume_ratio < config.volume_ratio_min:
            return (None, f"volume_ratio {data.volume_ratio:.2f} < min {config.volume_ratio_min}")
        oversold = getattr(config, "rsi_oversold", 30.0)
        overbought = getattr(config, "rsi_overbought", 70.0)
        # Price filter & limit offset settings
        filter_mult = getattr(config, "price_filter_atr_mult", 0.0)
        offset_mult = getattr(config, "limit_offset_atr_mult", 0.0)
        ema20 = _ema(closes, 20) if len(closes) >= 20 else closes[-1]
        if rsi_val <= oversold:
            # Price filter: reject if price already ran too far above EMA
            if filter_mult > 0:
                threshold = ema20 + atr_val * filter_mult
                if price > threshold:
                    return (None, f"Price filter(long): {price:.2f} > EMA20({ema20:.2f})+ATR*{filter_mult} = {threshold:.2f}")
            entry = price - atr_val * offset_mult
            sl = entry - atr_val * config.atr_sl_mult
            tp = entry + atr_val * config.atr_tp_mult
            return (SignalResult(
                side="long",
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                reason=f"RSI={rsi_val:.1f}<=oversold_{oversold} ATR={atr_val:.2f} entry={entry:.2f}",
            ), "")
        if rsi_val >= overbought:
            # Price filter: reject if price already dropped too far below EMA
            if filter_mult > 0:
                threshold = ema20 - atr_val * filter_mult
                if price < threshold:
                    return (None, f"Price filter(short): {price:.2f} < EMA20({ema20:.2f})-ATR*{filter_mult} = {threshold:.2f}")
            entry = price + atr_val * offset_mult
            sl = entry + atr_val * config.atr_sl_mult
            tp = entry - atr_val * config.atr_tp_mult
            return (SignalResult(
                side="short",
                entry_price=entry,
                stop_loss=sl,
                take_profit=tp,
                reason=f"RSI={rsi_val:.1f}>=overbought_{overbought} ATR={atr_val:.2f} entry={entry:.2f}",
            ), "")
        return (None, f"RSI={rsi_val:.1f} in range (need <={oversold} long or >={overbought} short)")
