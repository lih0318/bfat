"""Range-bound mean-reversion strategy.

Enters LONG near range low, SHORT near range high, with RSI and volume
Z-score confirmation.  All rolling windows are past-only (no lookahead).

Two evaluation modes:
  * **close** (`evaluate`)   — run on closed candles (legacy path).
  * **intrabar** (`evaluate_intrabar`) — run on the forming bar's real-time
    high/low/close while RSI / vol-z filters are taken from the last closed
    candle.  Includes a minimum reward-to-risk filter to avoid late entries.
"""

from typing import Any, Optional

from app.config.constants import StrategyConstants
from app.domain.enums import Side
from app.domain.signal import Signal


# ─── Fixed parameters ──────────────────────────────────────────────
RANGE_LOOKBACK = StrategyConstants.RANGE_LOOKBACK
RSI_PERIOD = StrategyConstants.RANGE_RSI_PERIOD
ATR_PERIOD = StrategyConstants.ATR_PERIOD
ENTRY_THRESHOLD = StrategyConstants.RANGE_ENTRY_THRESHOLD
STOP_BUFFER = StrategyConstants.RANGE_STOP_BUFFER_PCT
STOP_ATR_MULTIPLIER = StrategyConstants.RANGE_STOP_ATR_MULTIPLIER
RSI_LONG = StrategyConstants.RANGE_RSI_LONG
RSI_SHORT = StrategyConstants.RANGE_RSI_SHORT
VOLUME_LOOKBACK = StrategyConstants.RANGE_VOLUME_LOOKBACK
MIN_REWARD_RISK = StrategyConstants.RANGE_MIN_REWARD_RISK

MINIMUM_CANDLES = max(
    RANGE_LOOKBACK + 1, RSI_PERIOD + 1, ATR_PERIOD + 1, VOLUME_LOOKBACK + 1,
)


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


def _atr_value(candles: list[dict], period: int = ATR_PERIOD) -> Optional[float]:
    """Average True Range for the latest closed candle."""
    if len(candles) < period + 1:
        return None
    tr_list: list[float] = [0.0]
    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]
        tr_list.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    return sum(tr_list[-period:]) / period


def _range_stop_buffer(close: float, atr_value: float) -> float:
    """Use the wider of a fixed percent and ATR fraction for range stops."""
    return max(close * STOP_BUFFER, atr_value * STOP_ATR_MULTIPLIER)


# ─── Strategy ──────────────────────────────────────────────────────

class RangeStrategy:
    """Range mean-reversion strategy.  Enters near boundaries, targets mid.

    Supports both candle-close evaluation and intrabar (live_bar) evaluation.
    """

    def __init__(self, symbol: str = "BTCUSDT") -> None:
        self._symbol = symbol
        self._last_evaluation: dict = {}
        self._cached_range: dict[str, float] = {}
        self._cached_rsi: Optional[float] = None
        self._cached_vol_z: Optional[float] = None

    def get_last_evaluation_details(self) -> dict:
        """Return last evaluation context for insight API."""
        return dict(self._last_evaluation)

    def refresh_range_context(self, candles: list[dict]) -> None:
        """Update cached range metrics without producing signals."""
        ctx = self._compute_range_and_filters(candles)
        if ctx is not None:
            self._store_evaluation(
                ctx["range_high"], ctx["range_low"], ctx["range_mid"],
                ctx["atr"], ctx["rsi"], ctx["vol_z"], candles[-1]["close"],
                [], entry_conditions=[],
            )

    # ── Shared helpers ──────────────────────────────────────────────

    def _compute_range_and_filters(self, candles: list[dict]) -> Optional[dict]:
        """Compute range bounds, RSI, vol_z from *closed* candles.

        Returns dict with range_high/low/mid/rsi/vol_z or None if
        insufficient data. Also caches values for intrabar reuse.
        """
        if len(candles) < MINIMUM_CANDLES:
            return None
        prev_bars = candles[-(RANGE_LOOKBACK + 1):-1]
        range_high = max(c["high"] for c in prev_bars)
        range_low = min(c["low"] for c in prev_bars)
        range_mid = (range_high + range_low) / 2.0
        rsi = _rsi(candles, RSI_PERIOD)
        atr = _atr_value(candles, ATR_PERIOD)
        vol_z = _volume_zscore(candles, VOLUME_LOOKBACK)
        if atr is None or atr <= 0:
            return None
        result = {
            "range_high": range_high,
            "range_low": range_low,
            "range_mid": range_mid,
            "rsi": rsi,
            "atr": atr,
            "vol_z": vol_z,
        }
        self._cached_range = {"range_high": range_high, "range_low": range_low, "range_mid": range_mid}
        self._cached_rsi = rsi
        self._cached_vol_z = vol_z
        return result

    def _check_entry_conditions(
        self, high: float, low: float, close: float,
        range_high: float, range_low: float, range_mid: float,
        rsi: Optional[float], vol_z: Optional[float],
    ) -> tuple[bool, bool, list[dict]]:
        """Evaluate entry masks.  Returns (long_signal, short_signal, entry_conditions)."""
        low_threshold = range_low * (1 + ENTRY_THRESHOLD)
        high_threshold = range_high * (1 - ENTRY_THRESHOLD)
        near_low = low <= low_threshold
        near_high = high >= high_threshold
        rsi_oversold = rsi is not None and rsi < RSI_LONG
        rsi_overbought = rsi is not None and rsi > RSI_SHORT
        vol_quiet = vol_z is not None and vol_z < 1.0

        long_signal = near_low and rsi_oversold and vol_quiet
        short_signal = near_high and rsi_overbought and vol_quiet

        rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
        vol_z_str = f"{vol_z:.2f}" if vol_z is not None else "N/A"

        entry_conds = [
            {"label": "Touch range low (LONG)", "required": f"low ≤ {low_threshold:.2f}", "actual": f"{low:.2f}", "met": near_low},
            {"label": "Touch range high (SHORT)", "required": f"high ≥ {high_threshold:.2f}", "actual": f"{high:.2f}", "met": near_high},
            {"label": "RSI oversold (LONG)", "required": f"< {RSI_LONG}", "actual": rsi_str, "met": rsi_oversold},
            {"label": "RSI overbought (SHORT)", "required": f"> {RSI_SHORT}", "actual": rsi_str, "met": rsi_overbought},
            {"label": "Volume Z < 1.0 (no spike)", "required": "< 1.0", "actual": vol_z_str, "met": vol_quiet},
        ]
        return long_signal, short_signal, entry_conds

    @staticmethod
    def _passes_min_rr(
        side: Side, close: float,
        stop: float, tp: float,
    ) -> bool:
        """Return True if the reward/risk ratio from *current price* >= MIN_REWARD_RISK."""
        if side == Side.LONG:
            risk = abs(close - stop)
            reward = abs(tp - close)
        else:
            risk = abs(stop - close)
            reward = abs(close - tp)
        if risk <= 0:
            return False
        return (reward / risk) >= MIN_REWARD_RISK

    def _store_evaluation(
        self,
        range_high: float,
        range_low: float,
        range_mid: float,
        atr_value: float,
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
            "atr_value": round(atr_value, 4),
            "rsi": round(rsi_value, 4) if rsi_value is not None else None,
            "volume_zscore": round(vol_z, 4) if vol_z is not None else None,
            "close_price": round(close_price, 4),
            "engine_reasoning": engine_reasoning,
        }
        if entry_conditions is not None:
            self._last_evaluation["entry_conditions"] = entry_conditions

    def evaluate(self, candles: list[dict]) -> Optional[Signal]:
        """Evaluate on *closed* candles.  Returns Signal(LONG/SHORT) or None."""
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return None

        ctx = self._compute_range_and_filters(candles)
        if ctx is None:
            return None

        range_high = ctx["range_high"]
        range_low = ctx["range_low"]
        range_mid = ctx["range_mid"]
        rsi = ctx["rsi"]
        atr = ctx["atr"]
        vol_z = ctx["vol_z"]

        cur = candles[-1]
        close = cur["close"]
        high = cur["high"]
        low = cur["low"]
        signal_time = cur.get("timestamp", "")

        long_signal, short_signal, entry_conds = self._check_entry_conditions(
            high, low, close, range_high, range_low, range_mid, rsi, vol_z,
        )

        rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
        vol_z_str = f"{vol_z:.2f}" if vol_z is not None else "N/A"

        stop_buffer = _range_stop_buffer(close, atr)
        stop_long = range_low - stop_buffer
        stop_short = range_high + stop_buffer

        if long_signal:
            stop = stop_long
            tp = range_mid
            rr_ok = self._passes_min_rr(Side.LONG, close, stop, tp)
            risk_l = abs(close - stop)
            reward_l = abs(tp - close)
            rr_val = reward_l / risk_l if risk_l > 0 else 0.0
            entry_conds.append({
                "label": "Reward/Risk (LONG)",
                "required": f"≥ {MIN_REWARD_RISK}",
                "actual": f"{rr_val:.2f}",
                "met": rr_ok,
            })
            self._store_evaluation(
                range_high, range_low, range_mid, atr, rsi, vol_z, close,
                [
                    f"Range [{range_low:.2f} – {range_high:.2f}]",
                    f"Range bounce LONG: low {low:.2f}, RSI {rsi_str}, vol_z {vol_z_str}",
                ],
                entry_conditions=entry_conds,
            )
            if not rr_ok:
                return None
            return Signal(
                symbol=self._symbol,
                side=Side.LONG,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        if short_signal:
            stop = stop_short
            tp = range_mid
            rr_ok = self._passes_min_rr(Side.SHORT, close, stop, tp)
            risk_s = abs(stop - close)
            reward_s = abs(close - tp)
            rr_val = reward_s / risk_s if risk_s > 0 else 0.0
            entry_conds.append({
                "label": "Reward/Risk (SHORT)",
                "required": f"≥ {MIN_REWARD_RISK}",
                "actual": f"{rr_val:.2f}",
                "met": rr_ok,
            })
            self._store_evaluation(
                range_high, range_low, range_mid, atr, rsi, vol_z, close,
                [
                    f"Range [{range_low:.2f} – {range_high:.2f}]",
                    f"Range bounce SHORT: high {high:.2f}, RSI {rsi_str}, vol_z {vol_z_str}",
                ],
                entry_conditions=entry_conds,
            )
            if not rr_ok:
                return None
            return Signal(
                symbol=self._symbol,
                side=Side.SHORT,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        # ── No entry — explain why ──
        near_low = entry_conds[0]["met"]
        near_high = entry_conds[1]["met"]
        rsi_oversold = entry_conds[2]["met"]
        rsi_overbought = entry_conds[3]["met"]
        vol_quiet = entry_conds[4]["met"]
        low_threshold = range_low * (1 + ENTRY_THRESHOLD)
        high_threshold = range_high * (1 - ENTRY_THRESHOLD)
        reasoning: list[str] = [f"Range [{range_low:.2f} – {range_high:.2f}]"]
        if not near_low and not near_high:
            reasoning.append(f"Bar did not touch low ≤ {low_threshold:.2f} or high ≥ {high_threshold:.2f} (low={low:.2f}, high={high:.2f})")
        elif near_low and not rsi_oversold:
            reasoning.append(f"Near low but RSI {rsi_str} >= {RSI_LONG}")
        elif near_high and not rsi_overbought:
            reasoning.append(f"Near high but RSI {rsi_str} <= {RSI_SHORT}")
        if not vol_quiet:
            reasoning.append(f"Volume Z-score {vol_z_str} >= 1.0 (need < 1.0, no spike)")
        self._store_evaluation(
            range_high, range_low, range_mid, atr, rsi, vol_z, close, reasoning,
            entry_conditions=entry_conds,
        )
        return None

    def update_insight_live(self, candles: list[dict], live_bar: dict) -> None:
        """Refresh ``_last_evaluation`` using the forming bar without producing signals."""
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return

        ctx = self._compute_range_and_filters(candles)
        if ctx is None:
            return

        range_high = ctx["range_high"]
        range_low = ctx["range_low"]
        range_mid = ctx["range_mid"]
        rsi = ctx["rsi"]
        atr = ctx["atr"]
        vol_z = ctx["vol_z"]

        high = live_bar["high"]
        low = live_bar["low"]
        close = live_bar["close"]

        long_signal, short_signal, entry_conds = self._check_entry_conditions(
            high, low, close, range_high, range_low, range_mid, rsi, vol_z,
        )

        stop_buffer = _range_stop_buffer(close, atr)
        stop_long = range_low - stop_buffer
        stop_short = range_high + stop_buffer
        if long_signal:
            rr_ok = self._passes_min_rr(Side.LONG, close, stop_long, range_mid)
            risk_l = abs(close - stop_long)
            reward_l = abs(range_mid - close)
            rr_val = reward_l / risk_l if risk_l > 0 else 0.0
            entry_conds.append({
                "label": "Reward/Risk (LONG)",
                "required": f"≥ {MIN_REWARD_RISK}",
                "actual": f"{rr_val:.2f}",
                "met": rr_ok,
            })
        elif short_signal:
            rr_ok = self._passes_min_rr(Side.SHORT, close, stop_short, range_mid)
            risk_s = abs(stop_short - close)
            reward_s = abs(close - range_mid)
            rr_val = reward_s / risk_s if risk_s > 0 else 0.0
            entry_conds.append({
                "label": "Reward/Risk (SHORT)",
                "required": f"≥ {MIN_REWARD_RISK}",
                "actual": f"{rr_val:.2f}",
                "met": rr_ok,
            })

        rsi_str = f"{rsi:.2f}" if rsi is not None else "N/A"
        vol_z_str = f"{vol_z:.2f}" if vol_z is not None else "N/A"
        reasoning = [
            f"Range [{range_low:.2f} – {range_high:.2f}]",
            f"Live: close {close:.2f}, RSI {rsi_str}, vol_z {vol_z_str}",
        ]
        self._store_evaluation(
            range_high, range_low, range_mid, atr, rsi, vol_z, close, reasoning,
            entry_conditions=entry_conds,
        )

    def evaluate_intrabar(self, candles: list[dict], live_bar: dict) -> Optional[Signal]:
        """Evaluate using the *forming* bar's price action + last-closed-bar filters.

        `candles` must be the closed-candle buffer (same as `evaluate`).
        `live_bar` is the snapshot of the still-open 15m bar.

        Returns Signal with the live bar's timestamp (bucket), or None.
        """
        required_keys = ("open", "high", "low", "close", "volume")
        if not candles or not all(
            isinstance(c, dict) and all(k in c for k in required_keys)
            for c in candles
        ):
            return None

        ctx = self._compute_range_and_filters(candles)
        if ctx is None:
            return None

        range_high = ctx["range_high"]
        range_low = ctx["range_low"]
        range_mid = ctx["range_mid"]
        rsi = ctx["rsi"]
        atr = ctx["atr"]
        vol_z = ctx["vol_z"]

        high = live_bar["high"]
        low = live_bar["low"]
        close = live_bar["close"]
        signal_time = live_bar.get("timestamp", "")

        long_signal, short_signal, _ = self._check_entry_conditions(
            high, low, close, range_high, range_low, range_mid, rsi, vol_z,
        )
        stop_buffer = _range_stop_buffer(close, atr)

        if long_signal:
            stop = range_low - stop_buffer
            tp = range_mid
            if not self._passes_min_rr(Side.LONG, close, stop, tp):
                return None
            return Signal(
                symbol=self._symbol,
                side=Side.LONG,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        if short_signal:
            stop = range_high + stop_buffer
            tp = range_mid
            if not self._passes_min_rr(Side.SHORT, close, stop, tp):
                return None
            return Signal(
                symbol=self._symbol,
                side=Side.SHORT,
                signal_time=signal_time,
                signal_candle_ts=signal_time,
                stop_price=stop,
                take_profit=tp,
            )

        return None
