"""Range-bound mean-reversion strategy.

Enters LONG near range low, SHORT near range high, with RSI and volume
Z-score confirmation.  All rolling windows are past-only (no lookahead).
"""

from typing import Any, Optional

from app.domain.enums import Side
from app.domain.signal import Signal


# ─── Fixed parameters ──────────────────────────────────────────────
RANGE_LOOKBACK = 20
RSI_PERIOD = 14
ENTRY_THRESHOLD = 0.0015
STOP_BUFFER = 0.002
RSI_LONG = 38
RSI_SHORT = 62
VOLUME_LOOKBACK = 20

MINIMUM_CANDLES = max(RANGE_LOOKBACK + 1, RSI_PERIOD + 1, VOLUME_LOOKBACK + 1)


# ─── Indicators ────────────────────────────────────────────────────

def _rsi(candles: list[dict], period: int = RSI_PERIOD) -> Optional[float]:
    """Wilder-smoothed RSI.  Returns last value or None if insufficient data."""
    closes = [c["close"] for c in candles]
    if len(closes) < period + 1:
        return None
    gains: list[float] = []
    losses: list[float] = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))
    if len(gains) < period:
        return None
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _volume_zscore(candles: list[dict], period: int = VOLUME_LOOKBACK) -> Optional[float]:
    """Z-score of current candle volume vs past-only rolling window."""
    if len(candles) < period + 1:
        return None
    window = [c["volume"] for c in candles[-(period + 1) : -1]]
    mean = sum(window) / len(window)
    variance = sum((v - mean) ** 2 for v in window) / len(window)
    std = variance**0.5 if variance > 0 else 0.0
    if std == 0:
        return None
    current = candles[-1]["volume"]
    return (current - mean) / std


# ─── Strategy ──────────────────────────────────────────────────────

class RangeStrategy:
    """Range mean-reversion strategy.  Enters near boundaries, targets mid."""

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol
        self._last_evaluation: dict = {}

    def get_last_evaluation_details(self) -> dict:
        """Return last evaluation context for insight API."""
        return dict(self._last_evaluation)

    def _store_evaluation(
        self,
        range_high: float,
        range_low: float,
        range_mid: float,
        rsi_value: Optional[float],
        vol_z: Optional[float],
        close_price: float,
        engine_reasoning: list[str],
        *,
        entry_conditions: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        self._last_evaluation = {
            "range_high": round(range_high, 4),
            "range_low": round(range_low, 4),
            "range_mid": round(range_mid, 4),
            "rsi": round(rsi_value, 4) if rsi_value is not None else None,
            "volume_zscore": round(vol_z, 4) if vol_z is not None else None,
            "close_price": round(close_price, 4),
            "engine_reasoning": engine_reasoning,
        }
        if entry_conditions is not None:
            self._last_evaluation["entry_conditions"] = entry_conditions

    def evaluate(self, candles: list[dict]) -> Optional[Signal]:
        """Evaluate closed candles.  Returns Signal(LONG/SHORT) or None."""
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return None
        if len(candles) < MINIMUM_CANDLES:
            return None

        # ── Range bounds (shift(1): current candle excluded) ──
        prev_bars = candles[-(RANGE_LOOKBACK + 1) : -1]
        range_high = max(c["high"] for c in prev_bars)
        range_low = min(c["low"] for c in prev_bars)
        range_mid = (range_high + range_low) / 2.0

        cur = candles[-1]
        close = cur["close"]
        high = cur["high"]
        low = cur["low"]
        signal_time = cur.get("timestamp", "")

        rsi = _rsi(candles, RSI_PERIOD)
        vol_z = _volume_zscore(candles, VOLUME_LOOKBACK)

        # ── Single boolean entry masks (intrabar: any touch during 15m bar satisfies level) ──
        low_threshold = range_low * (1 + ENTRY_THRESHOLD)
        high_threshold = range_high * (1 - ENTRY_THRESHOLD)
        near_low = low <= low_threshold  # bar touched or went below level at some point
        near_high = high >= high_threshold  # bar touched or went above level at some point
        rsi_oversold = rsi is not None and rsi < RSI_LONG
        rsi_overbought = rsi is not None and rsi > RSI_SHORT
        vol_quiet = vol_z is not None and vol_z <= 0

        long_signal = near_low and rsi_oversold and vol_quiet
        short_signal = near_high and rsi_overbought and vol_quiet

        rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
        vol_z_str = f"{vol_z:.2f}" if vol_z is not None else "N/A"

        entry_conds = [
            {"label": "Touch range low (LONG)", "required": f"low ≤ {low_threshold:.2f}", "actual": f"{low:.2f}", "met": near_low},
            {"label": "Touch range high (SHORT)", "required": f"high ≥ {high_threshold:.2f}", "actual": f"{high:.2f}", "met": near_high},
            {"label": "RSI oversold (LONG)", "required": f"< {RSI_LONG}", "actual": rsi_str, "met": rsi_oversold},
            {"label": "RSI overbought (SHORT)", "required": f"> {RSI_SHORT}", "actual": rsi_str, "met": rsi_overbought},
            {"label": "Volume Z ≤ 0 (quiet)", "required": "≤ 0", "actual": vol_z_str, "met": vol_quiet},
        ]

        if long_signal:
            stop = range_low * (1 - STOP_BUFFER)
            tp = range_mid
            self._store_evaluation(
                range_high, range_low, range_mid, rsi, vol_z, close,
                [
                    f"Range [{range_low:.2f} – {range_high:.2f}]",
                    f"Range bounce LONG: low {low:.2f} touched ≤ {low_threshold:.2f}, RSI {rsi_str}, vol_z {vol_z_str}",
                ],
                entry_conditions=entry_conds,
            )
            return Signal(
                symbol=self._symbol,
                side=Side.LONG,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        if short_signal:
            stop = range_high * (1 + STOP_BUFFER)
            tp = range_mid
            self._store_evaluation(
                range_high, range_low, range_mid, rsi, vol_z, close,
                [
                    f"Range [{range_low:.2f} – {range_high:.2f}]",
                    f"Range bounce SHORT: high {high:.2f} touched ≥ {high_threshold:.2f}, RSI {rsi_str}, vol_z {vol_z_str}",
                ],
                entry_conditions=entry_conds,
            )
            return Signal(
                symbol=self._symbol,
                side=Side.SHORT,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        # ── No entry — explain why ──
        reasoning: list[str] = [f"Range [{range_low:.2f} – {range_high:.2f}]"]
        if not near_low and not near_high:
            reasoning.append(f"Bar did not touch low ≤ {low_threshold:.2f} or high ≥ {high_threshold:.2f} (low={low:.2f}, high={high:.2f})")
        elif near_low and not rsi_oversold:
            reasoning.append(f"Near low but RSI {rsi_str} >= {RSI_LONG}")
        elif near_high and not rsi_overbought:
            reasoning.append(f"Near high but RSI {rsi_str} <= {RSI_SHORT}")
        if not vol_quiet:
            reasoning.append(f"Volume Z-score {vol_z_str} > 0 (need quiet)")
        self._store_evaluation(
            range_high, range_low, range_mid, rsi, vol_z, close, reasoning,
            entry_conditions=entry_conds,
        )
        return None
