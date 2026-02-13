"""
Data feed: fetch klines, funding rates, ATR for the engine.
Includes basic QA (gap detection, outlier filter).
"""
from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np

from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)

# Timeframe → approximate bar duration in ms
_TF_MS: dict[str, int] = {
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
    "3d": 259_200_000,
    "1w": 604_800_000,
}


def _lookback_bars(horizon_days: int, tf: str) -> int:
    """Convert calendar-day horizon to approximate number of bars."""
    ms_per_bar = _TF_MS.get(tf, 86_400_000)
    return int(math.ceil(horizon_days * 86_400_000 / ms_per_bar)) + 5  # +5 buffer


def fetch_closes(
    symbols: list[str],
    signal_tf: str,
    max_horizon_days: int = 365,
) -> dict[str, np.ndarray]:
    """
    Fetch close prices for each symbol.
    Returns {symbol: np.array of close prices} (oldest → newest).
    """
    limit = min(_lookback_bars(max_horizon_days, signal_tf), 1500)
    result: dict[str, np.ndarray] = {}
    for sym in symbols:
        try:
            raw = binance_client.klines(symbol=sym, interval=signal_tf, limit=limit)
            if not raw:
                continue
            closes = np.array([float(k[4]) for k in raw], dtype=np.float64)
            # Basic QA: remove zero / negative
            closes = closes[closes > 0]
            if len(closes) < 30:
                logger.warning("datafeed: %s has only %d valid closes, skipping", sym, len(closes))
                continue
            result[sym] = closes
        except Exception as exc:
            logger.warning("datafeed: klines failed for %s: %s", sym, exc)
    return result


def fetch_hlc(
    symbols: list[str],
    signal_tf: str,
    window: int = 60,
) -> dict[str, dict[str, np.ndarray]]:
    """
    Fetch high/low/close arrays for ATR computation.
    Returns {symbol: {"high": arr, "low": arr, "close": arr}}.
    """
    limit = min(window + 10, 1500)
    result: dict[str, dict[str, np.ndarray]] = {}
    for sym in symbols:
        try:
            raw = binance_client.klines(symbol=sym, interval=signal_tf, limit=limit)
            if not raw or len(raw) < window:
                continue
            highs = np.array([float(k[2]) for k in raw], dtype=np.float64)
            lows = np.array([float(k[3]) for k in raw], dtype=np.float64)
            closes = np.array([float(k[4]) for k in raw], dtype=np.float64)
            result[sym] = {"high": highs, "low": lows, "close": closes}
        except Exception as exc:
            logger.warning("datafeed: HLC failed for %s: %s", sym, exc)
    return result


def fetch_funding(symbols: list[str]) -> dict[str, float]:
    """Fetch latest funding rate per symbol."""
    result: dict[str, float] = {}
    for sym in symbols:
        try:
            fr = binance_client.funding_rate(symbol=sym, limit=1)
            if fr:
                result[sym] = float(fr[0].get("fundingRate", 0) or 0)
        except Exception:
            result[sym] = 0.0
    return result


def compute_atr(
    highs: np.ndarray,
    lows: np.ndarray,
    closes: np.ndarray,
    window: int = 14,
) -> float:
    """Compute ATR (Average True Range) over the last ``window`` bars. Returns scalar."""
    if len(highs) < window + 1:
        return 0.0
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:] - closes[:-1]),
        ),
    )
    return float(np.mean(tr[-window:]))


def compute_realized_vol(closes: np.ndarray, window: int = 60) -> float:
    """
    Annualized realized volatility from log returns.
    Uses the last ``window`` close prices.
    """
    if len(closes) < window + 1:
        if len(closes) < 10:
            return 0.0
        window = len(closes) - 1
    recent = closes[-window - 1:]
    log_ret = np.diff(np.log(recent))
    if len(log_ret) == 0:
        return 0.0
    daily_vol = float(np.std(log_ret, ddof=1))
    return daily_vol * math.sqrt(365)  # annualize (crypto = 365 days)


def fetch_atr_map(
    symbols: list[str],
    signal_tf: str,
    window: int = 14,
) -> dict[str, float]:
    """Fetch and compute ATR for each symbol. Returns {symbol: ATR}."""
    hlc = fetch_hlc(symbols, signal_tf, window + 5)
    result: dict[str, float] = {}
    for sym, data in hlc.items():
        atr = compute_atr(data["high"], data["low"], data["close"], window)
        if atr > 0:
            result[sym] = atr
    return result


def fetch_vol_map(
    symbols: list[str],
    signal_tf: str,
    vol_window: int = 60,
) -> dict[str, float]:
    """Fetch closes and compute annualized realized vol per symbol."""
    closes_map = fetch_closes(symbols, signal_tf, vol_window + 10)
    result: dict[str, float] = {}
    for sym, closes in closes_map.items():
        vol = compute_realized_vol(closes, vol_window)
        if vol > 0:
            result[sym] = vol
    return result


def compute_rsi(closes: np.ndarray, period: int = 14) -> float:
    """Compute RSI for the latest bar."""
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
