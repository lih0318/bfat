"""
Confluence + ATR strategy: multi-indicator + ATR-based SL/TP.
Entry: RSI + trend alignment (entry_tf + trend_tf). SL/TP: ATR multiples only.
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


def _macd_histogram(closes: list[float], fast: int = 12, slow: int = 26) -> float:
    if len(closes) < slow:
        return 0.0
    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    return ema_fast - ema_slow


class ConfluenceATRStrategy(BaseStrategy):
    def get_signal(self, data: MarketData, config: Any) -> tuple[SignalResult | None, str]:
        if not isinstance(config, AutopilotConfig):
            return (None, "invalid config")
        entry_tf = config.entry_tf
        trend_tf = config.trend_tf
        if entry_tf not in data.candles or not data.candles[entry_tf]:
            return (None, "no candles")
        entry_candles = data.candles[entry_tf]
        trend_candles = data.candles.get(trend_tf, entry_candles)
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
        macd_hist = _macd_histogram(closes)
        trend_closes = [c.close for c in trend_candles]
        trend_ema = _ema(trend_closes, 20) if len(trend_closes) >= 20 else trend_closes[-1]
        trend_bull = price > trend_ema
        trend_bear = price < trend_ema
        if config.funding_rate_long_threshold is not None and data.funding_rate > config.funding_rate_long_threshold:
            return (None, f"funding_rate too high for long ({data.funding_rate:.4f})")
        if config.funding_rate_short_threshold is not None and data.funding_rate < config.funding_rate_short_threshold:
            return (None, f"funding_rate too low for short ({data.funding_rate:.4f})")
        if config.volume_ratio_min > 0 and data.volume_ratio < config.volume_ratio_min:
            return (None, f"volume_ratio {data.volume_ratio:.2f} < min {config.volume_ratio_min}")
        long_ok = (
            rsi_val >= config.rsi_long_min
            and macd_hist > 0
            and trend_bull
        )
        short_ok = (
            rsi_val <= config.rsi_short_max
            and macd_hist < 0
            and trend_bear
        )
        if long_ok:
            sl = price - atr_val * config.atr_sl_mult
            tp = price + atr_val * config.atr_tp_mult
            return (SignalResult(
                side="long",
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                reason=f"RSI={rsi_val:.1f} MACD>0 trend_bull ATR={atr_val:.2f}",
            ), "")
        if short_ok:
            sl = price + atr_val * config.atr_sl_mult
            tp = price - atr_val * config.atr_tp_mult
            return (SignalResult(
                side="short",
                entry_price=price,
                stop_loss=sl,
                take_profit=tp,
                reason=f"RSI={rsi_val:.1f} MACD<0 trend_bear ATR={atr_val:.2f}",
            ), "")
        # No entry: build reason for activity log
        reasons = []
        if rsi_val < config.rsi_long_min and rsi_val > config.rsi_short_max:
            reasons.append(f"RSI={rsi_val:.1f} (need >={config.rsi_long_min} long or <={config.rsi_short_max} short)")
        elif rsi_val < config.rsi_long_min:
            reasons.append(f"RSI={rsi_val:.1f}<long_min {config.rsi_long_min}")
        else:
            reasons.append(f"RSI={rsi_val:.1f}>short_max {config.rsi_short_max}")
        if macd_hist <= 0:
            reasons.append("MACD<=0")
        else:
            reasons.append("MACD>0")
        if not trend_bull and not trend_bear:
            reasons.append("trend neutral")
        elif not trend_bull:
            reasons.append("trend_bear")
        else:
            reasons.append("trend_bull")
        return (None, "; ".join(reasons))
