"""
Signal engine: generates LONG / SHORT / NONE, confidence (0~1), market_regime.
BTC: Trend following (4H regime + 15m pullback).
ALT: Mean reversion (5m BB + RSI + volume spike).
BTC dominance filter: blocks ALT long when BTC bearish + oversold.
Pure logic — no Binance API calls. Uses only MarketSnapshot data.
"""
from __future__ import annotations

import logging
from typing import Literal, Optional

import numpy as np

from app.engine.modular.config_model import ModularConfig
from app.engine.modular.types import (
    MarketSnapshot,
    SignalOutput,
    SignalResult,
    SignalSnapshot,
)

logger = logging.getLogger(__name__)

Direction = Literal["LONG", "SHORT", "NONE"]

# Config defaults for signal logic
RSI_PERIOD = 14
ATR_PERIOD = 14
BB_PERIOD = 20
BB_STD = 2.0
VOLUME_SPIKE_MULT = 1.5
VOLUME_LOOKBACK = 20
DEFAULT_PULLBACK_PCT = 0.005
ATR_EXTREME_MULT = 2.0
DEFAULT_ALT_RSI_LONG_MAX = 30
DEFAULT_ALT_RSI_SHORT_MIN = 70


def _ema(closes: np.ndarray, period: int) -> float:
    """EMA of last 'period' closes. Returns latest value."""
    if len(closes) < period:
        return float(closes[-1]) if len(closes) > 0 else 0.0
    arr = closes[-period:].astype(np.float64)
    k = 2.0 / (period + 1)
    ema_val = float(arr[0])
    for i in range(1, len(arr)):
        ema_val = arr[i] * k + ema_val * (1 - k)
    return ema_val


def _compute_rsi(closes: np.ndarray, period: int) -> float:
    """RSI for the latest bar."""
    if len(closes) < period + 1:
        return 50.0
    deltas = np.diff(closes[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_gain = float(np.mean(gains))
    avg_loss = float(np.mean(losses))
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _rsi_prior(closes: np.ndarray, period: int) -> float:
    """RSI of the bar before the latest (for rising/falling check)."""
    if len(closes) < period + 2:
        return 50.0
    # exclude last close to get prior bar RSI
    prior = closes[: -1]
    return _compute_rsi(prior, period)


def _compute_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, period: int) -> float:
    """ATR(14) for latest bar."""
    if len(high) < period + 1 or len(low) < period + 1 or len(close) < period + 1:
        return 0.0
    tr = np.maximum(
        high[1:] - low[1:],
        np.maximum(
            np.abs(high[1:] - close[:-1]),
            np.abs(low[1:] - close[:-1]),
        ),
    )
    return float(np.mean(tr[-period:]))


def _bollinger_bands(closes: np.ndarray, period: int = 20, std_mult: float = 2.0) -> tuple[float, float, float]:
    """Returns (upper, middle, lower) for latest bar."""
    if len(closes) < period:
        mid = float(closes[-1]) if len(closes) > 0 else 0.0
        return (mid, mid, mid)
    arr = closes[-period:]
    mid = float(np.mean(arr))
    std = float(np.std(arr))
    if std == 0:
        std = 1e-10
    upper = mid + std_mult * std
    lower = mid - std_mult * std
    return (upper, mid, lower)


def _is_volume_spike(volumes: np.ndarray, mult: float = VOLUME_SPIKE_MULT, lookback: int = VOLUME_LOOKBACK) -> bool:
    """True if latest volume >= mult * mean of recent lookback."""
    if len(volumes) < lookback + 1:
        return False
    recent = volumes[-(lookback + 1):-1]  # exclude current
    mean_vol = float(np.mean(recent))
    if mean_vol <= 0:
        return True
    return float(volumes[-1]) >= mult * mean_vol


def _price_near_ema(price: float, ema50: float, tolerance_pct: float = DEFAULT_PULLBACK_PCT) -> bool:
    """True if price is within tolerance of EMA50."""
    if ema50 <= 0:
        return False
    pct = abs(price - ema50) / ema50
    return pct <= tolerance_pct


def _atr_not_extreme(atr: float, hlc: dict[str, np.ndarray], period: int, mult: float = ATR_EXTREME_MULT) -> bool:
    """True if ATR is not extreme (not > mult * median of recent ATRs)."""
    high = hlc.get("high")
    low = hlc.get("low")
    close = hlc.get("close")
    if high is None or low is None or close is None or len(high) < period + 20:
        return True  # assume ok if not enough data
    n = min(20, len(high) - period - 1)
    atrs: list[float] = []
    for i in range(n):
        start = -(n - i + period + 1)
        end = -(n - i) if (n - i) > 0 else None
        h = high[start:end]
        l = low[start:end]
        c = close[start:end]
        if len(h) >= period + 1:
            atrs.append(_compute_atr(h, l, c, period))
    if not atrs:
        return True
    med = float(np.median(atrs))
    if med <= 0:
        return True
    return atr <= mult * med


def _btc_dominance_blocks_long(snapshot: MarketSnapshot) -> bool:
    """True if BTC 4H RSI < 35 AND price < EMA200 → block all ALT longs."""
    c4 = snapshot.closes_4h_map.get("BTCUSDT")
    if c4 is None or len(c4) < 201:
        return False
    price = c4[-1]
    ema200 = _ema(c4, 200)
    rsi = _compute_rsi(c4, RSI_PERIOD)
    return rsi < 35 and price < ema200


def _btc_signal(snapshot: MarketSnapshot, config: Optional[ModularConfig] = None) -> SignalOutput:
    """BTCUSDT trend-following signal (4H regime + 15m pullback)."""
    symbol = "BTCUSDT"
    pullback_tol = (getattr(config, "btc_pullback_tolerance_pct", None) or DEFAULT_PULLBACK_PCT) if config else DEFAULT_PULLBACK_PCT
    regime = "neutral"
    direction: Direction = "NONE"
    confidence = 0.0

    c4 = snapshot.closes_4h_map.get(symbol)
    c15 = snapshot.closes_15m_map.get(symbol)
    hlc15 = snapshot.hlc_15m_map.get(symbol)
    price = snapshot.price_map.get(symbol, 0.0)

    if c4 is None or len(c4) < 201:
        return SignalOutput(symbol=symbol, direction="NONE", confidence=0.0, market_regime="neutral")

    ema200_4h = _ema(c4, 200)
    if price > ema200_4h:
        regime = "bullish"
    else:
        regime = "bearish"

    if c15 is None or len(c15) < 51 or hlc15 is None:
        return SignalOutput(symbol=symbol, direction="NONE", confidence=0.0, market_regime=regime)

    ema50_15m = _ema(c15, 50)
    rsi15 = _compute_rsi(c15, RSI_PERIOD)
    rsi_prior = _rsi_prior(c15, RSI_PERIOD)
    atr15 = _compute_atr(hlc15["high"], hlc15["low"], hlc15["close"], ATR_PERIOD)
    atr_ok = _atr_not_extreme(atr15, hlc15, ATR_PERIOD)
    pullback = _price_near_ema(price, ema50_15m, pullback_tol)

    if regime == "bullish" and pullback and rsi15 > 45 and rsi15 > rsi_prior and atr_ok:
        direction = "LONG"
        confidence = 0.6 + 0.2 * min(1.0, (rsi15 - 45) / 30)  # 0.6~0.8
    elif regime == "bearish" and pullback and rsi15 < 55 and rsi15 < rsi_prior and atr_ok:
        direction = "SHORT"
        confidence = 0.6 + 0.2 * min(1.0, (55 - rsi15) / 25)

    return SignalOutput(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        market_regime=regime,
    )


def _alt_signal(
    symbol: str,
    snapshot: MarketSnapshot,
    btc_blocks_long: bool,
    config: Optional[ModularConfig] = None,
) -> SignalOutput:
    """ALT mean-reversion signal (5m BB + RSI + volume spike)."""
    regime = "ranging"
    rsi_long_max = (getattr(config, "alt_rsi_long_max", None) or DEFAULT_ALT_RSI_LONG_MAX) if config else DEFAULT_ALT_RSI_LONG_MAX
    rsi_short_min = (getattr(config, "alt_rsi_short_min", None) or DEFAULT_ALT_RSI_SHORT_MIN) if config else DEFAULT_ALT_RSI_SHORT_MIN
    direction: Direction = "NONE"
    confidence = 0.0

    c5 = snapshot.closes_5m_map.get(symbol)
    vol5 = snapshot.volume_5m_map.get(symbol)

    if c5 is None or len(c5) < 25:
        return SignalOutput(symbol=symbol, direction="NONE", confidence=0.0, market_regime=regime)

    price = float(c5[-1])
    upper, mid, lower = _bollinger_bands(c5, BB_PERIOD, BB_STD)
    rsi = _compute_rsi(c5, RSI_PERIOD)
    vol_spike = _is_volume_spike(vol5, VOLUME_SPIKE_MULT, VOLUME_LOOKBACK) if vol5 is not None and len(vol5) >= VOLUME_LOOKBACK + 1 else False

    touch_lower = price <= lower * 1.002  # within 0.2% of lower band
    touch_upper = price >= upper * 0.998

    if touch_lower and rsi < rsi_long_max and vol_spike and not btc_blocks_long:
        direction = "LONG"
        confidence = 0.65 + 0.15 * min(1.0, (rsi_long_max - rsi) / 20)
    elif touch_upper and rsi > rsi_short_min and vol_spike:
        direction = "SHORT"
        confidence = 0.65 + 0.15 * min(1.0, (rsi - rsi_short_min) / 20)

    return SignalOutput(
        symbol=symbol,
        direction=direction,
        confidence=min(1.0, confidence),
        market_regime=regime,
    )


def run(snapshot: MarketSnapshot, config: ModularConfig) -> SignalResult:
    """
    Generate signals from MarketSnapshot. No Binance calls.
    BTC: trend following. ALT: mean reversion. BTC dominance filter for ALT longs.
    """
    result = SignalResult()
    if not snapshot.symbols:
        return result

    btc_blocks_long = _btc_dominance_blocks_long(snapshot)
    is_btc = lambda s: s == "BTCUSDT"

    for sym in snapshot.symbols:
        snap = SignalSnapshot(symbol=sym)

        if is_btc(sym):
            out = _btc_signal(snapshot, config)
        else:
            out = _alt_signal(sym, snapshot, btc_blocks_long, config)

        result.outputs[sym] = out
        snap.final_score = 1.0 if out.direction == "LONG" else -1.0 if out.direction == "SHORT" else 0.0
        result.snapshots[sym] = snap

    return result
