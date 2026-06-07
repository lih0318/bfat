"""
Market regime: ADX-based ranging vs trending for UI. Provides both 1D (big picture) and 1h (trading timeframe).
"""
import logging
from typing import Any

from app.services.binance_client import binance_client

logger = logging.getLogger(__name__)

ADX_PERIOD = 14
ADX_RANGING_THRESHOLD = 25.0
REGIME_TIMEFRAMES = ("1d", "1h")


def _wilder_smooth(values: list[float], period: int) -> list[float]:
    """Wilder smoothing (RMA): first = sum(first period), then sm[i] = (sm[i-1]*(period-1) + val[i])/period."""
    if len(values) < period:
        return []
    out: list[float] = []
    first = sum(values[:period]) / period
    out.append(first)
    for i in range(period, len(values)):
        sm = (out[-1] * (period - 1) + values[i]) / period
        out.append(sm)
    return out


def compute_adx(
    highs: list[float], lows: list[float], closes: list[float], period: int = ADX_PERIOD
) -> tuple[float, float, float] | None:
    """
    Compute ADX, +DI, -DI for the last bar. Returns (adx, plus_di, minus_di) or None if insufficient data.
    """
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return None
    tr_list: list[float] = []
    plus_dm_list: list[float] = []
    minus_dm_list: list[float] = []
    for i in range(1, n):
        h, l_, c = highs[i], lows[i], closes[i]
        prev_h, prev_l, prev_c = highs[i - 1], lows[i - 1], closes[i - 1]
        tr = max(h - l_, abs(h - prev_c), abs(l_ - prev_c))
        tr_list.append(tr)
        up_move = h - prev_h
        down_move = prev_l - l_
        if up_move > down_move and up_move > 0:
            plus_dm_list.append(up_move)
            minus_dm_list.append(0.0)
        elif down_move > up_move and down_move > 0:
            plus_dm_list.append(0.0)
            minus_dm_list.append(down_move)
        else:
            plus_dm_list.append(0.0)
            minus_dm_list.append(0.0)
    if len(tr_list) < period:
        return None
    sm_tr = _wilder_smooth(tr_list, period)
    sm_plus = _wilder_smooth(plus_dm_list, period)
    sm_minus = _wilder_smooth(minus_dm_list, period)
    if not sm_tr or not sm_plus or not sm_minus:
        return None
    # Last values for +DI, -DI
    last_tr = sm_tr[-1]
    last_plus = sm_plus[-1]
    last_minus = sm_minus[-1]
    if last_tr <= 0:
        return None
    plus_di = 100.0 * last_plus / last_tr
    minus_di = 100.0 * last_minus / last_tr
    di_sum = plus_di + minus_di
    if di_sum <= 0:
        return None
    dx = 100.0 * abs(plus_di - minus_di) / di_sum
    # ADX = Wilder smooth of DX (over same period)
    dx_series: list[float] = []
    for j in range(len(sm_tr)):
        t = sm_tr[j]
        p = sm_plus[j]
        m = sm_minus[j]
        if t <= 0:
            dx_series.append(0.0)
            continue
        pdi = 100.0 * p / t
        mdi = 100.0 * m / t
        s = pdi + mdi
        if s <= 0:
            dx_series.append(0.0)
            continue
        dx_series.append(100.0 * abs(pdi - mdi) / s)
    adx_smoothed = _wilder_smooth(dx_series, period)
    if not adx_smoothed:
        return None
    adx = adx_smoothed[-1]
    return (adx, plus_di, minus_di)


def _regime_for_timeframe(symbol: str, interval: str) -> dict[str, Any]:
    """Compute ADX regime for one timeframe. Returns { timeframe, adx, regime, trend_direction }."""
    out: dict[str, Any] = {
        "timeframe": interval,
        "adx": None,
        "regime": "unknown",
        "trend_direction": "neutral",
    }
    try:
        limit = 60 if interval == "1d" else 80
        raw = binance_client.klines(symbol=symbol.upper(), interval=interval, limit=limit)
        if not raw or len(raw) < ADX_PERIOD + 2:
            return out
        highs = [float(k[2]) for k in raw]
        lows = [float(k[3]) for k in raw]
        closes = [float(k[4]) for k in raw]
        computed = compute_adx(highs, lows, closes, ADX_PERIOD)
        if computed is None:
            return out
        adx, plus_di, minus_di = computed
        out["adx"] = round(adx, 2)
        out["regime"] = "ranging" if adx < ADX_RANGING_THRESHOLD else "trending"
        if adx >= ADX_RANGING_THRESHOLD:
            out["trend_direction"] = "up" if plus_di > minus_di else "down"
    except Exception as e:
        logger.warning("Market regime failed for %s %s: %s", symbol, interval, e)
    return out


def get_market_regime(symbol: str) -> dict[str, Any]:
    """
    Fetch 1D and 1h klines, compute ADX for each, return both for UI.
    Returns: { symbol, "1d": { timeframe, adx, regime, trend_direction }, "1h": { ... } }.
    """
    sym = symbol.upper()
    result: dict[str, Any] = {"symbol": sym}
    for tf in REGIME_TIMEFRAMES:
        result[tf] = _regime_for_timeframe(sym, tf)
    return result
